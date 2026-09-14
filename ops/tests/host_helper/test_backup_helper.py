"""Contracts for scheduled-backup executable convergence."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from taskman_ops.host_helper import backup_helper
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
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda: (True, "inactive"))
    monkeypatch.setattr(backup_helper, "start_backup_timer", lambda: calls.append("start"))

    result = backup_helper.converge_backup_helper(
        paths,
        {"sha256": expected, "upload_path": None},
        confirmed_checksum=expected,
        confirmed_enabled=True,
        revalidate=lambda: calls.append("revalidate"),
        timeout_seconds=1,
    )

    assert result.mutation == backup_helper.BackupHelperMutation(restarted=True)
    assert calls == ["revalidate", "start"]
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
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda: (False, "active"))

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


def test_changed_executable_identity_refuses_before_stopping_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipping the confirmed identity check could replace a package after host drift."""

    paths = _paths(tmp_path)
    executable = tmp_path / "taskman-backup.pyz"
    executable.write_bytes(b"drifted executable")
    executable.chmod(0o750)
    calls: list[str] = []
    monkeypatch.setattr(backup_helper, "_BACKUP_COMMAND", executable)
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda: (True, "active"))
    monkeypatch.setattr(backup_helper, "stop_backup_timer", lambda: calls.append("stop"))

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
