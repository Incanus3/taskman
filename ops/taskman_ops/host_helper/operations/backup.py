"""Replayable creation of one validated PostgreSQL backup."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION

from ..commands import CommandError, run_command
from ..lock import LifecycleLockContention, lifecycle_lock
from ..paths import ManagedPaths, PathAuthorityError
from ..records import BackupRecord, RecordError, write_backup_manifest
from ..state import HostState, StateAmbiguityError, observe_host_state


_PARAMETER_KEYS = frozenset({"credentials_path", "database", "purpose"})
_DATABASE_KEYS = frozenset({"host", "name", "port", "role"})
_TEMPORARY_DUMP_RE = re.compile(r"\.backup-[0-9a-f]{32}\.dump\.tmp\Z")
_DUMP_RE = re.compile(r"backup-[0-9a-f]{32}\.dump\Z")
_LOCK_TIMEOUT_SECONDS = 5.0
_COMMAND_TIMEOUT_SECONDS = 60.0


class BackupAuthorityError(ValueError):
    """A completed or recognizable backup cannot be safely reconciled."""


def backup(request: HostRequest) -> HostResult:
    """Create a backup from observed state and publish only its completed record."""

    state: HostState | None = None
    try:
        credentials, database, purpose = _inputs(request)
        paths = ManagedPaths.from_mapping(request.paths)
        _validate_authoritative_paths(paths)
        with lifecycle_lock(paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
            _prepare_backup_root(paths)
            _normalize_incomplete_backups(paths)
            state = observe_host_state(paths)
            _validate_completed_backups(state, paths)
            _safe_credentials(credentials)
            record = create_validated_backup(
                state,
                paths,
                database,
                credentials,
                purpose=purpose,
            )
            final_state = observe_host_state(paths)
            dump = Path(paths.local(paths.backup_root / f"{record.backup_id}.dump"))
            facts = {
                "backup_id": record.backup_id,
                "dump_path": dump.as_posix(),
                "size_bytes": dump.stat().st_size,
                "source_database_size_bytes": record.source_database_size_bytes,
                "selected_release_id": final_state.selected_release_id,
                "service_state": final_state.service_state,
                "database_state": final_state.database_state,
            }
    except LifecycleLockContention:
        return _result(request, "retryable", "lifecycle lock is unavailable", {"locked": True})
    except (StateAmbiguityError, BackupAuthorityError):
        return _result(
            request,
            "manual",
            "backup authority is contradictory",
            _state_projection(state),
        )
    except (PathAuthorityError, TypeError, ValueError):
        return _result(request, "refused", "backup request is unsafe", _state_projection(state))
    except (CommandError, RecordError, OSError):
        return _result(
            request,
            "retryable",
            "backup did not complete; rerun to converge",
            _state_projection(state),
        )

    return _result(
        request,
        "succeeded",
        "validated backup completed",
        facts,
        final_state.warnings,
    )


def create_validated_backup(
    state: HostState,
    paths: ManagedPaths,
    database: Mapping[str, object],
    credentials: Path,
    *,
    purpose: str,
) -> BackupRecord:
    """Dump, validate, atomically publish, then record one completed backup."""

    if not isinstance(state, HostState):
        raise TypeError("backup needs observed host state")
    if state.selected_release_id is None:
        raise BackupAuthorityError("backup requires a selected release")
    database = _database(database)
    if purpose not in {"scheduled", "pre-deploy", "pre-rollback", "pre-restore"}:
        raise ValueError("invalid backup purpose")
    _safe_credentials(credentials)
    _prepare_backup_root(paths)

    backup_id = f"backup-{uuid4().hex}"
    root = Path(paths.local(paths.backup_root))
    temporary = root / f".{backup_id}.dump.tmp"
    published = root / f"{backup_id}.dump"
    source_size = _database_size(database, credentials)
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
    _safe_dump(temporary)
    run_command(
        ("pg_restore", "--list", temporary.as_posix()),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )
    digest = _sha256(temporary)
    record = BackupRecord(
        backup_id=backup_id,
        dump_sha256=digest,
        source_release_id=state.selected_release_id,
        migration_versions=state.applied_migrations,
        source_database_size_bytes=source_size,
    )
    os.replace(temporary, published)
    _fsync_directory(root)
    write_backup_manifest(paths, record)
    return record


def _inputs(request: HostRequest) -> tuple[Path, Mapping[str, object], str]:
    if not isinstance(request, HostRequest):
        raise TypeError("backup needs a host request")
    if set(request.expected_state) or set(request.parameters) != _PARAMETER_KEYS:
        raise ValueError("backup request is incomplete")
    credentials = request.parameters["credentials_path"]
    purpose = request.parameters["purpose"]
    if type(credentials) is not str or not Path(credentials).is_absolute():
        raise ValueError("backup credentials path is invalid")
    if type(purpose) is not str:
        raise ValueError("backup purpose is invalid")
    return Path(credentials), _database(request.parameters["database"]), purpose


def _database(value: object) -> Mapping[str, object]:
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


def _prepare_backup_root(paths: ManagedPaths) -> None:
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


def _validate_authoritative_paths(paths: ManagedPaths) -> None:
    """Classify unsafe managed roots as ambiguous before lock acquisition."""

    try:
        paths.validate_existing(owner_uid=os.geteuid())
    except PathAuthorityError as error:
        raise StateAmbiguityError("managed backup authority is unsafe") from error


def _safe_credentials(path: Path) -> None:
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


def _database_size(database: Mapping[str, object], credentials: Path) -> int:
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


def _normalize_incomplete_backups(paths: ManagedPaths) -> None:
    """Remove only exact Taskman temporary names and manifest-less publications."""

    root = Path(paths.local(paths.backup_root))
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if _TEMPORARY_DUMP_RE.fullmatch(entry.name):
            _safe_dump(entry)
            entry.unlink()
            _fsync_directory(root)
        elif _DUMP_RE.fullmatch(entry.name):
            manifest = root / f"{entry.stem}.json"
            if not (manifest.exists() or manifest.is_symlink()):
                _safe_dump(entry)
                entry.unlink()
                _fsync_directory(root)


def _validate_completed_backups(state: HostState, paths: ManagedPaths) -> None:
    root = Path(paths.local(paths.backup_root))
    for record in state.backups:
        dump = root / f"{record.backup_id}.dump"
        _safe_dump(dump)
        if _sha256(dump) != record.dump_sha256:
            raise BackupAuthorityError("completed backup dump identity changed")


def _safe_dump(path: Path) -> None:
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise BackupAuthorityError("backup dump cannot be hashed") from error
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _state_projection(state: HostState | None) -> dict[str, object]:
    return {} if state is None else {"selected_release_id": state.selected_release_id}


def _result(
    request: HostRequest,
    outcome: str,
    message: str,
    state: Mapping[str, object],
    warnings: tuple[str, ...] = (),
) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome=outcome,
        message=message,
        state=state,
        warnings=warnings,
    )


__all__ = ["backup", "create_validated_backup"]
