"""Coherent observation of completed host records and physical authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

from .paths import ManagedPaths, PathAuthorityError
from ..migrations import MigrationOrderError, validate_migration_versions
from taskman_ops.releases.identifiers import RELEASE_ID_RE
from .records import (
    MAX_RECORD_BYTES,
    BACKUP_ID_RE,
    BackupRecord,
    RecordError,
    ReleaseRecord,
    SelectionRecord,
    selection_filename,
)


MAX_WARNINGS = 64
MAX_TEMPORARY_PATHS = 64
MAX_INVENTORY_ENTRIES = 4096
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
        }


def observe_host_state(
    paths: ManagedPaths,
    *,
    database: Mapping[str, object] | None = None,
    include_runtime: bool = False,
    allow_selection_transition: bool = False,
) -> HostState:
    """Observe completed host records, optionally exposing one deploy transition.

    Ordinary callers reject any difference between ``current`` and durable
    selection history.  Deploy uses the narrowly scoped transition view while
    holding the lifecycle lock so it can finish a record publication lost
    immediately after its atomic current-link replacement.
    """

    if not isinstance(paths, ManagedPaths):
        raise TypeError("host-state observation needs managed paths")
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
    _note_unknown_deployment_entries(
        Path(paths.local(paths.deployment_root)), owner_uid, warnings
    )
    releases = _read_releases(release_root, owner_uid, temporary, warnings)
    backups = _read_backups(backup_root, owner_uid, temporary, warnings)
    selections = _read_selections(selection_root, owner_uid, warnings, temporary)

    release_by_id = {item.release_id: item for item in releases}
    backup_by_id = {item.backup_id: item for item in backups}
    _validate_selection_history(selections, release_by_id, backup_by_id)
    selected_from_history = selections[-1].release_id if selections else None
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

    applied_migrations, database_state = _database_state(database)
    service_state = _service_state(include_runtime)
    temporary.sort(key=lambda item: item.as_posix())
    warnings = sorted(set(warnings))[:MAX_WARNINGS]
    return HostState(
        selected_release_id=selected,
        releases=tuple(sorted(releases, key=lambda item: item.release_id)),
        backups=tuple(sorted(backups, key=lambda item: item.backup_id)),
        selections=tuple(selections),
        applied_migrations=applied_migrations,
        service_state=service_state,
        database_state=database_state,
        temporary_paths=tuple(temporary[:MAX_TEMPORARY_PATHS]),
        warnings=tuple(warnings),
    )


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
) -> None:
    """Bound the non-authoritative deployment area without reading old records."""

    for entry in _entries(root, owner_uid, "deployment root"):
        if entry.name != "selections":
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
            from taskman_ops.releases.identifiers import validate_release_id

            release_id = validate_release_id(entry.name)
        except (TypeError, ValueError):
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
        result.append(_read_record(manifest, ReleaseRecord.from_mapping, "release manifest", owner_uid))
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
) -> list[SelectionRecord]:
    entries = _entries(root, owner_uid, "selection root")
    result: list[SelectionRecord] = []
    seen: set[tuple[object, ...]] = set()
    for entry in entries:
        if _SELECTION_TEMP_RE.fullmatch(entry.name):
            _validate_temporary(entry, owner_uid, "selection temporary")
            temporary.append(PurePosixPath(entry.as_posix()))
            continue
        if not _SELECTION_FILE_RE.fullmatch(entry.name):
            warnings.append(f"unknown selection entry: {entry.name}")
            continue
        record = _read_record(entry, SelectionRecord.from_mapping, "selection record", owner_uid)
        if entry.name != selection_filename(record):
            raise StateAmbiguityError("selection record identity conflicts with its path")
        identity = (
            record.release_id,
            record.previous_release_id,
            record.backup_id,
            record.selected_at,
        )
        if identity in seen:
            raise StateAmbiguityError("duplicate selection identity")
        seen.add(identity)
        result.append(record)
    return sorted(result, key=lambda item: item.selected_at)


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


def _read_record(path: Path, parser: object, label: str, owner_uid: int):
    details = _lstat(path, label)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise StateAmbiguityError(f"authoritative {label} is not a regular file")
    if details.st_uid != owner_uid or details.st_mode & 0o7022:
        raise StateAmbiguityError(f"authoritative {label} is writable by group or other")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise StateAmbiguityError(f"unable to read {label}") from error
    if len(raw) > MAX_RECORD_BYTES:
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
    selections: list[SelectionRecord],
    releases: Mapping[str, ReleaseRecord],
    backups: Mapping[str, BackupRecord],
) -> None:
    previous: str | None = None
    for index, selection in enumerate(selections):
        if selection.release_id not in releases:
            raise StateAmbiguityError("selection references an unknown release")
        if selection.backup_id is not None and selection.backup_id not in backups:
            raise StateAmbiguityError("selection references an unknown backup")
        if index and selection.previous_release_id != previous:
            raise StateAmbiguityError("selection history is contradictory")
        if index and selection.selected_at <= selections[index - 1].selected_at:
            raise StateAmbiguityError("selection history is not chronological")
        previous = selection.release_id


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


def _database_state(database: Mapping[str, object] | None) -> tuple[tuple[int, ...], str]:
    if database is None:
        return (), "unknown"
    if not isinstance(database, Mapping):
        raise StateAmbiguityError("database observation is invalid")
    if set(database) - {"state", "applied_migrations"}:
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
    return migrations, state


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
