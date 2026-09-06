"""Exact controller translation for the helper's shared result planes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import re

from ..errors import ExitStatus, OpsError
from ..host_protocol import HostResult


_LOCK_RECOVERY = "wait for the recorded lifecycle operation to finish and retry"
_RECORDS_RECOVERY = "inspect the managed lifecycle state before retrying"
_HOLDER_OPERATION = re.compile(r"[a-z][a-z-]{0,63}\Z")
_HOLDER_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


def lifecycle_lock_error(result: HostResult) -> OpsError:
    """Translate only the canonical helper lifecycle-lock result to LOCKED."""

    runtime = result.runtime_state
    holder = runtime.get("lock_holder") if isinstance(runtime, Mapping) else None
    if (
        result.outcome != "failed"
        or result.stage != "lifecycle-lock"
        or result.changed_stages
        or result.lifecycle
        or result.verification
        or result.residue_paths
        or result.warnings
        or result.recovery_actions != (_LOCK_RECOVERY,)
        or not isinstance(runtime, Mapping)
        or set(runtime) not in (set(), {"lock_holder"})
    ):
        raise ValueError("invalid lifecycle lock result")
    if holder is not None and (
        not isinstance(holder, Mapping)
        or set(holder) != {"operation", "pid", "started_at", "mode"}
        or type(holder["operation"]) is not str
        or _HOLDER_OPERATION.fullmatch(holder["operation"]) is None
        or type(holder["pid"]) is not int
        or holder["pid"] <= 0
        or type(holder["started_at"]) is not str
        or not _holder_time(holder["started_at"])
        or type(holder["mode"]) is not str
        or holder["mode"] not in {"shared", "exclusive"}
    ):
        raise ValueError("invalid lifecycle lock holder")
    detail = (
        "an unknown lifecycle operation"
        if holder is None
        else f"{holder['operation']} (pid {holder['pid']}, started {holder['started_at']}, {holder['mode']})"
    )
    return OpsError(
        ExitStatus.LOCKED,
        "lifecycle-lock",
        f"lifecycle lock is held by {detail}",
        changed=False,
        next_action=_LOCK_RECOVERY,
    )


def lifecycle_records_refusal(result: HostResult) -> bool:
    """Whether ``result`` is the sole lifecycle-records refusal contract."""

    return bool(
        result.outcome == "refused"
        and result.stage == "lifecycle-records"
        and not result.changed_stages
        and not result.lifecycle
        and not result.runtime_state
        and not result.verification
        and not result.residue_paths
        and not result.warnings
        and result.recovery_actions == (_RECORDS_RECOVERY,)
    )


def _holder_time(value: str) -> bool:
    """Accept the exact UTC timestamp shape emitted by lifecycle locking."""

    if _HOLDER_TIME.fullmatch(value) is None:
        return False
    try:
        return datetime.fromisoformat(f"{value[:-1]}+00:00").tzinfo == UTC
    except ValueError:
        return False


__all__ = ["lifecycle_lock_error", "lifecycle_records_refusal"]
