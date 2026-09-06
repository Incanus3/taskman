"""Host-local guarded PostgreSQL restore and safe database swap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION

from ..lifecycle import (
    ActivationRecord,
    BackupRecord,
    LifecycleError,
    LifecycleLockContention,
    LifecycleStore,
    LifecycleWriteFailure,
)
from ..paths import ManagedPaths, PathAuthorityError
from ..verification import verify
from .backup import (
    BackupOperationFailure,
    _bounded_capture,
    _dump_digest,
    _prepare_backup_root,
    _safe_secret,
    _validate_dump,
    create_validated_backup,
)


_DATABASE_KEYS = frozenset({"host", "name", "port", "role"})
_PARAMETER_KEYS = frozenset(
    {
        "action",
        "backup_id",
        "credentials_path",
        "database",
        "expected_migration_versions",
        "verification",
    }
)
_DATABASE_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_MIGRATION_RE = re.compile(r"[0-9]+\Z")


class _Inputs:
    def __init__(
        self,
        action: str,
        backup_id: str,
        credentials: Path,
        database: dict[str, object],
        migrations: tuple[str, ...],
        verification: dict[str, object],
    ) -> None:
        self.action = action
        self.backup_id = backup_id
        self.credentials = credentials
        self.database = database
        self.migrations = migrations
        self.verification = verification


class _Failure(Exception):
    def __init__(
        self,
        stage: str,
        *,
        selected: str | None,
        service: str,
        database_state: str,
        source_backup_id: str,
        pre_restore_backup_id: str | None,
        recovery_id: str,
        intended_release_id: str,
        changed: tuple[str, ...],
        residue: tuple[str, ...] = (),
        verification: Mapping[str, object] | None = None,
        restore_recorded: bool = False,
    ) -> None:
        self.stage = stage
        self.selected = selected
        self.service = service
        self.database_state = database_state
        self.source_backup_id = source_backup_id
        self.pre_restore_backup_id = pre_restore_backup_id
        self.recovery_id = recovery_id
        self.intended_release_id = intended_release_id
        self.changed = changed
        self.residue = residue
        self.verification = {} if verification is None else verification
        self.restore_recorded = restore_recorded


class _SelectionFailure(LifecycleError):
    def __init__(self, *, published: bool) -> None:
        super().__init__("restore release selection failed")
        self.published = published


@dataclass(frozen=True)
class _RestoreRecordEffect:
    published: bool
    durable: bool


class _RestoreRecordFailure(LifecycleError):
    def __init__(
        self,
        effect: _RestoreRecordEffect,
        residue: tuple[Path, ...],
    ) -> None:
        super().__init__("restore record publication failed")
        self.effect = effect
        self.residue = residue


class _VerificationFailure(LifecycleError):
    def __init__(self, verification: Mapping[str, object]) -> None:
        super().__init__("restored host verification failed")
        self.verification = verification


def restore(request: HostRequest) -> HostResult:
    """Validate the selected dump and execute one recoverable database swap."""

    backup_id = request.parameters.get("backup_id")
    if type(backup_id) is not str:
        return _refused(request)
    try:
        inputs = _inputs(request)
        paths = ManagedPaths.from_mapping(request.paths)
        store = LifecycleStore(paths)
        paths.validate_existing(owner_uid=store.owner_uid)
        with store.shared_snapshot_lock(operation="restore-preflight"):
            preflight_records = store.read()
            preflight_source = _selected_backup(
                preflight_records,
                store,
                inputs,
            )
            preflight_intended = preflight_source.current_release_id
            if preflight_intended is None or not any(
                release.release_id == preflight_intended
                for release in preflight_records.releases
            ):
                return _refused(request, backup_id=backup_id)
            store.require_release_directory(
                store.release_path(preflight_intended)
            )
        with store.exclusive_lifecycle_lock(operation="restore"):
            records = store.read()
            source = _selected_backup(records, store, inputs)
            current = records.current_release_id
            intended = source.current_release_id
            if intended is None or not any(
                release.release_id == intended for release in records.releases
            ):
                return _refused(request, backup_id=backup_id)
            store.require_release_directory(store.release_path(intended))
            _safe_secret(inputs.credentials, store.owner_uid)
            if inputs.action == "execute":
                rerun = _completed_rerun(
                    request,
                    store,
                    records,
                    source,
                    current,
                    intended,
                    inputs,
                )
                if rerun is not None:
                    return rerun
            if request.expected_state["current_release_id"] != current or current is None:
                return _refused(request, backup_id=backup_id)
            _validate_dump(inputs.credentials, Path(source.dump_path.as_posix()))
            _validate_capacity(source.source_database_size_bytes)
            if inputs.action == "inspect":
                return _inspection(request, source, current, intended)
            return _execute(
                request,
                store,
                records,
                source,
                current,
                intended,
                inputs,
            )
    except LifecycleLockContention as error:
        return _locked(request, error)
    except _Failure as error:
        return _failed(request, error)
    except (LifecycleError, PathAuthorityError, OSError, ValueError, subprocess.SubprocessError):
        return _refused(request, backup_id=backup_id)


def _inputs(request: HostRequest) -> _Inputs:
    if (
        set(request.expected_state) != {"backup_id", "current_release_id"}
        or set(request.parameters) != _PARAMETER_KEYS
    ):
        raise ValueError("restore request is incomplete")
    action = request.parameters["action"]
    backup_id = request.parameters["backup_id"]
    credentials = request.parameters["credentials_path"]
    database = request.parameters["database"]
    migrations = request.parameters["expected_migration_versions"]
    verification = request.parameters["verification"]
    if (
        action not in {"inspect", "execute"}
        or type(backup_id) is not str
        or request.expected_state["backup_id"] != backup_id
        or type(credentials) is not str
        or not isinstance(database, Mapping)
        or set(database) != _DATABASE_KEYS
        or not isinstance(migrations, tuple)
        or not isinstance(verification, Mapping)
    ):
        raise ValueError("restore request is invalid")
    values = dict(database)
    if (
        not all(
            type(values[key]) is str and values[key]
            for key in ("host", "name", "role")
        )
        or _DATABASE_RE.fullmatch(str(values["name"])) is None
        or _DATABASE_RE.fullmatch(str(values["role"])) is None
        or type(values["port"]) is not int
        or not 0 < values["port"] < 65536
        or any(type(item) is not str or _MIGRATION_RE.fullmatch(item) is None for item in migrations)
        or tuple(sorted(set(migrations), key=int)) != migrations
    ):
        raise ValueError("restore database inputs are invalid")
    return _Inputs(
        action,
        backup_id,
        Path(credentials),
        values,
        migrations,
        dict(verification),
    )


def _selected_backup(
    records: object,
    store: LifecycleStore,
    inputs: _Inputs,
) -> BackupRecord:
    selected = next(
        (record for record in records.backups if record.backup_id == inputs.backup_id),
        None,
    )
    if selected is None or not selected.validated:
        raise LifecycleError("selected backup is unavailable")
    dump = Path(selected.dump_path.as_posix())
    details = dump.lstat()
    if (
        dump.parent != store.backup_root
        or not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != store.owner_uid
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_size <= 0
        or details.st_size != selected.size_bytes
        or selected.database != inputs.database["name"]
        or (
            selected.dump_sha256 is not None
            and _dump_digest(dump) != selected.dump_sha256
        )
    ):
        raise LifecycleError("selected backup authority is unsafe")
    return selected


def _inspection(
    request: HostRequest,
    source: BackupRecord,
    current: str,
    intended: str,
) -> HostResult:
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "succeeded",
        "restore-inspected",
        (),
        {
            "backup_id": source.backup_id,
            "dump_path": source.dump_path.as_posix(),
            "dump_size_bytes": source.size_bytes,
            "source_database_size_bytes": source.source_database_size_bytes,
            "current_release_id": current,
            "intended_release_id": intended,
            "dump_validated": True,
        },
        {},
        {"format": "custom", "validated": True},
        (),
        (),
        (),
    )


def _execute(
    request: HostRequest,
    store: LifecycleStore,
    records: object,
    source: BackupRecord,
    current: str,
    intended: str,
    inputs: _Inputs,
) -> HostResult:
    token = request.operation_id.removeprefix("op-")
    recovery_id = f"recovery-{token}"
    temporary_database = f"taskman_restore_{token}"
    recovery_database = f"taskman_recovery_{token}"
    pre_restore_backup_id: str | None = None
    selected: str | None = current
    service = "active"
    database_state = "unchanged"
    changed: list[str] = []
    verification: Mapping[str, object] = {}
    temp_exists = False
    recovery_exists = False
    restore_recorded = False
    stage = "backup"

    try:
        _prepare_backup_root(store)
        backup, backup_changed = create_validated_backup(
            store,
            records,
            operation_id=request.operation_id,
            database=inputs.database,
            credentials=inputs.credentials,
            reason="pre-restore",
            current_release_id=current,
            candidate_release_id=intended,
        )
        pre_restore_backup_id = backup.backup_id
        if backup_changed:
            changed.append("backup")

        stage = "stop"
        _service("stop")
        service = "stopped"
        changed.append("stop")

        stage = "restore"
        _admin(
            inputs,
            f'CREATE DATABASE "{temporary_database}" OWNER "{inputs.database["role"]}"',
        )
        temp_exists = True
        changed.append("restore")
        _restore_dump(inputs, source, temporary_database)
        stage = "validation"
        _validate_restored_database(inputs, temporary_database)
        changed.append("validation")

        stage = "swap"
        _admin(
            inputs,
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname IN ('{inputs.database['name']}', '{temporary_database}') "
            "AND pid <> pg_backend_pid()",
        )
        _admin(
            inputs,
            f'ALTER DATABASE "{inputs.database["name"]}" RENAME TO "{recovery_database}"',
        )
        recovery_exists = True
        database_state = "canonical-moved"
        changed.append("swap")
        try:
            _admin(
                inputs,
                f'ALTER DATABASE "{temporary_database}" RENAME TO "{inputs.database["name"]}"',
            )
        except (OSError, subprocess.SubprocessError) as error:
            try:
                _admin(
                    inputs,
                    f'ALTER DATABASE "{recovery_database}" RENAME TO "{inputs.database["name"]}"',
                )
                recovery_exists = False
                database_state = "canonical-restored"
            except (OSError, subprocess.SubprocessError):
                database_state = "unknown"
            raise error
        temp_exists = False
        database_state = "restored-promoted"

        stage = "selection"
        try:
            _select_release(store, request.operation_id, intended)
        except _SelectionFailure as error:
            if error.published:
                selected = intended
                changed.append("selection")
            raise
        selected = intended
        changed.append("selection")
        stage = "start"
        _service("start")
        service = "active"
        changed.append("start")
        stage = "verification"
        verification = _verify_locked(request, intended, inputs.verification)
        changed.append("verification")
        stage = "records"
        activation_id = f"activation-{token}"
        try:
            store.write_activation(
                ActivationRecord(
                    1,
                    activation_id,
                    current,
                    intended,
                    datetime.now(UTC).replace(microsecond=0),
                    pre_restore_backup_id,
                    "no-change",
                )
            )
        except LifecycleWriteFailure as error:
            if error.effect.published:
                changed.append("records")
            raise
        changed.append("records")
        record_effect = _write_restore_record(
            store,
            recovery_id=recovery_id,
            database=str(inputs.database["name"]),
            recovery_database=recovery_database,
            source_backup_id=source.backup_id,
            pre_restore_backup_id=pre_restore_backup_id,
            intended_release_id=intended,
        )
        restore_recorded = record_effect.published
    except Exception as error:
        if isinstance(error, _Failure):
            raise
        if isinstance(error, BackupOperationFailure):
            changed = list(
                dict.fromkeys((*changed, *error.changed_stages))
            )
            if error.changed_stages and pre_restore_backup_id is None:
                pre_restore_backup_id = (
                    f"backup-{request.operation_id.removeprefix('op-')}"
                )
        if isinstance(error, _VerificationFailure):
            verification = error.verification
        if isinstance(error, _RestoreRecordFailure):
            restore_recorded = error.effect.published
        if temp_exists and database_state == "unchanged":
            try:
                _admin(
                    inputs,
                    f'DROP DATABASE "{temporary_database}" WITH (FORCE)',
                )
                temp_exists = False
            except (OSError, subprocess.SubprocessError):
                pass
        if service == "stopped" and database_state in {"unchanged", "canonical-restored"}:
            try:
                _service("start")
                service = "active"
            except (OSError, subprocess.SubprocessError):
                service = "unknown"
        residue: list[str] = (
            [
                path.as_posix()
                for path in error.residue_paths
            ]
            if isinstance(error, BackupOperationFailure)
            else []
        )
        if temp_exists:
            residue.append(f"/database/{temporary_database}")
        if recovery_exists:
            residue.append(f"/database/{recovery_database}")
        if isinstance(error, _RestoreRecordFailure):
            residue.extend(path.as_posix() for path in error.residue)
        raise _Failure(
            error.stage if isinstance(error, BackupOperationFailure) else stage,
            selected=selected,
            service=service,
            database_state=database_state,
            source_backup_id=source.backup_id,
            pre_restore_backup_id=pre_restore_backup_id,
            recovery_id=recovery_id,
            intended_release_id=intended,
            changed=tuple(changed),
            residue=tuple(residue),
            verification=verification,
            restore_recorded=restore_recorded,
        ) from error

    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "succeeded",
        "records",
        tuple(changed),
        {
            "backup_id": source.backup_id,
            "pre_restore_backup_id": pre_restore_backup_id,
            "current_release_id": current,
            "intended_release_id": intended,
            "selected_release_id": intended,
            "recovery_id": recovery_id,
            "recovery_database": recovery_database,
            "service_state": "active",
            "database_state": "restored-promoted",
            "restore_recorded": True,
        },
        {},
        verification,
        (),
        (
            f"retain {recovery_database} until the restored database is accepted",
        ),
        (),
    )


def _validate_capacity(source_database_size_bytes: int) -> None:
    """Reserve room for both the temporary restore and retained database."""

    if (
        type(source_database_size_bytes) is not int
        or source_database_size_bytes <= 0
        or shutil.disk_usage("/var/lib/postgresql").free
        < source_database_size_bytes * 2
    ):
        raise LifecycleError("restore capacity is insufficient")


def _admin(inputs: _Inputs, sql: str) -> str:
    return _run(
        (
            "sudo",
            "-u",
            "postgres",
            "--",
            "psql",
            "--no-psqlrc",
            "--host",
            "/var/run/postgresql",
            "--port",
            str(inputs.database["port"]),
            "--username",
            "postgres",
            "--dbname=postgres",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            sql,
        ),
        inputs.credentials,
        capture=True,
    )


def _restore_dump(
    inputs: _Inputs,
    source: BackupRecord,
    database: str,
) -> None:
    _run(
        (
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--host",
            str(inputs.database["host"]),
            "--port",
            str(inputs.database["port"]),
            "--username",
            str(inputs.database["role"]),
            "--dbname",
            database,
            "--",
            source.dump_path.as_posix(),
        ),
        inputs.credentials,
    )


def _validate_restored_database(inputs: _Inputs, database: str) -> None:
    table = _run(
        (
            "psql",
            "--no-psqlrc",
            "--host",
            str(inputs.database["host"]),
            "--port",
            str(inputs.database["port"]),
            "--username",
            str(inputs.database["role"]),
            "--dbname",
            database,
            "--tuples-only",
            "--no-align",
            "--command",
            "SELECT 1 FROM information_schema.tables WHERE "
            "table_schema = 'public' AND table_name = 'schema_migrations'",
        ),
        inputs.credentials,
        capture=True,
    ).strip()
    versions = _run(
        (
            "psql",
            "--no-psqlrc",
            "--host",
            str(inputs.database["host"]),
            "--port",
            str(inputs.database["port"]),
            "--username",
            str(inputs.database["role"]),
            "--dbname",
            database,
            "--tuples-only",
            "--no-align",
            "--command",
            "SELECT version FROM schema_migrations ORDER BY version",
        ),
        inputs.credentials,
        capture=True,
    )
    observed = tuple(line for line in versions.splitlines() if line)
    if table != "1" or observed != inputs.migrations:
        raise LifecycleError("restored database validation failed")


def _run(
    argv: tuple[str, ...],
    credentials: Path,
    *,
    capture: bool = False,
) -> str:
    if capture:
        return _bounded_capture(
            argv,
            credentials,
            timeout=60,
        ).decode("utf-8", "replace")
    completed = subprocess.run(
        argv,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PGPASSFILE": credentials.as_posix()},
        timeout=60,
    )
    return ""


def _service(action: str) -> None:
    subprocess.run(
        ("systemctl", action, "taskman.service"),
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )


def _select_release(
    store: LifecycleStore,
    operation_id: str,
    intended: str,
) -> None:
    temporary = (
        store.paths.local(store.paths.install_root) / f".current-{operation_id}"
    )
    if temporary.exists() or temporary.is_symlink():
        raise LifecycleError("restore selection temporary exists")
    temporary.symlink_to(store.release_path(intended))
    published = False
    try:
        os.replace(temporary, store.current_link)
        published = True
        _fsync_directory(store.paths.local(store.paths.install_root))
    except OSError as error:
        raise _SelectionFailure(published=published) from error
    finally:
        if temporary.exists() or temporary.is_symlink():
            try:
                temporary.unlink()
            except OSError:
                pass


def _verify_locked(
    request: HostRequest,
    intended: str,
    settings: Mapping[str, object],
) -> Mapping[str, object]:
    result = verify(
        HostRequest(
            1,
            "verify",
            request.operation_id,
            {"expected_release_id": intended},
            request.paths,
            settings,
        ),
        lifecycle_locked=True,
    )
    if result.outcome != "succeeded" or result.stage != "verified":
        raise _VerificationFailure(result.verification)
    return result.verification


def _write_restore_record(
    store: LifecycleStore,
    *,
    recovery_id: str,
    database: str,
    recovery_database: str,
    source_backup_id: str,
    pre_restore_backup_id: str,
    intended_release_id: str,
) -> _RestoreRecordEffect:
    directory = store.deployment_root / "restores"
    path = directory / f"{recovery_id}.json"
    pending = directory / f".{recovery_id}.pending"
    authority = {
        "schema_version": 1,
        "recovery_id": recovery_id,
        "database": database,
        "recovery_database": recovery_database,
        "source_backup_id": source_backup_id,
        "pre_restore_backup_id": pre_restore_backup_id,
        "intended_release_id": intended_release_id,
        "state": "retained",
    }
    effect = _RestoreRecordEffect(published=False, durable=False)
    parent_durable = False
    try:
        if directory.exists() or directory.is_symlink():
            details = directory.lstat()
            if (
                not stat.S_ISDIR(details.st_mode)
                or stat.S_ISLNK(details.st_mode)
                or details.st_uid != store.owner_uid
                or stat.S_IMODE(details.st_mode) != 0o750
            ):
                raise LifecycleError("restore record directory is unsafe")
        else:
            directory.mkdir(mode=0o750, parents=True)
            os.chown(directory, store.owner_uid, -1)
            os.chmod(directory, 0o750)
        _fsync_directory(store.deployment_root)
        parent_durable = True
        if pending.exists() or pending.is_symlink():
            _read_restore_record(pending, store.owner_uid, authority)
        else:
            if path.exists() or path.is_symlink():
                raise LifecycleError("restore record already exists")
            payload = {
                **authority,
                "created_at": datetime.now(UTC)
                .replace(microsecond=0)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            encoded = (
                json.dumps(payload, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode()
            descriptor = os.open(
                pending,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                os.fchown(descriptor, store.owner_uid, -1)
                _write_all(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _fsync_directory(directory)
        if path.exists() or path.is_symlink():
            _read_restore_record(path, store.owner_uid, authority)
            if not os.path.samefile(pending, path):
                raise LifecycleError("restore record prefix is contradictory")
        else:
            os.link(pending, path, follow_symlinks=False)
        effect = _RestoreRecordEffect(published=True, durable=False)
        _fsync_directory(directory)
        effect = _RestoreRecordEffect(published=True, durable=True)
        pending.unlink()
        _fsync_directory(directory)
    except LifecycleError:
        raise
    except OSError as error:
        residue = (
            (pending,)
            if pending.exists() or pending.is_symlink()
            else (directory,)
            if not parent_durable
            else ()
        )
        raise _RestoreRecordFailure(
            effect,
            residue,
        ) from error
    return effect


def _read_restore_record(
    path: Path,
    owner_uid: int,
    authority: Mapping[str, object],
) -> Mapping[str, object]:
    try:
        details = path.lstat()
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LifecycleError("restore record prefix is invalid") from error
    created_at = value.get("created_at") if isinstance(value, Mapping) else None
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != owner_uid
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_size > 64 * 1024
        or not isinstance(value, Mapping)
        or set(value) != {*authority, "created_at"}
        or any(value.get(key) != expected for key, expected in authority.items())
        or type(created_at) is not str
    ):
        raise LifecycleError("restore record prefix is invalid")
    try:
        datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise LifecycleError("restore record prefix is invalid") from error
    return value


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short restore record write")
        offset += written


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _completed_rerun(
    request: HostRequest,
    store: LifecycleStore,
    records: object,
    source: BackupRecord,
    current: str | None,
    intended: str,
    inputs: _Inputs,
) -> HostResult | None:
    recovery_id = f"recovery-{request.operation_id.removeprefix('op-')}"
    record = store.deployment_root / "restores" / f"{recovery_id}.json"
    activation_id = f"activation-{request.operation_id.removeprefix('op-')}"
    activation = next(
        (
            item
            for item in records.activations
            if item.activation_id == activation_id
        ),
        None,
    )
    if activation is None:
        return None
    if (
        current != intended
        or activation.previous_release_id
        != request.expected_state.get("current_release_id")
        or activation.candidate_release_id != intended
        or activation.backup_id
        != f"backup-{request.operation_id.removeprefix('op-')}"
    ):
        return _refused(request, backup_id=source.backup_id)
    pre_restore_backup = next(
        (
            item
            for item in records.backups
            if item.backup_id == activation.backup_id
        ),
        None,
    )
    canonical = str(inputs.database["name"])
    recovery_database = (
        f"taskman_recovery_{request.operation_id.removeprefix('op-')}"
    )
    if (
        pre_restore_backup is None
        or pre_restore_backup.reason != "pre-restore"
        or pre_restore_backup.current_release_id
        != request.expected_state.get("current_release_id")
        or pre_restore_backup.candidate_release_id != intended
        or pre_restore_backup.database != canonical
        or not _restore_databases_present(
            inputs,
            canonical,
            recovery_database,
        )
    ):
        return _refused(request, backup_id=source.backup_id)
    pending = record.parent / f".{recovery_id}.pending"
    authority = {
        "schema_version": 1,
        "recovery_id": recovery_id,
        "database": canonical,
        "recovery_database": recovery_database,
        "source_backup_id": source.backup_id,
        "pre_restore_backup_id": pre_restore_backup.backup_id,
        "intended_release_id": intended,
        "state": "retained",
    }
    record_present = record.exists() or record.is_symlink()
    pending_present = pending.exists() or pending.is_symlink()
    if record_present:
        _read_restore_record(record, store.owner_uid, authority)
    if pending_present:
        _read_restore_record(pending, store.owner_uid, authority)
        if record_present and not os.path.samefile(pending, record):
            raise LifecycleError("restore record prefix is contradictory")
    try:
        verification = _verify_locked(request, intended, inputs.verification)
    except _VerificationFailure as error:
        raise _Failure(
            "verification",
            selected=intended,
            service=_service_state_from_verification(error.verification),
            database_state="restored-promoted",
            source_backup_id=source.backup_id,
            pre_restore_backup_id=pre_restore_backup.backup_id,
            recovery_id=recovery_id,
            intended_release_id=intended,
            changed=(),
            residue=(
                (f"/database/{recovery_database}", pending.as_posix())
                if pending_present
                else (f"/database/{recovery_database}",)
            ),
            verification=error.verification,
            restore_recorded=record_present,
        ) from error
    if pending_present or not record_present:
        try:
            effect = _write_restore_record(
                store,
                recovery_id=recovery_id,
                database=canonical,
                recovery_database=recovery_database,
                source_backup_id=source.backup_id,
                pre_restore_backup_id=pre_restore_backup.backup_id,
                intended_release_id=intended,
            )
        except _RestoreRecordFailure as error:
            raise _Failure(
                "records",
                selected=intended,
                service="active",
                database_state="restored-promoted",
                source_backup_id=source.backup_id,
                pre_restore_backup_id=pre_restore_backup.backup_id,
                recovery_id=recovery_id,
                intended_release_id=intended,
                changed=("records",) if error.effect.published else (),
                residue=(
                    f"/database/{recovery_database}",
                    *(path.as_posix() for path in error.residue),
                ),
                verification=verification,
                restore_recorded=error.effect.published,
            ) from error
        if not effect.published or not effect.durable:
            raise LifecycleError("restore record finalization is incomplete")
        return _rerun_result(
            request,
            source,
            intended,
            pre_restore_backup.backup_id,
            recovery_id,
            recovery_database,
            verification,
            outcome="succeeded",
            stage="records-finalized",
            changed=("records",),
        )
    return _rerun_result(
        request,
        source,
        intended,
        pre_restore_backup.backup_id,
        recovery_id,
        recovery_database,
        verification,
        outcome="no_change",
        stage="already-restored",
        changed=(),
    )


def _restore_databases_present(
    inputs: _Inputs,
    canonical: str,
    recovery: str,
) -> bool:
    observed = _admin(
        inputs,
        "SELECT datname FROM pg_database "
        f"WHERE datname IN ('{canonical}', '{recovery}') ORDER BY datname",
    )
    return tuple(line for line in observed.splitlines() if line) == tuple(
        sorted((canonical, recovery))
    )


def _service_state_from_verification(
    verification: Mapping[str, object],
) -> str:
    checks = verification.get("checks")
    if not isinstance(checks, tuple):
        return "unknown"
    service_checks = tuple(
        check
        for check in checks
        if isinstance(check, Mapping)
        and check.get("name") == "taskman-service"
    )
    if (
        len(service_checks) == 1
        and service_checks[0].get("schema_version") == 1
        and service_checks[0].get("status") == "passed"
    ):
        return "active"
    return "unknown"


def _rerun_result(
    request: HostRequest,
    source: BackupRecord,
    intended: str,
    pre_restore_backup_id: str,
    recovery_id: str,
    recovery_database: str,
    verification: Mapping[str, object],
    *,
    outcome: str,
    stage: str,
    changed: tuple[str, ...],
) -> HostResult:
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        outcome,
        stage,
        changed,
        {
            "backup_id": source.backup_id,
            "pre_restore_backup_id": pre_restore_backup_id,
            "current_release_id": request.expected_state["current_release_id"],
            "intended_release_id": intended,
            "selected_release_id": intended,
            "recovery_id": recovery_id,
            "recovery_database": recovery_database,
            "service_state": "active",
            "database_state": "restored-promoted",
            "restore_recorded": True,
        },
        {},
        verification,
        (),
        (f"retain {recovery_database} until the restored database is accepted",),
        (),
    )


def _refused(
    request: HostRequest,
    *,
    backup_id: str | None = None,
) -> HostResult:
    lifecycle = (
        {}
        if backup_id is None
        else {"backup_id": backup_id, "dump_validated": False}
    )
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "refused",
        "restore-preflight",
        (),
        lifecycle,
        {},
        {},
        (),
        ("inspect the selected backup before retrying",),
        (),
    )


def _failed(request: HostRequest, error: _Failure) -> HostResult:
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "failed",
        error.stage,
        error.changed,
        {
            "backup_id": error.source_backup_id,
            "pre_restore_backup_id": error.pre_restore_backup_id,
            "current_release_id": request.expected_state.get("current_release_id"),
            "intended_release_id": error.intended_release_id,
            "selected_release_id": error.selected,
            "recovery_id": error.recovery_id,
            "recovery_database":
                f"taskman_recovery_{request.operation_id.removeprefix('op-')}",
            "service_state": error.service,
            "database_state": error.database_state,
            "restore_recorded": error.restore_recorded,
        },
        {},
        error.verification,
        error.residue,
        _recovery_actions(request, error),
        ("restore operation did not complete",),
    )


def _recovery_actions(
    request: HostRequest,
    error: _Failure,
) -> tuple[str, ...]:
    database = request.parameters.get("database")
    canonical = database.get("name") if isinstance(database, Mapping) else "taskman_prod"
    token = request.operation_id.removeprefix("op-")
    recovery = f"taskman_recovery_{token}"
    temporary = f"taskman_restore_{token}"
    actions = [
        "systemctl status taskman.service",
        f"readlink -f {request.paths['install_root']}/current",
    ]
    if error.database_state == "unknown":
        actions.extend(
            (
                f"sudo -u postgres psql --dbname=postgres --command=\"SELECT datname FROM pg_database WHERE datname IN ('{canonical}','{recovery}','{temporary}') ORDER BY datname\"",
                "leave taskman.service stopped until the canonical database name is proven",
            )
        )
    elif error.database_state == "canonical-moved":
        actions.append(
            f"sudo -u postgres psql --dbname=postgres --command='ALTER DATABASE \"{recovery}\" RENAME TO \"{canonical}\"'"
        )
    elif error.database_state == "restored-promoted":
        actions.append(
            f"retain {recovery} and inspect the restored canonical database before retrying"
        )
    else:
        actions.append("inspect the unchanged canonical database before retrying")
    return tuple(actions)


def _locked(
    request: HostRequest,
    error: LifecycleLockContention,
) -> HostResult:
    runtime_state = (
        {} if error.holder is None else {"lock_holder": error.holder.to_mapping()}
    )
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "refused",
        "lifecycle-lock",
        (),
        {},
        runtime_state,
        {},
        (),
        ("wait for the recorded lifecycle operation to finish and retry",),
        (),
    )


__all__ = ["restore"]
