"""Controller planning, confirmation, and translation for exact cleanup."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..output import WorkflowResult
from ..remote import Remote
from .helper import (
    mutable,
    result_error,
    request as helper_request,
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
        discovery = run_request(remote, helper_request("discover", config))
        if discovery.outcome != "succeeded":
            raise result_error(discovery)
        expected = _expected_lifecycle(discovery.state)
        warnings = discovery.warnings
        inspected = run_request(
            remote,
            _request(config, expected, (), action="inspect"),
        )
        if inspected.outcome != "succeeded":
            raise result_error(inspected)
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
            _request(config, expected, plan.targets, action="execute"),
        )
        if result.outcome != "succeeded":
            raise result_error(result)
        result_facts = _success(result, plan.targets)
        warnings = _merge_warnings(warnings, result.warnings)
        return WorkflowResult(
            "cleanup",
            config.name or "",
            result.state.get("changed") is True,
            "cleaned" if result.state.get("changed") is True else "nothing-to-clean",
            result_facts,
            warnings,
            "rerun cleanup later after additional artifacts become eligible",
        )
    except OpsError as error:
        state = _error_state(error)
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
                "targets": tuple(state.get("targets", ())),
                "removed": tuple(state.get("removed", ())),
                "recoverability": tuple(state.get("recoverability", ())),
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
    expected_state: Mapping[str, object],
    targets: tuple[Mapping[str, object], ...],
    *,
    action: str,
) -> object:
    return helper_request(
        "cleanup",
        config,
        expected_state={"lifecycle": expected_state},
        parameters={
            "action": action,
            "targets": targets,
            "release_retention": config.release_retention,
            "backup_retention": config.backup_retention,
            "database_port": config.database_port,
        },
    )


def _expected_lifecycle(
    observed: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    records = mutable(observed)
    if not isinstance(records, Mapping):
        raise _safety("cleanup observed state is unavailable")
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
    state = result.state
    if (
        not isinstance(state, Mapping)
        or state.get("changed") is not False
        or not isinstance(state.get("targets"), tuple)
    ):
        raise _safety("cleanup helper returned invalid inspection evidence")
    targets = tuple(_target(value) for value in state["targets"])
    identifiers = ",".join(str(target["identifier"]) for target in targets)
    return CleanupPlan(
        targets,
        f"cleanup {environment} {identifiers or 'none'}",
    )


def _success(
    result: object,
    targets: tuple[Mapping[str, object], ...],
) -> dict[str, object]:
    state = result.state
    if (
        not isinstance(state, Mapping)
        or tuple(state.get("targets", ())) != targets
        or not isinstance(state.get("removed"), tuple)
        or not isinstance(state.get("recoverability"), tuple)
        or len(state["removed"]) != len(state["recoverability"])
    ):
        raise _safety("cleanup helper returned invalid success evidence")
    removed = tuple(_target(value) for value in state["removed"])
    if result.state.get("changed") is True and removed != targets:
        raise _safety("cleanup helper returned incomplete success evidence")
    if result.state.get("changed") is False and removed:
        raise _safety("cleanup helper returned invalid no-op evidence")
    if tuple(bool(item["recoverable"]) for item in removed) != tuple(
        state["recoverability"]
    ):
        raise _safety("cleanup helper returned invalid recovery evidence")
    return {
        "targets": targets,
        "removed": removed,
        "recoverability": tuple(state["recoverability"]),
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


def _error_state(error: OpsError) -> Mapping[str, object]:
    value = getattr(error, "state", {})
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
