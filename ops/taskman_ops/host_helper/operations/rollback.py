"""Replayable rollback from completed selection history."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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
from ..verification import verify
from .backup import create_validated_backup


_EXPECTED_STATE_KEYS = frozenset({"selected_release_id"})
_PARAMETER_KEYS = frozenset({"target_release_id", "credentials_path", "database", "verification"})
_DATABASE_KEYS = frozenset({"host", "port", "role", "name"})
_LOCK_TIMEOUT_SECONDS = 5.0
_COMMAND_TIMEOUT_SECONDS = 60.0
_RUNTIME_ENVIRONMENT = Path("/etc/taskman/taskman.env")
_MIGRATION_RE = re.compile(r"([0-9]{14})_[a-z0-9_]+\.exs\Z")


class RollbackRefused(ValueError):
    """The confirmed target is not safe to select."""


class RollbackManual(RuntimeError):
    """Completed facts cannot identify one safe replay transition."""


class _Retryable(RuntimeError):
    def __init__(self, boundary: str) -> None:
        super().__init__(boundary)
        self.boundary = boundary


@dataclass(frozen=True)
class _Inputs:
    paths: ManagedPaths
    current_release_id: str
    target_release_id: str
    credentials: Path
    database: Mapping[str, object]
    verification: Mapping[str, object]


def rollback(request: HostRequest) -> HostResult:
    """Converge one history-proven compatible release selection.

    Rollback never runs reverse migrations.  The selected target must have the
    same observed schema and be the immediately preceding completed selection.
    A retry may find the target link already replaced but its selection record
    not yet published; it then repeats only start, verification, and record
    publication.
    """

    state: HostState | None = None
    backup: BackupRecord | None = None
    report: object = {}
    changed = False
    inputs: _Inputs | None = None
    try:
        inputs = _inputs(request)
        _safe_credentials(inputs.credentials)
        with lifecycle_lock(inputs.paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
            state = _observe(inputs, allow_selection_transition=True)
            selection_status = _selection_status(state, inputs)

            if selection_status == "pending":
                try:
                    backup = create_validated_backup(
                        state,
                        inputs.paths,
                        inputs.database,
                        inputs.credentials,
                        purpose="pre-rollback",
                    )
                except (CommandError, RecordError, OSError, ValueError) as error:
                    raise _Retryable("backup") from error
                changed = True
                state = _observe(inputs, allow_selection_transition=True)
                _require_pending_selection(state, inputs)
                _service("stop")
                changed = True
                try:
                    _atomically_select(inputs.paths, inputs.target_release_id)
                except (OSError, ValueError) as error:
                    raise _Retryable("selection") from error
                changed = True
                state = _observe(inputs, allow_selection_transition=True)
                selection_status = _selection_status(state, inputs)

            if selection_status == "selected":
                if backup is None:
                    try:
                        backup = create_validated_backup(
                            replace(state, selected_release_id=inputs.current_release_id),
                            inputs.paths,
                            inputs.database,
                            inputs.credentials,
                            purpose="pre-rollback",
                        )
                    except (CommandError, RecordError, OSError, ValueError) as error:
                        raise _Retryable("backup") from error
                    state = _observe(inputs, allow_selection_transition=True)
                backup = _published_safety_backup(state, inputs, backup)
                changed = True
            elif selection_status == "completed":
                backup = _recorded_backup(state, inputs)
            else:  # pragma: no cover - _selection_status has a closed return vocabulary
                raise RollbackManual("rollback selection status is unknown")

            _service("start")
            verification = _verify(request, inputs)
            if verification.outcome != "succeeded":
                raise _Retryable("verification")
            report = verification.state.get("report", {})

            if selection_status != "completed":
                state = _record_selection(state, inputs, backup)
                changed = True
            else:
                state = _observe(inputs, allow_selection_transition=False)
    except LifecycleLockContention:
        return _result(request, "retryable", "lifecycle lock is unavailable", state, inputs, locked=True)
    except RollbackRefused:
        return _result(request, "refused", "rollback target is not compatible with successful history", state, inputs)
    except RollbackManual:
        return _result(request, "manual", "rollback state is contradictory", state, inputs)
    except _Retryable as error:
        return _result(
            request,
            "retryable",
            "rollback did not complete; rerun to converge",
            state,
            inputs,
            boundary=error.boundary,
        )
    except StateAmbiguityError:
        return _result(request, "manual", "rollback authority is contradictory", state, inputs)
    except CommandError:
        return _result(
            request,
            "retryable",
            "rollback observation did not complete; rerun to converge",
            state,
            inputs,
            boundary="observation",
        )
    except (PathAuthorityError, RecordError, TypeError, ValueError):
        return _result(request, "refused", "rollback request is unsafe", state, inputs)
    except OSError:
        return _result(request, "manual", "rollback authority is contradictory", state, inputs)

    return _result(
        request,
        "succeeded",
        "rollback converged",
        state,
        inputs,
        changed=changed,
        backup_id=None if backup is None else backup.backup_id,
        report=report,
    )


def _inputs(request: HostRequest) -> _Inputs:
    if not isinstance(request, HostRequest) or request.operation != "rollback":
        raise ValueError("rollback needs a final host request")
    if set(request.expected_state) != _EXPECTED_STATE_KEYS or set(request.parameters) != _PARAMETER_KEYS:
        raise ValueError("rollback request is incomplete")
    current = request.expected_state["selected_release_id"]
    target = request.parameters["target_release_id"]
    if type(current) is not str or type(target) is not str:
        raise ValueError("rollback release identifier is invalid")
    current = validate_release_id(current)
    target = validate_release_id(target)
    if current == target:
        raise RollbackRefused("rollback target is already current")
    credentials = request.parameters["credentials_path"]
    if type(credentials) is not str or not Path(credentials).is_absolute():
        raise ValueError("rollback credentials path is invalid")
    return _Inputs(
        ManagedPaths.from_mapping(request.paths),
        current,
        target,
        Path(credentials),
        _database(request.parameters["database"]),
        _verification(request.parameters["verification"]),
    )


def _database(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != _DATABASE_KEYS:
        raise ValueError("rollback database settings are invalid")
    if (
        type(value["host"]) is not str
        or not value["host"]
        or type(value["role"]) is not str
        or not value["role"]
        or type(value["name"]) is not str
        or not value["name"]
        or type(value["port"]) is not int
        or not 0 < value["port"] < 65_536
    ):
        raise ValueError("rollback database settings are invalid")
    return value


def _verification(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("rollback verification settings are invalid")
    return value


def _safe_credentials(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise ValueError("rollback credentials are unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise ValueError("rollback credentials are unsafe")


def _observe(inputs: _Inputs, *, allow_selection_transition: bool) -> HostState:
    return observe_host_state(
        inputs.paths,
        database=_observe_database(inputs.database, inputs.credentials),
        allow_selection_transition=allow_selection_transition,
    )


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
        raise RollbackManual("current database migration authority is unavailable")
    output = run_command(
        (*common, "--command", "SELECT version FROM schema_migrations ORDER BY version"),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout.decode("utf-8", "strict")
    try:
        versions = tuple(int(item) for item in output.splitlines() if item)
    except ValueError as error:
        raise RollbackManual("current database migration authority is invalid") from error
    if versions != tuple(sorted(set(versions))):
        raise RollbackManual("current database migration authority is invalid")
    return {"state": "ready", "applied_migrations": versions}


def _selection_status(state: HostState, inputs: _Inputs) -> str:
    _require_target_schema(state, inputs)
    if not state.selections:
        raise RollbackRefused("selection history is absent")
    latest = state.selections[-1]
    if state.selected_release_id == inputs.current_release_id:
        if latest.release_id != inputs.current_release_id or latest.previous_release_id != inputs.target_release_id:
            raise RollbackRefused("selection history does not prove the target")
        return "pending"
    if state.selected_release_id != inputs.target_release_id:
        raise RollbackManual("selected release is unrelated to the confirmed rollback")
    if latest.release_id == inputs.target_release_id:
        if latest.previous_release_id != inputs.current_release_id:
            raise RollbackManual("recorded selection is unrelated to the confirmed rollback")
        return "completed"
    if latest.release_id == inputs.current_release_id and latest.previous_release_id == inputs.target_release_id:
        return "selected"
    raise RollbackManual("selected release lacks a recognizable rollback transition")


def _require_target_schema(state: HostState, inputs: _Inputs) -> None:
    record = next((item for item in state.releases if item.release_id == inputs.target_release_id), None)
    if record is None:
        raise RollbackRefused("target release is not installed")
    if _migration_versions(record) != state.applied_migrations:
        raise RollbackRefused("target release is not compatible with the current database")


def _require_pending_selection(state: HostState, inputs: _Inputs) -> None:
    if _selection_status(state, inputs) != "pending":
        raise RollbackManual("selection changed before rollback stop")


def _migration_versions(record: ReleaseRecord) -> tuple[int, ...]:
    versions: list[int] = []
    for migration in record.migrations:
        name = migration.get("filename")
        match = _MIGRATION_RE.fullmatch(name) if type(name) is str else None
        if match is None:
            raise RollbackManual("release migration record is invalid")
        versions.append(int(match.group(1)))
    result = tuple(versions)
    if result != tuple(sorted(set(result))):
        raise RollbackManual("release migration record is invalid")
    return result


def _published_safety_backup(state: HostState, inputs: _Inputs, backup: BackupRecord | None) -> BackupRecord:
    if backup is None or backup.source_release_id != inputs.current_release_id:
        raise RollbackManual("fresh rollback backup has the wrong source release")
    if any(item.backup_id == backup.backup_id for item in state.backups):
        return backup
    raise RollbackManual("fresh rollback backup was not published")


def _recorded_backup(state: HostState, inputs: _Inputs) -> BackupRecord | None:
    latest = state.selections[-1]
    if latest.backup_id is None:
        raise RollbackManual("completed rollback lacks its safety backup")
    backup = next((item for item in state.backups if item.backup_id == latest.backup_id), None)
    if backup is None:
        raise RollbackManual("completed rollback backup is unavailable")
    if backup.source_release_id != inputs.current_release_id:
        raise RollbackManual("completed rollback backup has the wrong source release")
    return backup


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
            raise RollbackManual("selection temporary is unsafe")
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, Path(paths.local(paths.current_link)))
    _fsync_directory(root)


def _record_selection(state: HostState, inputs: _Inputs, backup: BackupRecord | None) -> HostState:
    if backup is None:
        raise RollbackManual("rollback selection needs its safety backup")
    if state.selected_release_id != inputs.target_release_id:
        raise RollbackManual("rollback verification did not retain the target")
    latest = state.selections[-1] if state.selections else None
    if latest is not None and latest.release_id == inputs.target_release_id:
        return _observe(inputs, allow_selection_transition=False)
    if latest is None or latest.release_id != inputs.current_release_id or latest.previous_release_id != inputs.target_release_id:
        raise RollbackManual("rollback history changed before record publication")
    selected_at = max(datetime.now(UTC).replace(microsecond=0), latest.selected_at + timedelta(seconds=1))
    try:
        append_selection(
            inputs.paths,
            SelectionRecord(inputs.target_release_id, inputs.current_release_id, backup.backup_id, selected_at),
        )
    except (OSError, RecordError, ValueError) as error:
        raise _Retryable("selection") from error
    return _observe(inputs, allow_selection_transition=False)


def _verify(request: HostRequest, inputs: _Inputs) -> HostResult:
    return verify(
        HostRequest(
            PROTOCOL_VERSION,
            "verify",
            request.correlation_id,
            {"expected_release_id": inputs.target_release_id},
            request.paths,
            inputs.verification,
        ),
        lifecycle_locked=True,
    )


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
    *,
    boundary: str | None = None,
    locked: bool = False,
    changed: bool | None = None,
    backup_id: str | None = None,
    report: object | None = None,
) -> HostResult:
    target = None if inputs is None else inputs.target_release_id
    current = None if inputs is None else inputs.current_release_id
    facts: dict[str, object] = {
        "previous_release_id": current,
        "target_release_id": target,
        "selected_release_id": None if state is None else state.selected_release_id,
        "database_state": "unchanged",
        "service_state": "unknown" if state is None else state.service_state,
    }
    if boundary is not None:
        facts["failed_boundary"] = boundary
    if locked:
        facts["locked"] = True
    if changed is not None:
        facts.update(
            {
                "changed": changed,
                "backup_id": backup_id,
                "report": {} if report is None else report,
                "service_state": "running",
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


__all__ = ["rollback"]
