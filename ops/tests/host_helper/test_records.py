"""Completed host record codecs and publication tests."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import hashlib
from pathlib import Path

import pytest

from tests.host_helper.support import managed_paths

from taskman_ops.host_helper.records import (
    BackupRecord,
    RecordError,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    write_backup_manifest,
    write_release_manifest,
)


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
OTHER_RELEASE = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
CURRENT_RELEASE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp29.0.6"
CURRENT_PRERELEASE = "0.2.0-rc.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp29.0.6"
BACKUP = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _release(**changes: object) -> ReleaseRecord:
    values: dict[str, object] = {
        "release_id": RELEASE,
        "source_revision": "a" * 40,
        "artifact_sha256": "b" * 64,
        "migrations": (
            {"filename": "20260907120000_bootstrap.exs", "sha256": "c" * 64},
        ),
    }
    values.update(changes)
    return ReleaseRecord(**values)  # type: ignore[arg-type]


def _backup(**changes: object) -> BackupRecord:
    values: dict[str, object] = {
        "backup_id": BACKUP,
        "created_at": AT,
        "dump_sha256": "d" * 64,
        "source_release_id": RELEASE,
        "migration_versions": (1, 2),
        "source_database_size_bytes": 4096,
    }
    values.update(changes)
    return BackupRecord(**values)  # type: ignore[arg-type]


def _selection(**changes: object) -> SelectionRecord:
    values: dict[str, object] = {
        "release_id": RELEASE,
        "previous_release_id": None,
        "backup_id": None,
        "selected_at": AT,
    }
    values.update(changes)
    return SelectionRecord(**values)  # type: ignore[arg-type]


def test_completed_records_have_exact_flat_schemas_and_round_trip() -> None:
    release = _release()
    backup = _backup()
    selection = _selection()

    assert set(release.to_mapping()) == {
        "release_id", "source_revision", "artifact_sha256", "migrations"
    }
    assert set(backup.to_mapping()) == {
        "backup_id", "created_at", "dump_sha256", "source_release_id",
        "migration_versions", "source_database_size_bytes",
    }
    assert set(selection.to_mapping()) == {
        "release_id", "previous_release_id", "backup_id", "selected_at"
    }
    forbidden = {
        "pending", "provisional", "finalization", "recovery", "operation_id",
    }
    assert not forbidden.intersection(release.to_mapping())
    assert not forbidden.intersection(backup.to_mapping())
    assert not forbidden.intersection(selection.to_mapping())

    assert ReleaseRecord.from_mapping(release.to_mapping()) == release
    assert BackupRecord.from_mapping(backup.to_mapping()) == backup
    assert SelectionRecord.from_mapping(selection.to_mapping()) == selection


def test_release_records_validate_source_provenance_against_their_own_runtime_identity() -> None:
    """Using the new default while reading an OTP 27 record would reject deployed history."""

    legacy = _release()
    current = _release(release_id=CURRENT_RELEASE, source_revision="b" * 40)

    assert ReleaseRecord.from_mapping(legacy.to_mapping()) == legacy
    assert ReleaseRecord.from_mapping(current.to_mapping()) == current
    with pytest.raises(RecordError, match="release identity"):
        _release(release_id=CURRENT_RELEASE, source_revision="a" * 40)


def test_release_record_provenance_keeps_prerelease_version_metadata() -> None:
    """Splitting an ID at its first hyphen would corrupt a valid prerelease identity."""

    record = _release(release_id=CURRENT_PRERELEASE, source_revision="b" * 40)

    assert ReleaseRecord.from_mapping(record.to_mapping()) == record


def test_backup_record_serializes_a_whole_second_utc_creation_time() -> None:
    """Dropping or fractionalizing creation time would make backup retention nondeterministic."""

    record = _backup()

    assert record.to_mapping()["created_at"] == "2026-09-07T12:00:00Z"
    with pytest.raises(RecordError, match="creation time"):
        _backup(created_at=datetime(2026, 9, 7, 12, 0, 0, 1, tzinfo=UTC))


@pytest.mark.parametrize("timestamp", ("2026-09-07T12:00Z", "2026-09-07T12:00:00.000Z"))
def test_backup_record_rejects_noncanonical_creation_timestamps(timestamp: str) -> None:
    """Accepting alternate spellings would make the persisted record representation ambiguous."""

    mapping = _backup().to_mapping()
    mapping["created_at"] = timestamp

    with pytest.raises(RecordError, match="creation time"):
        BackupRecord.from_mapping(mapping)


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    [
        (_release, "release_id", "not-a-release"),
        (_release, "source_revision", "short"),
        (_release, "artifact_sha256", "not-a-sha"),
        (_backup, "backup_id", "backup-unsafe"),
        (_backup, "source_release_id", "not-a-release"),
        (_selection, "release_id", "not-a-release"),
        (_selection, "backup_id", "backup-unsafe"),
    ],
)
def test_completed_record_identifiers_are_validated(
    factory: object, field: str, value: object
) -> None:
    with pytest.raises((RecordError, ValueError)):
        factory(**{field: value})  # type: ignore[operator]


def test_completed_record_json_is_bounded() -> None:
    with pytest.raises((RecordError, ValueError), match="invalid|size|large|bounded"):
        _release(
            migrations=tuple(
                {"filename": f"2026090712{i:04d}_bootstrap.exs", "sha256": "a" * 64}
                for i in range(257)
            )
        )


def test_release_migration_fingerprints_are_transitively_immutable() -> None:
    record = _release()

    with pytest.raises(TypeError):
        record.migrations[0]["sha256"] = "d" * 64  # type: ignore[index]


def test_backup_manifest_hashes_large_dumps_incrementally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = managed_paths(tmp_path)
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"incrementally validated dump")
    dump.chmod(0o600)

    def no_read_bytes(_path: Path) -> bytes:
        raise AssertionError("backup hashing must not load the dump with read_bytes")

    monkeypatch.setattr(Path, "read_bytes", no_read_bytes)
    write_backup_manifest(
        paths,
        _backup(dump_sha256=hashlib.sha256(b"incrementally validated dump").hexdigest()),
    )


def test_release_manifest_uses_the_derived_release_location_and_private_mode(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    release_path = Path(paths.local(paths.release_root / RELEASE))
    release_path.mkdir(parents=True)
    release_path.chmod(0o750)
    write_release_manifest(paths, _release())

    manifest = release_path / ".taskman-release.json"
    assert manifest.is_file()
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert json.loads(manifest.read_text(encoding="utf-8")) == _release().to_mapping()
    assert not list(release_path.glob(".*.tmp"))

    with pytest.raises((RecordError, ValueError), match="exists|published"):
        write_release_manifest(paths, _release())


def test_backup_manifest_is_written_beside_the_validated_dump(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"validated dump")
    dump.chmod(0o600)
    write_backup_manifest(
        paths,
        _backup(dump_sha256=hashlib.sha256(dump.read_bytes()).hexdigest()),
    )

    manifest = backup_root / f"{BACKUP}.json"
    assert manifest.is_file()
    assert manifest.stat().st_mode & 0o777 == 0o600
    expected = _backup(dump_sha256=hashlib.sha256(dump.read_bytes()).hexdigest())
    assert json.loads(manifest.read_text(encoding="utf-8")) == expected.to_mapping()


def test_selection_records_are_create_once_and_atomic(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    append_selection(paths, _selection())
    selection_root = Path(paths.local(paths.selection_root))
    files = sorted(selection_root.glob("*.json"))
    assert len(files) == 1
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert SelectionRecord.from_mapping(json.loads(files[0].read_text(encoding="utf-8"))) == _selection()
    assert not list(selection_root.glob(".*.tmp"))
    with pytest.raises((RecordError, ValueError), match="exists|published"):
        append_selection(paths, _selection())
