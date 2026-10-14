"""Opt-in native fixture hooks; importing this module performs no host action.

Use only on the separately authorized fixture target. Instrumented/direct helper
calls are capability evidence, not unmodified public provision/history evidence.
No production function, executable, unit or request validator is replaced.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from taskman_ops.host_helper.filesystem import fsync_directory
from taskman_ops.host_protocol import validate_caddy_request
from taskman_ops.host_protocol.caddy import SITE_PATH, _version as valid_package_version


ACCEPTANCE_ROOT = Path('/root/taskman-caddy-acceptance')
FRONTEND_LOCK = Path('/var/lib/dpkg/lock-frontend')
RELOAD_ARGV = ('systemctl', 'reload', 'caddy.service')
VALIDATE_PREFIX = ('sudo', '-n', '-u', 'caddy', '/usr/bin/caddy', 'validate')
BARRIER_SECONDS = 15.0


def _parents_without_links(path):
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe fixture path')
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise ValueError('linked fixture path')


class PrivateDirectory:
    """Existing, owner-only receipt root; exclusive writes never erase evidence."""
    def __init__(self, root, *, owner=0, group=None):
        self.root, self.owner, self.group = Path(root), owner, owner if group is None else group
        _parents_without_links(self.root)
        info = self.root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_gid != self.group
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('unsafe private receipt directory')

    def path(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', name) or name in {'.', '..'}:
            raise ValueError('unsafe receipt name')
        return self.root / name

    def write(self, name, data):
        path = self.path(name)
        descriptor, staging = tempfile.mkstemp(prefix='.receipt-', dir=self.root)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # link() atomically exposes complete bytes and refuses any occupied name.
            os.link(staging, path, follow_symlinks=False)
        finally:
            os.unlink(staging)
        fsync_directory(self.root)

    def read(self, name):
        path = self.path(name)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner or info.st_gid != self.group
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('unsafe private receipt file')
        # Removing the staging link changes ctime: do not admit the inode until
        # publication cleanup is complete. Checkpoint pollers retry absence.
        if info.st_nlink != 1:
            raise FileNotFoundError('private receipt publication incomplete')
        def identity(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                    value.st_ctime_ns, value.st_mode, value.st_uid, value.st_gid)
        expected = identity(info)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
            if identity(os.fstat(stream.fileno())) != expected:
                raise ValueError('private receipt changed before read')
            data = stream.read(65537)
            end = os.fstat(stream.fileno())
        if len(data) > 65536:
            raise ValueError('oversized private receipt')
        if (identity(end) != expected or identity(path.lstat()) != expected
                or len(data) != info.st_size):
            raise ValueError('private receipt changed during read')
        return data


def acceptance_receipts(case):
    """Host entry: caller precreates the absent case directory, root:root:0700."""
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', case):
        raise ValueError('unsafe fixture case')
    return PrivateDirectory(ACCEPTANCE_ROOT / 'receipts' / case)


def helper_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    if type(pid) is not int or pid <= 0:
        raise ValueError('invalid helper pid')
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'start_ticks': int(fields[19])}


def _request_identity(request):
    data = json.dumps(request.to_mapping(), sort_keys=True, separators=(',', ':')).encode()
    return {'request_id': request.correlation_id, 'operation': request.operation,
            'mode': request.parameters['mode'], 'request_sha256': hashlib.sha256(data).hexdigest()}


class Checkpoint:
    """Fsynced correlation barrier; expiry exits before any helper result frame."""
    def __init__(self, directory, request):
        self.directory, self.identity = directory, _request_identity(request)
        suffix = self.identity['request_sha256']
        self.ack_name, self.release_name = f'{suffix}.ack.json', f'{suffix}.release.json'

    def publish(self, event, *, package_version=None):
        if event not in {'reload-reply-loss', 'post-apt', 'frontend-lock'}:
            raise ValueError('unsupported checkpoint')
        native_operation = {'reload-reply-loss': 'systemctl reload caddy.service',
                            'frontend-lock': 'fcntl.lockf /var/lib/dpkg/lock-frontend'}.get(event)
        if event == 'post-apt':
            if not valid_package_version(package_version):
                raise ValueError('invalid checkpoint package version')
            native_operation = 'apt-get install caddy=' + package_version
        elif package_version is not None:
            raise ValueError('unexpected checkpoint package version')
        ack = {**self.identity, 'event': event, 'native_return_status': 0, 'native_operation': native_operation,
               'helper_identity': helper_identity()}
        self.directory.write(self.ack_name, json.dumps(ack, sort_keys=True).encode())
        return ack

    def read_ack(self, event):
        ack = json.loads(self.directory.read(self.ack_name))
        validate_ack(ack, self.identity, event)
        return ack

    def release(self, ack, *, invocation_closed):
        recorded = self.read_ack(ack['event'])
        if ack != recorded or ack['helper_identity'] != helper_identity(ack['helper_identity']['pid']):
            raise ValueError('checkpoint/helper identity mismatch')
        if type(invocation_closed) is not bool or ack['event'] == 'reload-reply-loss' and not invocation_closed:
            raise ValueError('invocation must be closed before release')
        self.directory.write(self.release_name, json.dumps(
            {'ack': ack, 'invocation_closed': invocation_closed}, sort_keys=True).encode())

    def wait(self, event, *, terminate=os._exit, on_published=None, timeout_seconds=BARRIER_SECONDS):
        if not 0 < timeout_seconds <= BARRIER_SECONDS:
            raise ValueError('checkpoint bound must be at most fifteen seconds')
        deadline = time.monotonic() + timeout_seconds
        try:
            ack = self.publish(event)
            if on_published is not None:
                on_published()
            while time.monotonic() < deadline:
                try:
                    release = json.loads(self.directory.read(self.release_name))
                except FileNotFoundError:
                    time.sleep(0.02)
                    continue
                if (set(release) != {'ack', 'invocation_closed'} or release['ack'] != ack
                        or type(release['invocation_closed']) is not bool
                        or event == 'reload-reply-loss' and not release['invocation_closed']
                        or time.monotonic() >= deadline):
                    break
                return ack
        except Exception:
            # Never let CaddyExecution encode a reply after a broken checkpoint.
            pass
        terminate(87)
        raise SystemExit(87)


def validate_ack(ack, identity, event):
    operations = {'reload-reply-loss': 'systemctl reload caddy.service',
                  'frontend-lock': 'fcntl.lockf /var/lib/dpkg/lock-frontend'}
    operation = ack.get('native_operation') if isinstance(ack, dict) else None
    valid_operation = event in operations and operation == operations[event]
    if event == 'post-apt':
        prefix = 'apt-get install caddy='
        valid_operation = (type(operation) is str and operation.startswith(prefix)
                           and valid_package_version(operation[len(prefix):]))
    if (not isinstance(ack, dict) or set(ack) != {*identity, 'event', 'native_return_status', 'native_operation', 'helper_identity'}
            or any(ack[key] != value for key, value in identity.items())
            or ack['event'] != event or not valid_operation
            or type(ack['native_return_status']) is not int
            or ack['native_return_status'] != 0
            or not isinstance(ack['helper_identity'], dict)
            or set(ack['helper_identity']) != {'pid', 'start_ticks'}
            or any(type(value) is not int or value <= 0 for value in ack['helper_identity'].values())):
        raise ValueError('uncorrelated native checkpoint')


class ReplyLossController:
    """Construct BEFORE dispatch with an independent already pinned SSH observer.

    Observer supplies connected, read_ack(timeout_seconds=...),
    helper_identity(pid, timeout_seconds=...), release(ack, timeout_seconds=...).
    Every IO method must enforce its supplied remaining deadline. The
    invocation is the actual Paramiko channel; close() and .closed are native.
    This adapter never reads/discards an invocation result or closes its client.
    """
    def __init__(self, observer, request):
        if observer.connected is not True:
            raise ValueError('independent observer must already be connected')
        self.observer, self.identity = observer, _request_identity(request)

    def close_at_checkpoint(self, invocation):
        deadline = time.monotonic() + BARRIER_SECONDS
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError('checkpoint expired; scenario unproven')
            return value
        while time.monotonic() < deadline:
            try:
                ack = self.observer.read_ack(timeout_seconds=remaining())
            except FileNotFoundError:
                time.sleep(0.02)
                continue
            validate_ack(ack, self.identity, 'reload-reply-loss')
            if self.observer.helper_identity(ack['helper_identity']['pid'], timeout_seconds=remaining()) != ack['helper_identity']:
                raise ValueError('helper identity changed')
            if time.monotonic() >= deadline:
                raise TimeoutError('checkpoint expired; scenario unproven')
            invocation.close()
            if invocation.closed is not True:
                raise ValueError('physical invocation channel remains open')
            self.observer.release(ack, timeout_seconds=remaining())
            return ack
        raise TimeoutError('checkpoint unavailable; scenario unproven')


def install_argv(cache, version):
    if not valid_package_version(version):
        raise ValueError('invalid fixture package version')
    return ('apt-get', 'install', '--yes', '--no-download', '--no-install-recommends', '--no-remove',
            '-o', 'Dir::Cache::archives=' + str(cache), '-o', 'Dpkg::Options::=--force-confold',
            '-o', 'APT::Get::AllowUnauthenticated=false', '-o', 'Acquire::AllowInsecureRepositories=false',
            '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false', 'caddy=' + version)


class NativeFaultObserver:
    """Delegates observations and native argv unchanged; opt-in direct helper seam."""
    def __init__(self, observer, request, directory, *, fault, original_sha256=None,
                 fixture_main_sha256=None, terminate=os._exit):
        validate_caddy_request(request)
        modes = {'reload-reply-loss': 'activate', 'saved-original-checksum': 'prepare',
                 'terminate-after-apt': 'prepare', 'preservation-mismatch': 'prepare'}
        if fault not in modes or request.parameters['mode'] != modes[fault]:
            raise ValueError('fault/request mode mismatch')
        self.observer, self.request, self.directory = observer, request, directory
        self.fault, self.original_sha256 = fault, original_sha256
        self.fixture_main_sha256, self.terminate = fixture_main_sha256, terminate
        self.execution, self.fired = None, False

    def __getattr__(self, name):
        return getattr(self.observer, name)

    def _native(self, argv, *, timeout_seconds):
        if (not self.fired and self.fault == 'saved-original-checksum'
                and tuple(argv) == (*VALIDATE_PREFIX, '--config', str(self.observer._path('/etc/caddy/Caddyfile')),
                                    '--adapter', 'caddyfile') and self.execution is not None
                and self.execution.receipt is not None and self.execution.receipt['site_changed']):
            self._corrupt_original()
            self.fired = True
        result = self.observer._native(argv, timeout_seconds=timeout_seconds)
        if self.fired:
            return result
        if self.fault == 'reload-reply-loss' and tuple(argv) == RELOAD_ARGV:
            self.fired = True
            try:
                remaining = self.observer._remaining(BARRIER_SECONDS)
            except Exception:
                self.terminate(87)
                raise SystemExit(87)
            Checkpoint(self.directory, self.request).wait('reload-reply-loss', terminate=self.terminate,
                                                        timeout_seconds=remaining)
        elif (len(argv) == 17 and type(argv[7]) is str and argv[7].startswith('Dir::Cache::archives=')
                and type(argv[-1]) is str and argv[-1].startswith('caddy=')
                and valid_package_version(argv[-1][6:])
                and tuple(argv) == install_argv(argv[7].split('=', 1)[1], argv[-1][6:])):
            version = argv[-1][6:]
            if self.fault == 'terminate-after-apt':
                self.fired = True
                try:
                    Checkpoint(self.directory, self.request).publish('post-apt', package_version=version)
                except Exception:
                    # Missed acknowledgement is unproven and cannot become a
                    # framable ordinary CaddyExecution package failure.
                    self.terminate(87)
                    raise SystemExit(87)
                self.terminate(86)
                raise SystemExit(86)
            if self.fault == 'preservation-mismatch':
                self._change_fixture_main()
                self.fired = True
        return result

    def _corrupt_original(self):
        from taskman_ops.host_helper import caddy_files
        receipt = self.execution.receipt
        identifier = receipt['recovery_id']
        if (receipt['site_path'] != SITE_PATH or not isinstance(identifier, str)
                or not re.fullmatch('[0-9a-f]{32}', identifier) or receipt['original_absent']
                or receipt['original_sha256'] != self.original_sha256):
            raise ValueError('fault requires independently bound existing original')
        directory = self.observer._path('/etc/taskman/caddy-recovery') / identifier
        self.observer._parents(directory)
        caddy_files._protected_metadata(self.observer, directory, directory=True)
        source = directory / 'site'
        caddy_files._protected_metadata(self.observer, source)
        data = self.observer._read(source, 4096)
        live = self.observer._path(SITE_PATH)
        if (hashlib.sha256(data).hexdigest() != self.original_sha256
                or hashlib.sha256(self.observer._read(live, 4096)).hexdigest() != self.request.parameters['site_sha256']):
            raise ValueError('fixture checksum authority changed')
        self.directory.write('independent-original', data)
        # Keep within the original site's 4096-byte bound so refusal is checksum,
        # rather than unreadable/oversized recovery evidence.
        _rewrite(source, bytes([data[0] ^ 1]) + data[1:] if data else b'#')

    def _change_fixture_main(self):
        path = self.observer._path('/etc/caddy/Caddyfile')
        self.observer._parents(path)
        metadata = self.observer._metadata(path)
        data = self.observer._read(path, 65536)
        if metadata is None or hashlib.sha256(data).hexdigest() != self.fixture_main_sha256:
            raise ValueError('fixture main identity mismatch')
        self.directory.write('independent-main', data)
        _rewrite(path, data + b'\n# native preservation mismatch\n')


def run_fault(request, directory, *, fault, observer=None, **options):
    """Direct native capability entry; caller owns transport/frame encoding.

    Returns (validated HostResult, hook_fired). False leaves the requested fault
    unproven even if ordinary execution succeeded. Helper-stop events deliberately
    produce no result. Default observer is the unmodified production module.
    """
    from taskman_ops.host_helper.operations import caddy as production
    from taskman_ops.host_helper.caddy_execution import CaddyExecution
    validate_caddy_request(request)
    facade = NativeFaultObserver(production if observer is None else observer,
                                 request, directory, fault=fault, **options)
    token = production._DEADLINE.set(time.monotonic() + (600 if request.parameters['mode'] == 'prepare' else 45))
    try:
        execution = CaddyExecution(facade, request)
        facade.execution = execution
        return execution.run(), facade.fired
    finally:
        production._DEADLINE.reset(token)


def _rewrite(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW), 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def frontend_lock(path=FRONTEND_LOCK, *, owner=0):
    """Acquire native POSIX record lock, refusing contention; never unlink lock."""
    path = Path(path)
    _parents_without_links(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) not in {0o600, 0o640, 0o644}:
        raise ValueError('unsafe native frontend lock')
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


DATABASE_DIGEST_PREAMBLE = """BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL client_encoding = 'UTF8';
SET LOCAL TimeZone = 'UTC';
SET LOCAL DateStyle = 'ISO, YMD';
SET LOCAL IntervalStyle = 'postgres';
SET LOCAL extra_float_digits = 3;
SET LOCAL bytea_output = 'hex';
"""
DATABASE_TABLES_SQL = """SELECT n.nspname, c.relname FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C";"""


def table_copy_sql(schema, table):
    """Catalog-derived identifiers only; all COPY commands share one session."""
    def quote(value):
        if not isinstance(value, str) or not value or '\x00' in value:
            raise ValueError('invalid database identifier')
        return '"' + value.replace('"', '""') + '"'
    return (f'COPY (SELECT to_jsonb(t)::text FROM {quote(schema)}.{quote(table)} AS t '
            'ORDER BY (to_jsonb(t)::text) COLLATE "C") TO STDOUT WITH (FORMAT text);')


def digest_copy_rows(chunks):
    """Hash COPY bytes without retaining rows; stdout must never be logged."""
    digest, count, last = hashlib.sha256(), 0, None
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise ValueError('COPY digest requires bytes')
        if chunk:
            digest.update(chunk)
            count += chunk.count(b'\n')
            last = chunk[-1]
    if last is not None and last != 10:
        raise ValueError('truncated COPY output')
    return {'row_count': count, 'sha256': digest.hexdigest()}
