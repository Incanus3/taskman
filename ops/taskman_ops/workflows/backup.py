"""Controller planning and final-result translation for database backup."""

from __future__ import annotations

from collections.abc import Mapping
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..output import WorkflowResult
from ..remote import Remote
from .helper import database_settings, request as helper_request, result_error, run_request


_PGPASS = "/etc/taskman/pgpass"
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_FACT_KEYS = frozenset(
    {
        "backup_id",
        "dump_path",
        "size_bytes",
        "source_database_size_bytes",
        "selected_release_id",
        "service_state",
        "database_state",
    }
)


def run_backup(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    dry_run: bool = False,
) -> WorkflowResult:
    """Present one backup plan and translate one final helper result."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("backup requires a validated environment configuration")
    if not isinstance(dry_run, bool):
        raise TypeError("backup dry-run flag must be boolean")
    warnings: tuple[str, ...] = ()
    try:
        discovery = run_request(remote, helper_request("discover", config))
        if discovery.outcome != "succeeded":
            raise result_error(discovery)
        warnings = discovery.warnings
        selected = _selected_release(discovery.state)
        plan = {"selected_release_id": selected, "planned_backup": True}
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
        result = run_request(
            remote,
            helper_request(
                "backup",
                config,
                parameters={
                    "credentials_path": _PGPASS,
                    "database": database_settings(config),
                    "purpose": "scheduled",
                },
            ),
        )
        if result.outcome != "succeeded":
            raise result_error(result)
        facts = _success(result)
        return WorkflowResult(
            "backup",
            config.name or "",
            True,
            "backed-up",
            facts,
            tuple(dict.fromkeys((*warnings, *result.warnings))),
            "copy the validated backup off-host according to the recovery policy",
        )
    except OpsError as error:
        return WorkflowResult(
            "backup",
            config.name or "",
            error.changed,
            "lock-contended" if error.status is ExitStatus.LOCKED else "backup-failed",
            {"backup_id": None},
            tuple(dict.fromkeys((*warnings, *getattr(error, "warnings", ())))),
            error.next_action,
            error.status,
        )


def _selected_release(state: object) -> str | None:
    if not isinstance(state, Mapping):
        raise _safety("backup planning helper returned invalid host state")
    selected = state.get("selected_release_id")
    if selected is not None and type(selected) is not str:
        raise _safety("backup planning helper returned invalid host state")
    return selected


def _success(result: object) -> dict[str, object]:
    state = getattr(result, "state", None)
    if (
        not isinstance(state, Mapping)
        or not _FACT_KEYS <= set(state)
        or _BACKUP_RE.fullmatch(str(state["backup_id"])) is None
        or type(state["dump_path"]) is not str
        or not state["dump_path"].startswith("/")
        or type(state["size_bytes"]) is not int
        or state["size_bytes"] <= 0
        or type(state["source_database_size_bytes"]) is not int
        or state["source_database_size_bytes"] <= 0
        or state["selected_release_id"] is not None and type(state["selected_release_id"]) is not str
        or state["service_state"] not in {"running", "stopped", "failed", "unknown"}
        or state["database_state"] not in {"ready", "absent", "unknown"}
    ):
        raise _safety("backup helper returned invalid final state")
    return {key: state[key] for key in _FACT_KEYS}


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "backup",
        message,
        False,
        "inspect observed backup state before retrying",
    )


__all__ = ["run_backup"]
