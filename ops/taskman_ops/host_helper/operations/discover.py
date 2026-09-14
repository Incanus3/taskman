"""Read-only projections of the completed host state."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from taskman_ops.host_protocol import (
    HostRequest,
    HostResult,
    PROTOCOL_VERSION,
    ProtocolError,
    encode_result,
)

from ..commands import CommandError
from ..credentials import validate_credentials
from ..database import database_mapping, observe_database_state
from ..lock import LifecycleLockContention, lifecycle_lock
from ..paths import ManagedPaths, PathAuthorityError
from ..state import HostState, StateAmbiguityError, observe_host_state


_SNAPSHOT_TIMEOUT_SECONDS = 5.0
_DISCOVERY_PARAMETERS = frozenset({"credentials_path", "database", "mode"})
_RESTORE_DISCOVERY_PARAMETERS = _DISCOVERY_PARAMETERS | {"backup_id"}
_DISCOVERY_MODES = frozenset({"strict", "deploy", "provision", "restore"})
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")


def discover(request: HostRequest) -> HostResult:
    """Return one bounded projection of completed records and physical state."""

    try:
        state = _observe(request)
    except LifecycleLockContention:
        return _locked(request)
    except (CommandError, PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
        return _refused(request)
    mode = request.parameters["mode"]
    if mode != "strict":
        # Timer and restore-database authority are not yet observable in this
        # continuation.  Refuse rather than representing either unknown fact
        # as a nullable absence in a non-strict projection.
        return _refused(request)
    return _success(request, _discovery_state(state), state.warnings)


def list_releases(request: HostRequest) -> HostResult:
    """Return completed release manifests from one coherent snapshot."""

    try:
        state = _observe(request)
    except LifecycleLockContention:
        return _locked(request)
    except (PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
        return _refused(request)
    projection = {"releases": tuple(record.to_mapping() for record in state.releases)}
    return _success(request, projection, state.warnings)


def list_backups(request: HostRequest) -> HostResult:
    """Return validated backup manifests from one coherent snapshot."""

    try:
        state = _observe(request)
    except LifecycleLockContention:
        return _locked(request)
    except (PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
        return _refused(request)
    projection = {"backups": tuple(record.to_mapping() for record in state.backups)}
    return _success(request, projection, state.warnings)


def _observe(request: HostRequest) -> HostState:
    paths = ManagedPaths.from_mapping(request.paths)
    # The lock is held only while the completed records and physical selection
    # are read.  Health/readiness commands run after this snapshot is released.
    with lifecycle_lock(paths, timeout_seconds=_SNAPSHOT_TIMEOUT_SECONDS):
        if request.operation == "discover":
            if set(request.expected_state):
                raise ValueError("discovery request is incomplete")
            mode = request.parameters.get("mode")
            if type(mode) is not str or mode not in _DISCOVERY_MODES:
                raise ValueError("discovery mode is invalid")
            expected_parameters = (
                _RESTORE_DISCOVERY_PARAMETERS if mode == "restore" else _DISCOVERY_PARAMETERS
            )
            if set(request.parameters) != expected_parameters:
                raise ValueError("discovery request is incomplete")
            backup_id = request.parameters.get("backup_id")
            if mode == "restore":
                if type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None:
                    raise ValueError("restore backup identifier is invalid")
            elif backup_id is not None:
                raise ValueError("discovery backup identifier is invalid")
            credentials = request.parameters["credentials_path"]
            if type(credentials) is not str or not credentials.startswith("/"):
                raise ValueError("discovery credentials path is invalid")
            credentials_path = Path(credentials)
            validate_credentials(credentials_path)
            database = database_mapping(request.parameters["database"])
            observation = observe_database_state(database, credentials_path)
            return observe_host_state(paths, database=observation)
        if request.expected_state or request.parameters:
            raise ValueError("listing request is invalid")
        return observe_host_state(paths)


def _discovery_state(state: HostState) -> dict[str, object]:
    return {
        "selected_release_id": state.selected_release_id,
        "last_successful_selection_id": state.latest_successful_selection_filename,
        "last_successful_selection": (
            None
            if state.latest_successful_selection is None
            else state.latest_successful_selection.to_mapping()
        ),
        "previous_successful_selection": (
            None
            if state.previous_successful_selection is None
            else state.previous_successful_selection.to_mapping()
        ),
        "applied_migrations": state.applied_migrations,
        "service_state": state.service_state,
        "database_state": state.database_state,
    }


def _success(
    request: HostRequest,
    state: Mapping[str, object],
    warnings: tuple[str, ...],
) -> HostResult:
    try:
        result = HostResult(
            protocol_version=PROTOCOL_VERSION,
            operation=request.operation,
            correlation_id=request.correlation_id,
            outcome="succeeded",
            message="host state observed",
            state=state,
            warnings=warnings,
        )
        # HostResult validates item counts and nesting, while the final wire
        # envelope also has a byte bound.  Check both before returning so a
        # valid HostState never falls through the helper's generic exception
        # path when its authoritative projection is too large.
        encode_result(result)
    except ProtocolError:
        return _projection_refusal(request)
    return result


def _projection_refusal(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="refused",
        message="host state projection exceeds helper bounds",
        state={},
        warnings=(),
    )


def _refused(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="refused",
        message="authoritative host state is ambiguous",
        state={},
        warnings=(),
    )


def _locked(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="retryable",
        message="lifecycle lock is unavailable",
        state={"locked": True},
        warnings=(),
    )


__all__ = ["discover", "list_backups", "list_releases"]
