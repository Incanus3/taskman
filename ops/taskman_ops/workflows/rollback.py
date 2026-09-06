"""Controller plan, confirmation, and translation for helper rollback."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_runner import new_operation_id
from ..host_helper.lifecycle import (
    ActivationRecord,
    AdoptionRecord,
    BackupRecord,
    LifecycleError,
    LifecycleRecords,
    ReleaseRecord,
    rollback_eligibility,
)
from ..host_protocol import HostRequest
from ..output import WorkflowResult
from ..remote import Remote
from ..releases.identifiers import validate_release_id
from .helper_recovery import (
    database_settings,
    discover_lifecycle,
    helper_paths,
    mutable,
    result_error,
    run_request,
    successful_verification,
    verification_settings,
)


_PGPASS = "/etc/taskman/pgpass"
_ID = re.compile(r"(?:backup|activation)-[0-9a-f]{32}\Z")
_RESULT_KEYS = frozenset(
    {
        "previous_release_id",
        "target_release_id",
        "selected_release_id",
        "backup_id",
        "activation_id",
        "service_state",
        "database_state",
        "activation_recorded",
    }
)
_SUCCESS_STAGES = (
    "backup",
    "stop",
    "selection",
    "start",
    "verification",
    "records",
)


@dataclass(frozen=True)
class RollbackPlan:
    current_release_id: str
    target_release_id: str
    activation_ids: tuple[str, ...]
    confirmation_fingerprint: str


def assess_rollback(
    records: LifecycleRecords,
    current_release_id: str,
    target_release_id: str,
) -> RollbackPlan:
    """Describe the exact compatible reverse activation segment."""

    if not isinstance(records, LifecycleRecords):
        raise TypeError("rollback assessment requires lifecycle records")
    try:
        current = validate_release_id(current_release_id)
        target = validate_release_id(target_release_id)
    except ValueError:
        raise _safety("release identifier is invalid") from None
    eligible, reason = rollback_eligibility(records, current, target)
    if not eligible:
        raise _safety(
            reason or "target release is not connected to activation history"
        )
    ids: list[str] = []
    edges: list[str] = []
    cursor = current
    for activation in reversed(records.activations):
        if activation.candidate_release_id != cursor:
            raise _safety("activation chain is incomplete")
        ids.append(activation.activation_id)
        edges.append(
            f"{activation.activation_id}:{activation.migration_policy}"
        )
        if activation.previous_release_id == target:
            return RollbackPlan(current, target, tuple(ids), ",".join(edges))
        if activation.previous_release_id is None:
            break
        cursor = activation.previous_release_id
    raise _safety("target release is not connected to activation history")


def rollback(
    remote: Remote,
    config: EnvironmentConfig,
    release_id: str,
    *,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Confirm helper-derived authority, then make one mutation request."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("rollback requires a validated environment configuration")
    if not isinstance(dry_run, bool):
        raise TypeError("rollback dry-run flag must be boolean")
    target = validate_release_id(release_id)
    current: str | None = None
    try:
        lifecycle, warnings = discover_lifecycle(remote, config)
        records = _records(lifecycle)
        current = records.current_release_id
        if current is None:
            raise _safety("no current release is recorded")
        assessed = assess_rollback(records, current, target)
        plan = {
            "current_release_id": assessed.current_release_id,
            "target_release_id": assessed.target_release_id,
            "activation_ids": assessed.activation_ids,
            "confirmation_fingerprint": assessed.confirmation_fingerprint,
            "target_release_path": (
                config.release_root / target
            ).as_posix(),
            "planned_backup": True,
            "services_affected": ("taskman.service",),
        }
        if dry_run:
            return WorkflowResult(
                "rollback",
                config.name or "",
                False,
                "planned",
                {
                    **plan,
                    "backup_id": None,
                    "selected_release_id": current,
                },
                warnings,
                "review the exact rollback plan and run without --dry-run only after explicit confirmation",
            )
        if not (confirm or _confirm)(plan):
            return WorkflowResult(
                "rollback",
                config.name or "",
                False,
                "confirmation-cancelled",
                {
                    **plan,
                    "backup_id": None,
                    "selected_release_id": current,
                    "service_state": "unknown",
                    "database_state": "unchanged",
                    "activation_recorded": False,
                },
                warnings,
                "review the exact rollback plan and confirm a later run when ready",
            )
        request = HostRequest(
            1,
            "rollback",
            new_operation_id(),
            {"current_release_id": current},
            helper_paths(config),
            {
                "target_release_id": target,
                "credentials_path": _PGPASS,
                "database": database_settings(config),
                "verification": verification_settings(config),
            },
        )
        result = run_request(remote, request)
        if result.outcome not in {"succeeded", "no_change"}:
            raise result_error(result, default_status=ExitStatus.RELEASE)
        facts = _validate_success(result, request)
        return WorkflowResult(
            "rollback",
            config.name or "",
            result.outcome == "succeeded",
            "rolled-back"
            if result.outcome == "succeeded"
            else "already-current",
            facts,
            tuple(result.warnings),
            "perform the remaining browser, email, and API acceptance checks",
        )
    except OpsError as error:
        lifecycle = _error_lifecycle(error)
        return WorkflowResult(
            "rollback",
            config.name or "",
            error.changed,
            "lock-contended"
            if error.status is ExitStatus.LOCKED
            else "safety-refused"
            if error.status is ExitStatus.SAFETY
            else f"{error.stage}-failed",
            {
                "previous_release_id": lifecycle.get(
                    "previous_release_id", current
                ),
                "target_release_id": lifecycle.get(
                    "target_release_id", target
                ),
                "selected_release_id": lifecycle.get(
                    "selected_release_id", current
                ),
                "backup_id": lifecycle.get("backup_id"),
                "activation_id": lifecycle.get("activation_id"),
                "service_state": lifecycle.get("service_state", "unknown"),
                "database_state": lifecycle.get(
                    "database_state", "unknown"
                ),
                "activation_recorded": lifecycle.get(
                    "activation_recorded", False
                ),
                "changed_stages": tuple(
                    getattr(error, "changed_stages", ())
                ),
                "residue_paths": tuple(
                    getattr(error, "residue_paths", ())
                ),
                "recovery_commands": tuple(
                    getattr(error, "recovery_commands", ())
                ),
                "verification": getattr(error, "verification", {}),
            },
            tuple(getattr(error, "warnings", ())),
            error.next_action,
            error.status,
        )


def _validate_success(result: object, request: HostRequest) -> dict[str, object]:
    lifecycle = result.lifecycle
    token = request.operation_id.removeprefix("op-")
    succeeded = result.outcome == "succeeded"
    expected_stages = _SUCCESS_STAGES if succeeded else ()
    if (
        not isinstance(lifecycle, Mapping)
        or set(lifecycle) != _RESULT_KEYS
        or lifecycle["previous_release_id"]
        != request.expected_state["current_release_id"]
        or lifecycle["target_release_id"]
        != request.parameters["target_release_id"]
        or lifecycle["selected_release_id"]
        != request.parameters["target_release_id"]
        or lifecycle["backup_id"] != f"backup-{token}"
        or lifecycle["activation_id"] != f"activation-{token}"
        or _ID.fullmatch(str(lifecycle["backup_id"])) is None
        or _ID.fullmatch(str(lifecycle["activation_id"])) is None
        or lifecycle["service_state"] != "active"
        or lifecycle["database_state"] != "unchanged"
        or lifecycle["activation_recorded"] is not True
        or result.changed_stages != expected_stages
        or result.stage
        != ("records" if succeeded else "already-current")
        or result.residue_paths
        or result.warnings
    ):
        raise _safety("rollback helper returned invalid success evidence")
    if not successful_verification(
        result.verification,
        release_id=request.parameters["target_release_id"],
    ):
        raise _safety("rollback helper returned invalid verification evidence")
    return {
        **dict(lifecycle),
        "changed": succeeded,
        "changed_stages": tuple(result.changed_stages),
        "residue_paths": tuple(result.residue_paths),
        "recovery_commands": tuple(result.recovery_actions),
        "verification": dict(result.verification),
    }


def _records(lifecycle: Mapping[str, object]) -> LifecycleRecords:
    value = mutable(lifecycle.get("records"))
    if not isinstance(value, Mapping):
        raise _safety("rollback planning helper returned invalid lifecycle evidence")
    try:
        releases = tuple(
            ReleaseRecord.from_mapping(item)
            for item in _list(value, "releases")
        )
        activations = tuple(
            ActivationRecord.from_mapping(item)
            for item in _list(value, "activations")
        )
        backups = tuple(
            BackupRecord.from_mapping(item)
            for item in _list(value, "backups")
        )
        adoptions = tuple(
            AdoptionRecord.from_mapping(item)
            for item in _list(value, "adoptions")
        )
    except (LifecycleError, TypeError, ValueError):
        raise _safety(
            "rollback planning helper returned invalid lifecycle evidence"
        ) from None
    return LifecycleRecords(releases, activations, backups, adoptions, ())


def _list(value: Mapping[str, object], key: str) -> list[object]:
    item = value.get(key)
    if not isinstance(item, list):
        raise ValueError(f"invalid lifecycle {key}")
    return item


def _error_lifecycle(error: OpsError) -> Mapping[str, object]:
    value = getattr(error, "lifecycle", {})
    return value if isinstance(value, Mapping) else {}


def _confirm(plan: Mapping[str, object]) -> bool:
    return (
        input(
            f"Roll back Taskman from {plan['current_release_id']} to "
            f"{plan['target_release_id']}? Type yes to continue: "
        )
        .strip()
        .lower()
        == "yes"
    )


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "rollback",
        message,
        False,
        "inspect managed activation history and use restore when rollback is unsafe",
    )


__all__ = ["RollbackPlan", "assess_rollback", "rollback"]
