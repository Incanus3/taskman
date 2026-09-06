"""Lifecycle record schemas and their pure serialization rules.

The helper's filesystem mechanics live in :mod:`lifecycle`.  Keeping the
records here lets every reader share one exact, storage-independent schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
import re


SCHEMA_VERSION = 1
_RELEASE_RE = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26\.04-amd64-otp27\.3\.4\.6\Z"
)
_ACTIVATION_RE = re.compile(r"activation-[0-9a-f]{32}\Z")
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DATABASE_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")
_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required", "adopted"})
_BACKUP_REASONS = frozenset({"scheduled", "pre-deploy", "pre-rollback", "pre-restore"})


class LifecycleError(ValueError):
    """A malformed, unsafe, or contradictory lifecycle shape."""


def _exact(value: object, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields or not all(type(key) is str for key in value):
        raise LifecycleError(f"invalid {label}")
    return value


def _release(value: object, label: str = "release identifier") -> str:
    if type(value) is not str or _RELEASE_RE.fullmatch(value) is None:
        raise LifecycleError(f"invalid {label}")
    return value


def _optional_release(value: object, label: str) -> str | None:
    return None if value is None else _release(value, label)


def _sha256(value: object, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise LifecycleError(f"invalid {label}")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z") or "T" not in value:
        raise LifecycleError(f"invalid {label}")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise LifecycleError(f"invalid {label}") from error
    if parsed.tzinfo != UTC:
        raise LifecycleError(f"invalid {label}")
    return parsed


def _format_timestamp(value: datetime, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo != UTC:
        raise LifecycleError(f"invalid {label}")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _path(value: object, label: str) -> PurePosixPath:
    if type(value) is not str or not value.startswith("/") or "\x00" in value:
        raise LifecycleError(f"invalid {label}")
    result = PurePosixPath(value)
    if result.as_posix() != value or any(part in {"", ".", ".."} for part in result.parts[1:]):
        raise LifecycleError(f"invalid {label}")
    return result


def _under(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path != root and path.is_relative_to(root)


def _policy(value: object) -> str:
    if type(value) is not str or value not in _POLICIES:
        raise LifecycleError("invalid migration policy")
    return value


def _activation(value: object) -> str:
    if type(value) is not str or _ACTIVATION_RE.fullmatch(value) is None:
        raise LifecycleError("invalid activation identifier")
    return value


def _backup_id(value: object, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str or _BACKUP_RE.fullmatch(value) is None:
        raise LifecycleError("invalid backup identifier")
    return value


def _migrations(value: object) -> tuple[Mapping[str, object], ...]:
    """Validate the bounded, sorted migration fingerprint sequence."""

    if not isinstance(value, (list, tuple)):
        raise LifecycleError("invalid adoption migrations")
    result: list[Mapping[str, object]] = []
    for item in value:
        data = _exact(item, frozenset({"filename", "sha256"}), "migration fingerprint")
        filename, digest = data["filename"], data["sha256"]
        if type(filename) is not str or re.fullmatch(r"[0-9]{14}_[a-z0-9_]+\.exs", filename) is None:
            raise LifecycleError("invalid migration fingerprint")
        _sha256(digest, "migration checksum")
        result.append({"filename": filename, "sha256": digest})
    filenames = [item["filename"] for item in result]
    if filenames != sorted(filenames) or len(set(filenames)) != len(filenames):
        raise LifecycleError("adoption migrations must be unique and sorted")
    return tuple(result)


@dataclass(frozen=True)
class ReleaseRecord:
    schema_version: int
    release_id: str
    artifact_sha256: str | None
    installed_at: datetime
    activated_at: datetime | None
    previous_release_id: str | None
    backup_id: str | None
    migration_policy: str

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

    @classmethod
    def from_mapping(cls, value: object) -> "ReleaseRecord":
        data = _exact(value, cls._FIELDS, "release record")
        activated = data["activated_at"]
        result = cls(
            data["schema_version"],
            _release(data["release_id"]),
            _sha256(data["artifact_sha256"], "artifact checksum", optional=True),
            _timestamp(data["installed_at"], "installation time"),
            None if activated is None else _timestamp(activated, "activation time"),
            _optional_release(data["previous_release_id"], "previous release identifier"),
            _backup_id(data["backup_id"], optional=True),
            _policy(data["migration_policy"]),
        )
        result._validate()
        return result

    def _validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise LifecycleError("unsupported release record schema")
        _release(self.release_id)
        _sha256(self.artifact_sha256, "artifact checksum", optional=True)
        _format_timestamp(self.installed_at, "installation time")
        if self.activated_at is not None:
            _format_timestamp(self.activated_at, "activation time")
        _optional_release(self.previous_release_id, "previous release identifier")
        _backup_id(self.backup_id, optional=True)
        if _policy(self.migration_policy) == "adopted" and self.artifact_sha256 is not None:
            raise LifecycleError("adopted release checksum must be unavailable")

    def to_mapping(self) -> dict[str, object]:
        self._validate()
        return {
            "schema_version": SCHEMA_VERSION,
            "release_id": self.release_id,
            "artifact_sha256": self.artifact_sha256,
            "installed_at": _format_timestamp(self.installed_at, "installation time"),
            "activated_at": (
                None
                if self.activated_at is None
                else _format_timestamp(self.activated_at, "activation time")
            ),
            "previous_release_id": self.previous_release_id,
            "backup_id": self.backup_id,
            "migration_policy": self.migration_policy,
        }


@dataclass(frozen=True)
class ActivationRecord:
    schema_version: int
    activation_id: str
    previous_release_id: str | None
    candidate_release_id: str
    activated_at: datetime
    backup_id: str | None
    migration_policy: str

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

    @classmethod
    def from_mapping(cls, value: object) -> "ActivationRecord":
        data = _exact(value, cls._FIELDS, "activation record")
        result = cls(
            data["schema_version"],
            data["activation_id"],
            _optional_release(data["previous_release_id"], "previous release identifier"),
            _release(data["candidate_release_id"]),
            _timestamp(data["activated_at"], "activation time"),
            _backup_id(data["backup_id"], optional=True),
            _policy(data["migration_policy"]),
        )
        result._validate()
        return result

    def _validate(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != SCHEMA_VERSION
            or type(self.activation_id) is not str
            or _ACTIVATION_RE.fullmatch(self.activation_id) is None
        ):
            raise LifecycleError("invalid activation record")
        if _optional_release(self.previous_release_id, "previous release identifier") == _release(
            self.candidate_release_id
        ):
            raise LifecycleError("activation must select a different release")
        _format_timestamp(self.activated_at, "activation time")
        _backup_id(self.backup_id, optional=True)
        _policy(self.migration_policy)

    def to_mapping(self) -> dict[str, object]:
        self._validate()
        return {
            "schema_version": SCHEMA_VERSION,
            "activation_id": self.activation_id,
            "previous_release_id": self.previous_release_id,
            "candidate_release_id": self.candidate_release_id,
            "activated_at": _format_timestamp(self.activated_at, "activation time"),
            "backup_id": self.backup_id,
            "migration_policy": self.migration_policy,
        }


@dataclass(frozen=True)
class BackupRecord:
    schema_version: int
    backup_id: str
    created_at: datetime
    size_bytes: int
    source_database_size_bytes: int
    database: str
    current_release_id: str | None
    candidate_release_id: str | None
    reason: str
    validated: bool
    dump_path: PurePosixPath
    dump_sha256: str | None = None

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
            "dump_sha256",
        }
    )
    _LEGACY_FIELDS = _FIELDS - {"dump_sha256"}

    @classmethod
    def from_mapping(cls, value: object) -> "BackupRecord":
        if (
            not isinstance(value, Mapping)
            or set(value) not in {cls._FIELDS, cls._LEGACY_FIELDS}
            or not all(type(key) is str for key in value)
        ):
            raise LifecycleError("invalid backup record")
        data = value
        dump_sha256 = (
            None if "dump_sha256" not in data else _sha256(data["dump_sha256"], "backup checksum")
        )
        result = cls(
            data["schema_version"],
            _backup_id(data["backup_id"]),
            _timestamp(data["created_at"], "backup time"),
            data["size_bytes"],
            data["source_database_size_bytes"],
            data["database"],
            _optional_release(data["current_release_id"], "current release identifier"),
            _optional_release(data["candidate_release_id"], "candidate release identifier"),
            data["reason"],
            data["validated"],
            _path(data["dump_path"], "dump path"),
            dump_sha256,
        )
        result._validate()
        return result

    def _validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise LifecycleError("unsupported backup record schema")
        _backup_id(self.backup_id)
        _format_timestamp(self.created_at, "backup time")
        if (
            type(self.size_bytes) is not int
            or self.size_bytes < 0
            or type(self.source_database_size_bytes) is not int
            or self.source_database_size_bytes <= 0
        ):
            raise LifecycleError("invalid backup size")
        if type(self.database) is not str or _DATABASE_RE.fullmatch(self.database) is None:
            raise LifecycleError("invalid backup database")
        _optional_release(self.current_release_id, "current release identifier")
        _optional_release(self.candidate_release_id, "candidate release identifier")
        if self.reason not in _BACKUP_REASONS or type(self.validated) is not bool:
            raise LifecycleError("invalid backup record")
        _path(self.dump_path.as_posix(), "dump path")
        _sha256(self.dump_sha256, "backup checksum", optional=True)

    def to_mapping(self) -> dict[str, object]:
        self._validate()
        value: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "backup_id": self.backup_id,
            "created_at": _format_timestamp(self.created_at, "backup time"),
            "size_bytes": self.size_bytes,
            "source_database_size_bytes": self.source_database_size_bytes,
            "database": self.database,
            "current_release_id": self.current_release_id,
            "candidate_release_id": self.candidate_release_id,
            "reason": self.reason,
            "validated": self.validated,
            "dump_path": self.dump_path.as_posix(),
        }
        if self.dump_sha256 is not None:
            value["dump_sha256"] = self.dump_sha256
        return value


@dataclass(frozen=True)
class AdoptionRecord:
    schema_version: int
    release_id: str
    adopted_at: datetime
    release_path: PurePosixPath
    content_sha256: str
    application_version: str
    source_revision: str
    artifact_sha256: str
    migrations: tuple[Mapping[str, object], ...]

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

    @classmethod
    def from_mapping(cls, value: object) -> "AdoptionRecord":
        data = _exact(value, cls._FIELDS, "adoption record")
        result = cls(
            data["schema_version"],
            _release(data["release_id"]),
            _timestamp(data["adopted_at"], "adoption time"),
            _path(data["release_path"], "release path"),
            _sha256(data["content_sha256"], "content checksum") or "",
            data["application_version"],
            data["source_revision"],
            data["artifact_sha256"],
            _migrations(data["migrations"]),
        )
        result._validate()
        return result

    def _validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise LifecycleError("unsupported adoption record schema")
        _release(self.release_id)
        _format_timestamp(self.adopted_at, "adoption time")
        _path(self.release_path.as_posix(), "release path")
        _sha256(self.content_sha256, "content checksum")
        if (
            type(self.application_version) is not str
            or _VERSION_RE.fullmatch(self.application_version) is None
            or self.source_revision != "unknown"
            or self.artifact_sha256 != "unknown"
        ):
            raise LifecycleError("invalid adoption provenance")
        _migrations(list(self.migrations))

    def to_mapping(self) -> dict[str, object]:
        self._validate()
        return {
            "schema_version": SCHEMA_VERSION,
            "release_id": self.release_id,
            "adopted_at": _format_timestamp(self.adopted_at, "adoption time"),
            "release_path": self.release_path.as_posix(),
            "content_sha256": self.content_sha256,
            "application_version": self.application_version,
            "source_revision": "unknown",
            "artifact_sha256": "unknown",
            "migrations": [dict(item) for item in self.migrations],
        }


@dataclass(frozen=True)
class ManualAdoptionCandidate:
    """The exact manual-release authority an operator may confirm once."""

    schema_version: int
    release_id: str
    release_path: PurePosixPath
    content_sha256: str
    application_version: str
    migrations: tuple[Mapping[str, object], ...]

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
            raise LifecycleError("unsupported manual adoption schema")
        _release(self.release_id)
        _path(self.release_path.as_posix(), "manual release path")
        _sha256(self.content_sha256, "manual release checksum")
        if type(self.application_version) is not str or _VERSION_RE.fullmatch(self.application_version) is None:
            raise LifecycleError("invalid manual application version")
        if self.release_id != f"{self.application_version}-{self.content_sha256[:12]}-ubuntu26.04-amd64-otp27.3.4.6":
            raise LifecycleError("manual release identifier conflicts with content")
        _migrations(list(self.migrations))

    @classmethod
    def from_mapping(cls, value: object) -> "ManualAdoptionCandidate":
        data = _exact(value, cls._FIELDS, "manual adoption candidate")
        return cls(
            data["schema_version"],
            _release(data["release_id"]),
            _path(data["release_path"], "manual release path"),
            _sha256(data["content_sha256"], "manual release checksum") or "",
            data["application_version"],
            _migrations(data["migrations"]),
        )

    def to_mapping(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": SCHEMA_VERSION,
            "release_id": self.release_id,
            "release_path": self.release_path.as_posix(),
            "content_sha256": self.content_sha256,
            "application_version": self.application_version,
            "migrations": [dict(value) for value in self.migrations],
        }


@dataclass(frozen=True)
class StagedRelease:
    """A no-replace provisional activation authority for immutable staging."""

    schema_version: int
    activation_id: str
    previous_release_id: str | None
    candidate_release_id: str
    backup_id: str | None
    migration_policy: str
    artifact_sha256: str
    installed_at: datetime
    activated_at: datetime
    manifest: Mapping[str, object]

    _FIELDS = frozenset(
        {
            "schema_version",
            "activation_id",
            "previous_release_id",
            "candidate_release_id",
            "backup_id",
            "migration_policy",
            "artifact_sha256",
            "installed_at",
            "activated_at",
            "manifest",
        }
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise LifecycleError("unsupported immutable staging schema")
        _activation(self.activation_id)
        _optional_release(self.previous_release_id, "staged previous release")
        _release(self.candidate_release_id)
        if self.previous_release_id == self.candidate_release_id:
            raise LifecycleError("staged activation must change release")
        _backup_id(self.backup_id, optional=True)
        _policy(self.migration_policy)
        _sha256(self.artifact_sha256, "staged artifact checksum")
        _format_timestamp(self.installed_at, "staged installation time")
        _format_timestamp(self.activated_at, "staged activation time")
        if not isinstance(self.manifest, Mapping) or not all(type(key) is str for key in self.manifest):
            raise LifecycleError("invalid staged manifest")

    @classmethod
    def from_mapping(cls, value: object) -> "StagedRelease":
        data = _exact(value, cls._FIELDS, "immutable staging record")
        return cls(
            data["schema_version"],
            data["activation_id"],
            _optional_release(data["previous_release_id"], "staged previous release"),
            _release(data["candidate_release_id"]),
            _backup_id(data["backup_id"], optional=True),
            _policy(data["migration_policy"]),
            _sha256(data["artifact_sha256"], "staged artifact checksum") or "",
            _timestamp(data["installed_at"], "staged installation time"),
            _timestamp(data["activated_at"], "staged activation time"),
            data["manifest"],
        )

    def to_mapping(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": SCHEMA_VERSION,
            "activation_id": self.activation_id,
            "previous_release_id": self.previous_release_id,
            "candidate_release_id": self.candidate_release_id,
            "backup_id": self.backup_id,
            "migration_policy": self.migration_policy,
            "artifact_sha256": self.artifact_sha256,
            "installed_at": _format_timestamp(self.installed_at, "staged installation time"),
            "activated_at": _format_timestamp(self.activated_at, "staged activation time"),
            "manifest": dict(self.manifest),
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


__all__ = [
    "ActivationRecord",
    "AdoptionRecord",
    "BackupRecord",
    "LifecycleError",
    "LifecycleRecords",
    "ManualAdoptionCandidate",
    "ReleaseRecord",
    "SCHEMA_VERSION",
    "StagedRelease",
]
