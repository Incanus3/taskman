"""Durable binding for one unfinished database restore."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from types import MappingProxyType

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
        "dump_sha256",
        "source_release_id",
        "base_selection_id",
        "observed_previous_release_id",
        "original_database_oid",
        "restored_database_oid",
        "temporary_creation_pending",
        "safety_backup_id",
        "replacement",
        "safety_backup_attempts",
    }
)
_REPLACEMENT_FIELDS = frozenset(
    {"backup_id", "dump_sha256", "source_release_id", "discard_database_oid"}
)
_ATTEMPT_FIELDS = frozenset({"backup_id", "attempt_number"})
_SELECTION_FILE_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _exact(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if (
        not isinstance(value, Mapping)
        or set(value) != keys
        or not all(type(key) is str for key in value)
    ):
        raise RecordError(f"invalid {label} fields")
    return value


def _backup_id(value: object, label: str = "backup identifier") -> str:
    if type(value) is not str or _BACKUP_ID_RE.fullmatch(value) is None:
        raise RecordError(f"invalid {label}")
    return value


def _selection_id(value: object, label: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _SELECTION_FILE_RE.fullmatch(value) is None:
        raise RecordError(f"invalid {label}")
    return value


def _sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise RecordError(f"invalid {label}")
    return value


def _oid(value: object, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int or value <= 0:
        raise RecordError(f"invalid {label}")
    return value


def _attempts(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (tuple, list)) or not value or len(value) > 64:
        raise RecordError("invalid restore safety backup attempts")
    result: list[Mapping[str, object]] = []
    seen_backup_ids: set[str] = set()
    seen_numbers: set[int] = set()
    for item in value:
        mapping = _exact(item, _ATTEMPT_FIELDS, "restore safety backup attempt")
        backup_id = _backup_id(mapping["backup_id"], "safety backup identifier")
        attempt_number = mapping["attempt_number"]
        if type(attempt_number) is not int or attempt_number < 0:
            raise RecordError("invalid restore safety backup attempt number")
        if backup_id in seen_backup_ids or attempt_number in seen_numbers:
            raise RecordError("restore safety backup attempts must be unique")
        seen_backup_ids.add(backup_id)
        seen_numbers.add(attempt_number)
        result.append(
            MappingProxyType({"backup_id": backup_id, "attempt_number": attempt_number})
        )
    if result[0]["attempt_number"] != 0 or tuple(item["attempt_number"] for item in result) != tuple(
        sorted(item["attempt_number"] for item in result)
    ):
        raise RecordError("restore safety backup attempts must start at zero and be sorted")
    return tuple(result)


def _replacement(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    mapping = _exact(value, _REPLACEMENT_FIELDS, "restore replacement")
    return MappingProxyType(
        {
            "backup_id": _backup_id(mapping["backup_id"], "replacement backup identifier"),
            "dump_sha256": _sha256(mapping["dump_sha256"], "replacement dump checksum"),
            "source_release_id": _validate_release(mapping["source_release_id"], "replacement source release identifier"),
            "discard_database_oid": _oid(
                mapping["discard_database_oid"], "replacement discard database OID", nullable=True
            ),
        }
    )


def _validate_release(value: object, label: str) -> str:
    if type(value) is not str:
        raise RecordError(f"invalid {label}")
    try:
        return validate_release_id(value)
    except ValueError as error:
        raise RecordError(f"invalid {label}") from error


@dataclass(frozen=True)
class RestoreTarget:
    """The exact input, original identity, and creation intent for a restore."""

    schema_version: int
    backup_id: str
    dump_sha256: str
    source_release_id: str
    base_selection_id: str | None
    observed_previous_release_id: str | None
    original_database_oid: int
    restored_database_oid: int | None
    temporary_creation_pending: bool
    safety_backup_id: str
    replacement: Mapping[str, object] | None
    safety_backup_attempts: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise RecordError("unsupported restore target schema")
        object.__setattr__(self, "backup_id", _backup_id(self.backup_id))
        object.__setattr__(self, "dump_sha256", _sha256(self.dump_sha256, "dump checksum"))
        object.__setattr__(
            self, "source_release_id", _validate_release(self.source_release_id, "source release identifier")
        )
        object.__setattr__(self, "base_selection_id", _selection_id(self.base_selection_id, "base selection identifier"))
        object.__setattr__(
            self,
            "observed_previous_release_id",
            _selection_release(self.observed_previous_release_id),
        )
        original_oid = _oid(self.original_database_oid, "original database OID")
        restored_oid = _oid(self.restored_database_oid, "restored database OID", nullable=True)
        if restored_oid is not None and restored_oid == original_oid:
            raise RecordError("original and restored database OIDs must differ")
        if not self.temporary_creation_pending and restored_oid is None:
            raise RecordError("a registered restore target must have a restored database OID")
        object.__setattr__(self, "original_database_oid", original_oid)
        object.__setattr__(self, "restored_database_oid", restored_oid)
        if type(self.temporary_creation_pending) is not bool:
            raise RecordError("temporary creation intent must be a boolean")
        object.__setattr__(self, "safety_backup_id", _backup_id(self.safety_backup_id, "safety backup identifier"))
        replacement = _replacement(self.replacement)
        if replacement is not None:
            discard_oid = replacement["discard_database_oid"]
            if discard_oid is not None:
                if restored_oid is None or discard_oid != restored_oid:
                    raise RecordError("replacement discard OID does not match the restored database")
                if discard_oid == original_oid:
                    raise RecordError("replacement cannot discard the original database")
        object.__setattr__(self, "replacement", replacement)
        attempts = _attempts(self.safety_backup_attempts)
        if attempts[0]["backup_id"] != self.safety_backup_id:
            raise RecordError("safety backup attempts do not identify the original safety backup")
        object.__setattr__(self, "safety_backup_attempts", attempts)
        _record_json(self.to_mapping())

    @classmethod
    def from_mapping(cls, value: object) -> "RestoreTarget":
        mapping = _exact(value, _FIELDS, "restore target")
        return cls(
            schema_version=mapping["schema_version"],  # type: ignore[arg-type]
            backup_id=mapping["backup_id"],  # type: ignore[arg-type]
            dump_sha256=mapping["dump_sha256"],  # type: ignore[arg-type]
            source_release_id=mapping["source_release_id"],  # type: ignore[arg-type]
            base_selection_id=mapping["base_selection_id"],
            observed_previous_release_id=mapping["observed_previous_release_id"],
            original_database_oid=mapping["original_database_oid"],  # type: ignore[arg-type]
            restored_database_oid=mapping["restored_database_oid"],  # type: ignore[arg-type]
            temporary_creation_pending=mapping["temporary_creation_pending"],  # type: ignore[arg-type]
            safety_backup_id=mapping["safety_backup_id"],  # type: ignore[arg-type]
            replacement=mapping["replacement"],
            safety_backup_attempts=mapping["safety_backup_attempts"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "backup_id": self.backup_id,
            "dump_sha256": self.dump_sha256,
            "source_release_id": self.source_release_id,
            "base_selection_id": self.base_selection_id,
            "observed_previous_release_id": self.observed_previous_release_id,
            "original_database_oid": self.original_database_oid,
            "restored_database_oid": self.restored_database_oid,
            "temporary_creation_pending": self.temporary_creation_pending,
            "safety_backup_id": self.safety_backup_id,
            "replacement": None if self.replacement is None else dict(self.replacement),
            "safety_backup_attempts": [dict(item) for item in self.safety_backup_attempts],
        }


def _selection_release(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise RecordError("invalid observed previous release identifier")
    try:
        return validate_release_id(value)
    except ValueError as error:
        raise RecordError("invalid observed previous release identifier") from error


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
        raise RecordError("restore target is not JSON serializable") from error
    if maximum is None:
        maximum = MAX_RECORD_BYTES
    if len(payload) > maximum:
        raise RecordError("restore target is oversized")
    return payload


def restore_target_sha256(value: RestoreTarget | Mapping[str, object]) -> str:
    """Hash binding fields for discovery drift checks without persisting the digest."""

    record = value if isinstance(value, RestoreTarget) else RestoreTarget.from_mapping(value)
    payload = json.dumps(
        record.to_mapping(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _prepare_paths(paths: ManagedPaths) -> tuple[ManagedPaths, int, Path]:
    if not isinstance(paths, ManagedPaths):
        raise TypeError("restore target writes need managed paths")
    owner_uid = os.geteuid()
    try:
        paths.validate_existing(owner_uid=owner_uid)
    except PathAuthorityError as error:
        raise RecordError(str(error)) from error
    _safe_directory(Path(paths.local(paths.install_root)), owner_uid=owner_uid)
    root = Path(paths.local(paths.deployment_root))
    _safe_directory(root, owner_uid=owner_uid)
    return paths, owner_uid, root


def write_restore_target(paths: ManagedPaths, record: RestoreTarget) -> None:
    """Create the one restore binding without replacing an existing binding."""

    if not isinstance(record, RestoreTarget):
        raise TypeError("restore target publication needs a RestoreTarget")
    paths, owner_uid, _root = _prepare_paths(paths)
    target = Path(paths.local(paths.restore_target_path))
    if target.exists() or target.is_symlink():
        raise RecordError("restore target already exists")
    _atomic_create(target, _record_json(record.to_mapping()), owner_uid=owner_uid)


def replace_restore_target(paths: ManagedPaths, record: RestoreTarget) -> None:
    """Atomically replace the restore binding and flush its parent directory."""

    if not isinstance(record, RestoreTarget):
        raise TypeError("restore target replacement needs a RestoreTarget")
    paths, owner_uid, root = _prepare_paths(paths)
    target = Path(paths.local(paths.restore_target_path))
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
        raise RecordError("unable to replace restore target") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

__all__ = [
    "RestoreTarget",
    "replace_restore_target",
    "restore_target_sha256",
    "write_restore_target",
]
