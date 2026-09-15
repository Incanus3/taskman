"""Coherent observation of completed host records and physical authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sqlite3
import time

from ..migrations import MigrationOrderError, validate_migration_versions
from taskman_ops.releases.identifiers import RELEASE_ID_RE, validate_release_id
from .backup_protection import BackupProtection, backup_protection_retirement_root
from .paths import ManagedPaths, PathAuthorityError
from .records import (
    MAX_RECORD_BYTES,
    MAX_RELEASE_RECORD_BYTES,
    BACKUP_ID_RE,
    BackupRecord,
    RecordError,
    ReleaseRecord,
    SelectionRecord,
    selection_filename,
)
from .restore_target import RestoreTarget, restore_target_sha256


MAX_WARNINGS = 64
MAX_TEMPORARY_PATHS = 64
MAX_INVENTORY_ENTRIES = 4096
_STATE_LOADING_TIMEOUT_SECONDS = 60.0
_RELEASE_TEMP_RE = re.compile(
    rf"\.release-{RELEASE_ID_RE.pattern.removesuffix(r'\Z')}\.tmp\Z"
)
_RELEASE_MANIFEST_TEMP_RE = re.compile(
    r"\.\.taskman-release\.json\.[0-9]+\.(?:[0-9]|[1-9][0-9])\.tmp\Z"
)
_BACKUP_TEMP_RE = re.compile(
    r"(?:\.backup-[0-9a-f]{32}\.dump\.tmp|"
    r"\.backup-[0-9a-f]{32}\.json\.[0-9]+\.(?:[0-9]|[1-9][0-9])\.tmp)\Z"
)
_SELECTION_FILE_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")
_SELECTION_TEMP_RE = re.compile(
    r"\.selection-[0-9a-f]{64}\.json\.[0-9]+\.(?:[0-9]|[1-9][0-9])\.tmp\Z"
)
_UNSUPPORTED_RELEASE_RE = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?-"
    r"[0-9a-f]{12}-ubuntu26\.04-amd64-otp[0-9A-Za-z.-]+"
    r"(?:-[0-9a-f]{64}(?:-dirty)?)?\Z"
)
_BACKUP_PROTECTION_FILE_RE = re.compile(r"backup-[0-9a-f]{32}\.json\Z")
_BACKUP_PROTECTION_TEMP_RE = re.compile(
    r"\.backup-[0-9a-f]{32}\.json\.[0-9]+\.(?:[0-9]|[1-9][0-9])\.tmp\Z"
)
_RESTORE_TARGET_TEMP_RE = re.compile(
    r"\.restore-target\.json\.[0-9]+\.(?:[0-9]|[1-9][0-9])\.tmp\Z"
)


class StateAmbiguityError(ValueError):
    """Authoritative host facts contradict or cannot prove one state."""


@dataclass(frozen=True)
class HostState:
    """The finite host facts consumed by later helper commands."""

    selected_release_id: str | None
    releases: tuple[ReleaseRecord, ...]
    backups: tuple[BackupRecord, ...]
    selections: tuple[SelectionRecord, ...]
    applied_migrations: tuple[int, ...]
    service_state: str
    database_state: str
    temporary_paths: tuple[PurePosixPath, ...]
    warnings: tuple[str, ...]
    backup_protections: tuple[BackupProtection, ...] = ()
    restore_target: RestoreTarget | None = None
    selection_filenames: tuple[str, ...] = ()
    successful_backup_ids: frozenset[str] = frozenset()
    retiring_backup_protections: tuple[BackupProtection, ...] = ()
    initial_database_empty: bool = False

    def __post_init__(self) -> None:
        if self.selected_release_id is not None and not isinstance(self.selected_release_id, str):
            raise StateAmbiguityError("invalid selected release")
        if self.service_state not in {"running", "stopped", "failed", "unknown"}:
            raise StateAmbiguityError("invalid service state")
        if self.database_state not in {"ready", "absent", "unknown"}:
            raise StateAmbiguityError("invalid database state")
        if not isinstance(self.releases, tuple) or not all(isinstance(item, ReleaseRecord) for item in self.releases):
            raise StateAmbiguityError("invalid release state")
        if not isinstance(self.backups, tuple) or not all(isinstance(item, BackupRecord) for item in self.backups):
            raise StateAmbiguityError("invalid backup state")
        if not isinstance(self.selections, tuple) or not all(isinstance(item, SelectionRecord) for item in self.selections):
            raise StateAmbiguityError("invalid selection state")
        if not isinstance(self.applied_migrations, tuple) or any(
            type(item) is not int or item < 0 for item in self.applied_migrations
        ):
            raise StateAmbiguityError("invalid applied migrations")
        if self.applied_migrations != tuple(sorted(set(self.applied_migrations))):
            raise StateAmbiguityError("applied migrations are not sorted and unique")
        if not isinstance(self.temporary_paths, tuple) or not all(
            isinstance(item, PurePosixPath) for item in self.temporary_paths
        ):
            raise StateAmbiguityError("invalid temporary paths")
        if not isinstance(self.warnings, tuple) or not all(isinstance(item, str) for item in self.warnings):
            raise StateAmbiguityError("invalid state warnings")
        if not isinstance(self.backup_protections, tuple) or not all(
            isinstance(item, BackupProtection) for item in self.backup_protections
        ):
            raise StateAmbiguityError("invalid backup protection state")
        if self.restore_target is not None and not isinstance(self.restore_target, RestoreTarget):
            raise StateAmbiguityError("invalid restore target state")
        if not isinstance(self.retiring_backup_protections, tuple) or not all(
            isinstance(item, BackupProtection) for item in self.retiring_backup_protections
        ):
            raise StateAmbiguityError("invalid retiring backup protection state")
        if not self.selection_filenames and self.selections:
            object.__setattr__(
                self,
                "selection_filenames",
                tuple(selection_filename(item) for item in self.selections),
            )
        if (
            not isinstance(self.selection_filenames, tuple)
            or len(self.selection_filenames) != len(self.selections)
            or not all(_SELECTION_FILE_RE.fullmatch(item) for item in self.selection_filenames)
        ):
            raise StateAmbiguityError("invalid successful selection filenames")
        if not self.successful_backup_ids and self.selections:
            object.__setattr__(
                self,
                "successful_backup_ids",
                frozenset(
                    backup_id
                    for selection in self.selections
                    for backup_id in (
                        *((selection.backup_id,) if selection.backup_id is not None else ()),
                        *selection.recovery_backup_ids,
                    )
                ),
            )
        if not isinstance(self.successful_backup_ids, frozenset) or not all(
            type(item) is str and BACKUP_ID_RE.fullmatch(item) for item in self.successful_backup_ids
        ):
            raise StateAmbiguityError("invalid successful backup references")
        if type(self.initial_database_empty) is not bool:
            raise StateAmbiguityError("invalid initial database evidence")

    @property
    def latest_successful_selection(self) -> SelectionRecord | None:
        return self.selections[-1] if self.selections else None

    @property
    def previous_successful_selection(self) -> SelectionRecord | None:
        return self.selections[-2] if len(self.selections) > 1 else None

    @property
    def latest_successful_selection_filename(self) -> str | None:
        return self.selection_filenames[-1] if self.selection_filenames else None

    @property
    def previous_successful_selection_filename(self) -> str | None:
        return self.selection_filenames[-2] if len(self.selection_filenames) > 1 else None

    def to_mapping(self) -> dict[str, object]:
        return {
            "selected_release_id": self.selected_release_id,
            "releases": [item.to_mapping() for item in self.releases],
            "backups": [item.to_mapping() for item in self.backups],
            "selections": [item.to_mapping() for item in self.selections],
            "applied_migrations": list(self.applied_migrations),
            "service_state": self.service_state,
            "database_state": self.database_state,
            "temporary_paths": [item.as_posix() for item in self.temporary_paths],
            "warnings": list(self.warnings),
            "backup_protections": [item.to_mapping() for item in self.backup_protections],
            "restore_target": None if self.restore_target is None else self.restore_target.to_mapping(),
        }


def mutation_observations(
    state: HostState,
    operation: str,
    *,
    scheduler: Mapping[str, object] | None = None,
    restore_database_state: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Project one post-mutation locked snapshot into the exact wire facts."""

    if not isinstance(state, HostState) or operation not in {
        "deploy",
        "genesis",
        "restore",
        "cleanup",
    }:
        raise ValueError("mutation observation request is invalid")
    protections = tuple(
        sorted(
            (*state.backup_protections, *state.retiring_backup_protections),
            key=lambda item: item.backup_id,
        )
    )
    protection_rows = tuple(item.to_mapping() for item in protections)
    observations: dict[str, object] = {
        "selected_release_id": state.selected_release_id,
        "last_successful_selection_id": state.latest_successful_selection_filename,
        "backup_protection_sha256": hashlib.sha256(
            json.dumps(
                protection_rows,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest(),
        "restore_target_sha256": (
            None
            if state.restore_target is None
            else restore_target_sha256(state.restore_target)
        ),
    }
    if operation == "cleanup":
        return observations
    if not isinstance(scheduler, Mapping) or set(scheduler) != {
        "scheduled_backup_sha256",
        "backup_timer_enabled",
        "backup_timer_state",
    }:
        raise ValueError("mutation scheduler observation is invalid")
    protected = {item.backup_id for item in protections}
    if state.restore_target is not None:
        protected.update(
            str(item["backup_id"])
            for item in state.restore_target.safety_backup_attempts
        )
        protected.update(
            {
                state.restore_target.backup_id,
                state.restore_target.safety_backup_id,
            }
        )
        if state.restore_target.replacement is not None:
            protected.add(str(state.restore_target.replacement["backup_id"]))
    observations.update(
        {
            "applied_migrations": state.applied_migrations,
            "protected_backup_ids": tuple(sorted(protected)),
            "database_state": state.database_state,
            "service_state": state.service_state,
            **scheduler,
        }
    )
    if operation == "restore":
        observations["restore_database_state"] = restore_database_state
        canonical = (
            None
            if not isinstance(restore_database_state, Mapping)
            else restore_database_state.get("canonical")
        )
        observations["applied_migrations"] = (
            None
            if not isinstance(canonical, Mapping)
            or canonical.get("migration_table_present") is not True
            else canonical.get("applied_migrations")
        )
    return observations


def mutation_observation_availability(
    operation: str,
    observations: Mapping[str, object],
) -> tuple[tuple[str, ...], str | None]:
    """Describe only domains that the final locked observation could not prove."""

    if operation not in {"deploy", "genesis", "restore", "cleanup"}:
        raise ValueError("mutation observation operation is invalid")
    unavailable = {
        field
        for field in ("database_state", "service_state", "backup_timer_state")
        if observations.get(field) == "unknown"
    }
    unavailable.update(
        field
        for field in (
            "applied_migrations",
            "protected_backup_ids",
            "backup_protection_sha256",
            "backup_timer_enabled",
            "restore_database_state",
        )
        if field in observations and observations[field] is None
    )
    if (
        "scheduled_backup_sha256" in observations
        and observations["scheduled_backup_sha256"] is None
        and (
            observations.get("backup_timer_enabled") is None
            or observations.get("backup_timer_state") == "unknown"
        )
    ):
        unavailable.add("scheduled_backup_sha256")
    result = tuple(sorted(unavailable))
    return result, "unsafe-observation" if result else None


@dataclass
class _SuccessfulHistory:
    """Disk-backed complete history with only bounded facts retained in memory."""

    database: sqlite3.Connection
    projection: tuple[SelectionRecord, ...] = ()
    projection_filenames: tuple[str, ...] = ()
    successful_backup_ids: frozenset[str] = frozenset()

    def close(self) -> None:
        self.database.close()

    def ordered_rows(self, deadline: float):
        _require_history_deadline(deadline)
        cursor = self.database.execute(
            "SELECT filename, payload FROM selections ORDER BY selected_at, filename"
        )
        for filename, payload in cursor:
            _require_history_deadline(deadline)
            yield str(filename), SelectionRecord.from_mapping(json.loads(str(payload)))
        _require_history_deadline(deadline)

    def record(self, filename: str, deadline: float) -> SelectionRecord | None:
        _require_history_deadline(deadline)
        row = self.database.execute(
            "SELECT payload FROM selections WHERE filename = ?", (filename,)
        ).fetchone()
        _require_history_deadline(deadline)
        return None if row is None else SelectionRecord.from_mapping(json.loads(str(row[0])))

    def successor(self, filename: str, deadline: float) -> SelectionRecord | None:
        _require_history_deadline(deadline)
        row = self.database.execute(
            """
            SELECT successor.payload
            FROM selections AS baseline
            JOIN selections AS successor ON successor.selected_at > baseline.selected_at
            WHERE baseline.filename = ?
            ORDER BY successor.selected_at, successor.filename
            LIMIT 1
            """,
            (filename,),
        ).fetchone()
        _require_history_deadline(deadline)
        return None if row is None else SelectionRecord.from_mapping(json.loads(str(row[0])))

    def first(self, deadline: float) -> SelectionRecord | None:
        _require_history_deadline(deadline)
        row = self.database.execute(
            "SELECT payload FROM selections ORDER BY selected_at, filename LIMIT 1"
        ).fetchone()
        _require_history_deadline(deadline)
        return None if row is None else SelectionRecord.from_mapping(json.loads(str(row[0])))


def observe_host_state(
    paths: ManagedPaths,
    *,
    database: Mapping[str, object] | None = None,
    include_runtime: bool = False,
    allow_selection_transition: bool = False,
    deadline: float | None = None,
) -> HostState:
    """Observe completed host records, optionally exposing one deploy transition.

    Ordinary callers reject any difference between ``current`` and durable
    selection history.  Deploy uses the narrowly scoped transition view while
    holding the lifecycle lock so it can finish a record publication lost
    immediately after its atomic current-link replacement.
    """

    if not isinstance(paths, ManagedPaths):
        raise TypeError("host-state observation needs managed paths")
    if deadline is None:
        deadline = time.monotonic() + _STATE_LOADING_TIMEOUT_SECONDS
    if type(deadline) not in {int, float}:
        raise TypeError("host-state deadline is invalid")
    owner_uid = os.geteuid()
    try:
        paths.validate_existing(owner_uid=owner_uid)
    except PathAuthorityError as error:
        raise StateAmbiguityError(str(error)) from error

    temporary: list[PurePosixPath] = []
    warnings: list[str] = []
    install_root = Path(paths.local(paths.install_root))
    release_root = Path(paths.local(paths.release_root))
    backup_root = Path(paths.local(paths.backup_root))
    selection_root = Path(paths.local(paths.selection_root))

    _check_or_note_directory(install_root, owner_uid, "install root")
    deployment_root = Path(paths.local(paths.deployment_root))
    _note_unknown_deployment_entries(deployment_root, owner_uid, warnings, temporary)
    releases = _read_releases(release_root, owner_uid, temporary, warnings)
    backups = _read_backups(backup_root, owner_uid, temporary, warnings)
    release_by_id = {item.release_id: item for item in releases}
    backup_by_id = {item.backup_id: item for item in backups}
    history = _read_selections(
        selection_root,
        owner_uid,
        warnings,
        temporary,
        release_by_id,
        backup_by_id,
        float(deadline),
    )
    try:
        backup_protection_root = Path(paths.local(paths.backup_protection_root))
        backup_protections = _read_backup_protections(
            backup_protection_root, owner_uid, temporary, warnings
        )
        retiring_backup_protections = _read_retiring_backup_protections(
            backup_protection_retirement_root(paths), owner_uid
        )
        retiring_dump_paths = {
            Path(paths.local(paths.backup_root / f"{item.backup_id}.dump"))
            for item in retiring_backup_protections
        }
        temporary[:] = [item for item in temporary if Path(item) not in retiring_dump_paths]
        restore_target_path = Path(paths.local(paths.restore_target_path))
        restore_target = _read_restore_target(restore_target_path, owner_uid)

        _require_history_deadline(float(deadline))
        _validate_backup_sources(backup_by_id, release_by_id, float(deadline))
        _validate_selection_history(history, release_by_id, backup_by_id, float(deadline))
        _validate_backup_protections(
            backup_protections,
            release_by_id,
            backup_by_id,
            history,
            float(deadline),
        )
        _validate_retiring_backup_protections(
            retiring_backup_protections,
            backup_protections,
            release_by_id,
            backup_by_id,
            history,
            float(deadline),
        )
        _validate_restore_target(
            restore_target,
            release_by_id,
            backup_by_id,
            history,
            float(deadline),
        )
        selected_from_history = history.projection[-1].release_id if history.projection else None
    except Exception:
        history.close()
        raise
    try:
        selected_from_link = _selected_link(paths, release_by_id)
        if selected_from_history != selected_from_link:
            if selected_from_history is None and selected_from_link is None:
                selected = None
            elif allow_selection_transition:
                selected = selected_from_link
            else:
                raise StateAmbiguityError("current selection contradicts completed selection history")
        else:
            selected = selected_from_history

        applied_migrations, database_state, initial_database_empty = _database_state(database)
        service_state = _service_state(include_runtime)
        temporary.sort(key=lambda item: item.as_posix())
        warnings = sorted(set(warnings))[:MAX_WARNINGS]
        return HostState(
            selected_release_id=selected,
            releases=tuple(sorted(releases, key=lambda item: item.release_id)),
            backups=tuple(sorted(backups, key=lambda item: item.backup_id)),
            selections=history.projection,
            applied_migrations=applied_migrations,
            service_state=service_state,
            database_state=database_state,
            temporary_paths=tuple(temporary[:MAX_TEMPORARY_PATHS]),
            warnings=tuple(warnings),
            backup_protections=tuple(sorted(backup_protections, key=lambda item: item.backup_id)),
            restore_target=restore_target,
            selection_filenames=history.projection_filenames,
            successful_backup_ids=history.successful_backup_ids,
            retiring_backup_protections=tuple(
                sorted(retiring_backup_protections, key=lambda item: item.backup_id)
            ),
            initial_database_empty=initial_database_empty,
        )
    finally:
        history.close()


def _check_or_note_directory(path: Path, owner_uid: int, label: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise StateAmbiguityError(f"unable to inspect {label}") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
    ):
        raise StateAmbiguityError(f"{label} is unsafe")


def _note_unknown_deployment_entries(
    root: Path,
    owner_uid: int,
    warnings: list[str],
    temporary: list[PurePosixPath],
) -> None:
    """Bound the non-authoritative deployment area without reading old records."""

    for entry in _entries(root, owner_uid, "deployment root"):
        if entry.name in {
            "selections",
            "backup-protections",
            "backup-protection-retirements",
            "restore-target.json",
        }:
            continue
        if _RESTORE_TARGET_TEMP_RE.fullmatch(entry.name):
            _validate_temporary(entry, owner_uid, "restore target temporary")
            temporary.append(PurePosixPath(entry.as_posix()))
            continue
        warnings.append(f"unknown deployment entry: {entry.name}")


def _read_releases(
    root: Path,
    owner_uid: int,
    temporary: list[PurePosixPath],
    warnings: list[str],
) -> list[ReleaseRecord]:
    entries = _entries(root, owner_uid, "release root")
    result: list[ReleaseRecord] = []
    seen: set[str] = set()
    for entry in entries:
        if _RELEASE_TEMP_RE.fullmatch(entry.name):
            # Deployment staging is a deterministic directory containing an
            # extracted immutable release.  It is recognizable on rerun and
            # belongs to the convergent deploy procedure, unlike the small
            # file temporaries written by completed-record publication.
            _validate_temporary(entry, owner_uid, "release temporary", directory=True)
            temporary.append(PurePosixPath(entry.as_posix()))
            continue
        if entry.name.startswith(".") and entry.name.endswith(".tmp"):
            warnings.append(f"unknown release temporary entry: {entry.name}")
            continue
        try:
            release_id = validate_release_id(entry.name)
        except (TypeError, ValueError):
            if _UNSUPPORTED_RELEASE_RE.fullmatch(entry.name) is not None:
                raise StateAmbiguityError("unsupported release authority")
            try:
                entry_details = entry.lstat()
                if stat.S_ISDIR(entry_details.st_mode):
                    manifest_details = (entry / ".taskman-release.json").lstat()
                    if stat.S_ISLNK(manifest_details.st_mode) or stat.S_ISREG(manifest_details.st_mode):
                        raise StateAmbiguityError("unsupported release authority")
            except FileNotFoundError:
                pass
            except StateAmbiguityError:
                raise
            except OSError as error:
                raise StateAmbiguityError("unable to inspect release authority") from error
            warnings.append(f"unknown release entry: {entry.name}")
            continue
        details = _lstat(entry, "release directory")
        if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise StateAmbiguityError("authoritative release path is not a directory")
        if details.st_uid != owner_uid or details.st_mode & 0o7022:
            raise StateAmbiguityError("authoritative release directory is unsafe")
        manifest = entry / ".taskman-release.json"
        if release_id in seen:
            raise StateAmbiguityError("duplicate release identity")
        seen.add(release_id)
        _read_release_manifest_temporaries(entry, owner_uid, temporary)
        result.append(
            _read_record(
                manifest,
                ReleaseRecord.from_mapping,
                "release manifest",
                owner_uid,
                maximum=MAX_RELEASE_RECORD_BYTES,
            )
        )
        if result[-1].release_id != release_id:
            raise StateAmbiguityError("release manifest identity conflicts with its path")
    return result


def _read_backups(
    root: Path,
    owner_uid: int,
    temporary: list[PurePosixPath],
    warnings: list[str],
) -> list[BackupRecord]:
    entries = _entries(root, owner_uid, "backup root")
    result: list[BackupRecord] = []
    seen: set[str] = set()
    for entry in entries:
        if _BACKUP_TEMP_RE.fullmatch(entry.name):
            _validate_temporary(entry, owner_uid, "backup temporary")
            temporary.append(PurePosixPath(entry.as_posix()))
            continue
        if entry.name.endswith(".dump"):
            identifier = entry.name[:-5]
            manifest = root / f"{identifier}.json"
            if BACKUP_ID_RE.fullmatch(identifier) is not None:
                try:
                    manifest.lstat()
                except FileNotFoundError:
                    _validate_temporary(entry, owner_uid, "incomplete backup dump")
                    temporary.append(PurePosixPath(entry.as_posix()))
                    continue
                except OSError as error:
                    raise StateAmbiguityError("unable to inspect backup manifest") from error
            if BACKUP_ID_RE.fullmatch(identifier) is None:
                warnings.append(f"unknown backup entry: {entry.name}")
            continue
        if entry.suffix != ".json":
            warnings.append(f"unknown backup entry: {entry.name}")
            continue
        if BACKUP_ID_RE.fullmatch(entry.stem) is None:
            warnings.append(f"unknown backup entry: {entry.name}")
            continue
        try:
            record = _read_record(entry, BackupRecord.from_mapping, "backup manifest", owner_uid)
        except StateAmbiguityError:
            raise
        if record.backup_id in seen:
            raise StateAmbiguityError("duplicate backup identity")
        if record.backup_id != entry.stem:
            raise StateAmbiguityError("backup manifest identity conflicts with its path")
        seen.add(record.backup_id)
        dump = root / f"{entry.stem}.dump"
        details = _lstat(dump, "backup dump")
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise StateAmbiguityError("completed backup dump is not a regular file")
        if details.st_uid != owner_uid or details.st_mode & 0o7022:
            raise StateAmbiguityError("completed backup dump is unsafe")
        result.append(record)
    return result


def _read_selections(
    root: Path,
    owner_uid: int,
    warnings: list[str],
    temporary: list[PurePosixPath],
    releases: Mapping[str, ReleaseRecord],
    backups: Mapping[str, BackupRecord],
    deadline: float,
) -> _SuccessfulHistory:
    database = sqlite3.connect("")
    history = _SuccessfulHistory(database)
    try:
        database.execute(
            """
            CREATE TABLE selections (
                filename TEXT PRIMARY KEY,
                selected_at TEXT NOT NULL,
                identity TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL
            )
            """
        )
        for entry in _selection_entries(root, owner_uid, deadline):
            _require_history_deadline(deadline)
            if _SELECTION_TEMP_RE.fullmatch(entry.name):
                _validate_temporary(entry, owner_uid, "selection temporary")
                if len(temporary) < MAX_TEMPORARY_PATHS:
                    temporary.append(PurePosixPath(entry.as_posix()))
                continue
            if not _SELECTION_FILE_RE.fullmatch(entry.name):
                if len(warnings) < MAX_WARNINGS:
                    warnings.append(f"unknown selection entry: {entry.name}")
                continue
            record = _read_record(entry, SelectionRecord.from_mapping, "selection record", owner_uid)
            _require_history_deadline(deadline)
            _validate_selection_record_references(record, releases, backups)
            if entry.name != selection_filename(record):
                raise StateAmbiguityError("selection record identity conflicts with its path")
            identity = json.dumps(
                [
                    record.release_id,
                    record.previous_release_id,
                    record.backup_id,
                    record.selected_at.isoformat(),
                ],
                separators=(",", ":"),
            )
            payload = json.dumps(record.to_mapping(), separators=(",", ":"), sort_keys=True)
            try:
                database.execute(
                    "INSERT INTO selections VALUES (?, ?, ?, ?)",
                    (entry.name, record.selected_at.isoformat(), identity, payload),
                )
            except sqlite3.IntegrityError as error:
                raise StateAmbiguityError("duplicate selection identity") from error
            _require_history_deadline(deadline)
        database.commit()
        _require_history_deadline(deadline)
        return history
    except Exception:
        history.close()
        raise


def _selection_entries(root: Path, owner_uid: int, deadline: float):
    """Yield complete history without imposing an installation-lifetime count cap."""

    _require_history_deadline(deadline)
    try:
        details = root.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise StateAmbiguityError("unable to inspect selection root") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise StateAmbiguityError("authoritative selection root is not a directory")
    if details.st_uid != owner_uid or details.st_mode & 0o7022:
        raise StateAmbiguityError("authoritative selection root is unsafe")
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                _require_history_deadline(deadline)
                yield Path(entry.path)
    except StateAmbiguityError:
        raise
    except OSError as error:
        raise StateAmbiguityError("unable to enumerate selection root") from error


def _require_history_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise StateAmbiguityError("selection history inspection timed out")


def _validate_selection_record_references(
    selection: SelectionRecord,
    releases: Mapping[str, ReleaseRecord],
    backups: Mapping[str, BackupRecord],
) -> None:
    if selection.release_id not in releases:
        raise StateAmbiguityError("selection references an unknown release")
    if selection.backup_id is not None and selection.backup_id not in backups:
        raise StateAmbiguityError("selection references an unknown backup")
    if (
        selection.observed_previous_release_id is not None
        and selection.observed_previous_release_id not in releases
    ):
        raise StateAmbiguityError("selection references an unknown observed release")
    if any(backup_id not in backups for backup_id in selection.recovery_backup_ids):
        raise StateAmbiguityError("selection references an unknown recovery backup")


def _read_backup_protections(
    root: Path,
    owner_uid: int,
    temporary: list[PurePosixPath],
    warnings: list[str],
) -> list[BackupProtection]:
    entries = _entries(root, owner_uid, "backup protection root")
    result: list[BackupProtection] = []
    seen: set[str] = set()
    for entry in entries:
        if _BACKUP_PROTECTION_TEMP_RE.fullmatch(entry.name):
            _validate_temporary(entry, owner_uid, "backup protection temporary")
            temporary.append(PurePosixPath(entry.as_posix()))
            continue
        if not _BACKUP_PROTECTION_FILE_RE.fullmatch(entry.name):
            warnings.append(f"unknown backup protection entry: {entry.name}")
            continue
        record = _read_record(
            entry,
            BackupProtection.from_mapping,
            "backup protection record",
            owner_uid,
        )
        if record.backup_id in seen:
            raise StateAmbiguityError("duplicate backup protection identity")
        if record.backup_id != entry.stem:
            raise StateAmbiguityError("backup protection identity conflicts with its path")
        seen.add(record.backup_id)
        result.append(record)
    return result


def _read_retiring_backup_protections(
    root: Path, owner_uid: int
) -> list[BackupProtection]:
    result: list[BackupProtection] = []
    seen: set[str] = set()
    for entry in _entries(root, owner_uid, "backup protection retirement root"):
        if not _BACKUP_PROTECTION_FILE_RE.fullmatch(entry.name):
            raise StateAmbiguityError("unknown backup protection retirement entry")
        record = _read_record(
            entry,
            BackupProtection.from_mapping,
            "backup protection retirement marker",
            owner_uid,
        )
        if record.backup_id in seen:
            raise StateAmbiguityError("duplicate backup protection retirement identity")
        if record.backup_id != entry.stem:
            raise StateAmbiguityError(
                "backup protection retirement identity conflicts with its path"
            )
        seen.add(record.backup_id)
        result.append(record)
    return result


def _read_restore_target(path: Path, owner_uid: int) -> RestoreTarget | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateAmbiguityError("unable to inspect restore target") from error
    return _read_record(path, RestoreTarget.from_mapping, "restore target", owner_uid)


def _read_release_manifest_temporaries(
    release_directory: Path,
    owner_uid: int,
    temporary: list[PurePosixPath],
) -> None:
    """Recognize only the private temporary shape emitted by the manifest writer."""

    try:
        with os.scandir(release_directory) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > MAX_INVENTORY_ENTRIES:
                    raise StateAmbiguityError(
                        "release directory inventory exceeds safe bound"
                    )
                if _RELEASE_MANIFEST_TEMP_RE.fullmatch(entry.name):
                    path = Path(entry.path)
                    _validate_temporary(path, owner_uid, "release manifest temporary")
                    temporary.append(PurePosixPath(path.as_posix()))
    except StateAmbiguityError:
        raise
    except OSError as error:
        raise StateAmbiguityError("unable to inspect release directory") from error


def _validate_temporary(
    path: Path,
    owner_uid: int,
    label: str,
    *,
    directory: bool = False,
) -> None:
    details = _lstat(path, label)
    if (
        stat.S_ISLNK(details.st_mode)
        or not (stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode))
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
    ):
        raise StateAmbiguityError(f"{label} is unsafe")


def _read_record(
    path: Path,
    parser: object,
    label: str,
    owner_uid: int,
    *,
    maximum: int = MAX_RECORD_BYTES,
):
    details = _lstat(path, label)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise StateAmbiguityError(f"authoritative {label} is not a regular file")
    if details.st_uid != owner_uid or stat.S_IMODE(details.st_mode) != 0o600:
        raise StateAmbiguityError(f"authoritative {label} has unsafe owner or mode")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise StateAmbiguityError(f"unable to read {label}") from error
    if len(raw) > maximum:
        raise StateAmbiguityError(f"authoritative {label} is oversized")
    try:
        value = json.loads(raw.decode("utf-8"))
        return parser(value)  # type: ignore[operator]
    except (UnicodeDecodeError, json.JSONDecodeError, RecordError, ValueError, TypeError) as error:
        raise StateAmbiguityError(f"{label} is invalid") from error


def _entries(root: Path, owner_uid: int, label: str) -> tuple[Path, ...]:
    try:
        details = root.lstat()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise StateAmbiguityError(f"unable to inspect {label}") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise StateAmbiguityError(f"authoritative {label} is not a directory")
    if details.st_uid != owner_uid or details.st_mode & 0o7022:
        raise StateAmbiguityError(f"authoritative {label} is unsafe")
    try:
        paths: list[Path] = []
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > MAX_INVENTORY_ENTRIES:
                    raise StateAmbiguityError(
                        f"{label} inventory exceeds safe bound"
                    )
                paths.append(Path(entry.path))
        return tuple(sorted(paths, key=lambda item: item.name))
    except StateAmbiguityError:
        raise
    except OSError as error:
        raise StateAmbiguityError(f"unable to enumerate {label}") from error


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as error:
        raise StateAmbiguityError(f"{label} is absent") from error
    except OSError as error:
        raise StateAmbiguityError(f"unable to inspect {label}") from error


def _validate_selection_history(
    history: _SuccessfulHistory,
    releases: Mapping[str, ReleaseRecord],
    backups: Mapping[str, BackupRecord],
    deadline: float,
) -> None:
    previous: str | None = None
    previous_selected_at = None
    projection: list[SelectionRecord] = []
    projection_filenames: list[str] = []
    successful_backup_ids: set[str] = set()
    for index, (filename, selection) in enumerate(history.ordered_rows(deadline)):
        _require_history_deadline(deadline)
        if selection.release_id not in releases:
            raise StateAmbiguityError("selection references an unknown release")
        if selection.backup_id is not None and selection.backup_id not in backups:
            raise StateAmbiguityError("selection references an unknown backup")
        if (
            selection.observed_previous_release_id is not None
            and selection.observed_previous_release_id not in releases
        ):
            raise StateAmbiguityError("selection references an unknown observed release")
        if any(backup_id not in backups for backup_id in selection.recovery_backup_ids):
            raise StateAmbiguityError("selection references an unknown recovery backup")
        if index == 0 and selection.previous_release_id is not None:
            raise StateAmbiguityError("first selection cannot name a previous successful release")
        if index and selection.previous_release_id != previous:
            raise StateAmbiguityError("selection history is contradictory")
        if index and selection.observed_previous_release_id is None:
            raise StateAmbiguityError("later selection must record its observed previous release")
        if index and selection.selected_at <= previous_selected_at:
            raise StateAmbiguityError("selection history is not chronological")
        previous = selection.release_id
        previous_selected_at = selection.selected_at
        projection.append(selection)
        projection_filenames.append(filename)
        if len(projection) > 2:
            del projection[0]
            del projection_filenames[0]
        if selection.backup_id is not None:
            successful_backup_ids.add(selection.backup_id)
        successful_backup_ids.update(selection.recovery_backup_ids)
    history.projection = tuple(projection)
    history.projection_filenames = tuple(projection_filenames)
    history.successful_backup_ids = frozenset(successful_backup_ids)
    _require_history_deadline(deadline)


def _validate_backup_sources(
    backups: Mapping[str, BackupRecord], releases: Mapping[str, ReleaseRecord], deadline: float
) -> None:
    for backup in backups.values():
        _require_history_deadline(deadline)
        source = releases.get(backup.source_release_id)
        if source is None:
            raise StateAmbiguityError("backup references an unknown source release")
        source_versions = _release_migration_versions(source)
        if backup.migration_versions != source_versions[: len(backup.migration_versions)]:
            raise StateAmbiguityError("backup migration provenance contradicts its source release")


def _release_migration_versions(record: ReleaseRecord) -> tuple[int, ...]:
    try:
        versions = tuple(int(item["filename"][:14]) for item in record.migrations)
        return validate_migration_versions(versions)
    except (KeyError, TypeError, ValueError):
        raise StateAmbiguityError("installed release migration provenance is invalid") from None


def _release_migration_fingerprints(
    record: ReleaseRecord,
) -> dict[int, tuple[str, str]]:
    try:
        return {
            int(item["filename"][:14]): (item["filename"], item["sha256"])
            for item in record.migrations
        }
    except (KeyError, TypeError, ValueError):
        raise StateAmbiguityError("installed release migration provenance is invalid") from None


def _validate_release_fingerprint_agreement(
    source: ReleaseRecord, target: ReleaseRecord, versions: tuple[int, ...]
) -> None:
    source_fingerprints = _release_migration_fingerprints(source)
    target_fingerprints = _release_migration_fingerprints(target)
    for version in versions:
        if source_fingerprints.get(version) != target_fingerprints.get(version):
            raise StateAmbiguityError("backup protection migration fingerprints conflict")


def _validate_backup_protections(
    protections: list[BackupProtection],
    releases: Mapping[str, ReleaseRecord],
    backups: Mapping[str, BackupRecord],
    history: _SuccessfulHistory,
    deadline: float,
) -> None:
    """Validate each protection's references and its attempt ordering."""

    attempts_by_baseline: dict[str | None, set[int]] = {}
    first_selection = history.first(deadline)
    for protection in protections:
        _require_history_deadline(deadline)
        backup = backups.get(protection.backup_id)
        if backup is None:
            raise StateAmbiguityError("backup protection references an unknown backup")
        target = releases.get(protection.target_release_id)
        if target is None:
            raise StateAmbiguityError("backup protection references an unknown target release")
        if protection.base_selection_id is None:
            if first_selection and protection.backup_id not in first_selection.recovery_backup_ids:
                raise StateAmbiguityError(
                    "null-baseline backup protection is unresolved after successful history"
                )
        else:
            baseline = history.record(protection.base_selection_id, deadline)
            if baseline is None:
                raise StateAmbiguityError("backup protection references an unknown selection")
            successor = history.successor(protection.base_selection_id, deadline)
            if successor is not None and protection.backup_id not in successor.recovery_backup_ids:
                raise StateAmbiguityError(
                    "backup protection was not resolved by the next successful selection"
                )

        target_versions = _release_migration_versions(target)
        if backup.migration_versions != target_versions[: len(backup.migration_versions)]:
            raise StateAmbiguityError("backup protection migration provenance is contradictory")
        source = releases.get(backup.source_release_id)
        if source is None:
            raise StateAmbiguityError("backup protection source provenance is unavailable")
        _validate_release_fingerprint_agreement(source, target, backup.migration_versions)

        attempts = attempts_by_baseline.setdefault(protection.base_selection_id, set())
        if protection.attempt_number in attempts:
            raise StateAmbiguityError("backup protection attempt numbers are duplicated")
        attempts.add(protection.attempt_number)

    for attempt_numbers in attempts_by_baseline.values():
        _require_history_deadline(deadline)
        if 0 not in attempt_numbers:
            raise StateAmbiguityError("backup protection is missing its original attempt")


def _validate_retiring_backup_protections(
    retiring: list[BackupProtection],
    active: list[BackupProtection],
    releases: Mapping[str, ReleaseRecord],
    backups: Mapping[str, BackupRecord],
    history: _SuccessfulHistory,
    deadline: float,
) -> None:
    active_ids = {item.backup_id for item in active}
    for protection in retiring:
        _require_history_deadline(deadline)
        if protection.backup_id in active_ids:
            raise StateAmbiguityError("backup protection is both active and retiring")
        target = releases.get(protection.target_release_id)
        if target is None:
            raise StateAmbiguityError("retiring protection references an unknown target release")
        if (
            protection.base_selection_id is not None
            and history.record(protection.base_selection_id, deadline) is None
        ):
            raise StateAmbiguityError("retiring protection references an unknown selection")
        backup = backups.get(protection.backup_id)
        if backup is not None:
            target_versions = _release_migration_versions(target)
            if backup.migration_versions != target_versions[: len(backup.migration_versions)]:
                raise StateAmbiguityError("retiring protection migration provenance is contradictory")
            source = releases.get(backup.source_release_id)
            if source is None:
                raise StateAmbiguityError("retiring protection source provenance is unavailable")
            _validate_release_fingerprint_agreement(source, target, backup.migration_versions)


def _validate_restore_target(
    target: RestoreTarget | None,
    releases: Mapping[str, ReleaseRecord],
    backups: Mapping[str, BackupRecord],
    history: _SuccessfulHistory,
    deadline: float,
) -> None:
    _require_history_deadline(deadline)
    if target is None:
        return
    source_release = releases.get(target.source_release_id)
    if source_release is None:
        raise StateAmbiguityError("restore target references an unknown source release")
    input_backup = backups.get(target.backup_id)
    if input_backup is None:
        raise StateAmbiguityError("restore target references an unknown input backup")
    if (
        input_backup.dump_sha256 != target.dump_sha256
        or input_backup.source_release_id != target.source_release_id
    ):
        raise StateAmbiguityError("restore target input backup identity disagrees")
    if (
        target.base_selection_id is not None
        and history.record(target.base_selection_id, deadline) is None
    ):
        raise StateAmbiguityError("restore target references an unknown base selection")
    if (
        target.observed_previous_release_id is not None
        and target.observed_previous_release_id not in releases
    ):
        raise StateAmbiguityError("restore target references an unknown observed release")

    if backups.get(target.safety_backup_id) is None:
        raise StateAmbiguityError("restore target references an unknown safety backup")
    for attempt in target.safety_backup_attempts:
        _require_history_deadline(deadline)
        backup = backups.get(str(attempt["backup_id"]))
        if backup is None:
            raise StateAmbiguityError("restore target references an unknown safety attempt")
    if target.replacement is not None:
        replacement_backup = backups.get(str(target.replacement["backup_id"]))
        if replacement_backup is None:
            raise StateAmbiguityError("restore target references an unknown replacement backup")
        if (
            replacement_backup.dump_sha256 != target.replacement["dump_sha256"]
            or replacement_backup.source_release_id != target.replacement["source_release_id"]
        ):
            raise StateAmbiguityError("restore target replacement backup identity disagrees")
        if target.replacement["source_release_id"] not in releases:
            raise StateAmbiguityError("restore target replacement references an unknown source release")


def _selected_link(paths: ManagedPaths, releases: Mapping[str, ReleaseRecord]) -> str | None:
    current = Path(paths.local(paths.current_link))
    try:
        details = current.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateAmbiguityError("unable to inspect current selection") from error
    if not stat.S_ISLNK(details.st_mode):
        raise StateAmbiguityError("current selection is not a symlink")
    try:
        target = current.resolve(strict=True)
    except OSError as error:
        raise StateAmbiguityError("current selection target is absent") from error
    root = Path(paths.local(paths.release_root)).resolve(strict=False)
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise StateAmbiguityError("current selection points outside release root") from error
    if len(relative.parts) != 1:
        raise StateAmbiguityError("current selection target is ambiguous")
    release_id = relative.parts[0]
    if release_id not in releases:
        raise StateAmbiguityError("current selection references an unknown release")
    return release_id


def _database_state(database: Mapping[str, object] | None) -> tuple[tuple[int, ...], str, bool]:
    if database is None:
        return (), "unknown", False
    if not isinstance(database, Mapping):
        raise StateAmbiguityError("database observation is invalid")
    if set(database) - {"state", "applied_migrations", "initial_empty"}:
        raise StateAmbiguityError("database observation contains duplicate or unknown facts")
    state = database.get("state", "unknown")
    if type(state) is not str or state not in {"ready", "absent", "unknown"}:
        raise StateAmbiguityError("database state is invalid")
    raw_migrations = database.get("applied_migrations", ())
    try:
        migrations = validate_migration_versions(raw_migrations)
    except MigrationOrderError:
        raise StateAmbiguityError("applied migrations are not sorted and unique") from None
    except ValueError:
        raise StateAmbiguityError("applied migrations are invalid") from None
    initial_empty = database.get("initial_empty", False)
    if type(initial_empty) is not bool or (initial_empty and (state != "ready" or migrations)):
        raise StateAmbiguityError("initial database evidence is invalid")
    return migrations, state, initial_empty


def _service_state(include_runtime: bool) -> str:
    if not include_runtime:
        return "unknown"
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", "taskman.service"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if completed.returncode == 0 and completed.stdout.strip() == "active":
        return "running"
    if completed.stdout.strip() in {"inactive", "deactivating"}:
        return "stopped"
    if completed.stdout.strip() in {"failed", "activating"}:
        return "failed"
    return "unknown"


__all__ = [
    "HostState",
    "MAX_INVENTORY_ENTRIES",
    "StateAmbiguityError",
    "observe_host_state",
]
