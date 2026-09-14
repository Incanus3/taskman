from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

import pytest

from taskman_ops.checksums import sha256_file
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.releases.manifests import (
    ArtifactManifest,
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    MigrationFingerprint,
    fingerprint_migrations,
    manifest_from_json,
    manifest_to_json,
    verify_artifact,
)
from taskman_ops.releases.identifiers import build_release_id, managed_release_path, validate_release_id


REVISION = "a" * 40
ARTIFACT_SHA256 = "b" * 64
RELEASE_ID = build_release_id(
    "0.2.0", REVISION, artifact_sha256=ARTIFACT_SHA256, source_dirty=False
)
CHECKSUM = "c" * 64


def manifest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 3,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": REVISION,
        "release_id": RELEASE_ID,
        "built_at": "2026-09-04T20:15:30Z",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "29.0.6",
        "elixir_version": "1.20.4",
        "node_version": "22.22.1",
        "builder_base_tag": BUILDER_BASE_TAG,
        "builder_base_digest": BUILDER_BASE_DIGEST,
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "migrations": [
            {
                "filename": "20260904065131_install_ash_functions_extensions_1.exs",
                "sha256": CHECKSUM,
            }
        ],
        "top_level": "taskman",
        "artifact_sha256": ARTIFACT_SHA256,
        "source_dirty": False,
    }
    payload.update(overrides)
    return payload


def write_manifest_bundle(tmp_path: Path, archive: Path, **overrides: object) -> tuple[Path, Path]:
    archive_sha256 = sha256_file(archive)
    overrides = {
        "release_id": build_release_id(
            "0.2.0", REVISION, artifact_sha256=archive_sha256, source_dirty=False
        ),
        "artifact_sha256": archive_sha256,
        **overrides,
    }
    manifest = tmp_path / "taskman.manifest.json"
    manifest.write_text(json.dumps(manifest_payload(**overrides)), encoding="utf-8")
    checksum = tmp_path / "taskman.tar.gz.sha256"
    checksum.write_text(f"{sha256_file(archive)}  {archive.name}\n", encoding="ascii")
    return manifest, checksum


def add_directory(archive: tarfile.TarFile, name: str) -> None:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = 0o755
    archive.addfile(member)


def add_file(archive: tarfile.TarFile, name: str, content: bytes = b"release", mode: int = 0o755) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    archive.addfile(member, io.BytesIO(content))


def write_release_archive(
    tmp_path: Path,
    *,
    omit: str | None = None,
    extra: tuple[str, bytes, bytes | None] | None = None,
) -> Path:
    archive_path = tmp_path / "taskman.tar.gz"
    directories = [
        "taskman",
        "taskman/bin",
        "taskman/lib",
        "taskman/releases",
        "taskman/erts-16.0",
    ]
    files = [
        "taskman/bin/taskman",
        "taskman/bin/server",
        "taskman/bin/migrate",
        "taskman/bin/create-admin",
    ]

    with tarfile.open(archive_path, "w:gz") as archive:
        for directory in directories:
            if directory != omit:
                add_directory(archive, directory)
        for filename in files:
            if filename != omit:
                add_file(archive, filename)
        if extra is not None:
            name, member_type, linkname = extra
            member = tarfile.TarInfo(name)
            member.type = member_type
            member.mode = 0o755
            if linkname is not None:
                member.linkname = linkname.decode("utf-8")
            archive.addfile(member)
    return archive_path


def test_release_identity_requires_archive_digest_and_tracks_source_provenance() -> None:
    release_id = build_release_id(
        "0.2.0", REVISION, artifact_sha256=ARTIFACT_SHA256, source_dirty=False
    )

    assert release_id == RELEASE_ID
    assert validate_release_id(release_id) == release_id
    assert managed_release_path(PurePosixPath("/opt/taskman/releases"), release_id) == PurePosixPath(
        f"/opt/taskman/releases/{RELEASE_ID}"
    )
    assert validate_release_id(RELEASE_ID) == RELEASE_ID
    dirty = build_release_id(
        "0.2.0", REVISION, artifact_sha256=ARTIFACT_SHA256, source_dirty=True
    )
    assert dirty == RELEASE_ID + "-dirty"


@pytest.mark.parametrize(
    ("release_id", "otp_version", "elixir_version"),
    (
        ("0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6-" + "b" * 64, "27.3.4.6", "1.18.3"),
        ("0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 63 + "c", "29.0.0", "1.20.4"),
        ("0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp28.0.0-" + "b" * 64, "28.0.0", "1.19.0"),
    ),
)
def test_manifest_refuses_an_unsupported_or_mismatched_runtime_tuple(
    release_id: str, otp_version: str, elixir_version: str
) -> None:
    """Accepting an arbitrary OTP version or cross-paired metadata loses provenance."""

    with pytest.raises(ValueError, match="release|toolchain"):
        ArtifactManifest.from_mapping(
            manifest_payload(
                release_id=release_id,
                artifact_sha256=release_id.split("-")[-1],
                otp_version=otp_version,
                elixir_version=elixir_version,
            )
        )


@pytest.mark.parametrize(
    "value",
    [
        "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64 + "/next",
        "../0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64,
        "0.2.0-AAAAAAAAAAAA-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64,
        "0.2.0-aaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64,
        "0.2.0-aaaaaaaaaaaa-ubuntu24.04-amd64-otp29.0.6-" + "b" * 64,
    ],
)
def test_release_identity_refuses_path_aliases_and_unsupported_targets(value: str) -> None:
    with pytest.raises(ValueError):
        validate_release_id(value)


def test_manifest_round_trip_has_the_exact_schema_and_utc_provenance() -> None:
    manifest = ArtifactManifest.from_mapping(manifest_payload())

    assert manifest.built_at == datetime(2026, 9, 4, 20, 15, 30, tzinfo=UTC)
    assert manifest.release_id == RELEASE_ID
    assert manifest.migrations == (
        MigrationFingerprint(
            filename="20260904065131_install_ash_functions_extensions_1.exs",
            sha256=CHECKSUM,
        ),
    )
    assert manifest_from_json(manifest_to_json(manifest)) == manifest
    with pytest.raises(FrozenInstanceError):
        manifest.release_id = "different"  # type: ignore[misc]


def test_manifest_records_the_readable_and_immutable_builder_base_identity() -> None:
    payload = manifest_payload(
        schema_version=3,
        builder_base_tag=BUILDER_BASE_TAG,
        builder_base_digest=BUILDER_BASE_DIGEST,
    )

    manifest = ArtifactManifest.from_mapping(payload)

    assert manifest.schema_version == 3
    assert manifest.builder_base_tag == BUILDER_BASE_TAG
    assert manifest.builder_base_digest == BUILDER_BASE_DIGEST
    assert manifest_from_json(manifest_to_json(manifest)) == manifest


@pytest.mark.parametrize(
    "overrides",
    [
        {"builder_base_tag": "ubuntu:latest"},
        {"builder_base_digest": "sha256:" + "0" * 64},
        {"builder_base_digest": BUILDER_BASE_DIGEST.removesuffix("9") + "0"},
    ],
)
def test_manifest_requires_the_exact_builder_base_identity(overrides: dict[str, object]) -> None:
    payload = manifest_payload(
        schema_version=3,
        builder_base_tag=BUILDER_BASE_TAG,
        builder_base_digest=BUILDER_BASE_DIGEST,
    )
    payload.update(overrides)

    with pytest.raises(ValueError):
        ArtifactManifest.from_mapping(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"unexpected": "value"},
        {"schema_version": 1},
        {"application": "other"},
        {"source_revision": "a" * 12},
        {"source_revision": "A" * 40},
        {"built_at": "2026-09-04T20:15:30+00:00"},
        {"built_at": "2026-09-04T20:15:30"},
        {"built_at": "2026-09-04T20:15:30.000Z"},
        {"target_os": "ubuntu24.04"},
        {"architecture": "x86_64"},
        {"otp_version": "27.3.4"},
        {"hex_version": "latest"},
        {"rebar3_version": "latest"},
        {"migrations": [{"filename": "../migration.exs", "sha256": CHECKSUM}]},
        {"migrations": [{"filename": "20260904065131_valid.exs", "sha256": "bad"}]},
    ],
)
def test_manifest_refuses_unknown_fields_and_invalid_provenance(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ArtifactManifest.from_mapping(manifest_payload(**overrides))


def test_migration_fingerprints_are_sorted_and_hash_the_file_contents(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "20260904065131_second.exs").write_bytes(b"second")
    (migrations / "20260719082112_first.exs").write_bytes(b"first")

    fingerprints = fingerprint_migrations(migrations)

    assert fingerprints == (
        MigrationFingerprint(
            filename="20260719082112_first.exs",
            sha256="a7937b64b8caa58f03721bb6bacf5c78cb235febe0e70b1b84cd99541461a08e",
        ),
        MigrationFingerprint(
            filename="20260904065131_second.exs",
            sha256="16367aacb67a4a017c8da8ab95682ccb390863780f7114dda0a0e0c55644c7c4",
        ),
    )


def test_migration_fingerprints_ignore_migration_directory_formatting_configuration(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / ".formatter.exs").write_text("[]\n", encoding="utf-8")
    (migrations / "20260904065131_create_records.exs").write_bytes(b"migration")

    fingerprints = fingerprint_migrations(migrations)

    assert fingerprints == (
        MigrationFingerprint(
            filename="20260904065131_create_records.exs",
            sha256="8a6cead4385ed4394247b71692fb729b0563f8e1bd4818a8c6c82940e9e099ba",
        ),
    )


def test_manifest_accepts_256_fingerprints_and_rejects_257(tmp_path: Path) -> None:
    filenames = tuple(f"{index:014d}_migration.exs" for index in range(256))
    migrations = [
        {"filename": filename, "sha256": CHECKSUM}
        for filename in filenames
    ]

    manifest = ArtifactManifest.from_mapping(manifest_payload(migrations=migrations))

    assert len(manifest.migrations) == 256
    with pytest.raises(ValueError, match="migration|256|supported"):
        ArtifactManifest.from_mapping(
            manifest_payload(
                migrations=[*migrations, {"filename": "99999999999999_last.exs", "sha256": CHECKSUM}]
            )
        )


def test_manifest_enforces_the_255_byte_migration_filename_bound() -> None:
    filename = "20260904065131_" + ("a" * 236) + ".exs"
    assert len(filename.encode("utf-8")) == 255
    migration = {"filename": filename, "sha256": CHECKSUM}

    assert ArtifactManifest.from_mapping(manifest_payload(migrations=[migration])).migrations[0].filename == filename
    with pytest.raises(ValueError, match="filename|long|255"):
        ArtifactManifest.from_mapping(
            manifest_payload(
                migrations=[{"filename": filename[:-4] + "aaaa.exs", "sha256": CHECKSUM}]
            )
        )


def test_manifest_serializer_enforces_its_encoded_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import taskman_ops.releases.manifests as manifests_module

    manifest = ArtifactManifest.from_mapping(manifest_payload())
    encoded_size = len(manifest_to_json(manifest).encode("ascii"))
    monkeypatch.setattr(manifests_module, "MAX_MANIFEST_BYTES", encoded_size - 1)

    with pytest.raises(ValueError, match="oversized|size|manifest"):
        manifest_to_json(manifest)


def test_manifest_reader_rejects_an_oversized_encoded_input_with_padding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import taskman_ops.releases.manifests as manifests_module

    payload = manifest_payload()
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    canonical_size = len((encoded + "\n").encode("utf-8"))
    monkeypatch.setattr(manifests_module, "MAX_MANIFEST_BYTES", canonical_size)

    assert manifest_from_json(encoded).application == "taskman"

    with pytest.raises(ValueError, match="oversized|size|manifest"):
        manifest_from_json(encoded + (" " * 2))


@pytest.mark.parametrize("entry_kind", ["file", "directory", "link", "formatter_link"])
def test_migration_fingerprints_reject_unrecognized_directory_entries(tmp_path: Path, entry_kind: str) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "20260904065131_create_records.exs").write_bytes(b"migration")

    if entry_kind == "file":
        (migrations / "notes.txt").write_text("not a migration\n", encoding="utf-8")
    elif entry_kind == "directory":
        (migrations / "nested").mkdir()
    else:
        target = migrations / "20260904065132_other.exs"
        target.write_bytes(b"other")
        name = ".formatter.exs" if entry_kind == "formatter_link" else "linked.txt"
        (migrations / name).symlink_to(target.name)

    with pytest.raises(OpsError) as raised:
        fingerprint_migrations(migrations)

    assert raised.value.status is ExitStatus.INVALID


def test_verify_artifact_returns_the_detached_checksum_for_a_safe_release_layout(tmp_path: Path) -> None:
    archive = write_release_archive(tmp_path)
    manifest, checksum = write_manifest_bundle(tmp_path, archive)

    verified = verify_artifact(archive, manifest, checksum)

    assert verified.archive == archive
    assert verified.manifest.artifact_sha256 == verified.sha256
    assert verified.sha256 == sha256_file(archive)
    assert verified.checksum == checksum


@pytest.mark.parametrize(
    "checksum_text",
    [
        "not-a-checksum\n",
        f"{'A' * 64}  taskman.tar.gz\n",
        f"{'a' * 64} taskman.tar.gz\n",
        f"{'a' * 64}  ../taskman.tar.gz\n",
        f"{'a' * 64}  another.tar.gz\n",
    ],
)
def test_verify_artifact_refuses_malformed_or_mismatched_detached_checksums(
    tmp_path: Path, checksum_text: str
) -> None:
    archive = write_release_archive(tmp_path)
    manifest, checksum = write_manifest_bundle(tmp_path, archive)
    checksum.write_text(checksum_text, encoding="ascii")

    with pytest.raises(OpsError) as raised:
        verify_artifact(archive, manifest, checksum)

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize(
    "extra",
    [
        ("/outside", tarfile.REGTYPE, None),
        ("taskman/../../outside", tarfile.REGTYPE, None),
        ("other/readme", tarfile.REGTYPE, None),
        ("taskman/bin/escape", tarfile.SYMTYPE, b"../../outside"),
        ("taskman/bin/escape", tarfile.LNKTYPE, b"../outside"),
        ("taskman/device", tarfile.CHRTYPE, None),
    ],
)
def test_verify_artifact_rejects_unsafe_members_before_any_extraction(
    tmp_path: Path, extra: tuple[str, bytes, bytes | None]
) -> None:
    archive = write_release_archive(tmp_path, extra=extra)
    manifest, checksum = write_manifest_bundle(tmp_path, archive)

    with pytest.raises(OpsError) as raised:
        verify_artifact(archive, manifest, checksum)

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize(
    "extra",
    [
        ("taskman/README", tarfile.REGTYPE, None),
        ("taskman/unrecognized/nested", tarfile.REGTYPE, None),
        ("taskman/erts-17.0", tarfile.DIRTYPE, None),
    ],
)
def test_verify_artifact_rejects_extra_runtime_root_children(
    tmp_path: Path, extra: tuple[str, bytes, bytes | None]
) -> None:
    archive = write_release_archive(tmp_path, extra=extra)
    manifest, checksum = write_manifest_bundle(tmp_path, archive)

    with pytest.raises(OpsError) as raised:
        verify_artifact(archive, manifest, checksum)

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize("omit", ["taskman/bin/migrate", "taskman/lib", "taskman/erts-16.0"])
def test_verify_artifact_requires_each_runtime_directory_and_launcher(tmp_path: Path, omit: str) -> None:
    archive = write_release_archive(tmp_path, omit=omit)
    manifest, checksum = write_manifest_bundle(tmp_path, archive)

    with pytest.raises(OpsError) as raised:
        verify_artifact(archive, manifest, checksum)

    assert raised.value.status is ExitStatus.INVALID
