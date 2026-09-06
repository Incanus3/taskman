"""Transaction-runtime contracts for explicit helper operations."""

from __future__ import annotations

from collections.abc import Callable
import time

import pytest

from taskman_ops.host_helper.runtime import (
    RuntimeFailure,
    TransactionRuntime,
    TransactionStage,
)
from taskman_ops.host_helper.lifecycle import LifecycleLockContention, LifecycleLockHolder


def test_runtime_discovers_under_lock_tracks_stages_and_retains_primary_failure() -> None:
    """A cleanup error must not hide the failed stage or its recovery evidence.

    Removing the under-lock discovery call, accepting an out-of-order stage, or
    replacing the migration failure with the cleanup failure must make this
    contract fail.
    """

    events: list[str] = []

    def lock() -> Callable[[], None]:
        events.append("lock")

        def release() -> None:
            events.append("unlock")

        return release

    def discover() -> dict[str, object]:
        events.append("discover")
        return {"current_release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"}

    def stage() -> bool:
        events.append("stage")
        return True

    def backup() -> bool:
        events.append("backup")
        return True

    def stop() -> bool:
        events.append("stop")
        return True

    def migrate() -> bool:
        events.append("migrate")
        raise RuntimeFailure(
            "migration",
            "database migration failed",
            recovery_actions=("inspect the selected release and database before retrying",),
        )

    def cleanup() -> None:
        events.append("cleanup")
        raise OSError("cannot remove operation residue")

    runtime = TransactionRuntime(
        operation="deploy",
        stages=(
            TransactionStage("staging", stage),
            TransactionStage("backup", backup),
            TransactionStage("stop", stop),
            TransactionStage("migration", migrate),
            TransactionStage("selection", lambda: True),
            TransactionStage("start", lambda: True),
            TransactionStage("verification", lambda: False),
            TransactionStage("records", lambda: True),
        ),
        acquire_lock=lock,
        discover=discover,
    )
    runtime.register_cleanup("/opt/taskman/deployments/uploads/.upload", cleanup)

    result = runtime.run()

    assert events == ["lock", "discover", "stage", "backup", "stop", "migrate", "cleanup", "unlock"]
    assert result.outcome == "failed"
    assert result.stage == "migration"
    assert result.changed_stages == ("staging", "backup", "stop")
    assert result.lifecycle == {
        "current_release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    }
    assert result.residue_paths == ("/opt/taskman/deployments/uploads/.upload",)
    assert result.recovery_actions == (
        "inspect the selected release and database before retrying",
    )
    assert result.warnings == ("unable to remove operation residue",)


def test_runtime_refuses_out_of_order_stage_before_mutation() -> None:
    """A transaction cannot claim selection before its required predecessor stages.

    Permitting a caller to run an explicit selection stage before a backup is
    a safety bug; the runtime must reject the declaration before callbacks.
    """

    with pytest.raises(ValueError, match="stage order"):
        TransactionRuntime(
            operation="deploy",
            stages=(TransactionStage("selection", lambda: True),),
            acquire_lock=lambda: lambda: None,
            discover=lambda: {},
        )


def test_runtime_rejects_a_monotonic_but_incomplete_deploy_program() -> None:
    """Missing backup or stop stages cannot be smuggled through monotonic names.

    Replacing exact allowed programs with a sorted-name check would permit a
    deploy to migrate after staging without its required safety boundaries.
    """

    with pytest.raises(ValueError, match="stage order"):
        TransactionRuntime(
            operation="deploy",
            stages=(
                TransactionStage("staging", lambda: True),
                TransactionStage("migration", lambda: True),
            ),
            acquire_lock=lambda: lambda: None,
            discover=lambda: {},
        )


@pytest.mark.parametrize(
    ("operation", "stages"),
    [
        ("deploy", (TransactionStage("staging", lambda: False),)),
        ("genesis", (TransactionStage("staging", lambda: False),)),
        (
            "deploy",
            (
                TransactionStage("selection", lambda: False),
                TransactionStage("start", lambda: False),
                TransactionStage("verification", lambda: False),
                TransactionStage("records", lambda: False),
            ),
        ),
    ],
)
def test_runtime_rejects_shortened_deploy_and_genesis_programs(
    operation: str, stages: tuple[TransactionStage, ...]
) -> None:
    """No-op and resume must keep every explicit safety callback in the program.

    Restoring either old short form would let an operation bypass its required
    backup/migration callbacks merely because those callbacks happen to return
    no-change evidence at runtime.
    """

    with pytest.raises(ValueError, match="stage order"):
        TransactionRuntime(
            operation=operation,
            stages=stages,
            acquire_lock=lambda: lambda: None,
            discover=lambda: {},
        )


def test_runtime_preserves_lifecycle_lock_holder_and_recovery_evidence() -> None:
    """Lock contention is not an internal transaction failure.

    Catching ``LifecycleLockContention`` in the generic runtime path would
    discard its holder and leave the operator without a safe wait instruction.
    """

    holder = LifecycleLockHolder("deploy", 4242, "2026-09-06T12:00:00Z", "exclusive")
    runtime = TransactionRuntime(
        operation="cleanup",
        stages=(TransactionStage("cleanup", lambda: True),),
        acquire_lock=lambda: (_ for _ in ()).throw(LifecycleLockContention(holder)),
        discover=lambda: pytest.fail("discovery ran without the lifecycle lock"),
    )

    result = runtime.run()

    assert result.outcome == "failed"
    assert result.stage == "lifecycle-lock"
    assert result.runtime_state == {"lock_holder": holder.to_mapping()}
    assert result.recovery_actions == (
        "wait for the recorded lifecycle operation to finish and retry",
    )


def test_runtime_reports_unexpected_rediscovery_failure_as_internal_not_lock_contention() -> None:
    """Only ``LifecycleLockContention`` may claim the lock is held.

    Reclassifying a parser or filesystem exception as lock contention hides the
    actual failure class and tells the operator to wait when it must be
    investigated instead.
    """

    runtime = TransactionRuntime(
        operation="cleanup",
        stages=(TransactionStage("cleanup", lambda: True),),
        acquire_lock=lambda: lambda: None,
        discover=lambda: (_ for _ in ()).throw(OSError("injected rediscovery fault")),
    )

    result = runtime.run()

    assert result.outcome == "failed"
    assert result.stage == "internal"
    assert result.runtime_state == {"failure": "helper transaction failed"}
    assert result.recovery_actions == ()


def test_runtime_deduplicates_cleanup_warnings_without_collapsing_residue_paths() -> None:
    """Each failed cleanup path remains actionable while its redacted warning is stable.

    Returning duplicate warnings violates the strict result codec and used to
    make a real helper entrypoint replace useful failure evidence with internal.
    """

    runtime = TransactionRuntime(
        operation="cleanup",
        stages=(TransactionStage("cleanup", lambda: True),),
        acquire_lock=lambda: lambda: None,
        discover=lambda: {},
    )
    runtime.register_cleanup("/opt/taskman/deployments/.stage-a", lambda: (_ for _ in ()).throw(OSError()))
    runtime.register_cleanup("/opt/taskman/deployments/.stage-b", lambda: (_ for _ in ()).throw(OSError()))

    result = runtime.run()

    assert result.outcome == "succeeded"
    assert result.residue_paths == (
        "/opt/taskman/deployments/.stage-b",
        "/opt/taskman/deployments/.stage-a",
    )
    assert result.warnings == ("unable to remove operation residue",)


def test_runtime_returns_structured_deadline_evidence_before_a_stage_starts() -> None:
    """An expired helper budget does not become a runner-side lost transaction."""

    events: list[str] = []
    runtime = TransactionRuntime(
        operation="cleanup",
        stages=(TransactionStage("cleanup", lambda: events.append("stage") or True),),
        acquire_lock=lambda: events.append("lock") or (lambda: events.append("unlock")),
        discover=lambda: events.append("discover") or {},
        deadline=time.monotonic() - 0.001,
    )

    result = runtime.run()

    assert result.outcome == "failed"
    assert result.stage == "operation-deadline"
    assert result.changed_stages == ()
    assert events == ["lock", "unlock"]
    assert result.recovery_actions == ("inspect the recorded lifecycle evidence before retrying",)


def test_runtime_refuses_to_begin_a_stage_without_its_bounded_execution_margin() -> None:
    """A runner timeout must not cut a newly started mutation off mid-stage.

    Removing the remaining-budget gate would run ``cleanup`` despite having
    less than its declared execution and result-encoding budget left.
    """

    events: list[str] = []
    runtime = TransactionRuntime(
        operation="cleanup",
        stages=(TransactionStage("cleanup", lambda: events.append("stage") or True),),
        acquire_lock=lambda: events.append("lock") or (lambda: events.append("unlock")),
        discover=lambda: events.append("discover") or {},
        deadline=time.monotonic() + 0.01,
    )

    result = runtime.run()

    assert result.outcome == "failed"
    assert result.stage == "operation-deadline"
    assert events == ["lock", "discover", "unlock"]


@pytest.mark.parametrize("stage", ("stop", "start"))
def test_runtime_reserves_systemctl_and_observation_budget_before_service_stage(stage: str) -> None:
    """A service transition needs both its command and its state observation.

    Reducing the service-stage budget below sixty seconds would begin a
    systemctl action with insufficient time to observe its durable outcome.
    """

    prefix = (
        TransactionStage("staging", lambda: False),
        TransactionStage("backup", lambda: False),
    )

    def must_not_run() -> bool:
        raise AssertionError("must not run")

    if stage == "stop":
        stages = (
            *prefix,
            TransactionStage("stop", must_not_run),
            TransactionStage("migration", lambda: False),
            TransactionStage("selection", lambda: False),
            TransactionStage("start", lambda: False),
            TransactionStage("verification", lambda: False),
            TransactionStage("records", lambda: False),
        )
        operation = "deploy"
    else:
        stages = (
            *prefix,
            TransactionStage("migration", lambda: False),
            TransactionStage("selection", lambda: False),
            TransactionStage("start", must_not_run),
            TransactionStage("verification", lambda: False),
            TransactionStage("records", lambda: False),
        )
        operation = "genesis"
    runtime = TransactionRuntime(
        operation=operation,
        stages=stages,
        acquire_lock=lambda: lambda: None,
        discover=lambda: {},
        # All prefix callbacks are immediate; this leaves less than the
        # required 60-second service budget plus 30-second evidence margin.
        deadline=time.monotonic() + 89.0,
        stage_budgets={
            "staging": 0.001,
            "backup": 0.001,
            "migration": 0.001,
            "selection": 0.001,
            "verification": 0.001,
            "records": 0.001,
        },
    )

    result = runtime.run()

    assert result.outcome == "failed"
    assert result.stage == "operation-deadline"
    assert stage not in result.changed_stages
