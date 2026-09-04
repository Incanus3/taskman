from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.manifests import (
    ArtifactManifest,
    MigrationFingerprint,
    fingerprint_migrations,
    manifest_from_json,
    manifest_to_json,
    sha256_file,
    verify_artifact,
)
from taskman_ops.releases.identifiers import build_release_id, managed_release_path, validate_release_id


REVISION = "a" * 40
RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CHECKSUM = "b" * 64


def manifest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": REVISION,
        "release_id": RELEASE_ID,
        "built_at": "2026-09-04T20:15:30Z",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "elixir_version": "1.18.3",
        "node_version": "22.22.1",
        "migrations": [
            {
                "filename": "20260904065131_install_ash_functions_extensions_1.exs",
                "sha256": CHECKSUM,
            }
        ],
        "top_level": "taskman",
    }
    payload.update(overrides)
    return payload


def write_manifest_bundle(tmp_path: Path, archive: Path, **overrides: object) -> tuple[Path, Path]:
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


def test_release_identity_is_exact_and_managed_paths_accept_only_validated_ids() -> None:
    release_id = build_release_id("0.2.0", REVISION)

    assert release_id == RELEASE_ID
    assert validate_release_id(release_id) == release_id
    assert managed_release_path(PurePosixPath("/opt/taskman/releases"), release_id) == PurePosixPath(
        f"/opt/taskman/releases/{RELEASE_ID}"
    )


@pytest.mark.parametrize(
    "value",
    [
        "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6/next",
        "../0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
        "0.2.0-AAAAAAAAAAAA-ubuntu26.04-amd64-otp27.3.4.6",
        "0.2.0-aaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
        "0.2.0-aaaaaaaaaaaa-ubuntu24.04-amd64-otp27.3.4.6",
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


@pytest.mark.parametrize(
    "overrides",
    [
        {"unexpected": "value"},
        {"schema_version": 2},
        {"application": "other"},
        {"source_revision": "a" * 12},
        {"source_revision": "A" * 40},
        {"built_at": "2026-09-04T20:15:30+00:00"},
        {"built_at": "2026-09-04T20:15:30"},
        {"target_os": "ubuntu24.04"},
        {"architecture": "x86_64"},
        {"otp_version": "27.3.4"},
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


def test_verify_artifact_returns_the_detached_checksum_for_a_safe_release_layout(tmp_path: Path) -> None:
    archive = write_release_archive(tmp_path)
    manifest, checksum = write_manifest_bundle(tmp_path, archive)

    verified = verify_artifact(archive, manifest, checksum)

    assert verified.archive == archive
    assert verified.manifest.release_id == RELEASE_ID
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


@pytest.mark.parametrize("omit", ["taskman/bin/migrate", "taskman/lib", "taskman/erts-16.0"])
def test_verify_artifact_requires_each_runtime_directory_and_launcher(tmp_path: Path, omit: str) -> None:
    archive = write_release_archive(tmp_path, omit=omit)
    manifest, checksum = write_manifest_bundle(tmp_path, archive)

    with pytest.raises(OpsError) as raised:
        verify_artifact(archive, manifest, checksum)

    assert raised.value.status is ExitStatus.INVALID
