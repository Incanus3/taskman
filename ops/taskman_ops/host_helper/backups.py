"""Shared completed-record PostgreSQL backup and retention capability."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from .commands import CommandError, run_command
from .credentials import CredentialError, validate_credentials
from .database import database_mapping
from ..checksums import sha256_file
from .filesystem import fsync_directory
from .paths import ManagedPaths
from .records import MAX_RECORD_BYTES, BackupRecord, RecordError, write_backup_manifest
from .state import HostState, observe_host_state


_TEMPORARY_DUMP_RE = re.compile(r"(?:\.backup-[0-9a-f]{32}\.dump\.tmp|backup-[0-9a-f]{32}\.dump)\Z")
_COMMAND_TIMEOUT_SECONDS = 60.0
_MINIMUM_CAPACITY_MARGIN_BYTES = 64 * 1024 * 1024


class BackupAuthorityError(ValueError):
    """A completed or recognizable backup cannot be safely reconciled."""


class BackupCapacityError(ValueError):
    """The backup filesystem cannot retain one validated database dump."""


def create_validated_backup(
    state: HostState,
    paths: ManagedPaths,
    database: Mapping[str, object],
    credentials: Path,
    *,
    purpose: str,
) -> BackupRecord:
    """Create one completed backup from already-observed selected state.

    Callers hold the shared lifecycle lock.  The capability deliberately takes
    an observed state rather than a controller request, so scheduled and
    interactive procedures cannot diverge on provenance or publication.
    """

    if not isinstance(state, HostState):
        raise TypeError("backup needs observed host state")
    if state.selected_release_id is None:
        raise BackupAuthorityError("backup requires a selected release")
    if state.database_state != "ready":
        raise BackupAuthorityError("backup requires an observed ready database")
    database = database_mapping(database)
    if purpose not in {"scheduled", "pre-deploy", "pre-rollback", "pre-restore"}:
        raise ValueError("invalid backup purpose")
    try:
        validate_credentials(credentials)
    except CredentialError as error:
        raise BackupAuthorityError(str(error)) from error
    prepare_backup_root(paths)

    backup_id = f"backup-{uuid4().hex}"
    root = Path(paths.local(paths.backup_root))
    temporary = root / f".{backup_id}.dump.tmp"
    published = root / f"{backup_id}.dump"
    source_size = database_size(database, credentials)
    ensure_capacity(root, source_size)
    environment = {"PGPASSFILE": credentials.as_posix()}

    run_command(
        (
            "pg_dump",
            "--format=custom",
            f"--file={temporary.as_posix()}",
            "--host",
            str(database["host"]),
            "--port",
            str(database["port"]),
            "--username",
            str(database["role"]),
            "--dbname",
            str(database["name"]),
            "--no-password",
        ),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )
    validate_dump(temporary)
    run_command(
        ("pg_restore", "--list", temporary.as_posix()),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )
    try:
        digest = sha256_file(temporary)
    except OSError as error:
        raise BackupAuthorityError("backup dump cannot be hashed") from error
    record = BackupRecord(
        backup_id=backup_id,
        created_at=datetime.now(UTC).replace(microsecond=0),
        dump_sha256=digest,
        source_release_id=state.selected_release_id,
        migration_versions=state.applied_migrations,
        source_database_size_bytes=source_size,
    )
    publish_dump(temporary, published)
    write_backup_manifest(paths, record)
    final_state = observe_host_state(
        paths,
        database={"state": state.database_state, "applied_migrations": state.applied_migrations},
    )
    if record not in final_state.backups:
        raise BackupAuthorityError("completed backup was not re-observed")
    return record


def normalize_temporary_dumps(paths: ManagedPaths, state: HostState) -> None:
    """Remove only recognized private temporary dumps after coherent observation."""

    if not isinstance(state, HostState):
        raise TypeError("incomplete backup normalization needs observed host state")
    root = Path(paths.local(paths.backup_root))
    for temporary in state.temporary_paths:
        entry = Path(paths.local(temporary))
        if entry.parent != root or _TEMPORARY_DUMP_RE.fullmatch(entry.name) is None:
            continue
        validate_dump(entry)
        entry.unlink()
        fsync_directory(root)


def validate_completed_backups(state: HostState, paths: ManagedPaths) -> None:
    """Reject a checksum-changing completed dump before a later mutation."""

    root = Path(paths.local(paths.backup_root))
    for record in state.backups:
        dump = root / f"{record.backup_id}.dump"
        validate_dump(dump)
        try:
            digest = sha256_file(dump)
        except OSError as error:
            raise BackupAuthorityError("backup dump cannot be hashed") from error
        if digest != record.dump_sha256:
            raise BackupAuthorityError("completed backup dump identity changed")
        validate_manifest_identity(root / f"{record.backup_id}.json", record)


def retained_backup_ids(state: HostState, retention: int) -> frozenset[str]:
    """Protect every selected backup plus the newest ordinary retained backups."""

    if not isinstance(state, HostState):
        raise TypeError("backup retention needs observed host state")
    if type(retention) is not int or not 1 <= retention <= 64:
        raise ValueError("backup retention is invalid")
    protected = {selection.backup_id for selection in state.selections if selection.backup_id}
    unprotected = [record for record in state.backups if record.backup_id not in protected]
    newest = sorted(unprotected, key=lambda record: (record.created_at, record.backup_id), reverse=True)
    protected.update(record.backup_id for record in newest[:retention])
    return frozenset(protected)


def prune_backups(paths: ManagedPaths, state: HostState, retention: int) -> HostState:
    """Delete only verified unprotected completed pairs, manifest before dump."""

    keep = retained_backup_ids(state, retention)
    for record in sorted(state.backups, key=lambda item: (item.created_at, item.backup_id)):
        if record.backup_id in keep:
            continue
        delete_completed_backup(paths, record)
    return observe_host_state(paths, database={"state": state.database_state, "applied_migrations": state.applied_migrations})


def delete_completed_backup(paths: ManagedPaths, record: BackupRecord) -> None:
    """Delete one completed pair only after checksum and identity revalidation."""

    if not isinstance(paths, ManagedPaths) or not isinstance(record, BackupRecord):
        raise TypeError("completed backup deletion needs managed paths and a record")
    root = Path(paths.local(paths.backup_root))
    dump = root / f"{record.backup_id}.dump"
    manifest = root / f"{record.backup_id}.json"
    dump_identity = validate_dump(dump)
    try:
        digest = sha256_file(dump)
    except OSError as error:
        raise BackupAuthorityError("backup dump cannot be hashed") from error
    if digest != record.dump_sha256 or validate_dump(dump) != dump_identity:
        raise BackupAuthorityError("completed backup dump identity changed")
    manifest_identity = validate_manifest_identity(manifest, record)
    _unlink_completed_pair(
        root,
        dump.name,
        dump_identity,
        manifest.name,
        manifest_identity,
    )


def prepare_backup_root(paths: ManagedPaths) -> None:
    if not isinstance(paths, ManagedPaths):
        raise TypeError("backup needs managed paths")
    owner_uid = os.geteuid()
    paths.validate_existing(owner_uid=owner_uid)
    root = Path(paths.local(paths.backup_root))
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    details = root.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
    ):
        raise BackupAuthorityError("backup root is unsafe")


def database_size(database: Mapping[str, object], credentials: Path) -> int:
    completed = run_command(
        (
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--host",
            str(database["host"]),
            "--port",
            str(database["port"]),
            "--username",
            str(database["role"]),
            "--dbname",
            str(database["name"]),
            "--no-password",
            "--command",
            "SELECT pg_database_size(current_database())",
        ),
        env={"PGPASSFILE": credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )
    try:
        size = int(completed.stdout.strip())
    except ValueError as error:
        raise BackupAuthorityError("database size evidence is invalid") from error
    if size <= 0:
        raise BackupAuthorityError("database size evidence is invalid")
    return size


def ensure_capacity(root: Path, database_bytes: int) -> None:
    if type(database_bytes) is not int or database_bytes <= 0:
        raise BackupAuthorityError("database size evidence is invalid")
    try:
        filesystem = os.statvfs(root)
    except OSError as error:
        raise BackupAuthorityError("backup capacity is unavailable") from error
    available = filesystem.f_bavail * filesystem.f_frsize
    margin = max((database_bytes + 9) // 10, _MINIMUM_CAPACITY_MARGIN_BYTES)
    if available < database_bytes + margin:
        raise BackupCapacityError("backup capacity is insufficient")


def publish_dump(temporary: Path, published: Path) -> None:
    try:
        os.link(temporary, published)
    except FileExistsError as error:
        raise BackupAuthorityError("completed backup dump already exists") from error
    except OSError as error:
        raise BackupAuthorityError("unable to publish completed backup dump") from error
    try:
        temporary.unlink()
    except OSError as error:
        raise BackupAuthorityError("unable to finalize completed backup dump") from error
    fsync_directory(published.parent)


def validate_dump(path: Path) -> tuple[int, int]:
    try:
        details = path.lstat()
    except OSError as error:
        raise BackupAuthorityError("backup dump is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_size <= 0
        or details.st_mode & 0o7022
    ):
        raise BackupAuthorityError("backup dump is unsafe")
    os.chmod(path, 0o600)
    return details.st_dev, details.st_ino


def validate_private_file(path: Path) -> tuple[int, int]:
    try:
        details = path.lstat()
    except OSError as error:
        raise BackupAuthorityError("backup manifest is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise BackupAuthorityError("backup manifest is unsafe")
    return details.st_dev, details.st_ino


def validate_manifest_identity(path: Path, expected: BackupRecord) -> tuple[int, int]:
    """Re-read the manifest immediately before a destructive retention action."""

    initial_identity = validate_private_file(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise BackupAuthorityError("backup manifest is unavailable") from error
    if len(raw) > MAX_RECORD_BYTES:
        raise BackupAuthorityError("backup manifest is oversized")
    try:
        observed = BackupRecord.from_mapping(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecordError, ValueError, TypeError) as error:
        raise BackupAuthorityError("backup manifest is invalid") from error
    if observed != expected:
        raise BackupAuthorityError("backup manifest identity changed")
    if validate_private_file(path) != initial_identity:
        raise BackupAuthorityError("backup manifest identity changed")
    return initial_identity


def _unlink_completed_pair(
    root: Path,
    dump_name: str,
    dump_identity: tuple[int, int],
    manifest_name: str,
    manifest_identity: tuple[int, int],
) -> None:
    """Remove a validated completed pair only while both directory entries are unchanged."""

    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        raise BackupAuthorityError("backup root is unavailable") from error
    try:
        _require_identity(descriptor, dump_name, dump_identity, "backup dump")
        _require_identity(descriptor, manifest_name, manifest_identity, "backup manifest")
        os.unlink(manifest_name, dir_fd=descriptor)
        os.fsync(descriptor)
        _require_identity(descriptor, dump_name, dump_identity, "backup dump")
        os.unlink(dump_name, dir_fd=descriptor)
        os.fsync(descriptor)
    except OSError as error:
        raise BackupAuthorityError("completed backup deletion failed") from error
    finally:
        os.close(descriptor)


def _require_identity(
    descriptor: int, name: str, expected: tuple[int, int], label: str
) -> None:
    try:
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except OSError as error:
        raise BackupAuthorityError(f"{label} identity changed") from error
    if (current.st_dev, current.st_ino) != expected:
        raise BackupAuthorityError(f"{label} identity changed")


__all__ = [
    "BackupAuthorityError",
    "BackupCapacityError",
    "CommandError",
    "create_validated_backup",
    "delete_completed_backup",
    "normalize_temporary_dumps",
    "prepare_backup_root",
    "prune_backups",
    "retained_backup_ids",
    "validate_completed_backups",
]
