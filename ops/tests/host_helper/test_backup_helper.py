"""Contracts for scheduled-backup executable convergence."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from threading import Event, Thread

import pytest

from taskman_ops.host_helper import backup_helper
from taskman_ops.host_helper.lock import acquire_lifecycle_lock
from taskman_ops.host_helper.paths import ManagedPaths


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths.from_mapping(
        {"install_root": (tmp_path / "install").as_posix(), "backup_root": (tmp_path / "backups").as_posix()}
    )


def test_enabled_inactive_timer_is_restarted_without_replacing_matching_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing inactive-timer repair would leave confirmed scheduled backups disabled in practice."""

    paths = _paths(tmp_path)
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"matching executable")
    executable.chmod(0o750)
    expected = hashlib.sha256(executable.read_bytes()).hexdigest()
    calls: list[str] = []
    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (True, "inactive"))
    monkeypatch.setattr(backup_helper, "start_backup_timer", lambda **_kwargs: calls.append("start"))

    result = backup_helper.converge_backup_helper(
        paths,
        {"sha256": expected, "upload_path": None},
        confirmed_checksum=expected,
        confirmed_enabled=True,
        revalidate=lambda: calls.append("revalidate"),
        timeout_seconds=1,
    )

    assert result.mutation == backup_helper.BackupHelperMutation(restarted=True)
    assert calls == ["start"]
    assert result.lock.held
    result.lock.release()


def test_disabled_active_timer_refuses_before_a_package_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Accepting an active disabled timer could restart an obsolete executable after replacement."""

    paths = _paths(tmp_path)
    upload = tmp_path / "install" / "deployments" / "uploads" / "upload.pyz"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"replacement executable")
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"old executable")
    executable.chmod(0o750)
    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (False, "active"))

    with pytest.raises(backup_helper.BackupHelperError, match="active while disabled"):
        backup_helper.converge_backup_helper(
            paths,
            {"sha256": hashlib.sha256(upload.read_bytes()).hexdigest(), "upload_path": upload.as_posix()},
            confirmed_checksum=hashlib.sha256(executable.read_bytes()).hexdigest(),
            confirmed_enabled=False,
            revalidate=lambda: None,
            timeout_seconds=1,
        )

    assert executable.read_bytes() == b"old executable"


def test_disabled_inactive_timer_stays_inactive_when_the_executable_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disabled schedule must not be enabled or started by package observation."""

    paths = _paths(tmp_path)
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"matching executable")
    executable.chmod(0o750)
    expected = hashlib.sha256(executable.read_bytes()).hexdigest()
    calls: list[str] = []
    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (False, "inactive"))
    monkeypatch.setattr(backup_helper, "start_backup_timer", lambda **_kwargs: calls.append("start"))

    result = backup_helper.converge_backup_helper(
        paths,
        {"sha256": expected, "upload_path": None},
        confirmed_checksum=expected,
        confirmed_enabled=False,
        revalidate=lambda: calls.append("revalidate"),
        timeout_seconds=1,
    )

    assert result.mutation == backup_helper.BackupHelperMutation()
    assert calls == []
    result.lock.release()


def test_changed_executable_identity_refuses_before_stopping_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipping the confirmed identity check could replace a package after host drift."""

    paths = _paths(tmp_path)
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"drifted executable")
    executable.chmod(0o750)
    calls: list[str] = []
    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (True, "active"))
    monkeypatch.setattr(backup_helper, "stop_backup_timer", lambda **_kwargs: calls.append("stop"))

    with pytest.raises(backup_helper.BackupHelperError, match="identity changed"):
        backup_helper.converge_backup_helper(
            paths,
            {"sha256": "a" * 64, "upload_path": None},
            confirmed_checksum="b" * 64,
            confirmed_enabled=True,
            revalidate=lambda: None,
            timeout_seconds=1,
        )

    assert calls == []


def test_refresh_releases_the_lock_for_an_old_backup_before_revalidating_and_replacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holding the lifecycle lock while waiting would deadlock an already-started scheduled backup."""

    paths = _paths(tmp_path)
    upload = tmp_path / "install" / "deployments" / "uploads" / "replacement.pyz"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"replacement")
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"earlier")
    executable.chmod(0o750)
    expected = hashlib.sha256(upload.read_bytes()).hexdigest()
    first_released = Event()
    old_waiting = Event()
    old_complete = Event()
    calls: list[str] = []

    initial = acquire_lifecycle_lock(paths, 1)
    original_acquire = acquire_lifecycle_lock

    def old_backup() -> None:
        old_waiting.set()
        with original_acquire(paths, 1):
            calls.append("old-backup-complete")
            old_complete.set()

    worker = Thread(target=old_backup)
    worker.start()
    assert old_waiting.wait(1)
    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (True, "active"))
    monkeypatch.setattr(backup_helper, "stop_backup_timer", lambda **_kwargs: calls.append("stop-timer"))
    monkeypatch.setattr(backup_helper, "_wait_for_backup_service", lambda _timeout: old_complete.wait(1))
    monkeypatch.setattr(backup_helper, "_replace_executable", lambda _upload, _digest: calls.append("replace-and-verify"))
    monkeypatch.setattr(backup_helper, "start_backup_timer", lambda **_kwargs: calls.append("start-timer"))

    result = backup_helper.converge_backup_helper(
        paths,
        {"sha256": expected, "upload_path": upload.as_posix()},
        confirmed_checksum=hashlib.sha256(executable.read_bytes()).hexdigest(),
        confirmed_enabled=True,
        revalidate=lambda: calls.append("revalidate"),
        timeout_seconds=1,
        lock=initial,
    )
    worker.join(1)

    assert result.mutation == backup_helper.BackupHelperMutation(paused=True, replaced=True, restarted=True)
    assert calls == [
        "stop-timer",
        "old-backup-complete",
        "revalidate",
        "replace-and-verify",
        "start-timer",
    ]
    assert result.lock is initial
    result.lock.release()


def test_refresh_uses_one_finite_deadline_for_timer_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixed sixty-second timer call could exceed the confirmed refresh deadline."""

    paths = _paths(tmp_path)
    upload = tmp_path / "install" / "deployments" / "uploads" / "replacement.pyz"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"replacement")
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"earlier")
    executable.chmod(0o750)
    expected = hashlib.sha256(upload.read_bytes()).hexdigest()
    observed_timeouts: list[float] = []

    def command(argv: tuple[str, ...], *, timeout_seconds: float, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_timeouts.append(timeout_seconds)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "run_command", command)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (True, "active"))
    monkeypatch.setattr(backup_helper, "_wait_for_backup_service", lambda _timeout: None)

    result = backup_helper.converge_backup_helper(
        paths,
        {"sha256": expected, "upload_path": upload.as_posix()},
        confirmed_checksum=hashlib.sha256(executable.read_bytes()).hexdigest(),
        confirmed_enabled=True,
        revalidate=lambda: None,
        timeout_seconds=1,
    )

    assert observed_timeouts
    assert all(timeout <= 1 for timeout in observed_timeouts)
    result.lock.release()


def test_reacquired_authority_drift_restores_enabled_timer_without_replacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing after lock-release drift would publish a package outside the confirmed plan."""

    paths = _paths(tmp_path)
    upload = tmp_path / "install" / "deployments" / "uploads" / "replacement.pyz"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"replacement")
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"earlier")
    executable.chmod(0o750)
    calls: list[str] = []
    checks = iter(("revalidate",))

    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (True, "active"))
    monkeypatch.setattr(backup_helper, "stop_backup_timer", lambda **_kwargs: calls.append("stop"))
    monkeypatch.setattr(backup_helper, "start_backup_timer", lambda **_kwargs: calls.append("start"))
    monkeypatch.setattr(backup_helper, "_wait_for_backup_service", lambda _timeout: calls.append("wait"))
    monkeypatch.setattr(backup_helper, "_replace_executable", lambda *_args: calls.append("replace"))

    def revalidate() -> None:
        calls.append(next(checks))
        raise ValueError("authority drift")

    with pytest.raises(backup_helper.BackupHelperError) as raised:
        backup_helper.converge_backup_helper(
            paths,
            {"sha256": hashlib.sha256(upload.read_bytes()).hexdigest(), "upload_path": upload.as_posix()},
            confirmed_checksum=hashlib.sha256(executable.read_bytes()).hexdigest(),
            confirmed_enabled=True,
            revalidate=revalidate,
            timeout_seconds=1,
        )

    assert raised.value.mutation == backup_helper.BackupHelperMutation(paused=True, restarted=True)
    assert calls == ["stop", "wait", "revalidate", "start"]
    assert executable.read_bytes() == b"earlier"


def test_interrupted_replacement_reports_a_distinct_timer_restoration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed replacement plus failed restart needs separate recovery evidence."""

    paths = _paths(tmp_path)
    upload = tmp_path / "install" / "deployments" / "uploads" / "replacement.pyz"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"replacement")
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"earlier")
    executable.chmod(0o750)
    desired = hashlib.sha256(upload.read_bytes()).hexdigest()

    def interrupted_replace(_upload: Path | None, _digest: str) -> None:
        executable.write_bytes(b"replacement")
        executable.chmod(0o750)
        raise OSError("interrupted replacement")

    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (True, "active"))
    monkeypatch.setattr(backup_helper, "stop_backup_timer", lambda **_kwargs: None)
    monkeypatch.setattr(backup_helper, "start_backup_timer", lambda **_kwargs: (_ for _ in ()).throw(OSError("restart failed")))
    monkeypatch.setattr(backup_helper, "_wait_for_backup_service", lambda _timeout: None)
    monkeypatch.setattr(backup_helper, "_replace_executable", interrupted_replace)

    with pytest.raises(backup_helper.BackupHelperError) as raised:
        backup_helper.converge_backup_helper(
            paths,
            {"sha256": desired, "upload_path": upload.as_posix()},
            confirmed_checksum=hashlib.sha256(b"earlier").hexdigest(),
            confirmed_enabled=True,
            revalidate=lambda: None,
            timeout_seconds=1,
        )

    assert raised.value.mutation == backup_helper.BackupHelperMutation(paused=True, replaced=True)
    assert raised.value.restoration_failed is True
