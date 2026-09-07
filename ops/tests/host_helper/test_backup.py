from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from taskman_ops.host_helper.operations import backup as backup_module
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.host_helper.state import HostState
from taskman_ops.host_protocol import HostRequest


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
PREVIOUS_BACKUP = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths.from_mapping(
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}
    )


def _state() -> HostState:
    return HostState(RELEASE, (), (), (), (1,), "running", "ready", (), ())


def _database() -> dict[str, object]:
    return {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}


def _credentials(tmp_path: Path) -> Path:
    credentials = tmp_path / "pgpass"
    credentials.write_text("127.0.0.1:5432:taskman:taskman:database-password-canary\n", encoding="utf-8")
    credentials.chmod(0o600)
    return credentials


def _command_double(calls: list[tuple[tuple[str, ...], dict[str, object]]]):
    def run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, dict(kwargs)))
        if argv[0] == "psql":
            return subprocess.CompletedProcess(argv, 0, b"1024\n", b"")
        if argv[0] == "pg_dump":
            destination = Path(next(value.removeprefix("--file=") for value in argv if value.startswith("--file=")))
            destination.write_bytes(b"validated custom dump")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    return run


def _publish_selected_release(paths: ManagedPaths) -> None:
    release_path = Path(paths.local(paths.release_root / RELEASE))
    release_path.mkdir(parents=True)
    release_path.chmod(0o750)
    write_release_manifest(paths, ReleaseRecord(RELEASE, "a" * 40, "b" * 64, ()))
    append_selection(
        paths,
        SelectionRecord(RELEASE, None, None, datetime(2026, 9, 7, 12, 0, tzinfo=UTC)),
    )
    Path(paths.local(paths.current_link)).symlink_to(release_path)


def _request(paths: ManagedPaths, credentials: Path) -> HostRequest:
    return HostRequest(
        2,
        "backup",
        "op-0123456789abcdef0123456789abcdef",
        {},
        {"install_root": paths.install_root.as_posix(), "backup_root": paths.backup_root.as_posix()},
        {"credentials_path": credentials.as_posix(), "database": _database(), "purpose": "scheduled"},
    )


def test_create_validated_backup_validates_before_same_root_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing before pg_restore validation would make an invalid dump authoritative."""

    paths = _paths(tmp_path)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    events: list[str] = []
    monkeypatch.setattr(backup_module, "run_command", _command_double(calls))
    replace = os.replace

    def same_root_replace(source: Path | str, destination: Path | str) -> None:
        assert Path(source).parent == Path(destination).parent
        events.append("publish")
        replace(source, destination)

    monkeypatch.setattr(backup_module.os, "replace", same_root_replace)

    record = backup_module.create_validated_backup(
        _state(), paths, _database(), _credentials(tmp_path), purpose="scheduled"
    )

    command_names = [argv[0] for argv, _kwargs in calls]
    assert command_names == ["psql", "pg_dump", "pg_restore"]
    assert events == ["publish"]
    dump = Path(paths.local(paths.backup_root / f"{record.backup_id}.dump"))
    assert dump.read_bytes() == b"validated custom dump"
    assert Path(paths.local(paths.backup_manifest(record.backup_id))).is_file()


def test_create_validated_backup_uses_pgpassfile_without_putting_the_password_in_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding a password argument would expose the database secret in process listings."""

    paths = _paths(tmp_path)
    credentials = _credentials(tmp_path)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(backup_module, "run_command", _command_double(calls))

    backup_module.create_validated_backup(_state(), paths, _database(), credentials, purpose="scheduled")

    pg_dump, kwargs = next((argv, kwargs) for argv, kwargs in calls if argv[0] == "pg_dump")
    assert "database-password-canary" not in " ".join(pg_dump)
    assert kwargs["env"] == {"PGPASSFILE": credentials.as_posix()}


@pytest.mark.parametrize("boundary", ["dump", "validation", "publication", "manifest"])
def test_backup_rerun_finishes_after_each_recognizable_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Leaving any normal interrupted backup shape unrecoverable would require a journal."""

    paths = _paths(tmp_path)
    _publish_selected_release(paths)
    credentials = _credentials(tmp_path)
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(exist_ok=True)
    prior_dump = backup_root / f"{PREVIOUS_BACKUP}.dump"
    if boundary in {"dump", "validation"}:
        prior_dump = backup_root / f".{PREVIOUS_BACKUP}.dump.tmp"
        prior_dump.write_bytes(b"incomplete dump")
    else:
        prior_dump.write_bytes(b"validated custom dump")
    prior_dump.chmod(0o600)
    if boundary == "manifest":
        write_backup_manifest(
            paths,
            BackupRecord(
                PREVIOUS_BACKUP,
                hashlib.sha256(prior_dump.read_bytes()).hexdigest(),
                RELEASE,
                (1,),
                1024,
            ),
        )
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(backup_module, "run_command", _command_double(calls))

    result = backup_module.backup(_request(paths, credentials))

    assert result.outcome == "succeeded"
    assert result.state["backup_id"].startswith("backup-")
    if boundary != "manifest":
        assert not prior_dump.exists()


def test_backup_returns_manual_when_a_completed_dump_identity_is_contradictory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting or replacing a corrupted completed dump would hide an authoritative conflict."""

    paths = _paths(tmp_path)
    _publish_selected_release(paths)
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(exist_ok=True)
    dump = backup_root / f"{PREVIOUS_BACKUP}.dump"
    dump.write_bytes(b"original validated dump")
    dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(
            PREVIOUS_BACKUP,
            hashlib.sha256(dump.read_bytes()).hexdigest(),
            RELEASE,
            (1,),
            1024,
        ),
    )
    dump.write_bytes(b"contradictory replacement")
    monkeypatch.setattr(backup_module, "run_command", lambda *_args, **_kwargs: pytest.fail("must not dump"))

    result = backup_module.backup(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "manual"
    assert result.state == {"selected_release_id": RELEASE}


def test_backup_keeps_a_dump_when_its_manifest_path_is_an_ambiguous_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken sidecar link must not make a completed-looking dump safe to remove."""

    paths = _paths(tmp_path)
    _publish_selected_release(paths)
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(exist_ok=True)
    dump = backup_root / f"{PREVIOUS_BACKUP}.dump"
    dump.write_bytes(b"validated custom dump")
    dump.chmod(0o600)
    (backup_root / f"{PREVIOUS_BACKUP}.json").symlink_to(tmp_path / "missing-manifest")
    monkeypatch.setattr(backup_module, "run_command", lambda *_args, **_kwargs: pytest.fail("must not dump"))

    result = backup_module.backup(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "manual"
    assert dump.exists()
