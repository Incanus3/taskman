"""Controller confirmation and final-result translation for exact cleanup."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..output import WorkflowResult
from ..remote import Remote
from .helper import request as helper_request, result_error, run_request


_TARGET_KEYS = frozenset({"kind", "identifier", "path"})


@dataclass(frozen=True)
class CleanupPlan:
    """The exact target set whose deletion the operator confirms."""

    targets: tuple[Mapping[str, object], ...]
    typed_confirmation: str


def cleanup(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Inspect exact targets, obtain confirmation, then ask the helper to delete them."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("cleanup requires a validated environment configuration")
    if not isinstance(dry_run, bool):
        raise TypeError("cleanup dry-run flag must be boolean")
    warnings: tuple[str, ...] = ()
    try:
        discovery = run_request(remote, helper_request("discover", config))
        if discovery.outcome != "succeeded":
            raise result_error(discovery)
        selected = _selected_release(discovery.state)
        warnings = discovery.warnings
        inspected = run_request(remote, _request(config, selected, (), action="inspect"))
        if inspected.outcome != "succeeded":
            raise result_error(inspected)
        plan = _inspection(inspected, config.name or "")
        warnings = _merge_warnings(warnings, inspected.warnings)
        facts = {"targets": plan.targets, "typed_confirmation": plan.typed_confirmation}
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
                facts,
                warnings,
                "confirm the exact target list on a later run",
            )
        result = run_request(remote, _request(config, selected, plan.targets, action="execute"))
        if result.outcome != "succeeded":
            raise result_error(result)
        final = _final_state(result)
        return WorkflowResult(
            "cleanup",
            config.name or "",
            final["changed"],
            "cleaned" if final["changed"] else "nothing-to-clean",
            {"targets": plan.targets, **final},
            _merge_warnings(warnings, result.warnings),
            "rerun cleanup later after additional artifacts become eligible",
        )
    except OpsError as error:
        return WorkflowResult(
            "cleanup",
            config.name or "",
            error.changed,
            "lock-contended"
            if error.status is ExitStatus.LOCKED
            else "safety-refused"
            if error.status is ExitStatus.SAFETY
            else "cleanup-failed",
            {"targets": ()},
            _merge_warnings(warnings, tuple(getattr(error, "warnings", ()))),
            error.next_action,
            error.status,
        )


def _request(
    config: EnvironmentConfig,
    selected_release_id: str | None,
    targets: tuple[Mapping[str, object], ...],
    *,
    action: str,
):
    return helper_request(
        "cleanup",
        config,
        expected_state={"selected_release_id": selected_release_id},
        parameters={
            "action": action,
            "targets": targets,
            "release_retention": config.release_retention,
            "backup_retention": config.backup_retention,
        },
    )


def _selected_release(state: object) -> str | None:
    if not isinstance(state, Mapping):
        raise _safety("cleanup planning helper returned invalid host state")
    selected = state.get("selected_release_id")
    if selected is not None and type(selected) is not str:
        raise _safety("cleanup planning helper returned invalid host state")
    return selected


def _inspection(result: object, environment: str) -> CleanupPlan:
    state = getattr(result, "state", None)
    if not isinstance(state, Mapping) or not isinstance(state.get("targets"), tuple):
        raise _safety("cleanup helper returned invalid inspection state")
    targets = tuple(_target(value) for value in state["targets"])
    identifiers = ",".join(str(target["identifier"]) for target in targets)
    return CleanupPlan(targets, f"cleanup {environment} {identifiers or 'none'}")


def _final_state(result: object) -> dict[str, object]:
    state = getattr(result, "state", None)
    if (
        not isinstance(state, Mapping)
        or type(state.get("changed")) is not bool
        or state.get("selected_release_id") is not None and type(state.get("selected_release_id")) is not str
        or state.get("service_state") not in {"running", "stopped", "failed", "unknown"}
        or state.get("database_state") not in {"ready", "absent", "unknown"}
    ):
        raise _safety("cleanup helper returned invalid final state")
    return {
        "changed": state["changed"],
        "selected_release_id": state["selected_release_id"],
        "service_state": state["service_state"],
        "database_state": state["database_state"],
    }


def _target(value: object) -> Mapping[str, object]:
    if (
        not isinstance(value, Mapping)
        or set(value) != _TARGET_KEYS
        or value.get("kind") not in {"release", "backup", "temporary"}
        or type(value.get("identifier")) is not str
        or type(value.get("path")) is not str
        or not str(value["path"]).startswith("/")
    ):
        raise _safety("cleanup helper returned invalid target")
    return dict(value)


def _merge_warnings(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in groups for item in group))


def _confirm(plan: Mapping[str, object]) -> bool:
    expected = str(plan["typed_confirmation"])
    return input(f"Delete exact Taskman artifacts? Type '{expected}': ").strip() == expected


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "cleanup",
        message,
        False,
        "inspect exact cleanup targets before retrying",
    )


__all__ = ["CleanupPlan", "cleanup"]
