"""Supported persisted host record codecs and publication tests."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import pytest

from tests.host_helper.support import managed_paths

from taskman_ops.host_helper.records import (
    BackupRecord,
    MAX_RECORD_BYTES,
    RecordError,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    selection_filename,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import (
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    ArtifactManifest,
)


REVISION = "a" * 40
ARTIFACT_SHA256 = "b" * 64
RELEASE = build_release_id(
    "0.2.0", REVISION, artifact_sha256=ARTIFACT_SHA256, source_dirty=False
)
OTHER_RELEASE = build_release_id(
    "0.2.1", "c" * 40, artifact_sha256="d" * 64, source_dirty=False
)
BACKUP = "backup-" + "e" * 32
AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _manifest(
    *,
    release_id: str = RELEASE,
    source_revision: str = REVISION,
    artifact_sha256: str = ARTIFACT_SHA256,
    source_dirty: bool = False,
    migrations: tuple[dict[str, str], ...] = (),
) -> ArtifactManifest:
    return ArtifactManifest.from_mapping(
        {
            "schema_version": 3,
            "application": "taskman",
            "application_version": release_id.split("-", 1)[0],
            "source_revision": source_revision,
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
            "source_dirty": source_dirty,
        }
    )


def _release(**changes: object) -> ReleaseRecord:
    values: dict[str, object] = {
        "schema_version": 2,
        "release_id": RELEASE,
        "source_revision": REVISION,
        "artifact_sha256": ARTIFACT_SHA256,
        "migrations": (),
        "artifact_manifest": _manifest(),
    }
    values.update(changes)
    return ReleaseRecord(**values)  # type: ignore[arg-type]


def _backup(**changes: object) -> BackupRecord:
    values: dict[str, object] = {
        "backup_id": BACKUP,
        "created_at": AT,
        "dump_sha256": "f" * 64,
        "source_release_id": RELEASE,
        "migration_versions": (1, 2),
        "source_database_size_bytes": 4096,
    }
    values.update(changes)
    return BackupRecord(**values)  # type: ignore[arg-type]


def _selection(**changes: object) -> SelectionRecord:
    values: dict[str, object] = {
        "schema_version": 2,
        "release_id": RELEASE,
        "previous_release_id": None,
        "backup_id": None,
        "selected_at": AT,
        "observed_previous_release_id": None,
        "recovery_backup_ids": (),
    }
    values.update(changes)
    return SelectionRecord(**values)  # type: ignore[arg-type]


def test_supported_records_have_exact_schemas_and_round_trip() -> None:
    release = _release()
    backup = _backup()
    selection = _selection()

    assert set(release.to_mapping()) == {
        "release_id",
        "source_revision",
        "artifact_sha256",
        "migrations",
        "schema_version",
        "artifact_manifest",
    }
    assert set(backup.to_mapping()) == {
        "backup_id",
        "created_at",
        "dump_sha256",
        "source_release_id",
        "migration_versions",
        "source_database_size_bytes",
    }
    assert set(selection.to_mapping()) == {
        "release_id",
        "previous_release_id",
        "backup_id",
        "selected_at",
        "schema_version",
        "observed_previous_release_id",
        "recovery_backup_ids",
    }
    assert ReleaseRecord.from_mapping(release.to_mapping()) == release
    assert BackupRecord.from_mapping(backup.to_mapping()) == backup
    assert SelectionRecord.from_mapping(selection.to_mapping()) == selection


@pytest.mark.parametrize(
    "record",
    (
        {
            "release_id": RELEASE,
            "source_revision": REVISION,
            "artifact_sha256": ARTIFACT_SHA256,
            "migrations": [],
        },
        {
            "release_id": RELEASE,
            "previous_release_id": None,
            "backup_id": None,
            "selected_at": "2026-09-07T12:00:00Z",
        },
    ),
)
def test_unversioned_records_are_rejected(record: dict[str, object]) -> None:
    parser = ReleaseRecord.from_mapping if "source_revision" in record else SelectionRecord.from_mapping

    with pytest.raises(RecordError, match="invalid|schema|fields"):
        parser(record)


def test_release_record_requires_embedded_manifest_identity_to_match() -> None:
    mapping = _release().to_mapping()
    embedded = dict(mapping["artifact_manifest"])
    embedded["artifact_sha256"] = "1" * 64
    mapping["artifact_manifest"] = embedded

    with pytest.raises(RecordError, match="manifest|identity|digest"):
        ReleaseRecord.from_mapping(mapping)


def test_release_record_preserves_manifest_build_timestamp_as_descriptive_provenance() -> None:
    first = _release()
    mapping = first.to_mapping()
    embedded = dict(mapping["artifact_manifest"])
    embedded["built_at"] = "2026-09-08T13:00:00Z"
    mapping["artifact_manifest"] = embedded

    second = ReleaseRecord.from_mapping(mapping)

    assert second.artifact_manifest.built_at == datetime(2026, 9, 8, 13, tzinfo=UTC)
    assert second.release_id == first.release_id
    assert second.artifact_sha256 == first.artifact_sha256


def test_selection_record_allows_same_release_predecessor_when_reconciling_history() -> None:
    selection = _selection(previous_release_id=RELEASE, observed_previous_release_id=OTHER_RELEASE)

    assert SelectionRecord.from_mapping(selection.to_mapping()) == selection


def test_selection_recovery_references_are_sorted_unique_and_bounded() -> None:
    first = "backup-" + "1" * 32
    second = "backup-" + "2" * 32
    assert _selection(recovery_backup_ids=(first, second)).recovery_backup_ids == (first, second)

    with pytest.raises(RecordError, match="sorted|unique"):
        _selection(recovery_backup_ids=(second, first))
    with pytest.raises(RecordError, match="sorted|unique"):
        _selection(recovery_backup_ids=(first, first))
    with pytest.raises(RecordError, match="recovery|64|bounded"):
        _selection(recovery_backup_ids=tuple(f"backup-{index:032x}" for index in range(65)))


def test_backup_record_serializes_a_whole_second_utc_creation_time() -> None:
    record = _backup()

    assert record.to_mapping()["created_at"] == "2026-09-07T12:00:00Z"
    with pytest.raises(RecordError, match="creation time"):
        _backup(created_at=datetime(2026, 9, 7, 12, 0, 0, 1, tzinfo=UTC))


@pytest.mark.parametrize("timestamp", ("2026-09-07T12:00Z", "2026-09-07T12:00:00.000Z"))
def test_backup_record_rejects_noncanonical_creation_timestamps(timestamp: str) -> None:
    mapping = _backup().to_mapping()
    mapping["created_at"] = timestamp

    with pytest.raises(RecordError, match="creation time"):
        BackupRecord.from_mapping(mapping)


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    (
        (_release, "release_id", "not-a-release"),
        (_release, "source_revision", "short"),
        (_release, "artifact_sha256", "not-a-sha"),
        (_backup, "backup_id", "backup-unsafe"),
        (_backup, "source_release_id", "not-a-release"),
        (_selection, "release_id", "not-a-release"),
        (_selection, "backup_id", "backup-unsafe"),
    ),
)
def test_completed_record_identifiers_are_validated(factory: object, field: str, value: object) -> None:
    with pytest.raises((RecordError, ValueError)):
        factory(**{field: value})  # type: ignore[operator]


def test_release_migration_fingerprints_are_transitively_immutable() -> None:
    migration = {"filename": "20260907120000_bootstrap.exs", "sha256": "c" * 64}
    manifest = _manifest(migrations=(migration,))
    record = _release(
        migrations=(migration,),
        artifact_manifest=manifest,
    )

    with pytest.raises(TypeError):
        record.migrations[0]["sha256"] = "d" * 64  # type: ignore[index]


def test_release_record_accepts_the_256_fingerprint_limit() -> None:
    migrations = tuple(
        {"filename": f"{index:014d}_migration.exs", "sha256": "c" * 64}
        for index in range(256)
    )
    record = _release(migrations=migrations, artifact_manifest=_manifest(migrations=migrations))

    assert len(record.migrations) == 256


def test_release_manifest_publication_uses_the_256_kibibyte_record_budget(
    tmp_path: Path,
) -> None:
    migrations = tuple(
        {
            "filename": f"{index:014d}_{'migration_' + ('a' * 180)}.exs",
            "sha256": "c" * 64,
        }
        for index in range(256)
    )
    record = _release(migrations=migrations, artifact_manifest=_manifest(migrations=migrations))
    encoded = json.dumps(record.to_mapping(), separators=(",", ":"), sort_keys=True).encode("ascii")
    assert len(encoded) > MAX_RECORD_BYTES
    assert len(encoded) < 256 * 1024

    paths = managed_paths(tmp_path)
    release_path = Path(paths.local(paths.release_root / RELEASE))
    release_path.mkdir(parents=True)
    release_path.chmod(0o750)

    write_release_manifest(paths, record)

    assert (release_path / ".taskman-release.json").is_file()


def test_backup_manifest_hashes_large_dumps_incrementally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_release_manifest_uses_derived_release_location_and_private_mode(tmp_path: Path) -> None:
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


def test_backup_manifest_is_written_beside_the_validated_dump(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    backup_root = Path(paths.local(paths.backup_root))
    backup_root.mkdir(parents=True)
    dump = backup_root / f"{BACKUP}.dump"
    dump.write_bytes(b"validated dump")
    dump.chmod(0o600)
    record = _backup(dump_sha256=hashlib.sha256(dump.read_bytes()).hexdigest())
    write_backup_manifest(paths, record)

    manifest = backup_root / f"{BACKUP}.json"
    assert manifest.is_file()
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert json.loads(manifest.read_text(encoding="utf-8")) == record.to_mapping()


def test_selection_records_are_create_once_and_atomic(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    record = _selection()
    append_selection(paths, record)
    selection_root = Path(paths.local(paths.selection_root))
    files = sorted(selection_root.glob("*.json"))
    assert len(files) == 1
    assert files[0].name == selection_filename(record)
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert SelectionRecord.from_mapping(json.loads(files[0].read_text(encoding="utf-8"))) == record
    assert not list(selection_root.glob(".*.tmp"))
    with pytest.raises((RecordError, ValueError), match="exists|published"):
        append_selection(paths, record)


def test_record_serialization_budget_is_enforced() -> None:
    assert MAX_RECORD_BYTES == 64 * 1024
    recovery_ids = tuple(f"backup-{index:032x}" for index in range(64))
    assert _selection(recovery_backup_ids=recovery_ids).recovery_backup_ids == recovery_ids
    with pytest.raises((RecordError, ValueError), match="recovery|64|bounded"):
        _selection(recovery_backup_ids=(*recovery_ids, "backup-" + "f" * 32))


def test_each_record_codec_enforces_its_declared_encoded_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import taskman_ops.host_helper.records as records_module

    selection_mapping = _selection().to_mapping()
    monkeypatch.setattr(records_module, "MAX_RECORD_BYTES", 1)
    with pytest.raises(RecordError, match="oversized|size|record"):
        SelectionRecord.from_mapping(selection_mapping)

    monkeypatch.setattr(records_module, "MAX_RELEASE_RECORD_BYTES", 1)
    with pytest.raises(RecordError, match="oversized|size|record"):
        ReleaseRecord.from_mapping(_release().to_mapping())
