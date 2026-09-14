"""Durable protection records for backups taken before migrations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re

from taskman_ops.releases.identifiers import validate_release_id

from .filesystem import fsync_directory
from .paths import ManagedPaths, PathAuthorityError
from .records import (
    MAX_RECORD_BYTES,
    RecordError,
    _atomic_create,
    _open_temporary,
    _safe_directory,
    _safe_file,
)


_FIELDS = frozenset(
    {
        "schema_version",
        "backup_id",
        "base_selection_id",
        "target_release_id",
        "attempt_number",
        "created_at",
    }
)
_SELECTION_FILE_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")


def _exact(value: object, label: str) -> Mapping[str, object]:
    if (
        not isinstance(value, Mapping)
        or set(value) != _FIELDS
        or not all(type(key) is str for key in value)
    ):
        raise RecordError(f"invalid {label} fields")
    return value


def _backup_id(value: object, label: str = "backup identifier") -> str:
    if type(value) is not str or _BACKUP_ID_RE.fullmatch(value) is None:
        raise RecordError(f"invalid {label}")
    return value


def _selection_id(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _SELECTION_FILE_RE.fullmatch(value) is None:
        raise RecordError("invalid base selection identifier")
    return value


def _timestamp(value: object) -> datetime:
    if type(value) is not str or not value.endswith("Z") or "T" not in value:
        raise RecordError("invalid protection creation time")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise RecordError("invalid protection creation time") from error
    if parsed.tzinfo != UTC or parsed.microsecond:
        raise RecordError("invalid protection creation time")
    if parsed.isoformat(timespec="seconds").replace("+00:00", "Z") != value:
        raise RecordError("invalid protection creation time")
    return parsed


def _format_timestamp(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo != UTC or value.microsecond:
        raise RecordError("invalid protection creation time")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class BackupProtection:
    """A narrow durable reference that keeps one migration backup retained."""

    schema_version: int
    backup_id: str
    base_selection_id: str | None
    target_release_id: str
    attempt_number: int
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise RecordError("unsupported backup protection schema")
        object.__setattr__(self, "backup_id", _backup_id(self.backup_id))
        object.__setattr__(self, "base_selection_id", _selection_id(self.base_selection_id))
        try:
            object.__setattr__(self, "target_release_id", validate_release_id(self.target_release_id))
        except (TypeError, ValueError) as error:
            raise RecordError("invalid target release identifier") from error
        if type(self.attempt_number) is not int or self.attempt_number < 0:
            raise RecordError("invalid protection attempt number")
        _format_timestamp(self.created_at)
        _record_json(self.to_mapping())

    @classmethod
    def from_mapping(cls, value: object) -> "BackupProtection":
        mapping = _exact(value, "backup protection")
        return cls(
            schema_version=mapping["schema_version"],  # type: ignore[arg-type]
            backup_id=mapping["backup_id"],  # type: ignore[arg-type]
            base_selection_id=mapping["base_selection_id"],
            target_release_id=mapping["target_release_id"],  # type: ignore[arg-type]
            attempt_number=mapping["attempt_number"],  # type: ignore[arg-type]
            created_at=_timestamp(mapping["created_at"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "backup_id": self.backup_id,
            "base_selection_id": self.base_selection_id,
            "target_release_id": self.target_release_id,
            "attempt_number": self.attempt_number,
            "created_at": _format_timestamp(self.created_at),
        }


def _record_json(value: Mapping[str, object], *, maximum: int | None = None) -> bytes:
    try:
        payload = (
            json.dumps(
                value,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RecordError("backup protection is not JSON serializable") from error
    if maximum is None:
        maximum = MAX_RECORD_BYTES
    if len(payload) > maximum:
        raise RecordError("backup protection is oversized")
    return payload


def backup_protection_sha256(value: BackupProtection | Mapping[str, object]) -> str:
    """Hash one canonical protection mapping for diagnostics and discovery."""

    record = value if isinstance(value, BackupProtection) else BackupProtection.from_mapping(value)
    return hashlib.sha256(_record_json(record.to_mapping()).rstrip(b"\n")).hexdigest()


def _prepare_paths(paths: ManagedPaths) -> tuple[ManagedPaths, int, Path]:
    if not isinstance(paths, ManagedPaths):
        raise TypeError("backup protection writes need managed paths")
    owner_uid = os.geteuid()
    try:
        paths.validate_existing(owner_uid=owner_uid)
    except PathAuthorityError as error:
        raise RecordError(str(error)) from error
    _safe_directory(Path(paths.local(paths.install_root)), owner_uid=owner_uid)
    _safe_directory(Path(paths.local(paths.deployment_root)), owner_uid=owner_uid)
    root = Path(paths.local(paths.backup_protection_root))
    _safe_directory(root, owner_uid=owner_uid)
    return paths, owner_uid, root


def write_backup_protection(paths: ManagedPaths, record: BackupProtection) -> None:
    """Create one protection record without replacing an existing record."""

    if not isinstance(record, BackupProtection):
        raise TypeError("backup protection publication needs a BackupProtection")
    paths, owner_uid, _root = _prepare_paths(paths)
    target = Path(paths.local(paths.backup_protection(record.backup_id)))
    if target.exists() or target.is_symlink():
        raise RecordError("backup protection already exists")
    _atomic_create(target, _record_json(record.to_mapping()), owner_uid=owner_uid)


def replace_backup_protection(paths: ManagedPaths, record: BackupProtection) -> None:
    """Atomically replace an existing protection record and flush its directory."""

    if not isinstance(record, BackupProtection):
        raise TypeError("backup protection replacement needs a BackupProtection")
    paths, owner_uid, root = _prepare_paths(paths)
    target = Path(paths.local(paths.backup_protection(record.backup_id)))
    _safe_file(target, owner_uid=owner_uid)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = _open_temporary(root, target.name)
        temporary = root / temporary_name
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = None
            output.write(_record_json(record.to_mapping()))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
        fsync_directory(root)
    except RecordError:
        raise
    except OSError as error:
        raise RecordError("unable to replace backup protection") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

__all__ = [
    "BackupProtection",
    "backup_protection_sha256",
    "replace_backup_protection",
    "write_backup_protection",
]
