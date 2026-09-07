"""Controller plan, confirmation, and translation for guarded restore."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
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
    verification_settings,
)


_PGPASS = "/etc/taskman/pgpass"
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_RESULT_KEYS = frozenset(
    {
        "backup_id",
        "pre_restore_backup_id",
        "current_release_id",
        "intended_release_id",
        "selected_release_id",
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
        discovery = run_request(remote, helper_request("discover", config))
        if discovery.outcome != "succeeded":
            raise result_error(discovery)
        records = mutable(discovery.state)
        warnings = discovery.warnings
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
        migrations = _migration_versions(records, intended)
        inspect_request = _request(
            config,
            backup_id,
            current,
            migrations,
            action="inspect",
        )
        inspected = run_request(remote, inspect_request)
        if inspected.outcome != "succeeded":
            raise result_error(inspected)
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
            backup_id,
            current,
            migrations,
            action="execute",
        )
        result = run_request(remote, execute_request)
        if result.outcome != "succeeded":
            raise result_error(result)
        facts = _success(result, execute_request, intended)
        warnings = _merge_warnings(warnings, result.warnings)
        return WorkflowResult(
            "restore",
            config.name or "",
            result.state.get("changed") is True,
            "restored"
            if result.state.get("changed") is True
            else "already-restored",
            facts,
            warnings,
            "inspect restored behavior before any later cleanup",
        )
    except OpsError as error:
        state = _error_state(error)
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
                "pre_restore_backup_id": state.get(
                    "pre_restore_backup_id"
                ),
                "current_release_id": state.get(
                    "current_release_id", current
                ),
                "intended_release_id": state.get(
                    "intended_release_id", intended
                ),
                "selected_release_id": state.get(
                    "selected_release_id", current
                ),
                "service_state": state.get(
                    "service_state", "unknown"
                ),
                "database_state": state.get(
                    "database_state", "unknown"
                ),
                "restore_recorded": state.get(
                    "restore_recorded", False
                ),
                "verification": state.get("report", {}),
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
    backup_id: str,
    current: str,
    migrations: tuple[str, ...],
    *,
    action: str,
) -> object:
    return helper_request(
        "restore",
        config,
        expected_state={"backup_id": backup_id, "current_release_id": current},
        parameters={
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
    request: object,
    intended: str,
) -> RestorePlan:
    state = result.state
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
        not isinstance(state, Mapping)
        or not keys <= set(state)
        or state["backup_id"] != request.parameters["backup_id"]
        or state["current_release_id"]
        != request.expected_state["current_release_id"]
        or state["intended_release_id"] != intended
        or state["dump_validated"] is not True
        or type(state["dump_path"]) is not str
        or state["dump_path"]
        != (
            f"{request.paths['backup_root']}/"
            f"{request.parameters['backup_id']}.dump"
        )
        or type(state["dump_size_bytes"]) is not int
        or state["dump_size_bytes"] <= 0
        or type(state["source_database_size_bytes"]) is not int
        or state["source_database_size_bytes"] <= 0
    ):
        raise _safety("restore helper returned invalid inspection evidence")
    return RestorePlan(
        str(state["backup_id"]),
        PurePosixPath(str(state["dump_path"])),
        int(state["dump_size_bytes"]),
        int(state["source_database_size_bytes"]),
        str(state["current_release_id"]),
        str(state["intended_release_id"]),
    )


def _success(
    result: object,
    request: object,
    intended: str,
) -> dict[str, object]:
    state = result.state
    if (
        not isinstance(state, Mapping)
        or not _RESULT_KEYS <= set(state)
        or state["backup_id"] != request.parameters["backup_id"]
        or state["current_release_id"]
        != request.expected_state["current_release_id"]
        or state["intended_release_id"] != intended
        or state["selected_release_id"] != intended
        or _BACKUP_RE.fullmatch(str(state["pre_restore_backup_id"])) is None
        or state["service_state"] != "active"
        or state["database_state"] != "restored-promoted"
        or state["restore_recorded"] is not True
    ):
        raise _safety("restore helper returned invalid success evidence")
    return {
        **dict(state),
        "verification": dict(state.get("report", {})),
    }


def _migration_versions(
    observed: Mapping[str, object],
    intended: str,
) -> tuple[str, ...]:
    manifests = mutable(observed.get("manifests"))
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


def _error_state(error: OpsError) -> Mapping[str, object]:
    value = getattr(error, "state", {})
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
