"""Validated, durable lifecycle records for the managed Taskman host.

The records are deliberately separate from release directories.  A release
directory contains executable code; these root-owned JSON documents describe
which validated releases and backups the controller is allowed to select.
Directory names only locate a record.  The record's validated metadata is the
authority for every release and backup identifier.
"""

from __future__ import annotations

from collections.abc import Mapping
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from ..errors import ExitStatus, OpsError
from ..manifests import MigrationFingerprint
from .identifiers import validate_application_version, validate_release_id
from .remote_adoption import REMOTE_ADOPTION_TRANSACTION
from .remote_locking import REMOTE_ATOMIC_WRITE
from .remote_snapshot import REMOTE_SNAPSHOT_TRANSACTION

if TYPE_CHECKING:
    from ..remote import CommandResult, Remote


SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_ACTIVATION_ID_RE = re.compile(r"activation-[0-9a-f]{32}\Z")
_DATABASE_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required", "adopted"})
_BACKUP_REASONS = frozenset({"scheduled", "pre-deploy", "pre-rollback", "pre-restore"})
_UNKNOWN = "unknown"
_REMOTE_OPERATION_RE = re.compile(r"[a-z][a-z-]{0,63}\Z")


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "lifecycle-records",
        message,
        changed=False,
        next_action="inspect the managed lifecycle state and resolve the contradiction",
    )


def _exact_mapping(value: object, expected: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected or not all(isinstance(key, str) for key in value):
        raise ValueError(f"invalid {label} fields")
    return value


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise ValueError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise ValueError(f"invalid {label}") from exc
    if parsed.tzinfo != UTC:
        raise ValueError(f"{label} must be a UTC timestamp")
    return parsed


def _format_utc(value: datetime, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo != UTC:
        raise ValueError(f"{label} must be a UTC timestamp")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_or_none(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _optional_release_id(value: object, label: str) -> str | None:
    if value is None:
        return None
    try:
        return validate_release_id(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ValueError(f"invalid {label}") from exc


def _optional_backup_id(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _BACKUP_ID_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _policy(value: object) -> str:
    if not isinstance(value, str) or value not in _POLICIES:
        raise ValueError("invalid migration policy")
    return value


def _absolute_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ValueError(f"invalid {label}")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError(f"invalid {label}")
    return path


def _under(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path != root and path.is_relative_to(root)


@dataclass(frozen=True)
class ReleaseRecord:
    """One immutable installed-release identity and its first activation data."""

    schema_version: Literal[1]
    release_id: str
    artifact_sha256: str | None
    installed_at: datetime
    activated_at: datetime | None
    previous_release_id: str | None
    backup_id: str | None
    migration_policy: Literal["no-change", "backward-compatible", "restore-required", "adopted"]

    _FIELDS = frozenset(
        {
            "schema_version",
            "release_id",
            "artifact_sha256",
            "installed_at",
            "activated_at",
            "previous_release_id",
            "backup_id",
            "migration_policy",
        }
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported release record schema")
        validate_release_id(self.release_id)
        _sha256_or_none(self.artifact_sha256, "artifact SHA-256")
        _format_utc(self.installed_at, "installation time")
        if self.activated_at is not None:
            _format_utc(self.activated_at, "activation time")
        _optional_release_id(self.previous_release_id, "previous release identifier")
        _optional_backup_id(self.backup_id, "backup identifier")
        policy = _policy(self.migration_policy)
        if policy == "adopted" and self.artifact_sha256 is not None:
            raise ValueError("adopted release artifact checksum must be unavailable")

    @classmethod
    def from_mapping(cls, value: object) -> ReleaseRecord:
        data = _exact_mapping(value, cls._FIELDS, "release record")
        activated = data["activated_at"]
        return cls(
            schema_version=data["schema_version"],  # type: ignore[arg-type]
            release_id=validate_release_id(data["release_id"]),  # type: ignore[arg-type]
            artifact_sha256=_sha256_or_none(data["artifact_sha256"], "artifact SHA-256"),
            installed_at=_utc(data["installed_at"], "installation time"),
            activated_at=None if activated is None else _utc(activated, "activation time"),
            previous_release_id=_optional_release_id(data["previous_release_id"], "previous release identifier"),
            backup_id=_optional_backup_id(data["backup_id"], "backup identifier"),
            migration_policy=_policy(data["migration_policy"]),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": SCHEMA_VERSION,
            "release_id": self.release_id,
            "artifact_sha256": self.artifact_sha256,
            "installed_at": _format_utc(self.installed_at, "installation time"),
            "activated_at": None if self.activated_at is None else _format_utc(self.activated_at, "activation time"),
            "previous_release_id": self.previous_release_id,
            "backup_id": self.backup_id,
            "migration_policy": self.migration_policy,
        }


@dataclass(frozen=True)
class ActivationRecord:
    """An append-only edge in the release activation history."""

    schema_version: Literal[1]
    activation_id: str
    previous_release_id: str | None
    candidate_release_id: str
    activated_at: datetime
    backup_id: str | None
    migration_policy: Literal["no-change", "backward-compatible", "restore-required", "adopted"]

    _FIELDS = frozenset(
        {
            "schema_version",
            "activation_id",
            "previous_release_id",
            "candidate_release_id",
            "activated_at",
            "backup_id",
            "migration_policy",
        }
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported activation record schema")
        if not isinstance(self.activation_id, str) or _ACTIVATION_ID_RE.fullmatch(self.activation_id) is None:
            raise ValueError("invalid activation identifier")
        previous = _optional_release_id(self.previous_release_id, "previous release identifier")
        candidate = validate_release_id(self.candidate_release_id)
        if previous == candidate:
            raise ValueError("activation must select a different release")
        _format_utc(self.activated_at, "activation time")
        _optional_backup_id(self.backup_id, "backup identifier")
        _policy(self.migration_policy)

    @classmethod
    def from_mapping(cls, value: object) -> ActivationRecord:
        data = _exact_mapping(value, cls._FIELDS, "activation record")
        return cls(
            schema_version=data["schema_version"],  # type: ignore[arg-type]
            activation_id=data["activation_id"],  # type: ignore[arg-type]
            previous_release_id=_optional_release_id(data["previous_release_id"], "previous release identifier"),
            candidate_release_id=validate_release_id(data["candidate_release_id"]),  # type: ignore[arg-type]
            activated_at=_utc(data["activated_at"], "activation time"),
            backup_id=_optional_backup_id(data["backup_id"], "backup identifier"),
            migration_policy=_policy(data["migration_policy"]),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": SCHEMA_VERSION,
            "activation_id": self.activation_id,
            "previous_release_id": self.previous_release_id,
            "candidate_release_id": self.candidate_release_id,
            "activated_at": _format_utc(self.activated_at, "activation time"),
            "backup_id": self.backup_id,
            "migration_policy": self.migration_policy,
        }


@dataclass(frozen=True)
class BackupRecord:
    """Metadata for one validated custom-format PostgreSQL dump."""

    schema_version: Literal[1]
    backup_id: str
    created_at: datetime
    size_bytes: int
    source_database_size_bytes: int
    database: str
    current_release_id: str | None
    candidate_release_id: str | None
    reason: Literal["scheduled", "pre-deploy", "pre-rollback", "pre-restore"]
    validated: bool
    dump_path: PurePosixPath

    _FIELDS = frozenset(
        {
            "schema_version",
            "backup_id",
            "created_at",
            "size_bytes",
            "source_database_size_bytes",
            "database",
            "current_release_id",
            "candidate_release_id",
            "reason",
            "validated",
            "dump_path",
        }
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported backup record schema")
        if not isinstance(self.backup_id, str) or _BACKUP_ID_RE.fullmatch(self.backup_id) is None:
            raise ValueError("invalid backup identifier")
        _format_utc(self.created_at, "creation time")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("invalid dump size")
        if type(self.source_database_size_bytes) is not int or self.source_database_size_bytes <= 0:
            raise ValueError("invalid source database size")
        if not isinstance(self.database, str) or _DATABASE_RE.fullmatch(self.database) is None:
            raise ValueError("invalid database")
        _optional_release_id(self.current_release_id, "current release identifier")
        _optional_release_id(self.candidate_release_id, "candidate release identifier")
        if not isinstance(self.reason, str) or self.reason not in _BACKUP_REASONS:
            raise ValueError("invalid backup reason")
        if type(self.validated) is not bool:
            raise ValueError("backup validation must be boolean")
        if not isinstance(self.dump_path, PurePosixPath):
            raise ValueError("dump path must be a POSIX path")
        _absolute_path(self.dump_path.as_posix(), "dump path")

    @classmethod
    def from_mapping(cls, value: object) -> BackupRecord:
        data = _exact_mapping(value, cls._FIELDS, "backup record")
        return cls(
            schema_version=data["schema_version"],  # type: ignore[arg-type]
            backup_id=data["backup_id"],  # type: ignore[arg-type]
            created_at=_utc(data["created_at"], "creation time"),
            size_bytes=data["size_bytes"],  # type: ignore[arg-type]
            source_database_size_bytes=data["source_database_size_bytes"],  # type: ignore[arg-type]
            database=data["database"],  # type: ignore[arg-type]
            current_release_id=_optional_release_id(data["current_release_id"], "current release identifier"),
            candidate_release_id=_optional_release_id(data["candidate_release_id"], "candidate release identifier"),
            reason=data["reason"],  # type: ignore[arg-type]
            validated=data["validated"],  # type: ignore[arg-type]
            dump_path=_absolute_path(data["dump_path"], "dump path"),
        )

    def to_mapping(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": SCHEMA_VERSION,
            "backup_id": self.backup_id,
            "created_at": _format_utc(self.created_at, "creation time"),
            "size_bytes": self.size_bytes,
            "source_database_size_bytes": self.source_database_size_bytes,
            "database": self.database,
            "current_release_id": self.current_release_id,
            "candidate_release_id": self.candidate_release_id,
            "reason": self.reason,
            "validated": self.validated,
            "dump_path": self.dump_path.as_posix(),
        }


@dataclass(frozen=True)
class AdoptionRecord:
    """Evidence recorded once for a confirmed manual-installation baseline."""

    schema_version: Literal[1]
    release_id: str
    adopted_at: datetime
    release_path: PurePosixPath
    content_sha256: str
    application_version: str
    source_revision: Literal["unknown"]
    artifact_sha256: Literal["unknown"]
    migrations: tuple[MigrationFingerprint, ...]

    _FIELDS = frozenset(
        {
            "schema_version",
            "release_id",
            "adopted_at",
            "release_path",
            "content_sha256",
            "application_version",
            "source_revision",
            "artifact_sha256",
            "migrations",
        }
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported adoption record schema")
        validate_release_id(self.release_id)
        _format_utc(self.adopted_at, "adoption time")
        if not isinstance(self.release_path, PurePosixPath):
            raise ValueError("release path must be a POSIX path")
        _absolute_path(self.release_path.as_posix(), "release path")
        _sha256_or_none(self.content_sha256, "content SHA-256")
        validate_application_version(self.application_version)
        if self.source_revision != _UNKNOWN or self.artifact_sha256 != _UNKNOWN:
            raise ValueError("manual adoption provenance must be unknown")
        if tuple(fingerprint.filename for fingerprint in self.migrations) != tuple(
            sorted(fingerprint.filename for fingerprint in self.migrations)
        ) or len({fingerprint.filename for fingerprint in self.migrations}) != len(self.migrations):
            raise ValueError("adoption migrations must be unique and sorted")

    @classmethod
    def from_mapping(cls, value: object) -> AdoptionRecord:
        data = _exact_mapping(value, cls._FIELDS, "adoption record")
        migrations_data = data["migrations"]
        if not isinstance(migrations_data, list):
            raise ValueError("adoption migrations must be a list")
        return cls(
            schema_version=data["schema_version"],  # type: ignore[arg-type]
            release_id=validate_release_id(data["release_id"]),  # type: ignore[arg-type]
            adopted_at=_utc(data["adopted_at"], "adoption time"),
            release_path=_absolute_path(data["release_path"], "release path"),
            content_sha256=_sha256_or_none(data["content_sha256"], "content SHA-256") or "",
            application_version=validate_application_version(data["application_version"]),  # type: ignore[arg-type]
            source_revision=data["source_revision"],  # type: ignore[arg-type]
            artifact_sha256=data["artifact_sha256"],  # type: ignore[arg-type]
            migrations=tuple(MigrationFingerprint.from_mapping(item) for item in migrations_data),
        )

    def to_mapping(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": SCHEMA_VERSION,
            "release_id": self.release_id,
            "adopted_at": _format_utc(self.adopted_at, "adoption time"),
            "release_path": self.release_path.as_posix(),
            "content_sha256": self.content_sha256,
            "application_version": self.application_version,
            "source_revision": _UNKNOWN,
            "artifact_sha256": _UNKNOWN,
            "migrations": [fingerprint.to_mapping() for fingerprint in self.migrations],
        }


@dataclass(frozen=True)
class ManualAdoptionCandidate:
    """Exact nonmutating authority for a manual release awaiting adoption."""

    schema_version: Literal[1]
    release_id: str
    release_path: PurePosixPath
    content_sha256: str
    application_version: str
    migrations: tuple[MigrationFingerprint, ...]

    _FIELDS = frozenset(
        {
            "schema_version",
            "release_id",
            "release_path",
            "content_sha256",
            "application_version",
            "migrations",
        }
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported manual adoption candidate schema")
        validate_release_id(self.release_id)
        if not isinstance(self.release_path, PurePosixPath):
            raise ValueError("release path must be a POSIX path")
        _absolute_path(self.release_path.as_posix(), "release path")
        if _sha256_or_none(self.content_sha256, "content SHA-256") is None:
            raise ValueError("invalid content SHA-256")
        validate_application_version(self.application_version)
        filenames = tuple(fingerprint.filename for fingerprint in self.migrations)
        if filenames != tuple(sorted(filenames)) or len(set(filenames)) != len(filenames):
            raise ValueError("manual adoption candidate migrations must be unique and sorted")

    @classmethod
    def from_mapping(cls, value: object) -> ManualAdoptionCandidate:
        data = _exact_mapping(value, cls._FIELDS, "manual adoption candidate")
        migrations_data = data["migrations"]
        if not isinstance(migrations_data, list):
            raise ValueError("manual adoption candidate migrations must be a list")
        return cls(
            schema_version=data["schema_version"],  # type: ignore[arg-type]
            release_id=validate_release_id(data["release_id"]),  # type: ignore[arg-type]
            release_path=_absolute_path(data["release_path"], "release path"),
            content_sha256=_sha256_or_none(data["content_sha256"], "content SHA-256") or "",
            application_version=validate_application_version(data["application_version"]),  # type: ignore[arg-type]
            migrations=tuple(MigrationFingerprint.from_mapping(item) for item in migrations_data),
        )

    def to_mapping(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": SCHEMA_VERSION,
            "release_id": self.release_id,
            "release_path": self.release_path.as_posix(),
            "content_sha256": self.content_sha256,
            "application_version": self.application_version,
            "migrations": [fingerprint.to_mapping() for fingerprint in self.migrations],
        }


@dataclass(frozen=True)
class LifecycleRecords:
    releases: tuple[ReleaseRecord, ...]
    activations: tuple[ActivationRecord, ...]
    backups: tuple[BackupRecord, ...]
    adoptions: tuple[AdoptionRecord, ...]
    warnings: tuple[str, ...]

    @property
    def current_release_id(self) -> str | None:
        return self.activations[-1].candidate_release_id if self.activations else None


@dataclass(frozen=True)
class LifecycleStore:
    """The root-managed on-host record tree.

    ``owner_uid`` defaults to root for production.  Tests pass their current
    UID to exercise ownership enforcement without pretending to be root.
    """

    deployment_root: Path
    managed_root: Path
    release_root: Path
    backup_root: Path
    owner_uid: int = 0

    @property
    def releases_directory(self) -> Path:
        return self.deployment_root / "releases"

    @property
    def activations_directory(self) -> Path:
        return self.deployment_root / "activations"

    @property
    def backups_directory(self) -> Path:
        return self.deployment_root / "backups"

    @property
    def adoptions_directory(self) -> Path:
        return self.deployment_root / "adoptions"

    @property
    def current_link(self) -> Path:
        return self.managed_root / "current"

    def release_path(self, release_id: str) -> Path:
        return self.releases_directory / f"release-{validate_release_id(release_id)}.json"

    def activation_path(self, activation_id: str) -> Path:
        if _ACTIVATION_ID_RE.fullmatch(activation_id) is None:
            raise ValueError("invalid activation identifier")
        return self.activations_directory / f"{activation_id}.json"

    def backup_path(self, backup_id: str) -> Path:
        if _BACKUP_ID_RE.fullmatch(backup_id) is None:
            raise ValueError("invalid backup identifier")
        return self.backups_directory / f"{backup_id}.json"

    def adoption_path(self, release_id: str) -> Path:
        return self.adoptions_directory / f"adoption-{validate_release_id(release_id)}.json"

    def write_release(self, record: ReleaseRecord) -> None:
        self._write(self.release_path(record.release_id), record.to_mapping())

    def write_activation(self, record: ActivationRecord) -> None:
        self._write(self.activation_path(record.activation_id), record.to_mapping())

    def write_backup(self, record: BackupRecord) -> None:
        root = PurePosixPath(self.backup_root.as_posix())
        if not _under(record.dump_path, root):
            raise _safety("backup dump path is outside the managed backup root")
        self._write(self.backup_path(record.backup_id), record.to_mapping())

    def write_adoption(self, record: AdoptionRecord) -> None:
        root = PurePosixPath(self.release_root.as_posix())
        if not _under(record.release_path, root):
            raise _safety("adopted release path is outside the managed release root")
        self._write(self.adoption_path(record.release_id), record.to_mapping())

    def _write(self, path: Path, payload: Mapping[str, object]) -> None:
        directory = path.parent
        self._ensure_directory(directory)
        if path.exists() or path.is_symlink():
            raise _safety("lifecycle record identifier already exists")
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        temporary = directory / f".{path.name}.{uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, self.owner_uid, -1)
            _write_all(descriptor, data.encode("utf-8"))
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            # ``rename`` replaces an existing destination on POSIX.  A hard
            # link into the same directory publishes the completed inode only
            # when the final name is still absent, so a concurrent controller
            # cannot clobber a record that won the race.
            os.link(temporary, path, follow_symlinks=False)
            self._fsync_directory(directory)
        except OpsError:
            raise
        except OSError:
            raise _safety("unable to atomically write lifecycle record") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _ensure_directory(self, directory: Path) -> None:
        if directory.exists() or directory.is_symlink():
            _safe_directory(directory, self.owner_uid)
            return
        try:
            self._ensure_deployment_root()
            directory.mkdir()
            os.chmod(directory, 0o750)
            os.chown(directory, self.owner_uid, -1)
            _safe_directory(directory, self.owner_uid)
        except FileExistsError:
            _safe_directory(directory, self.owner_uid)
        except OpsError:
            raise
        except OSError:
            raise _safety("unable to prepare lifecycle record directory") from None

    def _ensure_deployment_root(self) -> None:
        root = self.deployment_root
        if root.exists() or root.is_symlink():
            _safe_directory(root, self.owner_uid)
            return
        try:
            root.mkdir(parents=True)
            os.chmod(root, 0o750)
            os.chown(root, self.owner_uid, -1)
            _safe_directory(root, self.owner_uid)
        except FileExistsError:
            _safe_directory(root, self.owner_uid)
        except OpsError:
            raise
        except OSError:
            raise _safety("unable to prepare lifecycle record root") from None

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    """Handle a short ``write(2)`` without publishing a truncated record."""

    pending = memoryview(data)
    while pending:
        written = os.write(descriptor, pending)
        if written <= 0:
            raise OSError("short lifecycle record write")
        pending = pending[written:]


# Compatibility aliases preserve the module-level shell constants used by callers and
# focused subprocess tests. Their implementations live with the common remote
# lock and framing machinery in the focused remote transaction modules.
_REMOTE_ATOMIC_WRITE = REMOTE_ATOMIC_WRITE
_REMOTE_SNAPSHOT_TRANSACTION = REMOTE_SNAPSHOT_TRANSACTION
_REMOTE_ADOPTION_TRANSACTION = REMOTE_ADOPTION_TRANSACTION


def _record_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("remote record collection must be a list")
    return value


def _warning_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("remote lifecycle warnings must be strings")
    return value


def raise_remote_lifecycle_failure(result: object) -> None:
    """Raise the stable error for a failed remote lifecycle transaction.

    Every controller capability that consumes the shared lifecycle lock uses
    this one strict holder parser.  A lock holder is itself untrusted remote
    input, so accepting a different schema in a new capability would turn a
    genuine contention into an unsafe or misleading controller result.
    """
    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", "")
    if returncode == ExitStatus.LOCKED:
        try:
            payload = _exact_mapping(json.loads(stdout), frozenset({"schema_version", "holder"}), "lock holder")
            holder = _exact_mapping(payload["holder"], frozenset({"operation", "pid", "started_at", "mode"}), "lock holder")
            if (
                payload["schema_version"] != SCHEMA_VERSION
                or not isinstance(holder["operation"], str)
                or _REMOTE_OPERATION_RE.fullmatch(holder["operation"]) is None
            ):
                raise ValueError
            if type(holder["pid"]) is not int or holder["pid"] <= 0 or holder["mode"] not in {"shared", "exclusive"}:
                raise ValueError
            _utc(holder["started_at"], "lock holder start time")
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _safety("remote lifecycle lock is contended without valid holder metadata") from None
        raise OpsError(
            ExitStatus.LOCKED,
            "lifecycle-lock",
            f"lifecycle lock is held by {holder['operation']} (pid {holder['pid']}, {holder['started_at']}, {holder['mode']})",
            changed=False,
            next_action="wait for the named lifecycle operation to finish before retrying",
        )
    raise _safety("remote lifecycle read was refused")


# Kept as a private compatibility alias while callers migrate to the explicit
# shared boundary above.
_raise_remote_lifecycle_failure = raise_remote_lifecycle_failure


def _validate_remote_records(records: LifecycleRecords, store: RemoteLifecycleStore) -> None:
    _validate_records(records, store)  # type: ignore[arg-type]


def _validate_remote_current_target(
    records: LifecycleRecords,
    current_target: object,
    store: RemoteLifecycleStore,
) -> None:
    if current_target is None:
        if records.current_release_id is not None:
            raise _safety("remote current symlink is absent despite activation records")
        return
    if not isinstance(current_target, str):
        raise _safety("remote current symlink target is invalid")
    try:
        target = _absolute_path(current_target, "remote current symlink target")
    except ValueError:
        raise _safety("remote current symlink target is invalid") from None
    expected_id = records.current_release_id
    if expected_id is None:
        raise _safety("remote current symlink conflicts with activation records")
    adopted_paths = {
        adoption.release_id: adoption.release_path for adoption in records.adoptions
    }
    expected_path = adopted_paths.get(expected_id, store.release_root / expected_id)
    if target != expected_path:
        raise _safety("remote current symlink conflicts with activation records")


@dataclass(frozen=True)
class RemoteLifecycleStore:
    """Validated lifecycle writes performed through the strict ``Remote`` API."""

    remote: "Remote"
    deployment_root: PurePosixPath
    managed_root: PurePosixPath
    release_root: PurePosixPath
    backup_root: PurePosixPath
    application_port: int = 4000
    distribution_port: int = 6789
    database_port: int = 5432
    caddy_config: PurePosixPath = PurePosixPath("/etc/caddy/Caddyfile")

    def __post_init__(self) -> None:
        for value in (self.deployment_root, self.managed_root, self.release_root, self.backup_root, self.caddy_config):
            if (
                not isinstance(value, PurePosixPath)
                or not value.is_absolute()
                or any(part in {"", ".", ".."} for part in value.parts[1:])
            ):
                raise ValueError("managed remote roots must be absolute POSIX paths")
        ports = (self.application_port, self.distribution_port, self.database_port)
        if any(type(port) is not int or not 1 <= port <= 65_535 for port in ports):
            raise ValueError("managed remote ports must be integers between 1 and 65535")
        if len(set(ports)) != len(ports) or any(port in {80, 443} for port in ports):
            raise ValueError("managed remote ports must be distinct and non-public")

    @property
    def releases_directory(self) -> PurePosixPath:
        return self.deployment_root / "releases"

    @property
    def activations_directory(self) -> PurePosixPath:
        return self.deployment_root / "activations"

    @property
    def backups_directory(self) -> PurePosixPath:
        return self.deployment_root / "backups"

    @property
    def adoptions_directory(self) -> PurePosixPath:
        return self.deployment_root / "adoptions"

    def write_release(self, record: ReleaseRecord) -> None:
        self._write(self.releases_directory, f"release-{record.release_id}.json", record.to_mapping())

    def write_activation(self, record: ActivationRecord) -> None:
        self._write(self.activations_directory, f"{record.activation_id}.json", record.to_mapping())

    def write_backup(self, record: BackupRecord) -> None:
        if not _under(record.dump_path, self.backup_root):
            raise _safety("backup dump path is outside the managed backup root")
        self._write(self.backups_directory, f"{record.backup_id}.json", record.to_mapping())

    def write_adoption(self, record: AdoptionRecord) -> None:
        if not _under(record.release_path, self.release_root):
            raise _safety("adopted release path is outside the managed release root")
        self._write(self.adoptions_directory, f"adoption-{record.release_id}.json", record.to_mapping())

    def read(
        self,
        *,
        operation: str,
        lock_timeout_seconds: float,
    ) -> tuple[LifecycleRecords, Mapping[str, object]]:
        """Read one shared-lock host snapshot through ``Remote``.

        The controller intentionally receives only structured bytes from the
        managed host.  It never opens deployment paths itself; local record
        helpers remain useful solely for pure parsing tests.
        """

        if not isinstance(operation, str) or _REMOTE_OPERATION_RE.fullmatch(operation) is None:
            raise ValueError("invalid lifecycle operation")
        if not isinstance(lock_timeout_seconds, (int, float)) or isinstance(lock_timeout_seconds, bool):
            raise ValueError("lock timeout must be numeric")
        if lock_timeout_seconds < 0:
            raise ValueError("lock timeout must be non-negative")
        result = self.remote.run(
            (
                "sh", "-ceu", _REMOTE_SNAPSHOT_TRANSACTION, "taskman-lifecycle-snapshot",
                self.deployment_root.as_posix(), self.managed_root.as_posix(),
                self.release_root.as_posix(), self.backup_root.as_posix(),
                "/var/lock/taskman", operation, str(int(lock_timeout_seconds * 1000)), "0",
            ),
            sudo=True,
            stdin=None,
            sensitive=False,
        )
        if not result.succeeded:
            _raise_remote_lifecycle_failure(result)
        try:
            snapshot = json.loads(result.stdout)
            response = _exact_mapping(
                snapshot,
                frozenset({"schema_version", "records", "current_target", "manifests", "dump_states", "warnings"}),
                "remote lifecycle snapshot",
            )
            if response["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unsupported remote lifecycle snapshot schema")
            records_value = _exact_mapping(
                response["records"],
                frozenset({"releases", "activations", "backups", "adoptions"}),
                "remote lifecycle records",
            )
            records = LifecycleRecords(
                releases=tuple(sorted((ReleaseRecord.from_mapping(item) for item in _record_list(records_value["releases"])), key=lambda item: item.release_id)),
                activations=tuple(sorted((ActivationRecord.from_mapping(item) for item in _record_list(records_value["activations"])), key=lambda item: (item.activated_at, item.activation_id))),
                backups=tuple(sorted((BackupRecord.from_mapping(item) for item in _record_list(records_value["backups"])), key=lambda item: (item.created_at, item.backup_id))),
                adoptions=tuple(sorted((AdoptionRecord.from_mapping(item) for item in _record_list(records_value["adoptions"])), key=lambda item: item.release_id)),
                warnings=tuple(sorted(_warning_list(response["warnings"]))),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _safety("remote lifecycle snapshot is invalid") from None
        _validate_remote_records(records, self)
        _validate_remote_current_target(records, response["current_target"], self)
        return records, response

    def inspect_manual_current_release(
        self, *, lock_timeout_seconds: float = 5
    ) -> ManualAdoptionCandidate:
        """Derive exact adoptable-current authority without publishing records."""

        result = self._run_manual_adoption_transaction(
            mode="inspect",
            lock_timeout_seconds=lock_timeout_seconds,
            expected=None,
        )
        try:
            response = _exact_mapping(
                json.loads(result.stdout),
                frozenset({"schema_version", "candidate"}),
                "remote manual adoption inspection",
            )
            if response["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unsupported remote manual adoption inspection schema")
            candidate = ManualAdoptionCandidate.from_mapping(response["candidate"])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _safety("remote manual adoption inspection is invalid") from None
        if not _under(candidate.release_path, self.release_root):
            raise _safety("remote manual release path conflicts with its metadata")
        return candidate

    def adopt_current_release(
        self,
        *,
        confirmed: bool,
        expected: ManualAdoptionCandidate | None = None,
        lock_timeout_seconds: float = 5,
    ) -> AdoptionRecord:
        """Ask the managed host to evidence and atomically adopt ``current``."""

        if type(confirmed) is not bool:
            raise ValueError("manual adoption confirmation must be boolean")
        if not confirmed:
            raise _safety("manual release adoption requires explicit confirmation")
        if expected is not None and not isinstance(expected, ManualAdoptionCandidate):
            raise TypeError("expected manual adoption authority is invalid")
        result = self._run_manual_adoption_transaction(
            mode="adopt",
            lock_timeout_seconds=lock_timeout_seconds,
            expected=expected,
        )
        try:
            response = _exact_mapping(json.loads(result.stdout), frozenset({"schema_version", "adoption"}), "remote adoption")
            if response["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unsupported remote adoption schema")
            adoption = AdoptionRecord.from_mapping(response["adoption"])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _safety("remote adoption result is invalid") from None
        if not _under(adoption.release_path, self.release_root):
            raise _safety("remote adopted release path conflicts with its metadata")
        if expected is not None and (
            adoption.release_id != expected.release_id
            or adoption.release_path != expected.release_path
            or adoption.content_sha256 != expected.content_sha256
            or adoption.application_version != expected.application_version
            or adoption.migrations != expected.migrations
        ):
            raise _safety("remote adopted release conflicts with confirmed authority")
        return adoption

    def _run_manual_adoption_transaction(
        self,
        *,
        mode: Literal["inspect", "adopt"],
        lock_timeout_seconds: float,
        expected: ManualAdoptionCandidate | None,
    ) -> "CommandResult":
        if not isinstance(lock_timeout_seconds, (int, float)) or isinstance(lock_timeout_seconds, bool):
            raise ValueError("lock timeout must be numeric")
        if lock_timeout_seconds < 0:
            raise ValueError("lock timeout must be non-negative")
        expected_migrations = (
            "-"
            if expected is None
            else json.dumps(
                [fingerprint.to_mapping() for fingerprint in expected.migrations],
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        result = self.remote.run(
            (
                "sh", "-ceu", _REMOTE_ADOPTION_TRANSACTION, f"taskman-lifecycle-adoption-{mode}",
                self.deployment_root.as_posix(), self.managed_root.as_posix(),
                self.release_root.as_posix(), self.backup_root.as_posix(),
                "/var/lock/taskman", str(int(lock_timeout_seconds * 1000)),
                str(self.application_port), str(self.distribution_port), str(self.database_port), self.caddy_config.as_posix(),
                mode,
                expected.release_id if expected is not None else "-",
                expected.content_sha256 if expected is not None else "-",
                (
                    base64.b64encode(expected_migrations.encode("utf-8")).decode("ascii")
                    if expected is not None
                    else "-"
                ),
            ),
            sudo=True,
            stdin=None,
            sensitive=False,
        )
        if not result.succeeded:
            _raise_remote_lifecycle_failure(result)
        return result

    def _write(self, directory: PurePosixPath, filename: str, payload: Mapping[str, object]) -> None:
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        result = self.remote.run(
            ("sh", "-ceu", _REMOTE_ATOMIC_WRITE, "taskman-lifecycle-record", str(directory), filename),
            sudo=True,
            stdin=encoded,
            sensitive=False,
        )
        if not result.succeeded:
            raise _safety("remote lifecycle record write was refused")


def load_lifecycle_records(store: LifecycleStore, *, current_link: Path | None = None) -> LifecycleRecords:
    """Read every recognized record after validating its secure storage shape."""

    releases, release_warnings = _read_records(store, "releases", ReleaseRecord, "release-")
    activations, activation_warnings = _read_records(store, "activations", ActivationRecord, "")
    backups, backup_warnings = _read_records(store, "backups", BackupRecord, "")
    adoptions, adoption_warnings = _read_records(store, "adoptions", AdoptionRecord, "adoption-")
    records = LifecycleRecords(
        releases=tuple(sorted(releases, key=lambda item: item.release_id)),
        activations=tuple(sorted(activations, key=lambda item: (item.activated_at, item.activation_id))),
        backups=tuple(sorted(backups, key=lambda item: (item.created_at, item.backup_id))),
        adoptions=tuple(sorted(adoptions, key=lambda item: item.release_id)),
        warnings=tuple(sorted((*release_warnings, *activation_warnings, *backup_warnings, *adoption_warnings))),
    )
    _validate_records(records, store)
    if current_link is not None:
        _validate_current_link(records, store, current_link)
    return records


def _read_records(
    store: LifecycleStore,
    category: str,
    record_type: type[ReleaseRecord] | type[ActivationRecord] | type[BackupRecord] | type[AdoptionRecord],
    filename_prefix: str,
) -> tuple[list[ReleaseRecord] | list[ActivationRecord] | list[BackupRecord] | list[AdoptionRecord], list[str]]:
    directory = getattr(store, f"{category}_directory")
    if not directory.exists() and not directory.is_symlink():
        return [], []
    _safe_directory(directory, store.owner_uid)
    loaded: list[ReleaseRecord] | list[ActivationRecord] | list[BackupRecord] | list[AdoptionRecord] = []
    warnings: list[str] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        identifier = _record_identifier(entry.name, category, filename_prefix)
        if identifier is None:
            warnings.append(f"unrecognized deployment storage entry: {category}/{entry.name}")
            continue
        _safe_record_file(entry, store.owner_uid)
        try:
            parsed = json.loads(entry.read_text(encoding="utf-8"))
            record = record_type.from_mapping(parsed)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise _safety("invalid lifecycle record") from None
        if _id_for(record) != identifier:
            raise _safety("lifecycle record filename does not match metadata")
        loaded.append(record)
    return loaded, warnings


def _record_identifier(filename: str, category: str, prefix: str) -> str | None:
    if not filename.startswith(prefix) or not filename.endswith(".json"):
        return None
    identifier = filename[len(prefix) : -len(".json")]
    if category in {"releases", "adoptions"}:
        try:
            return validate_release_id(identifier)
        except ValueError:
            return None
    if category == "activations" and _ACTIVATION_ID_RE.fullmatch(identifier):
        return identifier
    if category == "backups" and _BACKUP_ID_RE.fullmatch(identifier):
        return identifier
    return None


def _id_for(record: ReleaseRecord | ActivationRecord | BackupRecord | AdoptionRecord) -> str:
    if isinstance(record, ActivationRecord):
        return record.activation_id
    if isinstance(record, BackupRecord):
        return record.backup_id
    return record.release_id


def _safe_directory(directory: Path, owner_uid: int) -> None:
    try:
        details = directory.lstat()
    except OSError:
        raise _safety("unable to inspect lifecycle record directory") from None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != owner_uid:
        raise _safety("lifecycle record directory is unsafe")
    if details.st_mode & 0o027:
        raise _safety("lifecycle record directory permissions are unsafe")


def _safe_record_file(path: Path, owner_uid: int) -> None:
    try:
        details = path.lstat()
    except OSError:
        raise _safety("unable to inspect lifecycle record") from None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise _safety("lifecycle record is not a regular file")
    if details.st_uid != owner_uid or stat.S_IMODE(details.st_mode) != 0o600:
        raise _safety("lifecycle record ownership or permissions are unsafe")


def _validate_records(records: LifecycleRecords, store: LifecycleStore) -> None:
    release_ids = {record.release_id for record in records.releases}
    if len(release_ids) != len(records.releases):
        raise _safety("duplicate release identifiers")
    backup_ids = {record.backup_id for record in records.backups}
    if len(backup_ids) != len(records.backups):
        raise _safety("duplicate backup identifiers")
    activation_ids = {record.activation_id for record in records.activations}
    if len(activation_ids) != len(records.activations):
        raise _safety("duplicate activation identifiers")
    adoption_ids = {record.release_id for record in records.adoptions}
    if len(adoption_ids) != len(records.adoptions):
        raise _safety("duplicate adopted release identifiers")
    if not adoption_ids.issubset(release_ids):
        raise _safety("adopted release has no release record")

    backup_root = PurePosixPath(store.backup_root.as_posix())
    for backup in records.backups:
        if not _under(backup.dump_path, backup_root):
            raise _safety("backup dump path is outside the managed backup root")
        if not {item for item in (backup.current_release_id, backup.candidate_release_id) if item is not None}.issubset(
            release_ids
        ):
            raise _safety("backup references an unknown release")

    previous: str | None = None
    last_time: datetime | None = None
    first_activation_by_release: dict[str, ActivationRecord] = {}
    for activation in records.activations:
        if last_time is not None and activation.activated_at <= last_time:
            raise _safety("activation times are ambiguous")
        if activation.previous_release_id != previous:
            raise _safety("activation chain is broken")
        if activation.candidate_release_id not in release_ids:
            raise _safety("activation references an unknown release")
        if activation.backup_id is not None and activation.backup_id not in backup_ids:
            raise _safety("activation references an unknown backup")
        first_activation_by_release.setdefault(activation.candidate_release_id, activation)
        previous = activation.candidate_release_id
        last_time = activation.activated_at

    for release in records.releases:
        if release.previous_release_id is not None and release.previous_release_id not in release_ids:
            raise _safety("release references an unknown previous release")
        if release.backup_id is not None and release.backup_id not in backup_ids:
            raise _safety("release references an unknown backup")
        activation = first_activation_by_release.get(release.release_id)
        if activation is None:
            if release.activated_at is not None or release.previous_release_id is not None or release.backup_id is not None:
                raise _safety("release activation fields exist without an activation edge")
        elif (
            release.activated_at != activation.activated_at
            or release.previous_release_id != activation.previous_release_id
            or release.backup_id != activation.backup_id
            or release.migration_policy != activation.migration_policy
        ):
            raise _safety("release fields contradict its first activation edge")

    release_by_id = {release.release_id: release for release in records.releases}
    release_root = PurePosixPath(store.release_root.as_posix())
    for adoption in records.adoptions:
        release = release_by_id[adoption.release_id]
        activation = first_activation_by_release.get(adoption.release_id)
        expected_release_id = (
            f"{adoption.application_version}-{adoption.content_sha256[:12]}-ubuntu26.04-amd64-otp27.3.4.6"
        )
        if (
            adoption.release_id != expected_release_id
            or not _under(adoption.release_path, release_root)
            or release.artifact_sha256 is not None
            or release.migration_policy != "adopted"
            or activation is None
            or activation.migration_policy != "adopted"
            or release.installed_at != adoption.adopted_at
            or release.activated_at != adoption.adopted_at
            or activation.activated_at != adoption.adopted_at
        ):
            raise _safety("adoption record conflicts with its release activation")


def _validate_current_link(records: LifecycleRecords, store: LifecycleStore, current_link: Path) -> None:
    if Path(current_link) != store.current_link:
        raise _safety("current selection path is not the configured managed symlink")
    expected_id = records.current_release_id
    if expected_id is None:
        if current_link.exists() or current_link.is_symlink():
            raise _safety("current symlink exists without an activation record")
        return
    try:
        details = current_link.lstat()
        if not stat.S_ISLNK(details.st_mode):
            raise _safety("current selection is not a symlink")
        target = current_link.resolve(strict=True)
        adoption_paths = {
            adoption.release_id: Path(adoption.release_path)
            for adoption in records.adoptions
        }
        expected = adoption_paths.get(expected_id, store.release_root / expected_id).resolve(strict=True)
    except OpsError:
        raise
    except OSError:
        raise _safety("current symlink is invalid") from None
    try:
        target.relative_to(store.release_root.resolve(strict=True))
    except ValueError:
        raise _safety("current symlink escapes the managed release root") from None
    if target != expected:
        raise _safety("current symlink conflicts with activation records")


def rollback_eligibility(
    records: LifecycleRecords,
    *,
    current_release_id: str,
    target_release_id: str,
) -> tuple[bool, str | None]:
    """Return whether the complete recorded path permits a code-only rollback."""

    try:
        validate_release_id(current_release_id)
        validate_release_id(target_release_id)
    except ValueError:
        return False, "release identifier is invalid"
    if current_release_id == target_release_id:
        return False, "target release is already current"
    if records.current_release_id != current_release_id:
        return False, "current release does not match activation records"
    cursor = current_release_id
    for activation in reversed(records.activations):
        if activation.candidate_release_id != cursor:
            return False, "activation chain is incomplete"
        if activation.migration_policy == "restore-required":
            return False, f"activation {activation.activation_id} requires database restore"
        if activation.previous_release_id == target_release_id:
            return True, None
        if activation.previous_release_id is None:
            break
        cursor = activation.previous_release_id
    return False, "target release is not connected to the current activation chain"


__all__ = [
    "ActivationRecord",
    "AdoptionRecord",
    "BackupRecord",
    "LifecycleRecords",
    "LifecycleStore",
    "ReleaseRecord",
    "SCHEMA_VERSION",
    "load_lifecycle_records",
    "raise_remote_lifecycle_failure",
    "rollback_eligibility",
]
