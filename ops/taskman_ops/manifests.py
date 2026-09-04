"""Detached release manifests, checksums, migration fingerprints, and tar inspection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import tarfile
from typing import Any

from .errors import ExitStatus, OpsError
from .releases.identifiers import (
    build_release_id,
    validate_application_version,
    validate_release_id,
    validate_source_revision,
)


SCHEMA_VERSION = 1
APPLICATION = "taskman"
TARGET_OS = "ubuntu26.04"
ARCHITECTURE = "amd64"
OTP_VERSION = "27.3.4.6"
ELIXIR_VERSION = "1.18.3"
NODE_VERSION = "22.22.1"
TOP_LEVEL = "taskman"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
MIGRATION_FILENAME_RE = re.compile(r"[0-9]{14}_[a-z0-9_]+\.exs\Z")
_CHECKSUM_LINE_RE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]*)\n\Z")
_ERTS_DIRECTORY_RE = re.compile(r"taskman/erts-[0-9][A-Za-z0-9._-]*\Z")
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "application",
        "application_version",
        "source_revision",
        "release_id",
        "built_at",
        "target_os",
        "architecture",
        "otp_version",
        "elixir_version",
        "node_version",
        "migrations",
        "top_level",
    }
)
_MIGRATION_FIELDS = frozenset({"filename", "sha256"})
_REQUIRED_DIRECTORIES = frozenset({"taskman", "taskman/bin", "taskman/lib", "taskman/releases"})
_REQUIRED_LAUNCHERS = frozenset(
    {
        "taskman/bin/taskman",
        "taskman/bin/server",
        "taskman/bin/migrate",
        "taskman/bin/create-admin",
    }
)


def _artifact_error(message: str) -> OpsError:
    return OpsError(
        status=ExitStatus.INVALID,
        stage="artifact",
        message=message,
        changed=False,
        next_action="inspect the release artifact and retry with a verified archive",
    )


def _require_exact_keys(value: object, expected: frozenset[str], *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected or not all(isinstance(key, str) for key in value):
        raise ValueError(f"invalid {label} fields")
    return value


def _parse_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {label} SHA-256")
    return value


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise ValueError("build timestamp must be UTC")
    try:
        result = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise ValueError("invalid build timestamp") from exc
    if result.tzinfo != UTC:
        raise ValueError("build timestamp must be UTC")
    return result


def _format_utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo != UTC:
        raise ValueError("build timestamp must be UTC")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class MigrationFingerprint:
    """One source migration filename paired with its streamed content digest."""

    filename: str
    sha256: str

    @classmethod
    def from_mapping(cls, value: object) -> MigrationFingerprint:
        mapping = _require_exact_keys(value, _MIGRATION_FIELDS, label="migration")
        filename = mapping["filename"]
        if not isinstance(filename, str) or MIGRATION_FILENAME_RE.fullmatch(filename) is None:
            raise ValueError("invalid migration filename")
        return cls(filename=filename, sha256=_parse_sha256(mapping["sha256"], label="migration"))

    def to_mapping(self) -> dict[str, str]:
        # Re-validate values even though frozen dataclasses are normally
        # constructed from trusted code: this is also a stable serialization
        # boundary for future consumers.
        return self.from_mapping({"filename": self.filename, "sha256": self.sha256}).__dict__.copy()


@dataclass(frozen=True)
class ArtifactManifest:
    """The exact, schema-versioned provenance for one detached release archive."""

    schema_version: int
    application: str
    application_version: str
    source_revision: str
    release_id: str
    built_at: datetime
    target_os: str
    architecture: str
    otp_version: str
    elixir_version: str
    node_version: str
    migrations: tuple[MigrationFingerprint, ...]
    top_level: str

    @classmethod
    def from_mapping(cls, value: object) -> ArtifactManifest:
        mapping = _require_exact_keys(value, _MANIFEST_FIELDS, label="artifact manifest")
        schema_version = mapping["schema_version"]
        if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported artifact manifest schema")
        if mapping["application"] != APPLICATION:
            raise ValueError("unsupported application")
        application_version = validate_application_version(mapping["application_version"])
        source_revision = validate_source_revision(mapping["source_revision"])
        release_id = validate_release_id(mapping["release_id"])
        if release_id != build_release_id(application_version, source_revision):
            raise ValueError("release identity does not match source provenance")
        if mapping["target_os"] != TARGET_OS or mapping["architecture"] != ARCHITECTURE:
            raise ValueError("unsupported artifact target")
        if (
            mapping["otp_version"] != OTP_VERSION
            or mapping["elixir_version"] != ELIXIR_VERSION
            or mapping["node_version"] != NODE_VERSION
        ):
            raise ValueError("unsupported artifact toolchain")
        if mapping["top_level"] != TOP_LEVEL:
            raise ValueError("unexpected artifact top-level")
        migrations_value = mapping["migrations"]
        if not isinstance(migrations_value, list):
            raise ValueError("migrations must be a list")
        migrations = tuple(MigrationFingerprint.from_mapping(item) for item in migrations_value)
        if tuple(fingerprint.filename for fingerprint in migrations) != tuple(
            sorted(fingerprint.filename for fingerprint in migrations)
        ) or len({fingerprint.filename for fingerprint in migrations}) != len(migrations):
            raise ValueError("migration fingerprints must be unique and sorted")
        return cls(
            schema_version=schema_version,
            application=APPLICATION,
            application_version=application_version,
            source_revision=source_revision,
            release_id=release_id,
            built_at=_parse_utc_timestamp(mapping["built_at"]),
            target_os=TARGET_OS,
            architecture=ARCHITECTURE,
            otp_version=OTP_VERSION,
            elixir_version=ELIXIR_VERSION,
            node_version=NODE_VERSION,
            migrations=migrations,
            top_level=TOP_LEVEL,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "application": self.application,
            "application_version": self.application_version,
            "source_revision": self.source_revision,
            "release_id": self.release_id,
            "built_at": _format_utc_timestamp(self.built_at),
            "target_os": self.target_os,
            "architecture": self.architecture,
            "otp_version": self.otp_version,
            "elixir_version": self.elixir_version,
            "node_version": self.node_version,
            "migrations": [fingerprint.to_mapping() for fingerprint in self.migrations],
            "top_level": self.top_level,
        }


@dataclass(frozen=True)
class VerifiedArtifact:
    """A detached archive whose checksum, manifest, and member layout agree."""

    archive: Path
    manifest_path: Path
    checksum: Path
    sha256: str
    manifest: ArtifactManifest


def sha256_file(path: Path) -> str:
    """Stream a file's bytes through SHA-256 without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_migrations(directory: Path) -> tuple[MigrationFingerprint, ...]:
    """Fingerprint the complete migration set in stable filename order."""

    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise _artifact_error("unable to read migrations") from None
    fingerprints: list[MigrationFingerprint] = []
    for path in entries:
        if path.name == ".formatter.exs":
            continue
        if path.suffix != ".exs":
            continue
        if path.is_symlink() or not path.is_file() or MIGRATION_FILENAME_RE.fullmatch(path.name) is None:
            raise _artifact_error("invalid migration source")
        try:
            digest = sha256_file(path)
        except OSError:
            raise _artifact_error("unable to read migration source") from None
        fingerprints.append(MigrationFingerprint(filename=path.name, sha256=digest))
    return tuple(fingerprints)


def manifest_to_json(manifest: ArtifactManifest) -> str:
    """Serialize one already validated manifest deterministically."""

    validated = ArtifactManifest.from_mapping(manifest.to_mapping())
    return json.dumps(validated.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n"


def manifest_from_json(value: str) -> ArtifactManifest:
    """Parse one exact manifest JSON document without accepting extra fields."""

    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid artifact manifest JSON") from exc
    return ArtifactManifest.from_mapping(parsed)


def _read_manifest(path: Path) -> ArtifactManifest:
    try:
        return manifest_from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise _artifact_error("invalid artifact manifest") from None


def _read_detached_checksum(path: Path, archive: Path) -> str:
    try:
        line = path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise _artifact_error("invalid detached artifact checksum") from None
    match = _CHECKSUM_LINE_RE.fullmatch(line)
    if match is None or match.group(2) != archive.name:
        raise _artifact_error("invalid detached artifact checksum")
    return match.group(1)


def _member_parts(name: object) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise _artifact_error("unsafe archive member")
    parts = tuple(name.split("/"))
    if any(part in {"", ".", ".."} for part in parts) or parts[0] != TOP_LEVEL:
        raise _artifact_error("unsafe archive member")
    return parts


def _symlink_target(parts: tuple[str, ...], linkname: object) -> tuple[str, ...]:
    if not isinstance(linkname, str) or not linkname or linkname.startswith("/") or "\\" in linkname:
        raise _artifact_error("unsafe archive link")
    target = list(parts[:-1])
    for part in linkname.split("/"):
        if part in {"", "."}:
            raise _artifact_error("unsafe archive link")
        if part == "..":
            if len(target) <= 1:
                raise _artifact_error("unsafe archive link")
            target.pop()
        else:
            target.append(part)
    if not target or target[0] != TOP_LEVEL:
        raise _artifact_error("unsafe archive link")
    return tuple(target)


def _hardlink_target(linkname: object) -> tuple[str, ...]:
    return _member_parts(linkname)


def _inspect_archive(path: Path) -> None:
    try:
        archive = tarfile.open(path, mode="r:*")
    except (OSError, tarfile.TarError):
        raise _artifact_error("invalid release archive") from None
    with archive:
        try:
            members = archive.getmembers()
        except tarfile.TarError:
            raise _artifact_error("invalid release archive") from None
        if not members:
            raise _artifact_error("release archive is empty")
        names: set[str] = set()
        directories: set[str] = set()
        files: dict[str, tarfile.TarInfo] = {}
        erts_directories: set[str] = set()
        for member in members:
            parts = _member_parts(member.name)
            if member.name in names:
                raise _artifact_error("duplicate archive member")
            names.add(member.name)
            if member.isdev() or member.isfifo() or not (member.isdir() or member.isreg() or member.issym() or member.islnk()):
                raise _artifact_error("unsupported archive member")
            if member.mode & 0o022:
                raise _artifact_error("archive member is group or world writable")
            if member.issym():
                _symlink_target(parts, member.linkname)
            elif member.islnk():
                _hardlink_target(member.linkname)
            if member.isdir():
                directories.add(member.name)
                if _ERTS_DIRECTORY_RE.fullmatch(member.name) is not None:
                    erts_directories.add(member.name)
            elif member.isreg():
                files[member.name] = member
        if not _REQUIRED_DIRECTORIES <= directories or not erts_directories:
            raise _artifact_error("release archive has an incomplete runtime layout")
        if not _REQUIRED_LAUNCHERS <= files.keys():
            raise _artifact_error("release archive is missing a launcher")
        if any(files[launcher].mode & 0o111 == 0 for launcher in _REQUIRED_LAUNCHERS):
            raise _artifact_error("release launcher is not executable")


def verify_artifact(archive: Path, manifest: Path, checksum: Path) -> VerifiedArtifact:
    """Verify detached provenance and every tar member without extracting it."""

    archive = Path(archive)
    manifest = Path(manifest)
    checksum = Path(checksum)
    if not archive.is_file() or not manifest.is_file() or not checksum.is_file():
        raise _artifact_error("artifact input is missing")
    expected_checksum = _read_detached_checksum(checksum, archive)
    try:
        actual_checksum = sha256_file(archive)
    except OSError:
        raise _artifact_error("unable to read release archive") from None
    if actual_checksum != expected_checksum:
        raise _artifact_error("release archive checksum does not match")
    artifact_manifest = _read_manifest(manifest)
    _inspect_archive(archive)
    return VerifiedArtifact(
        archive=archive,
        manifest_path=manifest,
        checksum=checksum,
        sha256=actual_checksum,
        manifest=artifact_manifest,
    )


__all__ = [
    "APPLICATION",
    "ARCHITECTURE",
    "ArtifactManifest",
    "ELIXIR_VERSION",
    "MigrationFingerprint",
    "NODE_VERSION",
    "OTP_VERSION",
    "SCHEMA_VERSION",
    "TARGET_OS",
    "TOP_LEVEL",
    "VerifiedArtifact",
    "fingerprint_migrations",
    "manifest_from_json",
    "manifest_to_json",
    "sha256_file",
    "verify_artifact",
]
