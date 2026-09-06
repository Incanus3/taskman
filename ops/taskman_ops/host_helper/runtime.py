"""Small common runtime for explicit helper lifecycle transactions.

Operation modules retain their own policy and call ordinary Python stage
functions in order.  This module only owns the common lock, stage, cleanup,
and terminal-evidence mechanics; it does not interpret a workflow language.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import time
from typing import Literal

from .lifecycle import LifecycleLockContention


Outcome = Literal["succeeded", "no_change", "refused", "failed"]
StageCallback = Callable[[], bool | None]
ReleaseLock = Callable[[], None]
AcquireLock = Callable[[], ReleaseLock]
Discover = Callable[[], Mapping[str, object]]
Cleanup = Callable[[], None]


@dataclass(frozen=True)
class TransactionStage:
    """One named explicit mutation or verification callback."""

    name: str
    callback: StageCallback


@dataclass(frozen=True)
class RuntimeFailure(Exception):
    """A redacted terminal transaction failure supplied by an operation stage."""

    stage: str
    message: str
    outcome: Outcome = "failed"
    recovery_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in {"refused", "failed"}:
            raise ValueError("runtime failure outcome is invalid")
        if not self.stage or not self.message:
            raise ValueError("runtime failure requires stage and message")


@dataclass(frozen=True)
class RuntimeResult:
    """Transaction evidence independent of the protocol envelope."""

    outcome: Outcome
    stage: str
    changed_stages: tuple[str, ...]
    lifecycle: Mapping[str, object]
    runtime_state: Mapping[str, object]
    residue_paths: tuple[str, ...]
    recovery_actions: tuple[str, ...]
    warnings: tuple[str, ...]


_ALLOWED_STAGE_PROGRAMS = {
    "deploy": frozenset(
        {
            ("staging", "backup", "stop", "migration", "selection", "start", "verification", "records"),
        }
    ),
    "genesis": frozenset(
        {
            ("staging", "backup", "migration", "selection", "start", "verification", "records"),
        }
    ),
    "rollback": frozenset({("backup", "stop", "migration", "selection", "start", "verification", "records")}),
    "restore": frozenset({("backup", "stop", "migration", "selection", "start", "verification", "records")}),
    "backup": frozenset({("backup", "records")}),
    "cleanup": frozenset({("cleanup",)}),
}

# A stage starts only when the operation can finish its bounded work and still
# encode cleanup evidence.  These ceilings cover the fixed helper commands,
# filesystem durability work, and verification's own bounded probing; callers
# may supply a stricter operation-specific map but cannot omit a stage budget.
_DEFAULT_STAGE_BUDGETS = {
    "staging": 60.0,
    "backup": 90.0,
    # ``systemctl`` has its own bounded command timeout and must be followed
    # by an equally bounded state observation before evidence is truthful.
    "stop": 60.0,
    "migration": 30.0,
    "selection": 30.0,
    "start": 60.0,
    "verification": 45.0,
    "records": 30.0,
    "cleanup": 30.0,
}
_DEFAULT_CLEANUP_ENCODING_MARGIN = 30.0


class TransactionRuntime:
    """Run one explicit transaction under a canonical operation lock."""

    def __init__(
        self,
        *,
        operation: str,
        stages: tuple[TransactionStage, ...],
        acquire_lock: AcquireLock,
        discover: Discover,
        deadline: float | None = None,
        stage_budgets: Mapping[str, float] | None = None,
        cleanup_encoding_margin: float = _DEFAULT_CLEANUP_ENCODING_MARGIN,
    ) -> None:
        if operation not in _ALLOWED_STAGE_PROGRAMS:
            raise ValueError("transaction operation is invalid")
        if not callable(acquire_lock) or not callable(discover):
            raise TypeError("transaction runtime requires lock and discovery callables")
        if not isinstance(stages, tuple) or not all(isinstance(stage, TransactionStage) for stage in stages):
            raise TypeError("transaction runtime requires transaction stages")
        self._validate_stages(operation, stages)
        self.operation = operation
        self.stages = stages
        self.acquire_lock = acquire_lock
        self.discover = discover
        if deadline is not None and type(deadline) not in {int, float}:
            raise TypeError("transaction runtime deadline is invalid")
        self.deadline = None if deadline is None else float(deadline)
        if (
            not isinstance(cleanup_encoding_margin, (int, float))
            or isinstance(cleanup_encoding_margin, bool)
            or float(cleanup_encoding_margin) <= 0.0
        ):
            raise ValueError("transaction cleanup encoding margin is invalid")
        budgets = dict(_DEFAULT_STAGE_BUDGETS)
        if stage_budgets is not None:
            if not isinstance(stage_budgets, Mapping):
                raise TypeError("transaction stage budgets are invalid")
            for name, budget in stage_budgets.items():
                if (
                    type(name) is not str
                    or not isinstance(budget, (int, float))
                    or isinstance(budget, bool)
                    or float(budget) <= 0.0
                ):
                    raise ValueError("transaction stage budgets are invalid")
                budgets[name] = float(budget)
        if any(stage.name not in budgets for stage in stages):
            raise ValueError("transaction stage budgets are invalid")
        self.stage_budgets = budgets
        self.cleanup_encoding_margin = float(cleanup_encoding_margin)
        self._cleanups: list[tuple[str, Cleanup]] = []
        self._changed: list[str] = []
        self._active_stage: str | None = None

    @staticmethod
    def _validate_stages(
        operation: str,
        stages: tuple[TransactionStage, ...],
    ) -> None:
        names = tuple(stage.name for stage in stages)
        if not names or any(not callable(stage.callback) for stage in stages):
            raise ValueError("transaction stage order is invalid")
        if names not in _ALLOWED_STAGE_PROGRAMS[operation]:
            raise ValueError("transaction stage order is invalid")

    def register_cleanup(self, residue_path: str, callback: Cleanup) -> None:
        """Register an exact operation-owned cleanup without touching foreign paths."""

        if type(residue_path) is not str or not residue_path.startswith("/") or not callable(callback):
            raise ValueError("transaction cleanup is invalid")
        self._cleanups.append((residue_path, callback))

    def mark_changed(self, stage: str) -> None:
        """Record a stage as changed before a later fallible observation.

        A rename, dump, command, or record write can take effect before its
        following fsync or validation fails.  Stage callbacks use this explicit
        marker to retain conservative evidence instead of reporting a false
        no-change result.
        """

        if stage != self._active_stage or stage not in {item.name for item in self.stages}:
            raise RuntimeError("transaction changed stage is invalid")
        if stage not in self._changed:
            self._changed.append(stage)

    def run(self) -> RuntimeResult:
        """Acquire, rediscover, execute stages, then preserve primary failure evidence."""

        lifecycle: Mapping[str, object] = {}
        self._changed = []
        primary: RuntimeFailure | None = None
        runtime_state: dict[str, object] = {}
        release: ReleaseLock | None = None
        try:
            release = self.acquire_lock()
            if not callable(release):
                raise RuntimeFailure("lifecycle-lock", "unable to acquire lifecycle lock")
            self._check_deadline()
            lifecycle = dict(self.discover())
            for stage in self.stages:
                try:
                    self._check_deadline()
                    self._active_stage = stage.name
                    self._check_stage_budget(stage.name)
                    stage_changed = stage.callback()
                except RuntimeFailure:
                    raise
                except Exception as error:
                    raise RuntimeFailure(stage.name, f"{stage.name} stage failed") from error
                if stage_changed is True:
                    self.mark_changed(stage.name)
                elif stage_changed is not False and stage_changed is not None:
                    raise RuntimeFailure(stage.name, f"{stage.name} stage returned invalid evidence")
                self._active_stage = None
                self._check_deadline()
        except RuntimeFailure as error:
            primary = error
        except LifecycleLockContention as error:
            holder = None if error.holder is None else error.holder.to_mapping()
            if holder is not None:
                runtime_state["lock_holder"] = holder
            primary = RuntimeFailure(
                "lifecycle-lock",
                "lifecycle lock is held",
                recovery_actions=("wait for the recorded lifecycle operation to finish and retry",),
            )
        except Exception as error:
            primary = RuntimeFailure("internal", "helper transaction failed")
        finally:
            self._active_stage = None
            residue_paths, warnings = self._run_cleanups()
            if release is not None:
                try:
                    release()
                except Exception:
                    warnings.append("unable to release lifecycle lock metadata")

        if primary is not None:
            if primary.stage != "lifecycle-lock":
                runtime_state["failure"] = primary.message
            return RuntimeResult(
                outcome=primary.outcome,
                stage=primary.stage,
                changed_stages=tuple(self._changed),
                lifecycle=lifecycle,
                runtime_state=runtime_state,
                residue_paths=tuple(residue_paths),
                recovery_actions=primary.recovery_actions,
                warnings=tuple(warnings),
            )
        return RuntimeResult(
            outcome="succeeded" if self._changed else "no_change",
            stage=self.stages[-1].name if self.stages else "completed",
            changed_stages=tuple(self._changed),
            lifecycle=lifecycle,
            runtime_state=runtime_state,
            residue_paths=tuple(residue_paths),
            recovery_actions=(),
            warnings=tuple(warnings),
        )

    def _run_cleanups(self) -> tuple[list[str], list[str]]:
        residue_paths: list[str] = []
        warnings: list[str] = []
        for path, callback in reversed(self._cleanups):
            try:
                callback()
            except Exception:
                residue_paths.append(path)
                # Cleanup exceptions can contain command, filesystem, or
                # credential-adjacent details.  The protocol keeps only one
                # stable redacted warning while retaining the exact residue.
                warnings.append("unable to remove operation residue")
        return residue_paths, list(dict.fromkeys(warnings))

    def _check_deadline(self) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RuntimeFailure(
                "operation-deadline",
                "helper operation deadline expired",
                recovery_actions=("inspect the recorded lifecycle evidence before retrying",),
            )

    def _check_stage_budget(self, stage: str) -> None:
        """Refuse before beginning work the runner could terminate mid-stage."""

        if self.deadline is None:
            return
        required = self.stage_budgets[stage] + self.cleanup_encoding_margin
        if self.deadline - time.monotonic() < required:
            raise RuntimeFailure(
                "operation-deadline",
                "helper operation deadline cannot cover the next stage",
                recovery_actions=("inspect the recorded lifecycle evidence before retrying",),
            )


__all__ = ["RuntimeFailure", "RuntimeResult", "TransactionRuntime", "TransactionStage"]
