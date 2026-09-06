"""Translate a completed helper deployment result into controller evidence."""

from __future__ import annotations

from collections.abc import Mapping
import re

from ..errors import ExitStatus, OpsError
from ..helper_runner import HelperInvocation
from ..host_protocol import HostRequest, HostResult

_LIFECYCLE_KEYS = frozenset(
    {
        "previous_release_id",
        "candidate_release_id",
        "selected_release_id",
        "backup_id",
        "activation_id",
        "service_state",
        "database_state",
        "activation_recorded",
    }
)
_ACTIVATION_RE = re.compile(r"activation-([0-9a-f]{32})\Z")
_BACKUP_RE = re.compile(r"backup-([0-9a-f]{32})\Z")
_SUCCESSFUL_VERIFICATION_CHECKS = (
    {"schema_version": 1, "name": "taskman-service", "status": "passed", "summary": "taskman.service is active with a positive MainPID"},
    {"schema_version": 1, "name": "release-identity", "status": "passed", "summary": "systemd MainPID executable is under the selected release"},
    {"schema_version": 1, "name": "caddy-service", "status": "passed", "summary": "caddy.service is active"},
    {"schema_version": 1, "name": "listener-topology", "status": "passed", "summary": "Taskman, distribution, and PostgreSQL listeners have the required topology"},
    {"schema_version": 1, "name": "startup-journal", "status": "passed", "summary": "recent startup journal evidence is clean"},
    {"schema_version": 1, "name": "local-readiness", "status": "passed", "summary": "loopback health endpoint returned exact ready response"},
    {"schema_version": 1, "name": "public-readiness", "status": "passed", "summary": "public HTTPS health endpoint returned exact ready response"},
    {"schema_version": 1, "name": "public-hsts", "status": "passed", "summary": "public HTTPS response includes HSTS"},
)

def translate_helper_deployment_result(invocation: HelperInvocation, request: HostRequest) -> dict[str, object]:
    if not isinstance(invocation, HelperInvocation):
        raise deployment_error("deploy", "deployment helper returned invalid invocation evidence", changed=True)
    result = invocation.result
    if (result.protocol_version, result.operation, result.operation_id) != (
        request.protocol_version,
        request.operation,
        request.operation_id,
    ):
        raise deployment_error("deploy", "deployment helper result does not match its request", changed=True)
    if result.stage == "lifecycle-lock":
        raise _result_error(result, invocation, status=ExitStatus.LOCKED)
    if result.outcome == "refused":
        raise _result_error(result, invocation, status=ExitStatus.SAFETY)
    if result.outcome == "failed":
        raise _result_error(result, invocation, status=_failure_status(result.stage))
    if result.outcome == "succeeded":
        _validate_success(result, request)
    elif result.outcome == "no_change":
        _validate_no_change(result, request)
    else:
        raise deployment_error("deploy", "deployment helper returned invalid result evidence", changed=True)
    lifecycle = result.lifecycle
    payload = {
        "stage": "deployed" if result.outcome == "succeeded" else "already-current" if result.outcome == "no_change" else result.stage,
        "previous_release_id": lifecycle["previous_release_id"],
        "candidate_release_id": lifecycle["candidate_release_id"],
        "selected_release_id": lifecycle["selected_release_id"],
        "backup_id": lifecycle["backup_id"],
        "activation_id": lifecycle.get("activation_id"),
        "service_state": lifecycle["service_state"],
        "database_state": lifecycle["database_state"],
        "activation_recorded": lifecycle["activation_recorded"],
        "changed": result.outcome == "succeeded",
        "changed_stages": tuple(result.changed_stages),
        "warnings": _warnings(result, invocation),
        "recovery_commands": tuple(result.recovery_actions),
        "residue_paths": tuple(result.residue_paths),
        "verification": dict(result.verification),
    }
    return payload


def _validate_success(result: HostResult, request: HostRequest) -> None:
    if result.stage != "records":
        raise _invalid_success()
    lifecycle = _lifecycle(result)
    resumed = _resume_plane(result, lifecycle)
    normal = (
        ("staging", "backup", "migration", "selection", "start", "records")
        if request.operation == "genesis"
        else ("staging", "backup", "stop", "migration", "selection", "start", "records")
    )
    resume_sequences = {("selection", "start", "records"), ("start", "records")}
    if (resumed and result.changed_stages not in resume_sequences) or (
        not resumed and result.changed_stages != normal
    ):
        raise _invalid_success()
    _validate_completed_lifecycle(lifecycle, result, request, resumed=resumed)
    _validate_verification(result.verification, candidate=request.parameters["candidate_release_id"])
    if result.recovery_actions:
        raise _invalid_success()


def _validate_no_change(result: HostResult, request: HostRequest) -> None:
    if result.stage != "already-current" or result.changed_stages:
        raise deployment_error("deploy", "deployment helper returned invalid no-change evidence", changed=False)
    lifecycle = _lifecycle(result, no_change=True)
    candidate = request.parameters["candidate_release_id"]
    if (
        lifecycle["previous_release_id"] != request.expected_state["previous_release_id"]
        or lifecycle["candidate_release_id"] != candidate
        or lifecycle["selected_release_id"] != candidate
        or lifecycle["backup_id"] is not None
        or type(lifecycle["activation_id"]) is not str
        or _ACTIVATION_RE.fullmatch(lifecycle["activation_id"]) is None
        or lifecycle["service_state"] != "active"
        # A candidate already current caused no database action even when its
        # original activation used a non-no-change migration policy.
        or lifecycle["database_state"] != "unchanged"
        or lifecycle["activation_recorded"] is not True
        or result.runtime_state != {}
        or result.recovery_actions != ()
    ):
        raise deployment_error("deploy", "deployment helper returned invalid no-change evidence", changed=False)
    _validate_verification(result.verification, candidate, no_change=True)


def _lifecycle(result: HostResult, *, no_change: bool = False) -> Mapping[str, object]:
    if not isinstance(result.lifecycle, Mapping) or set(result.lifecycle) != _LIFECYCLE_KEYS:
        error = deployment_error(
            "deploy",
            "deployment helper returned invalid no-change evidence" if no_change else "deployment helper returned invalid success evidence",
            changed=not no_change,
        )
        raise error
    return result.lifecycle


def _validate_completed_lifecycle(
    lifecycle: Mapping[str, object], result: HostResult, request: HostRequest, *, resumed: bool
) -> None:
    expected_previous = request.expected_state["previous_release_id"]
    candidate = request.parameters["candidate_release_id"]
    expected_backup = f"backup-{request.operation_id.removeprefix('op-')}"
    expected_activation = f"activation-{request.operation_id.removeprefix('op-')}"
    expected_database = "unchanged" if request.parameters["migration_policy"] == "no-change" else "changed"
    valid_identifiers = (
        type(lifecycle["previous_release_id"]) is type(expected_previous)
        and lifecycle["previous_release_id"] == expected_previous
        and lifecycle["candidate_release_id"] == candidate
        and lifecycle["selected_release_id"] == candidate
        and type(lifecycle["activation_id"]) is str
        and _ACTIVATION_RE.fullmatch(lifecycle["activation_id"]) is not None
        and lifecycle["service_state"] == "active"
        and lifecycle["database_state"] == expected_database
        and lifecycle["activation_recorded"] is True
    )
    if resumed:
        activation_match = (
            _ACTIVATION_RE.fullmatch(lifecycle["activation_id"])
            if type(lifecycle["activation_id"]) is str
            else None
        )
        backup_match = (
            _BACKUP_RE.fullmatch(lifecycle["backup_id"])
            if type(lifecycle["backup_id"]) is str
            else None
        )
        valid_identifiers = (
            valid_identifiers
            and activation_match is not None
            and backup_match is not None
            and activation_match.group(1) == backup_match.group(1)
        )
    else:
        valid_identifiers = valid_identifiers and lifecycle["backup_id"] == expected_backup and lifecycle["activation_id"] == expected_activation
    if not valid_identifiers:
        raise _invalid_success()


def _resume_plane(result: HostResult, lifecycle: Mapping[str, object]) -> bool:
    """Accept either the empty normal plane or one exact resumed activation plane."""

    state = result.runtime_state
    if state == {}:
        return False
    activation = lifecycle["activation_id"]
    if (
        isinstance(state, Mapping)
        and set(state) == {"resumed_activation_id"}
        and state["resumed_activation_id"] == activation
        and type(activation) is str
        and _ACTIVATION_RE.fullmatch(activation) is not None
    ):
        return True
    raise _invalid_success()


def _validate_verification(
    verification: object, candidate: object, *, no_change: bool = False
) -> None:
    """Require a verification report bound to this selected candidate."""

    # A successful outcome must include the report generated after this exact
    # selected release was verified; a truthy or unrelated mapping is unsafe.
    required = {
        "schema_version",
        "status",
        "exit_status",
        "release_id",
        "expected_release_id",
        "checks",
        "next_action",
    }
    if (
        not isinstance(verification, Mapping)
        or set(verification) != required
        or verification["schema_version"] != 1
        or verification["status"] != "ok"
        or verification["exit_status"] != 0
        or verification["release_id"] != candidate
        or verification["expected_release_id"] != candidate
        or not isinstance(verification["checks"], tuple)
        or verification["next_action"] is not None
    ):
        raise _invalid_result(no_change)
    checks = verification["checks"]
    if (
        not all(isinstance(item, Mapping) for item in checks)
        or tuple(dict(item) for item in checks) != _SUCCESSFUL_VERIFICATION_CHECKS
    ):
        raise _invalid_result(no_change)


def _invalid_success() -> OpsError:
    return deployment_error("deploy", "deployment helper returned invalid success evidence", changed=True)


def _invalid_result(no_change: bool) -> OpsError:
    return (
        deployment_error("deploy", "deployment helper returned invalid no-change evidence", changed=False)
        if no_change
        else _invalid_success()
    )


def _warnings(result: HostResult, invocation: HelperInvocation) -> tuple[str, ...]:
    values = (*result.warnings, *((invocation.cleanup_warning,) if invocation.cleanup_warning else ()))
    return tuple(dict.fromkeys(values))


def _result_error(result: HostResult, invocation: HelperInvocation, *, status: ExitStatus) -> OpsError:
    lifecycle = dict(result.lifecycle) if isinstance(result.lifecycle, Mapping) else {}
    error = deployment_error(
        result.stage,
        "deployment lifecycle lock is held" if status is ExitStatus.LOCKED else "deployment helper did not complete the confirmed transaction",
        changed=bool(result.changed_stages),
        status=status,
    )
    for key in _LIFECYCLE_KEYS:
        if key in lifecycle:
            setattr(error, key, lifecycle[key])
    error.changed_stages = tuple(result.changed_stages)
    error.warnings = _warnings(result, invocation)
    error.recovery_commands = tuple(result.recovery_actions)
    error.residue_paths = tuple(result.residue_paths)
    error.verification = dict(result.verification)
    error.runtime_state = dict(result.runtime_state)
    return error


def deployment_error(stage: str, message: str, *, changed: bool, status: ExitStatus = ExitStatus.SAFETY) -> OpsError:
    return OpsError(status, stage, message, changed=changed, next_action="inspect helper deployment evidence before retrying")


def _failure_status(stage: str) -> ExitStatus:
    return {
        "backup": ExitStatus.BACKUP,
        "migration": ExitStatus.MIGRATION,
        "verification": ExitStatus.READINESS,
        "lifecycle-lock": ExitStatus.LOCKED,
    }.get(stage, ExitStatus.RELEASE)



__all__ = ["deployment_error", "translate_helper_deployment_result"]
