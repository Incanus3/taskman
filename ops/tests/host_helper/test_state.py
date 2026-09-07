"""Coherent HostState observation tests."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path

import pytest

from taskman_ops.host_helper.lock import LifecycleLockContention, lifecycle_lock
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import (
    BackupRecord,
    RecordError,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.host_helper.state import HostState, StateAmbiguityError, observe_host_state


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
OTHER_RELEASE = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths.from_mapping(
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}
    )


def _release(release_id: str = RELEASE) -> ReleaseRecord:
    source = "b" * 40 if release_id == OTHER_RELEASE else "a" * 40
    return ReleaseRecord(release_id, source, "b" * 64, ())


def _backup() -> BackupRecord:
    return BackupRecord(BACKUP, "c" * 64, RELEASE, (1,), 1024)


def _selection(release_id: str = RELEASE, previous_release_id: str | None = None) -> SelectionRecord:
    return SelectionRecord(release_id, previous_release_id, None, AT)


def _publish_release(paths: ManagedPaths, release: ReleaseRecord) -> None:
    directory = Path(paths.local(paths.release_root / release.release_id))
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o750)
    write_release_manifest(paths, release)


def test_observe_empty_host_returns_one_bounded_state_value(tmp_path: Path) -> None:
    state = observe_host_state(_paths(tmp_path))

    assert isinstance(state, HostState)
    assert state.selected_release_id is None
    assert state.releases == ()
    assert state.backups == ()
    assert state.selections == ()
    assert state.applied_migrations == ()
    assert state.service_state == "unknown"
    assert state.database_state == "unknown"
    assert state.temporary_paths == ()
    assert state.warnings == ()


def test_observe_selected_release_and_database_projection(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    release = _release()
    _publish_release(paths, release)
    append_selection(paths, _selection())
    Path(paths.local(paths.current_link)).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.local(paths.current_link)).symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    state = observe_host_state(
        paths,
        database={"state": "ready", "applied_migrations": (1, 2, 3)},
    )

    assert state.selected_release_id == RELEASE
    assert state.releases == (release,)
    assert state.selections == (_selection(),)
    assert state.applied_migrations == (1, 2, 3)
    assert state.database_state == "ready"
    assert state.service_state == "unknown"


def test_observe_multiple_completed_records_and_validated_backup(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _publish_release(paths, _release())
    _publish_release(paths, _release(OTHER_RELEASE))
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"dump")
    dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(BACKUP, hashlib.sha256(dump.read_bytes()).hexdigest(), RELEASE, (1,), 1024),
    )
    append_selection(paths, _selection())
    append_selection(
        paths,
        SelectionRecord(OTHER_RELEASE, RELEASE, None, AT.replace(minute=1)),
    )
    current = Path(paths.local(paths.current_link))
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path(paths.local(paths.release_root / OTHER_RELEASE)))

    state = observe_host_state(paths)

    assert {item.release_id for item in state.releases} == {RELEASE, OTHER_RELEASE}
    assert state.backups == (
        BackupRecord(BACKUP, hashlib.sha256(dump.read_bytes()).hexdigest(), RELEASE, (1,), 1024),
    )
    assert [item.release_id for item in state.selections] == [RELEASE, OTHER_RELEASE]
    assert state.selected_release_id == OTHER_RELEASE


def test_observe_reports_only_recognizable_temporary_artifacts_and_bounded_unknown_warning(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    install = Path(paths.local(paths.install_root))
    backup_root = Path(paths.local(paths.backup_root))
    (install / "releases").mkdir(parents=True)
    backup_root.mkdir(parents=True)
    expected = (
        install / "releases" / f".{RELEASE}.tmp",
        backup_root / f".{BACKUP}.tmp",
    )
    for path in expected:
        path.write_bytes(b"incomplete")
        path.chmod(0o600)
    (install / "releases" / "operator-notes.txt").write_text("ignore", encoding="utf-8")

    state = observe_host_state(paths)

    assert tuple(Path(item.as_posix()) for item in state.temporary_paths) == tuple(
        Path(item) for item in sorted(path.as_posix() for path in expected)
    )
    assert any("unknown" in warning or "operator-notes" in warning for warning in state.warnings)


def test_observe_refuses_authoritative_symlink_and_duplicate_identity(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    install = Path(paths.local(paths.install_root))
    target = tmp_path / "outside"
    target.mkdir()
    install.mkdir()
    (install / "releases").symlink_to(target, target_is_directory=True)
    with pytest.raises(StateAmbiguityError, match="unsafe|authoritative|symlink"):
        observe_host_state(paths)

    paths = _paths(tmp_path / "duplicates")
    _publish_release(paths, _release())
    deployment = Path(paths.local(paths.deployment_root))
    duplicate = deployment / "releases"
    duplicate.mkdir(parents=True)
    (duplicate / f"{RELEASE}.json").write_text(
        json_for(_release()), encoding="utf-8"
    )
    with pytest.raises(StateAmbiguityError, match="duplicate"):
        observe_host_state(paths)


def test_observe_refuses_contradictory_selection_history(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _publish_release(paths, _release())
    append_selection(paths, _selection())
    append_selection(paths, _selection(RELEASE, OTHER_RELEASE))

    with pytest.raises(StateAmbiguityError, match="selection"):
        observe_host_state(paths)


def test_lifecycle_lock_serializes_writers_and_times_out(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    with lifecycle_lock(paths, timeout_seconds=0.2):
        with pytest.raises(LifecycleLockContention):
            with lifecycle_lock(paths, timeout_seconds=0.01):
                pass


def json_for(record: object) -> str:
    import json

    return json.dumps(record.to_mapping(), separators=(",", ":"))
