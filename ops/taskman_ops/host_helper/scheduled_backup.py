"""Fixed environment and status adapter for the scheduled backup zipapp."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

from .backups import (
    BackupAuthorityError,
    BackupCapacityError,
    CommandError,
    create_validated_backup,
    normalize_temporary_dumps,
    prepare_backup_root,
    prune_backups,
    validate_completed_backups,
)
from .credentials import validate_credentials
from .database import database_mapping, observe_database_migrations
from .lock import LifecycleLockContention, lifecycle_lock
from .paths import ManagedPaths, PathAuthorityError
from .records import RecordError
from .state import StateAmbiguityError, observe_host_state


_ENVIRONMENT_KEYS = frozenset(
    {
        "TASKMAN_BACKUP_DATABASE_HOST",
        "TASKMAN_BACKUP_DATABASE_PORT",
        "TASKMAN_BACKUP_DATABASE_ROLE",
        "TASKMAN_BACKUP_DATABASE_NAME",
        "TASKMAN_BACKUP_BACKUP_ROOT",
        "TASKMAN_BACKUP_INSTALL_ROOT",
        "TASKMAN_BACKUP_RETENTION",
    }
)
_CREDENTIALS_PATH = Path("/etc/taskman/pgpass")
_LOCK_TIMEOUT_SECONDS = 5.0
_STATUS_MESSAGES = {
    0: "taskman scheduled backup completed",
    2: "taskman scheduled backup configuration is invalid",
    6: "taskman scheduled backup needs retry",
    10: "taskman scheduled backup needs manual attention",
    12: "taskman scheduled backup lifecycle lock is unavailable",
}


@dataclass(frozen=True)
class ScheduledBackupInputs:
    """Validated non-secret systemd configuration for one timer invocation."""

    paths: ManagedPaths
    database: Mapping[str, object]
    credentials_path: Path
    retention: int


def inputs_from_environment(environment: Mapping[str, str]) -> ScheduledBackupInputs:
    """Accept exactly the installed non-secret environment vocabulary."""

    if not isinstance(environment, Mapping):
        raise TypeError("scheduled backup environment is invalid")
    values = {key: value for key, value in environment.items() if key.startswith("TASKMAN_BACKUP_")}
    if set(values) != _ENVIRONMENT_KEYS or any(type(value) is not str for value in values.values()):
        raise ValueError("scheduled backup environment is invalid")
    try:
        port = int(values["TASKMAN_BACKUP_DATABASE_PORT"])
        retention = int(values["TASKMAN_BACKUP_RETENTION"])
    except ValueError as error:
        raise ValueError("scheduled backup environment is invalid") from error
    paths = ManagedPaths.from_mapping(
        {
            "install_root": values["TASKMAN_BACKUP_INSTALL_ROOT"],
            "backup_root": values["TASKMAN_BACKUP_BACKUP_ROOT"],
        }
    )
    database = database_mapping(
        {
            "host": values["TASKMAN_BACKUP_DATABASE_HOST"],
            "port": port,
            "role": values["TASKMAN_BACKUP_DATABASE_ROLE"],
            "name": values["TASKMAN_BACKUP_DATABASE_NAME"],
        }
    )
    if not 1 <= retention <= 64:
        raise ValueError("scheduled backup environment is invalid")
    return ScheduledBackupInputs(paths, database, _CREDENTIALS_PATH, retention)


def run_scheduled_backup(inputs: ScheduledBackupInputs) -> None:
    """Create and retain backups under the one shared lifecycle lock."""

    if not isinstance(inputs, ScheduledBackupInputs):
        raise TypeError("scheduled backup inputs are invalid")
    with lifecycle_lock(inputs.paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
        prepare_backup_root(inputs.paths)
        initial_state = observe_host_state(inputs.paths)
        normalize_temporary_dumps(inputs.paths, initial_state)
        validate_credentials(inputs.credentials_path)
        facts = observe_database_migrations(inputs.database, inputs.credentials_path)
        state = observe_host_state(inputs.paths, database=facts)
        validate_completed_backups(state, inputs.paths)
        create_validated_backup(
            state,
            inputs.paths,
            inputs.database,
            inputs.credentials_path,
            purpose="scheduled",
        )
        completed = observe_host_state(inputs.paths, database=facts)
        prune_backups(inputs.paths, completed, inputs.retention)


def main(environment: Mapping[str, str] | None = None) -> int:
    """Return the fixed non-secret systemd exit status for one invocation."""

    try:
        inputs = inputs_from_environment(os.environ if environment is None else environment)
    except (PathAuthorityError, TypeError, ValueError):
        return _status(2)
    try:
        run_scheduled_backup(inputs)
    except LifecycleLockContention:
        return _status(12)
    except (BackupCapacityError, CommandError, RecordError, OSError):
        return _status(6)
    except (BackupAuthorityError, PathAuthorityError, StateAmbiguityError, TypeError, ValueError):
        return _status(10)
    return _status(0)


def _status(value: int) -> int:
    print(_STATUS_MESSAGES[value])
    return value


__all__ = ["ScheduledBackupInputs", "inputs_from_environment", "main", "run_scheduled_backup"]
