from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import zipfile

import pytest

from taskman_ops.helper_client.package import (
    ARCHIVE_MEMBERS,
    build_helper_package,
    build_scheduled_backup_package,
    temporary_helper_package,
)


def test_builder_produces_identical_allowlisted_zipapps(tmp_path: Path) -> None:
    """Environment-dependent archive metadata must not change a transferred helper's checksum."""

    first = build_helper_package(tmp_path / "first.pyz")
    second = build_helper_package(tmp_path / "second.pyz")
    first_bytes = first.path.read_bytes()
    second_bytes = second.path.read_bytes()

    assert first_bytes == second_bytes
    assert first.sha256 == hashlib.sha256(first_bytes).hexdigest()
    assert second.sha256 == first.sha256
    assert first.protocol_version == 3
    assert first.identity == f"v3-{first.sha256}"
    assert stat.S_IMODE(first.path.stat().st_mode) == 0o600


def test_builder_writes_only_lexical_fixed_metadata_members(tmp_path: Path) -> None:
    """Archive discovery could otherwise transfer tests, secrets, or controller configuration."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    with zipfile.ZipFile(package.path) as archive:
        members = archive.infolist()

    names = [member.filename for member in members]
    assert names == sorted(names)
    assert names == list(ARCHIVE_MEMBERS)
    assert "taskman_ops/checksums.py" in names
    assert "taskman_ops/host_helper/commands.py" in names
    assert "taskman_ops/host_helper/credentials.py" in names
    assert "taskman_ops/host_helper/database.py" in names
    assert "taskman_ops/host_helper/filesystem.py" in names
    assert "taskman_ops/host_helper/records.py" in names
    assert "taskman_ops/host_helper/backup_protection.py" in names
    assert "taskman_ops/host_helper/backup_helper.py" in names
    assert "taskman_ops/host_helper/restore_target.py" in names
    assert "taskman_ops/host_helper/restore_database.py" in names
    assert "taskman_ops/host_helper/selection.py" in names
    assert "taskman_ops/host_helper/services.py" in names
    assert "taskman_ops/host_helper/state.py" in names
    assert "taskman_ops/releases/manifests.py" in names
    assert not any(
        name in names
        for name in (
            "taskman_ops/host_helper/facts.py",
            "taskman_ops/host_helper/legacy_result.py",
            "taskman_ops/host_helper/lifecycle.py",
            "taskman_ops/host_helper/lifecycle_records.py",
            "taskman_ops/host_helper/operations/legacy_backup.py",
            "taskman_ops/host_helper/runtime.py",
        )
    )
    assert all(member.date_time == (2020, 1, 1, 0, 0, 0) for member in members)
    assert all((member.external_attr >> 16) == (stat.S_IFREG | 0o644) for member in members)
    assert not any(
        forbidden in name.lower()
        for name in names
        if name != "taskman_ops/host_helper/postgresql_configuration.py"
        for forbidden in ("config", "secret", "test", "cache", "pyc", ".git", "metadata")
    )


def test_generated_packages_execute_without_checkout_or_site_packages(tmp_path: Path) -> None:
    import subprocess

    transient = build_helper_package(tmp_path / "transient.pyz")
    scheduled = build_scheduled_backup_package(tmp_path / "scheduled.pyz")

    for package, expected_code in ((transient, 0), (scheduled, 2)):
        completed = subprocess.run(
            ["python3", "-I", "-S", str(package.path)],
            input=b"",
            capture_output=True,
            check=False,
        )
        assert completed.returncode == expected_code
        assert completed.stderr == b""


def test_temporary_package_is_removed_after_its_invocation_scope() -> None:
    """The controller must not leave its local helper archive behind after use."""

    with temporary_helper_package() as package:
        path = package.path
        assert path.is_file()

    assert not path.exists()


@pytest.mark.parametrize("database_state,mode,expected,authenticates", [
    ("absent", "authenticate", 0, False), ("ready", "authenticate", 5, True),
    ("ready", "defer-for-configuration", 0, False),
])
def test_isolated_packaged_private_entry_uses_validated_database_presence(tmp_path, database_state, mode, expected, authenticates):
    import os
    import subprocess
    import sys

    package = build_helper_package(tmp_path / "helper.pyz")
    marker = tmp_path / "authenticated"
    psql = tmp_path / "psql"
    psql.write_text(
        '#!/bin/sh\nif [ "$1" = --version ]; then printf "psql (PostgreSQL) 18.6\\n"; exit 0; fi\n'
        'test -z "${PGPASSWORD:-}" || exit 99\n'
        f'test "$PGPASSFILE" = "{tmp_path}/pgpass.probe" || exit 99\n'
        f'test "$(stat -c %a "$PGPASSFILE")" = 600 || exit 99\n'
        'case "$*" in *"dbname=taskman_prod require_auth=scram-sha-256"*) ;; *) exit 99 ;; esac\n'
        f"printf called > '{marker}'\nexit 1\n"
    )
    psql.chmod(0o700)
    # Keep the archived entry, shared proof and command runner live under -I -S.
    script = '''import sys
from pathlib import Path
sys.argv[0] = sys.argv.pop(1)
sys.path.insert(0, sys.argv[0])
from taskman_ops.host_helper.operations import preflight
from taskman_ops.host_helper import database
preflight._PGPASS_PATH = Path(sys.argv.pop(1))
original_run = database.run_command
psql = sys.argv.pop(1)
def run(argv, **kwargs):
    return original_run((psql, *argv[1:]), **kwargs)
database.run_command = run
from taskman_ops.host_helper.__main__ import main
raise SystemExit(main())
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(package.path), str(tmp_path / "pgpass"), str(psql),
         "provision-pgpass-authority", "127.0.0.1", "5432", "taskman", "taskman_prod", database_state, mode],
        input=b"127.0.0.1:5432:taskman_prod:taskman:secret\n", capture_output=True,
        env=os.environ,
        check=False,
    )
    assert completed.returncode == expected
    assert completed.stdout == completed.stderr == b""
    assert marker.exists() is authenticates
    assert not (tmp_path / "pgpass").exists()
    assert not (tmp_path / "pgpass.probe").exists()


def test_ufw_adapter_has_only_transient_scoped_dependency(tmp_path, monkeypatch):
    from taskman_ops.helper_client import package as builder

    adapter = "taskman_ops/host_helper/ufw_adapter.py"
    assert adapter in builder.ARCHIVE_MEMBERS
    assert adapter not in builder.BACKUP_ARCHIVE_MEMBERS
    source = tmp_path / "source.py"
    source.write_text("import ufw.backend_iptables\n")
    monkeypatch.setitem(builder._SOURCE_MEMBERS, adapter, source)
    build_helper_package(tmp_path / "helper.pyz")
    with pytest.raises(RuntimeError, match="third-party import"):
        builder._build_package(tmp_path / "backup.pyz", tuple(sorted((*builder.BACKUP_ARCHIVE_MEMBERS, adapter))),
                               builder._FIXED_BACKUP_MAIN)
    source.write_text("import requests\n")
    with pytest.raises(RuntimeError, match="third-party import"):
        build_helper_package(tmp_path / "bad.pyz")


@pytest.mark.parametrize("archive", ["transient", "backup"])
def test_ufw_import_in_another_helper_member_is_rejected(tmp_path, monkeypatch, archive):
    from taskman_ops.helper_client import package as builder

    source = tmp_path / "source.py"
    source.write_text("import ufw\n")
    monkeypatch.setitem(builder._SOURCE_MEMBERS, "taskman_ops/host_helper/commands.py", source)
    build = build_helper_package if archive == "transient" else build_scheduled_backup_package
    with pytest.raises(RuntimeError, match="third-party import"):
        build(tmp_path / "bad.pyz")


def test_isolated_archive_dispatches_firewall_and_retains_failed_native_evidence(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from taskman_ops.host_protocol import HostRequest, decode_result, encode_request, validate_firewall_result

    package = build_helper_package(tmp_path / 'helper.pyz')
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps({'active': False, 'rules': [], 'commands': []}))
    native = tmp_path / 'ufw'
    native.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys
from pathlib import Path
path = Path(os.environ['FAKE_STATE'])
data = json.loads(path.read_text())
args = sys.argv[1:]
data['commands'].append(args)
if args == ['--force', 'enable']:
    data['active'] = True
else:
    family = 4 if args[2] == '0.0.0.0/0' else 6
    data['rules'].append([family, args[0], int(args[6])])
path.write_text(json.dumps(data))
if os.environ.get('FAIL_AT') == str(len(data['commands'])):
    sys.exit(1)
''')
    native.chmod(0o700)
    script = '''import sys, os, json
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
from taskman_ops.host_helper.operations import firewall
from taskman_ops.host_helper.ufw_adapter import UfwRule, UfwSnapshot
state_path, executable = Path(sys.argv.pop(1)), sys.argv.pop(1)
def inspect(**kwargs):
    data = json.loads(state_path.read_text())
    rules = tuple(UfwRule(family, 'in', action, 'tcp', '0.0.0.0/0' if family == 4 else '::/0',
        '0.0.0.0/0' if family == 4 else '::/0', None, ((port, port),), '', '', '', '', '', '')
        for family in (4, 6) for rule_family, action, port in data['rules'] if rule_family == family)
    return UfwSnapshot(data['active'], (('default_forward_policy', 'DROP'),
        ('default_input_policy', 'DROP'), ('default_output_policy', 'ACCEPT'), ('ipv6', 'yes')), rules)
firewall.inspect_ufw = inspect
original_run = firewall.run_command
def run(argv, **kwargs):
    return original_run((executable, *argv[1:]), **kwargs)
firewall.run_command = run
from taskman_ops.host_helper.__main__ import main
sys.argv = ['helper.pyz']
raise SystemExit(main())
'''
    ports = {'ssh': 2202, 'application': 4000, 'database': 5432, 'distribution': 6789}
    def invoke(mode, digest=None, **env):
        request = HostRequest(3, 'firewall', 'op-0123456789abcdef0123456789abcdef',
            {'snapshot_sha256': digest}, {}, {'mode': mode, 'ports': ports})
        completed = subprocess.run([sys.executable, '-I', '-S', '-c', script,
            str(package.path), str(state_path), str(native)], input=encode_request(request),
            capture_output=True, check=False, env={**os.environ, 'FAKE_STATE': str(state_path),
                'SSH_CONNECTION': '192.0.2.10 42311 198.51.100.2 2202', **env})
        assert completed.returncode == 0
        assert completed.stderr == b''
        result = decode_result(completed.stdout)
        return validate_firewall_result(request, result)
    preview = invoke('inspect')
    assert preview.outcome == 'succeeded'
    assert not json.loads(state_path.read_text())['commands']
    failed = invoke('converge', preview.state['snapshot_sha256'], FAIL_AT='2')
    assert failed.outcome == 'manual'
    assert failed.state['changed'] and failed.state['possibly_changed']
    assert failed.state['completed'] == (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),)
    assert len(json.loads(state_path.read_text())['commands']) == 2
    refreshed = invoke('inspect')
    done = invoke('converge', refreshed.state['snapshot_sha256'])
    assert done.outcome == 'succeeded'
    assert json.loads(state_path.read_text())['active']
    count = len(json.loads(state_path.read_text())['commands'])
    noop = invoke('converge', done.state['snapshot_sha256'])
    assert not noop.state['changed']
    assert len(json.loads(state_path.read_text())['commands']) == count
