"""Filesystem and native-command fixtures for proxy observation consumers."""
from __future__ import annotations

import hashlib
import os
import pwd
from types import SimpleNamespace
from pathlib import Path
import subprocess

from taskman_ops.host_protocol import HostRequest, PROTOCOL_VERSION


def request(mode='inspect', expected=None, **updates):
    parameters = dict(mode=mode, hostname='taskman.acme.tld', application_port=4011,
                      site_content='taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4011\n}\n',
                      site_sha256='3916b0a333f47be5ad051f2268d5d7553b92f92207726ae4dce7b9a829404893',
                      receipt=None, public_probe=None)
    parameters['site_sha256'] = hashlib.sha256(parameters['site_content'].encode()).hexdigest()
    parameters.update(updates)
    return HostRequest(PROTOCOL_VERSION, 'caddy', 'op-' + '1' * 32, expected or {}, {}, parameters)


def host(tmp_path, monkeypatch, *, installed=True, active=True, masked=False, config=True, main=True):
    from taskman_ops.host_helper.operations import caddy as module
    root = tmp_path / 'host'
    (root / 'etc/systemd/system').mkdir(parents=True)
    (root / 'usr/lib/systemd/system').mkdir(parents=True)
    (root / 'usr/bin').mkdir(parents=True)
    (root / 'proc/123').mkdir(parents=True)
    if config:
        (root / 'etc/caddy').mkdir()
        if main:
            (root / 'etc/caddy/Caddyfile').write_text('import sites-enabled/*.caddy\n')
    unit = root / 'usr/lib/systemd/system/caddy.service'
    api = root / 'usr/lib/systemd/system/caddy-api.service'
    executable = root / 'usr/bin/caddy'
    if installed:
        unit.write_text('[Service]\nExecStart=/usr/bin/caddy run\n')
        api.write_text('[Service]\nExecStart=/usr/bin/caddy run --resume\n')
        executable.write_bytes(b'packaged executable')
        executable.chmod(0o755)
        (root / 'proc/123/exe').symlink_to(executable)
        (root / 'proc/123/cgroup').write_text('0::/system.slice/caddy.service\n')
    if masked:
        (root / 'etc/systemd/system/caddy.service').symlink_to('/dev/null')
    monkeypatch.setattr(module, '_ROOT', root)
    monkeypatch.setattr(pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=998, pw_gid=998))
    monkeypatch.setattr(os, 'getgrouplist', lambda name, primary_gid: [998])
    native_stat = module._stat
    def root_stat(path):
        info = native_stat(path)
        if info is None:
            return None
        fields = list(info)
        fields[4] = 0
        fields[5] = 998 if path == root / 'etc/caddy/Caddyfile' else 0
        return os.stat_result(fields)
    monkeypatch.setattr(module, '_stat', root_stat)
    recorded_sums = ''.join(f'{hashlib.md5(path.read_bytes()).hexdigest()}  {path.relative_to(root)}\n' for path in (unit, api, executable)).encode() if installed else b''
    def command(argv):
        if argv[:2] == ('dpkg-query', '--show'):
            return (0, b'installed\t2.10.2\n', b'') if installed else (1, b'', b'dpkg-query: no packages found matching caddy\n')
        if argv[:2] == ('dpkg-query', '--listfiles'):
            return 0, b'/usr/bin/caddy\n/usr/lib/systemd/system/caddy.service\n/usr/lib/systemd/system/caddy-api.service\n', b''
        if argv[:2] == ('dpkg-query', '--control-show'):
            return 0, recorded_sums, b''
        if argv[0] == 'systemctl':
            service = argv[2]
            is_api = service == 'caddy-api.service'
            effective_mask = masked and not is_api
            data = dict(LoadState='masked' if effective_mask else 'loaded' if installed else 'not-found',
                        ActiveState='active' if active and installed and not is_api else 'inactive',
                        UnitFileState='masked' if effective_mask else 'enabled' if installed and not is_api else 'disabled' if installed else '',
                        FragmentPath='/dev/null' if effective_mask else '/usr/lib/systemd/system/' + service if installed else '',
                        DropInPaths='', MainPID='123' if active and installed and not is_api else '0')
            return 0, ''.join(f'{key}={value}\n' for key, value in data.items()).encode(), b''
        if argv[0] == 'ss':
            return 0, (b'LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:(("caddy",pid=123,fd=8))\nLISTEN 0 4096 [::]:443 [::]:* users:(("caddy",pid=123,fd=9))\n' if active and installed else b''), b''
        if argv[0] == 'grep':
            completed = subprocess.run(argv, capture_output=True)
            return completed.returncode, completed.stdout, completed.stderr
        if argv == ('uname', '-m'):
            return 0, b'x86_64\n', b''
        raise AssertionError(argv)
    monkeypatch.setattr(module, '_command', command)
    (root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
    (root / 'proc/1').mkdir()
    (root / 'proc/1/comm').write_text('systemd\n')
    return module, root, command



def mutation_host(tmp_path, monkeypatch, *, active=True, imported=True, original=None):
    module, root, command = host(tmp_path, monkeypatch, active=active)
    if not imported:
        (root / 'etc/caddy/Caddyfile').write_text('# operator main\n')
    directory = root / 'etc/caddy/sites-enabled'
    directory.mkdir(mode=0o750)
    (directory / 'other.caddy').write_bytes(b'operator sibling\n')
    if original is not None:
        (directory / 'taskman.caddy').write_bytes(original)
    monkeypatch.setattr(os, 'chown', lambda *args, **kwargs: None)
    monkeypatch.setattr(os, 'fchown', lambda *args, **kwargs: None)
    calls = []
    def native(argv, **kwargs):
        calls.append((argv, kwargs))
        return b''
    monkeypatch.setattr(module, '_native', native, raising=False)
    return module, root, calls



def installation_host(tmp_path, monkeypatch, *, preserved=True, masked=False, main=True,
                      version='2.11.7', default=None, priority=500):
    module, root, original_command = host(tmp_path, monkeypatch, installed=False, active=False,
                                          config=preserved, main=main, masked=masked)
    monkeypatch.setattr(os, 'chown', lambda *args, **kwargs: None)
    monkeypatch.setattr(os, 'fchown', lambda *args, **kwargs: None)
    if not preserved:
        original_stat = module._stat
        def package_stat(path):
            info = original_stat(path)
            if info is not None and path == root/'etc/caddy/Caddyfile':
                fields = list(info); fields[5] = 0
                return os.stat_result(fields)
            return info
        monkeypatch.setattr(module, '_stat', package_stat)
    fixture = Path(__file__).parents[1] / 'fixtures/caddy/package-default.Caddyfile'
    default = fixture.read_bytes() if default is None else default
    archive_bytes = b'authenticated package fixture' if version == '2.11.7' else version.encode() + default
    state = {'installed': False, 'active': False, 'enabled': False, 'failure': None,
             'version': version, 'default': default}
    calls = []
    def command(argv):
        if argv[:2] == ('dpkg-query', '--show') and state['installed']:
            return 0, ('installed\t' + state['version'] + '\n').encode(), b''
        if argv[:2] == ('dpkg-query', '--listfiles'):
            return 0, b'/usr/bin/caddy\n/usr/lib/systemd/system/caddy.service\n/usr/lib/systemd/system/caddy-api.service\n', b''
        if argv[:2] == ('dpkg-query', '--control-show'):
            data = b''.join(f'{hashlib.md5(p.read_bytes()).hexdigest()}  {p.relative_to(root)}\n'.encode() for p in
                  (root/'usr/bin/caddy',root/'usr/lib/systemd/system/caddy.service',root/'usr/lib/systemd/system/caddy-api.service'))
            return 0, data, b''
        if argv[:2] == ('systemctl', 'show'):
            name = argv[2]; is_api = name == 'caddy-api.service'
            is_masked = (root/'etc/systemd/system'/name).is_symlink()
            data = dict(LoadState='masked' if is_masked else 'loaded' if state['installed'] else 'not-found',
                        ActiveState='active' if state['active'] and not is_api else 'inactive',
                        UnitFileState='masked' if is_masked else 'enabled' if state['enabled'] and not is_api else 'disabled' if state['installed'] else '',
                        FragmentPath='/dev/null' if is_masked else '/usr/lib/systemd/system/'+name if state['installed'] else '',
                        DropInPaths='',MainPID='123' if state['active'] and not is_api else '0')
            return 0, ''.join(f'{k}={v}\n' for k,v in data.items()).encode(), b''
        if argv[0] == 'ss' and state['active']:
            return 0, b'LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:(("caddy",pid=123,fd=8))\nLISTEN 0 4096 [::]:443 [::]:* users:(("caddy",pid=123,fd=9))\n', b''
        return original_command(argv)
    monkeypatch.setattr(module, '_command', command)
    def native(argv, **kwargs):
        calls.append((argv,kwargs))
        if argv[:2] == ('systemctl', 'mask'):
            (root/'etc/systemd/system'/argv[2]).symlink_to('/dev/null')
        elif argv[:2] == ('apt-cache', 'policy'):
            return (f'caddy:\n  Installed: (none)\n  Candidate: {version}\n  Version table:\n'
                    f'     {version} {priority}\n        {priority} https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version/main amd64 Packages\n').encode()
        elif argv[0] == 'sh':
            Path(argv[4], f'caddy_{version}_amd64.deb').write_bytes(archive_bytes)
        elif argv[:2] == ('dpkg-deb', '--show'):
            return f'caddy\t{version}\tamd64\n'.encode()
        elif argv[:2] == ('dpkg-deb', '--extract'):
            path = Path(argv[3]) / 'etc/caddy/Caddyfile'
            path.parent.mkdir(parents=True)
            path.write_bytes(default)
            path.chmod(0o644)
        elif argv[:2] == ('apt-get', 'install'):
            for name in ('caddy.service','caddy-api.service'):
                (root/'usr/lib/systemd/system'/name).write_text('[Service]\nExecStart=/usr/bin/caddy run\n')
            exe=root/'usr/bin/caddy';exe.write_bytes(b'packaged executable');exe.chmod(0o755)
            (root/'proc/123/exe').symlink_to(exe)
            (root/'proc/123/cgroup').write_text('0::/system.slice/caddy.service\n')
            state['installed'] = True
            directory=root/'etc/caddy';directory.mkdir(exist_ok=True)
            main_path=directory/'Caddyfile'
            if not main_path.exists():main_path.write_bytes(state['default'])
            if state['failure']=='package':raise RuntimeError('private package diagnostics')
            if state['failure']=='preservation':main_path.write_bytes(b'changed operator main')
        elif argv[:2] == ('systemctl', 'unmask'):
            (root/'etc/systemd/system'/argv[2]).unlink()
        elif argv[:2] == ('systemctl', 'enable'):
            state['enabled']=True
        elif argv[:2] == ('systemctl', 'start'):
            state['active']=True
        return b''
    monkeypatch.setattr(module,'_native',native,raising=False)
    return module,root,state,calls


def authority_result(*, branch='bootstrap', active=False, imported=False, masked=False,
                     site_sha256=None, receipt=None, mutation_state='unchanged'):
    """Closed controller evidence for workflows that isolate native proxy actions."""
    from taskman_ops.host_protocol import HostResult
    installed = branch == 'existing'
    authority = dict(
        branch=branch, package_installed=installed, package_version='2.11.7' if installed else None,
        executable_sha256='1' * 64 if installed else None, executable_trusted=installed,
        unit_path='/usr/lib/systemd/system/caddy.service' if installed else None,
        unit_sha256='2' * 64 if installed else None, unit_trusted=installed,
        api_unit_path='/usr/lib/systemd/system/caddy-api.service' if installed else None,
        api_unit_sha256='3' * 64 if installed else None,
        service_active=active, service_enabled='masked' if masked else 'enabled' if installed else 'absent',
        service_masked=masked, api_service_active=False,
        api_service_enabled='disabled' if installed else 'absent', api_service_masked=False,
        main_pid=123 if active else 0,
        public_listeners=[{'address': '0.0.0.0', 'port': port, 'pid': 123} for port in (80, 443)] if active else [],
        configuration_directory=None if branch == 'bootstrap' else {'uid': 0, 'gid': 0, 'mode': 0o755},
        main=None if branch == 'bootstrap' else {'uid': 0, 'gid': 998, 'mode': 0o640},
        glob_import_present=imported,
        site_directory={'uid': 0, 'gid': 0, 'mode': 0o755} if site_sha256 else None,
        site={'uid': 0, 'gid': 0, 'mode': 0o644} if site_sha256 else None,
        site_sha256=site_sha256,
    )
    if branch == 'preserve-configuration':
        authority['main_sha256'] = '4' * 64
    return HostResult(3, 'caddy', 'op-' + '1' * 32, 'succeeded', 'Caddy authority validated',
        dict(exit_code=0, failed_boundary=None, mutation_state=mutation_state, authority=authority,
             unavailable_fields=[], receipt=receipt, validation='not-attempted', reload='not-attempted',
             recovery={'status': 'not-needed', 'runtime_uncertain': False}, reason='compatible'), ())


def prepared_result(plan, *, changed=False):
    import json
    initial = json.loads(plan.authority_json)
    receipt = dict(site_path='/etc/caddy/sites-enabled/taskman.caddy', site_sha256=plan.site_sha256,
                   site_changed=changed, recovery_id='a' * 32 if changed else None,
                   original_absent=initial['site'] is None, original_sha256=initial['site_sha256'],
                   original_metadata=initial['site'], created_masks=[], installed_package_version=None)
    return authority_result(branch='existing', active=plan.branch == 'bootstrap' or initial['service_active'],
                            imported=plan.branch == 'bootstrap' or initial['glob_import_present'],
                            masked=initial['service_masked'], site_sha256=plan.site_sha256, receipt=receipt,
                            mutation_state='changed' if changed else 'unchanged')


def controller_plan(config=None, *, branch='bootstrap'):
    from tests.support.environments import environment_config
    from taskman_ops.services.caddy import build_caddy_ownership_plan
    return build_caddy_ownership_plan(config or environment_config(), authority_result(branch=branch))
