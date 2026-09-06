"""Controller plan, confirmation, and translation for guarded restore."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
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
    successful_verification,
    verification_settings,
)


_PGPASS = "/etc/taskman/pgpass"
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_RECOVERY_RE = re.compile(r"recovery-[0-9a-f]{32}\Z")
_RESULT_KEYS = frozenset(
    {
        "backup_id",
        "pre_restore_backup_id",
        "current_release_id",
        "intended_release_id",
        "selected_release_id",
        "recovery_id",
        "recovery_database",
        "service_state",
        "database_state",
        "restore_recorded",
    }
)
_SUCCESS_STAGES = (
    "backup",
    "stop",
    "restore",
    "validation",
    "swap",
    "selection",
    "start",
    "verification",
    "records",
)


@dataclass(frozen=True)
class RestorePlan:
    backup_id: str
    dump_path: PurePosixPath
    dump_size_bytes: int
    source_database_size_bytes: int
    current_release_id: str
    intended_release_id: str


def restore(
    remote: Remote,
    config: EnvironmentConfig,
    backup_id: str,
    *,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Validate before confirmation, then repeat and mutate under the lock."""

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
        lifecycle, warnings = discover_lifecycle(remote, config)
        records = mutable(lifecycle.get("records"))
        if not isinstance(records, Mapping):
            raise _safety("restore planning helper returned invalid lifecycle evidence")
        activations = records.get("activations")
        backups = records.get("backups")
        if (
            not isinstance(activations, list)
            or not activations
            or not isinstance(backups, list)
        ):
            raise _safety("restore requires managed lifecycle and backup authority")
        current = _field(activations[-1], "candidate_release_id")
        backup = next(
            (
                item
                for item in backups
                if isinstance(item, Mapping)
                and item.get("backup_id") == backup_id
            ),
            None,
        )
        if not isinstance(backup, Mapping):
            raise _safety("selected backup is not recorded")
        intended = _field(backup, "current_release_id")
        migrations = _migration_versions(lifecycle, intended)
        inspect_request = _request(
            config,
            new_operation_id(),
            backup_id,
            current,
            migrations,
            action="inspect",
        )
        inspected = run_request(remote, inspect_request)
        if inspected.outcome != "succeeded":
            raise result_error(inspected, default_status=ExitStatus.RESTORE)
        plan = _inspection(inspected, inspect_request, intended)
        warnings = _merge_warnings(warnings, inspected.warnings)
        plan_mapping = {
            "backup_id": plan.backup_id,
            "dump_path": plan.dump_path.as_posix(),
            "dump_size_bytes": plan.dump_size_bytes,
            "source_database_size_bytes": plan.source_database_size_bytes,
            "current_release_id": plan.current_release_id,
            "intended_release_id": plan.intended_release_id,
            "planned_pre_restore_backup": True,
            "services_affected": ("taskman.service",),
            "typed_confirmation":
                f"restore {config.name or ''} {plan.backup_id}",
        }
        if dry_run:
            return WorkflowResult(
                "restore",
                config.name or "",
                False,
                "planned",
                plan_mapping,
                warnings,
                "review the restore plan and run without --dry-run only after explicit confirmation",
            )
        if not (confirm or _confirm)(plan_mapping):
            return WorkflowResult(
                "restore",
                config.name or "",
                False,
                "confirmation-cancelled",
                {
                    **plan_mapping,
                    "selected_release_id": current,
                    "service_state": "unknown",
                    "database_state": "unchanged",
                    "restore_recorded": False,
                },
                warnings,
                "review the exact restore plan and confirm a later run when ready",
            )
        execute_request = _request(
            config,
            new_operation_id(),
            backup_id,
            current,
            migrations,
            action="execute",
        )
        result = run_request(remote, execute_request)
        if result.outcome not in {"succeeded", "no_change"}:
            raise result_error(result, default_status=ExitStatus.RESTORE)
        facts = _success(result, execute_request, intended)
        warnings = _merge_warnings(warnings, result.warnings)
        return WorkflowResult(
            "restore",
            config.name or "",
            result.outcome == "succeeded",
            "restored"
            if result.outcome == "succeeded"
            else "already-restored",
            facts,
            warnings,
            "retain the recovery database until restored behavior is accepted",
        )
    except OpsError as error:
        lifecycle_error = _error_lifecycle(error)
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
                "backup_id": lifecycle_error.get("backup_id", backup_id),
                "pre_restore_backup_id": lifecycle_error.get(
                    "pre_restore_backup_id"
                ),
                "current_release_id": lifecycle_error.get(
                    "current_release_id", current
                ),
                "intended_release_id": lifecycle_error.get(
                    "intended_release_id", intended
                ),
                "selected_release_id": lifecycle_error.get(
                    "selected_release_id", current
                ),
                "recovery_id": lifecycle_error.get("recovery_id"),
                "recovery_database": lifecycle_error.get(
                    "recovery_database"
                ),
                "service_state": lifecycle_error.get(
                    "service_state", "unknown"
                ),
                "database_state": lifecycle_error.get(
                    "database_state", "unknown"
                ),
                "restore_recorded": lifecycle_error.get(
                    "restore_recorded", False
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
            _merge_warnings(
                warnings,
                tuple(getattr(error, "warnings", ())),
            ),
            error.next_action,
            error.status,
        )


def _request(
    config: EnvironmentConfig,
    operation_id: str,
    backup_id: str,
    current: str,
    migrations: tuple[str, ...],
    *,
    action: str,
) -> HostRequest:
    return HostRequest(
        1,
        "restore",
        operation_id,
        {"backup_id": backup_id, "current_release_id": current},
        helper_paths(config),
        {
            "action": action,
            "backup_id": backup_id,
            "credentials_path": _PGPASS,
            "database": database_settings(config),
            "expected_migration_versions": migrations,
            "verification": verification_settings(config),
        },
    )


def _inspection(
    result: object,
    request: HostRequest,
    intended: str,
) -> RestorePlan:
    lifecycle = result.lifecycle
    keys = {
        "backup_id",
        "dump_path",
        "dump_size_bytes",
        "source_database_size_bytes",
        "current_release_id",
        "intended_release_id",
        "dump_validated",
    }
    if (
        result.stage != "restore-inspected"
        or result.changed_stages
        or not isinstance(lifecycle, Mapping)
        or set(lifecycle) != keys
        or lifecycle["backup_id"] != request.parameters["backup_id"]
        or lifecycle["current_release_id"]
        != request.expected_state["current_release_id"]
        or lifecycle["intended_release_id"] != intended
        or lifecycle["dump_validated"] is not True
        or type(lifecycle["dump_path"]) is not str
        or lifecycle["dump_path"]
        != (
            f"{request.paths['backup_root']}/"
            f"{request.parameters['backup_id']}.dump"
        )
        or type(lifecycle["dump_size_bytes"]) is not int
        or lifecycle["dump_size_bytes"] <= 0
        or type(lifecycle["source_database_size_bytes"]) is not int
        or lifecycle["source_database_size_bytes"] <= 0
        or result.verification != {"format": "custom", "validated": True}
        or result.recovery_actions
        or result.residue_paths
    ):
        raise _safety("restore helper returned invalid inspection evidence")
    return RestorePlan(
        str(lifecycle["backup_id"]),
        PurePosixPath(str(lifecycle["dump_path"])),
        int(lifecycle["dump_size_bytes"]),
        int(lifecycle["source_database_size_bytes"]),
        str(lifecycle["current_release_id"]),
        str(lifecycle["intended_release_id"]),
    )


def _success(
    result: object,
    request: HostRequest,
    intended: str,
) -> dict[str, object]:
    lifecycle = result.lifecycle
    token = request.operation_id.removeprefix("op-")
    succeeded = result.outcome == "succeeded"
    expected_program = (
        result.stage == "records"
        and result.changed_stages == _SUCCESS_STAGES
        or result.stage == "records-finalized"
        and result.changed_stages == ("records",)
        if succeeded
        else result.stage == "already-restored"
        and result.changed_stages == ()
    )
    if (
        not isinstance(lifecycle, Mapping)
        or set(lifecycle) != _RESULT_KEYS
        or lifecycle["backup_id"] != request.parameters["backup_id"]
        or lifecycle["pre_restore_backup_id"] != f"backup-{token}"
        or lifecycle["current_release_id"]
        != request.expected_state["current_release_id"]
        or lifecycle["intended_release_id"] != intended
        or lifecycle["selected_release_id"] != intended
        or lifecycle["recovery_id"] != f"recovery-{token}"
        or lifecycle["recovery_database"]
        != f"taskman_recovery_{token}"
        or lifecycle["service_state"] != "active"
        or lifecycle["database_state"] != "restored-promoted"
        or lifecycle["restore_recorded"] is not True
        or not expected_program
        or result.residue_paths
    ):
        raise _safety("restore helper returned invalid success evidence")
    if not successful_verification(
        result.verification,
        release_id=intended,
    ):
        raise _safety("restore helper returned invalid verification evidence")
    return {
        **dict(lifecycle),
        "changed_stages": tuple(result.changed_stages),
        "residue_paths": tuple(result.residue_paths),
        "recovery_commands": tuple(result.recovery_actions),
        "verification": dict(result.verification),
    }


def _migration_versions(
    lifecycle: Mapping[str, object],
    intended: str,
) -> tuple[str, ...]:
    manifests = mutable(lifecycle.get("manifests"))
    if not isinstance(manifests, Mapping):
        raise _safety("restore lifecycle manifests are unavailable")
    manifest = manifests.get(intended)
    if not isinstance(manifest, Mapping):
        raise _safety("restore intended release manifest is unavailable")
    migrations = manifest.get("migrations")
    if not isinstance(migrations, list):
        raise _safety("restore intended migration authority is invalid")
    versions: list[str] = []
    for migration in migrations:
        if not isinstance(migration, Mapping):
            raise _safety("restore intended migration authority is invalid")
        filename = migration.get("filename")
        if type(filename) is not str or "_" not in filename:
            raise _safety("restore intended migration authority is invalid")
        version = filename.split("_", 1)[0]
        if not version.isdecimal():
            raise _safety("restore intended migration authority is invalid")
        versions.append(version)
    result = tuple(sorted(set(versions), key=int))
    if len(result) != len(versions):
        raise _safety("restore intended migration authority is invalid")
    return result


def _field(value: object, name: str) -> str:
    if not isinstance(value, Mapping) or type(value.get(name)) is not str:
        raise _safety("restore planning helper returned invalid lifecycle evidence")
    return str(value[name])


def _merge_warnings(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in groups for item in group))


def _error_lifecycle(error: OpsError) -> Mapping[str, object]:
    value = getattr(error, "lifecycle", {})
    return value if isinstance(value, Mapping) else {}


def _confirm(plan: Mapping[str, object]) -> bool:
    expected = str(plan["typed_confirmation"])
    return input(
        f"Restore {plan['backup_id']} and select "
        f"{plan['intended_release_id']}? Type '{expected}' to continue: "
    ).strip() == expected


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "restore",
        message,
        False,
        "inspect the selected backup and managed lifecycle before retrying",
    )


__all__ = ["RestorePlan", "restore"]
