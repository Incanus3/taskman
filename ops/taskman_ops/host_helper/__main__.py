"""Bounded stdin/stdout entry point for the transient host helper."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
import sys
from typing import Callable

from taskman_ops.host_protocol import (
    MAX_INPUT_BYTES,
    OPERATION_NAMES,
    PROTOCOL_VERSION,
    HostRequest,
    HostResult,
    MUTATION_OPERATIONS,
    ProtocolError,
    decode_request,
    encode_result,
    unavailable_observations,
    validate_mutation_state,
    validate_verification_report,
)
from taskman_ops.host_helper.credentials import validate_credentials
from taskman_ops.host_helper.database import database_mapping, observe_database_state
from taskman_ops.host_helper.lock import LifecycleLockContention, lifecycle_lock
from taskman_ops.host_helper.restore_database import observe_restore_databases
from taskman_ops.host_helper.operations.backup import backup
from taskman_ops.host_helper.operations.cleanup import cleanup
from taskman_ops.host_helper.operations.deploy import deploy, genesis
from taskman_ops.host_helper.operations.discover import (
    _scheduler_facts,
    discover,
    list_backups,
    list_releases,
    provision_authority,
)
from taskman_ops.host_helper.operations.rollback import rollback
from taskman_ops.host_helper.operations.restore import restore
from taskman_ops.host_helper.operations.preflight import provision_pgpass_authority, restore_preflight
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.state import (
    mutation_observation_availability,
    mutation_observations,
    observe_host_state,
)
from taskman_ops.host_helper.verification import verify
from taskman_ops.releases.identifiers import validate_release_id


_FALLBACK_OPERATION = "discover"
_FALLBACK_CORRELATION_ID = "op-00000000000000000000000000000000"


def _failure_result(
    *,
    operation: str = _FALLBACK_OPERATION,
    correlation_id: str = _FALLBACK_CORRELATION_ID,
    outcome: str = "retryable",
    message: str,
) -> HostResult:
    """Return fixed redacted state without serializing input or exceptions."""

    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=operation,
        correlation_id=correlation_id,
        outcome=outcome,
        message=message,
        state={},
        warnings=(),
    )


def _mutation_failure_result(
    request: HostRequest,
    *,
    outcome: str | None = None,
    message: str,
    boundary: str = "inspection",
    mutation_state: str | None = None,
    observe: bool = True,
) -> HostResult:
    """Return exact invocation evidence without exposing the exception."""

    cleanup_inspection = (
        request.operation == "cleanup" and request.parameters.get("action") == "inspect"
    )
    if outcome is None:
        outcome = "refused" if cleanup_inspection else "retryable"
    if mutation_state is None:
        mutation_state = "unchanged" if cleanup_inspection else "unknown"
    observations, unavailable = unavailable_observations(request.operation)
    inspection_error: str | None = "inspection-failed"
    if observe:
        try:
            observations, unavailable, inspection_error = _observe_final_mutation(request)
        except LifecycleLockContention:
            observations, unavailable = unavailable_observations(request.operation)
            inspection_error = "lock-unavailable"
        except Exception:
            observations, unavailable = unavailable_observations(request.operation)
            inspection_error = "inspection-failed"
    state: dict[str, object] = {
        "mutation_state": mutation_state,
        "exit_code": _exit_code(request.operation, boundary),
        "failed_boundary": boundary,
        "observations": observations,
        "unavailable_fields": unavailable,
        "inspection_error": inspection_error,
        "report": None,
    }
    if request.operation in {"deploy", "genesis"}:
        state.update(desired_release_id=_requested_release(request), backup_id=None)
    elif request.operation == "restore":
        state.update(
            desired_release_id=None,
            backup_id=_requested_backup(request),
            pre_restore_backup_id=None,
        )
    else:
        state["completed_targets"] = ()
    return HostResult.for_request(request, outcome, message, state)


def _observe_final_mutation(
    request: HostRequest,
) -> tuple[dict[str, object], tuple[str, ...], str | None]:
    """Attempt one read-only, lifecycle-locked post-invocation observation."""

    paths = ManagedPaths.from_mapping(request.paths)
    with lifecycle_lock(paths, timeout_seconds=5.0):
        if request.operation == "cleanup":
            state = observe_host_state(paths, allow_selection_transition=True)
            observations = mutation_observations(state, request.operation)
            unavailable, inspection_error = _observation_unavailable(
                request.operation, observations
            )
            return observations, unavailable, inspection_error
        credentials_value = request.parameters["credentials_path"]
        if type(credentials_value) is not str or not credentials_value.startswith("/"):
            raise ValueError("mutation credentials path is invalid")
        credentials = Path(credentials_value)
        validate_credentials(credentials)
        database = database_mapping(request.parameters["database"])
        restore_database_state = None
        if request.operation == "restore":
            restore_database_state = observe_restore_databases(database, credentials)
            canonical = restore_database_state["canonical"]
            database_observation = (
                {"state": "absent", "applied_migrations": ()}
                if canonical is None
                else {
                    "state": "ready",
                    "applied_migrations": (
                        canonical["applied_migrations"]
                        if canonical["migration_table_present"]
                        else ()
                    ),
                }
            )
        else:
            database_observation = observe_database_state(database, credentials)
        state = observe_host_state(
            paths,
            database=database_observation,
            include_runtime=True,
            allow_selection_transition=True,
        )
        scheduler = _scheduler_facts(paths)
        observations = mutation_observations(
            state,
            request.operation,
            scheduler=scheduler,
            restore_database_state=restore_database_state,
        )
        unavailable, inspection_error = _observation_unavailable(
            request.operation, observations
        )
        return observations, unavailable, inspection_error


def _observation_unavailable(
    operation: str,
    observations: Mapping[str, object],
) -> tuple[tuple[str, ...], str | None]:
    return mutation_observation_availability(operation, observations)


def _requested_release(request: HostRequest) -> str | None:
    target = request.parameters.get("target")
    if not isinstance(target, Mapping):
        return None
    target_kind = target.get("kind")
    if target_kind == "upload":
        manifest = target.get("manifest")
        value = manifest.get("release_id") if isinstance(manifest, Mapping) else None
    elif target_kind == "installed":
        record = target.get("release_record")
        value = record.get("release_id") if isinstance(record, Mapping) else None
    else:
        return None
    if type(value) is not str:
        return None
    try:
        return validate_release_id(value)
    except ValueError:
        return None


def _requested_backup(request: HostRequest) -> str | None:
    value = request.parameters.get("backup_id")
    return (
        value
        if type(value) is str and re.fullmatch(r"backup-[0-9a-f]{32}", value)
        else None
    )


def _exit_code(operation: str, boundary: str) -> int:
    if boundary == "input":
        return 2
    if boundary == "lock":
        return 12
    if boundary in {"authority", "expected_state", "cleanup"}:
        return 10
    if boundary == "backup":
        return 6
    if boundary == "migration":
        return 7
    if boundary == "verification":
        return 9
    if operation == "cleanup":
        return 10
    if operation == "restore":
        return 11
    return 8


def _legacy_mutation_result(request: HostRequest, result: HostResult) -> HostResult:
    """Translate current in-process handlers before their exact state crosses the wire."""

    state = result.state
    boundary_value = state.get("failed_boundary")
    boundary = boundary_value if type(boundary_value) is str else None
    boundary = {"start": "service", "stop": "service", "observation": "inspection"}.get(
        boundary, boundary
    )
    if result.outcome == "succeeded":
        boundary = None
    elif state.get("locked") is True:
        boundary = "lock"
    elif boundary is None:
        boundary = "input" if result.outcome == "refused" and not state else "authority"
    if {
        "final_observations",
        "final_unavailable_fields",
        "final_inspection_error",
    } <= set(state):
        observations = state["final_observations"]
        unavailable = state["final_unavailable_fields"]
        inspection_error = state["final_inspection_error"]
    else:
        observations, unavailable, inspection_error = _observe_final_mutation_safely(request)
    changed_value = state.get("changed")
    if changed_value is True:
        mutation_state = "changed"
    elif result.outcome == "succeeded":
        mutation_state = "unchanged" if changed_value is False else "unknown"
    elif boundary in {"input", "lock", "authority", "expected_state"}:
        mutation_state = "unchanged"
    else:
        mutation_state = "unknown"
    exact: dict[str, object] = {
        "mutation_state": mutation_state,
        "exit_code": (
            0
            if result.outcome == "succeeded"
            else _exit_code(request.operation, boundary)
        ),
        "failed_boundary": boundary,
        "observations": observations,
        "unavailable_fields": unavailable,
        "inspection_error": inspection_error,
        "report": state.get("report") or None,
    }
    if request.operation in {"deploy", "genesis"}:
        exact.update(
            desired_release_id=_requested_release(request),
            backup_id=state.get("backup_id"),
        )
    elif request.operation == "restore":
        exact.update(
            desired_release_id=state.get("intended_release_id"),
            backup_id=state.get("backup_id") or _requested_backup(request),
            pre_restore_backup_id=state.get("pre_restore_backup_id"),
        )
    else:
        exact["completed_targets"] = state.get("completed_targets", ())
    try:
        exact = validate_mutation_state(request.operation, result.outcome, exact)
    except ProtocolError:
        report = state.get("report") or None
        if report is not None:
            try:
                report = validate_verification_report(report)
            except ProtocolError:
                report = None
        return _mutation_failure_from_observation(
            request,
            observations,
            unavailable,
            inspection_error,
            mutation_state=mutation_state,
            report=report,
            message=result.message,
        )
    return HostResult.for_request(
        request,
        result.outcome,
        result.message,
        exact,
        result.warnings,
    )


def _observe_final_mutation_safely(
    request: HostRequest,
) -> tuple[dict[str, object], tuple[str, ...], str | None]:
    try:
        return _observe_final_mutation(request)
    except LifecycleLockContention:
        observations, unavailable = unavailable_observations(request.operation)
        return observations, tuple(unavailable), "lock-unavailable"
    except Exception:
        observations, unavailable = unavailable_observations(request.operation)
        return observations, tuple(unavailable), "inspection-failed"


def _mutation_failure_from_observation(
    request: HostRequest,
    observations: Mapping[str, object],
    unavailable: tuple[str, ...],
    inspection_error: str | None,
    *,
    mutation_state: str,
    report: Mapping[str, object] | None,
    message: str,
) -> HostResult:
    state: dict[str, object] = {
        "mutation_state": mutation_state,
        "exit_code": 8 if request.operation != "restore" else 11,
        "failed_boundary": "inspection",
        "observations": observations,
        "unavailable_fields": unavailable,
        "inspection_error": inspection_error,
        "report": report,
    }
    if request.operation in {"deploy", "genesis"}:
        state.update(desired_release_id=_requested_release(request), backup_id=None)
    elif request.operation == "restore":
        state.update(
            desired_release_id=None,
            backup_id=_requested_backup(request),
            pre_restore_backup_id=None,
        )
    else:
        state.update(
            mutation_state=(
                "unchanged"
                if request.parameters.get("action") == "inspect"
                else "unknown"
            ),
            exit_code=10,
            completed_targets=(),
            report=None,
        )
    outcome = "refused" if request.operation == "cleanup" else "retryable"
    return HostResult.for_request(request, outcome, message, state)


_DISPATCH: dict[str, Callable[[HostRequest], HostResult]] = {
    "discover": discover,
    "list_releases": list_releases,
    "list_backups": list_backups,
    "provision_authority": provision_authority,
    "verify": verify,
    "deploy": deploy,
    "genesis": genesis,
    "backup": backup,
    "cleanup": cleanup,
    "rollback": rollback,
    "restore": restore,
    "restore_preflight": restore_preflight,
}

if frozenset(_DISPATCH) != frozenset(OPERATION_NAMES):
    raise RuntimeError("helper dispatch does not cover the protocol operations")


def _encode_or_internal_failure(
    result: HostResult,
    *,
    operation: str,
    correlation_id: str,
    request: HostRequest | None = None,
) -> bytes:
    """Reduce serialization failures to the fixed, known-small internal result."""

    try:
        return encode_result(result)
    except Exception:
        if request is not None and request.operation in MUTATION_OPERATIONS:
            return encode_result(
                _mutation_failure_result(
                    request,
                    message="helper internal failure",
                    observe=False,
                )
            )
        return encode_result(
            _failure_result(
                operation=operation,
                correlation_id=correlation_id,
                message="helper internal failure",
            )
        )


def _dispatch(request: HostRequest) -> HostResult:
    """Invoke exactly one final-protocol helper operation."""

    try:
        handler = _DISPATCH[request.operation]
    except KeyError as error:
        raise ValueError("helper operation is unsupported") from error
    result = handler(request)
    if not isinstance(result, HostResult):
        raise TypeError("helper returned an invalid result")
    if request.operation in MUTATION_OPERATIONS and not (
        request.operation == "cleanup"
        and request.parameters.get("action") == "inspect"
        and result.outcome == "succeeded"
    ):
        try:
            validate_mutation_state(request.operation, result.outcome, result.state)
        except ProtocolError:
            return _legacy_mutation_result(request, result)
    return result


def main() -> int:
    """Read one bounded request and emit exactly one bounded redacted result."""

    if len(sys.argv) > 1:
        if sys.argv[1] == "provision-pgpass-authority":
            return provision_pgpass_authority(tuple(sys.argv[2:]), sys.stdin.buffer)
        return 2

    payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    operation = _FALLBACK_OPERATION
    correlation_id = _FALLBACK_CORRELATION_ID
    request: HostRequest | None = None
    if len(payload) > MAX_INPUT_BYTES:
        result = _failure_result(message="helper protocol failure")
    else:
        try:
            request = decode_request(payload)
        except ProtocolError:
            result = _failure_result(message="helper protocol failure")
        else:
            operation = request.operation
            correlation_id = request.correlation_id
            try:
                result = _dispatch(request)
            except Exception:
                result = (
                    _mutation_failure_result(
                        request,
                        message="helper internal failure",
                    )
                    if request.operation in MUTATION_OPERATIONS
                    else _failure_result(
                        operation=operation,
                        correlation_id=correlation_id,
                        message="helper internal failure",
                    )
                )

    sys.stdout.buffer.write(
        _encode_or_internal_failure(
            result,
            operation=operation,
            correlation_id=correlation_id,
            request=request,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
