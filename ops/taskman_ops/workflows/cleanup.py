"""Controller planning, confirmation, and translation for exact cleanup."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_runner import new_operation_id
from ..host_protocol import HostRequest
from ..output import WorkflowResult
from ..remote import Remote
from .helper_recovery import (
    discover_lifecycle,
    helper_paths,
    mutable,
    result_error,
    run_request,
)


_TARGET_KEYS = frozenset(
    {"authority", "identifier", "kind", "path", "recoverable"}
)


@dataclass(frozen=True)
class CleanupPlan:
    """The immutable exact target list validated by the host helper."""

    targets: tuple[Mapping[str, object], ...]
    typed_confirmation: str


def cleanup(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Ask the helper to plan, confirm exact targets, then revalidate and delete."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("cleanup requires a validated environment configuration")
    if not isinstance(dry_run, bool):
        raise TypeError("cleanup dry-run flag must be boolean")
    warnings: tuple[str, ...] = ()
    try:
        lifecycle, warnings = discover_lifecycle(remote, config)
        expected = _expected_lifecycle(lifecycle)
        inspected = run_request(
            remote,
            _request(
                config,
                new_operation_id(),
                expected,
                (),
                action="inspect",
            ),
        )
        if inspected.outcome != "succeeded":
            raise result_error(inspected, default_status=ExitStatus.SAFETY)
        plan = _inspection(inspected, expected, config.name or "")
        warnings = _merge_warnings(warnings, inspected.warnings)
        facts = {
            "targets": plan.targets,
            "typed_confirmation": plan.typed_confirmation,
        }
        if dry_run:
            return WorkflowResult(
                "cleanup",
                config.name or "",
                False,
                "planned",
                facts,
                warnings,
                "review the exact cleanup targets and rerun without --dry-run",
            )
        if not (confirm or _confirm)(facts):
            return WorkflowResult(
                "cleanup",
                config.name or "",
                False,
                "confirmation-cancelled",
                {**facts, "removed": (), "recoverability": ()},
                warnings,
                "confirm the exact target list on a later run",
            )
        result = run_request(
            remote,
            _request(
                config,
                new_operation_id(),
                expected,
                plan.targets,
                action="execute",
            ),
        )
        if result.outcome not in {"succeeded", "no_change"}:
            raise result_error(result, default_status=ExitStatus.SAFETY)
        result_facts = _success(result, plan.targets)
        warnings = _merge_warnings(warnings, result.warnings)
        return WorkflowResult(
            "cleanup",
            config.name or "",
            result.outcome == "succeeded",
            "cleaned" if result.outcome == "succeeded" else "nothing-to-clean",
            result_facts,
            warnings,
            "rerun cleanup later after additional artifacts become eligible",
        )
    except OpsError as error:
        lifecycle = _error_lifecycle(error)
        return WorkflowResult(
            "cleanup",
            config.name or "",
            error.changed,
            "lock-contended"
            if error.status is ExitStatus.LOCKED
            else "safety-refused"
            if error.status is ExitStatus.SAFETY
            else f"{error.stage}-failed",
            {
                "targets": tuple(lifecycle.get("targets", ())),
                "removed": tuple(lifecycle.get("removed", ())),
                "recoverability": tuple(lifecycle.get("recoverability", ())),
                "database_state": lifecycle.get("database_state"),
                "changed_stages": tuple(
                    getattr(error, "changed_stages", ())
                ),
                "residue_paths": tuple(
                    getattr(error, "residue_paths", ())
                ),
                "recovery_commands": tuple(
                    getattr(error, "recovery_commands", ())
                ),
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
    lifecycle: Mapping[str, object],
    targets: tuple[Mapping[str, object], ...],
    *,
    action: str,
) -> HostRequest:
    return HostRequest(
        1,
        "cleanup",
        operation_id,
        {"lifecycle": lifecycle},
        helper_paths(config),
        {
            "action": action,
            "targets": targets,
            "release_retention": config.release_retention,
            "backup_retention": config.backup_retention,
            "database_port": config.database_port,
        },
    )


def _expected_lifecycle(
    lifecycle: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    records = mutable(lifecycle.get("records"))
    if not isinstance(records, Mapping):
        raise _safety("cleanup lifecycle authority is unavailable")
    values: dict[str, tuple[str, ...]] = {}
    for plural, identifier in (
        ("releases", "release_id"),
        ("activations", "activation_id"),
        ("backups", "backup_id"),
    ):
        items = records.get(plural)
        if not isinstance(items, list):
            raise _safety("cleanup lifecycle authority is invalid")
        parsed: list[str] = []
        for item in items:
            if (
                not isinstance(item, Mapping)
                or type(item.get(identifier)) is not str
            ):
                raise _safety("cleanup lifecycle authority is invalid")
            parsed.append(str(item[identifier]))
        values[plural] = tuple(parsed)
    return values


def _inspection(
    result: object,
    expected: Mapping[str, object],
    environment: str,
) -> CleanupPlan:
    lifecycle = result.lifecycle
    if (
        result.stage != "cleanup-inspected"
        or result.changed_stages
        or not isinstance(lifecycle, Mapping)
        or set(lifecycle) != {"lifecycle", "targets"}
        or lifecycle["lifecycle"] != expected
        or not isinstance(lifecycle["targets"], tuple)
        or result.verification
        or result.residue_paths
        or result.recovery_actions
    ):
        raise _safety("cleanup helper returned invalid inspection evidence")
    targets = tuple(_target(value) for value in lifecycle["targets"])
    identifiers = ",".join(str(target["identifier"]) for target in targets)
    return CleanupPlan(
        targets,
        f"cleanup {environment} {identifiers or 'none'}",
    )


def _success(
    result: object,
    targets: tuple[Mapping[str, object], ...],
) -> dict[str, object]:
    lifecycle = result.lifecycle
    if (
        result.stage != "cleanup"
        or not isinstance(lifecycle, Mapping)
        or set(lifecycle) != {"removed", "recoverability", "targets"}
        or tuple(lifecycle["targets"]) != targets
        or not isinstance(lifecycle["removed"], tuple)
        or not isinstance(lifecycle["recoverability"], tuple)
        or len(lifecycle["removed"]) != len(lifecycle["recoverability"])
        or result.changed_stages
        != (("cleanup",) if result.outcome == "succeeded" else ())
        or result.verification
        or result.recovery_actions
        or result.residue_paths
    ):
        raise _safety("cleanup helper returned invalid success evidence")
    removed = tuple(_target(value) for value in lifecycle["removed"])
    if result.outcome == "succeeded" and removed != targets:
        raise _safety("cleanup helper returned incomplete success evidence")
    if result.outcome == "no_change" and removed:
        raise _safety("cleanup helper returned invalid no-op evidence")
    if tuple(bool(item["recoverable"]) for item in removed) != tuple(
        lifecycle["recoverability"]
    ):
        raise _safety("cleanup helper returned invalid recovery evidence")
    return {
        "targets": targets,
        "removed": removed,
        "recoverability": tuple(lifecycle["recoverability"]),
        "changed_stages": tuple(result.changed_stages),
        "residue_paths": tuple(result.residue_paths),
    }


def _target(value: object) -> Mapping[str, object]:
    if (
        not isinstance(value, Mapping)
        or set(value) != _TARGET_KEYS
        or type(value["kind"]) is not str
        or type(value["identifier"]) is not str
        or type(value["path"]) is not str
        or not str(value["path"]).startswith("/")
        or type(value["recoverable"]) is not bool
    ):
        raise _safety("cleanup helper returned invalid target evidence")
    return dict(value)


def _merge_warnings(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in groups for item in group))


def _error_lifecycle(error: OpsError) -> Mapping[str, object]:
    value = getattr(error, "lifecycle", {})
    return value if isinstance(value, Mapping) else {}


def _confirm(plan: Mapping[str, object]) -> bool:
    expected = str(plan["typed_confirmation"])
    return (
        input(f"Delete exact Taskman artifacts? Type '{expected}': ").strip()
        == expected
    )


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "cleanup",
        message,
        False,
        "inspect managed lifecycle and exact cleanup authority before retrying",
    )


__all__ = ["CleanupPlan", "cleanup"]
