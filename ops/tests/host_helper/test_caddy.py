from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from tests.support.caddy import host, request
from tests.support.listeners import write_ss_fixture




@pytest.mark.parametrize(('content', 'present'), [
    ('import /etc/caddy/sites-enabled/*.caddy\n', True),
    ('import sites-enabled/*.caddy\n', True),
    ('\t import\t"/etc/caddy/sites-enabled/*.caddy"\t# comment\n', True),
    ('import "sites-enabled/*.caddy"\n', True),
    ('# import /etc/caddy/sites-enabled/*.caddy\n', False),
    ('import /etc/caddy/sites-enabled/*\n', False),
    ('import sites-enabled/*.caddy extra\n', False),
    ("import 'sites-enabled/*.caddy'\n", False),
])
def test_literal_import_uses_native_bounded_grep(tmp_path, content, present):
    from taskman_ops.host_helper.operations.caddy import inspect_glob_import
    main = tmp_path / 'Caddyfile'
    main.write_text(content)
    assert inspect_glob_import(main) is present


def test_glob_import_boundary_and_unsafe_files(tmp_path):
    from taskman_ops.host_helper.operations.caddy import inspect_glob_import, CaddyObservationError
    main = tmp_path / 'Caddyfile'
    main.write_bytes(b'#' + b'x' * (65536 - 2) + b'\n')
    assert inspect_glob_import(main) is False
    main.write_bytes(b'x' * 65537)
    with pytest.raises(CaddyObservationError) as error:
        inspect_glob_import(main)
    assert error.value.exit_code == 5
    main.unlink()
    assert inspect_glob_import(main) is False
    main.symlink_to(tmp_path / 'absent')
    with pytest.raises(CaddyObservationError):
        inspect_glob_import(main)


def test_grep_errors_are_unavailable_and_output_does_not_escape(tmp_path, monkeypatch):
    from taskman_ops.host_helper.operations import caddy as module
    main = tmp_path / 'Caddyfile'
    main.write_text('private-secret\n')
    monkeypatch.setattr(module, '_command', lambda argv: (2, b'', b'private-secret'))
    with pytest.raises(module.CaddyObservationError) as error:
        module.inspect_glob_import(main)
    assert error.value.exit_code == 5
    assert 'private-secret' not in str(error.value)




@pytest.mark.parametrize(('installed', 'active', 'masked', 'config', 'main', 'branch'), [
    (True, True, False, True, True, 'existing'),
    (True, False, False, True, True, 'existing'),
    (True, False, True, True, True, 'existing'),
    (True, False, True, False, False, 'existing'),
    (False, False, False, True, True, 'preserve-configuration'),
    (False, False, False, True, False, 'preserve-configuration'),
    (False, False, True, True, False, 'preserve-configuration'),
    (False, False, False, False, False, 'bootstrap'),
])
def test_observation_selects_ownership_without_taskman(tmp_path, monkeypatch, installed, active, masked, config, main, branch):
    module, root, _ = host(tmp_path, monkeypatch, installed=installed, active=active, masked=masked, config=config, main=main)
    result = module.caddy(request())
    assert result.outcome == 'succeeded', result.to_mapping()
    assert result.state['authority']['branch'] == branch
    assert result.state['authority']['service_masked'] is masked
    assert result.state['mutation_state'] == 'unchanged'
    assert ('main_sha256' in result.state['authority']) is (branch == 'preserve-configuration')


@pytest.mark.parametrize('broken', ['unit', 'executable', 'foreign-unit', 'listener', 'systemctl', 'dpkg', 'unsafe-parent', 'platform'])
def test_authority_failures_never_become_absence(tmp_path, monkeypatch, broken):
    module, root, command = host(tmp_path, monkeypatch)
    if broken in {'unit', 'executable'}:
        (root / ('usr/lib/systemd/system/caddy.service' if broken == 'unit' else 'usr/bin/caddy')).write_text('modified')
    elif broken == 'foreign-unit':
        (root / 'etc/systemd/system/caddy.service').write_text('foreign')
    elif broken == 'unsafe-parent':
        (root / 'etc/caddy').chmod(0o777)
    elif broken == 'platform':
        (root / 'etc/os-release').write_text('ID=debian\nVERSION_ID="26.04"\n')
    else:
        def failing(argv):
            if argv[0] == {'systemctl': 'systemctl', 'dpkg': 'dpkg-query', 'listener': 'ss'}[broken]:
                return (0, b'LISTEN 0 1 0.0.0.0:80 0.0.0.0:* users:(("other",pid=9,fd=1))\n', b'') if broken == 'listener' else (2, b'', b'private error')
            return command(argv)
        monkeypatch.setattr(module, '_command', failing)
    result = module.caddy(request())
    assert result.outcome != 'succeeded'
    assert result.state['authority'] is None
    assert result.state['exit_code'] == (2 if broken == 'platform' else 5 if broken in {'systemctl', 'dpkg'} else 10)


def test_existing_main_safe_metadata_and_unrelated_bytes_do_not_bind_confirmation(tmp_path, monkeypatch):
    module, root, _ = host(tmp_path, monkeypatch)
    main = root / 'etc/caddy/Caddyfile'
    main.chmod(0o640)
    before = module.caddy(request()).state['authority']
    assert before['main'] == {'uid': 0, 'gid': 998, 'mode': 0o640}
    main.write_text('import sites-enabled/*.caddy\n# unrelated operator edit\n')
    after = module.caddy(request()).state['authority']
    assert before == after
    assert main.read_bytes() == b'import sites-enabled/*.caddy\n# unrelated operator edit\n'


def test_write_modes_validate_and_refuse_without_mutation(tmp_path, monkeypatch):
    module, _, _ = host(tmp_path, monkeypatch)
    authority = module.caddy(request()).state['authority']
    result = module.caddy(request('guard-install', authority))
    assert result.outcome == 'refused'
    assert result.state['exit_code'] == 10
    assert result.state['mutation_state'] == 'unchanged'


def test_absent_with_original_mask_and_no_configuration_refuses(tmp_path, monkeypatch):
    module, _, _ = host(tmp_path, monkeypatch, installed=False, active=False, config=False, main=False, masked=True)
    assert module.caddy(request()).state['exit_code'] == 10


def test_main_read_failure_is_inspection_failure(tmp_path, monkeypatch):
    module, root, _ = host(tmp_path, monkeypatch)
    original = Path.open
    def denied(path, *args, **kwargs):
        if path == root / 'etc/caddy/Caddyfile':
            raise PermissionError('secret filename')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', denied)
    result = module.caddy(request())
    assert result.state['exit_code'] == 5
    assert 'secret' not in result.message


def test_masked_underlying_dropin_does_not_gain_package_authority(tmp_path, monkeypatch):
    module, root, _ = host(tmp_path, monkeypatch, active=False, masked=True)
    (root / 'etc/systemd/system/caddy.service.d').mkdir()
    (root / 'etc/systemd/system/caddy.service.d/override.conf').write_text('[Service]\nExecStart=/opt/foreign\n')
    assert module.caddy(request()).state['exit_code'] == 10


def test_parent_symlinks_refuse_even_with_safe_child_metadata(tmp_path, monkeypatch):
    module, root, _ = host(tmp_path, monkeypatch)
    (root / 'etc/systemd').rename(root / 'etc/other-systemd')
    (root / 'etc/systemd').symlink_to(root / 'etc/other-systemd')
    assert module.caddy(request()).state['exit_code'] == 10


def test_native_status_adapter_preserves_status_and_bounds(monkeypatch):
    from taskman_ops.host_helper.operations import caddy as module
    assert module._command(('grep', '-Ec', '--', '^match$', '/dev/null'))[:2] == (1, b'0\n')
    with pytest.raises(Exception):
        module._command(('python3', '-c', 'import sys; sys.stdout.write("x"*16385)'))


def test_executable_parent_write_authority_refuses(tmp_path, monkeypatch):
    module, root, _ = host(tmp_path, monkeypatch)
    (root / 'usr/bin').chmod(0o777)
    assert module.caddy(request()).state['exit_code'] == 10


def test_grep_unknown_diagnostics_are_not_absence(tmp_path, monkeypatch):
    from taskman_ops.host_helper.operations import caddy as module
    main = tmp_path / 'Caddyfile'
    main.write_text('# no import\n')
    monkeypatch.setattr(module, '_command', lambda argv: (1, b'0\n', b'unavailable input'))
    with pytest.raises(module.CaddyObservationError):
        module.inspect_glob_import(main)


@pytest.mark.parametrize('listeners', [b'', b'LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:(("caddy",pid=123,fd=8))\n'])
def test_active_caddy_requires_both_supported_public_ports(tmp_path, monkeypatch, listeners):
    module, _, command = host(tmp_path, monkeypatch)
    monkeypatch.setattr(module, '_command', lambda argv: (0, listeners, b'') if argv[0] == 'ss' else command(argv))
    assert module.caddy(request()).state['exit_code'] == 10


@pytest.mark.parametrize('oversized_public_output', [False, True])
def test_public_listener_query_excludes_large_unrelated_process_output(tmp_path, monkeypatch, oversized_public_output):
    from taskman_ops.host_helper.operations import caddy as module
    native_command = module._command
    module, _, fixture_command = host(tmp_path, monkeypatch)
    query = ('ss', '-H', '-ltnp', '( sport = :80 or sport = :443 )')
    selected = fixture_command(query)[1]
    if oversized_public_output:
        selected += b' ' * (16 * 1024)
    executable = write_ss_fixture(tmp_path, query[1:], selected)
    queries = []

    def command(argv):
        if argv[0] == 'ss':
            queries.append(argv)
            return native_command((str(executable), *argv[1:]))
        return fixture_command(argv)

    monkeypatch.setattr(module, '_command', command)
    result = module.caddy(request())
    assert queries == [query]
    assert result.state['mutation_state'] == 'unchanged'
    if oversized_public_output:
        assert result.outcome == 'retryable'
        assert result.state['exit_code'] == 5
        assert result.state['authority'] is None
    else:
        assert result.outcome == 'succeeded', result.to_mapping()
        assert result.state['authority']['public_listeners'] == (
            {'address': '0.0.0.0', 'port': 80, 'pid': 123},
            {'address': '::', 'port': 443, 'pid': 123},
        )


@pytest.mark.skipif(shutil.which('ss') is None, reason='native ss is unavailable')
def test_native_public_listener_filter_excludes_an_unrelated_socket():
    from taskman_ops.host_helper.operations import caddy as module
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        status, own_output, diagnostic = module._command(('ss', '-H', '-ltnp', f'sport = :{port}'))
        assert status == 0 and diagnostic == b''
        assert f'127.0.0.1:{port}'.encode() in own_output
        status, selected, diagnostic = module._command(('ss', '-H', '-ltnp', '( sport = :80 or sport = :443 )'))
        assert status == 0 and diagnostic == b''
        assert f'127.0.0.1:{port}'.encode() not in selected
        assert all(int(line.split()[3].rsplit(b':', 1)[1]) in {80, 443} for line in selected.splitlines())


def test_foreign_local_executable_is_not_positive_installation_absence(tmp_path, monkeypatch):
    module, root, _ = host(tmp_path, monkeypatch, installed=False, active=False, config=False, main=False)
    (root / 'usr/local/bin').mkdir(parents=True)
    (root / 'usr/local/bin/caddy').write_text('foreign executable')
    (root / 'usr/local/bin/caddy').chmod(0o755)
    assert module.caddy(request()).state['exit_code'] == 10


@pytest.mark.parametrize(('resource', 'mode', 'gid'), [
    ('usr/bin/caddy', 0o644, 0),
    ('usr/lib/systemd/system/caddy.service', 0o600, 0),
    ('usr/lib/systemd/system/caddy-api.service', 0o600, 0),
    ('usr/lib/systemd/system/caddy.service', 0o644, 998),
])
def test_package_permission_drift_revokes_trust_and_confirmation(tmp_path, monkeypatch, resource, mode, gid):
    from tests.support.environments import environment_config
    from taskman_ops.services.caddy import build_caddy_ownership_plan, validate_caddy_plan_authority
    from taskman_ops.errors import OpsError
    module, root, _ = host(tmp_path, monkeypatch)
    initial = module.caddy(request())
    assert initial.outcome == 'succeeded'
    plan = build_caddy_ownership_plan(environment_config(application_port=4011), initial)
    changed = root / resource
    changed.chmod(mode)
    original_stat = module._stat
    def changed_stat(path):
        info = original_stat(path)
        if path == changed and info is not None:
            fields = list(info)
            fields[5] = gid
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(module, '_stat', changed_stat)
    fresh = module.caddy(request())
    assert fresh.state['exit_code'] == 10
    assert fresh.state['authority'] is None
    with pytest.raises(OpsError) as error:
        validate_caddy_plan_authority(plan, fresh)
    assert error.value.status == 10


@pytest.mark.parametrize('owners', [
    'users:(("caddy",pid=123,fd=8),("foreign",fd=9))',
    'users:(("caddy",pid=123,fd=8)) unexpected',
])
def test_public_socket_rejects_unconsumed_or_partial_owner_evidence(tmp_path, monkeypatch, owners):
    module, _, command = host(tmp_path, monkeypatch)
    output = ''.join(f'LISTEN 0 4096 0.0.0.0:{port} 0.0.0.0:* {owners}\n' for port in (80, 443)).encode()
    monkeypatch.setattr(module, '_command', lambda argv: (0, output, b'') if argv[0] == 'ss' else command(argv))
    result = module.caddy(request())
    assert result.state['exit_code'] == 10
    assert result.state['authority'] is None


@pytest.mark.parametrize(('mode', 'gid', 'groups', 'exit_code'), [
    (0o750, 998, [998], 0),
    (0o745, 998, [998], 10),
    (0o750, 999, [998], 10),
    (0o711, 0, [998], 0),
    (0o750, 999, [998, 999], 0),
])
def test_executable_access_uses_caddy_identity_permission_precedence(tmp_path, monkeypatch, mode, gid, groups, exit_code):
    module, root, _ = host(tmp_path, monkeypatch)
    executable = root / 'usr/bin/caddy'
    executable.chmod(mode)
    original_stat = module._stat
    def file_group(path):
        info = original_stat(path)
        if path == executable and info is not None:
            fields = list(info)
            fields[5] = gid
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(module, '_stat', file_group)
    monkeypatch.setattr(os, 'getgrouplist', lambda name, primary_gid: groups)
    assert module.caddy(request()).state['exit_code'] == exit_code


@pytest.mark.parametrize('unknown', ['account', 'groups'])
def test_unknown_caddy_identity_is_inspection_failure(tmp_path, monkeypatch, unknown):
    import pwd
    module, _, _ = host(tmp_path, monkeypatch)
    def unavailable(*args):
        raise KeyError('private identity evidence')
    monkeypatch.setattr(pwd if unknown == 'account' else os,
                        'getpwnam' if unknown == 'account' else 'getgrouplist', unavailable)
    result = module.caddy(request())
    assert result.state['exit_code'] == 5
    assert result.state['authority'] is None
    assert 'private' not in result.message
