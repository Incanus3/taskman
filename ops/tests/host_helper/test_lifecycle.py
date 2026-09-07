"""Retained lifecycle safety-floor tests during the completed-state migration."""

from __future__ import annotations

from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import time

import pytest

from taskman_ops.host_helper.lifecycle import (
    ActivationRecord,
    LifecycleError,
    LifecycleLockContention,
    LifecycleRecords,
    LifecycleStore,
    ReleaseRecord,
    rollback_eligibility,
)
from taskman_ops.host_helper.paths import ManagedPaths


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths.from_mapping(
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}
    )


def _release() -> ReleaseRecord:
    return ReleaseRecord.from_mapping(
        {
            "schema_version": 1,
            "release_id": RELEASE_ID,
            "artifact_sha256": "a" * 64,
            "installed_at": "2026-09-05T12:00:00Z",
            "activated_at": None,
            "previous_release_id": None,
            "backup_id": None,
            "migration_policy": "no-change",
        }
    )


def test_lifecycle_keeps_first_release_activation_immutable_across_a_later_rollback_event(
    tmp_path: Path,
) -> None:
    """Rollback history does not rewrite the original release provenance."""

    release_a = RELEASE_ID
    release_b = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
    first = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    second = datetime(2026, 9, 5, 12, 1, tzinfo=UTC)
    rollback_time = datetime(2026, 9, 5, 12, 2, tzinfo=UTC)
    records = LifecycleRecords(
        (
            ReleaseRecord(1, release_a, "a" * 64, first, first, None, None, "no-change"),
            ReleaseRecord(1, release_b, "b" * 64, second, second, release_a, None, "no-change"),
        ),
        (
            ActivationRecord(1, "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, release_a, first, None, "no-change"),
            ActivationRecord(1, "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", release_a, release_b, second, None, "no-change"),
            ActivationRecord(1, "activation-cccccccccccccccccccccccccccccccc", release_b, release_a, rollback_time, None, "no-change"),
        ),
        (),
        (),
        (),
    )

    from taskman_ops.host_helper.lifecycle import validate_lifecycle_records

    validate_lifecycle_records(records, _paths(tmp_path))
    assert records.current_release_id == release_a
    assert records.releases[0].activated_at == first


def test_store_writes_private_completed_records_without_replacement(tmp_path: Path) -> None:
    """A completed record is published once and cannot be overwritten by retry."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    store.write_release(_release())

    record_path = tmp_path / "install" / "deployments" / "releases" / f"release-{RELEASE_ID}.json"
    assert record_path.exists()
    assert record_path.stat().st_mode & 0o777 == 0o600
    assert store.read().releases[0].release_id == RELEASE_ID

    with pytest.raises(LifecycleError, match="already exists"):
        store.write_release(_release())


@pytest.mark.parametrize("mode", (0o777, 0o7000))
def test_store_refuses_unsafe_record_storage_before_reading(
    tmp_path: Path, mode: int
) -> None:
    """Group-writable and special-bit roots cannot become lifecycle authority."""

    root = tmp_path / "install" / "deployments" / "releases"
    root.mkdir(parents=True)
    root.chmod(mode)

    try:
        with pytest.raises(LifecycleError, match="directory"):
            LifecycleStore(_paths(tmp_path), owner_uid=os.getuid()).read()
    finally:
        root.chmod(0o700)


def test_store_rejects_a_current_link_without_an_activation_record(tmp_path: Path) -> None:
    """A selected path is not lifecycle authority without completed history."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    release = tmp_path / "install" / "releases" / RELEASE_ID
    release.mkdir(parents=True)
    release.parent.chmod(0o750)
    release.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(release)

    with pytest.raises(LifecycleError, match="without an activation"):
        store.read()


def test_snapshot_lock_fails_by_deadline_when_a_writer_holds_the_lock(tmp_path: Path) -> None:
    """Read-only helper calls do not wait beyond their bounded lock deadline."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    lock_root = tmp_path / ".taskman-lock"
    lock_root.mkdir()
    lock_root.chmod(0o750)
    descriptor = os.open(lock_root / "lifecycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        started = time.monotonic()
        context = store.shared_snapshot_lock(deadline=started + 0.02)
        with pytest.raises(LifecycleLockContention):
            context.__enter__()
        assert time.monotonic() - started < 0.5
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _activation(
    activation_id: str,
    previous_release_id: str | None,
    candidate_release_id: str,
    *,
    migration_policy: str = "no-change",
    hour: int,
) -> ActivationRecord:
    return ActivationRecord.from_mapping(
        {
            "schema_version": 1,
            "activation_id": activation_id,
            "previous_release_id": previous_release_id,
            "candidate_release_id": candidate_release_id,
            "activated_at": f"2026-09-05T{hour:02d}:00:00Z",
            "backup_id": None,
            "migration_policy": migration_policy,
        }
    )


@pytest.mark.parametrize(
    ("activations", "current", "target", "eligible", "reason"),
    (
        (
            (_activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, hour=12), _activation("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_ID, "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6", hour=13)),
            "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
            RELEASE_ID,
            True,
            None,
        ),
        (
            (_activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, hour=12),),
            RELEASE_ID,
            "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6",
            False,
            "target release is not connected to the current activation chain",
        ),
        (
            (_activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, hour=12), _activation("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_ID, "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6", migration_policy="restore-required", hour=13)),
            "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
            RELEASE_ID,
            False,
            "activation activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb requires database restore",
        ),
        (
            (_activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, hour=12), _activation("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6", "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6", hour=13)),
            "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
            RELEASE_ID,
            False,
            "activation chain is incomplete",
        ),
    ),
)
def test_rollback_eligibility_reports_connected_and_refusal_chain_states(
    activations: tuple[ActivationRecord, ...],
    current: str,
    target: str,
    eligible: bool,
    reason: str | None,
) -> None:
    """Rollback facts distinguish connected and unsafe history."""

    records = LifecycleRecords((), activations, (), (), ())

    assert rollback_eligibility(records, current, target) == (eligible, reason)
