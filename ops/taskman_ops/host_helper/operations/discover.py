"""Read-only lifecycle discovery and list operations."""

from __future__ import annotations

from taskman_ops.host_protocol import PROTOCOL_VERSION

from ..facts import backup_rows, classify_lifecycle, collect_lifecycle_facts, lifecycle_mapping, release_rows
from ..legacy_result import OperationRequest as HostRequest, OperationResult as HostResult
from ..lifecycle import LifecycleError, LifecycleLockContention, LifecycleStore
from ..paths import ManagedPaths, PathAuthorityError


def discover(request: HostRequest) -> HostResult:
    """Return the complete coherent lifecycle classification."""

    try:
        facts = collect_lifecycle_facts(ManagedPaths.from_mapping(request.paths))
        state = classify_lifecycle(facts)
        lifecycle = lifecycle_mapping(facts, state)
        include_manual_adoption = request.parameters == {"include_manual_adoption": True}
        if include_manual_adoption:
            lifecycle["manual_adoption"] = (
                LifecycleStore(facts.paths, owner_uid=facts.owner_uid).inspect_manual_current().to_mapping()
                if state == "manual"
                else None
            )
    except LifecycleLockContention as error:
        return _locked(request, error)
    except (LifecycleError, PathAuthorityError):
        return _refused(request)
    return _success(request, lifecycle, tuple(lifecycle["warnings"]))


def list_releases(request: HostRequest) -> HostResult:
    """Return selected release metadata from one coherent local snapshot."""

    try:
        facts = collect_lifecycle_facts(ManagedPaths.from_mapping(request.paths))
        state = classify_lifecycle(facts)
        lifecycle = {"state": state, "records": release_rows(facts), "warnings": list(facts.records.warnings)}
    except LifecycleLockContention as error:
        return _locked(request, error)
    except (LifecycleError, PathAuthorityError):
        return _refused(request)
    return _success(request, lifecycle, tuple(lifecycle["warnings"]))


def list_backups(request: HostRequest) -> HostResult:
    """Return retained backup metadata without fresh dump-content validation."""

    try:
        facts = collect_lifecycle_facts(ManagedPaths.from_mapping(request.paths))
        state = classify_lifecycle(facts)
        rows, warnings = backup_rows(facts)
        lifecycle = {"state": state, "records": rows, "warnings": warnings}
    except LifecycleLockContention as error:
        return _locked(request, error)
    except (LifecycleError, PathAuthorityError):
        return _refused(request)
    return _success(request, lifecycle, tuple(warnings))


def _success(request: HostRequest, lifecycle: dict[str, object], warnings: tuple[str, ...]) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="succeeded",
        stage="discovered",
        changed_stages=(),
        lifecycle=lifecycle,
        runtime_state={},
        verification={},
        residue_paths=(),
        recovery_actions=(),
        warnings=warnings,
    )


def _refused(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="refused",
        stage="lifecycle-records",
        changed_stages=(),
        lifecycle={},
        runtime_state={},
        verification={},
        residue_paths=(),
        recovery_actions=("inspect the managed lifecycle state before retrying",),
        warnings=(),
    )


def _locked(request: HostRequest, error: LifecycleLockContention) -> HostResult:
    holder = None if error.holder is None else error.holder.to_mapping()
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="failed",
        stage="lifecycle-lock",
        changed_stages=(),
        lifecycle={},
        runtime_state={} if holder is None else {"lock_holder": holder},
        verification={},
        residue_paths=(),
        recovery_actions=("wait for the recorded lifecycle operation to finish and retry",),
        warnings=(),
    )


__all__ = ["discover", "list_backups", "list_releases"]
