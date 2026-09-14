"""Durable protection records for backups taken before migrations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import TYPE_CHECKING

from taskman_ops.releases.identifiers import validate_release_id

from .filesystem import fsync_directory
from .paths import ManagedPaths, PathAuthorityError
from .records import (
    MAX_RECORD_BYTES,
    RecordError,
    SelectionRecord,
    _atomic_create,
    _open_temporary,
    _safe_directory,
    _safe_file,
    append_selection,
)

if TYPE_CHECKING:
    from .state import HostState


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
_BACKUP_PROTECTION_FILE_RE = re.compile(r"backup-[0-9a-f]{32}\.json\Z")
_RETIREMENT_DIRECTORY = "backup-protection-retirements"


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


def allocate_protection_attempt(
    protections: Iterable[BackupProtection], base_selection_id: str | None
) -> int:
    """Allocate the next monotonic migration-attempt number for one baseline.

    Callers hold the lifecycle lock and publish the resulting protection before
    retiring any older attempt.  The newest retained attempt keeps the sequence
    durable, so bounded intermediate pruning cannot cause a number to repeat.
    """

    base_selection_id = _selection_id(base_selection_id)
    matching = _protections_for_baseline(protections, base_selection_id)
    if not matching:
        return 0
    attempt_numbers = {item.attempt_number for item in matching}
    if 0 not in attempt_numbers or len(attempt_numbers) != len(matching):
        raise RecordError("backup protection attempts are contradictory")
    return max(attempt_numbers) + 1


def register_backup_protection(
    paths: ManagedPaths,
    protections: Iterable[BackupProtection],
    *,
    backup_id: str,
    base_selection_id: str | None,
    target_release_id: str,
    created_at: datetime | None = None,
) -> BackupProtection:
    """Publish the fresh migration-attempt reference before any retirement.

    The lifecycle-lock caller must finish a previously confirmed prune before
    it creates another backup.  This keeps one temporary sixth protection
    inspectable without turning retries into a count limit.
    """

    base_selection_id = _selection_id(base_selection_id)
    backup_id = _backup_id(backup_id)
    if _read_retirement_protections(paths):
        raise RecordError("confirmed backup-protection retirement must finish before a fresh attempt")
    try:
        target_release_id = validate_release_id(target_release_id)
    except (TypeError, ValueError) as error:
        raise RecordError("invalid target release identifier") from error
    current = _protections_for_baseline(protections, base_selection_id)
    if protection_prune_ids(current, base_selection_id):
        raise RecordError("confirmed backup-protection pruning must finish before a fresh attempt")
    record = BackupProtection(
        schema_version=1,
        backup_id=backup_id,
        base_selection_id=base_selection_id,
        target_release_id=target_release_id,
        attempt_number=allocate_protection_attempt(current, base_selection_id),
        created_at=(created_at or datetime.now(UTC).replace(microsecond=0)),
    )
    write_backup_protection(paths, record)
    return record


def protection_prune_ids(
    protections: Iterable[BackupProtection],
    base_selection_id: str | None,
    *,
    independently_held_backup_ids: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return the exact removable intermediate protections for one baseline.

    Original attempt zero and the newest attempt always remain.  A separately
    history- or restore-held backup keeps its data but is retired from this
    bounded attempt set and does not occupy one of its three intermediate
    slots.  The caller confirms this sorted ID set while holding the lock.
    """

    base_selection_id = _selection_id(base_selection_id)
    matching = _protections_for_baseline(protections, base_selection_id)
    held = _held_backup_ids(independently_held_backup_ids)
    if not matching:
        return ()
    attempt_numbers = {item.attempt_number for item in matching}
    if 0 not in attempt_numbers or len(attempt_numbers) != len(matching):
        raise RecordError("backup protection attempts are contradictory")
    newest = max(attempt_numbers)
    eligible = [
        item
        for item in matching
        if item.attempt_number not in {0, newest} and item.backup_id not in held
    ]
    retained = {
        item.backup_id
        for item in sorted(eligible, key=lambda item: item.attempt_number, reverse=True)[:3]
    }
    return tuple(
        sorted(
            item.backup_id
            for item in matching
            if item.attempt_number not in {0, newest} and item.backup_id not in retained
        )
    )


def _protections_for_baseline(
    protections: Iterable[BackupProtection], base_selection_id: str | None
) -> tuple[BackupProtection, ...]:
    try:
        values = tuple(protections)
    except TypeError as error:
        raise TypeError("backup protections must be iterable") from error
    if not all(isinstance(item, BackupProtection) for item in values):
        raise TypeError("backup protections must contain BackupProtection records")
    return tuple(item for item in values if item.base_selection_id == base_selection_id)


def _held_backup_ids(backup_ids: Iterable[str]) -> frozenset[str]:
    try:
        values = frozenset(backup_ids)
    except TypeError as error:
        raise TypeError("independent backup references must be iterable") from error
    if any(type(item) is not str or _BACKUP_ID_RE.fullmatch(item) is None for item in values):
        raise RecordError("independent backup reference is invalid")
    return values


def retire_protection_attempts(
    paths: ManagedPaths,
    state: HostState,
    base_selection_id: str | None,
    confirmed_prune_ids: Iterable[str],
) -> None:
    """Retire exactly confirmed migration-attempt references under the lock.

    Reference removal is fsynced before a completed pair can be deleted.  If
    another history, restore, or protection reference names that backup, the
    attempt entry is still retired but the independently held pair remains.
    """

    try:
        protections = tuple(state.backup_protections)
        pending = tuple(state.retiring_backup_protections)
        backups = {item.backup_id: item for item in state.backups}
    except AttributeError as error:
        raise TypeError("protection retirement needs observed host state") from error
    base_selection_id = _selection_id(base_selection_id)
    independent = _independent_state_backup_ids(state)
    if pending:
        if any(item.base_selection_id != base_selection_id for item in pending):
            raise RecordError("pending backup-protection retirement has another baseline")
        expected = tuple(sorted(item.backup_id for item in pending))
    else:
        expected = protection_prune_ids(
            protections,
            base_selection_id,
            independently_held_backup_ids=independent,
        )
    confirmed = _confirmed_prune_ids(confirmed_prune_ids)
    if confirmed != expected:
        raise RecordError("confirmed backup-protection prune set changed")
    retiring = pending or tuple(
        protection
        for protection in _protections_for_baseline(protections, base_selection_id)
        if protection.backup_id in confirmed
    )
    if not pending:
        _mark_retiring_protections(paths, retiring)
    remaining_protection_ids = {
        item.backup_id for item in protections if item.backup_id not in confirmed
    }
    for backup_id in confirmed:
        if backup_id in independent or backup_id in remaining_protection_ids:
            _remove_retirement_marker(paths, backup_id)
            continue
        record = backups.get(backup_id)
        _finish_retired_backup(paths, backup_id, record)
        _remove_retirement_marker(paths, backup_id)


def backup_protection_retirement_root(paths: ManagedPaths) -> Path:
    """Return the fixed host-local directory for pending pair retirements."""

    if not isinstance(paths, ManagedPaths):
        raise TypeError("backup protection retirement needs managed paths")
    return Path(paths.local(paths.deployment_root / _RETIREMENT_DIRECTORY))


def backup_protection_retirement_path(paths: ManagedPaths, backup_id: str) -> Path:
    """Return one deterministic pending-retirement marker path."""

    return backup_protection_retirement_root(paths) / f"{_backup_id(backup_id)}.json"


def _prepare_retirement_root(paths: ManagedPaths) -> tuple[int, Path]:
    _paths, owner_uid, _active_root = _prepare_paths(paths)
    root = backup_protection_retirement_root(paths)
    try:
        root.mkdir(mode=0o750, exist_ok=True)
    except OSError as error:
        raise RecordError("unable to prepare backup-protection retirement directory") from error
    _safe_directory(root, owner_uid=owner_uid)
    return owner_uid, root


def _read_retirement_protections(paths: ManagedPaths) -> tuple[BackupProtection, ...]:
    if not isinstance(paths, ManagedPaths):
        raise TypeError("backup protection retirement needs managed paths")
    owner_uid = os.geteuid()
    try:
        paths.validate_existing(owner_uid=owner_uid)
    except PathAuthorityError as error:
        raise RecordError(str(error)) from error
    root = backup_protection_retirement_root(paths)
    try:
        details = root.lstat()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise RecordError("unable to inspect backup-protection retirement directory") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
    ):
        raise RecordError("backup-protection retirement directory is unsafe")
    result: list[BackupProtection] = []
    try:
        entries = tuple(sorted(root.iterdir(), key=lambda item: item.name))
    except OSError as error:
        raise RecordError("unable to enumerate backup-protection retirements") from error
    for entry in entries:
        if _BACKUP_PROTECTION_FILE_RE.fullmatch(entry.name) is None:
            raise RecordError("unknown backup-protection retirement entry")
        _safe_file(entry, owner_uid=owner_uid)
        try:
            raw = entry.read_bytes()
            if len(raw) > MAX_RECORD_BYTES:
                raise RecordError("backup protection is oversized")
            record = BackupProtection.from_mapping(json.loads(raw.decode("utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise RecordError("backup-protection retirement marker is invalid") from error
        if entry.stem != record.backup_id:
            raise RecordError("backup-protection retirement identity conflicts with its path")
        result.append(record)
    return tuple(result)


def _mark_retiring_protections(
    paths: ManagedPaths, protections: Iterable[BackupProtection]
) -> None:
    protections = tuple(sorted(protections, key=lambda item: item.backup_id))
    if not protections:
        return
    owner_uid, retirement_root = _prepare_retirement_root(paths)
    active_root = Path(paths.local(paths.backup_protection_root))
    for protection in protections:
        source = Path(paths.local(paths.backup_protection(protection.backup_id)))
        target = backup_protection_retirement_path(paths, protection.backup_id)
        _safe_file(source, owner_uid=owner_uid)
        if target.exists() or target.is_symlink():
            raise RecordError("backup-protection retirement marker already exists")
        try:
            os.replace(source, target)
            fsync_directory(active_root)
            fsync_directory(retirement_root)
        except OSError as error:
            raise RecordError("unable to mark backup protection for retirement") from error


def _finish_retired_backup(
    paths: ManagedPaths, backup_id: str, record: object | None
) -> None:
    root = Path(paths.local(paths.backup_root))
    manifest = root / f"{backup_id}.json"
    dump = root / f"{backup_id}.dump"
    manifest_exists = manifest.exists() or manifest.is_symlink()
    dump_exists = dump.exists() or dump.is_symlink()
    if manifest_exists and dump_exists:
        if record is None:
            raise RecordError("pending retirement completed backup is not observed")
        delete_completed_backup(paths, record)
        return
    if manifest_exists:
        raise RecordError("pending retirement backup manifest has no dump")
    if dump_exists:
        from .backups import validate_dump

        validate_dump(dump)
        try:
            dump.unlink()
            fsync_directory(root)
        except OSError as error:
            raise RecordError("completed backup deletion failed") from error


def _remove_retirement_marker(paths: ManagedPaths, backup_id: str) -> None:
    owner_uid, root = _prepare_retirement_root(paths)
    marker = backup_protection_retirement_path(paths, backup_id)
    _safe_file(marker, owner_uid=owner_uid)
    try:
        marker.unlink()
        fsync_directory(root)
    except OSError as error:
        raise RecordError("unable to remove backup-protection retirement marker") from error


def _independent_state_backup_ids(state: HostState) -> frozenset[str]:
    try:
        result = set(state.successful_backup_ids)
        target = state.restore_target
    except AttributeError as error:
        raise TypeError("protection retirement needs observed host state") from error
    if target is None:
        return frozenset(result)
    result.update({target.backup_id, target.safety_backup_id})
    result.update(str(item["backup_id"]) for item in target.safety_backup_attempts)
    if target.replacement is not None:
        result.add(str(target.replacement["backup_id"]))
    return frozenset(result)


def _confirmed_prune_ids(value: Iterable[str]) -> tuple[str, ...]:
    try:
        result = tuple(value)
    except TypeError as error:
        raise TypeError("confirmed backup-protection prune IDs must be iterable") from error
    if (
        any(type(item) is not str or _BACKUP_ID_RE.fullmatch(item) is None for item in result)
        or result != tuple(sorted(result))
        or len(set(result)) != len(result)
    ):
        raise RecordError("confirmed backup-protection prune IDs must be sorted and unique")
    return result


def delete_completed_backup(paths: ManagedPaths, record: object) -> None:
    """Import the pair-deletion boundary lazily to keep record readers acyclic."""

    from .backups import delete_completed_backup as delete

    delete(paths, record)  # type: ignore[arg-type]


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


def complete_successful_selection(
    paths: ManagedPaths,
    state: HostState,
    *,
    release_id: str,
    observed_previous_release_id: str | None,
    backup_id: str | None = None,
    recovery_backup_ids: Iterable[str] = (),
    selected_at: datetime | None = None,
) -> tuple[SelectionRecord, bool]:
    """Publish verified success, then retire protections whose references are durable.

    ``state`` is the already validated locked host observation.  The function
    deliberately owns both publication and protection retirement so callers
    cannot reverse their crash-safe ordering.
    """

    try:
        selected_release_id = state.selected_release_id
        latest = state.latest_successful_selection
        latest_filename = state.latest_successful_selection_filename
        protections = tuple(state.backup_protections)
        successful_backup_ids = frozenset(state.successful_backup_ids)
        backups = {item.backup_id for item in state.backups}
    except AttributeError as error:
        raise TypeError("successful selection needs observed host state") from error
    try:
        release_id = validate_release_id(release_id)
        observed_previous_release_id = (
            None
            if observed_previous_release_id is None
            else validate_release_id(observed_previous_release_id)
        )
    except (TypeError, ValueError) as error:
        raise RecordError("successful selection release identity is invalid") from error
    if selected_release_id != release_id:
        raise RecordError("successful selection does not match physical current")

    resolved: list[BackupProtection] = []
    unresolved: list[BackupProtection] = []
    for protection in protections:
        if protection.backup_id in successful_backup_ids:
            resolved.append(protection)
        elif latest is None and protection.base_selection_id is None:
            unresolved.append(protection)
        elif latest is not None and protection.base_selection_id == latest_filename:
            unresolved.append(protection)
        elif protection.base_selection_id is None:
            raise RecordError("null-baseline protection can resolve only into the first success")
        else:
            raise RecordError("backup protection baseline is not the latest successful selection")

    try:
        supplied_recovery_ids = tuple(recovery_backup_ids)
    except TypeError as error:
        raise RecordError("successful recovery backup references are invalid") from error
    referenced_ids = tuple(
        sorted({*supplied_recovery_ids, *(item.backup_id for item in unresolved)})
    )
    if any(type(item) is not str or _BACKUP_ID_RE.fullmatch(item) is None for item in referenced_ids):
        raise RecordError("successful recovery backup references are invalid")
    if any(item not in backups for item in referenced_ids):
        raise RecordError("successful selection references an unknown recovery backup")
    if backup_id is not None:
        backup_id = _backup_id(backup_id, "successful backup identifier")
        if backup_id not in backups:
            raise RecordError("successful selection references an unknown backup")
    elif unresolved:
        backup_id = max(unresolved, key=lambda item: item.attempt_number).backup_id

    generic_no_op = (
        latest is not None
        and latest.release_id == release_id
        and observed_previous_release_id == release_id
        and backup_id is None
        and not referenced_ids
    )
    exact_completed_retry = (
        latest is not None
        and latest.release_id == release_id
        and not unresolved
        and latest.observed_previous_release_id == observed_previous_release_id
        and latest.backup_id == backup_id
        and latest.recovery_backup_ids == referenced_ids
    )
    if generic_no_op or exact_completed_retry:
        _remove_resolved_protections(paths, resolved)
        return latest, False

    if latest is None:
        previous_release_id = None
    else:
        if observed_previous_release_id is None:
            raise RecordError("later success must record its observed previous release")
        previous_release_id = latest.release_id

    timestamp = selected_at or datetime.now(UTC).replace(microsecond=0)
    if latest is not None:
        timestamp = max(timestamp, latest.selected_at + timedelta(seconds=1))
    record = SelectionRecord(
        release_id,
        previous_release_id,
        backup_id,
        timestamp,
        2,
        observed_previous_release_id,
        referenced_ids,
    )
    append_selection(paths, record)
    _remove_resolved_protections(paths, (*resolved, *unresolved))
    return record, True


def _remove_resolved_protections(
    paths: ManagedPaths,
    protections: Iterable[BackupProtection],
) -> None:
    protections = tuple(sorted(protections, key=lambda item: item.backup_id))
    if not protections:
        return
    paths, owner_uid, root = _prepare_paths(paths)
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        raise RecordError("unable to open backup protection directory") from error
    try:
        for protection in protections:
            target = Path(paths.local(paths.backup_protection(protection.backup_id)))
            _safe_file(target, owner_uid=owner_uid)
            os.unlink(target.name, dir_fd=descriptor)
            os.fsync(descriptor)
    except (OSError, RecordError) as error:
        raise RecordError("unable to remove resolved backup protection") from error
    finally:
        os.close(descriptor)

__all__ = [
    "BackupProtection",
    "allocate_protection_attempt",
    "backup_protection_sha256",
    "backup_protection_retirement_path",
    "backup_protection_retirement_root",
    "complete_successful_selection",
    "protection_prune_ids",
    "register_backup_protection",
    "retire_protection_attempts",
    "replace_backup_protection",
    "write_backup_protection",
]
