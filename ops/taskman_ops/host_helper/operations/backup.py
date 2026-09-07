"""Host-local validated PostgreSQL custom-format backups."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import time

from taskman_ops.host_protocol import PROTOCOL_VERSION

from ..lifecycle import BackupRecord, LifecycleError, LifecycleLockContention, LifecycleStore
from ..legacy_result import OperationRequest as HostRequest, OperationResult as HostResult, validate_private_operation_id
from ..paths import ManagedPaths, PathAuthorityError


_PARAMETER_KEYS = frozenset({"credentials_path", "database", "reason", "retention"})
_DATABASE_KEYS = frozenset({"host", "name", "port", "role"})


class BackupOperationFailure(LifecycleError):
    """A backup failure with conservative durable-effect evidence."""

    def __init__(
        self,
        stage: str,
        *,
        changed: bool = False,
        changed_stages: tuple[str, ...] = (),
        residue_paths: tuple[Path, ...] = (),
        pruned_backup_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(f"{stage} backup stage failed")
        self.stage = stage
        self.changed = changed
        self.changed_stages = changed_stages or (("backup",) if changed else ())
        self.residue_paths = residue_paths
        self.pruned_backup_ids = pruned_backup_ids


class ForeignBackupPublication(LifecycleError):
    """Validated residue belonging to one other resumable backup operation."""

    def __init__(self, operation_id: str, residue_paths: tuple[Path, ...]) -> None:
        self.operation_id = operation_id
        self.residue_paths = residue_paths
        super().__init__("another backup publication is incomplete")


def backup(request: HostRequest) -> HostResult:
    """Create a validated dump and publish its record only after validation."""

    try:
        inputs = _inputs(request)
        paths = ManagedPaths.from_mapping(request.paths)
        store = LifecycleStore(paths)
        paths.validate_existing(owner_uid=store.owner_uid)
        with store.exclusive_lifecycle_lock(operation="backup"):
            records = store.read()
            observed_ids = tuple(record.backup_id for record in records.backups)
            correlated_id = (
                f"backup-{request.operation_id.removeprefix('op-')}"
            )
            correlated_rerun = correlated_id in observed_ids
            if (
                request.expected_state["backup_ids"] != observed_ids
                and not correlated_rerun
            ):
                return _refused(request)
            _safe_secret(inputs.credentials_path, store.owner_uid)
            _prepare_backup_root(store)
            record, changed = create_validated_backup(
                store,
                records,
                operation_id=request.operation_id,
                database=inputs.database,
                credentials=inputs.credentials_path,
                reason=inputs.reason,
                current_release_id=records.current_release_id,
                candidate_release_id=None,
            )
            try:
                removed = _prune_scheduled_backups(
                    store,
                    store.read(),
                    credentials=inputs.credentials_path,
                    keep=inputs.retention,
                )
            except BackupOperationFailure as error:
                prefix = ("backup", "records") if changed else ()
                raise BackupOperationFailure(
                    error.stage,
                    changed=bool(prefix) or error.changed,
                    changed_stages=(*prefix, *error.changed_stages),
                    residue_paths=error.residue_paths,
                    pruned_backup_ids=error.pruned_backup_ids,
                ) from error
            except (LifecycleError, OSError) as error:
                raise BackupOperationFailure(
                    "cleanup",
                    changed=changed,
                    changed_stages=("backup", "records") if changed else (),
                ) from error
    except LifecycleLockContention as error:
        return _locked(request, error)
    except ForeignBackupPublication as error:
        return _foreign_incomplete(request, error)
    except BackupOperationFailure as error:
        return _failed(request, error)
    except (LifecycleError, PathAuthorityError, OSError, ValueError, subprocess.SubprocessError):
        return _refused(request)

    changed_stages = ("backup", "records") if changed else ()
    if removed:
        changed_stages = (*changed_stages, "cleanup")
    return HostResult(
        protocol_version=PROTOCOL_VERSION, operation=request.operation, operation_id=request.operation_id,
        outcome="succeeded" if changed_stages else "no_change", stage="records", changed_stages=changed_stages,
        lifecycle={
            "backup_id": record.backup_id,
            "dump_path": record.dump_path.as_posix(),
            "size_bytes": record.size_bytes,
            "source_database_size_bytes": record.source_database_size_bytes,
            "reason": record.reason,
            "pruned_backup_ids": removed,
        },
        runtime_state={},
        verification={"format": "custom", "validated": True}, residue_paths=(), recovery_actions=(), warnings=(),
    )


def create_validated_backup(
    store: LifecycleStore,
    records: object,
    *,
    operation_id: str,
    database: Mapping[str, object],
    credentials: Path,
    reason: str,
    current_release_id: str | None,
    candidate_release_id: str | None,
) -> tuple[BackupRecord, bool]:
    """Create or revalidate the exact operation-correlated backup."""

    backup_id = f"backup-{operation_id.removeprefix('op-')}"
    dump = store.backup_root / f"{backup_id}.dump"
    pending = store.backup_root / f".{backup_id}.pending.json"
    # This operation-bound name is durable recovery authority: a retry can
    # prove the exact pre-rename dump rather than discovering a new temp file.
    temporary = store.backup_root / f".{backup_id}.tmp"
    other_incomplete = tuple(
        path
        for pattern in (".backup-*.pending.json", ".backup-*.tmp")
        for path in store.backup_root.glob(pattern)
        if path not in {pending, temporary}
    )
    if other_incomplete:
        raise _validated_foreign_publication(store, other_incomplete)
    existing = next(
        (item for item in records.backups if item.backup_id == backup_id),
        None,
    )
    if existing is not None:
        if temporary.exists() or temporary.is_symlink():
            raise BackupOperationFailure(
                "backup",
                residue_paths=tuple(
                    path
                    for path in (pending, temporary)
                    if path.exists() or path.is_symlink()
                ),
            )
        if existing.dump_path != store.paths.backup_root / dump.name:
            raise LifecycleError("backup record dump path is noncanonical")
        expected = (
            existing.reason == reason
            and existing.current_release_id == current_release_id
            and existing.candidate_release_id == candidate_release_id
            and existing.database == database["name"]
            and existing.validated
        )
        if not expected:
            raise BackupOperationFailure("backup")
        dump = Path(existing.dump_path.as_posix())
        _safe_published_dump(dump, store.owner_uid)
        if (
            dump.stat().st_size != existing.size_bytes
            or existing.dump_sha256 is None
            or _dump_digest(dump) != existing.dump_sha256
        ):
            raise LifecycleError("finalized backup dump identity changed")
        _validate_dump(credentials, dump)
        if pending.exists() or pending.is_symlink():
            origin, expected_digest = _read_pending_backup(
                pending,
                store.owner_uid,
                operation_id=operation_id,
                backup_id=backup_id,
                database=database,
                current_release_id=current_release_id,
                candidate_release_id=candidate_release_id,
                reason=reason,
                dump=dump,
            )
            if (
                existing != origin
                or existing.dump_sha256 != expected_digest
                or _dump_digest(dump) != expected_digest
            ):
                raise LifecycleError("pending backup dump identity changed")
            _remove_pending_backup(pending)
            return existing, True
        return existing, False

    if dump.exists() or dump.is_symlink():
        if temporary.exists() or temporary.is_symlink():
            raise BackupOperationFailure(
                "backup",
                changed=dump.exists() and not dump.is_symlink(),
                residue_paths=tuple(
                    path
                    for path in (dump, pending, temporary)
                    if path.exists() or path.is_symlink()
                ),
            )
        record, expected_digest = _read_pending_backup(
            pending,
            store.owner_uid,
            operation_id=operation_id,
            backup_id=backup_id,
            database=database,
            current_release_id=current_release_id,
            candidate_release_id=candidate_release_id,
            reason=reason,
            dump=dump,
        )
        _safe_published_dump(dump, store.owner_uid)
        if (
            dump.stat().st_size != record.size_bytes
            or record.dump_sha256 != expected_digest
            or _dump_digest(dump) != expected_digest
        ):
            raise LifecycleError("pending backup dump identity changed")
        _validate_dump(credentials, dump)
        try:
            store.write_backup(record)
        except (LifecycleError, OSError) as error:
            raise BackupOperationFailure(
                "records",
                changed=True,
                residue_paths=(dump, pending),
            ) from error
        _remove_pending_backup(pending)
        return record, True
    if pending.exists() or pending.is_symlink():
        if not (temporary.exists() or temporary.is_symlink()):
            raise BackupOperationFailure("backup", residue_paths=(pending,))
        try:
            record, expected_digest = _read_pending_backup(
                pending,
                store.owner_uid,
                operation_id=operation_id,
                backup_id=backup_id,
                database=database,
                current_release_id=current_release_id,
                candidate_release_id=candidate_release_id,
                reason=reason,
                dump=dump,
            )
            _safe_published_dump(temporary, store.owner_uid)
            if (
                temporary.stat().st_size != record.size_bytes
                or record.dump_sha256 != expected_digest
                or _dump_digest(temporary) != expected_digest
            ):
                raise LifecycleError("pending backup dump identity changed")
            _validate_dump(credentials, temporary)
            _fsync_file(temporary)
            os.replace(temporary, dump)
            _fsync_directory(store.backup_root)
        except Exception as error:
            raise BackupOperationFailure(
                "backup",
                changed=dump.exists() and not dump.is_symlink(),
                residue_paths=tuple(
                    path
                    for path in (dump, pending, temporary)
                    if path.exists() or path.is_symlink()
                ),
            ) from error
        try:
            store.write_backup(record)
        except (LifecycleError, OSError) as error:
            raise BackupOperationFailure(
                "records",
                changed=True,
                residue_paths=(dump, pending),
            ) from error
        _remove_pending_backup(pending)
        return record, True
    if temporary.exists() or temporary.is_symlink():
        raise BackupOperationFailure(
            "backup",
            residue_paths=(temporary,),
        )
    try:
        source_size = _database_size(database, credentials)
        _dump(database, credentials, temporary)
        _safe_dump(temporary, store.owner_uid)
        _validate_dump(credentials, temporary)
        _fsync_file(temporary)
        dump_digest = _dump_digest(temporary)
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
            dump_digest,
        )
        _write_pending_backup(
            pending,
            store.owner_uid,
            operation_id=operation_id,
            record=record,
            dump_digest=dump_digest,
        )
        os.replace(temporary, dump)
        _fsync_directory(store.backup_root)
    except Exception as error:
        if not dump.exists() and not dump.is_symlink():
            try:
                removed = False
                for path in (temporary, pending):
                    if path.exists() or path.is_symlink():
                        path.unlink()
                        removed = True
                if removed:
                    _fsync_directory(store.backup_root)
            except OSError:
                pass
        residue = tuple(
            path
            for path in (dump, pending, temporary)
            if path.exists() or path.is_symlink()
        )
        raise BackupOperationFailure(
            "backup",
            changed=dump.exists(),
            residue_paths=residue,
        ) from error
    try:
        store.write_backup(record)
    except (LifecycleError, OSError) as error:
        raise BackupOperationFailure(
            "records",
            changed=True,
            residue_paths=(dump, pending),
        ) from error
    _remove_pending_backup(pending)
    return record, True


def _prune_scheduled_backups(
    store: LifecycleStore,
    records: object,
    *,
    credentials: Path,
    keep: int,
) -> tuple[str, ...]:
    """Prune only old scheduled dumps with exact lifecycle authority."""

    scheduled = sorted(
        (item for item in records.backups if item.reason == "scheduled"),
        key=lambda item: (item.created_at, item.backup_id),
        reverse=True,
    )
    references = _backup_dump_references(store, records.backups)
    candidates = scheduled[keep:]
    if any(references.get(record.dump_path, 0) != 1 for record in candidates):
        raise BackupOperationFailure("cleanup")
    validated: list[tuple[BackupRecord, Path, Path]] = []
    for record in candidates:
        dump = _validated_scheduled_dump(store, record, credentials)
        record_path = (
            store.deployment_root
            / "backups"
            / f"{record.backup_id}.json"
        )
        details = record_path.lstat()
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
            or details.st_uid != store.owner_uid
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise BackupOperationFailure("cleanup")
        validated.append((record, dump, record_path))

    removed: list[str] = []
    for record, dump, record_path in validated:
        dump_changed = False
        try:
            dump.unlink()
            dump_changed = True
            _fsync_directory(dump.parent)
        except OSError as error:
            dump_changed = not dump.exists() and not dump.is_symlink()
            changed = bool(removed) or dump_changed
            raise BackupOperationFailure(
                "cleanup",
                changed=changed,
                changed_stages=("cleanup",) if changed else (),
                residue_paths=_remaining_prune_residue(validated),
                pruned_backup_ids=tuple(removed),
            ) from error
        try:
            record_path.unlink()
            _fsync_directory(record_path.parent)
        except OSError as error:
            record_changed = (
                not record_path.exists() and not record_path.is_symlink()
            )
            changed = bool(removed) or dump_changed or record_changed
            raise BackupOperationFailure(
                "cleanup",
                changed=changed,
                changed_stages=("cleanup",) if changed else (),
                residue_paths=_remaining_prune_residue(validated),
                pruned_backup_ids=tuple(removed),
            ) from error
        removed.append(record.backup_id)
    return tuple(removed)


def _remaining_prune_residue(
    validated: list[tuple[BackupRecord, Path, Path]],
) -> tuple[Path, ...]:
    return tuple(
        path
        for _record, dump, record_path in validated
        for path in (dump, record_path)
        if path.exists() or path.is_symlink()
    )


def _backup_dump_references(
    store: LifecycleStore,
    backups: tuple[BackupRecord, ...],
) -> dict[PurePosixPath, int]:
    """Count every safe canonical backup reference before scheduled deletion."""

    references: dict[PurePosixPath, int] = {}
    for record in backups:
        dump = record.dump_path
        if (
            dump.parent == store.paths.backup_root
            and re.fullmatch(r"backup-[0-9a-f]{32}\.dump", dump.name) is not None
        ):
            references[dump] = references.get(dump, 0) + 1
    return references


def _validated_scheduled_dump(
    store: LifecycleStore,
    record: BackupRecord,
    credentials: Path,
) -> Path:
    """Return a fully proved scheduled dump that may authorize pruning.

    Scheduled pruning is destructive and therefore cannot recover a partial
    prior deletion by merely removing its lifecycle record.  A current record
    must prove the exact canonical dump, including content and format, before
    either member of the pair is unlinked.
    """

    expected = store.paths.backup_root / f"{record.backup_id}.dump"
    if record.dump_path != expected or record.dump_sha256 is None:
        raise LifecycleError("scheduled backup authority is incomplete")
    dump = Path(expected.as_posix())
    _safe_published_dump(dump, store.owner_uid)
    if dump.stat().st_size != record.size_bytes:
        raise LifecycleError("scheduled backup dump size changed")
    if _dump_digest(dump) != record.dump_sha256:
        raise LifecycleError("scheduled backup dump checksum changed")
    _validate_dump(credentials, dump)
    return dump


class _Inputs:
    def __init__(self, credentials_path: Path, database: dict[str, object], reason: str, retention: int) -> None:
        self.credentials_path = credentials_path
        self.database = database
        self.reason = reason
        self.retention = retention


def _inputs(request: HostRequest) -> _Inputs:
    if set(request.expected_state) != {"backup_ids"} or set(request.parameters) != _PARAMETER_KEYS:
        raise ValueError("backup request is incomplete")
    database_value = request.parameters["database"]
    if not isinstance(database_value, Mapping) or set(database_value) != _DATABASE_KEYS:
        raise ValueError("backup database settings are invalid")
    database = dict(database_value)
    if (
        not all(type(database[key]) is str and database[key] for key in ("host", "name", "role"))
        or type(database["port"]) is not int or not 0 < database["port"] < 65536
        or type(request.parameters["credentials_path"]) is not str
        or type(request.parameters["reason"]) is not str
        or request.parameters["reason"] not in {"scheduled", "pre-deploy", "pre-rollback", "pre-restore"}
        or type(request.parameters["retention"]) is not int or not 1 <= request.parameters["retention"] <= 64
        or not isinstance(request.expected_state["backup_ids"], tuple)
    ):
        raise ValueError("backup request is invalid")
    return _Inputs(Path(request.parameters["credentials_path"]), database, request.parameters["reason"], request.parameters["retention"])


def _safe_secret(path: Path, owner_uid: int) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_uid != owner_uid or stat.S_IMODE(details.st_mode) != 0o600:
        raise LifecycleError("database credential input is unsafe")


def _prepare_backup_root(store: LifecycleStore) -> None:
    root = store.backup_root
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    details = root.lstat()
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode) or details.st_uid != store.owner_uid or details.st_mode & 0o7022:
        raise LifecycleError("backup root is unsafe")


def _database_size(database: Mapping[str, object], credentials: Path) -> int:
    completed = _command(
        ("psql", "--no-psqlrc", "--tuples-only", "--no-align", "--host", database["host"], "--port", str(database["port"]), "--username", database["role"], "--dbname", database["name"], "--command", "SELECT pg_database_size(current_database())"), credentials, capture=True,
    )
    try:
        value = int(completed.stdout.strip())
    except ValueError as error:
        raise LifecycleError("database size evidence is invalid") from error
    if value <= 0:
        raise LifecycleError("database size evidence is invalid")
    return value


def _dump(database: Mapping[str, object], credentials: Path, destination: Path) -> None:
    _command(("pg_dump", "--format=custom", f"--file={destination}", "--host", database["host"], "--port", str(database["port"]), "--username", database["role"], "--dbname", database["name"]), credentials)


def _validate_dump(credentials: Path, destination: Path) -> None:
    _command(("pg_restore", "--list", destination.as_posix()), credentials)


def _command(argv: tuple[object, ...], credentials: Path, *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    values = tuple(str(value) for value in argv)
    if capture:
        output = _bounded_capture(values, credentials, timeout=60)
        return subprocess.CompletedProcess(
            values,
            0,
            stdout=output.decode("utf-8", "replace"),
            stderr=None,
        )
    return subprocess.run(
        values, check=True, text=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env={**os.environ, "PGPASSFILE": credentials.as_posix()}, timeout=60,
    )


def _bounded_capture(
    argv: tuple[str, ...],
    credentials: Path,
    *,
    timeout: float,
) -> bytes:
    """Read a child incrementally and terminate it at the protocol output bound."""

    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PGPASSFILE": credentials.as_posix()},
    )
    assert process.stdout is not None
    deadline = time.monotonic() + timeout
    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            events = selector.select(remaining)
            if not events:
                if process.poll() is not None:
                    break
                raise subprocess.TimeoutExpired(argv, timeout)
            chunk = os.read(
                process.stdout.fileno(),
                min(4097 - len(output), 4096),
            )
            if chunk:
                output.extend(chunk)
                if len(output) > 4096:
                    raise LifecycleError("database command output is oversized")
                continue
            if process.poll() is not None:
                break
        returncode = process.wait(
            timeout=max(0.001, deadline - time.monotonic())
        )
        if returncode:
            raise subprocess.CalledProcessError(returncode, argv)
        return bytes(output)
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()


def _safe_dump(path: Path, owner_uid: int) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode) or details.st_uid != owner_uid or details.st_size <= 0:
        raise LifecycleError("backup dump is unsafe")
    os.chmod(path, 0o600)


def _safe_published_dump(path: Path, owner_uid: int) -> None:
    """Validate an op-correlated orphan without normalizing its authority."""

    details = path.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != owner_uid
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_size <= 0
    ):
        raise LifecycleError("pending backup dump is unsafe")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_pending_backup(
    path: Path,
    owner_uid: int,
    *,
    operation_id: str,
    record: BackupRecord,
    dump_digest: str,
) -> None:
    payload = {
        "schema_version": 1,
        "operation_id": operation_id,
        "record": record.to_mapping(),
        "dump_sha256": dump_digest,
    }
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > 64 * 1024:
        raise LifecycleError("pending backup authority is oversized")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, owner_uid, -1)
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short pending backup write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _validated_foreign_publication(
    store: LifecycleStore,
    paths: tuple[Path, ...],
) -> ForeignBackupPublication:
    """Return only a complete, self-authenticating foreign recovery pair.

    Names from an incomplete directory scan are untrusted.  Do not return
    them to a caller until one pending authority and its exact deterministic
    temporary file prove each other's operation-bound identity.
    """

    candidates = tuple(sorted(paths, key=lambda path: path.as_posix()))
    pending_paths = tuple(
        path for path in candidates if path.name.endswith(".pending.json")
    )
    if len(pending_paths) != 1:
        raise LifecycleError("another backup publication is incomplete")
    pending = pending_paths[0]
    prefix = ".backup-"
    suffix = ".pending.json"
    if not pending.name.startswith(prefix):
        raise LifecycleError("another backup publication is incomplete")
    token = pending.name[len(prefix) : -len(suffix)]
    operation_id = f"op-{token}"
    try:
        validate_private_operation_id(operation_id)
    except ValueError as error:
        raise LifecycleError("another backup publication is incomplete") from error
    backup_id = f"backup-{token}"
    temporary = store.backup_root / f".{backup_id}.tmp"
    if candidates != tuple(sorted((pending, temporary), key=lambda path: path.as_posix())):
        raise LifecycleError("another backup publication is incomplete")
    dump = store.backup_root / f"{backup_id}.dump"
    record, digest = _read_pending_authority(
        pending,
        store.owner_uid,
        operation_id=operation_id,
    )
    if (
        record.backup_id != backup_id
        or record.validated is not True
        or record.dump_path.as_posix() != dump.as_posix()
    ):
        raise LifecycleError("another backup publication is incomplete")
    _safe_published_dump(temporary, store.owner_uid)
    if (
        temporary.stat().st_size != record.size_bytes
        or record.dump_sha256 != digest
        or _dump_digest(temporary) != digest
    ):
        raise LifecycleError("another backup publication is incomplete")
    return ForeignBackupPublication(operation_id, candidates)


def _read_pending_authority(
    path: Path,
    owner_uid: int,
    *,
    operation_id: str,
) -> tuple[BackupRecord, str]:
    """Read one bounded pending authority before request-specific comparison."""

    try:
        details = path.lstat()
    except OSError as error:
        raise LifecycleError("pending backup authority is invalid") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != owner_uid
        or stat.S_IMODE(details.st_mode) != 0o600
        or not 0 < details.st_size <= 64 * 1024
    ):
        raise LifecycleError("pending backup authority is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LifecycleError("pending backup authority is invalid") from error
    if (
        not isinstance(value, Mapping)
        or set(value) != {
            "schema_version",
            "operation_id",
            "record",
            "dump_sha256",
        }
        or value["schema_version"] != 1
        or value["operation_id"] != operation_id
    ):
        raise LifecycleError("pending backup authority is invalid")
    record = BackupRecord.from_mapping(value["record"])
    digest = value["dump_sha256"]
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or record.dump_sha256 != digest
    ):
        raise LifecycleError("pending backup digest is invalid")
    return record, digest


def _read_pending_backup(
    path: Path,
    owner_uid: int,
    *,
    operation_id: str,
    backup_id: str,
    database: Mapping[str, object],
    current_release_id: str | None,
    candidate_release_id: str | None,
    reason: str,
    dump: Path,
) -> tuple[BackupRecord, str]:
    record, digest = _read_pending_authority(
        path,
        owner_uid,
        operation_id=operation_id,
    )
    expected_path = dump.as_posix()
    if (
        record.backup_id != backup_id
        or record.database != database["name"]
        or record.current_release_id != current_release_id
        or record.candidate_release_id != candidate_release_id
        or record.reason != reason
        or record.validated is not True
        or record.dump_path.as_posix() != expected_path
    ):
        raise LifecycleError("pending backup authority is contradictory")
    return record, digest


def _dump_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_pending_backup(path: Path) -> None:
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        raise BackupOperationFailure(
            "records",
            changed=True,
            residue_paths=(path,)
            if path.exists() or path.is_symlink()
            else (),
        ) from error


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _refused(request: HostRequest) -> HostResult:
    return HostResult(PROTOCOL_VERSION, request.operation, request.operation_id, "refused", "backup-preflight", (), {}, {}, {}, (), ("supply validated backup authority before retrying",), ())


def _foreign_incomplete(
    request: HostRequest,
    error: ForeignBackupPublication,
) -> HostResult:
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "refused",
        "backup-preflight",
        (),
        {},
        {},
        {},
        tuple(path.as_posix() for path in error.residue_paths),
        (
            f"retry backup operation {error.operation_id} before starting another backup",
        ),
        (),
    )


def _locked(request: HostRequest, error: LifecycleLockContention) -> HostResult:
    runtime_state = {} if error.holder is None else {"lock_holder": error.holder.to_mapping()}
    return HostResult(PROTOCOL_VERSION, request.operation, request.operation_id, "refused", "lifecycle-lock", (), {}, runtime_state, {}, (), ("wait for the recorded lifecycle operation to finish and retry",), ())


def _publication_failed(request: HostRequest, dump: Path) -> HostResult:
    """Retain the exact validated dump when record publication cannot finish."""

    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="failed",
        stage="records",
        changed_stages=("backup",),
        lifecycle={},
        runtime_state={},
        verification={"format": "custom", "validated": True},
        residue_paths=(dump.as_posix(),),
        recovery_actions=("inspect the validated backup dump before retrying",),
        warnings=("unable to publish validated backup",),
    )


def _failed(request: HostRequest, error: BackupOperationFailure) -> HostResult:
    warning = (
        "unable to publish validated backup"
        if error.stage == "records"
        else "backup operation did not complete"
    )
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="failed",
        stage=error.stage,
        changed_stages=error.changed_stages,
        lifecycle=(
            {"pruned_backup_ids": error.pruned_backup_ids}
            if error.pruned_backup_ids
            else {}
        ),
        runtime_state={},
        verification={},
        residue_paths=tuple(path.as_posix() for path in error.residue_paths),
        recovery_actions=("inspect exact backup residue before retrying",),
        warnings=(warning,),
    )


__all__ = ["BackupOperationFailure", "backup", "create_validated_backup"]
