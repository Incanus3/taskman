"""Bounded Caddy ownership observation, independent of Taskman installation."""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
import time
import os
import pwd
from pathlib import Path
import re
import stat

from taskman_ops.host_protocol import HostRequest, HostResult, ProtocolError, validate_caddy_request, validate_caddy_result
from taskman_ops.host_protocol.caddy import validate_caddy_authority
from taskman_ops.host_helper.commands import run_command

_DEADLINE = ContextVar("caddy_deadline", default=None)

_ROOT = Path('/')
_MAIN_LIMIT = 64 * 1024
_SITE_LIMIT = 4096
_GLOB_PATTERN = r'^[[:blank:]]*import[[:blank:]]+(/etc/caddy/sites-enabled/\*\.caddy|sites-enabled/\*\.caddy|"(/etc/caddy/sites-enabled/\*\.caddy|sites-enabled/\*\.caddy)")[[:blank:]]*(#.*)?$'
_PROPERTIES = ('LoadState', 'ActiveState', 'UnitFileState', 'FragmentPath', 'DropInPaths', 'MainPID')


class CaddyObservationError(RuntimeError):
    def __init__(self, exit_code=5, reason='inspection-failed'):
        self.exit_code, self.reason = exit_code, reason
        super().__init__('Caddy ' + reason)


def _command(argv):
    """Retain native status through the bounded runner; never interpolate argv.

    The runner requires exit zero. This finite shell adapter preserves status 1
    for grep/dpkg absence without swallowing status 2 or transport failures.
    """
    result = run_command(('sh', '-c', '"$@"; status=$?; printf "\\nTASKMAN_STATUS=%s\\n" "$status"',
                          'taskman-caddy-observation', *argv),
                         env={**os.environ, 'LC_ALL': 'C', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin'}, timeout_seconds=_remaining(3), output_limit=16 * 1024)
    data, separator, status_value = result.stdout.rpartition(b'\nTASKMAN_STATUS=')
    if not separator or not re.fullmatch(rb'[0-9]{1,3}\n', status_value):
        raise CaddyObservationError()
    return int(status_value), data, result.stderr


def _remaining(cap):
    deadline = _DEADLINE.get()
    remaining = cap if deadline is None else min(cap, deadline - time.monotonic())
    if remaining <= 0:
        raise CaddyObservationError()
    return remaining


def _native(argv, *, timeout_seconds):
    return run_command(argv, env={'LC_ALL': 'C', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                'DEBIAN_FRONTEND': 'noninteractive'},
                       timeout_seconds=_remaining(timeout_seconds), output_limit=16 * 1024).stdout


def _success(argv):
    code, out, err = _command(argv)
    if code != 0 or err:
        raise CaddyObservationError()
    return out


def _path(path):
    return _ROOT / path.lstrip('/')


def _stat(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _metadata(path, *, directory=False, site=False):
    info = _stat(path)
    if info is None:
        return None
    mode = stat.S_IMODE(info.st_mode)
    if (info.st_uid != 0 or mode & 0o022 or mode & ~0o777
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or directory and not mode & 0o111
            or site and (info.st_gid != 0 or mode != 0o644)):
        raise CaddyObservationError(10, 'unsafe-authority')
    return {'uid': info.st_uid, 'gid': info.st_gid, 'mode': mode}


def _parents(path):
    for parent in reversed(path.parents):
        if parent == _ROOT or _ROOT not in parent.parents:
            continue
        # Ubuntu usrmerge's documented /lib alias is the only allowed redirect.
        if parent == _path('/lib') and parent.is_symlink() and parent.resolve() == _path('/usr/lib').resolve():
            _metadata(_path('/usr/lib'), directory=True)
        else:
            _metadata(parent, directory=True)


def _read(path, limit):
    try:
        with path.open('rb') as stream:
            value = stream.read(limit + 1)
    except OSError:
        raise CaddyObservationError() from None
    if len(value) > limit:
        raise CaddyObservationError()
    return value


def inspect_glob_import(main: Path) -> bool:
    """Return only the normalized literal predicate; grep never exports lines."""
    info = _stat(main)
    if info is None:
        return False
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o022:
        raise CaddyObservationError(10, 'unsafe-authority')
    _read(main, _MAIN_LIMIT)
    status_value, count, diagnostic = _command(('grep', '-Ec', '--', _GLOB_PATTERN, str(main)))
    if diagnostic or status_value not in {0, 1} or re.fullmatch(rb'[0-9]+\n', count) is None:
        raise CaddyObservationError()
    number = int(count)
    if (status_value == 0) != (number > 0):
        raise CaddyObservationError()
    return status_value == 0


def _platform():
    text = _read(_path('/etc/os-release'), 4096).decode('ascii')
    values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    if (values.get('ID', '').strip('"') != 'ubuntu' or values.get('VERSION_ID', '').strip('"') != '26.04'
            or _success(('uname', '-m')).strip() != b'x86_64'
            or _read(_path('/proc/1/comm'), 64).strip() != b'systemd'):
        raise CaddyObservationError(2, 'unsupported-platform')


def _service(service):
    out = _success(('systemctl', 'show', service, '--no-pager', *('--property=' + name for name in _PROPERTIES)))
    try:
        lines = out.decode('ascii').splitlines()
        values = dict(line.split('=', 1) for line in lines)
        if len(lines) != len(_PROPERTIES) or set(values) != set(_PROPERTIES):
            raise ValueError
        if (values['LoadState'] not in {'loaded', 'masked', 'not-found'}
                or values['ActiveState'] not in {'active', 'inactive', 'failed'}
                or values['UnitFileState'] not in {'', 'enabled', 'disabled', 'static', 'masked', 'masked-runtime'}
                or values['DropInPaths'] or re.fullmatch(r'[0-9]+', values['MainPID']) is None):
            raise CaddyObservationError(10, 'unsafe-authority')
    except ValueError:
        raise CaddyObservationError() from None
    return values


def _mask(service):
    observed = False
    for directory in ('/etc/systemd/system', '/run/systemd/system'):
        parent = _path(directory)
        for ancestor in reversed(parent.parents):
            if ancestor == _ROOT or _ROOT not in ancestor.parents:
                continue
            if _stat(ancestor) is not None:
                _metadata(ancestor, directory=True)
        if _stat(parent) is not None:
            _metadata(parent, directory=True)
        for unit_directory in (parent, _path('/usr/lib/systemd/system'), _path('/lib/systemd/system')):
            if _stat(unit_directory / (service + '.d')) is not None:
                # Masks may hide effective DropInPaths. Underlying overrides remain foreign authority.
                raise CaddyObservationError(10, 'unsafe-authority')
        path = parent / service
        info = _stat(path)
        if info is None:
            continue
        if not stat.S_ISLNK(info.st_mode) or os.readlink(path) != '/dev/null' or info.st_uid != 0:
            raise CaddyObservationError(10, 'unsafe-authority')
        observed = True
    return observed


def _installed_package():
    code, out, err = _command(('dpkg-query', '--show', '--showformat=${db:Status-Status}\t${Version}\n', 'caddy'))
    if code == 1 and not out and err == b'dpkg-query: no packages found matching caddy\n':
        return False, None
    if code != 0 or err:
        raise CaddyObservationError()
    try:
        state, version = out.decode('ascii').strip().split('\t')
    except ValueError:
        raise CaddyObservationError() from None
    if state != 'installed' or not re.fullmatch(r'[A-Za-z0-9.+:~_-]{1,128}', version):
        raise CaddyObservationError(10, 'unsafe-authority')
    return True, version


def _executable_access(metadata):
    account = pwd.getpwnam('caddy')
    groups = os.getgrouplist('caddy', account.pw_gid)
    if (type(account.pw_uid) is not int or account.pw_uid < 0
            or type(account.pw_gid) is not int or account.pw_gid < 0
            or not isinstance(groups, (list, tuple)) or len(groups) > 64
            or any(type(group) is not int or group < 0 for group in groups)
            or account.pw_gid not in groups):
        raise CaddyObservationError()
    # POSIX chooses one identity class; other execute cannot override a matching group.
    shift = 6 if account.pw_uid == metadata['uid'] else 3 if metadata['gid'] in groups else 0
    if not (metadata['mode'] >> shift) & 0o1:
        raise CaddyObservationError(10, 'unsafe-authority')


def _package_trust(installed):
    executable = _path('/usr/bin/caddy')
    for directory in ('/usr/local/bin', '/usr/local/sbin', '/usr/sbin', '/sbin', '/bin'):
        candidate = _path(directory + '/caddy')
        if _stat(candidate) is not None and candidate.resolve() != executable.resolve():
            raise CaddyObservationError(10, 'unsafe-authority')
    service_paths = {}
    for service in ('caddy.service', 'caddy-api.service'):
        candidates = [path for prefix in ('/usr/lib/systemd/system/', '/lib/systemd/system/')
                      if _stat(path := _path(prefix + service)) is not None]
        if len({str(path.resolve()) for path in candidates}) > 1:
            raise CaddyObservationError(10, 'unsafe-authority')
        service_paths[service] = candidates
    if not installed:
        if _stat(executable) is not None or any(service_paths.values()):
            raise CaddyObservationError(10, 'unsafe-authority')
        return {key: None for key in ('executable_sha256', 'unit_path', 'unit_sha256', 'api_unit_path', 'api_unit_sha256')}
    files = set(_success(('dpkg-query', '--listfiles', 'caddy')).decode('ascii').splitlines())
    sums = _success(('dpkg-query', '--control-show', 'caddy', 'md5sums')).decode('ascii').splitlines()
    checksums = {}
    for line in sums:
        match = re.fullmatch(r'([0-9a-f]{32})  (\S+)', line)
        if match is None or match[2] in checksums:
            raise CaddyObservationError()
        checksums[match[2]] = match[1]
    selected = {'executable': executable}
    for key, service in (('unit', 'caddy.service'), ('api_unit', 'caddy-api.service')):
        candidates = [path for path in service_paths[service] if '/' + str(path.relative_to(_ROOT)) in files]
        if not candidates:
            raise CaddyObservationError(10, 'unsafe-authority')
        selected[key] = candidates[0]
    result = {}
    for key, path in selected.items():
        _parents(path)
        metadata = _metadata(path)
        relative = str(path.relative_to(_ROOT))
        if metadata is None or '/' + relative not in files or relative not in checksums:
            raise CaddyObservationError(10, 'unsafe-authority')
        if key == 'executable':
            _executable_access(metadata)
        elif metadata != {'uid': 0, 'gid': 0, 'mode': 0o644}:
            raise CaddyObservationError(10, 'unsafe-authority')
        data = _read(path, 64 * 1024 * 1024 if key == 'executable' else 64 * 1024)
        if hashlib.md5(data).hexdigest() != checksums[relative]:
            raise CaddyObservationError(10, 'unsafe-authority')
        result[key + '_sha256'] = hashlib.sha256(data).hexdigest()
        if key != 'executable':
            result[key + '_path'] = '/' + relative
    return result


def _listeners(service, installed):
    pid = int(service['MainPID'])
    active = service['ActiveState'] == 'active'
    if active != (pid > 0) or active and not installed:
        raise CaddyObservationError(10, 'unsafe-authority')
    if active:
        try:
            proc = _path('/proc/' + str(pid))
            if ((proc / 'exe').resolve(strict=True) != _path('/usr/bin/caddy').resolve(strict=True)
                    or not any(line.split(':', 2)[-1].endswith('/caddy.service') for line in _read(proc / 'cgroup', 4096).decode('ascii').splitlines())):
                raise CaddyObservationError(10, 'unsafe-authority')
        except OSError:
            raise CaddyObservationError() from None
    listeners = []
    for line in _success(('ss', '-H', '-ltnp', '( sport = :80 or sport = :443 )')).decode('ascii').splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0] != 'LISTEN':
            raise CaddyObservationError()
        try:
            address, number = fields[3].rsplit(':', 1)
            port = int(number)
        except ValueError:
            raise CaddyObservationError() from None
        if port not in {80, 443}:
            continue
        owner_field = ' '.join(fields[5:])
        owner_tuple = r'\("[^"]+",pid=[1-9][0-9]*,fd=[0-9]+\)'
        if re.fullmatch(r'users:\(' + owner_tuple + r'(?:,' + owner_tuple + r')*\)', owner_field) is None:
            raise CaddyObservationError(10, 'unsafe-authority')
        matches = re.findall(r'\("([^"]+)",pid=([0-9]+),fd=[0-9]+\)', owner_field)
        if not active or not matches or set(matches) != {('caddy', str(pid))}:
            raise CaddyObservationError(10, 'unsafe-authority')
        listeners.append({'address': address.strip('[]'), 'port': port, 'pid': pid})
    if active and {listener['port'] for listener in listeners} != {80, 443}:
        raise CaddyObservationError(10, 'unsafe-authority')
    return sorted(listeners, key=lambda value: (value['port'], value['address'], value['pid']))


def observe_caddy(*, bootstrap_masks=()):
    """Own all proxy inspection policy in one host-local production capability."""
    _remaining(3)
    _platform()
    installed, version = _installed_package()
    trust = _package_trust(installed)
    service, api = (_service(name) for name in ('caddy.service', 'caddy-api.service'))
    masks = [_mask(name) for name in ('caddy.service', 'caddy-api.service')]
    for values, masked, unit in ((service, masks[0], trust['unit_path']), (api, masks[1], trust['api_unit_path'])):
        if masked != (values['LoadState'] == 'masked'):
            raise CaddyObservationError(10, 'unsafe-authority')
        if values['LoadState'] == 'loaded':
            if unit is None or _path(values['FragmentPath']).resolve() != _path(unit).resolve():
                raise CaddyObservationError(10, 'unsafe-authority')
        elif not masked and installed:
            raise CaddyObservationError(10, 'unsafe-authority')
        if not installed and values['LoadState'] not in {'masked', 'not-found'}:
            raise CaddyObservationError(10, 'unsafe-authority')
    if api['ActiveState'] != 'inactive' or api['MainPID'] != '0' or api['UnitFileState'] == 'enabled':
        raise CaddyObservationError(10, 'unsafe-authority')
    for name in ('/etc',):
        _metadata(_path(name), directory=True)
    directory = _metadata(_path('/etc/caddy'), directory=True)
    main = _metadata(_path('/etc/caddy/Caddyfile'))
    site_directory = _metadata(_path('/etc/caddy/sites-enabled'), directory=True)
    site = _metadata(_path('/etc/caddy/sites-enabled/taskman.caddy'), site=True)
    listeners = _listeners(service, installed)
    branch = 'existing' if installed else 'preserve-configuration' if directory is not None else 'bootstrap'
    if not installed and (listeners or service['ActiveState'] != 'inactive') or branch == 'bootstrap' and masks != [name in bootstrap_masks for name in ('caddy.service', 'caddy-api.service')]:
        raise CaddyObservationError(10, 'unsafe-authority')
    authority = dict(branch=branch, package_installed=installed, package_version=version,
        **trust, executable_trusted=installed, unit_trusted=installed,
        service_active=service['ActiveState'] == 'active', service_enabled=service['UnitFileState'] or 'absent',
        service_masked=masks[0], api_service_active=False, api_service_enabled=api['UnitFileState'] or 'absent',
        api_service_masked=masks[1], main_pid=int(service['MainPID']), public_listeners=listeners,
        configuration_directory=directory, main=main,
        glob_import_present=inspect_glob_import(_path('/etc/caddy/Caddyfile')),
        site_directory=site_directory, site=site,
        site_sha256=hashlib.sha256(_read(_path('/etc/caddy/sites-enabled/taskman.caddy'), _SITE_LIMIT)).hexdigest() if site else None)
    if branch == 'preserve-configuration':
        authority['main_sha256'] = hashlib.sha256(_read(_path('/etc/caddy/Caddyfile'), _MAIN_LIMIT)).hexdigest() if main else None
    _remaining(3)
    return validate_caddy_authority(authority, bootstrap_masks=bootstrap_masks)


def caddy_failure_result(request, evidence=None, *, exit_code=5, boundary='inspection', reason='inspection-failed', mutation_state=None):
    base = HostResult.for_request(request, 'refused' if exit_code in {2, 10} else 'retryable', 'Caddy ' + reason,
        dict(exit_code=exit_code, failed_boundary=boundary,
             mutation_state=mutation_state or ('unchanged' if request.parameters.get('mode') == 'inspect' else 'unknown'),
             authority=None, unavailable_fields=('authority',), receipt=None,
             validation='not-attempted', reload='not-attempted',
             recovery={'status': 'not-needed', 'runtime_uncertain': False}, reason=reason))

    if evidence is None:
        return base
    # Only request-correlated cooperative results can contribute independently
    # validated closed evidence; no wire consumer uses this reduction.
    try:
        from taskman_ops.host_protocol.envelope import validate_result_for_request
        validate_result_for_request(request, evidence)
    except Exception:
        return base
    state = base.to_mapping()['state']
    raw = evidence.to_mapping()['state']
    for keys in (('mutation_state',), ('receipt', 'validation', 'reload', 'recovery'),
                 ('exit_code', 'failed_boundary', 'reason')):
        projection = {**state, **{key: raw.get(key) for key in keys}}
        if projection['exit_code'] == 0:
            continue
        candidate = HostResult.for_request(request,
            'refused' if projection['exit_code'] in {2, 10} else 'retryable',
            'Caddy inspection-failed', projection)
        try:
            validate_caddy_result(request, candidate)
        except Exception:
            continue
        state = projection
    return validate_caddy_result(request, HostResult.for_request(request,
        'refused' if state['exit_code'] in {2, 10} else 'retryable', 'Caddy inspection-failed', state))


def _caddy_request(request: HostRequest) -> HostResult:
    try:
        validate_caddy_request(request)
    except ProtocolError:
        return caddy_failure_result(request, exit_code=2, boundary='input', reason='invalid-input', mutation_state='unchanged')
    if request.parameters['mode'] != 'inspect':
        from taskman_ops.host_helper.caddy_execution import CaddyExecution
        import sys
        return CaddyExecution(sys.modules[__name__], request).run()
    try:
        authority = observe_caddy()
        result = HostResult.for_request(request, 'succeeded', 'Caddy compatible',
            dict(exit_code=0, failed_boundary=None, mutation_state='unchanged', authority=authority,
                 unavailable_fields=(), receipt=None, validation='not-attempted', reload='not-attempted',
                 recovery={'status': 'not-needed', 'runtime_uncertain': False}, reason='compatible'))
        return validate_caddy_result(request, result)
    except CaddyObservationError as error:
        return caddy_failure_result(request, exit_code=error.exit_code,
            boundary='authority' if error.exit_code == 10 else 'inspection', reason=error.reason)
    except Exception:
        return caddy_failure_result(request)


def caddy(request: HostRequest) -> HostResult:
    token = _DEADLINE.set(time.monotonic() + (600 if request.parameters.get('mode') == 'prepare' else 45))
    try:
        return _caddy_request(request)
    finally:
        _DEADLINE.reset(token)


__all__ = ['caddy', 'observe_caddy', 'inspect_glob_import', 'CaddyObservationError']
