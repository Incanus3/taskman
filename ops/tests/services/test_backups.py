"""Black-box contracts for the installed scheduled-backup zipapp."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import subprocess
import zipfile

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.helper_package import BACKUP_ARCHIVE_MEMBERS, build_scheduled_backup_package
from taskman_ops import scheduled_backup
from taskman_ops.host_helper.backups import BackupAuthorityError
from taskman_ops.host_helper.commands import CommandError
from taskman_ops.host_helper.lock import LifecycleLockContention
from taskman_ops.services.backups import (
    BACKUP_COMMAND,
    backup_service_contract,
    render_backup_timer,
    validate_systemd_calendar,
)
from taskman_ops.services.systemd import render_backup_service


ROOT = Path(__file__).resolve().parents[2]
CANARY = "backup-password-canary-never-print"


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
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
    )


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
    assert "taskman_ops/scheduled_backup.py" in names
    assert "taskman_ops/host_helper/backups.py" in names
    assert not any(
        forbidden in name
        for name in names
        for forbidden in ("operations/deploy", "operations/rollback", "operations/restore", "remote", "config")
    )
    completed = subprocess.run(
        ["python", first.path.as_posix()],
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
    assert "ProtectSystem=full" in unit
    assert "PrivateTmp=true" in unit
    assert "/var/lock/taskman" not in unit
    assert "deployment" not in unit.lower()
    assert CANARY not in unit


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


def test_rendered_systemd_units_validate_with_the_persistent_zipapp(tmp_path: Path) -> None:
    """A unit that systemd cannot parse must fail before host installation."""

    unit_root = tmp_path / "unit-root"
    units = unit_root / "etc" / "systemd" / "system"
    package = unit_root / "usr" / "local" / "lib" / "taskman" / "taskman-backup.pyz"
    units.mkdir(parents=True)
    package.parent.mkdir(parents=True)
    package.write_bytes(build_scheduled_backup_package(tmp_path / "backup.pyz").path.read_bytes())
    package.chmod(0o750)
    (units / "taskman-backup.service").write_text(render_backup_service(_config()), encoding="utf-8")
    (units / "taskman-backup.timer").write_text(render_backup_timer(_config()), encoding="utf-8")
    for unit in ("sysinit.target", "network-online.target", "postgresql.service", "timers.target"):
        (units / unit).write_text("[Unit]\nDescription=fixture\n", encoding="utf-8")

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
    assert stat.S_IMODE(package.stat().st_mode) == 0o750
