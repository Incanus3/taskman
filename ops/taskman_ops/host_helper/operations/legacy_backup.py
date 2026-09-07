"""Private backup capability retained only for unmigrated operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import stat

from ..commands import run_command
from ..lifecycle import BackupRecord, LifecycleError


_COMMAND_TIMEOUT_SECONDS = 60.0


class BackupOperationFailure(LifecycleError):
    """Represent a legacy backup failure for rollback and restore only."""

    def __init__(
        self,
        stage: str,
        *,
        changed: bool = False,
        changed_stages: tuple[str, ...] = (),
        residue_paths: tuple[Path, ...] = (),
    ) -> None:
        super().__init__(f"{stage} backup stage failed")
        self.stage = stage
        self.changed = changed
        self.changed_stages = changed_stages or (("backup",) if changed else ())
        self.residue_paths = residue_paths


def create_validated_backup(
    store: object,
    records: object,
    *,
    operation_id: str,
    database: Mapping[str, object],
    credentials: Path,
    reason: str,
    current_release_id: str | None,
    candidate_release_id: str | None,
) -> tuple[BackupRecord, bool]:
    """Preserve the legacy backup input contract until its callers migrate."""

    backup_id = f"backup-{operation_id.removeprefix('op-')}"
    root = Path(store.backup_root)
    dump = root / f"{backup_id}.dump"
    existing = next((item for item in records.backups if item.backup_id == backup_id), None)
    if existing is not None:
        if (
            existing.reason != reason
            or existing.current_release_id != current_release_id
            or existing.candidate_release_id != candidate_release_id
            or existing.database != database["name"]
            or not existing.validated
        ):
            raise BackupOperationFailure("backup")
        validate_dump(credentials, Path(existing.dump_path.as_posix()))
        return existing, False
    temporary = root / f".{backup_id}.dump.tmp"
    try:
        source_size = _database_size(database, credentials)
        _dump(database, credentials, temporary)
        _safe_dump(temporary, store.owner_uid)
        validate_dump(credentials, temporary)
        digest = dump_digest(temporary)
        record = BackupRecord(
            1,
            backup_id,
            datetime.now(UTC).replace(microsecond=0),
            temporary.stat().st_size,
            source_size,
            str(database["name"]),
            current_release_id,
            candidate_release_id,
            reason,
            True,
            store.paths.backup_root / dump.name,
            digest,
        )
        os.replace(temporary, dump)
        _fsync_directory(root)
        store.write_backup(record)
        return record, True
    except BackupOperationFailure:
        raise
    except Exception as error:
        raise BackupOperationFailure(
            "backup",
            changed=dump.exists() and not dump.is_symlink(),
            residue_paths=tuple(path for path in (temporary, dump) if path.exists() or path.is_symlink()),
        ) from error


def prepare_backup_root(store: object) -> None:
    root = Path(store.backup_root)
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    details = root.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != store.owner_uid
        or details.st_mode & 0o7022
    ):
        raise LifecycleError("backup root is unsafe")


def safe_secret(path: Path, owner_uid: int) -> None:
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != owner_uid
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise LifecycleError("database credential input is unsafe")


def dump_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_dump(credentials: Path, destination: Path) -> None:
    run_command(
        ("pg_restore", "--list", destination.as_posix()),
        env={"PGPASSFILE": credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )


def bounded_capture(argv: tuple[str, ...], credentials: Path, *, timeout: float) -> bytes:
    return run_command(
        argv,
        env={"PGPASSFILE": credentials.as_posix()},
        timeout_seconds=timeout,
    ).stdout


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
        raise LifecycleError("database size evidence is invalid") from error
    if size <= 0:
        raise LifecycleError("database size evidence is invalid")
    return size


def _dump(database: Mapping[str, object], credentials: Path, destination: Path) -> None:
    run_command(
        (
            "pg_dump",
            "--format=custom",
            f"--file={destination.as_posix()}",
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
        env={"PGPASSFILE": credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )


def _safe_dump(path: Path, owner_uid: int) -> None:
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_size <= 0
    ):
        raise LifecycleError("backup dump is unsafe")
    os.chmod(path, 0o600)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
