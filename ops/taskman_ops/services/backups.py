"""Validated local-backup controller boundary and systemd asset contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Literal

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..releases.identifiers import validate_release_id
from ..releases.records import BackupRecord, RemoteLifecycleStore, raise_remote_lifecycle_failure
from ..remote import Remote


BACKUP_COMMAND = PurePosixPath("/usr/local/lib/taskman/taskman-backup")
BACKUP_SERVICE = PurePosixPath("/etc/systemd/system/taskman-backup.service")
BACKUP_TIMER = PurePosixPath("/etc/systemd/system/taskman-backup.timer")
BACKUP_ENVIRONMENT_FILE = PurePosixPath("/etc/taskman/taskman-backup.env")
_REASONS = frozenset({"scheduled", "pre-deploy", "pre-rollback", "pre-restore"})
_ASSET_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BACKUP_SCHEDULE = "*-*-* 02:15:00"
_SAFE_SYSTEMD_CALENDAR = re.compile(r"[A-Za-z0-9*.,:+/ ~-]+\Z")
CalendarValidator = Callable[[str], None]


@dataclass(frozen=True)
class ManagedBackupAsset:
    """One root-controlled file host convergence may install without enabling it."""

    source: Path
    destination: PurePosixPath
    mode: int


@dataclass(frozen=True)
class BackupServiceContract:
    """The non-secret files and environment needed by the timer service."""

    assets: tuple[ManagedBackupAsset, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class BackupContext:
    """Validated non-secret inputs for one host-owned backup transaction."""

    config: EnvironmentConfig
    store: RemoteLifecycleStore
    current_release_id: str | None = None
    candidate_release_id: str | None = None
    lock_timeout_seconds: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.config, EnvironmentConfig):
            raise TypeError("backup context requires an environment configuration")
        if not isinstance(self.store, RemoteLifecycleStore):
            raise TypeError("backup context requires a remote lifecycle store")
        if (
            self.store.deployment_root != self.config.deployment_root
            or self.store.managed_root != self.config.managed_root
            or self.store.release_root != self.config.release_root
            or self.store.backup_root != self.config.backup_root
        ):
            raise ValueError("backup lifecycle store does not match environment configuration")
        for release_id in (self.current_release_id, self.candidate_release_id):
            if release_id is not None:
                validate_release_id(release_id)
        if type(self.lock_timeout_seconds) is not int or self.lock_timeout_seconds < 0:
            raise ValueError("backup lock timeout must be a non-negative integer")


def backup_service_contract(config: EnvironmentConfig) -> BackupServiceContract:
    """Return the deterministic, non-mutating backup-service installation contract.

    Host convergence owns remote installation, daemon reload, and timer
    enablement. This capability intentionally has no ``Remote`` dependency, so
    callers cannot accidentally converge a host merely by asking for the asset
    contract.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("backup service contract requires an environment configuration")
    return BackupServiceContract(
        assets=(
            ManagedBackupAsset(_ASSET_ROOT / "backup" / "taskman-backup", BACKUP_COMMAND, 0o750),
            ManagedBackupAsset(_ASSET_ROOT / "systemd" / "taskman-backup.service", BACKUP_SERVICE, 0o644),
            ManagedBackupAsset(_ASSET_ROOT / "systemd" / "taskman-backup.timer", BACKUP_TIMER, 0o644),
        ),
        environment={
            "TASKMAN_BACKUP_DATABASE_HOST": config.database_host,
            "TASKMAN_BACKUP_DATABASE_PORT": str(config.database_port),
            "TASKMAN_BACKUP_DATABASE_ROLE": config.database_role,
            "TASKMAN_BACKUP_DATABASE_NAME": config.database_name,
            "TASKMAN_BACKUP_ROOT": config.backup_root.as_posix(),
            "TASKMAN_DEPLOYMENT_ROOT": config.deployment_root.as_posix(),
            "TASKMAN_MANAGED_ROOT": config.managed_root.as_posix(),
            "TASKMAN_RELEASE_ROOT": config.release_root.as_posix(),
            "TASKMAN_BACKUP_RETENTION": str(config.backup_retention),
        },
    )


def validate_systemd_calendar(
    schedule: str,
    *,
    runner: Callable[[tuple[str, ...]], int] | None = None,
) -> None:
    """Use a native systemd calendar parser without exposing its diagnostics.

    A host installer can inject a runner that executes this exact argv on the
    staged managed host. The local default is only a developer
    convenience; if the workstation lacks ``systemd-analyze`` it raises a
    stable prerequisite error instead of silently accepting an unverified
    calendar.
    """

    if (
        not isinstance(schedule, str)
        or schedule.startswith("-")
        or _SAFE_SYSTEMD_CALENDAR.fullmatch(schedule) is None
    ):
        raise ValueError("invalid systemd backup schedule")
    command = ("systemd-analyze", "calendar", "--", schedule)
    if runner is None:
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise RuntimeError("systemd calendar validation is unavailable") from None
        returncode = completed.returncode
    else:
        try:
            returncode = runner(command)
        except Exception:
            raise RuntimeError("systemd calendar validation is unavailable") from None
    if returncode != 0:
        raise ValueError("invalid systemd backup schedule")


def render_backup_timer(
    config: EnvironmentConfig,
    *,
    calendar_validator: CalendarValidator | None = None,
) -> str:
    """Render the managed timer with one validated ``OnCalendar`` directive.

    The installation capability can write these bytes verbatim.  It does not
    need to parse systemd syntax or splice a configuration value into a unit,
    keeping the schedule boundary owned by the backup capability.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("backup timer rendering requires an environment configuration")
    schedule = config.backup_schedule
    if schedule.startswith("-") or _SAFE_SYSTEMD_CALENDAR.fullmatch(schedule) is None:
        raise ValueError("invalid systemd backup schedule")
    if calendar_validator is None:
        validate_systemd_calendar(schedule)
    else:
        calendar_validator(schedule)
    template = (_ASSET_ROOT / "systemd" / "taskman-backup.timer").read_text(encoding="utf-8")
    target = f"OnCalendar={_DEFAULT_BACKUP_SCHEDULE}"
    if template.count(target) != 1:
        raise RuntimeError("backup timer template has no unique schedule directive")
    return template.replace(target, f"OnCalendar={schedule}")


def create_backup(
    remote: Remote,
    context: BackupContext,
    reason: Literal["scheduled", "pre-deploy", "pre-rollback", "pre-restore"],
) -> BackupRecord:
    """Run exactly one root-owned remote backup transaction and validate its record.

    The managed command, rather than this controller, owns exclusive lifecycle
    locking, health/capacity checks, dump validation, publication, and pruning.
    Keeping the invocation as one command prevents a controller-held lock from
    deadlocking the timer's identical transaction.
    """

    if not isinstance(context, BackupContext):
        raise TypeError("backup creation requires a backup context")
    if remote is not context.store.remote:
        raise ValueError("backup remote does not match lifecycle store")
    if reason not in _REASONS:
        raise ValueError("invalid backup reason")

    result = remote.run(
        _backup_argv(context, reason),
        sudo=True,
        stdin=None,
        sensitive=False,
    )
    if result.returncode == ExitStatus.LOCKED:
        raise_remote_lifecycle_failure(result)
    if result.returncode == ExitStatus.SAFETY:
        raise _backup_safety_error()
    if result.returncode == ExitStatus.BACKUP:
        raise _backup_error("database backup failed; inspect PostgreSQL and backup capacity before retrying")
    if result.returncode != ExitStatus.OK:
        raise _backup_error("database backup failed; inspect PostgreSQL and backup capacity before retrying")
    try:
        record = BackupRecord.from_mapping(json.loads(result.stdout))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise _backup_error("database backup result is invalid") from None
    _validate_result(record, context, reason)
    return record


def _backup_argv(context: BackupContext, reason: str) -> tuple[str, ...]:
    config = context.config
    argv = [
        BACKUP_COMMAND.as_posix(),
        "--database-host",
        config.database_host,
        "--database-port",
        str(config.database_port),
        "--database-role",
        config.database_role,
        "--database-name",
        config.database_name,
        "--backup-root",
        config.backup_root.as_posix(),
        "--deployment-root",
        config.deployment_root.as_posix(),
        "--managed-root",
        config.managed_root.as_posix(),
        "--release-root",
        config.release_root.as_posix(),
        "--retention",
        str(config.backup_retention),
        "--reason",
        reason,
        "--lock-timeout-seconds",
        str(context.lock_timeout_seconds),
    ]
    if context.current_release_id is not None:
        argv.extend(("--current-release", context.current_release_id))
    if context.candidate_release_id is not None:
        argv.extend(("--candidate-release", context.candidate_release_id))
    return tuple(argv)


def _validate_result(record: BackupRecord, context: BackupContext, reason: str) -> None:
    if (
        record.database != context.config.database_name
        or not record.dump_path.is_relative_to(context.config.backup_root)
        or record.reason != reason
        or not record.validated
    ):
        raise _backup_error("database backup result is invalid")
    if context.current_release_id is not None and record.current_release_id != context.current_release_id:
        raise _backup_error("database backup result is invalid")
    if context.candidate_release_id is not None and record.candidate_release_id != context.candidate_release_id:
        raise _backup_error("database backup result is invalid")


def _backup_error(message: str) -> OpsError:
    return OpsError(
        ExitStatus.BACKUP,
        "backup",
        message,
        changed=False,
        next_action="inspect PostgreSQL and available backup capacity before retrying",
    )


def _backup_safety_error() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "backup",
        "backup lifecycle state is unsafe; inspect managed backup records before retrying",
        changed=False,
        next_action="inspect the managed lifecycle state and resolve the contradiction",
    )


__all__ = [
    "BACKUP_COMMAND",
    "BACKUP_ENVIRONMENT_FILE",
    "BACKUP_SERVICE",
    "BACKUP_TIMER",
    "BackupContext",
    "BackupServiceContract",
    "ManagedBackupAsset",
    "backup_service_contract",
    "create_backup",
    "render_backup_timer",
    "validate_systemd_calendar",
]
