"""Black-box contract tests for the root-owned PostgreSQL backup asset."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import select
import signal
import fcntl
from datetime import UTC, datetime

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.releases.records import ActivationRecord, ReleaseRecord
from taskman_ops.services.backups import backup_service_contract, render_backup_timer, validate_systemd_calendar


ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / "backup" / "taskman-backup"
RELEASE_A = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_B = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_C = "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_D = "0.2.0-dddddddddddd-ubuntu26.04-amd64-otp27.3.4.6"
CANARY = "backup-password-canary-never-print"
FIRST = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
SECOND = datetime(2026, 9, 5, 10, 1, tzinfo=UTC)
THIRD = datetime(2026, 9, 5, 10, 2, tzinfo=UTC)
ACTIVATION_A = "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ACTIVATION_B = "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
ACTIVATION_C = "activation-cccccccccccccccccccccccccccccccc"
ACTIVATION_D = "activation-dddddddddddddddddddddddddddddddd"


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _postgres_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    commands = tmp_path / "commands"
    commands.mkdir(exist_ok=True)
    command_log = tmp_path / "commands.log"

    _write_executable(
        commands / "psql",
        """#!/bin/sh
printf 'psql PGPASSFILE=%s args=%s\\n' "$PGPASSFILE" "$*" >> "$COMMAND_LOG"
if [ "${FAIL_PSQL:-}" = 1 ]; then
  printf '%s\\n' "$FAKE_SECRET" >&2
  exit 1
fi
case "$*" in
  *pg_database_size*) printf '%s\\n' "${FAKE_DATABASE_BYTES:-1048576}" ;;
  *) printf '1\\n' ;;
esac
""",
    )
    _write_executable(
        commands / "pg_dump",
        """#!/bin/sh
printf 'pg_dump PGPASSFILE=%s args=%s\\n' "$PGPASSFILE" "$*" >> "$COMMAND_LOG"
if [ "${FAIL_PG_DUMP:-}" = 1 ]; then
  printf '%s\\n' "$FAKE_SECRET" >&2
  exit 1
fi
if [ -n "${BLOCK_DUMP_READY:-}" ]; then
  printf 'ready\\n' > "$BLOCK_DUMP_READY"
  read -r ignored < "$BLOCK_DUMP_GATE"
fi
output=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --file) output=$2; shift 2 ;;
    --file=*) output=${1#--file=}; shift ;;
    *) shift ;;
  esac
done
test -n "$output"
printf 'custom-format-dump\\n' > "$output"
""",
    )
    _write_executable(
        commands / "pg_restore",
        """#!/bin/sh
printf 'pg_restore PGPASSFILE=%s args=%s\\n' "$PGPASSFILE" "$*" >> "$COMMAND_LOG"
if [ "${FAIL_PG_RESTORE:-}" = 1 ]; then
  printf '%s\\n' "$FAKE_SECRET" >&2
  exit 1
fi
exit 0
""",
    )
    _write_executable(
        commands / "df",
        """#!/bin/sh
printf 'Avail\\n%s\\n' "${FAKE_AVAILABLE_BYTES:-1073741824}"
""",
    )
    return commands, command_log


_AUTOMATIC_CURRENT = object()


def _prepare_direct_current(tmp_path: Path, release_id: str) -> None:
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    deployment_root = tmp_path / "deployments"
    selected = release_root / release_id
    selected.mkdir(parents=True, exist_ok=True)
    managed_root.mkdir(exist_ok=True)
    current = managed_root / "current"
    current.unlink(missing_ok=True)
    current.symlink_to(selected)
    releases = deployment_root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    record = releases / f"release-{release_id}.json"
    _write_lifecycle_record(
        record,
        ReleaseRecord(1, release_id, "c" * 64, FIRST, FIRST, None, None, "no-change"),
    )
    _write_lifecycle_record(
        deployment_root / "activations" / f"{ACTIVATION_A}.json",
        ActivationRecord(1, ACTIVATION_A, None, release_id, FIRST, None, "no-change"),
    )


def _write_lifecycle_record(path: Path, record: ReleaseRecord | ActivationRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _write_private_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o600)


def _prepare_adopted_current(tmp_path: Path, release_id: str, *, migrations: str = "[]") -> Path:
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    adopted = release_root / "historical" / "taskman"
    adopted.mkdir(parents=True)
    managed_root.mkdir(exist_ok=True)
    (managed_root / "current").symlink_to(adopted)
    deployment_root = tmp_path / "deployments"
    bundle = deployment_root / "adoption-transactions" / f"adoption-{release_id}"
    bundle.mkdir(parents=True)
    bundle.chmod(0o750)
    timestamp = "2026-09-05T10:00:00Z"
    _write_private_text(
        bundle / "release.json",
        '{"schema_version":1,"release_id":"' + release_id + '","artifact_sha256":null,'
        '"installed_at":"' + timestamp + '","activated_at":"' + timestamp + '",'
        '"previous_release_id":null,"backup_id":null,"migration_policy":"adopted"}\n',
    )
    _write_private_text(
        bundle / "activation.json",
        '{"schema_version":1,"activation_id":"' + ACTIVATION_A + '","previous_release_id":null,'
        '"candidate_release_id":"' + release_id + '","activated_at":"' + timestamp + '",'
        '"backup_id":null,"migration_policy":"adopted"}\n',
    )
    _write_private_text(
        deployment_root / "adoptions" / f"adoption-{release_id}.json",
        '{"schema_version":1,"release_id":"' + release_id + '","adopted_at":"' + timestamp + '",'
        '"release_path":"' + adopted.as_posix() + '","content_sha256":"' + "a" * 64 + '",'
        '"application_version":"0.2.0","source_revision":"unknown","artifact_sha256":"unknown",'
        '"migrations":' + migrations + '}\n',
    )
    return adopted


def _append_direct_activation(
    tmp_path: Path,
    release_id: str,
    *,
    previous_release_id: str,
    activation_id: str = ACTIVATION_B,
    activated_at: datetime = SECOND,
    select: bool = True,
) -> Path:
    """Append one later direct activation after an adopted genesis edge."""

    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    selected = release_root / release_id
    selected.mkdir(parents=True, exist_ok=True)
    _write_lifecycle_record(
        tmp_path / "deployments" / "releases" / f"release-{release_id}.json",
        ReleaseRecord(1, release_id, "d" * 64, activated_at, activated_at, previous_release_id, None, "no-change"),
    )
    activation = tmp_path / "deployments" / "activations" / f"{activation_id}.json"
    if previous_release_id == release_id:
        # Deliberately bypass the Python boundary: the shell asset must reject
        # an on-host record that is schema-shaped but cyclic.
        _write_private_text(
            activation,
            '{"activated_at":"2026-09-05T10:01:00Z","activation_id":"'
            + activation_id
            + '","backup_id":null,"candidate_release_id":"'
            + release_id
            + '","migration_policy":"no-change","previous_release_id":"'
            + previous_release_id
            + '","schema_version":1}\n',
        )
    else:
        _write_lifecycle_record(
            activation,
            ActivationRecord(1, activation_id, previous_release_id, release_id, activated_at, None, "no-change"),
        )
    if select:
        current = managed_root / "current"
        current.unlink(missing_ok=True)
        current.symlink_to(selected)
    return selected


def _run_backup(
    tmp_path: Path,
    *extra: str,
    environment: dict[str, str] | None = None,
    selected_release: str | None | object = _AUTOMATIC_CURRENT,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    commands, command_log = _postgres_fixtures(tmp_path)
    backup_root = tmp_path / "backups"
    deployment_root = tmp_path / "deployments"
    lock_root = tmp_path / "locks"
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    requested_current = next(
        (extra[index + 1] for index, value in enumerate(extra[:-1]) if value == "--current-release"),
        None,
    )
    if selected_release is _AUTOMATIC_CURRENT:
        selected_release = requested_current
    if isinstance(selected_release, str):
        _prepare_direct_current(tmp_path, selected_release)
    values = {
        "PATH": f"{commands}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "FAKE_SECRET": CANARY,
    }
    if environment:
        values.update(environment)
    return subprocess.run(
        [
            "sh",
            str(ASSET),
            "--database-host",
            "127.0.0.1",
            "--database-port",
            "5432",
            "--database-role",
            "taskman",
            "--database-name",
            "taskman_prod",
            "--backup-root",
            str(backup_root),
            "--deployment-root",
            str(deployment_root),
            "--managed-root",
            str(managed_root),
            "--release-root",
            str(release_root),
            "--lock-root",
            str(lock_root),
            "--retention",
            "2",
            "--reason",
            "scheduled",
            *extra,
        ],
        env={**os.environ, **values},
        text=True,
        capture_output=True,
        check=False,
        pass_fds=pass_fds,
    )


def _records(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "deployments" / "backups").glob("backup-*.json"))


def test_backup_asset_refuses_an_inherited_token_without_the_still_held_deploy_descriptor(tmp_path: Path) -> None:
    """A token and canonical-looking files must not create an already-locked bypass."""

    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o750)
    for filename in ("lifecycle.lock", "lifecycle.lock.meta"):
        path = lock_root / filename
        path.touch(mode=0o600)
        path.chmod(0o600)
    completed = _run_backup(
        tmp_path,
        "--already-locked",
        environment={"TASKMAN_LIFECYCLE_LOCK_HELD": "a" * 32, "TASKMAN_LIFECYCLE_LOCK_FD": "9"},
    )

    assert completed.returncode == 10


@pytest.mark.parametrize(
    ("operation", "reason"),
    [("deploy", "pre-deploy"), ("rollback", "pre-rollback"), ("restore", "pre-restore")],
)
def test_backup_asset_accepts_only_the_actual_inherited_canonical_lifecycle_lock_descriptor(
    tmp_path: Path,
    operation: str,
    reason: str,
) -> None:
    """Deploy, rollback, and restore may delegate only through the canonical held flock."""

    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o750)
    lock_file = lock_root / "lifecycle.lock"
    metadata = lock_root / "lifecycle.lock.meta"
    for path in (lock_file, metadata):
        path.touch(mode=0o600)
        path.chmod(0o600)
    token = "a" * 32
    metadata.write_text(f"{token}|{operation}|{os.getpid()}|2026-09-05T12:00:00Z|exclusive\n", encoding="utf-8")
    lock = lock_file.open("r+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        try:
            saved_fd = os.dup(9)
        except OSError:
            saved_fd = None
        os.dup2(lock.fileno(), 9)
        try:
            completed = _run_backup(
                tmp_path,
                "--already-locked",
                "--current-release",
                RELEASE_A,
                "--candidate-release",
                RELEASE_B,
                "--reason",
                reason,
                environment={"TASKMAN_LIFECYCLE_LOCK_HELD": token, "TASKMAN_LIFECYCLE_LOCK_FD": "9"},
                pass_fds=(9,),
            )
        finally:
            if saved_fd is None:
                os.close(9)
            else:
                os.dup2(saved_fd, 9)
                os.close(saved_fd)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

    assert completed.returncode == 0, completed.stderr
    assert (lock_root / "lifecycle.lock").is_file()
    assert (lock_root / "lifecycle.lock.meta").is_file()


def test_backup_asset_refuses_a_held_descriptor_for_a_different_lock_inode(tmp_path: Path) -> None:
    """A locked FD is authority only when it names the canonical lifecycle inode."""

    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o750)
    lock_file = lock_root / "lifecycle.lock"
    metadata = lock_root / "lifecycle.lock.meta"
    for path in (lock_file, metadata):
        path.touch(mode=0o600)
        path.chmod(0o600)
    token = "b" * 32
    metadata.write_text(f"{token}|deploy|{os.getpid()}|2026-09-05T12:00:00Z|exclusive\n", encoding="utf-8")
    wrong_lock = tmp_path / "different-locked-file"
    wrong_lock.touch(mode=0o600)
    wrong_lock.chmod(0o600)
    wrong = wrong_lock.open("r+")
    fcntl.flock(wrong.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        try:
            saved_fd = os.dup(9)
        except OSError:
            saved_fd = None
        os.dup2(wrong.fileno(), 9)
        try:
            completed = _run_backup(
                tmp_path,
                "--already-locked",
                environment={"TASKMAN_LIFECYCLE_LOCK_HELD": token, "TASKMAN_LIFECYCLE_LOCK_FD": "9"},
                pass_fds=(9,),
            )
        finally:
            if saved_fd is None:
                os.close(9)
            else:
                os.dup2(saved_fd, 9)
                os.close(saved_fd)
    finally:
        fcntl.flock(wrong.fileno(), fcntl.LOCK_UN)
        wrong.close()

    assert completed.returncode == 10


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


def test_backup_service_contract_declares_only_root_assets_and_nonsecret_timer_environment() -> None:
    """Changing a managed path, mode, or leaking a password-bearing value must fail this."""

    contract = backup_service_contract(_config())

    assert [(asset.destination.as_posix(), asset.mode) for asset in contract.assets] == [
        ("/usr/local/lib/taskman/taskman-backup", 0o750),
        ("/etc/systemd/system/taskman-backup.service", 0o644),
        ("/etc/systemd/system/taskman-backup.timer", 0o644),
    ]
    assert contract.environment == {
        "TASKMAN_BACKUP_DATABASE_HOST": "127.0.0.1",
        "TASKMAN_BACKUP_DATABASE_PORT": "5432",
        "TASKMAN_BACKUP_DATABASE_ROLE": "taskman",
        "TASKMAN_BACKUP_DATABASE_NAME": "taskman_prod",
        "TASKMAN_BACKUP_ROOT": "/var/backups/taskman",
        "TASKMAN_DEPLOYMENT_ROOT": "/opt/taskman/deployments",
        "TASKMAN_MANAGED_ROOT": "/opt/taskman",
        "TASKMAN_RELEASE_ROOT": "/opt/taskman/releases",
        "TASKMAN_BACKUP_RETENTION": "14",
    }


def test_backup_timer_renderer_uses_the_validated_environment_schedule_without_permitting_unit_injection() -> None:
    """A later installer must need no timer knowledge beyond this deterministic renderer."""

    config = _config().model_copy(update={"backup_schedule": "Mon..Fri 04:30:00"})

    rendered = render_backup_timer(config)

    assert "OnCalendar=Mon..Fri 04:30:00" in rendered
    assert rendered.count("OnCalendar=") == 1
    staged_checks: list[str] = []
    assert render_backup_timer(config, calendar_validator=staged_checks.append) == rendered
    assert staged_checks == ["Mon..Fri 04:30:00"]
    unsafe = _config().model_copy(update={"backup_schedule": "*-*-* 02:15:00\nExecStart=/bin/false"})
    with pytest.raises(ValueError, match="invalid systemd backup schedule"):
        render_backup_timer(unsafe)
    semantically_unsafe = _config().model_copy(update={"backup_schedule": "nonsense"})
    with pytest.raises(ValueError, match="invalid systemd backup schedule"):
        render_backup_timer(semantically_unsafe)


def test_backup_timer_calendar_validator_exposes_one_fixed_native_command_for_a_staged_host_runner() -> None:
    """An installer can validate the schedule remotely without understanding timer template internals."""

    commands: list[tuple[str, ...]] = []

    validate_systemd_calendar("Mon..Fri 04:30:00", runner=lambda command: commands.append(command) or 0)

    assert commands == [("systemd-analyze", "calendar", "--", "Mon..Fri 04:30:00")]
    with pytest.raises(ValueError, match="invalid systemd backup schedule"):
        validate_systemd_calendar("Mon..Fri 04:30:00", runner=lambda _command: 1)


@pytest.mark.parametrize("schedule", ["--help", "--version", "-q"])
def test_backup_timer_calendar_validator_refuses_option_like_values_before_native_execution(schedule: str) -> None:
    """A sanitized calendar must never become a `systemd-analyze` option."""

    commands: list[tuple[str, ...]] = []

    with pytest.raises(ValueError, match="invalid systemd backup schedule"):
        validate_systemd_calendar(schedule, runner=lambda command: commands.append(command) or 0)

    assert commands == []


def test_rendered_backup_systemd_assets_pass_the_native_unit_validator_for_a_nondefault_schedule(tmp_path: Path) -> None:
    """A malformed timer/service contract must fail before host installation."""

    unit_root = tmp_path / "unit-root"
    units = unit_root / "etc" / "systemd" / "system"
    command = unit_root / "usr" / "local" / "lib" / "taskman" / "taskman-backup"
    units.mkdir(parents=True)
    command.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "systemd" / "taskman-backup.service", units)
    (units / "taskman-backup.timer").write_text(
        render_backup_timer(_config().model_copy(update={"backup_schedule": "Mon..Fri 04:30:00"})),
        encoding="utf-8",
    )
    shutil.copy2(ROOT / "backup" / "taskman-backup", command)
    command.chmod(0o750)
    for unit in ("sysinit.target", "network-online.target", "postgresql.service", "timers.target"):
        (units / unit).write_text("[Unit]\nDescription=validator fixture\n", encoding="utf-8")
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


def test_backup_asset_creates_and_validates_a_private_custom_dump_before_publishing_metadata(tmp_path: Path) -> None:
    """A missing dump validation/publication step must make this fail."""

    completed = _run_backup(tmp_path)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == 1
    assert payload["reason"] == "scheduled"
    assert payload["validated"] is True
    assert payload["database"] == "taskman_prod"
    dump = Path(payload["dump_path"])
    record = tmp_path / "deployments" / "backups" / f"{payload['backup_id']}.json"
    assert dump.is_file()
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600
    assert json.loads(record.read_text(encoding="utf-8")) == payload
    assert not list((tmp_path / "backups").glob(".taskman-backup-*"))
    assert not list((tmp_path / "deployments" / "backups").glob(".taskman-backup-*"))
    assert not list((tmp_path / "locks").glob(".taskman-backup-authority-*"))
    command_log = (tmp_path / "commands.log").read_text(encoding="utf-8")
    assert "--format=custom" in command_log
    assert "pg_restore PGPASSFILE=/etc/taskman/pgpass args=--list" in command_log
    assert "PGPASSFILE=/etc/taskman/pgpass" in command_log
    assert CANARY not in completed.stdout + completed.stderr + command_log


def test_backup_asset_returns_status_six_and_removes_private_partial_files_without_leaking_a_database_secret(tmp_path: Path) -> None:
    """Removing stderr redirection, cleanup, or backup error mapping must fail this."""

    completed = _run_backup(tmp_path, environment={"FAIL_PG_DUMP": "1"})

    assert completed.returncode == 6
    assert completed.stdout == ""
    assert "database backup failed" in completed.stderr
    assert CANARY not in completed.stdout + completed.stderr
    assert not list((tmp_path / "backups").glob("*"))
    assert not _records(tmp_path)


def test_backup_asset_returns_status_six_and_removes_the_dump_when_pg_restore_validation_fails(tmp_path: Path) -> None:
    """A custom dump must never be published before `pg_restore --list` accepts it."""

    completed = _run_backup(tmp_path, environment={"FAIL_PG_RESTORE": "1"})

    assert completed.returncode == 6
    assert "database backup failed" in completed.stderr
    assert not list((tmp_path / "backups").glob("*"))
    assert not _records(tmp_path)
    assert "pg_restore PGPASSFILE=/etc/taskman/pgpass args=--list" in (
        tmp_path / "commands.log"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("available_bytes", "database_bytes", "expected_status"),
    [
        (68_157_440, 1_048_576, 0),
        (68_157_439, 1_048_576, 6),
        (200_000_000, "not-a-size", 6),
    ],
)
def test_backup_asset_requires_database_size_plus_the_configured_capacity_margin_before_dumping(
    tmp_path: Path, available_bytes: int, database_bytes: int | str, expected_status: int
) -> None:
    """Capacity must be measured under the transaction, rather than inferred from one free byte."""

    completed = _run_backup(
        tmp_path,
        environment={
            "FAKE_AVAILABLE_BYTES": str(available_bytes),
            "FAKE_DATABASE_BYTES": str(database_bytes),
        },
    )

    assert completed.returncode == expected_status
    command_log = (tmp_path / "commands.log").read_text(encoding="utf-8")
    assert "pg_database_size(current_database())" in command_log
    if expected_status == 0:
        assert "pg_dump " in command_log
    else:
        assert "database backup failed" in completed.stderr
        assert "pg_dump " not in command_log
        assert not list((tmp_path / "backups").glob("*"))
        assert not _records(tmp_path)


def test_backup_asset_records_the_exact_source_database_allocation(tmp_path: Path) -> None:
    """A validated dump carries the source allocation authority needed for a later restore."""

    completed = _run_backup(tmp_path, environment={"FAKE_DATABASE_BYTES": "2097152"})

    assert completed.returncode == 0, completed.stderr
    record = json.loads(completed.stdout)
    assert record["source_database_size_bytes"] == 2_097_152
    persisted = next((tmp_path / "deployments" / "backups").glob("backup-*.json"))
    assert json.loads(persisted.read_text(encoding="utf-8"))["source_database_size_bytes"] == 2_097_152


def test_backup_asset_refuses_a_caller_current_release_without_an_authoritative_selected_release(tmp_path: Path) -> None:
    """The controller cannot invent `current_release_id` metadata on an unselected host."""

    completed = _run_backup(tmp_path, "--current-release", RELEASE_A, selected_release=None)

    assert completed.returncode == 10
    assert completed.stdout == ""
    assert not list((tmp_path / "backups").glob("*"))
    assert not _records(tmp_path)


def test_backup_asset_refuses_a_caller_current_release_that_conflicts_with_the_selected_release(tmp_path: Path) -> None:
    """Caller metadata has to agree with the managed selection under the lock."""

    completed = _run_backup(tmp_path, "--current-release", RELEASE_B, selected_release=RELEASE_A)

    assert completed.returncode == 10
    assert completed.stdout == ""


def test_backup_asset_refuses_a_malformed_direct_lifecycle_record_even_when_its_filename_and_mode_match(tmp_path: Path) -> None:
    """A private expected filename alone cannot authorize current-release metadata."""

    _prepare_direct_current(tmp_path, RELEASE_A)
    record = tmp_path / "deployments" / "releases" / f"release-{RELEASE_A}.json"
    record.write_text('{"release_id":"' + RELEASE_A + '"}\n', encoding="utf-8")
    record.chmod(0o600)

    completed = _run_backup(tmp_path, "--current-release", RELEASE_A, selected_release=None)

    assert completed.returncode == 10
    assert completed.stdout == ""
    assert not _records(tmp_path)


def test_backup_asset_refuses_an_unactivated_direct_record_as_the_current_selection(tmp_path: Path) -> None:
    """The current symlink must correspond to a strict first activation edge, not merely an install."""

    _prepare_direct_current(tmp_path, RELEASE_A)
    _write_lifecycle_record(
        tmp_path / "deployments" / "releases" / f"release-{RELEASE_A}.json",
        ReleaseRecord(1, RELEASE_A, "c" * 64, FIRST, None, None, None, "no-change"),
    )

    completed = _run_backup(tmp_path, "--current-release", RELEASE_A, selected_release=None)

    assert completed.returncode == 10
    assert completed.stdout == ""


def test_backup_asset_accepts_a_strict_direct_selected_release_and_its_initial_activation(tmp_path: Path) -> None:
    """A valid release record and first activation authorize the selected direct path."""

    _prepare_direct_current(tmp_path, RELEASE_A)

    completed = _run_backup(tmp_path, "--current-release", RELEASE_A, selected_release=None)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["current_release_id"] == RELEASE_A


def test_backup_asset_refuses_a_partial_or_fabricated_adoption_bundle(tmp_path: Path) -> None:
    """A marker and private arbitrary JSON are not an authoritative adopted selection."""

    _prepare_adopted_current(tmp_path, RELEASE_A)
    activation = tmp_path / "deployments" / "adoption-transactions" / f"adoption-{RELEASE_A}" / "activation.json"
    activation.write_text("{}\n", encoding="utf-8")
    activation.chmod(0o600)

    completed = _run_backup(tmp_path, "--current-release", RELEASE_A, selected_release=None)

    assert completed.returncode == 10
    assert completed.stdout == ""
    assert not _records(tmp_path)


def test_backup_asset_accepts_a_strict_marker_gated_adopted_selected_release(tmp_path: Path) -> None:
    """An adopted current path is authorized only by its complete strict lifecycle bundle."""

    _prepare_adopted_current(
        tmp_path,
        RELEASE_A,
        migrations='[{"filename":"20260905100000_create_tasks.exs","sha256":"' + "b" * 64 + '"}]',
    )

    completed = _run_backup(tmp_path, "--current-release", RELEASE_A, selected_release=None)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["current_release_id"] == RELEASE_A


def test_backup_asset_uses_the_latest_direct_activation_after_a_marker_gated_adopted_genesis(tmp_path: Path) -> None:
    """An adoption bundle is part of the same history as later ordinary releases."""

    _prepare_adopted_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_A)

    completed = _run_backup(tmp_path, "--current-release", RELEASE_B, selected_release=None)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["current_release_id"] == RELEASE_B


def test_backup_asset_revalidates_the_confirmed_rollback_edges_and_adopted_or_direct_target_path(
    tmp_path: Path,
) -> None:
    """The inherited rollback backup is the one strict in-lock graph authority, before PostgreSQL probing."""

    _prepare_direct_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_A)
    target = tmp_path / "managed" / "releases" / RELEASE_A
    _write_private_text(tmp_path / "deployments" / "manifests" / f"release-{RELEASE_A}.json", "{}\n")

    completed = _run_backup(
        tmp_path,
        "--reason",
        "pre-rollback",
        "--current-release",
        RELEASE_B,
        "--candidate-release",
        RELEASE_A,
        "--rollback-confirmation",
        f"{ACTIVATION_B}:no-change",
        "--rollback-target-path",
        str(target),
        "--rollback-target-authority",
        "direct",
        selected_release=None,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["current_release_id"] == RELEASE_B


def test_backup_asset_refuses_a_direct_rollback_target_without_its_manifest_marker(tmp_path: Path) -> None:
    """An in-lock direct target reread must not rely solely on the earlier controller snapshot."""

    _prepare_direct_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_A)

    completed = _run_backup(
        tmp_path,
        "--reason",
        "pre-rollback",
        "--current-release",
        RELEASE_B,
        "--candidate-release",
        RELEASE_A,
        "--rollback-confirmation",
        f"{ACTIVATION_B}:no-change",
        "--rollback-target-path",
        str(tmp_path / "managed" / "releases" / RELEASE_A),
        "--rollback-target-authority",
        "direct",
        selected_release=None,
    )

    assert completed.returncode == 10
    assert not (tmp_path / "commands.log").exists()


def test_backup_asset_refuses_a_rollback_target_when_its_authority_kind_changed_under_the_lock(tmp_path: Path) -> None:
    """Reclassifying a direct target as adopted would skip its immutable marker requirement."""

    _prepare_direct_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_A)
    _write_private_text(tmp_path / "deployment/manifests/release-A.json", "{}\n")

    completed = _run_backup(
        tmp_path,
        "--reason",
        "pre-rollback",
        "--current-release",
        RELEASE_B,
        "--candidate-release",
        RELEASE_A,
        "--rollback-confirmation",
        f"{ACTIVATION_B}:no-change",
        "--rollback-target-path",
        str(tmp_path / "managed" / "releases" / RELEASE_A),
        "--rollback-target-authority",
        "adopted",
        selected_release=None,
    )

    assert completed.returncode == 10
    assert not (tmp_path / "commands.log").exists()


def test_backup_asset_revalidates_an_adopted_rollback_target_by_its_marker_path(tmp_path: Path) -> None:
    """A compatible adopted baseline retains its marker-selected directory rather than an invented ID directory."""

    adopted = _prepare_adopted_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_A)

    completed = _run_backup(
        tmp_path,
        "--reason",
        "pre-rollback",
        "--current-release",
        RELEASE_B,
        "--candidate-release",
        RELEASE_A,
        "--rollback-confirmation",
        f"{ACTIVATION_B}:no-change",
        "--rollback-target-path",
        str(adopted),
        "--rollback-target-authority",
        "adopted",
        selected_release=None,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["candidate_release_id"] == RELEASE_A


@pytest.mark.parametrize(
    ("fingerprint", "target_suffix"),
    [
        (f"{ACTIVATION_A}:restore-required", RELEASE_A),
        (f"{ACTIVATION_B}:no-change", "outside-root"),
    ],
)
def test_backup_asset_refuses_stale_or_outside_rollback_confirmation_before_database_access(
    tmp_path: Path,
    fingerprint: str,
    target_suffix: str,
) -> None:
    """A changed history or path cannot turn the fresh backup into an authorization bypass."""

    _prepare_direct_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_A)
    target = tmp_path / "managed" / "releases" / target_suffix
    command_log = tmp_path / "commands.log"

    completed = _run_backup(
        tmp_path,
        "--reason",
        "pre-rollback",
        "--current-release",
        RELEASE_B,
        "--candidate-release",
        RELEASE_A,
        "--rollback-confirmation",
        fingerprint,
        "--rollback-target-path",
        str(target),
        "--rollback-target-authority",
        "direct",
        selected_release=None,
    )

    assert completed.returncode == 10
    assert not command_log.exists() or command_log.read_text(encoding="utf-8") == ""


def test_backup_asset_refuses_a_same_current_changed_history_with_a_new_restore_required_path_before_database_access(
    tmp_path: Path,
) -> None:
    """Reusing the current ID must not let a post-confirmation restore boundary evade the inherited lock check."""

    _prepare_direct_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_A, select=False)
    _append_direct_activation(
        tmp_path,
        RELEASE_C,
        previous_release_id=RELEASE_B,
        activation_id=ACTIVATION_C,
        activated_at=THIRD,
    )
    deployment = tmp_path / "deployments"
    release_root = tmp_path / "managed" / "releases"
    _write_private_text(deployment / "manifests" / f"release-{RELEASE_A}.json", "{}\n")

    # This represents the authoritative state after the operator reviewed the
    # original A -> B -> C plan but before the lock-held backup begins. C is
    # still current, yet it now arrives through A -> D -> C and D -> C requires
    # a database restore.
    (deployment / "releases" / f"release-{RELEASE_B}.json").unlink()
    (deployment / "activations" / f"{ACTIVATION_B}.json").unlink()
    _append_direct_activation(tmp_path, RELEASE_D, previous_release_id=RELEASE_A, activation_id=ACTIVATION_D, select=False)
    _write_lifecycle_record(
        deployment / "releases" / f"release-{RELEASE_C}.json",
        ReleaseRecord(1, RELEASE_C, "d" * 64, THIRD, THIRD, RELEASE_D, None, "restore-required"),
    )
    _write_lifecycle_record(
        deployment / "activations" / f"{ACTIVATION_C}.json",
        ActivationRecord(1, ACTIVATION_C, RELEASE_D, RELEASE_C, THIRD, None, "restore-required"),
    )

    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o750)
    lock_file = lock_root / "lifecycle.lock"
    metadata = lock_root / "lifecycle.lock.meta"
    for path in (lock_file, metadata):
        path.touch(mode=0o600)
        path.chmod(0o600)
    token = "d" * 32
    metadata.write_text(f"{token}|rollback|{os.getpid()}|2026-09-05T12:00:00Z|exclusive\n", encoding="utf-8")
    lock = lock_file.open("r+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        try:
            saved_fd = os.dup(9)
        except OSError:
            saved_fd = None
        os.dup2(lock.fileno(), 9)
        try:
            completed = _run_backup(
                tmp_path,
                "--already-locked",
                "--reason",
                "pre-rollback",
                "--current-release",
                RELEASE_C,
                "--candidate-release",
                RELEASE_A,
                "--rollback-confirmation",
                f"{ACTIVATION_C}:no-change,{ACTIVATION_B}:no-change",
                "--rollback-target-path",
                str(release_root / RELEASE_A),
                "--rollback-target-authority",
                "direct",
                selected_release=None,
                environment={"TASKMAN_LIFECYCLE_LOCK_HELD": token, "TASKMAN_LIFECYCLE_LOCK_FD": "9"},
                pass_fds=(9,),
            )
        finally:
            if saved_fd is None:
                os.close(9)
            else:
                os.dup2(saved_fd, 9)
                os.close(saved_fd)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

    assert completed.returncode == 10
    assert not (tmp_path / "commands.log").exists()
    assert not _records(tmp_path)


def test_backup_asset_refuses_a_current_link_drifted_back_to_an_adopted_path_after_a_later_direct_activation(
    tmp_path: Path,
) -> None:
    """The current link must target the latest chain candidate, even when the old path is valid."""

    adopted = _prepare_adopted_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_A)
    current = tmp_path / "managed" / "current"
    current.unlink()
    current.symlink_to(adopted)

    completed = _run_backup(tmp_path, "--current-release", RELEASE_A, selected_release=None)

    assert completed.returncode == 10
    assert completed.stdout == ""
    assert not _records(tmp_path)


def test_backup_asset_refuses_a_self_loop_in_a_mixed_adopted_and_direct_activation_history(
    tmp_path: Path,
) -> None:
    """A record-consistent release cannot make an activation chain cyclic."""

    _prepare_adopted_current(tmp_path, RELEASE_A)
    _append_direct_activation(tmp_path, RELEASE_B, previous_release_id=RELEASE_B)

    completed = _run_backup(tmp_path, "--current-release", RELEASE_B, selected_release=None)

    assert completed.returncode == 10
    assert completed.stdout == ""
    assert not _records(tmp_path)


def test_backup_asset_refuses_a_duplicate_activation_id_across_adopted_and_direct_history(tmp_path: Path) -> None:
    """An ID is globally unique even when the records live in different lifecycle storage forms."""

    _prepare_adopted_current(tmp_path, RELEASE_A)
    _append_direct_activation(
        tmp_path,
        RELEASE_B,
        previous_release_id=RELEASE_A,
        activation_id=ACTIVATION_A,
    )

    completed = _run_backup(tmp_path, "--current-release", RELEASE_B, selected_release=None)

    assert completed.returncode == 10
    assert completed.stdout == ""
    assert not _records(tmp_path)


def test_backup_asset_leaves_impossible_utc_record_pairs_untouched_by_retention(tmp_path: Path) -> None:
    """A regex-shaped impossible date cannot authorize deletion of a durable dump pair."""

    first = _run_backup(tmp_path)
    assert first.returncode == 0
    payload = json.loads(first.stdout)
    record = tmp_path / "deployments" / "backups" / f"{payload['backup_id']}.json"
    corrupted = json.loads(record.read_text(encoding="utf-8"))
    corrupted["created_at"] = "2026-02-30T12:00:00Z"
    record.write_text(json.dumps(corrupted, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    record.chmod(0o600)

    second = _run_backup(tmp_path, "--retention", "1")

    assert second.returncode == 0, second.stderr
    assert record.is_file()
    assert Path(payload["dump_path"]).is_file()
    assert "unrecognized retention entry left in place" in second.stderr


def test_backup_asset_cleans_operation_owned_state_before_releasing_the_lifecycle_lock_on_termination(tmp_path: Path) -> None:
    """A synchronous signal during dumping must leave neither partial files nor a live holder."""

    ready = tmp_path / "ready.fifo"
    gate = tmp_path / "gate.fifo"
    os.mkfifo(ready)
    os.mkfifo(gate)
    ready_fd = os.open(ready, os.O_RDONLY | os.O_NONBLOCK)
    try:
        commands, command_log = _postgres_fixtures(tmp_path)
        process = subprocess.Popen(
            [
                "sh", str(ASSET), "--database-host", "127.0.0.1", "--database-port", "5432",
                "--database-role", "taskman", "--database-name", "taskman_prod", "--backup-root", str(tmp_path / "backups"),
                "--deployment-root", str(tmp_path / "deployments"), "--managed-root", str(tmp_path / "managed"),
                "--release-root", str(tmp_path / "managed" / "releases"), "--lock-root", str(tmp_path / "locks"),
                "--retention", "2", "--reason", "scheduled",
            ],
            env={
                **os.environ,
                "PATH": f"{commands}:{os.environ['PATH']}",
                "COMMAND_LOG": str(command_log),
                "FAKE_SECRET": CANARY,
                "BLOCK_DUMP_READY": str(ready),
                "BLOCK_DUMP_GATE": str(gate),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        readable, _, _ = select.select([ready_fd], [], [], 5)
        assert readable, "pg_dump did not reach the synchronized interruption point"
        assert os.read(ready_fd, 16) == b"ready\n"
        # A POSIX shell defers its trap while waiting for a foreground child.
        # The real unit receives the cgroup signal, so exercise that boundary
        # by interrupting the shell and the blocked dump process together.
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        os.close(ready_fd)

    assert process.returncode != 0
    assert stdout == ""
    assert list((tmp_path / "backups").iterdir()) == []
    assert not _records(tmp_path)
    assert (tmp_path / "locks" / "lifecycle.lock.meta").read_text(encoding="utf-8") == ""


def test_backup_asset_refuses_to_overwrite_unknown_or_contradictory_retention_entries_and_preserves_matching_predeploy_backup(tmp_path: Path) -> None:
    """A glob-based deletion or an unpinned pre-deploy record must fail this."""

    first = _run_backup(
        tmp_path,
        "--reason",
        "pre-deploy",
        "--current-release",
        RELEASE_A,
        "--candidate-release",
        RELEASE_B,
    )
    assert first.returncode == 0, first.stderr
    predeploy = json.loads(first.stdout)
    predeploy_record = tmp_path / "deployments" / "backups" / f"{predeploy['backup_id']}.json"
    rewritten = json.loads(predeploy_record.read_text(encoding="utf-8"))
    rewritten["created_at"] = "2020-01-01T00:00:00Z"
    predeploy_record.write_text(json.dumps(rewritten, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    predeploy_record.chmod(0o600)

    unknown = tmp_path / "backups" / "leave-me-alone.dump"
    unknown.write_text("unknown", encoding="utf-8")
    unknown.chmod(0o600)
    second = _run_backup(
        tmp_path,
        "--retention",
        "1",
        "--current-release",
        RELEASE_A,
        "--candidate-release",
        RELEASE_B,
    )

    assert second.returncode == 0, second.stderr
    assert unknown.is_file()
    assert "unrecognized retention entry left in place" in second.stderr
    assert predeploy_record.is_file()
    assert Path(predeploy["dump_path"]).is_file()
    assert len(_records(tmp_path)) == 2


def test_retention_keeps_only_the_newest_matching_predeploy_backup_outside_the_count(tmp_path: Path) -> None:
    """Fail if a second older matching pre-deploy backup is pinned as well."""

    oldest = _run_backup(
        tmp_path,
        "--retention",
        "10",
        "--reason",
        "pre-deploy",
        "--current-release",
        RELEASE_A,
        "--candidate-release",
        RELEASE_B,
    )
    assert oldest.returncode == 0
    oldest_payload = json.loads(oldest.stdout)
    oldest_record = tmp_path / "deployments" / "backups" / f"{oldest_payload['backup_id']}.json"
    oldest_contents = json.loads(oldest_record.read_text(encoding="utf-8"))
    oldest_contents["created_at"] = "2020-01-01T00:00:00Z"
    oldest_record.write_text(json.dumps(oldest_contents, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    oldest_record.chmod(0o600)

    newest = _run_backup(
        tmp_path,
        "--retention",
        "1",
        "--reason",
        "pre-deploy",
        "--current-release",
        RELEASE_A,
        "--candidate-release",
        RELEASE_B,
    )

    assert newest.returncode == 0, newest.stderr
    newest_payload = json.loads(newest.stdout)
    assert not (tmp_path / "deployments" / "backups" / f"{oldest_payload['backup_id']}.json").exists()
    assert (tmp_path / "deployments" / "backups" / f"{newest_payload['backup_id']}.json").is_file()
    assert len(_records(tmp_path)) == 1


def test_retention_warns_and_leaves_contradictory_records_that_reference_the_same_dump(tmp_path: Path) -> None:
    """Deleting either side of a duplicate dump reference must make this fail."""

    first = _run_backup(tmp_path)
    assert first.returncode == 0
    payload = json.loads(first.stdout)
    record = tmp_path / "deployments" / "backups" / f"{payload['backup_id']}.json"
    duplicate_id = "backup-cccccccccccccccccccccccccccccccc"
    duplicate = json.loads(record.read_text(encoding="utf-8"))
    duplicate["backup_id"] = duplicate_id
    duplicate_record = tmp_path / "deployments" / "backups" / f"{duplicate_id}.json"
    duplicate_record.write_text(json.dumps(duplicate, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    duplicate_record.chmod(0o600)

    second = _run_backup(tmp_path, "--retention", "1")

    assert second.returncode == 0, second.stderr
    assert record.is_file()
    assert duplicate_record.is_file()
    assert Path(payload["dump_path"]).is_file()
    assert "unrecognized retention entry left in place" in second.stderr
