"""Replayable same-backup restore with durable database identity authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION
from taskman_ops.host_protocol.mutation_results import unavailable_observations

from ...checksums import sha256_file
from ..backup_helper import BackupHelperError, converge_backup_helper
from ..backup_protection import complete_successful_selection
from ..backups import BackupAuthorityError
from ..commands import CommandError, run_command
from ..credentials import validate_credentials
from ..database import database_mapping, release_migration_versions
from ..lock import LifecycleLockContention, lifecycle_lock
from ..operations.backup import create_validated_backup
from ..operations.discover import _scheduler_facts
from ..paths import ManagedPaths, PathAuthorityError
from ..records import BackupRecord, RecordError
from ..restore_database import (
    RestoreDatabaseError,
    begin_temporary_rebuild,
    create_temporary_database,
    drop_registered_retired,
    drop_registered_temporary,
    load_registered_temporary,
    observe_restore_databases,
    register_restored_database,
    rename_registered_database,
    validate_restore_database_state,
)
from ..restore_target import (
    RestoreTarget,
    remove_restore_target as _remove_restore_target_file,
    restore_target_sha256,
    write_restore_target,
)
from ..selection import SelectionAmbiguityError, select_current
from ..services import change_service
from ..state import (
    HostState,
    StateAmbiguityError,
    mutation_observation_availability,
    mutation_observations,
    observe_host_state,
)
from ..verification import available_bytes, verification_request, verify


_EXPECTED_STATE_KEYS = frozenset({
    "selected_release_id", "last_successful_selection_id", "applied_migrations",
    "backup_protection_sha256", "scheduled_backup_sha256", "backup_timer_enabled",
    "backup_id", "restore_target_sha256", "restore_database_state",
})
_PARAMETER_KEYS = frozenset({
    "backup_id", "credentials_path", "database", "verification", "backup_helper",
    "prune_backup_ids", "replace_unfinished", "reapply",
})
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SELECTION_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")
_LOCK_TIMEOUT_SECONDS = 5.0
_COMMAND_TIMEOUT_SECONDS = 60.0
_POSTGRES_DATA = Path("/var/lib/postgresql")


class RestoreRefused(ValueError):
    """The request or confirmed authority cannot permit a restore."""


class RestoreManual(RuntimeError):
    """Observed restore authority is contradictory."""


class _Retryable(RuntimeError):
    def __init__(self, boundary: str, *, possibly_changed: bool = True) -> None:
        super().__init__(boundary)
        self.boundary = boundary
        self.possibly_changed = possibly_changed


@dataclass(frozen=True)
class _Inputs:
    paths: ManagedPaths
    expected_state: Mapping[str, object]
    backup_id: str
    credentials: Path
    database: Mapping[str, object]
    verification: Mapping[str, object]
    backup_helper: Mapping[str, object]
    prune_backup_ids: tuple[str, ...]
    replace_unfinished: bool
    reapply: bool


# Kept as a module-owned name so interruption tests and the workflow share the
# same bounded cleanup boundary.
remove_restore_target = _remove_restore_target_file


def restore(request: HostRequest) -> HostResult:
    inputs: _Inputs | None = None
    state: HostState | None = None
    source: BackupRecord | None = None
    safety: BackupRecord | None = None
    changed = False
    possibly_changed = False
    report: Mapping[str, object] | None = None
    try:
        inputs = _inputs(request)
        validate_credentials(inputs.credentials)
        with lifecycle_lock(inputs.paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS) as lock:
            state, databases = _observe_locked(inputs)
            _validate_expected_state(state, databases, inputs)
            source = _source_record(state, inputs)
            _validate_source_dump(source, inputs)

            target = state.restore_target
            if target is not None and _durable_success(state, databases, target, source):
                safety = _required_backup(state, target.safety_backup_id, inputs)
                changed = _cleanup_completed(inputs, state, databases, target) or changed
                state, databases = _observe_locked(inputs, include_runtime=True)
                return _result(request, "succeeded", "restore cleanup converged", state, inputs, source, changed=changed, report=None, databases=databases, pre_restore_backup_id=safety.backup_id)
            if target is not None and inputs.reapply:
                raise RestoreRefused("unfinished restore must be resumed before reapply")
            if target is None and not inputs.reapply and _completed_without_binding(state, databases, source):
                state, databases = _observe_locked(inputs, include_runtime=True)
                return _result(
                    request, "succeeded", "restore already completed", state, inputs, source,
                    changed=False, report=None, databases=databases,
                    pre_restore_backup_id=state.latest_successful_selection.backup_id,
                )

            if target is None:
                _require_initial_arrangement(databases)
            else:
                _validate_bound_target(state, databases, target, source, inputs)

            try:
                convergence = converge_backup_helper(
                    inputs.paths,
                    inputs.backup_helper,
                    confirmed_checksum=inputs.expected_state["scheduled_backup_sha256"],
                    confirmed_enabled=inputs.expected_state["backup_timer_enabled"],
                    revalidate=lambda: _revalidate_before_consequences(inputs),
                    timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
                    lock=lock,
                )
            except BackupHelperError as error:
                changed = changed or any((error.mutation.paused, error.mutation.replaced, error.mutation.restarted))
                possibly_changed = changed
                raise _Retryable("backup_helper", possibly_changed=possibly_changed) from error
            changed = changed or any((convergence.mutation.paused, convergence.mutation.replaced, convergence.mutation.restarted))

            state, databases = _observe_locked(inputs)
            if target is None:
                safety_state = _state_for_database(state, databases["canonical"])
                try:
                    safety = create_validated_backup(
                        safety_state, inputs.paths, inputs.database, inputs.credentials,
                        purpose="pre-restore",
                    )
                except (BackupAuthorityError, CommandError, OSError, RecordError, ValueError) as error:
                    raise _Retryable("backup") from error
                changed = True
                target = RestoreTarget(
                    1, source.backup_id, source.dump_sha256, source.source_release_id,
                    state.latest_successful_selection_filename, state.selected_release_id,
                    int(databases["canonical"]["oid"]), None, True,
                    safety.backup_id, None,
                    ({"backup_id": safety.backup_id, "attempt_number": 0},),
                )
                try:
                    write_restore_target(inputs.paths, target)
                except (OSError, RecordError) as error:
                    raise _Retryable("restore") from error
                changed = True
                state, databases = _observe_locked(inputs)
            else:
                safety = _required_backup(state, target.safety_backup_id, inputs)
                for attempt in target.safety_backup_attempts:
                    _required_backup(state, str(attempt["backup_id"]), inputs)

            try:
                change_service("stop")
            except CommandError as error:
                raise _Retryable("service") from error
            changed = True
            target, databases = _converge_database(inputs, source, target, databases)
            changed = True

            state = _observe_metadata(inputs)
            _validate_history_baseline(state, target)
            if state.selected_release_id != source.source_release_id:
                try:
                    select_current(inputs.paths, source.source_release_id)
                except (OSError, SelectionAmbiguityError, ValueError) as error:
                    raise _Retryable("selection") from error
                changed = True
                state = _observe_metadata(inputs)
            if state.selected_release_id != source.source_release_id:
                raise RestoreManual("restore selection did not retain the source release")

            try:
                change_service("start")
            except CommandError as error:
                raise _Retryable("service") from error
            try:
                verification = verify(
                    verification_request(request, source.source_release_id, inputs.verification),
                    lifecycle_locked=True,
                )
            except CommandError as error:
                raise _Retryable("restore") from error
            report_value = verification.state.get("report")
            report = report_value if isinstance(report_value, Mapping) else None
            if verification.outcome != "succeeded":
                raise _Retryable("verification")

            recovery_ids = tuple(sorted({target.backup_id, *(str(item["backup_id"]) for item in target.safety_backup_attempts)}))
            try:
                _record, published = complete_successful_selection(
                    inputs.paths,
                    state,
                    release_id=source.source_release_id,
                    observed_previous_release_id=target.observed_previous_release_id,
                    backup_id=target.safety_backup_id,
                    recovery_backup_ids=recovery_ids,
                )
            except (OSError, RecordError, ValueError) as error:
                raise _Retryable("history") from error
            changed = changed or published
            state, databases = _observe_locked(inputs)
            if not _durable_success(state, databases, target, source):
                raise RestoreManual("successful restore history is not authoritative")
            changed = _cleanup_completed(inputs, state, databases, target) or changed
            state, databases = _observe_locked(inputs, include_runtime=True)
    except LifecycleLockContention:
        return _failure(request, "retryable", "lifecycle lock is unavailable", "lock", inputs, source, state, changed=False, possibly_changed=False, report=report, safety=safety)
    except _Retryable as error:
        return _failure(request, "retryable", "restore did not complete; rerun to converge", error.boundary, inputs, source, state, changed=changed, possibly_changed=error.possibly_changed, report=report, safety=safety)
    except RestoreRefused:
        return _failure(request, "refused", "restore request is unsafe", "input", inputs, source, state, changed=changed, possibly_changed=possibly_changed, report=report, safety=safety)
    except (RestoreManual, RestoreDatabaseError, StateAmbiguityError):
        return _failure(request, "manual", "restore authority is contradictory", "restore", inputs, source, state, changed=changed, possibly_changed=possibly_changed, report=report, safety=safety)
    except (PathAuthorityError, RecordError, TypeError, ValueError):
        return _failure(request, "refused", "restore request is unsafe", "input", inputs, source, state, changed=changed, possibly_changed=possibly_changed, report=report, safety=safety)
    except (BackupAuthorityError, CommandError, OSError):
        return _failure(request, "retryable", "restore observation did not complete", "restore", inputs, source, state, changed=changed, possibly_changed=True, report=report, safety=safety)

    return _result(request, "succeeded", "restore converged", state, inputs, source, changed=changed, report=report, databases=databases, pre_restore_backup_id=None if safety is None else safety.backup_id)


def _inputs(request: HostRequest) -> _Inputs:
    if not isinstance(request, HostRequest) or request.operation != "restore":
        raise RestoreRefused("invalid restore operation")
    if set(request.expected_state) != _EXPECTED_STATE_KEYS or set(request.parameters) != _PARAMETER_KEYS:
        raise RestoreRefused("restore request fields are incomplete")
    replace_unfinished = request.parameters["replace_unfinished"]
    reapply = request.parameters["reapply"]
    if type(replace_unfinished) is not bool or type(reapply) is not bool or replace_unfinished and reapply:
        raise RestoreRefused("restore mode flags are invalid")
    if replace_unfinished:
        raise RestoreRefused("unfinished restore replacement is not enabled")
    backup_id = request.parameters["backup_id"]
    if type(backup_id) is not str or _BACKUP_RE.fullmatch(backup_id) is None or request.expected_state["backup_id"] != backup_id:
        raise RestoreRefused("restore backup authority is invalid")
    selected = request.expected_state["selected_release_id"]
    if selected is not None:
        from taskman_ops.releases.identifiers import validate_release_id
        validate_release_id(selected)
    selection = request.expected_state["last_successful_selection_id"]
    if selection is not None and (type(selection) is not str or _SELECTION_RE.fullmatch(selection) is None):
        raise RestoreRefused("restore successful selection authority is invalid")
    for key in ("backup_protection_sha256", "restore_target_sha256"):
        value = request.expected_state[key]
        if value is not None and (type(value) is not str or _SHA256_RE.fullmatch(value) is None):
            raise RestoreRefused("restore digest authority is invalid")
    scheduler = request.expected_state["scheduled_backup_sha256"]
    if scheduler is not None and (type(scheduler) is not str or _SHA256_RE.fullmatch(scheduler) is None):
        raise RestoreRefused("restore scheduler authority is invalid")
    if type(request.expected_state["backup_timer_enabled"]) is not bool:
        raise RestoreRefused("restore timer authority is invalid")
    expected_databases = validate_restore_database_state(request.expected_state["restore_database_state"])
    canonical = expected_databases["canonical"]
    expected_migrations = request.expected_state["applied_migrations"]
    canonical_migrations = None if canonical is None or not canonical["migration_table_present"] else canonical["applied_migrations"]
    if expected_migrations != canonical_migrations:
        raise RestoreRefused("restore canonical migrations are inconsistent")
    credentials = request.parameters["credentials_path"]
    if type(credentials) is not str or not Path(credentials).is_absolute():
        raise RestoreRefused("restore credentials path is invalid")
    verification = request.parameters["verification"]
    helper = request.parameters["backup_helper"]
    prune = request.parameters["prune_backup_ids"]
    if not isinstance(verification, Mapping):
        raise RestoreRefused("restore verification settings are invalid")
    if not isinstance(helper, Mapping) or set(helper) != {"sha256", "upload_path"} or type(helper["sha256"]) is not str or _SHA256_RE.fullmatch(helper["sha256"]) is None or helper["upload_path"] is not None and type(helper["upload_path"]) is not str:
        raise RestoreRefused("restore backup helper authority is invalid")
    if not isinstance(prune, (tuple, list)):
        raise RestoreRefused("restore pruning authority is invalid")
    prune_ids = tuple(prune)
    if prune_ids != tuple(sorted(set(prune_ids))) or any(type(item) is not str or _BACKUP_RE.fullmatch(item) is None for item in prune_ids):
        raise RestoreRefused("restore pruning authority is invalid")
    if prune_ids:
        raise RestoreRefused("restore safety-attempt pruning requires replacement execution")
    return _Inputs(
        ManagedPaths.from_mapping(request.paths), dict(request.expected_state), backup_id,
        Path(credentials), database_mapping(request.parameters["database"]), dict(verification),
        dict(helper), prune_ids, replace_unfinished, reapply,
    )


def _observe_metadata(inputs: _Inputs, *, include_runtime: bool = False) -> HostState:
    return observe_host_state(
        inputs.paths,
        include_runtime=include_runtime,
        allow_selection_transition=True,
    )


def _observe_locked(inputs: _Inputs, *, include_runtime: bool = False) -> tuple[HostState, dict[str, object]]:
    databases = validate_restore_database_state(observe_restore_databases(inputs.database, inputs.credentials))
    canonical = databases["canonical"]
    if canonical is None:
        database_fact = {"state": "absent", "applied_migrations": ()}
    elif canonical["migration_table_present"]:
        database_fact = {"state": "ready", "applied_migrations": canonical["applied_migrations"]}
    else:
        database_fact = {"state": "ready", "applied_migrations": ()}
    state = observe_host_state(
        inputs.paths,
        database=database_fact,
        include_runtime=include_runtime,
        allow_selection_transition=True,
    )
    return state, databases


def _validate_expected_state(state: HostState, databases: Mapping[str, object], inputs: _Inputs) -> None:
    protections = tuple(sorted((*state.backup_protections, *state.retiring_backup_protections), key=lambda item: item.backup_id))
    protection_digest = hashlib.sha256(json.dumps([item.to_mapping() for item in protections], ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    scheduler = _scheduler_facts(inputs.paths)
    expected = inputs.expected_state
    if (
        state.selected_release_id != expected["selected_release_id"]
        or state.latest_successful_selection_filename != expected["last_successful_selection_id"]
        or protection_digest != expected["backup_protection_sha256"]
        or scheduler["scheduled_backup_sha256"] != expected["scheduled_backup_sha256"]
        or scheduler["backup_timer_enabled"] != expected["backup_timer_enabled"]
        or (None if state.restore_target is None else restore_target_sha256(state.restore_target)) != expected["restore_target_sha256"]
        or databases != expected["restore_database_state"]
    ):
        raise RestoreManual("confirmed restore state changed")
    canonical = databases["canonical"]
    migrations = None if canonical is None or not canonical["migration_table_present"] else canonical["applied_migrations"]
    if migrations != expected["applied_migrations"]:
        raise RestoreManual("confirmed canonical schema changed")


def _revalidate_before_consequences(inputs: _Inputs) -> None:
    state, databases = _observe_locked(inputs)
    _validate_expected_state(state, databases, inputs)


def _source_record(state: HostState, inputs: _Inputs) -> BackupRecord:
    source = next((item for item in state.backups if item.backup_id == inputs.backup_id), None)
    if source is None:
        raise RestoreRefused("selected backup is unavailable")
    release = next((item for item in state.releases if item.release_id == source.source_release_id), None)
    if release is None:
        raise RestoreManual("backup source release is unavailable")
    if release_migration_versions(release.migrations) != source.migration_versions:
        raise RestoreManual("backup source migrations are contradictory")
    return source


def _validate_source_dump(source: BackupRecord, inputs: _Inputs) -> None:
    dump = Path(inputs.paths.local(inputs.paths.backup_root / f"{source.backup_id}.dump"))
    try:
        details = dump.lstat()
    except OSError as error:
        raise RestoreManual("restore dump is unavailable") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid() or details.st_size <= 0 or details.st_mode & 0o7022 or sha256_file(dump) != source.dump_sha256:
        raise RestoreManual("restore dump identity is contradictory")
    if stat.S_IMODE(details.st_mode) != 0o600:
        os.chmod(dump, 0o600)
    run_command(("pg_restore", "--list", dump.as_posix()), env={"PGPASSFILE": inputs.credentials.as_posix()}, timeout_seconds=_COMMAND_TIMEOUT_SECONDS)


def _required_backup(state: HostState, backup_id: str, inputs: _Inputs) -> BackupRecord:
    record = next((item for item in state.backups if item.backup_id == backup_id), None)
    if record is None:
        raise RestoreManual("required restore recovery backup is unavailable")
    _validate_completed_dump(record, inputs)
    return record


def _validate_completed_dump(record: BackupRecord, inputs: _Inputs) -> None:
    dump = Path(inputs.paths.local(inputs.paths.backup_root / f"{record.backup_id}.dump"))
    try:
        details = dump.lstat()
    except OSError as error:
        raise RestoreManual("required restore recovery dump is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_size <= 0
        or sha256_file(dump) != record.dump_sha256
    ):
        raise RestoreManual("required restore recovery dump identity changed")
    try:
        run_command(
            ("pg_restore", "--list", dump.as_posix()),
            env={"PGPASSFILE": inputs.credentials.as_posix()},
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
        )
    except CommandError as error:
        raise RestoreManual("required restore recovery dump cannot be validated") from error


def _state_for_database(state: HostState, database: object) -> HostState:
    if not isinstance(database, Mapping) or not database["migration_table_present"]:
        raise RestoreManual("original database schema is unavailable")
    return replace(state, applied_migrations=database["applied_migrations"], database_state="ready")


def _roles(databases: Mapping[str, object]) -> frozenset[str]:
    return frozenset(key for key, value in databases.items() if value is not None)


def _require_initial_arrangement(databases: Mapping[str, object]) -> None:
    if _roles(databases) != frozenset({"canonical"}):
        raise RestoreManual("unbound restore databases are not canonical-only")


def _validate_bound_target(
    state: HostState,
    databases: Mapping[str, object],
    target: RestoreTarget,
    source: BackupRecord,
    inputs: _Inputs,
) -> None:
    if target.replacement is not None:
        raise RestoreRefused("unfinished replacement requires replacement execution")
    if (target.backup_id, target.dump_sha256, target.source_release_id) != (source.backup_id, source.dump_sha256, source.source_release_id):
        raise RestoreRefused("requested backup does not match the unfinished restore")
    if target.base_selection_id != state.latest_successful_selection_filename:
        raise RestoreManual("restore history baseline changed")
    roles = _roles(databases)
    recognized = {
        frozenset({"canonical"}),
        frozenset({"canonical", "temporary"}),
        frozenset({"temporary", "retired"}),
        frozenset({"retired"}),
        frozenset({"canonical", "retired"}),
    }
    if roles not in recognized:
        raise RestoreManual("restore database arrangement is not recognized")
    for value in databases.values():
        if isinstance(value, Mapping) and value["owner"] != inputs.database["role"]:
            raise RestoreManual("restore database owner changed")
    canonical = databases["canonical"]
    retired = databases["retired"]
    original = retired if retired is not None else canonical
    if not isinstance(original, Mapping) or original["oid"] != target.original_database_oid:
        # After the swap, canonical is the restored OID and retired must be
        # the original until durable success permits cleanup.
        if not (
            isinstance(retired, Mapping)
            and retired["oid"] == target.original_database_oid
            and isinstance(canonical, Mapping)
            and canonical["oid"] == target.restored_database_oid
        ):
            raise RestoreManual("bound original database identity changed")
    temporary = databases["temporary"]
    if temporary is not None and not target.temporary_creation_pending and temporary["oid"] != target.restored_database_oid:
        raise RestoreManual("registered temporary database identity changed")


def _validate_history_baseline(state: HostState, target: RestoreTarget) -> None:
    if state.latest_successful_selection_filename != target.base_selection_id:
        raise RestoreManual("restore history changed before success publication")


def _converge_database(
    inputs: _Inputs,
    source: BackupRecord,
    target: RestoreTarget,
    databases: dict[str, object],
) -> tuple[RestoreTarget, dict[str, object]]:
    roles = _roles(databases)
    if roles == frozenset({"canonical", "retired"}):
        canonical = databases["canonical"]
        retired = databases["retired"]
        if not isinstance(canonical, Mapping) or canonical["oid"] != target.restored_database_oid or not isinstance(retired, Mapping) or retired["oid"] != target.original_database_oid:
            raise RestoreManual("completed restore swap identity changed")
        _validate_restored(canonical, source, target)
        return target, databases

    old_oid = target.restored_database_oid
    temporary = databases["temporary"]
    if not target.temporary_creation_pending:
        target = begin_temporary_rebuild(inputs.paths, target)
    if old_oid is not None:
        if isinstance(temporary, Mapping) and temporary["oid"] == old_oid:
            drop_registered_temporary(inputs.database, inputs.credentials, old_oid)
            databases = validate_restore_database_state(observe_restore_databases(inputs.database, inputs.credentials))
            temporary = databases["temporary"]
        if any(isinstance(value, Mapping) and value["oid"] == old_oid for value in databases.values()):
            raise RestoreManual("previous restored database OID is still present")
    if temporary is None:
        if available_bytes(_POSTGRES_DATA) < source.source_database_size_bytes * 2:
            raise RestoreRefused("restore capacity is insufficient")
        create_temporary_database(inputs.database, inputs.credentials)
    target = register_restored_database(inputs.paths, target, inputs.database, inputs.credentials)
    dump = Path(inputs.paths.local(inputs.paths.backup_root / f"{source.backup_id}.dump"))
    load_registered_temporary(target, inputs.database, inputs.credentials, dump)
    databases = validate_restore_database_state(observe_restore_databases(inputs.database, inputs.credentials))
    temporary = databases["temporary"]
    _validate_restored(temporary, source, target)

    canonical = databases["canonical"]
    retired = databases["retired"]
    if isinstance(canonical, Mapping):
        if canonical["oid"] != target.original_database_oid or retired is not None:
            raise RestoreManual("original canonical identity changed before swap")
        _terminate_connections(inputs)
        rename_registered_database(inputs.database, inputs.credentials, "canonical", "retired", target.original_database_oid)
    elif not isinstance(retired, Mapping) or retired["oid"] != target.original_database_oid:
        raise RestoreManual("retired original identity changed before promotion")
    rename_registered_database(inputs.database, inputs.credentials, "temporary", "canonical", int(target.restored_database_oid))
    databases = validate_restore_database_state(observe_restore_databases(inputs.database, inputs.credentials))
    _validate_restored(databases["canonical"], source, target)
    return target, databases


def _validate_restored(database: object, source: BackupRecord, target: RestoreTarget) -> None:
    if (
        not isinstance(database, Mapping)
        or database["oid"] != target.restored_database_oid
        or database["migration_table_present"] is not True
        or database["applied_migrations"] != source.migration_versions
    ):
        raise RestoreManual("restored database validation failed")


def _terminate_connections(inputs: _Inputs) -> None:
    names = (str(inputs.database["name"]), f"{inputs.database['name']}__restore_tmp")
    variables = tuple(item for index, name in enumerate(names) for item in ("--set", f"database_{index}={name}"))
    run_command(
        (
            "runuser", "-u", "postgres", "--", "psql", "--no-psqlrc", "--host", "/var/run/postgresql",
            "--port", str(inputs.database["port"]), "--username", "postgres", "--dbname", "postgres",
            "--no-password", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1", *variables,
            "--command", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN (:'database_0', :'database_1') AND pid <> pg_backend_pid()",
        ),
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )


def _durable_success(
    state: HostState,
    databases: Mapping[str, object],
    target: RestoreTarget,
    source: BackupRecord,
) -> bool:
    latest = state.latest_successful_selection
    if latest is None:
        return False
    required = {target.backup_id, *(str(item["backup_id"]) for item in target.safety_backup_attempts)}
    canonical = databases["canonical"]
    return (
        latest.release_id == source.source_release_id
        and latest.backup_id == target.safety_backup_id
        and latest.observed_previous_release_id == target.observed_previous_release_id
        and required.issubset(set(latest.recovery_backup_ids))
        and all(item.backup_id in latest.recovery_backup_ids for item in (*state.backup_protections, *state.retiring_backup_protections))
        and isinstance(canonical, Mapping)
        and canonical["oid"] == target.restored_database_oid
    )


def _completed_without_binding(state: HostState, databases: Mapping[str, object], source: BackupRecord) -> bool:
    latest = state.latest_successful_selection
    canonical = databases["canonical"]
    return (
        latest is not None
        and latest.release_id == source.source_release_id
        and source.backup_id in latest.recovery_backup_ids
        and state.selected_release_id == source.source_release_id
        and _roles(databases) == frozenset({"canonical"})
        and isinstance(canonical, Mapping)
        and canonical["migration_table_present"] is True
        and canonical["applied_migrations"] == source.migration_versions
    )


def _cleanup_completed(
    inputs: _Inputs,
    state: HostState,
    databases: Mapping[str, object],
    target: RestoreTarget,
) -> bool:
    latest = state.latest_successful_selection
    if latest is None:
        raise RestoreManual("restore success is unavailable")
    required = tuple(sorted({target.backup_id, *(str(item["backup_id"]) for item in target.safety_backup_attempts)}))
    try:
        _record, _protection_changed = complete_successful_selection(
            inputs.paths,
            state,
            release_id=target.source_release_id,
            observed_previous_release_id=target.observed_previous_release_id,
            backup_id=target.safety_backup_id,
            recovery_backup_ids=required,
        )
    except (OSError, RecordError, ValueError) as error:
        raise _Retryable("history") from error
    retired = databases["retired"]
    if retired is not None:
        try:
            drop_registered_retired(inputs.database, inputs.credentials, target.original_database_oid)
        except RestoreDatabaseError as error:
            raise _Retryable("restore") from error
    try:
        remove_restore_target(inputs.paths)
    except (OSError, RecordError) as error:
        raise _Retryable("restore") from error
    return True


def _failure(
    request: HostRequest,
    outcome: str,
    message: str,
    boundary: str,
    inputs: _Inputs | None,
    source: BackupRecord | None,
    state: HostState | None,
    *,
    changed: bool,
    possibly_changed: bool,
    report: Mapping[str, object] | None,
    safety: BackupRecord | None,
) -> HostResult:
    databases: Mapping[str, object] | None = None
    final_state = state
    if inputs is not None:
        try:
            final_state, databases = _observe_locked(inputs, include_runtime=True)
        except Exception:
            pass
    return _result(
        request, outcome, message, final_state, inputs, source,
        boundary=boundary, changed=changed, possibly_changed=possibly_changed,
        report=report, databases=databases,
        pre_restore_backup_id=(
            safety.backup_id
            if safety is not None
            else None if state is None or state.restore_target is None else state.restore_target.safety_backup_id
        ),
    )


def _result(
    request: HostRequest,
    outcome: str,
    message: str,
    state: HostState | None,
    inputs: _Inputs | None,
    source: BackupRecord | None,
    *,
    boundary: str | None = None,
    changed: bool,
    possibly_changed: bool = False,
    report: Mapping[str, object] | None,
    databases: Mapping[str, object] | None,
    pre_restore_backup_id: str | None,
) -> HostResult:
    if state is not None and inputs is not None and databases is not None:
        try:
            scheduler = _scheduler_facts(inputs.paths)
            observations = mutation_observations(
                state,
                "restore",
                scheduler=scheduler,
                restore_database_state=databases,
            )
            unavailable, inspection_error = mutation_observation_availability("restore", observations)
        except Exception:
            observations, unavailable = unavailable_observations("restore")
            inspection_error = "inspection-failed"
    else:
        observations, unavailable = unavailable_observations("restore")
        inspection_error = "inspection-failed"
    if outcome == "succeeded":
        exit_code = 0
        failed_boundary = None
    else:
        failed_boundary = boundary or "restore"
        exit_code = {
            "input": 2,
            "lock": 12,
            "backup_helper": 8,
            "backup": 6,
            "verification": 9,
            "history": 11,
            "selection": 11,
            "service": 11,
            "restore": 11,
        }.get(failed_boundary, 10)
    mutation_state = "changed" if changed else ("unknown" if possibly_changed else "unchanged")
    facts = {
        "mutation_state": mutation_state,
        "exit_code": exit_code,
        "failed_boundary": failed_boundary,
        "observations": observations,
        "unavailable_fields": unavailable,
        "inspection_error": inspection_error,
        "report": None if report is None else dict(report),
        "desired_release_id": None if source is None else source.source_release_id,
        "backup_id": None if source is None else source.backup_id,
        "pre_restore_backup_id": pre_restore_backup_id,
    }
    return HostResult(PROTOCOL_VERSION, request.operation, request.correlation_id, outcome, message, facts, () if state is None else state.warnings)


__all__ = ["restore"]
