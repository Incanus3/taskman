from __future__ import annotations

from multiprocessing import Event, Process
import json
import os
from pathlib import Path

import pytest

from taskman_ops.errors import ExitStatus, OpsError
import taskman_ops.releases.locking as locking
from taskman_ops.releases.locking import LifecycleLock, lifecycle_lock


def _hold_exclusive_lock(path: str, ready: Event, release: Event) -> None:
    with lifecycle_lock(Path(path), operation="deploy", exclusive=True, timeout_seconds=1, owner_uid=os.getuid()):
        ready.set()
        assert release.wait(timeout=5)


def _hold_shared_lock(path: str, ready: Event, release: Event) -> None:
    with lifecycle_lock(Path(path), operation="releases", exclusive=False, timeout_seconds=1, owner_uid=os.getuid()):
        ready.set()
        assert release.wait(timeout=5)


def test_shared_locks_coexist_but_exclusive_lock_refuses_with_holder_metadata(tmp_path: Path) -> None:
    lock_path = tmp_path / "lifecycle.lock"
    ready = Event()
    release = Event()
    contender = Process(target=_hold_exclusive_lock, args=(str(lock_path), ready, release))
    contender.start()
    assert ready.wait(timeout=5)

    with pytest.raises(OpsError) as raised:
        with lifecycle_lock(lock_path, operation="releases", exclusive=False, timeout_seconds=0, owner_uid=os.getuid()):
            pass

    release.set()
    contender.join(timeout=5)
    assert contender.exitcode == 0
    assert raised.value.status is ExitStatus.LOCKED
    assert "deploy" in raised.value.message
    assert "pid" in raised.value.message
    assert lock_path.stat().st_mode & 0o777 == 0o600
    assert lock_path.stat().st_uid == os.getuid()


def test_exclusive_lock_reports_a_shared_holder_with_complete_metadata(tmp_path: Path) -> None:
    lock_path = tmp_path / "lifecycle.lock"
    ready = Event()
    release = Event()
    holder = Process(target=_hold_shared_lock, args=(str(lock_path), ready, release))
    holder.start()
    assert ready.wait(timeout=5)

    with pytest.raises(OpsError) as raised:
        lifecycle_lock(lock_path, operation="adopt", exclusive=True, timeout_seconds=0, owner_uid=os.getuid())

    release.set()
    holder.join(timeout=5)
    assert holder.exitcode == 0
    assert raised.value.status is ExitStatus.LOCKED
    assert "releases" in raised.value.message
    assert "shared" in raised.value.message
    assert "pid" in raised.value.message


def test_shared_locks_coexist_and_exclusive_metadata_is_removed_on_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "lifecycle.lock"

    with lifecycle_lock(lock_path, operation="releases", exclusive=False, timeout_seconds=0, owner_uid=os.getuid()) as first:
        with lifecycle_lock(lock_path, operation="backups", exclusive=False, timeout_seconds=0, owner_uid=os.getuid()) as second:
            assert isinstance(first, LifecycleLock)
            assert isinstance(second, LifecycleLock)
            assert first.exclusive is False
            assert second.exclusive is False

    with lifecycle_lock(lock_path, operation="deploy", exclusive=True, timeout_seconds=0, owner_uid=os.getuid()) as exclusive:
        assert exclusive.operation == "deploy"
        assert exclusive.pid == os.getpid()

    assert '"operation"' not in lock_path.read_text(encoding="utf-8")


def test_lock_refuses_an_existing_file_with_unsafe_mode_instead_of_repairing_it(tmp_path: Path) -> None:
    lock_path = tmp_path / "lifecycle.lock"
    lock_path.write_text("{}\n", encoding="utf-8")
    lock_path.chmod(0o644)

    with pytest.raises(OpsError) as raised:
        lifecycle_lock(lock_path, operation="deploy", exclusive=True, timeout_seconds=0, owner_uid=os.getuid())

    assert raised.value.status is ExitStatus.SAFETY


def test_holder_metadata_completes_short_kernel_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "lifecycle.lock"
    original_write = locking.os.write

    def short_write(descriptor: int, data: bytes) -> int:
        return original_write(descriptor, data[: max(1, len(data) // 2)])

    monkeypatch.setattr(locking.os, "write", short_write)

    with lifecycle_lock(lock_path, operation="deploy", exclusive=True, timeout_seconds=0, owner_uid=os.getuid()):
        holders = json.loads((tmp_path / "lifecycle.lock.meta").read_text(encoding="utf-8"))["holders"]
        assert holders[0]["operation"] == "deploy"


def test_local_lock_prunes_stale_crash_metadata_before_publishing_its_holder(tmp_path: Path) -> None:
    lock_path = tmp_path / "lifecycle.lock"
    metadata_path = tmp_path / "lifecycle.lock.meta"
    with lifecycle_lock(lock_path, operation="setup", exclusive=True, timeout_seconds=0, owner_uid=os.getuid()):
        pass
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "holders": [
                    {
                        "token": "a" * 32,
                        "operation": "crashed-deploy",
                        "pid": 999_999_999,
                        "started_at": "2026-09-05T12:00:00Z",
                        "mode": "exclusive",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata_path.chmod(0o600)

    with lifecycle_lock(lock_path, operation="deploy", exclusive=True, timeout_seconds=0, owner_uid=os.getuid()):
        holders = json.loads(metadata_path.read_text(encoding="utf-8"))["holders"]
        assert [holder["operation"] for holder in holders] == ["deploy"]

    assert json.loads(metadata_path.read_text(encoding="utf-8"))["holders"] == []
