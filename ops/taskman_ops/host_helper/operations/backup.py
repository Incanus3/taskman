"""Interactive adapter for the shared completed-record backup capability."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION

from ..backups import (
    BackupAuthorityError,
    BackupCapacityError,
    CommandError,
    create_validated_backup,
    normalize_temporary_dumps,
    prepare_backup_root,
    validate_completed_backups,
)
from ..credentials import validate_credentials
from ..database import DatabaseObservationError, database_mapping, observe_database_migrations
from ..lock import LifecycleLockContention, lifecycle_lock
from ..paths import ManagedPaths, PathAuthorityError
from ..records import RecordError
from ..state import HostState, StateAmbiguityError, observe_host_state


_PARAMETER_KEYS = frozenset({"credentials_path", "database", "purpose"})
_LOCK_TIMEOUT_SECONDS = 5.0


def backup(request: HostRequest) -> HostResult:
    """Create a backup under the shared lifecycle lock for a workstation request."""

    state: HostState | None = None
    try:
        credentials, database, purpose = _inputs(request)
        paths = ManagedPaths.from_mapping(request.paths)
        _validate_authoritative_paths(paths)
        with lifecycle_lock(paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
            prepare_backup_root(paths)
            state = observe_host_state(paths)
            normalize_temporary_dumps(paths, state)
            state = observe_host_state(paths)
            validate_completed_backups(state, paths)
            validate_credentials(credentials)
            try:
                facts = observe_database_migrations(database, credentials)
            except DatabaseObservationError as error:
                raise BackupAuthorityError("database migration evidence is invalid") from error
            state = observe_host_state(paths, database=facts)
            record = create_validated_backup(state, paths, database, credentials, purpose=purpose)
            final_state = observe_host_state(paths, database=facts)
            dump = Path(paths.local(paths.backup_root / f"{record.backup_id}.dump"))
            result_state = {
                "backup_id": record.backup_id,
                "dump_path": dump.as_posix(),
                "size_bytes": dump.stat().st_size,
                "source_database_size_bytes": record.source_database_size_bytes,
                "selected_release_id": final_state.selected_release_id,
                "service_state": final_state.service_state,
                "database_state": final_state.database_state,
            }
    except LifecycleLockContention:
        return _result(request, "retryable", "lifecycle lock is unavailable", {"locked": True})
    except (StateAmbiguityError, BackupAuthorityError):
        return _result(request, "manual", "backup authority is contradictory", _state_projection(state))
    except (PathAuthorityError, BackupCapacityError, TypeError, ValueError):
        return _result(request, "refused", "backup request is unsafe", _state_projection(state))
    except (CommandError, RecordError, OSError):
        return _result(
            request,
            "retryable",
            "backup did not complete; rerun to converge",
            _state_projection(state),
        )

    return _result(
        request,
        "succeeded",
        "validated backup completed",
        result_state,
        final_state.warnings,
    )


def _inputs(request: HostRequest) -> tuple[Path, Mapping[str, object], str]:
    if not isinstance(request, HostRequest):
        raise TypeError("backup needs a host request")
    if set(request.expected_state) or set(request.parameters) != _PARAMETER_KEYS:
        raise ValueError("backup request is incomplete")
    credentials = request.parameters["credentials_path"]
    purpose = request.parameters["purpose"]
    if type(credentials) is not str or not Path(credentials).is_absolute():
        raise ValueError("backup credentials path is invalid")
    if type(purpose) is not str:
        raise ValueError("backup purpose is invalid")
    return Path(credentials), database_mapping(request.parameters["database"]), purpose


def _validate_authoritative_paths(paths: ManagedPaths) -> None:
    """Classify unsafe managed roots as ambiguity before lock acquisition."""

    try:
        paths.validate_existing(owner_uid=os.geteuid())
    except PathAuthorityError as error:
        raise StateAmbiguityError("managed backup authority is unsafe") from error


def _state_projection(state: HostState | None) -> dict[str, object]:
    return {} if state is None else {"selected_release_id": state.selected_release_id}


def _result(
    request: HostRequest,
    outcome: str,
    message: str,
    state: Mapping[str, object],
    warnings: tuple[str, ...] = (),
) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome=outcome,
        message=message,
        state=state,
        warnings=warnings,
    )


__all__ = ["backup", "create_validated_backup"]
