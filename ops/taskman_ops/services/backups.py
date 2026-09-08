"""Validated local-backup controller boundary and systemd asset contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

from ..config import EnvironmentConfig
from ..helper_package import build_scheduled_backup_package


BACKUP_COMMAND = PurePosixPath("/usr/local/lib/taskman/taskman-backup.pyz")
BACKUP_SERVICE = PurePosixPath("/etc/systemd/system/taskman-backup.service")
BACKUP_TIMER = PurePosixPath("/etc/systemd/system/taskman-backup.timer")
BACKUP_ENVIRONMENT_FILE = PurePosixPath("/etc/taskman/taskman-backup.env")
_ASSET_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BACKUP_SCHEDULE = "*-*-* 02:15:00"
_SAFE_SYSTEMD_CALENDAR = re.compile(r"[A-Za-z0-9*.,:+/ ~-]+\Z")
CalendarValidator = Callable[[str], None]


@dataclass(frozen=True)
class ManagedBackupAsset:
    """One root-controlled file host convergence may install without enabling it."""

    source: Path | None
    destination: PurePosixPath
    mode: int
    content: bytes = b""
    sha256: str = ""


@dataclass(frozen=True)
class BackupServiceContract:
    """The non-secret files and environment needed by the timer service."""

    assets: tuple[ManagedBackupAsset, ...]
    environment: Mapping[str, str]


def backup_service_contract(config: EnvironmentConfig) -> BackupServiceContract:
    """Return the deterministic, non-mutating backup-service installation contract.

    Host convergence owns remote installation, daemon reload, and timer
    enablement. This capability intentionally has no ``Remote`` dependency, so
    callers cannot accidentally converge a host merely by asking for the asset
    contract.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("backup service contract requires an environment configuration")
    package = _scheduled_backup_asset()
    return BackupServiceContract(
        assets=(
            package,
            ManagedBackupAsset(_ASSET_ROOT / "systemd" / "taskman-backup.service", BACKUP_SERVICE, 0o644),
            ManagedBackupAsset(_ASSET_ROOT / "systemd" / "taskman-backup.timer", BACKUP_TIMER, 0o644),
        ),
        environment={
            "TASKMAN_BACKUP_DATABASE_HOST": config.database_host,
            "TASKMAN_BACKUP_DATABASE_PORT": str(config.database_port),
            "TASKMAN_BACKUP_DATABASE_ROLE": config.database_role,
            "TASKMAN_BACKUP_DATABASE_NAME": config.database_name,
            "TASKMAN_BACKUP_BACKUP_ROOT": config.backup_root.as_posix(),
            "TASKMAN_BACKUP_INSTALL_ROOT": config.install_root.as_posix(),
            "TASKMAN_BACKUP_RETENTION": str(config.backup_retention),
        },
    )


def _scheduled_backup_asset() -> ManagedBackupAsset:
    """Build the persistent package in memory so convergence installs exact bytes."""

    with tempfile.TemporaryDirectory(prefix="taskman-scheduled-backup-") as directory:
        package = build_scheduled_backup_package(Path(directory) / "taskman-backup.pyz")
        content = package.path.read_bytes()
    return ManagedBackupAsset(
        source=None,
        destination=BACKUP_COMMAND,
        mode=0o750,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
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


__all__ = [
    "BACKUP_COMMAND",
    "BACKUP_ENVIRONMENT_FILE",
    "BACKUP_SERVICE",
    "BACKUP_TIMER",
    "BackupServiceContract",
    "ManagedBackupAsset",
    "backup_service_contract",
    "render_backup_timer",
    "validate_systemd_calendar",
]
