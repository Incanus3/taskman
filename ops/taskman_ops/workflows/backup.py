"""Controller planning and translation for helper-owned database backup."""

from __future__ import annotations

from collections.abc import Mapping
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_runner import new_operation_id
from ..host_protocol import HostRequest
from ..output import WorkflowResult
from ..remote import Remote
from .helper_recovery import (
    database_settings,
    discover_lifecycle,
    helper_paths,
    mutable,
    result_error,
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
        lifecycle, warnings = discover_lifecycle(remote, config)
        records = mutable(lifecycle.get("records"))
        if not isinstance(records, Mapping):
            raise _safety("backup planning helper returned invalid lifecycle evidence")
        backups = records.get("backups")
        activations = records.get("activations")
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
        operation_id = new_operation_id()
        request = HostRequest(
            1,
            "backup",
            operation_id,
            {"backup_ids": backup_ids},
            helper_paths(config),
            {
                "credentials_path": _PGPASS,
                "database": database_settings(config),
                "reason": "scheduled",
                "retention": config.backup_retention,
            },
        )
        result = run_request(remote, request)
        if result.outcome not in {"succeeded", "no_change"}:
            raise result_error(result, default_status=ExitStatus.BACKUP)
        facts = _success(result, request)
        return WorkflowResult(
            "backup",
            config.name or "",
            result.outcome == "succeeded",
            "backed-up" if result.outcome == "succeeded" else "already-backed-up",
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
                "changed_stages": tuple(getattr(error, "changed_stages", ())),
                "residue_paths": tuple(getattr(error, "residue_paths", ())),
                "recovery_commands": tuple(
                    getattr(error, "recovery_commands", ())
                ),
            },
            tuple(getattr(error, "warnings", ())),
            (
                error.next_action
                if valid_failure_evidence
                else "inspect managed backup lifecycle before retrying"
            ),
            error.status if valid_failure_evidence else ExitStatus.SAFETY,
        )


def _success(result: object, request: HostRequest) -> dict[str, object]:
    lifecycle = result.lifecycle
    token = request.operation_id.removeprefix("op-")
    succeeded = result.outcome == "succeeded"
    if (
        not isinstance(lifecycle, Mapping)
        or set(lifecycle) != _LIFECYCLE_KEYS
        or lifecycle["backup_id"] != f"backup-{token}"
        or _BACKUP_RE.fullmatch(str(lifecycle["backup_id"])) is None
        or lifecycle["dump_path"]
        != f"{request.paths['backup_root']}/{lifecycle['backup_id']}.dump"
        or type(lifecycle["size_bytes"]) is not int
        or lifecycle["size_bytes"] <= 0
        or type(lifecycle["source_database_size_bytes"]) is not int
        or lifecycle["source_database_size_bytes"] <= 0
        or lifecycle["reason"] != "scheduled"
        or not isinstance(lifecycle["pruned_backup_ids"], tuple)
        or result.stage != "records"
        or result.verification != {"format": "custom", "validated": True}
        or result.recovery_actions
        or result.residue_paths
    ):
        raise _safety("backup helper returned invalid success evidence")
    pruned = tuple(lifecycle["pruned_backup_ids"])
    stages = tuple(result.changed_stages)
    coherent = (
        not succeeded
        and stages == ()
        and pruned == ()
        or succeeded
        and stages == ("backup", "records")
        and pruned == ()
        or succeeded
        and stages in {
            ("backup", "records", "cleanup"),
            ("cleanup",),
        }
        and bool(pruned)
    )
    if not coherent or any(
        type(identifier) is not str
        or _BACKUP_RE.fullmatch(identifier) is None
        or identifier == lifecycle["backup_id"]
        for identifier in pruned
    ):
        raise _safety("backup helper returned invalid success evidence")
    return {
        "backup_id": lifecycle["backup_id"],
        "dump_path": lifecycle["dump_path"],
        "size_bytes": lifecycle["size_bytes"],
        "source_database_size_bytes": lifecycle[
            "source_database_size_bytes"
        ],
        "reason": lifecycle["reason"],
        "pruned_backup_ids": pruned,
        "changed_stages": stages,
        "residue_paths": (),
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
    value = getattr(error, "lifecycle", {})
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
    changed_stages = getattr(error, "changed_stages", ())
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
        or type(changed_stages) is not tuple
        or changed_stages
        not in {
            ("cleanup",),
            ("backup", "records", "cleanup"),
        }
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
