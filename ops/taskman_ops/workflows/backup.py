"""Controller planning and translation for helper-owned database backup."""

from __future__ import annotations

from collections.abc import Mapping
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..output import WorkflowResult
from ..remote import Remote
from .helper import (
    database_settings,
    mutable,
    result_error,
    request as helper_request,
    run_request,
)


_PGPASS = "/etc/taskman/pgpass"
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_LIFECYCLE_KEYS = frozenset(
    {
        "backup_id",
        "dump_path",
        "size_bytes",
        "source_database_size_bytes",
        "reason",
        "pruned_backup_ids",
    }
)


def run_backup(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    dry_run: bool = False,
) -> WorkflowResult:
    """Plan with helper discovery, then make one correlated backup request."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("backup requires a validated environment configuration")
    if not isinstance(dry_run, bool):
        raise TypeError("backup dry-run flag must be boolean")
    backup_ids: tuple[str, ...] = ()
    try:
        discovery = run_request(remote, helper_request("discover", config))
        if discovery.outcome != "succeeded":
            raise result_error(discovery)
        warnings = discovery.warnings
        observed = mutable(discovery.state)
        if not isinstance(observed, Mapping):
            raise _safety("backup planning helper returned invalid observed state")
        backups = observed.get("backups")
        activations = observed.get("activations")
        if not isinstance(backups, list) or not isinstance(activations, list):
            raise _safety("backup planning helper returned invalid lifecycle evidence")
        backup_ids = tuple(_identifier(item, "backup_id") for item in backups)
        current = (
            _identifier(activations[-1], "candidate_release_id")
            if activations
            else None
        )
        plan = {
            "current_release_id": current,
            "planned_backup": True,
            "reason": "scheduled",
        }
        if dry_run:
            return WorkflowResult(
                "backup",
                config.name or "",
                False,
                "planned",
                plan,
                warnings,
                "review the backup plan and rerun without --dry-run",
            )
        request = helper_request(
            "backup",
            config,
            expected_state={"backup_ids": backup_ids},
            parameters={
                "credentials_path": _PGPASS,
                "database": database_settings(config),
                "reason": "scheduled",
                "retention": config.backup_retention,
            },
        )
        result = run_request(remote, request)
        if result.outcome != "succeeded":
            raise result_error(result)
        facts = _success(result)
        changed = result.state.get("changed") is True
        return WorkflowResult(
            "backup",
            config.name or "",
            changed,
            "backed-up" if changed else "already-backed-up",
            facts,
            tuple(result.warnings),
            "copy the validated local backup off-host according to the recovery policy",
        )
    except OpsError as error:
        pruned_backup_ids = _failure_pruned_backup_ids(
            error,
            known_backup_ids=backup_ids,
        )
        valid_failure_evidence = pruned_backup_ids is not None
        return WorkflowResult(
            "backup",
            config.name or "",
            error.changed,
            "lock-contended"
            if valid_failure_evidence and error.status is ExitStatus.LOCKED
            else "safety-refused"
            if not valid_failure_evidence or error.status is ExitStatus.SAFETY
            else f"{error.stage}-failed",
            {
                "backup_id": None,
                "dump_path": None,
                "reason": "scheduled",
                "pruned_backup_ids": pruned_backup_ids or (),
            },
            tuple(getattr(error, "warnings", ())),
            (
                error.next_action
                if valid_failure_evidence
                else "inspect managed backup lifecycle before retrying"
            ),
            error.status if valid_failure_evidence else ExitStatus.SAFETY,
        )


def _success(result: object) -> dict[str, object]:
    state = result.state
    if (
        not isinstance(state, Mapping)
        or not _LIFECYCLE_KEYS <= set(state)
        or _BACKUP_RE.fullmatch(str(state["backup_id"])) is None
        or type(state["size_bytes"]) is not int
        or state["size_bytes"] <= 0
        or type(state["source_database_size_bytes"]) is not int
        or state["source_database_size_bytes"] <= 0
        or state["reason"] != "scheduled"
        or not isinstance(state["pruned_backup_ids"], tuple)
    ):
        raise _safety("backup helper returned invalid success evidence")
    pruned = tuple(state["pruned_backup_ids"])
    if any(
        type(identifier) is not str
        or _BACKUP_RE.fullmatch(identifier) is None
        or identifier == state["backup_id"]
        for identifier in pruned
    ):
        raise _safety("backup helper returned invalid success evidence")
    return {
        "backup_id": state["backup_id"],
        "dump_path": state["dump_path"],
        "size_bytes": state["size_bytes"],
        "source_database_size_bytes": state[
            "source_database_size_bytes"
        ],
        "reason": state["reason"],
        "pruned_backup_ids": pruned,
    }


def _identifier(value: object, key: str) -> str:
    if not isinstance(value, Mapping) or type(value.get(key)) is not str:
        raise _safety("backup planning helper returned invalid lifecycle evidence")
    return str(value[key])


def _failure_pruned_backup_ids(
    error: OpsError,
    *,
    known_backup_ids: tuple[str, ...],
) -> tuple[str, ...] | None:
    value = getattr(error, "state", {})
    if not isinstance(value, Mapping):
        return None
    if not value:
        return ()
    if set(value) != {"pruned_backup_ids"}:
        return None
    identifiers = value["pruned_backup_ids"]
    if (
        not isinstance(identifiers, tuple)
        or not identifiers
        or any(type(identifier) is not str for identifier in identifiers)
    ):
        return None
    if (
        any(
            _BACKUP_RE.fullmatch(identifier) is None
            or identifier not in known_backup_ids
            for identifier in identifiers
        )
        or len(set(identifiers)) != len(identifiers)
        or not error.changed
        or error.status is not ExitStatus.BACKUP
        or error.stage != "cleanup"
    ):
        return None
    return identifiers


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "backup",
        message,
        False,
        "inspect managed backup lifecycle before retrying",
    )


__all__ = ["run_backup"]
