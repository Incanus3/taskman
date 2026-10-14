"""Local guard/correlation checks; these do not establish native host acceptance."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

from tests.services import native_caddy_acceptance as harness
from tests.support.caddy import mutation_host, request


def private(tmp_path):
    root = tmp_path / 'receipts'
    root.mkdir(mode=0o700)
    return harness.PrivateDirectory(root, owner=os.getuid())


def test_private_files_refuse_symlinks_unsafe_mode_and_overwrite(tmp_path):
    directory = private(tmp_path)
    directory.write('original', b'protected')
    assert directory.read('original') == b'protected'
    assert (directory.root / 'original').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        directory.write('original', b'wrong')
    for name in ('../escape', '/absolute', 'nested/file'):
        with pytest.raises(ValueError):
            directory.write(name, b'wrong')
    (directory.root / 'link').symlink_to(directory.root / 'original')
    with pytest.raises(ValueError):
        directory.read('link')
    (directory.root / 'original').chmod(0o644)
    with pytest.raises(ValueError):
        directory.read('original')


def test_private_directory_refuses_linked_parent(tmp_path):
    target = tmp_path / 'real'
    target.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(target)
    nested = target / 'receipts'
    nested.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        harness.PrivateDirectory(link / 'receipts', owner=os.getuid())


@pytest.mark.parametrize('publication,pause_at', [('ack', 'write'), ('release', 'staging-unlink')])
def test_checkpoint_polling_sees_only_complete_publications(tmp_path, monkeypatch, publication, pause_at):
    directory = private(tmp_path)
    req = request()
    barrier = harness.Checkpoint(directory, req)
    paused, polled, resume = threading.Event(), threading.Event(), threading.Event()
    errors, completed = [], []
    ack_published = threading.Event()
    original_fdopen = os.fdopen
    original_unlink = os.unlink
    target = barrier.ack_name if publication == 'ack' else barrier.release_name

    class PausedStream:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def write(self, data):
            if pause_at == 'write' and (publication == 'ack' and b'native_operation' in data
                    or publication == 'release' and b'invocation_closed' in data):
                paused.set()
                assert resume.wait(2)
            return self.stream.write(data)

    def fdopen(descriptor, mode):
        stream = original_fdopen(descriptor, mode)
        return PausedStream(stream) if mode == 'wb' else stream

    def poll_wait(_seconds):
        assert paused.wait(2)
        polled.set()
        assert resume.wait(2)

    def unlink(path, *args, **kwargs):
        if pause_at == 'staging-unlink' and directory.path(target).exists():
            paused.set()
            assert resume.wait(2)
        return original_unlink(path, *args, **kwargs)

    def stop(code):
        raise SystemExit(code)

    class Observer:
        connected = True
        def read_ack(self, *, timeout_seconds):
            return barrier.read_ack('reload-reply-loss')
        def helper_identity(self, pid, *, timeout_seconds):
            return harness.helper_identity(pid)
        def release(self, ack, *, timeout_seconds):
            assert invocation.closed
            barrier.release(ack, invocation_closed=True)

    class Invocation:
        closed = False
        def close(self):
            self.closed = True

    invocation = Invocation()
    def run(operation):
        try:
            completed.append(operation())
        except BaseException as error:
            errors.append(error)
            polled.set()

    monkeypatch.setattr(harness.os, 'fdopen', fdopen)
    monkeypatch.setattr(harness.os, 'unlink', unlink)
    monkeypatch.setattr(harness.time, 'sleep', poll_wait)
    helper = threading.Thread(target=run, args=(lambda: barrier.wait('reload-reply-loss', terminate=stop,
        on_published=ack_published.set, timeout_seconds=2),))
    controller = threading.Thread(target=run, args=(lambda: harness.ReplyLossController(
        Observer(), req).close_at_checkpoint(invocation),))
    helper.start()
    if publication == 'release':
        assert ack_published.wait(2)
    controller.start()
    try:
        assert paused.wait(2)
        assert polled.wait(2)
        assert errors == []
        if pause_at == 'write':
            assert not directory.path(target).exists()
        else:
            assert directory.path(target).stat().st_nlink == 2
            with pytest.raises(FileNotFoundError):
                directory.read(target)
        if publication == 'ack':
            assert not invocation.closed
    finally:
        resume.set()
        helper.join(2)
        controller.join(2)
    assert not helper.is_alive() and not controller.is_alive()
    assert errors == []
    assert len(completed) == 2
    assert completed[0] == completed[1]
    assert invocation.closed
    assert barrier.read_ack('reload-reply-loss')['native_return_status'] == 0


@pytest.mark.parametrize('failed_sync', ['file', 'directory'])
def test_failed_publication_sync_cannot_return_checkpoint_reply(tmp_path, monkeypatch, failed_sync):
    import stat
    directory = private(tmp_path)
    directory.write('retained', b'evidence')
    barrier = harness.Checkpoint(directory, request())
    original_fsync = os.fsync
    def fsync(descriptor):
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if is_directory == (failed_sync == 'directory'):
            raise OSError('publication sync failed')
        return original_fsync(descriptor)
    def stop(code):
        raise SystemExit(code)
    monkeypatch.setattr(harness.os, 'fsync', fsync)
    with pytest.raises(SystemExit) as stopped:
        barrier.wait('reload-reply-loss', terminate=stop)
    assert stopped.value.code == 87
    assert directory.read('retained') == b'evidence'
    assert not directory.path(barrier.release_name).exists()
    if failed_sync == 'file':
        assert not directory.path(barrier.ack_name).exists()
    else:
        assert barrier.read_ack('reload-reply-loss')['native_return_status'] == 0
    assert not list(directory.root.glob('.receipt-*'))


def test_checkpoint_refuses_release_permission_drift_before_open(tmp_path, monkeypatch):
    directory = private(tmp_path)
    barrier = harness.Checkpoint(directory, request())
    original_open = os.open
    def changed_open(path, flags, *args, **kwargs):
        if Path(path) == directory.path(barrier.release_name) and flags & os.O_NOFOLLOW:
            Path(path).chmod(0o644)
        return original_open(path, flags, *args, **kwargs)
    def release():
        barrier.release(barrier.read_ack('reload-reply-loss'), invocation_closed=True)
        monkeypatch.setattr(harness.os, 'open', changed_open)
    def stop(code):
        raise SystemExit(code)
    with pytest.raises(SystemExit) as stopped:
        barrier.wait('reload-reply-loss', terminate=stop, on_published=release)
    assert stopped.value.code == 87
    assert directory.path(barrier.release_name).stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize('boundary,change', [
    ('open', 'permission'), ('open', 'replacement'),
    ('read', 'content'), ('final-path', 'replacement'),
])
def test_private_read_refuses_authority_drift(tmp_path, monkeypatch, boundary, change):
    directory = private(tmp_path)
    directory.write('receipt', b'original')
    path = directory.path('receipt')
    def mutate():
        if change == 'permission':
            path.chmod(0o644)
        elif change == 'replacement':
            replacement = directory.path('replacement')
            replacement.write_bytes(b'original')
            replacement.chmod(0o600)
            replacement.replace(path)
        else:
            info = path.stat()
            path.write_bytes(b'modified')
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
    if boundary == 'open':
        original_open = os.open
        def changed_open(*args, **kwargs):
            mutate()
            return original_open(*args, **kwargs)
        monkeypatch.setattr(harness.os, 'open', changed_open)
    elif boundary == 'read':
        original_fdopen = os.fdopen
        class ChangedStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def fileno(self):
                return self.stream.fileno()
            def read(self, size):
                data = self.stream.read(size)
                mutate()
                return data
        monkeypatch.setattr(harness.os, 'fdopen', lambda *args: ChangedStream(original_fdopen(*args)))
    else:
        original_lstat = Path.lstat
        calls = 0
        def changed_lstat(self, *args, **kwargs):
            nonlocal calls
            if self == path:
                calls += 1
                if calls == 2:
                    mutate()
            return original_lstat(self, *args, **kwargs)
        monkeypatch.setattr(Path, 'lstat', changed_lstat)
    with pytest.raises(ValueError):
        directory.read('receipt')


def test_private_read_accepts_unchanged_bounded_bytes_and_rejects_oversize(tmp_path):
    directory = private(tmp_path)
    for name, content in [('empty', b''), ('limit', b'x' * 65536)]:
        directory.write(name, content)
        assert directory.read(name) == content
        assert directory.read(name) == content  # ordinary atime changes are irrelevant
    directory.write('oversize', b'x' * 65537)
    with pytest.raises(ValueError, match='oversized'):
        directory.read('oversize')


@pytest.mark.parametrize('occupied', ['file', 'directory', 'symlink'])
def test_atomic_publication_preserves_occupied_final_path(tmp_path, monkeypatch, occupied):
    directory = private(tmp_path)
    directory.write('retained', b'evidence')
    destination = directory.path('occupied')
    if occupied == 'file':
        destination.write_bytes(b'previous')
        destination.chmod(0o600)
    elif occupied == 'directory':
        destination.mkdir(mode=0o700)
    else:
        destination.symlink_to(directory.path('retained'))
    before = destination.lstat()
    with pytest.raises(FileExistsError):
        directory.write('occupied', b'candidate')
    assert destination.lstat() == before
    assert directory.read('retained') == b'evidence'
    if occupied == 'file':
        assert directory.read('occupied') == b'previous'
    assert not list(directory.root.glob('.receipt-*'))


def test_saved_original_fault_at_size_limit_reaches_checksum_refusal(tmp_path, monkeypatch):
    from taskman_ops.host_helper.caddy_execution import CaddyExecution
    from taskman_ops.host_helper import caddy_files
    original = b'a' * 4096
    module, _, _ = mutation_host(tmp_path, monkeypatch, original=original)
    authority = module.observe_caddy()
    req = request('prepare', expected=authority)
    wrapper = harness.NativeFaultObserver(module, req, private(tmp_path),
        fault='saved-original-checksum', original_sha256=hashlib.sha256(original).hexdigest())
    execution = CaddyExecution(wrapper, req)
    wrapper.execution = execution
    execution.prepare_site(authority)
    with pytest.raises(ValueError, match='checksum mismatch'):
        caddy_files.restore_site(module, execution.receipt)


def test_barrier_excludes_reply_until_correlated_channel_closed(tmp_path):
    directory = private(tmp_path)
    req = request()
    req = request('activate', expected={}, receipt=None)  # barrier correlates envelope only
    barrier = harness.Checkpoint(directory, req)
    reached = threading.Event()
    completed = threading.Event()
    errors = []
    def helper():
        try:
            barrier.wait('reload-reply-loss', on_published=reached.set)
            completed.set()
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=helper)
    worker.start()
    assert reached.wait(2)
    try:
        ack = barrier.read_ack('reload-reply-loss')
        assert ack['native_return_status'] == 0
        assert not completed.is_set()
        with pytest.raises(ValueError):
            barrier.release(ack, invocation_closed=False)
        barrier.release(ack, invocation_closed=True)
    finally:
        worker.join(2)
    assert not worker.is_alive()
    assert errors == []
    assert completed.is_set()


def test_checkpoint_rejects_other_request_or_changed_helper_identity(tmp_path):
    directory = private(tmp_path)
    barrier = harness.Checkpoint(directory, request())
    barrier.publish('frontend-lock')
    mapping = request().to_mapping()
    mapping['correlation_id'] = 'op-' + '2' * 32
    from taskman_ops.host_protocol import HostRequest
    other = harness.Checkpoint(directory, HostRequest.from_mapping(mapping))
    with pytest.raises(FileNotFoundError):
        other.read_ack('frontend-lock')
    ack = barrier.read_ack('frontend-lock')
    ack['helper_identity']['start_ticks'] += 1
    with pytest.raises(ValueError):
        barrier.release(ack, invocation_closed=False)


def test_expired_checkpoint_cannot_return_a_framed_reply(tmp_path, monkeypatch):
    directory = private(tmp_path)
    barrier = harness.Checkpoint(directory, request())
    ticks = iter((0.0, 0.0, 16.0))
    monkeypatch.setattr(harness.time, 'monotonic', lambda: next(ticks, 16.0))
    def stop(code):
        raise SystemExit(code)
    with pytest.raises(SystemExit) as stopped:
        barrier.wait('reload-reply-loss', terminate=stop)
    assert stopped.value.code == 87
    assert not (directory.root / barrier.release_name).exists()


@pytest.mark.parametrize('fault', ['terminate-after-apt', 'preservation-mismatch'])
def test_apt_callback_requires_exact_successful_native_install(tmp_path, monkeypatch, fault):
    from taskman_ops.host_helper.commands import CommandError
    module, root, _ = mutation_host(tmp_path, monkeypatch)
    main = root / 'etc/caddy/Caddyfile'
    baseline = main.read_bytes()
    req = request('prepare', expected=module.observe_caddy())
    def stop(code):
        raise SystemExit(code)
    directory = private(tmp_path)
    wrapper = harness.NativeFaultObserver(module, req, directory, fault=fault,
        fixture_main_sha256=hashlib.sha256(baseline).hexdigest(), terminate=stop)
    version = '2.13.1-1'
    argv = harness.install_argv(Path('/tmp/verified-cache'), version)
    def fail(*args, **kwargs):
        raise CommandError('command exited unsuccessfully')
    monkeypatch.setattr(module, '_native', fail)
    with pytest.raises(CommandError):
        wrapper._native(argv, timeout_seconds=300)
    assert main.read_bytes() == baseline
    assert not wrapper.fired
    monkeypatch.setattr(module, '_native', lambda *args, **kwargs: b'')
    if fault == 'terminate-after-apt':
        with pytest.raises(SystemExit) as stopped:
            wrapper._native(argv, timeout_seconds=300)
        assert stopped.value.code == 86
        assert main.read_bytes() == baseline
        assert harness.Checkpoint(directory, req).read_ack('post-apt')['native_operation'] == 'apt-get install caddy=' + version
    else:
        wrapper._native(argv, timeout_seconds=300)
        assert main.read_bytes() != baseline
        assert main.stat().st_mode & 0o777 == 0o644
    assert wrapper.fired


def test_apt_fault_does_not_fire_for_mismatched_native_install_argv(tmp_path, monkeypatch):
    module, root, calls = mutation_host(tmp_path, monkeypatch)
    main = root / 'etc/caddy/Caddyfile'
    baseline = main.read_bytes()
    directory = private(tmp_path)
    req = request('prepare', expected=module.observe_caddy())
    wrapper = harness.NativeFaultObserver(module, req, directory, fault='terminate-after-apt')
    argv = tuple('APT::Get::AllowUnauthenticated=true' if item == 'APT::Get::AllowUnauthenticated=false' else item
                 for item in harness.install_argv(Path('/tmp/verified-cache'), '2.13.1-1'))
    wrapper._native(argv, timeout_seconds=300)
    assert not wrapper.fired
    assert calls == [(argv, {'timeout_seconds': 300})]
    assert main.read_bytes() == baseline
    assert not list(directory.root.iterdir())


def test_native_frontend_lock_releases_without_unlink_or_replacement(tmp_path):
    import fcntl
    import subprocess
    import sys
    lock = tmp_path / 'lock-frontend'
    lock.touch(mode=0o640)
    identity = lock.stat().st_ino
    script = 'import fcntl,sys; f=open(sys.argv[1],"r+"); fcntl.lockf(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
    with harness.frontend_lock(lock, owner=os.getuid()):
        result = subprocess.run([sys.executable, '-c', script, str(lock)], capture_output=True)
        assert result.returncode != 0
    result = subprocess.run([sys.executable, '-c', script, str(lock)], capture_output=True)
    assert result.returncode == 0
    assert lock.stat().st_ino == identity


def test_frontend_lock_does_not_claim_an_existing_holder(tmp_path):
    import subprocess
    import sys
    lock = tmp_path / 'lock-frontend'
    lock.touch(mode=0o640)
    script = 'import fcntl,sys; f=open(sys.argv[1],"r+"); fcntl.lockf(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()'
    child = subprocess.Popen([sys.executable, '-c', script, str(lock)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.stdout.readline() == b'ready\n'
        with pytest.raises(BlockingIOError):
            with harness.frontend_lock(lock, owner=os.getuid()):
                pytest.fail('must refuse preexisting holder')
    finally:
        child.communicate(timeout=2)
    assert child.returncode == 0


def test_database_digest_handles_chunk_boundaries_duplicates_and_no_rows():
    digest = harness.digest_copy_rows([b'a\\n', b'b\na\\n', b'b\n'])
    assert digest == {'row_count': 2, 'sha256': hashlib.sha256(b'a\\nb\na\\nb\n').hexdigest()}
    assert harness.digest_copy_rows([]) == {'row_count': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
    with pytest.raises(ValueError):
        harness.digest_copy_rows([b'unterminated'])


def test_database_sql_quotes_catalog_identifiers_and_fixes_byte_order():
    sql = harness.table_copy_sql('public', 'odd"table')
    assert 'FROM "public"."odd""table" AS t' in sql
    assert 'ORDER BY (to_jsonb(t)::text) COLLATE "C"' in sql
    assert 'TO STDOUT WITH (FORMAT text)' in sql
    with pytest.raises(ValueError):
        harness.table_copy_sql('public', 'bad\x00')


def test_wrong_release_and_unreadable_checkpoint_exit_without_helper_reply(tmp_path):
    directory = private(tmp_path)
    barrier = harness.Checkpoint(directory, request())
    def invalid_release():
        directory.write(barrier.release_name, b'{"unrelated":true}')
    def stop(code):
        raise SystemExit(code)
    with pytest.raises(SystemExit) as stopped:
        barrier.wait('reload-reply-loss', terminate=stop, on_published=invalid_release)
    assert stopped.value.code == 87


def test_barrier_cannot_exceed_fifteen_seconds(tmp_path):
    barrier = harness.Checkpoint(private(tmp_path), request())
    with pytest.raises(ValueError):
        barrier.wait('reload-reply-loss', timeout_seconds=16)
    assert not (barrier.directory.root / barrier.ack_name).exists()


def test_controller_refuses_identity_drift_before_physical_closure(tmp_path):
    barrier = harness.Checkpoint(private(tmp_path), request())
    barrier.publish('reload-reply-loss')
    class Observer:
        connected = True
        def read_ack(self, **kwargs):
            return barrier.read_ack('reload-reply-loss')
        def helper_identity(self, pid, **kwargs):
            return {'pid': pid, 'start_ticks': 1}
        def release(self, ack, **kwargs):
            pytest.fail('must not release changed helper')
    class Invocation:
        closed = False
        def close(self):
            self.closed = True
    invocation = Invocation()
    observer = Observer()
    with pytest.raises(ValueError):
        harness.ReplyLossController(observer, request()).close_at_checkpoint(invocation)
    assert not invocation.closed
    observer.connected = False
    with pytest.raises(ValueError):
        harness.ReplyLossController(observer, request())


def test_corrupted_saved_original_retains_primary_validation_failure(tmp_path, monkeypatch):
    module, root, _ = mutation_host(tmp_path, monkeypatch, original=b'old fixture\n')
    directory = private(tmp_path)
    baseline = module.observe_caddy()
    def native(argv, **kwargs):
        if (root / 'etc/caddy/sites-enabled/taskman.caddy').read_bytes() == request().parameters['site_content'].encode():
            raise RuntimeError('suppressed candidate validation diagnostics')
        return b''
    monkeypatch.setattr(module, '_native', native)
    result, fired = harness.run_fault(request('prepare', expected=baseline), directory,
        fault='saved-original-checksum', observer=module,
        original_sha256=hashlib.sha256(b'old fixture\n').hexdigest())
    assert fired
    assert result.state['exit_code'] == 8
    assert result.state['failed_boundary'] == 'validation'
    assert result.state['recovery'] == {'status': 'failed', 'runtime_uncertain': True}
    assert result.state['mutation_state'] == 'changed'
    assert 'suppressed' not in str(result.to_mapping())
    assert directory.read('independent-original') == b'old fixture\n'
    assert (root / 'etc/caddy/sites-enabled/taskman.caddy').read_bytes() == request().parameters['site_content'].encode()
    assert (root / 'etc/caddy/Caddyfile').read_text() == 'import sites-enabled/*.caddy\n'
    assert (root / 'etc/caddy/sites-enabled/other.caddy').read_bytes() == b'operator sibling\n'


def test_post_install_mismatch_uses_actual_preservation_check(tmp_path, monkeypatch):
    from tests.support.caddy import installation_host
    version = '2.13.1-1'
    module, root, state, _ = installation_host(tmp_path, monkeypatch, version=version)
    authority = module.caddy(request()).state['authority']
    guarded = module.caddy(request('guard-install', expected=authority))
    main = root / 'etc/caddy/Caddyfile'
    original = main.read_bytes()
    result, fired = harness.run_fault(
        request('prepare', expected=authority, receipt=guarded.state['receipt']), private(tmp_path),
        fault='preservation-mismatch', observer=module, fixture_main_sha256=hashlib.sha256(original).hexdigest())
    assert fired
    assert state['installed']
    assert result.state['exit_code'] == 8
    assert result.state['failed_boundary'] == 'package'
    assert result.state['mutation_state'] == 'changed'
    assert result.state['receipt']['installed_package_version'] == version
    assert not (root / 'etc/caddy/sites-enabled/taskman.caddy').exists()
    assert (root / 'etc/systemd/system/caddy.service').readlink() == Path('/dev/null')
    assert main.read_bytes() != original


@pytest.mark.parametrize('termination_returns', [False, True])
def test_failed_post_apt_receipt_stops_without_ordinary_result(tmp_path, monkeypatch, termination_returns):
    from tests.support.caddy import installation_host
    module, root, state, _ = installation_host(tmp_path, monkeypatch)
    authority = module.caddy(request()).state['authority']
    guarded = module.caddy(request('guard-install', expected=authority))
    class BrokenReceipt:
        def write(self, name, data):
            raise OSError('suppressed receipt fsync failure')
    stopped = []
    def terminate(code):
        stopped.append(code)
        if not termination_returns:
            raise SystemExit(code)
    with pytest.raises(SystemExit) as ended:
        harness.run_fault(request('prepare', expected=authority, receipt=guarded.state['receipt']),
            BrokenReceipt(), fault='terminate-after-apt', observer=module, terminate=terminate)
    assert ended.value.code == 87
    assert stopped == [87]
    assert state['installed']
    assert not (root / 'etc/caddy/sites-enabled/taskman.caddy').exists()
    assert (root / 'etc/systemd/system/caddy.service').readlink() == Path('/dev/null')
