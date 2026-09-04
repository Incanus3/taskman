"""Controller planning and final-result translation for rollback."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..output import WorkflowResult
from ..remote import Remote
from ..releases.identifiers import validate_release_id
from .helper import (
    database_settings,
    discovery_request,
    merge_warnings,
    mutable,
    request as helper_request,
    result_error,
    run_request,
    successful_verification,
    verification_settings,
)
from .operational_preflight import validate_operational_preflight


_PGPASS = "/etc/taskman/pgpass"
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SUCCESS_FACTS = frozenset(
    {
        "changed",
        "previous_release_id",
        "target_release_id",
        "selected_release_id",
        "backup_id",
        "service_state",
        "database_state",
        "report",
    }
)


def rollback(
    remote: Remote,
    config: EnvironmentConfig,
    release_id: str,
    *,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Confirm an exact release target, then replay one helper procedure."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("rollback requires a validated environment configuration")
    if not isinstance(dry_run, bool):
        raise TypeError("rollback dry-run flag must be boolean")
    target = validate_release_id(release_id)
    current: str | None = None
    warnings: tuple[str, ...] = ()
    try:
        validate_operational_preflight(remote, config)
        discovered = run_request(remote, discovery_request(config))
        if discovered.outcome != "succeeded":
            raise result_error(discovered)
        current = _selected_release(discovered.state)
        warnings = discovered.warnings
        _target_is_installed(discovered.state, target)
        if current == target:
            raise _safety("rollback target is already selected")
        plan = {
            "current_release_id": current,
            "target_release_id": target,
            "target_release_path": (config.release_root / target).as_posix(),
            "planned_backup": True,
            "services_affected": ("taskman.service",),
            "typed_confirmation": f"rollback {config.name or ''} {target}",
        }
        if dry_run:
            return WorkflowResult(
                "rollback",
                config.name or "",
                False,
                "planned",
                {**plan, "backup_id": None, "selected_release_id": current},
                warnings,
                "review the exact rollback plan and rerun without --dry-run to confirm it",
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
                },
                warnings,
                "review the exact rollback plan and confirm a later run when ready",
            )
        request = helper_request(
            "rollback",
            config,
            expected_state={"selected_release_id": current},
            parameters={
                "target_release_id": target,
                "credentials_path": _PGPASS,
                "database": database_settings(config),
                "verification": verification_settings(config),
            },
        )
        result = run_request(remote, request)
        if result.outcome != "succeeded":
            raise result_error(result)
        facts = _success(result, request)
        return WorkflowResult(
            "rollback",
            config.name or "",
            facts["changed"],
            "rolled-back" if facts["changed"] else "already-current",
            facts,
            merge_warnings(warnings, result.warnings),
            "perform the remaining browser, email, and API acceptance checks",
        )
    except OpsError as error:
        state = error.state
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
                "previous_release_id": state.get("previous_release_id", current),
                "target_release_id": state.get("target_release_id", target),
                "selected_release_id": state.get("selected_release_id", current),
                "backup_id": state.get("backup_id"),
                "service_state": state.get("service_state", "unknown"),
                "database_state": state.get("database_state", "unknown"),
                "verification": state.get("report", {}),
            },
            merge_warnings(warnings, tuple(getattr(error, "warnings", ()))),
            error.next_action,
            error.status,
        )


def _selected_release(value: object) -> str:
    if not isinstance(value, Mapping) or type(value.get("selected_release_id")) is not str:
        raise _safety("rollback planning helper returned invalid host state")
    try:
        return validate_release_id(value["selected_release_id"])
    except ValueError:
        raise _safety("rollback planning helper returned invalid host state") from None


def _target_is_installed(value: object, target: str) -> None:
    if not isinstance(value, Mapping):
        raise _safety("rollback planning helper returned invalid host state")
    rows = mutable(value.get("releases"))
    if not isinstance(rows, list):
        raise _safety("rollback planning helper returned invalid host state")
    for row in rows:
        if isinstance(row, Mapping) and row.get("release_id") == target:
            return
    raise _safety("rollback target is not an installed completed release")


def _success(result: object, request: object) -> dict[str, object]:
    state = getattr(result, "state", None)
    if (
        not isinstance(state, Mapping)
        or not _SUCCESS_FACTS <= set(state)
        or state["previous_release_id"] != request.expected_state["selected_release_id"]
        or state["target_release_id"] != request.parameters["target_release_id"]
        or state["selected_release_id"] != request.parameters["target_release_id"]
        or _BACKUP_RE.fullmatch(str(state["backup_id"])) is None
        or state["service_state"] != "running"
        or state["database_state"] != "unchanged"
        or type(state["changed"]) is not bool
    ):
        raise _safety("rollback helper returned invalid final state")
    try:
        verification = successful_verification(state["report"], request.parameters["target_release_id"])
    except ValueError:
        raise _safety("rollback helper returned invalid final state") from None
    return {
        "changed": state["changed"],
        "previous_release_id": state["previous_release_id"],
        "target_release_id": state["target_release_id"],
        "selected_release_id": state["selected_release_id"],
        "backup_id": state["backup_id"],
        "service_state": state["service_state"],
        "database_state": state["database_state"],
        "verification": verification,
    }


def _confirm(plan: Mapping[str, object]) -> bool:
    expected = str(plan["typed_confirmation"])
    return input(
        f"Roll back Taskman from {plan['current_release_id']} to {plan['target_release_id']}? "
        f"Type '{expected}' to continue: "
    ).strip() == expected


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "rollback",
        message,
        False,
        "inspect the observed selection history and target release before retrying",
    )


__all__ = ["rollback"]
