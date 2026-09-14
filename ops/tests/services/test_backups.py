"""Black-box contracts for the installed scheduled-backup zipapp."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.helper_client.package import BACKUP_ARCHIVE_MEMBERS, build_scheduled_backup_package
from taskman_ops.host_helper import scheduled_backup
from taskman_ops.host_helper.backups import BackupAuthorityError, BackupCapacityError
from taskman_ops.host_helper.commands import CommandError
from taskman_ops.host_helper.lock import LifecycleLockContention, acquire_lifecycle_lock
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.services.backups import (
    BACKUP_COMMAND,
    backup_service_contract,
    scheduled_backup_helper,
    render_backup_timer,
    validate_systemd_calendar,
)
from taskman_ops.services.systemd import render_backup_service


ROOT = Path(__file__).resolve().parents[2]
CANARY = "backup-password-canary-never-print"
FROZEN_EARLIER_SHA256 = "0b663bd87abfc6004245170f7f849735f7a4ac404ba974637db7a10080b11b1b"


def _frozen_earlier_package(destination: Path) -> str:
    """Build the pinned earlier baseline without reading live package sources."""

    source = ROOT / "tests" / "fixtures" / "scheduled_backup_baseline.py"
    info = zipfile.ZipInfo("__main__.py", date_time=(2020, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def _supported_baseline_payload(install: Path, backups: Path) -> dict[str, object]:
    release_id = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
    backup_ids = {letter: f"backup-{letter * 32}" for letter in "abcde"}
    release = {
        "schema_version": 2,
        "release_id": release_id,
        "source_revision": "a" * 40,
        "artifact_sha256": "b" * 64,
        "migrations": [],
        "artifact_manifest": {
            "schema_version": 3,
            "application": "taskman",
            "application_version": "0.2.0",
            "source_revision": "a" * 40,
            "release_id": release_id,
            "built_at": "2026-09-07T12:00:00Z",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "29.0.6",
            "elixir_version": "1.20.4",
            "node_version": "22.22.1",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "builder_base_tag": "ubuntu:resolute-20260811.1",
            "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
            "migrations": [],
            "top_level": "taskman",
            "artifact_sha256": "b" * 64,
            "source_dirty": False,
        },
    }
    return {
        "install_root": install.as_posix(),
        "backup_root": backups.as_posix(),
        "retention": 1,
        "release": release,
        "selection": {
            "release_id": release_id,
            "previous_release_id": None,
            "backup_id": backup_ids["a"],
            "selected_at": "2026-09-07T12:00:00Z",
            "schema_version": 2,
            "observed_previous_release_id": None,
            "recovery_backup_ids": [backup_ids["b"]],
        },
        "backups": [
            {
                "backup_id": backup_ids[letter],
                "created_at": f"2026-09-07T{hour}:00:00Z",
                "dump_sha256": letter * 64,
                "source_release_id": release_id,
                "migration_versions": [],
                "source_database_size_bytes": 1,
            }
            for letter, hour in (("a", "10"), ("b", "11"), ("c", "12"), ("e", "13"), ("d", "14"))
        ],
        "protections": [
            {
                "schema_version": 1,
                "backup_id": backup_ids["c"],
                "base_selection_id": None,
                "target_release_id": release_id,
                "attempt_number": 0,
                "created_at": "2026-09-07T12:00:00Z",
            }
        ],
    }


def _config(**overrides: object) -> EnvironmentConfig:
    values: dict[str, object] = {
        "name": "production",
        "ssh_host": "203.0.113.10",
        "ssh_port": 22,
        "ssh_user": "deployer",
        "host_key_fingerprint": "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "public_hostname": "taskman.acme.tld",
        "public_ipv4": "203.0.113.10",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "application_port": 4000,
        "distribution_port": 6789,
        "database_name": "taskman_prod",
        "database_role": "taskman",
        "mail_from": "no-reply@example.test",
    }
    values.update(overrides)
    return EnvironmentConfig.model_validate(values)


def _environment() -> dict[str, str]:
    return {
        "TASKMAN_BACKUP_DATABASE_HOST": "127.0.0.1",
        "TASKMAN_BACKUP_DATABASE_PORT": "5432",
        "TASKMAN_BACKUP_DATABASE_ROLE": "taskman",
        "TASKMAN_BACKUP_DATABASE_NAME": "taskman_prod",
        "TASKMAN_BACKUP_BACKUP_ROOT": "/var/backups/taskman",
        "TASKMAN_BACKUP_INSTALL_ROOT": "/opt/taskman",
        "TASKMAN_BACKUP_RETENTION": "14",
    }


def test_scheduled_zipapp_is_deterministic_and_contains_only_backup_authority(tmp_path: Path) -> None:
    """A package member outside the narrow allowlist would give the timer excess authority."""

    first = build_scheduled_backup_package(tmp_path / "first.pyz")
    second = build_scheduled_backup_package(tmp_path / "second.pyz")

    first_bytes = first.path.read_bytes()
    assert first_bytes == second.path.read_bytes()
    assert first.sha256 == hashlib.sha256(first_bytes).hexdigest()
    with zipfile.ZipFile(first.path) as archive:
        names = archive.namelist()
    assert names == list(BACKUP_ARCHIVE_MEMBERS)
    assert "taskman_ops/checksums.py" in names
    assert "taskman_ops/host_helper/scheduled_backup.py" in names
    assert "taskman_ops/host_helper/backups.py" in names
    assert "taskman_ops/host_helper/credentials.py" in names
    assert "taskman_ops/host_helper/database.py" in names
    assert "taskman_ops/host_helper/filesystem.py" in names
    assert not any(
        forbidden in name
        for name in names
        for forbidden in (
            "operations/deploy",
            "operations/rollback",
            "operations/restore",
            "remote",
            "config",
            "selection.py",
            "services.py",
        )
    )
    first.path.chmod(0o750)
    completed = subprocess.run(
        [sys.executable, "-I", "-S", first.path.as_posix()],
        env={},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2


def test_backup_service_contract_installs_the_immutable_zipapp_with_only_nonsecret_inputs() -> None:
    """Passing roots derived by ManagedPaths or a password into systemd would widen service authority."""

    contract = backup_service_contract(_config())

    command, service, timer = contract.assets
    assert command.destination == BACKUP_COMMAND
    assert command.destination.as_posix() == "/usr/local/lib/taskman/taskman-backup.pyz"
    assert command.mode == 0o750
    assert command.sha256 == hashlib.sha256(command.content).hexdigest()
    assert service.destination.as_posix() == "/etc/systemd/system/taskman-backup.service"
    assert timer.destination.as_posix() == "/etc/systemd/system/taskman-backup.timer"
    assert contract.environment == {
        "TASKMAN_BACKUP_DATABASE_HOST": "127.0.0.1",
        "TASKMAN_BACKUP_DATABASE_PORT": "5432",
        "TASKMAN_BACKUP_DATABASE_ROLE": "taskman",
        "TASKMAN_BACKUP_DATABASE_NAME": "taskman_prod",
        "TASKMAN_BACKUP_BACKUP_ROOT": "/var/backups/taskman",
        "TASKMAN_BACKUP_INSTALL_ROOT": "/opt/taskman",
        "TASKMAN_BACKUP_RETENTION": "14",
    }
    assert CANARY not in command.content.decode("utf-8", "ignore")
    assert CANARY not in "\n".join(contract.environment.values())


def test_scheduled_helper_materializes_the_same_persistent_package(tmp_path: Path) -> None:
    """A separate controller package builder could upload bytes unlike the installed timer executable."""

    package = scheduled_backup_helper(tmp_path / "taskman-backup.pyz")

    assert package.path == tmp_path / "taskman-backup.pyz"
    assert package.sha256 == hashlib.sha256(package.path.read_bytes()).hexdigest()


def test_earlier_and_replacement_packages_keep_supported_records_protections_and_lock_authority_isolated(
    tmp_path: Path,
) -> None:
    """Replacing the timer archive must not require the running earlier archive to import the checkout."""

    earlier = tmp_path / "earlier.pyz"
    earlier_sha256 = _frozen_earlier_package(earlier)
    replacement = build_scheduled_backup_package(tmp_path / "replacement.pyz")
    earlier_bytes = earlier.read_bytes()
    assert earlier_sha256 == FROZEN_EARLIER_SHA256
    assert earlier_sha256 != replacement.sha256
    script = """
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from taskman_ops.host_helper.backup_protection import BackupProtection
from taskman_ops.host_helper.backups import retained_backup_ids
from taskman_ops.host_helper.lock import lifecycle_lock
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import BackupRecord, ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.state import HostState

payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
release = ReleaseRecord.from_mapping(payload["release"])
selection = SelectionRecord.from_mapping(payload["selection"])
backups = tuple(BackupRecord.from_mapping(item) for item in payload["backups"])
protections = tuple(BackupProtection.from_mapping(item) for item in payload["protections"])
paths = ManagedPaths.from_mapping({"install_root": payload["install_root"], "backup_root": payload["backup_root"]})
Path(payload["install_root"]).mkdir(mode=0o750, exist_ok=True)
print("waiting-for-lock", flush=True)
with lifecycle_lock(paths, 1):
    state = HostState(selection.release_id, (release,), backups, (selection,), (), "stopped", "ready", (), (), protections)
    retained = retained_backup_ids(state, payload["retention"])
print(json.dumps({"release_id": release.release_id, "selected_release_id": selection.release_id, "retained_backup_ids": sorted(retained)}, sort_keys=True))
"""
    install = tmp_path / "isolated-install"
    backups = tmp_path / "isolated-backups"
    payload = _supported_baseline_payload(install, backups)
    payload_path = tmp_path / "supported-baseline.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    expected = json.dumps(
        {
            "release_id": payload["release"]["release_id"],
            "selected_release_id": payload["release"]["release_id"],
            "retained_backup_ids": [f"backup-{letter * 32}" for letter in "abcd"],
        },
        sort_keys=True,
    )

    invocation = [sys.executable, "-I", "-S", earlier.as_posix(), payload_path.as_posix()]
    paths = ManagedPaths.from_mapping({"install_root": install.as_posix(), "backup_root": backups.as_posix()})
    with acquire_lifecycle_lock(paths, 1):
        earlier_process = subprocess.Popen(
            invocation,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert earlier_process.stdout is not None
        assert earlier_process.stdout.readline() == "waiting-for-lock\n"

    earlier_stdout, earlier_stderr = earlier_process.communicate(timeout=1)
    assert earlier_process.returncode == 0, earlier_stderr
    assert earlier_stdout.strip() == expected

    replacement_process = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, replacement.path.as_posix(), payload_path.as_posix()],
        text=True,
        capture_output=True,
        check=False,
    )
    assert replacement_process.returncode == 0, replacement_process.stderr
    assert replacement_process.stdout.splitlines() == ["waiting-for-lock", expected]

    assert earlier.read_bytes() == earlier_bytes


def test_backup_systemd_unit_has_a_fixed_entrypoint_and_exact_writable_paths() -> None:
    """Arguments or writable deployment roots would expose secrets or legacy lifecycle authority."""

    unit = render_backup_service(_config())

    assert "Type=oneshot" in unit
    assert "ExecStart=/usr/local/lib/taskman/taskman-backup.pyz" in unit
    assert "EnvironmentFile=/etc/taskman/taskman-backup.env" in unit
    assert "/etc/taskman/pgpass" not in unit
    assert "${TASKMAN_BACKUP" not in unit
    assert "ReadWritePaths=/var/backups/taskman /opt/taskman/lifecycle.lock" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "PrivateTmp=true" in unit
    assert "/var/lock/taskman" not in unit
    assert "deployment" not in unit.lower()
    assert CANARY not in unit


def test_backup_systemd_unit_quotes_path_list_roots_with_both_quote_types() -> None:
    """Path-list quoting must preserve quote characters without changing ordinary roots."""

    unit = render_backup_service(_config(backup_root='/srv/backup\'s"root'))

    assert 'ReadWritePaths="/srv/backup\'s\\"root" /opt/taskman/lifecycle.lock' in unit


def test_backup_timer_is_persistent_and_uses_a_validated_calendar() -> None:
    """A non-persistent or injected calendar could silently skip backups or alter the unit."""

    rendered = render_backup_timer(_config().model_copy(update={"backup_schedule": "Mon..Fri 04:30:00"}))

    assert "OnCalendar=Mon..Fri 04:30:00" in rendered
    assert "Persistent=true" in rendered
    commands: list[tuple[str, ...]] = []
    validate_systemd_calendar("Mon..Fri 04:30:00", runner=lambda command: commands.append(command) or 0)
    assert commands == [("systemd-analyze", "calendar", "--", "Mon..Fri 04:30:00")]
    with pytest.raises(ValueError, match="invalid systemd backup schedule"):
        validate_systemd_calendar("*-*-* 02:15:00\nExecStart=/bin/false", runner=lambda _command: 0)


@pytest.mark.parametrize(
    ("failure", "expected", "message"),
    [
        (None, 0, "taskman scheduled backup completed"),
        (LifecycleLockContention("held"), 12, "taskman scheduled backup lifecycle lock is unavailable"),
        (CommandError("pg_dump failed"), 6, "taskman scheduled backup needs retry"),
        (BackupCapacityError("insufficient"), 6, "taskman scheduled backup needs retry"),
        (BackupAuthorityError("contradictory"), 10, "taskman scheduled backup needs manual attention"),
    ],
)
def test_scheduled_adapter_maps_only_fixed_systemd_statuses(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception | None,
    expected: int,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Serializing exception details or remapping a class would make systemd outcomes unsafe."""

    if failure is None:
        monkeypatch.setattr(scheduled_backup, "run_scheduled_backup", lambda _inputs: None)
    else:
        def fail(_inputs: object) -> None:
            raise failure

        monkeypatch.setattr(scheduled_backup, "run_scheduled_backup", fail)

    assert scheduled_backup.main(_environment()) == expected
    assert capsys.readouterr().out == f"{message}\n"
    assert CANARY not in message


def test_scheduled_adapter_rejects_invalid_installed_configuration_with_status_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Treating malformed service environment as retryable would loop on an operator error."""

    environment = _environment()
    environment["TASKMAN_BACKUP_RETENTION"] = "not-an-integer"

    assert scheduled_backup.main(environment) == 2
    assert capsys.readouterr().out == "taskman scheduled backup configuration is invalid\n"


@pytest.mark.parametrize("backup_root", ["/var/backups/taskman", '/srv/backup\'s"root'])
def test_rendered_systemd_units_validate_with_the_persistent_zipapp(
    tmp_path: Path,
    backup_root: str,
) -> None:
    """A unit that systemd cannot parse must fail before host installation."""

    unit_root = tmp_path / "unit-root"
    units = unit_root / "etc" / "systemd" / "system"
    package = unit_root / "usr" / "local" / "lib" / "taskman" / "taskman-backup.pyz"
    units.mkdir(parents=True)
    package.parent.mkdir(parents=True)
    package.write_bytes(build_scheduled_backup_package(tmp_path / "backup.pyz").path.read_bytes())
    package.chmod(0o750)
    config = _config(backup_root=backup_root)
    (units / "taskman-backup.service").write_text(render_backup_service(config), encoding="utf-8")
    (units / "taskman-backup.timer").write_text(render_backup_timer(config), encoding="utf-8")
    for unit in ("sysinit.target", "network-online.target", "postgresql.service", "timers.target"):
        fixture = "[Unit]\nDescription=fixture\n"
        if unit == "postgresql.service":
            fixture += "[Service]\nType=oneshot\nExecStart=/usr/local/lib/taskman/taskman-backup.pyz\n"
        (units / unit).write_text(fixture, encoding="utf-8")

    completed = subprocess.run(
        [
            "systemd-analyze",
            "verify",
            f"--root={unit_root}",
            "taskman-backup.service",
            "taskman-backup.timer",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert stat.S_IMODE(package.stat().st_mode) == 0o750
