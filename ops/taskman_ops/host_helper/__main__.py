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
    validate_cleanup_completion,
    validate_mutation_state,
    validate_verification_report,
)
from taskman_ops.host_protocol.envelope import validate_result_for_request
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
_INVALID_MUTATION_WARNING = "invalid internal mutation evidence was rejected"
_ENCODING_WARNING = "helper result encoding failed; final observations are unavailable"


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

    cleanup_inspection = request.operation == "cleanup" and request.parameters.get("action") == "inspect"
    if outcome is None:
        outcome = "refused" if request.operation == "cleanup" else "retryable"
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
    try:
        state = validate_mutation_state(request.operation, outcome, state)
    except ProtocolError:
        observations, unavailable = unavailable_observations(request.operation)
        state.update(
            observations=observations,
            unavailable_fields=unavailable,
            inspection_error="inspection-failed",
        )
        state = validate_mutation_state(request.operation, outcome, state)
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


def _observe_final_mutation_safely(
    request: HostRequest,
) -> tuple[dict[str, object], tuple[str, ...], str | None]:
    try:
        observed = _observe_final_mutation(request)
        if (
            not isinstance(observed, tuple)
            or len(observed) != 3
            or not isinstance(observed[0], Mapping)
            or not isinstance(observed[1], (list, tuple))
            or observed[2] is not None and type(observed[2]) is not str
        ):
            raise ValueError("final mutation observation is invalid")
        return dict(observed[0]), tuple(observed[1]), observed[2]
    except LifecycleLockContention:
        observations, unavailable = unavailable_observations(request.operation)
        return observations, tuple(unavailable), "lock-unavailable"
    except Exception:
        observations, unavailable = unavailable_observations(request.operation)
        return observations, tuple(unavailable), "inspection-failed"


def _with_warning(result: HostResult, warning: str) -> HostResult:
    return HostResult(
        result.protocol_version,
        result.operation,
        result.correlation_id,
        result.outcome,
        result.message,
        result.state,
        (warning,),
    )


def _operation_failure_base(request: HostRequest, *, observe: bool) -> HostResult:
    return _mutation_failure_result(
        request,
        message="helper internal failure",
        observe=observe,
    )


def _state_projection(
    request: HostRequest,
    outcome: str,
    state: Mapping[str, object],
    base: Mapping[str, object],
    keys: tuple[str, ...],
) -> dict[str, object] | None:
    candidate = dict(base)
    for key in keys:
        if key not in state:
            return None
        candidate[key] = state[key]
    try:
        return validate_mutation_state(request.operation, outcome, candidate)
    except ProtocolError:
        return None


def _matching_completion(
    request: HostRequest,
    state: Mapping[str, object],
    base: Mapping[str, object],
    outcome: str,
) -> object:
    candidate = _state_projection(
        request, outcome, state, base, ("completed_targets",)
    )
    if candidate is None:
        return base["completed_targets"]
    try:
        validate_cleanup_completion(request, outcome, candidate)
    except ProtocolError:
        return base["completed_targets"]
    return candidate["completed_targets"]


def _matching_report(request: HostRequest, state: Mapping[str, object]) -> dict[str, object] | None:
    if request.operation == "cleanup":
        return None
    value = state.get("report")
    if value is None:
        return None
    try:
        return validate_verification_report(value)
    except ProtocolError:
        return None


def _primary_failure(
    request: HostRequest,
    result: HostResult,
    state: Mapping[str, object],
    recovered: Mapping[str, object],
    base: HostResult,
    report: dict[str, object] | None,
) -> tuple[str, str, int, str, str]:
    """Choose only a full-validator-approved primary failure tuple."""

    if report is not None and report["status"] == "failed":
        candidate = {
            **recovered,
            "report": report,
            "exit_code": report["exit_status"],
            "failed_boundary": "verification",
        }
        if (
            result.outcome != "succeeded"
            and state.get("exit_code") == report["exit_status"]
            and state.get("failed_boundary") == "verification"
        ):
            try:
                validate_mutation_state(request.operation, result.outcome, candidate)
            except ProtocolError:
                pass
            else:
                return (
                    result.outcome,
                    result.message,
                    int(report["exit_status"]),
                    "verification",
                    "original",
                )
        return (
            "retryable",
            "helper internal failure",
            int(report["exit_status"]),
            "verification",
            "repaired",
        )

    candidate = dict(recovered)
    candidate["report"] = report
    for key in ("exit_code", "failed_boundary"):
        if key not in state:
            return (
                base.outcome,
                base.message,
                int(base.state["exit_code"]),
                str(base.state["failed_boundary"]),
                "base",
            )
        candidate[key] = state[key]
    if result.outcome != "succeeded":
        try:
            validate_mutation_state(request.operation, result.outcome, candidate)
        except ProtocolError:
            pass
        else:
            return (
                result.outcome,
                result.message,
                int(candidate["exit_code"]),
                str(candidate["failed_boundary"]),
                "original",
            )
    return (
        base.outcome,
        base.message,
        int(base.state["exit_code"]),
        str(base.state["failed_boundary"]),
        "base",
    )


def _invalid_mutation_result(request: HostRequest, result: HostResult) -> HostResult:
    """Recover independent exact evidence from one request-correlated invalid result."""

    base = _operation_failure_base(request, observe=False)
    raw_state = result.state if isinstance(result.state, Mapping) else {}
    state = dict(raw_state)
    recovered = dict(base.state)

    cleanup_inspection = (
        request.operation == "cleanup" and request.parameters.get("action") == "inspect"
    )
    if cleanup_inspection:
        recovered["mutation_state"] = "unchanged"
        recovered["completed_targets"] = []
    else:
        classification = _state_projection(
            request, base.outcome, state, recovered, ("mutation_state",)
        )
        if classification is not None:
            recovered["mutation_state"] = classification["mutation_state"]

    identity_keys = (
        ("desired_release_id", "backup_id")
        if request.operation in {"deploy", "genesis"}
        else ("desired_release_id", "backup_id", "pre_restore_backup_id")
        if request.operation == "restore"
        else ()
    )
    if identity_keys:
        identities = _state_projection(
            request, base.outcome, state, recovered, identity_keys
        )
        if identities is not None:
            for key in identity_keys:
                recovered[key] = identities[key]

    if request.operation == "cleanup" and not cleanup_inspection:
        recovered["completed_targets"] = _matching_completion(
            request, state, recovered, base.outcome
        )

    report = _matching_report(request, state)
    recovered["report"] = report
    outcome, message, exit_code, boundary, _source = _primary_failure(
        request, result, state, recovered, base, report
    )
    recovered["exit_code"] = exit_code
    recovered["failed_boundary"] = boundary

    facts = _state_projection(
        request,
        outcome,
        state,
        recovered,
        ("observations", "unavailable_fields", "inspection_error"),
    )
    if facts is not None:
        for key in ("observations", "unavailable_fields", "inspection_error"):
            recovered[key] = facts[key]
    else:
        observations, unavailable, inspection_error = _observe_final_mutation_safely(request)
        observed = {
            **recovered,
            "observations": observations,
            "unavailable_fields": unavailable,
            "inspection_error": inspection_error,
        }
        try:
            observed = validate_mutation_state(request.operation, outcome, observed)
        except ProtocolError:
            pass
        else:
            for key in ("observations", "unavailable_fields", "inspection_error"):
                recovered[key] = observed[key]

    validated = validate_mutation_state(request.operation, outcome, recovered)
    if request.operation == "cleanup":
        validate_cleanup_completion(request, outcome, validated)
    return HostResult.for_request(
        request, outcome, message, validated, (_INVALID_MUTATION_WARNING,)
    )


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
    """Encode once, then retain validated mutation proof in one reduced envelope."""

    try:
        return encode_result(result)
    except Exception:
        if request is not None and request.operation in MUTATION_OPERATIONS:
            if (
                request.operation == "cleanup"
                and request.parameters.get("action") == "inspect"
                and result.outcome == "succeeded"
            ):
                return encode_result(
                    _with_warning(_operation_failure_base(request, observe=False), _ENCODING_WARNING)
                )
            exact = validate_mutation_state(request.operation, result.outcome, result.state)
            if request.operation == "cleanup":
                validate_cleanup_completion(request, result.outcome, exact)
            base = _operation_failure_base(request, observe=False)
            reduced = dict(exact)
            reduced.update(
                observations=base.state["observations"],
                unavailable_fields=base.state["unavailable_fields"],
                inspection_error=base.state["inspection_error"],
            )
            outcome = result.outcome
            if outcome == "succeeded":
                outcome = base.outcome
                reduced["exit_code"] = base.state["exit_code"]
                reduced["failed_boundary"] = base.state["failed_boundary"]
            validated = validate_mutation_state(request.operation, outcome, reduced)
            if request.operation == "cleanup":
                validate_cleanup_completion(request, outcome, validated)
            return encode_result(
                HostResult.for_request(
                    request,
                    outcome,
                    "helper internal failure",
                    validated,
                    (_ENCODING_WARNING,),
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
    try:
        result = validate_result_for_request(request, result)
    except ProtocolError:
        return _with_warning(_operation_failure_base(request, observe=True), _INVALID_MUTATION_WARNING)
    if request.operation in MUTATION_OPERATIONS and not (
        request.operation == "cleanup"
        and request.parameters.get("action") == "inspect"
        and result.outcome == "succeeded"
    ):
        try:
            exact = validate_mutation_state(request.operation, result.outcome, result.state)
            if request.operation == "cleanup":
                validate_cleanup_completion(request, result.outcome, exact)
        except ProtocolError:
            return _invalid_mutation_result(request, result)
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
