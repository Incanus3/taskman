"""Controller planning and final-result translation for restore."""

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
        "backup_id",
        "pre_restore_backup_id",
        "current_release_id",
        "intended_release_id",
        "selected_release_id",
        "service_state",
        "database_state",
        "report",
    }
)


def restore(
    remote: Remote,
    config: EnvironmentConfig,
    backup_id: str,
    *,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Confirm an exact validated backup before invoking the direct procedure."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("restore requires a validated environment configuration")
    if _BACKUP_RE.fullmatch(backup_id) is None:
        raise ValueError("restore backup identifier is invalid")
    if not isinstance(dry_run, bool):
        raise TypeError("restore dry-run flag must be boolean")
    current: str | None = None
    intended: str | None = None
    warnings: tuple[str, ...] = ()
    try:
        validate_operational_preflight(remote, config)
        discovered = run_request(remote, discovery_request(config))
        if discovered.outcome != "succeeded":
            raise result_error(discovered)
        current = _selected_release(discovered.state)
        source = _source_backup(discovered.state, backup_id)
        intended = source["source_release_id"]
        size = source["source_database_size_bytes"]
        warnings = discovered.warnings
        plan = {
            "backup_id": backup_id,
            "dump_path": (config.backup_root / f"{backup_id}.dump").as_posix(),
            "source_database_size_bytes": size,
            "current_release_id": current,
            "intended_release_id": intended,
            "planned_pre_restore_backup": True,
            "services_affected": ("taskman.service",),
            "typed_confirmation": f"restore {config.name or ''} {backup_id}",
        }
        if dry_run:
            return WorkflowResult(
                "restore",
                config.name or "",
                False,
                "planned",
                plan,
                warnings,
                "review the exact restore plan and rerun without --dry-run to confirm it",
            )
        if not (confirm or _confirm)(plan):
            return WorkflowResult(
                "restore",
                config.name or "",
                False,
                "confirmation-cancelled",
                {
                    **plan,
                    "pre_restore_backup_id": None,
                    "selected_release_id": current,
                    "service_state": "unknown",
                    "database_state": "unchanged",
                },
                warnings,
                "review the exact restore plan and confirm a later run when ready",
            )
        request = helper_request(
            "restore",
            config,
            expected_state={"selected_release_id": current, "backup_id": backup_id},
            parameters={
                "backup_id": backup_id,
                "credentials_path": _PGPASS,
                "database": database_settings(config),
                "verification": verification_settings(config),
            },
        )
        result = run_request(remote, request)
        if result.outcome != "succeeded":
            raise result_error(result)
        facts = _success(result, request, intended)
        return WorkflowResult(
            "restore",
            config.name or "",
            facts["changed"],
            "restored" if facts["changed"] else "already-restored",
            facts,
            merge_warnings(warnings, result.warnings),
            "inspect restored behavior before any later cleanup",
        )
    except OpsError as error:
        state = error.state
        return WorkflowResult(
            "restore",
            config.name or "",
            error.changed,
            "lock-contended"
            if error.status is ExitStatus.LOCKED
            else "safety-refused"
            if error.status is ExitStatus.SAFETY
            else f"{error.stage}-failed",
            {
                "backup_id": state.get("backup_id", backup_id),
                "pre_restore_backup_id": state.get("pre_restore_backup_id"),
                "current_release_id": state.get("current_release_id", current),
                "intended_release_id": state.get("intended_release_id", intended),
                "selected_release_id": state.get("selected_release_id", current),
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
        raise _safety("restore planning helper returned invalid host state")
    try:
        return validate_release_id(value["selected_release_id"])
    except ValueError:
        raise _safety("restore planning helper returned invalid host state") from None


def _source_backup(value: object, backup_id: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _safety("restore planning helper returned invalid host state")
    rows = mutable(value.get("backups"))
    if not isinstance(rows, list):
        raise _safety("restore planning helper returned invalid backup state")
    source = next((item for item in rows if isinstance(item, Mapping) and item.get("backup_id") == backup_id), None)
    if not isinstance(source, Mapping):
        raise _safety("selected backup is not a completed validated backup")
    release = source.get("source_release_id")
    size = source.get("source_database_size_bytes")
    if type(release) is not str or type(size) is not int or size <= 0:
        raise _safety("selected backup has invalid restore authority")
    try:
        release = validate_release_id(release)
    except ValueError:
        raise _safety("selected backup has invalid restore authority") from None
    return {"source_release_id": release, "source_database_size_bytes": size}


def _success(result: object, request: object, intended: str) -> dict[str, object]:
    state = getattr(result, "state", None)
    if (
        not isinstance(state, Mapping)
        or not _SUCCESS_FACTS <= set(state)
        or state["backup_id"] != request.parameters["backup_id"]
        or state["current_release_id"] != request.expected_state["selected_release_id"]
        or state["intended_release_id"] != intended
        or state["selected_release_id"] != intended
        or _BACKUP_RE.fullmatch(str(state["pre_restore_backup_id"])) is None
        or state["service_state"] != "running"
        or state["database_state"] != "restored"
        or type(state["changed"]) is not bool
    ):
        raise _safety("restore helper returned invalid final state")
    try:
        verification = successful_verification(state["report"], intended)
    except ValueError:
        raise _safety("restore helper returned invalid final state") from None
    return {
        "changed": state["changed"],
        "backup_id": state["backup_id"],
        "pre_restore_backup_id": state["pre_restore_backup_id"],
        "current_release_id": state["current_release_id"],
        "intended_release_id": state["intended_release_id"],
        "selected_release_id": state["selected_release_id"],
        "service_state": state["service_state"],
        "database_state": state["database_state"],
        "verification": verification,
    }


def _confirm(plan: Mapping[str, object]) -> bool:
    expected = str(plan["typed_confirmation"])
    return input(
        f"Restore {plan['backup_id']} and select {plan['intended_release_id']}? "
        f"Type '{expected}' to continue: "
    ).strip() == expected


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "restore",
        message,
        False,
        "inspect the selected backup and observed host state before retrying",
    )


__all__ = ["restore"]
