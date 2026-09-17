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


@pytest.mark.parametrize("database_state,expected,authenticates", [("absent", 0, False), ("ready", 10, True)])
def test_isolated_packaged_private_entry_uses_validated_database_presence(tmp_path, database_state, expected, authenticates):
    import os
    import subprocess
    import sys

    package = build_helper_package(tmp_path / "helper.pyz")
    marker = tmp_path / "authenticated"
    psql = tmp_path / "psql"
    psql.write_text(
        '#!/bin/sh\n[ "$*" = "--no-psqlrc --quiet --host 127.0.0.1 --port 5432 --username taskman --dbname taskman_prod --no-password --command SELECT 1 --output /dev/null" ] || exit 99\n'
        f"printf called > '{marker}'\nexit 1\n"
    )
    psql.chmod(0o700)
    # Adapt the protected file location and external psql executable; use the archived production
    # dispatcher, parser and native command runner in an isolated interpreter.
    script = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
from taskman_ops.host_helper.operations import preflight
preflight._PGPASS_PATH = Path(sys.argv.pop(1))
original_run = preflight.run_command
psql = sys.argv.pop(1)
def run(argv, **kwargs):
    return original_run((psql, *argv[1:]), **kwargs)
preflight.run_command = run
from taskman_ops.host_helper.__main__ import main
raise SystemExit(main())
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(package.path), str(tmp_path / "pgpass"), str(psql),
         "provision-pgpass-authority", "127.0.0.1", "5432", "taskman", "taskman_prod", database_state],
        input=b"127.0.0.1:5432:taskman_prod:taskman:secret\n", capture_output=True,
        env=os.environ,
        check=False,
    )
    assert completed.returncode == expected
    assert completed.stdout == completed.stderr == b""
    assert marker.exists() is authenticates
    assert not (tmp_path / "pgpass").exists()
