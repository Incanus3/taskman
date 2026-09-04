"""Read-only projections of the completed host state."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

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
_DISCOVERY_PARAMETERS = frozenset({"credentials_path", "database"})


def discover(request: HostRequest) -> HostResult:
    """Return one bounded projection of completed records and physical state."""

    try:
        state = _observe(request)
    except LifecycleLockContention:
        return _locked(request)
    except (CommandError, PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
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
            if set(request.expected_state) or set(request.parameters) != _DISCOVERY_PARAMETERS:
                raise ValueError("discovery request is incomplete")
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
    releases = tuple(record.to_mapping() for record in state.releases)
    return {
        "selected_release_id": state.selected_release_id,
        "releases": releases,
        "backups": tuple(record.to_mapping() for record in state.backups),
        "selections": tuple(record.to_mapping() for record in state.selections),
        "applied_migrations": state.applied_migrations,
        "service_state": state.service_state,
        "database_state": state.database_state,
        # Restore planning still consumes this bounded migration authority
        # until its own command slice moves to HostState records.
        "release_migrations": tuple(
            {"release_id": record.release_id, "migrations": tuple(record.migrations)}
            for record in state.releases
        ),
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
