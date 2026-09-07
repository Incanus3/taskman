"""Completed host-state records and their atomic publication helpers.

The records in this module describe facts that have already completed.  They
do not carry operation identities or a transaction protocol: an interrupted
command leaves only the physical temporary paths that the state observer can
recognize on its next invocation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

from taskman_ops.releases.identifiers import (
    RELEASE_ID_RE,
    build_release_id,
    validate_release_id,
    validate_source_revision,
)

from .paths import ManagedPaths, PathAuthorityError


MAX_RECORD_BYTES = 64 * 1024
MAX_MIGRATIONS = 256
MAX_MIGRATION_VERSIONS = 512
MIGRATION_FILENAME_RE = re.compile(r"[0-9]{14}_[a-z0-9_]+\.exs\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")


class RecordError(ValueError):
    """A malformed, unsafe, or conflicting completed record."""


CompletedRecordError = RecordError


def _exact(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if (
        not isinstance(value, Mapping)
        or set(value) != keys
        or not all(type(key) is str for key in value)
    ):
        raise RecordError(f"invalid {label}")
    return value


def _sha256(value: object, label: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        raise RecordError(f"invalid {label}")
    return value


def _release(value: object, label: str = "release identifier") -> str:
    if type(value) is not str:
        raise RecordError(f"invalid {label}")
    try:
        return validate_release_id(value)
    except ValueError as error:
        raise RecordError(f"invalid {label}") from error


def _release_for_revision(release_id: str, source_revision: str) -> None:
    match = RELEASE_ID_RE.fullmatch(release_id)
    if match is None:
        raise RecordError("invalid release identifier")
    try:
        expected = build_release_id(match.group("version"), source_revision)
    except ValueError as error:
        raise RecordError("invalid source revision") from error
    if expected != release_id:
        raise RecordError("release identity does not match source revision")


def _optional_release(value: object, label: str) -> str | None:
    return None if value is None else _release(value, label)


def _backup(value: object, label: str = "backup identifier") -> str:
    if type(value) is not str or BACKUP_ID_RE.fullmatch(value) is None:
        raise RecordError(f"invalid {label}")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z") or "T" not in value:
        raise RecordError(f"invalid {label}")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise RecordError(f"invalid {label}") from error
    if parsed.tzinfo != UTC:
        raise RecordError(f"invalid {label}")
    return parsed


def _format_timestamp(value: object, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo != UTC:
        raise RecordError(f"invalid {label}")
    if value.microsecond:
        raise RecordError(f"invalid {label}")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _migration(value: object) -> Mapping[str, str]:
    mapping = _exact(value, frozenset({"filename", "sha256"}), "migration fingerprint")
    filename, checksum = mapping["filename"], mapping["sha256"]
    if type(filename) is not str or MIGRATION_FILENAME_RE.fullmatch(filename) is None:
        raise RecordError("invalid migration filename")
    return MappingProxyType(
        {"filename": filename, "sha256": _sha256(checksum, "migration checksum")}
    )


def _migrations(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (tuple, list)) or len(value) > MAX_MIGRATIONS:
        raise RecordError("invalid migration fingerprints")
    result = tuple(_migration(item) for item in value)
    names = tuple(item["filename"] for item in result)
    if names != tuple(sorted(names)) or len(set(names)) != len(names):
        raise RecordError("migration fingerprints must be unique and sorted")
    return result


def _migration_versions(value: object) -> tuple[int, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > MAX_MIGRATION_VERSIONS:
        raise RecordError("invalid migration versions")
    if any(type(item) is not int or item < 0 for item in value):
        raise RecordError("invalid migration versions")
    result = tuple(value)
    if result != tuple(sorted(set(result))):
        raise RecordError("migration versions must be sorted and unique")
    return result


def _record_json(value: Mapping[str, object]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RecordError("record is not JSON serializable") from error
    encoded += b"\n"
    if len(encoded) > MAX_RECORD_BYTES:
        raise RecordError("record is oversized")
    return encoded


@dataclass(frozen=True)
class ReleaseRecord:
    """Provenance that an immutable release was completely installed."""

    release_id: str
    source_revision: str
    artifact_sha256: str
    migrations: tuple[Mapping[str, object], ...]

    _FIELDS = frozenset({"release_id", "source_revision", "artifact_sha256", "migrations"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "release_id", _release(self.release_id))
        try:
            object.__setattr__(self, "source_revision", validate_source_revision(self.source_revision))
        except (TypeError, ValueError) as error:
            raise RecordError("invalid source revision") from error
        _release_for_revision(self.release_id, self.source_revision)
        object.__setattr__(self, "artifact_sha256", _sha256(self.artifact_sha256, "artifact checksum"))
        object.__setattr__(self, "migrations", _migrations(self.migrations))
        _record_json(self.to_mapping())

    @classmethod
    def from_mapping(cls, value: object) -> "ReleaseRecord":
        data = _exact(value, cls._FIELDS, "release record")
        return cls(
            _release(data["release_id"]),
            data["source_revision"],  # type: ignore[arg-type]
            _sha256(data["artifact_sha256"], "artifact checksum"),
            _migrations(data["migrations"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "release_id": self.release_id,
            "source_revision": self.source_revision,
            "artifact_sha256": self.artifact_sha256,
            "migrations": [dict(item) for item in self.migrations],
        }


@dataclass(frozen=True)
class BackupRecord:
    """A validated PostgreSQL dump and the migration state it captured."""

    backup_id: str
    dump_sha256: str
    source_release_id: str
    migration_versions: tuple[int, ...]
    source_database_size_bytes: int

    _FIELDS = frozenset(
        {
            "backup_id",
            "dump_sha256",
            "source_release_id",
            "migration_versions",
            "source_database_size_bytes",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "backup_id", _backup(self.backup_id))
        object.__setattr__(self, "dump_sha256", _sha256(self.dump_sha256, "dump checksum"))
        object.__setattr__(self, "source_release_id", _release(self.source_release_id, "source release identifier"))
        object.__setattr__(self, "migration_versions", _migration_versions(self.migration_versions))
        if type(self.source_database_size_bytes) is not int or self.source_database_size_bytes < 0:
            raise RecordError("invalid source database size")
        _record_json(self.to_mapping())

    @classmethod
    def from_mapping(cls, value: object) -> "BackupRecord":
        data = _exact(value, cls._FIELDS, "backup record")
        return cls(
            _backup(data["backup_id"]),
            _sha256(data["dump_sha256"], "dump checksum"),
            _release(data["source_release_id"], "source release identifier"),
            _migration_versions(data["migration_versions"]),
            data["source_database_size_bytes"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "backup_id": self.backup_id,
            "dump_sha256": self.dump_sha256,
            "source_release_id": self.source_release_id,
            "migration_versions": list(self.migration_versions),
            "source_database_size_bytes": self.source_database_size_bytes,
        }


@dataclass(frozen=True)
class SelectionRecord:
    """One successful atomic selection of an installed release."""

    release_id: str
    previous_release_id: str | None
    backup_id: str | None
    selected_at: datetime

    _FIELDS = frozenset({"release_id", "previous_release_id", "backup_id", "selected_at"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "release_id", _release(self.release_id))
        object.__setattr__(self, "previous_release_id", _optional_release(self.previous_release_id, "previous release identifier"))
        object.__setattr__(self, "backup_id", None if self.backup_id is None else _backup(self.backup_id))
        _format_timestamp(self.selected_at, "selection time")
        if self.previous_release_id == self.release_id:
            raise RecordError("selection must change release")
        _record_json(self.to_mapping())

    @classmethod
    def from_mapping(cls, value: object) -> "SelectionRecord":
        data = _exact(value, cls._FIELDS, "selection record")
        return cls(
            _release(data["release_id"]),
            _optional_release(data["previous_release_id"], "previous release identifier"),
            None if data["backup_id"] is None else _backup(data["backup_id"]),
            _timestamp(data["selected_at"], "selection time"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "release_id": self.release_id,
            "previous_release_id": self.previous_release_id,
            "backup_id": self.backup_id,
            "selected_at": _format_timestamp(self.selected_at, "selection time"),
        }


def selection_filename(record: SelectionRecord) -> str:
    """Derive a create-once filename without carrying an operation ID."""

    digest = hashlib.sha256(_record_json(record.to_mapping())).hexdigest()
    return f"selection-{digest}.json"


def _safe_directory(path: Path, *, owner_uid: int) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o750, parents=True, exist_ok=True)
        except OSError as error:
            raise RecordError("unable to create managed directory") from error
        details = path.lstat()
    except OSError as error:
        raise RecordError("unable to inspect managed directory") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
    ):
        raise RecordError("managed directory is unsafe")


def _safe_file(path: Path, *, owner_uid: int) -> os.stat_result:
    try:
        details = path.lstat()
    except (FileNotFoundError, OSError) as error:
        raise RecordError("managed file is absent or unreadable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
    ):
        raise RecordError("managed file is unsafe")
    return details


def _atomic_create(path: Path, payload: bytes, *, owner_uid: int) -> None:
    _safe_directory(path.parent, owner_uid=owner_uid)
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = _open_temporary(path.parent, path.name)
        temporary = path.parent / temporary_name
        with os.fdopen(descriptor, "wb", closefd=True) as target:
            descriptor = None
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        try:
            # Linking, rather than replacing, preserves create-once semantics
            # when a retry races with a prior successful publication.
            os.link(temporary, path)
        except FileExistsError as error:
            raise RecordError("completed record already exists") from error
        os.unlink(temporary)
        temporary = None
        _fsync_directory(path.parent)
    except RecordError:
        raise
    except OSError as error:
        raise RecordError("unable to publish completed record") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _open_temporary(directory: Path, stem: str) -> tuple[int, str]:
    for suffix in range(100):
        name = f".{stem}.{os.getpid()}.{suffix}.tmp"
        try:
            descriptor = os.open(
                directory / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            return descriptor, name
        except FileExistsError:
            continue
    raise RecordError("unable to allocate record temporary")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256_file(path: Path) -> str:
    """Hash a validated dump in bounded memory, regardless of its size."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise RecordError("unable to hash backup dump") from error
    return digest.hexdigest()


def _paths(paths: ManagedPaths) -> tuple[ManagedPaths, int]:
    if not isinstance(paths, ManagedPaths):
        raise TypeError("completed record writes need managed paths")
    owner_uid = os.geteuid()
    try:
        paths.validate_existing(owner_uid=owner_uid)
    except PathAuthorityError as error:
        raise RecordError(str(error)) from error
    _safe_directory(Path(paths.local(paths.install_root)), owner_uid=owner_uid)
    _safe_directory(Path(paths.local(paths.backup_root)), owner_uid=owner_uid)
    return paths, owner_uid


def write_release_manifest(paths: ManagedPaths, record: ReleaseRecord) -> None:
    """Publish one immutable release manifest at its derived release path."""

    if not isinstance(record, ReleaseRecord):
        raise TypeError("release manifest needs a ReleaseRecord")
    paths, owner_uid = _paths(paths)
    release_root = Path(paths.local(paths.release_root))
    _safe_directory(release_root, owner_uid=owner_uid)
    release_path = release_root / record.release_id
    _safe_directory(release_path, owner_uid=owner_uid)
    target = release_path / ".taskman-release.json"
    if target.exists() or target.is_symlink():
        raise RecordError("completed release manifest already exists")
    _atomic_create(target, _record_json(record.to_mapping()), owner_uid=owner_uid)


def write_backup_manifest(paths: ManagedPaths, record: BackupRecord) -> None:
    """Publish a backup manifest only beside its validated dump."""

    if not isinstance(record, BackupRecord):
        raise TypeError("backup manifest needs a BackupRecord")
    paths, owner_uid = _paths(paths)
    backup_root = Path(paths.local(paths.backup_root))
    dump = backup_root / f"{record.backup_id}.dump"
    _safe_file(dump, owner_uid=owner_uid)
    digest = _sha256_file(dump)
    if digest != record.dump_sha256:
        raise RecordError("backup dump checksum does not match completed record")
    target = backup_root / f"{record.backup_id}.json"
    if target.exists() or target.is_symlink():
        raise RecordError("completed backup manifest already exists")
    _atomic_create(target, _record_json(record.to_mapping()), owner_uid=owner_uid)


def append_selection(paths: ManagedPaths, record: SelectionRecord) -> None:
    """Create one successful selection record without replacing history."""

    if not isinstance(record, SelectionRecord):
        raise TypeError("selection history needs a SelectionRecord")
    paths, owner_uid = _paths(paths)
    selection_root = Path(paths.local(paths.selection_root))
    _safe_directory(Path(paths.local(paths.deployment_root)), owner_uid=owner_uid)
    _safe_directory(selection_root, owner_uid=owner_uid)
    target = selection_root / selection_filename(record)
    if target.exists() or target.is_symlink():
        raise RecordError("completed selection already exists")
    _atomic_create(target, _record_json(record.to_mapping()), owner_uid=owner_uid)


__all__ = [
    "BackupRecord",
    "CompletedRecordError",
    "MAX_RECORD_BYTES",
    "RecordError",
    "ReleaseRecord",
    "SelectionRecord",
    "append_selection",
    "selection_filename",
    "write_backup_manifest",
    "write_release_manifest",
]
