"""Shared completed-record PostgreSQL backup and retention capability."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from .commands import CommandError, run_command
from .paths import ManagedPaths, PathAuthorityError
from .records import MAX_RECORD_BYTES, BackupRecord, RecordError, write_backup_manifest
from .state import HostState, StateAmbiguityError, observe_host_state


_DATABASE_KEYS = frozenset({"host", "name", "port", "role"})
_TEMPORARY_DUMP_RE = re.compile(r"\.backup-[0-9a-f]{32}\.dump\.tmp\Z")
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
    database = validate_database(database)
    if purpose not in {"scheduled", "pre-deploy", "pre-rollback", "pre-restore"}:
        raise ValueError("invalid backup purpose")
    validate_credentials(credentials)
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
    digest = sha256(temporary)
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


def observe_database_state(database: Mapping[str, object], credentials: Path) -> dict[str, object]:
    """Read the applied migration versions from the database that will be dumped."""

    database = validate_database(database)
    validate_credentials(credentials)
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
            "SELECT version FROM schema_migrations ORDER BY version",
        ),
        env={"PGPASSFILE": credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )
    migrations: list[int] = []
    for line in completed.stdout.decode("utf-8", "strict").splitlines():
        value = line.strip()
        if not value:
            continue
        if not value.isdecimal():
            raise BackupAuthorityError("database migration evidence is invalid")
        migrations.append(int(value))
    if migrations != sorted(set(migrations)):
        raise BackupAuthorityError("database migration evidence is invalid")
    return {"state": "ready", "applied_migrations": tuple(migrations)}


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
        if sha256(dump) != record.dump_sha256:
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
    root = Path(paths.local(paths.backup_root))
    for record in sorted(state.backups, key=lambda item: (item.created_at, item.backup_id)):
        if record.backup_id in keep:
            continue
        dump = root / f"{record.backup_id}.dump"
        manifest = root / f"{record.backup_id}.json"
        validate_dump(dump)
        if sha256(dump) != record.dump_sha256:
            raise BackupAuthorityError("completed backup dump identity changed")
        validate_manifest_identity(manifest, record)
        manifest.unlink()
        fsync_directory(root)
        dump.unlink()
        fsync_directory(root)
    return observe_host_state(paths, database={"state": state.database_state, "applied_migrations": state.applied_migrations})


def validate_database(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != _DATABASE_KEYS:
        raise ValueError("backup database settings are invalid")
    if (
        type(value["host"]) is not str
        or not value["host"]
        or type(value["name"]) is not str
        or not value["name"]
        or type(value["role"]) is not str
        or not value["role"]
        or type(value["port"]) is not int
        or not 0 < value["port"] < 65536
    ):
        raise ValueError("backup database settings are invalid")
    return value


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


def validate_credentials(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise ValueError("backup credentials are unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise ValueError("backup credentials are unsafe")


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


def validate_dump(path: Path) -> None:
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


def validate_private_file(path: Path) -> None:
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


def validate_manifest_identity(path: Path, expected: BackupRecord) -> None:
    """Re-read the manifest immediately before a destructive retention action."""

    validate_private_file(path)
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise BackupAuthorityError("backup dump cannot be hashed") from error
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "BackupAuthorityError",
    "BackupCapacityError",
    "CommandError",
    "create_validated_backup",
    "normalize_temporary_dumps",
    "observe_database_state",
    "prepare_backup_root",
    "prune_backups",
    "retained_backup_ids",
    "validate_completed_backups",
]
