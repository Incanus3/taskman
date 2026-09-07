"""Replayable restore through a small set of observable database arrangements."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
import re
import stat

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION
from taskman_ops.releases.identifiers import validate_release_id

from ..commands import CommandError, run_command
from ..lock import LifecycleLockContention, lifecycle_lock
from ..paths import ManagedPaths, PathAuthorityError
from ..records import BackupRecord, RecordError, ReleaseRecord, SelectionRecord, append_selection
from ..state import HostState, StateAmbiguityError, observe_host_state
from ..verification import available_bytes, verify
from .backup import create_validated_backup


_EXPECTED_STATE_KEYS = frozenset({"selected_release_id", "backup_id"})
_PARAMETER_KEYS = frozenset({"backup_id", "credentials_path", "database", "verification"})
_DATABASE_KEYS = frozenset({"host", "port", "role", "name"})
_DATABASE_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,49}\Z")
_MIGRATION_RE = re.compile(r"([0-9]{14})_[a-z0-9_]+\.exs\Z")
_LOCK_TIMEOUT_SECONDS = 5.0
_COMMAND_TIMEOUT_SECONDS = 60.0
_POSTGRES_DATA = Path("/var/lib/postgresql")


class RestoreRefused(ValueError):
    """The selected source cannot safely be restored."""


class RestoreManual(RuntimeError):
    """Observed database or selection state does not identify a safe replay."""


class _Retryable(RuntimeError):
    def __init__(self, boundary: str) -> None:
        super().__init__(boundary)
        self.boundary = boundary


@dataclass(frozen=True)
class _Inputs:
    paths: ManagedPaths
    current_release_id: str
    backup_id: str
    credentials: Path
    database: Mapping[str, object]
    verification: Mapping[str, object]

    @property
    def temporary_database(self) -> str:
        return f"{self.database['name']}__restore_tmp"

    @property
    def retired_database(self) -> str:
        return f"{self.database['name']}__restore_old"


def restore(request: HostRequest) -> HostResult:
    """Converge a restore without a journal or an operation-derived name.

    The only database arrangements this procedure recognizes are the normal
    initial database, its deterministic restore temporary, and the old
    database retained between rename and completed selection publication.  A
    different arrangement is manual rather than a guess.
    """

    state: HostState | None = None
    inputs: _Inputs | None = None
    source: BackupRecord | None = None
    safety_backup: BackupRecord | None = None
    report: object = {}
    changed = False
    try:
        inputs = _inputs(request)
        _safe_credentials(inputs.credentials)
        with lifecycle_lock(inputs.paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
            state = _observe(inputs, allow_selection_transition=True)
            source = _source_record(state, inputs)
            _validate_source_dump(source, inputs)
            selection_status = _selection_status(state, inputs, source)
            arrangement = _database_arrangement(inputs)
            _validate_arrangement(arrangement, inputs, selection_status)

            if selection_status == "completed":
                _service("start")
                verification = _verify(request, inputs, source.source_release_id)
                if verification.outcome != "succeeded":
                    raise _Retryable("verification")
                report = verification.state.get("report", {})
                if arrangement == frozenset({str(inputs.database["name"]), inputs.retired_database}):
                    _drop_database(inputs, inputs.retired_database)
                    changed = True
                state = _observe(inputs, allow_selection_transition=False)
            else:
                swapped = frozenset({str(inputs.database["name"]), inputs.retired_database})
                if arrangement == swapped:
                    # A replay after the second rename has no remaining
                    # database consequence.  The only safe evidence is the
                    # unique completed backup of the original selected
                    # database; never create a second backup of the restored
                    # database and attach it to the old transition.
                    safety_backup = _selection_backup(state, inputs, source, None)
                else:
                    safety_database = _safety_database(arrangement, inputs)
                    safety_state = _state_for_database(state, inputs, safety_database)
                    try:
                        safety_backup = create_validated_backup(
                            safety_state,
                            inputs.paths,
                            _database_with_name(inputs.database, safety_database),
                            inputs.credentials,
                            purpose="pre-restore",
                        )
                    except (CommandError, RecordError, OSError, ValueError) as error:
                        raise _Retryable("backup") from error
                    changed = True
                    _service("stop")
                    arrangement, restored = _converge_database(arrangement, inputs, source)
                    changed = changed or restored
                state = _observe(inputs, allow_selection_transition=True)
                _require_selection_transition(state, inputs, source)

                if state.selected_release_id != source.source_release_id:
                    try:
                        _atomically_select(inputs.paths, source.source_release_id)
                    except (OSError, ValueError) as error:
                        raise _Retryable("selection") from error
                    changed = True
                    state = _observe(inputs, allow_selection_transition=True)
                _require_selected_target(state, inputs, source)

                _service("start")
                verification = _verify(request, inputs, source.source_release_id)
                if verification.outcome != "succeeded":
                    raise _Retryable("verification")
                report = verification.state.get("report", {})

                safety_backup = _selection_backup(state, inputs, source, safety_backup)
                state = _record_selection(state, inputs, source, safety_backup)
                changed = True
                _drop_database(inputs, inputs.retired_database)
                state = _observe(inputs, allow_selection_transition=False)
    except LifecycleLockContention:
        return _result(request, "retryable", "lifecycle lock is unavailable", state, inputs, source, locked=True)
    except RestoreRefused:
        return _result(request, "refused", "restore source is not safe", state, inputs, source)
    except RestoreManual:
        return _result(request, "manual", "restore state is contradictory", state, inputs, source)
    except _Retryable as error:
        return _result(
            request,
            "retryable",
            "restore did not complete; rerun to converge",
            state,
            inputs,
            source,
            boundary=error.boundary,
        )
    except StateAmbiguityError:
        return _result(request, "manual", "restore authority is contradictory", state, inputs, source)
    except CommandError:
        return _result(
            request,
            "retryable",
            "restore observation did not complete; rerun to converge",
            state,
            inputs,
            source,
            boundary="observation",
        )
    except (PathAuthorityError, RecordError, TypeError, ValueError):
        return _result(request, "refused", "restore request is unsafe", state, inputs, source)
    except OSError:
        return _result(request, "manual", "restore authority is contradictory", state, inputs, source)

    return _result(
        request,
        "succeeded",
        "restore converged",
        state,
        inputs,
        source,
        changed=changed,
        safety_backup=safety_backup,
        report=report,
    )


def _inputs(request: HostRequest) -> _Inputs:
    if not isinstance(request, HostRequest) or request.operation != "restore":
        raise ValueError("restore needs a final host request")
    if set(request.expected_state) != _EXPECTED_STATE_KEYS or set(request.parameters) != _PARAMETER_KEYS:
        raise ValueError("restore request is incomplete")
    current = request.expected_state["selected_release_id"]
    expected_backup = request.expected_state["backup_id"]
    backup = request.parameters["backup_id"]
    if type(current) is not str or type(expected_backup) is not str or expected_backup != backup:
        raise ValueError("restore confirmation state is invalid")
    current = validate_release_id(current)
    backup = _backup_id(backup)
    credentials = request.parameters["credentials_path"]
    if type(credentials) is not str or not Path(credentials).is_absolute():
        raise ValueError("restore credentials path is invalid")
    return _Inputs(
        ManagedPaths.from_mapping(request.paths),
        current,
        backup,
        Path(credentials),
        _database(request.parameters["database"]),
        _verification(request.parameters["verification"]),
    )


def _backup_id(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"backup-[0-9a-f]{32}", value) is None:
        raise ValueError("restore backup identifier is invalid")
    return value


def _database(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != _DATABASE_KEYS:
        raise ValueError("restore database settings are invalid")
    if (
        type(value["host"]) is not str
        or not value["host"]
        or type(value["role"]) is not str
        or not _DATABASE_NAME_RE.fullmatch(value["role"])
        or type(value["name"]) is not str
        or _DATABASE_NAME_RE.fullmatch(value["name"]) is None
        or type(value["port"]) is not int
        or not 0 < value["port"] < 65_536
    ):
        raise ValueError("restore database settings are invalid")
    return value


def _verification(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("restore verification settings are invalid")
    return value


def _safe_credentials(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise ValueError("restore credentials are unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise ValueError("restore credentials are unsafe")


def _observe(inputs: _Inputs, *, allow_selection_transition: bool) -> HostState:
    # A post-rename replay has no canonical database name temporarily.  The
    # physical selection and completed records remain observable without
    # asking PostgreSQL for the canonical schema first.
    return observe_host_state(inputs.paths, allow_selection_transition=allow_selection_transition)


def _state_for_database(state: HostState, inputs: _Inputs, name: str) -> HostState:
    observed = _observe_database(_database_with_name(inputs.database, name), inputs.credentials)
    versions = observed["applied_migrations"]
    database_state = observed["state"]
    if not isinstance(versions, tuple) or database_state != "ready":
        raise RestoreManual("safety backup database observation is invalid")
    return replace(state, applied_migrations=versions, database_state=database_state)


def _observe_database(database: Mapping[str, object], credentials: Path) -> Mapping[str, object]:
    environment = {"PGPASSFILE": credentials.as_posix()}
    common = (
        "psql", "--no-psqlrc", "--tuples-only", "--no-align", "--host", str(database["host"]),
        "--port", str(database["port"]), "--username", str(database["role"]),
        "--dbname", str(database["name"]), "--no-password",
    )
    table = run_command(
        (*common, "--command", "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'schema_migrations'"),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout.strip()
    if table != b"1":
        raise RestoreManual("database migration authority is unavailable")
    output = run_command(
        (*common, "--command", "SELECT version FROM schema_migrations ORDER BY version"),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout.decode("utf-8", "strict")
    try:
        versions = tuple(int(item) for item in output.splitlines() if item)
    except ValueError as error:
        raise RestoreManual("database migration authority is invalid") from error
    if versions != tuple(sorted(set(versions))):
        raise RestoreManual("database migration authority is invalid")
    return {"state": "ready", "applied_migrations": versions}


def _source_record(state: HostState, inputs: _Inputs) -> BackupRecord:
    source = next((item for item in state.backups if item.backup_id == inputs.backup_id), None)
    if source is None:
        raise RestoreRefused("selected backup is unavailable")
    release = next((item for item in state.releases if item.release_id == source.source_release_id), None)
    if release is None:
        raise RestoreManual("source backup release is unavailable")
    if _migration_versions(release) != source.migration_versions:
        raise RestoreManual("source backup and release migrations conflict")
    if not any(item.release_id == source.source_release_id for item in state.selections):
        raise RestoreManual("source release is not proven by successful selection history")
    return source


def _validate_source_dump(source: BackupRecord, inputs: _Inputs) -> None:
    dump = Path(inputs.paths.local(inputs.paths.backup_root / f"{source.backup_id}.dump"))
    try:
        details = dump.lstat()
    except OSError as error:
        raise RestoreManual("source dump is unavailable") from error
    if (
        dump.parent != Path(inputs.paths.local(inputs.paths.backup_root))
        or stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_size <= 0
        or details.st_mode & 0o7022
        or _sha256(dump) != source.dump_sha256
    ):
        raise RestoreManual("source dump identity is contradictory")
    try:
        run_command(
            ("pg_restore", "--list", dump.as_posix()),
            env={"PGPASSFILE": inputs.credentials.as_posix()},
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
        )
    except CommandError as error:
        raise RestoreRefused("source dump cannot be validated") from error


def _selection_status(state: HostState, inputs: _Inputs, source: BackupRecord) -> str:
    target = source.source_release_id
    if not state.selections:
        raise RestoreManual("selection history is absent")
    latest = state.selections[-1]
    if state.selected_release_id == inputs.current_release_id:
        if latest.release_id != inputs.current_release_id:
            raise RestoreManual("selection history contradicts the confirmed current release")
        return "pending"
    if state.selected_release_id != target:
        raise RestoreManual("selected release is unrelated to the restore source")
    if latest.release_id == target:
        if latest.previous_release_id != inputs.current_release_id:
            raise RestoreManual("completed restore selection is unrelated to the request")
        return "completed"
    if latest.release_id == inputs.current_release_id:
        return "selected"
    raise RestoreManual("restore selection transition is not recognizable")


def _database_arrangement(inputs: _Inputs) -> frozenset[str]:
    names = _admin(
        inputs,
        "SELECT datname FROM pg_database WHERE datname IN "
        f"('{inputs.database['name']}', '{inputs.temporary_database}', '{inputs.retired_database}') "
        "ORDER BY datname",
    )
    return frozenset(item for item in names.splitlines() if item)


def _validate_arrangement(arrangement: frozenset[str], inputs: _Inputs, selection_status: str) -> None:
    live = str(inputs.database["name"])
    recognized = {
        frozenset({live}),
        frozenset({live, inputs.temporary_database}),
        frozenset({inputs.temporary_database, inputs.retired_database}),
        frozenset({live, inputs.retired_database}),
    }
    if arrangement not in recognized:
        raise RestoreManual("database identities are not a recognized restore arrangement")
    if selection_status == "completed" and arrangement not in {frozenset({live}), frozenset({live, inputs.retired_database})}:
        raise RestoreManual("completed restore has an unfinished database arrangement")
    if selection_status != "completed" and arrangement == frozenset({live, inputs.retired_database}):
        # The database swap completed but selection publication did not.  It
        # is safe to resume through selection and verification.
        return


def _safety_database(arrangement: frozenset[str], inputs: _Inputs) -> str:
    live = str(inputs.database["name"])
    if live in arrangement:
        return live
    if inputs.retired_database in arrangement and inputs.temporary_database in arrangement:
        return inputs.retired_database
    raise RestoreManual("no original database is available for a fresh safety backup")


def _converge_database(
    arrangement: frozenset[str], inputs: _Inputs, source: BackupRecord
) -> tuple[frozenset[str], bool]:
    live = str(inputs.database["name"])
    temporary = inputs.temporary_database
    retired = inputs.retired_database
    changed = False
    if arrangement == frozenset({live}):
        _validate_capacity(source.source_database_size_bytes)
        _admin(inputs, f'CREATE DATABASE "{temporary}" OWNER "{inputs.database["role"]}"')
        _restore_dump(inputs, source, temporary)
        _validate_restored_database(inputs, temporary, source.migration_versions)
        arrangement = frozenset({live, temporary})
        changed = True
    elif arrangement == frozenset({live, temporary}):
        _validate_restored_database(inputs, temporary, source.migration_versions)
    elif arrangement == frozenset({temporary, retired}):
        _validate_restored_database(inputs, temporary, source.migration_versions)
        _rename_database(inputs, temporary, live)
        arrangement = frozenset({live, retired})
        changed = True
    elif arrangement == frozenset({live, retired}):
        return arrangement, changed
    else:  # pragma: no cover - caller validates the finite set
        raise RestoreManual("database arrangement is unknown")

    if arrangement == frozenset({live, temporary}):
        _terminate_connections(inputs, live, temporary)
        _rename_database(inputs, live, retired)
        _rename_database(inputs, temporary, live)
        arrangement = frozenset({live, retired})
        changed = True
    return arrangement, changed


def _validate_capacity(size: int) -> None:
    if type(size) is not int or size <= 0 or available_bytes(_POSTGRES_DATA) < size * 2:
        raise RestoreRefused("restore capacity is insufficient")


def _admin(inputs: _Inputs, sql: str) -> str:
    completed = run_command(
        (
            "sudo", "-u", "postgres", "--", "psql", "--no-psqlrc", "--host", "/var/run/postgresql",
            "--port", str(inputs.database["port"]), "--username", "postgres", "--dbname=postgres",
            "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1", "--command", sql,
        ),
        env={"PGPASSFILE": inputs.credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )
    return completed.stdout.decode("utf-8", "strict")


def _restore_dump(inputs: _Inputs, source: BackupRecord, database: str) -> None:
    dump = Path(inputs.paths.local(inputs.paths.backup_root / f"{source.backup_id}.dump"))
    run_command(
        (
            "pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", "--host",
            str(inputs.database["host"]), "--port", str(inputs.database["port"]), "--username",
            str(inputs.database["role"]), "--dbname", database, "--no-password", dump.as_posix(),
        ),
        env={"PGPASSFILE": inputs.credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )


def _validate_restored_database(inputs: _Inputs, database: str, expected: tuple[int, ...]) -> None:
    environment = {"PGPASSFILE": inputs.credentials.as_posix()}
    common = (
        "psql", "--no-psqlrc", "--host", str(inputs.database["host"]), "--port",
        str(inputs.database["port"]), "--username", str(inputs.database["role"]), "--dbname", database,
        "--no-password", "--tuples-only", "--no-align",
    )
    table = run_command(
        (*common, "--command", "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'schema_migrations'"),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout.strip()
    if table != b"1":
        raise RestoreManual("restored database migration table is unavailable")
    output = run_command(
        (*common, "--command", "SELECT version FROM schema_migrations ORDER BY version"),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout.decode("utf-8", "strict")
    try:
        actual = tuple(int(item) for item in output.splitlines() if item)
    except ValueError as error:
        raise RestoreManual("restored database migrations are invalid") from error
    if actual != expected:
        raise RestoreManual("restored database migrations do not match the source backup")


def _terminate_connections(inputs: _Inputs, *names: str) -> None:
    literals = ", ".join(f"'{name}'" for name in names)
    _admin(
        inputs,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname IN ({literals}) AND pid <> pg_backend_pid()",
    )


def _rename_database(inputs: _Inputs, source: str, destination: str) -> None:
    _admin(inputs, f'ALTER DATABASE "{source}" RENAME TO "{destination}"')


def _drop_database(inputs: _Inputs, name: str) -> None:
    _admin(inputs, f'DROP DATABASE "{name}" WITH (FORCE)')


def _require_selection_transition(state: HostState, inputs: _Inputs, source: BackupRecord) -> None:
    selection_status = _selection_status(state, inputs, source)
    if selection_status not in {"pending", "selected"}:
        raise RestoreManual("database restore lacks a pending selection transition")


def _require_selected_target(state: HostState, inputs: _Inputs, source: BackupRecord) -> None:
    if state.selected_release_id != source.source_release_id:
        raise RestoreManual("restore selection did not retain the source release")
    selection_status = _selection_status(state, inputs, source)
    if selection_status not in {"selected", "completed"}:
        raise RestoreManual("restore target selection is not recognizable")


def _selection_backup(
    state: HostState,
    inputs: _Inputs,
    source: BackupRecord,
    local: BackupRecord | None,
) -> BackupRecord:
    if local is not None:
        if local.source_release_id != inputs.current_release_id:
            raise RestoreManual("fresh restore backup has the wrong source release")
        if any(item.backup_id == local.backup_id for item in state.backups):
            return local
        raise RestoreManual("fresh restore backup was not published")

    previous = _observe_database(
        _database_with_name(inputs.database, inputs.retired_database), inputs.credentials
    )
    migrations = previous.get("applied_migrations")
    if not isinstance(migrations, tuple):
        raise RestoreManual("retired database migration authority is invalid")
    candidates = tuple(
        item
        for item in state.backups
        if item.source_release_id == inputs.current_release_id
        and item.migration_versions == migrations
    )
    if len(candidates) != 1:
        raise RestoreManual("restore safety backup cannot be recovered unambiguously")
    return candidates[0]


def _record_selection(
    state: HostState,
    inputs: _Inputs,
    source: BackupRecord,
    safety_backup: BackupRecord,
) -> HostState:
    target = source.source_release_id
    latest = state.selections[-1] if state.selections else None
    if latest is not None and latest.release_id == target:
        return _observe(inputs, allow_selection_transition=False)
    if latest is None or latest.release_id != inputs.current_release_id:
        raise RestoreManual("restore history changed before record publication")
    selected_at = max(datetime.now(UTC).replace(microsecond=0), latest.selected_at + timedelta(seconds=1))
    try:
        append_selection(
            inputs.paths,
            SelectionRecord(target, inputs.current_release_id, safety_backup.backup_id, selected_at),
        )
    except (OSError, RecordError, ValueError) as error:
        raise _Retryable("selection") from error
    return _observe(inputs, allow_selection_transition=False)


def _service(action: str) -> None:
    try:
        run_command(("systemctl", action, "taskman.service"), timeout_seconds=_COMMAND_TIMEOUT_SECONDS)
    except CommandError as error:
        raise _Retryable("start" if action == "start" else "stop") from error


def _atomically_select(paths: ManagedPaths, release_id: str) -> None:
    root = Path(paths.local(paths.install_root))
    target = Path(paths.local(paths.release_root / release_id))
    temporary = root / f".current-{release_id}.tmp"
    if temporary.exists() or temporary.is_symlink():
        details = temporary.lstat()
        if not stat.S_ISLNK(details.st_mode) or temporary.resolve(strict=False) != target:
            raise RestoreManual("selection temporary is unsafe")
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, Path(paths.local(paths.current_link)))
    _fsync_directory(root)


def _verify(request: HostRequest, inputs: _Inputs, target: str) -> HostResult:
    return verify(
        HostRequest(
            PROTOCOL_VERSION,
            "verify",
            request.correlation_id,
            {"expected_release_id": target},
            request.paths,
            inputs.verification,
        ),
        lifecycle_locked=True,
    )


def _migration_versions(record: ReleaseRecord) -> tuple[int, ...]:
    versions: list[int] = []
    for migration in record.migrations:
        name = migration.get("filename")
        match = _MIGRATION_RE.fullmatch(name) if type(name) is str else None
        if match is None:
            raise RestoreManual("release migration record is invalid")
        versions.append(int(match.group(1)))
    result = tuple(versions)
    if result != tuple(sorted(set(result))):
        raise RestoreManual("release migration record is invalid")
    return result


def _database_with_name(database: Mapping[str, object], name: str) -> Mapping[str, object]:
    return {**database, "name": name}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise RestoreManual("source dump cannot be read") from error
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _result(
    request: HostRequest,
    outcome: str,
    message: str,
    state: HostState | None,
    inputs: _Inputs | None,
    source: BackupRecord | None,
    *,
    boundary: str | None = None,
    locked: bool = False,
    changed: bool | None = None,
    safety_backup: BackupRecord | None = None,
    report: object | None = None,
) -> HostResult:
    current = None if inputs is None else inputs.current_release_id
    backup_id = None if source is None else source.backup_id
    intended = None if source is None else source.source_release_id
    facts: dict[str, object] = {
        "backup_id": backup_id,
        "pre_restore_backup_id": None if safety_backup is None else safety_backup.backup_id,
        "current_release_id": current,
        "intended_release_id": intended,
        "selected_release_id": None if state is None else state.selected_release_id,
        "service_state": "unknown" if state is None else state.service_state,
        "database_state": "unknown" if state is None else state.database_state,
    }
    if boundary is not None:
        facts["failed_boundary"] = boundary
    if locked:
        facts["locked"] = True
    if changed is not None:
        facts.update(
            {
                "changed": changed,
                "database_state": "restored",
                "service_state": "running",
                "report": {} if report is None else report,
            }
        )
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.correlation_id,
        outcome,
        message,
        facts,
        () if state is None else state.warnings,
    )


__all__ = ["restore"]
