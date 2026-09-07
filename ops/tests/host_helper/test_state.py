"""Coherent HostState observation tests."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
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
from taskman_ops.host_helper.state import (
    MAX_INVENTORY_ENTRIES,
    HostState,
    StateAmbiguityError,
    observe_host_state,
)


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
OTHER_RELEASE = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_BACKUP = "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
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


def test_observed_release_migrations_cannot_be_mutated_through_host_state(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    release = ReleaseRecord(
        RELEASE,
        "a" * 40,
        "b" * 64,
        ({"filename": "20260907120000_bootstrap.exs", "sha256": "c" * 64},),
    )
    _publish_release(paths, release)

    state = observe_host_state(paths)

    with pytest.raises(TypeError):
        state.releases[0].migrations[0]["filename"] = "20260907120001_changed.exs"  # type: ignore[index]


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


def test_observe_refuses_manifest_identity_mismatch_at_canonical_backup_path(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)
    dump = backup_root / f"{OTHER_BACKUP}.dump"
    dump.write_bytes(b"other backup")
    dump.chmod(0o600)
    mismatched = BackupRecord(
        OTHER_BACKUP,
        hashlib.sha256(b"other backup").hexdigest(),
        RELEASE,
        (1,),
        1024,
    )
    (backup_root / f"{BACKUP}.json").write_text(
        json.dumps(mismatched.to_mapping()), encoding="utf-8"
    )

    with pytest.raises(StateAmbiguityError, match="identity|path"):
        observe_host_state(paths)


def test_observe_refuses_selection_filename_identity_mismatch(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    release = _release()
    _publish_release(paths, release)
    selection_root = Path(paths.local(paths.selection_root))
    selection_root.mkdir(parents=True)
    selection = _selection()
    (selection_root / ("selection-" + "0" * 64 + ".json")).write_text(
        json.dumps(selection.to_mapping()), encoding="utf-8"
    )
    current = Path(paths.local(paths.current_link))
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    with pytest.raises(StateAmbiguityError, match="identity|filename|path"):
        observe_host_state(paths)


def test_observe_recognizes_only_exact_taskman_temporary_shapes(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    install = Path(paths.local(paths.install_root))
    backup_root = Path(paths.local(paths.backup_root))
    release = _release()
    _publish_release(paths, release)
    release_root = Path(paths.local(paths.release_root / RELEASE))
    selection_root = Path(paths.local(paths.selection_root))
    selection_root.mkdir(parents=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    expected = (
        release_root / "..taskman-release.json.12345.0.tmp",
        backup_root / f".{BACKUP}.json.12345.0.tmp",
        backup_root / f".{BACKUP}.dump.tmp",
        selection_root / (".selection-" + "0" * 64 + ".json.12345.0.tmp"),
    )
    expected = tuple(Path(path) for path in expected)
    for path in expected:
        path.write_bytes(b"incomplete")
        path.chmod(0o600)
    (install / "releases" / "operator-notes.txt").write_text("ignore", encoding="utf-8")
    (backup_root / f".{BACKUP}.pending.json").write_text("obsolete", encoding="utf-8")
    (backup_root / f".{BACKUP}.tmp").write_text("operator", encoding="utf-8")
    (install / "deployments").mkdir(parents=True, exist_ok=True)
    (install / "deployments" / (".stage-" + "a" * 32 + ".tmp")).write_text(
        "obsolete", encoding="utf-8"
    )

    state = observe_host_state(paths)

    assert tuple(Path(item.as_posix()) for item in state.temporary_paths) == tuple(
        Path(item) for item in sorted(path.as_posix() for path in expected)
    )
    assert all("pending" not in path.as_posix() for path in state.temporary_paths)
    assert any("unknown" in warning or "operator-notes" in warning for warning in state.warnings)


@pytest.mark.parametrize("version", ("0.2.0-rc.1", "0.2.0+build.7"))
def test_observe_recognizes_exact_release_temporary_with_valid_version_metadata(
    tmp_path: Path,
    version: str,
) -> None:
    paths = _paths(tmp_path)
    release_id = f"{version}-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    temporary = Path(paths.local(paths.release_root / f".release-{release_id}.tmp"))
    temporary.mkdir(parents=True)
    temporary.chmod(0o750)
    invalid = temporary.with_name(f"{temporary.name}.extra")
    invalid.mkdir()
    invalid.chmod(0o750)

    state = observe_host_state(paths)

    assert state.temporary_paths == (temporary,)
    assert any(invalid.name in warning for warning in state.warnings)


def test_observe_does_not_read_legacy_deployment_record_directories(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _publish_release(paths, _release())
    legacy_releases = Path(paths.local(paths.deployment_root / "releases"))
    legacy_releases.mkdir(parents=True)
    (legacy_releases / f"{RELEASE}.json").write_text("never deployed", encoding="utf-8")

    state = observe_host_state(paths)

    assert state.releases == (_release(),)


def test_observe_refuses_authoritative_symlink_and_duplicate_identity(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    install = Path(paths.local(paths.install_root))
    target = tmp_path / "outside"
    target.mkdir()
    install.mkdir()
    (install / "releases").symlink_to(target, target_is_directory=True)
    with pytest.raises(StateAmbiguityError, match="unsafe|authoritative|symlink"):
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


def test_observe_refuses_an_inventory_that_exceeds_the_safe_bound(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    release_root = Path(paths.local(paths.release_root))
    release_root.mkdir(parents=True)
    for index in range(MAX_INVENTORY_ENTRIES + 1):
        (release_root / f"operator-{index}").write_text("ignore", encoding="utf-8")

    with pytest.raises(StateAmbiguityError, match="inventory|entries|bounded"):
        observe_host_state(paths)
