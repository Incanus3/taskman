"""Eligibility tests for exact immutable cleanup plans."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.releases.cleanup import CleanupTarget, RecoveryAuthority, StagingAuthority, cleanup_plan
from taskman_ops.releases.records import ActivationRecord, BackupRecord, LifecycleRecords, ReleaseRecord


RELEASE_A = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_B = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_C = "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6"
FIRST = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
SECOND = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
THIRD = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _records() -> LifecycleRecords:
    return LifecycleRecords(
        releases=(
            ReleaseRecord(1, RELEASE_A, "a" * 64, FIRST, FIRST, None, None, "no-change"),
            ReleaseRecord(1, RELEASE_B, "b" * 64, SECOND, SECOND, RELEASE_A, None, "restore-required"),
            ReleaseRecord(1, RELEASE_C, "c" * 64, THIRD, THIRD, RELEASE_B, None, "no-change"),
        ),
        activations=(
            ActivationRecord(1, "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_A, FIRST, None, "no-change"),
            ActivationRecord(1, "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_A, RELEASE_B, SECOND, None, "restore-required"),
            ActivationRecord(1, "activation-cccccccccccccccccccccccccccccccc", RELEASE_B, RELEASE_C, THIRD, None, "no-change"),
        ),
        backups=(
            BackupRecord(1, "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", FIRST, 100, 100, "taskman_prod", RELEASE_A, RELEASE_B, "pre-deploy", True, PurePosixPath("/var/backups/taskman/a.dump")),
            BackupRecord(1, "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", SECOND, 100, 100, "taskman_prod", RELEASE_B, RELEASE_C, "pre-deploy", True, PurePosixPath("/var/backups/taskman/b.dump")),
            BackupRecord(1, "backup-cccccccccccccccccccccccccccccccc", THIRD, 100, 100, "taskman_prod", RELEASE_C, None, "scheduled", True, PurePosixPath("/var/backups/taskman/c.dump")),
        ),
        adoptions=(),
        warnings=(),
    )


def test_cleanup_plan_retains_current_previous_rollback_and_newest_predeploy_authority() -> None:
    """Retention counts must never let a release/backup destroy the only recovery edge."""

    plan = cleanup_plan(
        _records(),
        release_root=PurePosixPath("/opt/taskman/releases"),
        deployment_root=PurePosixPath("/opt/taskman/deployments"),
        backup_root=PurePosixPath("/var/backups/taskman"),
        release_retention=1,
        backup_retention=1,
        completed_staging=(),
        retained_databases=(),
    )

    assert plan.targets == ()
    assert plan.protected_release_ids == (RELEASE_A, RELEASE_B, RELEASE_C)
    assert plan.protected_backup_ids == (
        "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "backup-cccccccccccccccccccccccccccccccc",
    )


def test_cleanup_plan_includes_only_explicitly_recorded_completed_staging_and_retained_database_ids() -> None:
    """Extra deletion types need their own typed authority, never a directory listing guess."""

    staging = CleanupTarget(
        "staging",
        "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        PurePosixPath("/opt/taskman/deployments/uploads/stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        False,
        StagingAuthority(
            PurePosixPath("/opt/taskman/deployments/uploads/stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            "completed",
        ),
    )
    retained_database = CleanupTarget(
        "database",
        "recovery-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        PurePosixPath("/database/recovery-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        True,
        RecoveryAuthority(
            PurePosixPath("/opt/taskman/deployments/restores/recovery-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.json"),
            "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            RELEASE_A,
            "taskman_recovery_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "retained",
        ),
    )

    plan = cleanup_plan(
        _records(),
        release_root=PurePosixPath("/opt/taskman/releases"),
        deployment_root=PurePosixPath("/opt/taskman/deployments"),
        backup_root=PurePosixPath("/var/backups/taskman"),
        release_retention=1,
        backup_retention=1,
        completed_staging=(staging,),
        retained_databases=(retained_database,),
    )

    assert plan.targets == (staging, retained_database)
    assert plan.targets[-1].recoverable is True


def test_cleanup_plan_retains_restore_record_backups_and_release_before_deleting_its_database() -> None:
    """Removing a retained database must not also remove every artifact needed to recreate it."""

    from taskman_ops.releases.cleanup import RecoveryAuthority

    recovery = CleanupTarget(
        "database",
        "recovery-dddddddddddddddddddddddddddddddd",
        PurePosixPath("/database/recovery-dddddddddddddddddddddddddddddddd"),
        True,
        RecoveryAuthority(
            record_path=PurePosixPath("/opt/taskman/deployments/restores/recovery-dddddddddddddddddddddddddddddddd.json"),
            source_backup_id="backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            pre_restore_backup_id="backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            intended_release_id=RELEASE_A,
            recovery_database="taskman_recovery_dddddddddddddddddddddddddddddddd",
            state="retained",
        ),
    )

    plan = cleanup_plan(
        _records(),
        release_root=PurePosixPath("/opt/taskman/releases"),
        deployment_root=PurePosixPath("/opt/taskman/deployments"),
        backup_root=PurePosixPath("/var/backups/taskman"),
        release_retention=1,
        backup_retention=1,
        completed_staging=(),
        retained_databases=(recovery,),
    )

    assert recovery in plan.targets
    assert set(plan.protected_backup_ids) >= {
        "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }
    assert RELEASE_A in plan.protected_release_ids


@pytest.mark.parametrize(
    "target_factory",
    [
        lambda: CleanupTarget("release", RELEASE_A, PurePosixPath("/tmp/escape"), False),
        lambda: CleanupTarget("staging", "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", PurePosixPath("/opt/taskman/deployments/uploads/*"), False),
        lambda: CleanupTarget("backup", "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", PurePosixPath("/var/backups/taskman/a.dump"), False),
    ],
)
def test_cleanup_plan_refuses_escape_wildcard_and_protected_targets(target_factory: object) -> None:
    """Cleanup is exact-target only; a valid-looking identifier is insufficient authority."""

    if not callable(target_factory):
        pytest.fail("test target factory is invalid")
    try:
        target = target_factory()
    except ValueError:
        return
    with pytest.raises(OpsError) as raised:
        cleanup_plan(
            _records(),
            release_root=PurePosixPath("/opt/taskman/releases"),
            deployment_root=PurePosixPath("/opt/taskman/deployments"),
            backup_root=PurePosixPath("/var/backups/taskman"),
            release_retention=1,
            backup_retention=1,
            completed_staging=(target,),
            retained_databases=(),
        )
    assert raised.value.status is ExitStatus.SAFETY


def test_cleanup_plan_uses_the_exact_configured_deployment_root_for_staging_authority() -> None:
    """A valid custom root must not be replaced by a default sibling guess."""

    staging = CleanupTarget(
        "staging",
        "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        PurePosixPath("/srv/taskman/control/uploads/stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        False,
        StagingAuthority(
            PurePosixPath("/srv/taskman/control/uploads/stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            "completed",
        ),
    )

    plan = cleanup_plan(
        _records(),
        release_root=PurePosixPath("/srv/taskman/artifacts"),
        deployment_root=PurePosixPath("/srv/taskman/control"),
        backup_root=PurePosixPath("/srv/taskman-backups"),
        release_retention=1,
        backup_retention=1,
        completed_staging=(staging,),
        retained_databases=(),
    )

    assert staging in plan.targets
