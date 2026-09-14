"""Coherent HostState observation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.host_helper.support import managed_paths

import taskman_ops.host_helper.state as state_module

from taskman_ops.host_helper.backup_protection import (
    BackupProtection,
    backup_protection_retirement_path,
    backup_protection_retirement_root,
    write_backup_protection,
)
from taskman_ops.host_helper.backups import retained_backup_ids
from taskman_ops.host_helper.lock import LifecycleLockContention, lifecycle_lock
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import (
    BackupRecord,
    RecordError,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    selection_filename,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.host_helper.restore_target import RestoreTarget, write_restore_target
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import (
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    ArtifactManifest,
)
from taskman_ops.host_helper.state import (
    MAX_INVENTORY_ENTRIES,
    HostState,
    StateAmbiguityError,
    observe_host_state,
)


RELEASE = build_release_id("0.2.0", "a" * 40, artifact_sha256="b" * 64, source_dirty=False)
OTHER_RELEASE = build_release_id("0.2.1", "c" * 40, artifact_sha256="d" * 64, source_dirty=False)
BACKUP = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_BACKUP = "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _release(
    release_id: str = RELEASE,
    *,
    migrations: tuple[dict[str, str], ...] = (),
    artifact_manifest: ArtifactManifest | None = None,
) -> ReleaseRecord:
    source = "c" * 40 if release_id == OTHER_RELEASE else "a" * 40
    artifact_sha256 = "d" * 64 if release_id == OTHER_RELEASE else "b" * 64
    manifest = artifact_manifest or ArtifactManifest.from_mapping(
        {
            "schema_version": 3,
            "application": "taskman",
            "application_version": "0.2.1" if release_id == OTHER_RELEASE else "0.2.0",
            "source_revision": source,
            "release_id": release_id,
            "built_at": "2026-09-07T12:00:00Z",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "29.0.6",
            "elixir_version": "1.20.4",
            "node_version": "22.22.1",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "builder_base_tag": BUILDER_BASE_TAG,
            "builder_base_digest": BUILDER_BASE_DIGEST,
            "migrations": list(migrations),
            "top_level": "taskman",
            "artifact_sha256": artifact_sha256,
            "source_dirty": False,
        }
    )
    return ReleaseRecord(
        schema_version=2,
        release_id=release_id,
        source_revision=source,
        artifact_sha256=artifact_sha256,
        migrations=tuple(migrations),
        artifact_manifest=manifest,
    )


def _backup() -> BackupRecord:
    return BackupRecord(BACKUP, AT, "c" * 64, RELEASE, (1,), 1024)


def _selection(release_id: str = RELEASE, previous_release_id: str | None = None) -> SelectionRecord:
    return SelectionRecord(
        schema_version=2,
        release_id=release_id,
        previous_release_id=previous_release_id,
        backup_id=None,
        selected_at=AT,
        observed_previous_release_id=None,
        recovery_backup_ids=(),
    )


def _publish_release(paths: ManagedPaths, release: ReleaseRecord) -> None:
    directory = Path(paths.local(paths.release_root / release.release_id))
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o750)
    write_release_manifest(paths, release)


def test_observe_refuses_backup_versions_not_proved_by_declared_source(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    migration = {"filename": "20260907120001_one.exs", "sha256": "1" * 64}
    _publish_release(paths, _release(migrations=(migration,)))
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"backup")
    dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(BACKUP, AT, hashlib.sha256(b"backup").hexdigest(), RELEASE, (20260907120002,), 6),
    )

    with pytest.raises(StateAmbiguityError, match="backup.*source|migration provenance"):
        observe_host_state(paths)


def test_observe_refuses_protection_whose_source_and_target_fingerprints_conflict(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    source_migration = {"filename": "20260907120001_source.exs", "sha256": "1" * 64}
    target_migration = {"filename": "20260907120001_target.exs", "sha256": "2" * 64}
    _publish_release(paths, _release(migrations=(source_migration,)))
    _publish_release(paths, _release(OTHER_RELEASE, migrations=(target_migration,)))
    backup_root = Path(paths.local(paths.backup_root))
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"backup")
    dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(
            BACKUP,
            AT,
            hashlib.sha256(b"backup").hexdigest(),
            RELEASE,
            (20260907120001,),
            6,
        ),
    )
    write_backup_protection(
        paths,
        BackupProtection(1, BACKUP, None, OTHER_RELEASE, 0, AT),
    )

    with pytest.raises(StateAmbiguityError, match="fingerprint|provenance"):
        observe_host_state(paths)


def test_observe_keeps_a_retiring_orphan_dump_out_of_generic_temporary_cleanup(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    dump = Path(paths.local(paths.backup_root)) / f"{BACKUP}.dump"
    dump.write_bytes(b"retiring remainder")
    dump.chmod(0o600)
    retirement_root = backup_protection_retirement_root(paths)
    retirement_root.mkdir(parents=True, mode=0o750)
    protection = BackupProtection(1, BACKUP, None, RELEASE, 0, AT)
    marker = backup_protection_retirement_path(paths, BACKUP)
    marker.write_text(json.dumps(protection.to_mapping()), encoding="utf-8")
    marker.chmod(0o600)

    state = observe_host_state(paths)

    assert state.retiring_backup_protections == (protection,)
    assert dump.as_posix() not in {item.as_posix() for item in state.temporary_paths}


def _write_large_successful_history(
    paths: ManagedPaths,
    *,
    count: int,
    first_backup_id: str | None = None,
) -> tuple[SelectionRecord, SelectionRecord]:
    root = Path(paths.local(paths.selection_root))
    root.mkdir(parents=True, exist_ok=True)
    previous: SelectionRecord | None = None
    penultimate: SelectionRecord | None = None
    for index in range(count):
        record = SelectionRecord(
            schema_version=2,
            release_id=RELEASE,
            previous_release_id=None if index == 0 else RELEASE,
            backup_id=first_backup_id if index == 0 else None,
            selected_at=AT + timedelta(seconds=index),
            observed_previous_release_id=None if index == 0 else RELEASE,
            recovery_backup_ids=(),
        )
        target = root / selection_filename(record)
        target.write_text(json.dumps(record.to_mapping()), encoding="utf-8")
        target.chmod(0o600)
        penultimate, previous = previous, record
    assert penultimate is not None and previous is not None
    return penultimate, previous


def test_observe_empty_host_returns_one_bounded_state_value(tmp_path: Path) -> None:
    state = observe_host_state(managed_paths(tmp_path))

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
    assert state.backup_protections == ()
    assert state.restore_target is None


def test_observe_selected_release_and_database_projection(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
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


@pytest.mark.parametrize("mode", (0o644, 0o700))
def test_observe_refuses_authoritative_records_without_exact_private_mode(
    tmp_path: Path, mode: int
) -> None:
    paths = managed_paths(tmp_path)
    release = _release()
    _publish_release(paths, release)
    Path(paths.local(paths.release_manifest(release.release_id))).chmod(mode)

    with pytest.raises(StateAmbiguityError, match="mode|private"):
        observe_host_state(paths)


def test_observe_refuses_conflicting_duplicate_database_facts(tmp_path: Path) -> None:
    """Accepting aliases with divergent migrations could select the wrong completed state."""

    with pytest.raises(StateAmbiguityError, match="database|migration|contradict"):
        observe_host_state(
            managed_paths(tmp_path),
            database={
                "state": "ready",
                "database_state": "ready",
                "applied_migrations": (1,),
                "migrations": (2,),
            },
        )


def test_observed_release_migrations_cannot_be_mutated_through_host_state(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    migration = {"filename": "20260907120000_bootstrap.exs", "sha256": "c" * 64}
    release = _release(
        migrations=(migration,), artifact_manifest=ArtifactManifest.from_mapping(
            {
                "schema_version": 3,
                "application": "taskman",
                "application_version": "0.2.0",
                "source_revision": "a" * 40,
                "release_id": RELEASE,
                "built_at": "2026-09-07T12:00:00Z",
                "target_os": "ubuntu26.04",
                "architecture": "amd64",
                "otp_version": "29.0.6",
                "elixir_version": "1.20.4",
                "node_version": "22.22.1",
                "hex_version": "2.5.1",
                "rebar3_version": "3.24.0",
                "builder_base_tag": BUILDER_BASE_TAG,
                "builder_base_digest": BUILDER_BASE_DIGEST,
                "migrations": [migration],
                "top_level": "taskman",
                "artifact_sha256": "b" * 64,
                "source_dirty": False,
            }
        )
    )
    _publish_release(paths, release)

    state = observe_host_state(paths)

    with pytest.raises(TypeError):
        state.releases[0].migrations[0]["filename"] = "20260907120001_changed.exs"  # type: ignore[index]


def test_observe_multiple_completed_records_and_validated_backup(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    _publish_release(paths, _release(OTHER_RELEASE))
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"dump")
    dump.chmod(0o600)
    write_backup_manifest(
        paths,
            BackupRecord(BACKUP, AT, hashlib.sha256(dump.read_bytes()).hexdigest(), RELEASE, (), 1024),
    )
    append_selection(paths, _selection())
    append_selection(
        paths,
        SelectionRecord(
            schema_version=2,
            release_id=OTHER_RELEASE,
            previous_release_id=RELEASE,
            backup_id=None,
            selected_at=AT.replace(minute=1),
            observed_previous_release_id=RELEASE,
            recovery_backup_ids=(),
        ),
    )
    current = Path(paths.local(paths.current_link))
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path(paths.local(paths.release_root / OTHER_RELEASE)))

    state = observe_host_state(paths)

    assert {item.release_id for item in state.releases} == {RELEASE, OTHER_RELEASE}
    assert state.backups == (
        BackupRecord(BACKUP, AT, hashlib.sha256(dump.read_bytes()).hexdigest(), RELEASE, (), 1024),
    )
    assert [item.release_id for item in state.selections] == [RELEASE, OTHER_RELEASE]
    assert state.selected_release_id == OTHER_RELEASE


def test_observe_reads_supported_protection_and_restore_bindings(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    release = _release()
    _publish_release(paths, release)
    selection = _selection()
    append_selection(paths, selection)
    current = Path(paths.local(paths.current_link))
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"restore input")
    dump.chmod(0o600)
    backup = BackupRecord(
        BACKUP,
        AT,
        hashlib.sha256(dump.read_bytes()).hexdigest(),
        RELEASE,
        (),
        1024,
    )
    write_backup_manifest(paths, backup)

    protection = BackupProtection(
        schema_version=1,
        backup_id=BACKUP,
        base_selection_id=selection_filename(selection),
        target_release_id=RELEASE,
        attempt_number=0,
        created_at=AT,
    )
    write_backup_protection(paths, protection)
    target = RestoreTarget(
        schema_version=1,
        backup_id=BACKUP,
        dump_sha256=backup.dump_sha256,
        source_release_id=RELEASE,
        base_selection_id=selection_filename(selection),
        observed_previous_release_id=RELEASE,
        original_database_oid=101,
        restored_database_oid=None,
        temporary_creation_pending=True,
        safety_backup_id=BACKUP,
        replacement=None,
        safety_backup_attempts=({"backup_id": BACKUP, "attempt_number": 0},),
    )
    write_restore_target(paths, target)

    state = observe_host_state(paths)

    assert state.backup_protections == (protection,)
    assert state.restore_target == target


def test_observe_refuses_malformed_supported_protection_record(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    root = Path(paths.local(paths.backup_protection_root))
    root.mkdir(parents=True)
    malformed = root / f"{BACKUP}.json"
    malformed.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    malformed.chmod(0o600)

    with pytest.raises(StateAmbiguityError, match="protection|invalid|authoritative"):
        observe_host_state(paths)


def test_observe_refuses_unresolved_null_baseline_after_success(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    selection = _selection()
    append_selection(paths, selection)
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"protected")
    dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(
            BACKUP,
            AT,
            hashlib.sha256(dump.read_bytes()).hexdigest(),
            RELEASE,
            (),
            1024,
        ),
    )
    write_backup_protection(
        paths,
        BackupProtection(1, BACKUP, None, RELEASE, 0, AT),
    )

    with pytest.raises(StateAmbiguityError, match="null-baseline|unresolved|protection"):
        observe_host_state(paths)


def test_observe_refuses_manifest_identity_mismatch_at_canonical_backup_path(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)
    dump = backup_root / f"{OTHER_BACKUP}.dump"
    dump.write_bytes(b"other backup")
    dump.chmod(0o600)
    mismatched = BackupRecord(
        OTHER_BACKUP,
        AT,
        hashlib.sha256(b"other backup").hexdigest(),
        RELEASE,
        (),
        1024,
    )
    manifest_path = backup_root / f"{BACKUP}.json"
    manifest_path.write_text(
        json.dumps(mismatched.to_mapping()), encoding="utf-8"
    )
    manifest_path.chmod(0o600)

    with pytest.raises(StateAmbiguityError, match="identity|path"):
        observe_host_state(paths)


def test_observe_refuses_selection_filename_identity_mismatch(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    release = _release()
    _publish_release(paths, release)
    selection_root = Path(paths.local(paths.selection_root))
    selection_root.mkdir(parents=True)
    selection = _selection()
    selection_path = selection_root / ("selection-" + "0" * 64 + ".json")
    selection_path.write_text(
        json.dumps(selection.to_mapping()), encoding="utf-8"
    )
    selection_path.chmod(0o600)
    current = Path(paths.local(paths.current_link))
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    with pytest.raises(StateAmbiguityError, match="identity|filename|path"):
        observe_host_state(paths)


def test_observe_recognizes_only_exact_taskman_temporary_shapes(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
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
    paths = managed_paths(tmp_path)
    release_id = build_release_id(
        version, "a" * 40, artifact_sha256="b" * 64, source_dirty=False
    )
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
    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    legacy_releases = Path(paths.local(paths.deployment_root / "releases"))
    legacy_releases.mkdir(parents=True)
    (legacy_releases / f"{RELEASE}.json").write_text("never deployed", encoding="utf-8")

    state = observe_host_state(paths)

    assert state.releases == (_release(),)


def test_observe_refuses_authoritative_symlink_and_duplicate_identity(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    install = Path(paths.local(paths.install_root))
    target = tmp_path / "outside"
    target.mkdir()
    install.mkdir()
    (install / "releases").symlink_to(target, target_is_directory=True)
    with pytest.raises(StateAmbiguityError, match="unsafe|authoritative|symlink"):
        observe_host_state(paths)


def test_observe_refuses_contradictory_selection_history(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    append_selection(paths, _selection())
    append_selection(paths, _selection(RELEASE, OTHER_RELEASE))

    with pytest.raises(StateAmbiguityError, match="selection"):
        observe_host_state(paths)


def test_observe_refuses_a_first_selection_that_names_a_previous_success(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    append_selection(paths, _selection(previous_release_id=OTHER_RELEASE))
    current = Path(paths.local(paths.current_link))
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    with pytest.raises(StateAmbiguityError, match="previous|selection"):
        observe_host_state(paths)


def test_observe_refuses_a_later_selection_without_an_observed_previous_release(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    _publish_release(paths, _release(OTHER_RELEASE))
    append_selection(paths, _selection())
    append_selection(
        paths,
        SelectionRecord(
            schema_version=2,
            release_id=OTHER_RELEASE,
            previous_release_id=RELEASE,
            backup_id=None,
            selected_at=AT.replace(minute=1),
            observed_previous_release_id=None,
            recovery_backup_ids=(),
        ),
    )
    current = Path(paths.local(paths.current_link))
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path(paths.local(paths.release_root / OTHER_RELEASE)))

    with pytest.raises(StateAmbiguityError, match="observed|selection"):
        observe_host_state(paths)


def test_observe_refuses_an_unsupported_release_directory_with_old_authority(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    old_release = "0.2.0-" + "a" * 12 + "-ubuntu26.04-amd64-otp27.3.4.6"
    release_path = Path(paths.local(paths.release_root / old_release))
    release_path.mkdir(parents=True)
    release_path.chmod(0o750)
    manifest = release_path / ".taskman-release.json"
    manifest.write_text("{}", encoding="utf-8")
    manifest.chmod(0o600)

    with pytest.raises(StateAmbiguityError, match="unsupported|release|authority"):
        observe_host_state(paths)


def test_lifecycle_lock_serializes_writers_and_times_out(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    with lifecycle_lock(paths, timeout_seconds=0.2):
        with pytest.raises(LifecycleLockContention):
            with lifecycle_lock(paths, timeout_seconds=0.01):
                pass


def test_observe_refuses_an_inventory_that_exceeds_the_safe_bound(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    release_root = Path(paths.local(paths.release_root))
    release_root.mkdir(parents=True)
    for index in range(MAX_INVENTORY_ENTRIES + 1):
        (release_root / f"operator-{index}").write_text("ignore", encoding="utf-8")

    with pytest.raises(StateAmbiguityError, match="inventory|entries|bounded"):
        observe_host_state(paths)


def test_observe_validates_more_than_4096_selections_and_retains_old_references(
    tmp_path: Path,
) -> None:
    """A lifetime count cap or projected-only references would lose valid authority."""

    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"old history backup")
    dump.chmod(0o600)
    backup = BackupRecord(
        BACKUP,
        AT,
        hashlib.sha256(dump.read_bytes()).hexdigest(),
        RELEASE,
        (),
        1024,
    )
    write_backup_manifest(paths, backup)
    penultimate, latest = _write_large_successful_history(
        paths,
        count=MAX_INVENTORY_ENTRIES + 1,
        first_backup_id=BACKUP,
    )
    current = Path(paths.local(paths.current_link))
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    state = observe_host_state(paths)

    assert state.selections == (penultimate, latest)
    assert state.latest_successful_selection == latest
    assert state.previous_successful_selection == penultimate
    assert state.latest_successful_selection_filename == selection_filename(latest)
    assert state.previous_successful_selection_filename == selection_filename(penultimate)
    assert BACKUP in retained_backup_ids(state, 1)
    assert state.to_mapping()["selections"] == [
        penultimate.to_mapping(),
        latest.to_mapping(),
    ]


def test_observe_validates_authority_beyond_the_4096th_selection(tmp_path: Path) -> None:
    """Truncating validation would accept a malformed authoritative history tail."""

    paths = managed_paths(tmp_path)
    _publish_release(paths, _release())
    _write_large_successful_history(paths, count=MAX_INVENTORY_ENTRIES + 1)
    root = Path(paths.local(paths.selection_root))
    malformed = root / ("selection-" + "0" * 64 + ".json")
    malformed.write_text("{}", encoding="utf-8")
    malformed.chmod(0o600)

    with pytest.raises(StateAmbiguityError, match="selection record is invalid"):
        observe_host_state(paths)


def test_observe_refuses_incomplete_selection_history_inspection_at_deadline(
    tmp_path: Path,
) -> None:
    """An expired operation deadline must not produce a truncated successful state."""

    with pytest.raises(StateAmbiguityError, match="selection history|deadline|timed out"):
        observe_host_state(managed_paths(tmp_path), deadline=0.0)


def test_observe_enforces_deadline_during_history_relationship_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completing enumeration does not suspend the operation's state-loading deadline."""

    class CompletedEnumeration:
        def close(self) -> None:
            pass

    monkeypatch.setattr(state_module, "_read_selections", lambda *_args: CompletedEnumeration())
    monkeypatch.setattr(state_module.time, "monotonic", lambda: 2.0)

    with pytest.raises(StateAmbiguityError, match="selection history.*timed out"):
        observe_host_state(managed_paths(tmp_path), deadline=1.0)
