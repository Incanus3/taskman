"""Observed-state rollback convergence contracts."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import subprocess

import pytest

from tests.host_helper.support import database_mapping, managed_paths, verification_settings

from taskman_ops.host_helper.operations import rollback as rollback_module
from taskman_ops.host_helper import services as service_capability
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.releases.identifiers import build_release_id


CORRELATION = "op-0123456789abcdef0123456789abcdef"
TARGET_REVISION = "a" * 40
CURRENT_REVISION = "b" * 40
TARGET = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp29.0.6"
MIGRATION = {"filename": "20260905120000_create_tasks.exs", "sha256": "c" * 64}
MIGRATION_VERSION = 20260905120000


def _credentials(tmp_path: Path) -> Path:
    path = tmp_path / "pgpass"
    path.write_text("127.0.0.1:5432:*:taskman:database-password-canary\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _install_release(paths: ManagedPaths, release_id: str, revision: str) -> None:
    path = Path(paths.local(paths.release_root / release_id))
    path.mkdir(parents=True)
    path.chmod(0o750)
    write_release_manifest(paths, ReleaseRecord(release_id, revision, "d" * 64, (MIGRATION,)))


def _seed_history(paths: ManagedPaths, *, include_target_predecessor: bool = True) -> None:
    _install_release(paths, TARGET, TARGET_REVISION)
    _install_release(paths, CURRENT, CURRENT_REVISION)
    if include_target_predecessor:
        append_selection(
            paths,
            SelectionRecord(TARGET, None, None, datetime(2026, 9, 7, 11, 0, tzinfo=UTC)),
        )
        previous = TARGET
        selected_at = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    else:
        previous = None
        selected_at = datetime(2026, 9, 7, 11, 0, tzinfo=UTC)
    append_selection(paths, SelectionRecord(CURRENT, previous, None, selected_at))
    Path(paths.local(paths.current_link)).symlink_to(Path(paths.local(paths.release_root / CURRENT)))


def _request(paths: ManagedPaths, credentials: Path) -> HostRequest:
    return HostRequest(
        2,
        "rollback",
        CORRELATION,
        {"selected_release_id": CURRENT},
        {"install_root": paths.install_root.as_posix(), "backup_root": paths.backup_root.as_posix()},
        {
            "target_release_id": TARGET,
            "credentials_path": credentials.as_posix(),
            "database": database_mapping(),
            "verification": verification_settings(),
        },
    )


class _Runtime:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.events: list[str] = []
        self.fail_start = fail_start
        self.backups = 0

    def observe_database(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"state": "ready", "applied_migrations": (MIGRATION_VERSION,)}

    def backup(self, state: object, paths: ManagedPaths, *_args: object, **_kwargs: object) -> BackupRecord:
        self.events.append("backup")
        self.backups += 1
        backup_id = f"backup-{self.backups:032x}"
        dump = Path(paths.local(paths.backup_root / f"{backup_id}.dump"))
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_bytes(b"fresh rollback safety backup")
        dump.chmod(0o600)
        record = BackupRecord(
            backup_id,
            datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            hashlib.sha256(dump.read_bytes()).hexdigest(),
            state.selected_release_id,
            state.applied_migrations,
            1024,
        )
        write_backup_manifest(paths, record)
        return record

    def command(self, argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[:2] == ("systemctl", "stop"):
            self.events.append("stop")
        elif argv[:2] == ("systemctl", "start"):
            self.events.append("start")
            if self.fail_start:
                raise rollback_module.CommandError("start interrupted")
        elif argv[0] == "systemd-run":
            pytest.fail("rollback must never execute reverse migrations")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def verify(self, request: HostRequest, **_kwargs: object) -> HostResult:
        self.events.append("verify")
        return HostResult(2, "verify", request.correlation_id, "succeeded", "verified", {"report": {"ok": True}}, ())


def _install_runtime(monkeypatch: pytest.MonkeyPatch, runtime: _Runtime) -> None:
    monkeypatch.setattr(rollback_module, "observe_database_state", runtime.observe_database)
    monkeypatch.setattr(rollback_module, "create_validated_backup", runtime.backup, raising=False)
    monkeypatch.setattr(service_capability, "run_command", runtime.command)
    monkeypatch.setattr(rollback_module, "verify", runtime.verify, raising=False)


def test_rollback_creates_a_safety_backup_before_selecting_a_history_compatible_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping the fresh backup or history proof would make a rollback unsafe."""

    paths = managed_paths(tmp_path)
    _seed_history(paths)
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = rollback_module.rollback(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == TARGET
    assert result.state["target_release_id"] == TARGET
    assert result.state["database_state"] == "unchanged"
    assert str(result.state["backup_id"]).startswith("backup-")
    assert runtime.events == ["backup", "stop", "start", "verify"]
    assert Path(paths.local(paths.current_link)).resolve().name == TARGET


def test_rollback_rerun_completes_after_selection_when_start_loses_its_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selected target without its completed selection record must be replayable."""

    paths = managed_paths(tmp_path)
    _seed_history(paths)
    historical_id = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    historical_dump = Path(paths.local(paths.backup_root / f"{historical_id}.dump"))
    historical_dump.write_bytes(b"ordinary historical backup")
    historical_dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(
            historical_id,
            datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            hashlib.sha256(historical_dump.read_bytes()).hexdigest(),
            CURRENT,
            (MIGRATION_VERSION,),
            1024,
        ),
    )
    runtime = _Runtime(fail_start=True)
    _install_runtime(monkeypatch, runtime)
    request = _request(paths, _credentials(tmp_path))

    interrupted = rollback_module.rollback(request)

    assert interrupted.outcome == "retryable"
    assert Path(paths.local(paths.current_link)).resolve().name == TARGET
    runtime.fail_start = False
    completed = rollback_module.rollback(request)

    assert completed.outcome == "succeeded"
    assert completed.state["selected_release_id"] == TARGET
    assert completed.state["backup_id"] == "backup-00000000000000000000000000000002"
    assert runtime.events.count("backup") == 2
    assert runtime.events[-2:] == ["start", "verify"]


def test_rollback_refuses_a_target_not_proven_by_successful_selection_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An arbitrary installed release must not be treated as rollback-compatible."""

    paths = managed_paths(tmp_path)
    _seed_history(paths, include_target_predecessor=False)
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = rollback_module.rollback(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "refused"
    assert runtime.events == []
