"""Private compatibility boundary for procedures not yet rewritten.

This module is deliberately helper-only.  It accepts no wire data and its
``OperationResult`` never leaves the transient helper: ``__main__`` projects
it once into the final bounded protocol result.  Later command slices replace
their procedure and remove its use of this temporary representation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import secrets
import re

from taskman_ops.host_protocol import (
    HostRequest,
    HostResult,
    PROTOCOL_VERSION,
    ProtocolError,
    encode_result,
)


_PRIVATE_OPERATION_ID_RE = re.compile(r"op-[0-9a-f]{32}\Z")


def validate_private_operation_id(value: object) -> str:
    """Validate the pre-existing procedure-local identity without exposing it."""

    if type(value) is not str or _PRIVATE_OPERATION_ID_RE.fullmatch(value) is None:
        raise ValueError("invalid private operation identifier")
    return value


@dataclass(frozen=True)
class OperationRequest:
    """Private input shape required by the still-legacy helper procedures."""

    protocol_version: int
    operation: str
    operation_id: str
    expected_state: Mapping[str, object]
    paths: Mapping[str, str]
    parameters: Mapping[str, object]

    @classmethod
    def from_request(cls, request: HostRequest) -> "OperationRequest":
        """Detach legacy procedure identity from transport correlation."""

        return cls(
            protocol_version=request.protocol_version,
            operation=request.operation,
            operation_id=validate_private_operation_id(f"op-{secrets.token_hex(16)}"),
            expected_state=request.expected_state,
            paths=request.paths,
            parameters=request.parameters,
        )


@dataclass(frozen=True)
class OperationResult:
    """The old, in-process operation result; never a protocol envelope."""

    protocol_version: int
    operation: str
    operation_id: str
    outcome: str
    stage: str
    changed_stages: tuple[str, ...]
    lifecycle: Mapping[str, object]
    runtime_state: Mapping[str, object]
    verification: Mapping[str, object]
    residue_paths: tuple[str, ...]
    recovery_actions: tuple[str, ...]
    warnings: tuple[str, ...]


def project_result(request: HostRequest, result: OperationResult) -> HostResult:
    """Project private procedure evidence into the final public result.

    The projection is intentionally lossy: old stage histories, lifecycle and
    runtime records, residue, recovery programs, and private operation IDs
    never cross this boundary.  Only concise facts consumed by current public
    commands are selected.
    """

    if result.operation != request.operation:
        raise ValueError("helper operation returned an unrelated private result")

    outcome = _outcome(result)
    try:
        projected = HostResult(
            protocol_version=PROTOCOL_VERSION,
            operation=request.operation,
            correlation_id=request.correlation_id,
            outcome=outcome,
            message=_message(result, outcome),
            state=_state(result, outcome),
            warnings=tuple(result.warnings),
        )
        if request.operation == "discover":
            # Construction enforces nested collection limits, while encoding
            # enforces the final byte cap.  Discovery must refuse safely when
            # accepted historical authority cannot cross either boundary.
            encode_result(projected)
        return projected
    except ProtocolError:
        if request.operation == "discover":
            return _history_unavailable(request)
        raise


def _history_unavailable(request: HostRequest) -> HostResult:
    """Return a bounded refusal instead of truncating restore authority."""

    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="refused",
        message="helper discovery history exceeds protocol bounds",
        state={"history": "unavailable"},
        warnings=(),
    )


def _outcome(result: OperationResult) -> str:
    if result.outcome in {"succeeded", "no_change"}:
        return "succeeded"
    if result.outcome == "refused":
        return "refused"
    if result.outcome == "failed":
        return "manual" if result.residue_paths else "retryable"
    raise ValueError("helper operation returned an invalid private outcome")


def _message(result: OperationResult, outcome: str) -> str:
    if outcome == "succeeded":
        return "helper operation completed"
    if result.stage == "lifecycle-lock":
        return "lifecycle lock is unavailable"
    if outcome == "refused":
        return "helper operation was refused"
    if outcome == "manual":
        return "helper operation needs manual attention"
    return "helper operation can be retried"


def _state(result: OperationResult, outcome: str) -> dict[str, object]:
    state: dict[str, object] = {}
    if result.stage == "lifecycle-lock":
        state["locked"] = True
        return state

    if result.operation in {"deploy", "genesis", "rollback", "restore", "backup", "cleanup"}:
        state["changed"] = bool(result.changed_stages)
    if outcome != "succeeded":
        if result.stage == "host-preflight" and isinstance(result.runtime_state, Mapping):
            authority = result.runtime_state.get("host_authority")
            if authority in {"preflight", "unsupported"}:
                state["preflight"] = authority
        boundary = _failed_boundary(result)
        if boundary is not None:
            state["failed_boundary"] = boundary
        return state

    if result.operation in {"discover", "list_releases", "list_backups"}:
        return _discovery_state(result)
    if result.operation == "verify":
        return {"report": dict(result.verification)}
    if result.operation == "cleanup":
        return _cleanup_state(result, state)
    if result.operation == "backup":
        return _backup_state(result, state)
    if result.operation in {"deploy", "genesis", "rollback", "restore"}:
        return _mutation_state(result, state)
    return state


def _failed_boundary(result: OperationResult) -> str | None:
    if result.stage == "verification":
        return "verification"
    if result.stage == "backup":
        return "backup"
    if result.operation == "restore":
        return "restore"
    if result.operation in {"deploy", "genesis", "rollback"}:
        return "release"
    return None


def _discovery_state(result: OperationResult) -> dict[str, object]:
    lifecycle = result.lifecycle
    records = lifecycle.get("records") if isinstance(lifecycle, Mapping) else None
    value: dict[str, object] = {}
    if isinstance(lifecycle, Mapping) and lifecycle.get("state") in {"empty", "manual", "managed", "staged"}:
        value["host_kind"] = lifecycle["state"]
    if result.operation == "list_releases":
        if isinstance(records, (list, tuple)):
            value["releases"] = tuple(records)
        return value
    if result.operation == "list_backups":
        if isinstance(records, (list, tuple)):
            value["backups"] = tuple(records)
        return value
    if isinstance(records, Mapping):
        for old_key, new_key in (
            ("releases", "releases"),
            ("backups", "backups"),
            ("activations", "activations"),
            ("adoptions", "adoptions"),
        ):
            item = records.get(old_key)
            if isinstance(item, (list, tuple)):
                value[new_key] = tuple(item)
    migrations = lifecycle.get("current_migrations") if isinstance(lifecycle, Mapping) else None
    if isinstance(migrations, (list, tuple)):
        value["applied_migrations"] = tuple(migrations)
    release_migrations = lifecycle.get("release_migrations") if isinstance(lifecycle, Mapping) else None
    if isinstance(release_migrations, (list, tuple)):
        value["release_migrations"] = tuple(release_migrations)
    manual = lifecycle.get("manual_adoption") if isinstance(lifecycle, Mapping) else None
    if manual is not None:
        value["manual_adoption"] = manual
    return value


def _backup_state(result: OperationResult, state: dict[str, object]) -> dict[str, object]:
    if not isinstance(result.lifecycle, Mapping):
        return state
    for key in (
        "backup_id",
        "dump_path",
        "size_bytes",
        "source_database_size_bytes",
        "reason",
        "pruned_backup_ids",
    ):
        if key in result.lifecycle:
            state[key] = result.lifecycle[key]
    return state


def _cleanup_state(result: OperationResult, state: dict[str, object]) -> dict[str, object]:
    if not isinstance(result.lifecycle, Mapping):
        return state
    for key in ("targets", "removed", "recoverability"):
        if key in result.lifecycle:
            state[key] = result.lifecycle[key]
    return state


def _mutation_state(result: OperationResult, state: dict[str, object]) -> dict[str, object]:
    if isinstance(result.lifecycle, Mapping):
        for key in (
            "previous_release_id",
            "candidate_release_id",
            "selected_release_id",
            "backup_id",
            "activation_id",
            "pre_restore_backup_id",
            "current_release_id",
            "intended_release_id",
            "dump_path",
            "dump_size_bytes",
            "source_database_size_bytes",
            "dump_validated",
            "service_state",
            "database_state",
            "activation_recorded",
            "restore_recorded",
        ):
            if key in result.lifecycle:
                state[key] = result.lifecycle[key]
    if result.verification:
        state["report"] = dict(result.verification)
    return state


__all__ = [
    "OperationRequest",
    "OperationResult",
    "project_result",
    "validate_private_operation_id",
]
