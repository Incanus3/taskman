"""Host-local exact cleanup planning and deletion policy."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

from taskman_ops.host_protocol import PROTOCOL_VERSION

from ..lifecycle import (
    LifecycleError,
    LifecycleLockContention,
    LifecycleStore,
)
from ..legacy_result import OperationRequest as HostRequest, OperationResult as HostResult
from ..paths import ManagedPaths, PathAuthorityError


_PARAMETER_KEYS = frozenset(
    {
        "action",
        "targets",
        "release_retention",
        "backup_retention",
        "database_port",
    }
)
_EXPECTED_KEYS = frozenset({"lifecycle"})
_LIFECYCLE_KEYS = frozenset({"activations", "backups", "releases"})
_TARGET_KEYS = frozenset(
    {"authority", "identifier", "kind", "path", "recoverable"}
)
_RECOVERY_KEYS = frozenset(
    {
        "record_path",
        "source_backup_id",
        "pre_restore_backup_id",
        "intended_release_id",
        "recovery_database",
        "state",
    }
)
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_RECOVERY_RE = re.compile(r"recovery-[0-9a-f]{32}\Z")
_STAGING_RE = re.compile(r"stage-[0-9a-f]{32}\Z")
_DATABASE_RE = re.compile(r"taskman_recovery_[0-9a-f]{32}\Z")


class _CleanupFailure(Exception):
    def __init__(
        self,
        *,
        changed: bool,
        residue: tuple[str, ...],
        database_state: str | None = None,
        recovery: tuple[str, ...] = (),
    ) -> None:
        self.changed = changed
        self.residue = residue
        self.database_state = database_state
        self.recovery = recovery


def cleanup(request: HostRequest) -> HostResult:
    """Plan or apply exact, record-backed deletion under the lifecycle lock."""

    try:
        action, targets, release_retention, backup_retention, database_port = _inputs(
            request
        )
        paths = ManagedPaths.from_mapping(request.paths)
        store = LifecycleStore(paths)
        paths.validate_existing(owner_uid=store.owner_uid)
        with store.exclusive_lifecycle_lock(operation="cleanup"):
            records = store.read()
            observed = _lifecycle(records)
            if request.expected_state["lifecycle"] != observed:
                rerun = _completed_rerun(
                    request, store, observed, targets, database_port
                )
                if rerun is not None:
                    return rerun
                return _refused(request)
            planned = _plan(
                store,
                records,
                release_retention=release_retention,
                backup_retention=backup_retention,
            )
            if action == "inspect":
                if targets:
                    return _refused(request)
                return _inspection(request, planned, observed)
            rerun = _completed_rerun(
                request, store, observed, targets, database_port
            )
            if rerun is not None:
                return rerun
            if targets != planned:
                return _refused(request)
            return _delete(request, store, targets, database_port)
    except LifecycleLockContention as error:
        return _locked(request, error)
    except (LifecycleError, PathAuthorityError, OSError, ValueError):
        return _refused(request)


def _inputs(
    request: HostRequest,
) -> tuple[str, tuple[dict[str, object], ...], int, int, int]:
    if (
        set(request.parameters) != _PARAMETER_KEYS
        or set(request.expected_state) != _EXPECTED_KEYS
    ):
        raise ValueError("cleanup request is incomplete")
    expected = request.expected_state["lifecycle"]
    if not isinstance(expected, Mapping) or set(expected) != _LIFECYCLE_KEYS:
        raise ValueError("cleanup lifecycle authority is invalid")
    action = request.parameters["action"]
    targets = request.parameters["targets"]
    release_retention = request.parameters["release_retention"]
    backup_retention = request.parameters["backup_retention"]
    database_port = request.parameters["database_port"]
    if (
        action not in {"inspect", "execute"}
        or not isinstance(targets, tuple)
        or len(targets) > 64
        or type(release_retention) is not int
        or release_retention < 1
        or type(backup_retention) is not int
        or backup_retention < 1
        or type(database_port) is not int
        or not 0 < database_port < 65536
    ):
        raise ValueError("cleanup request is invalid")
    return (
        action,
        tuple(_target(item) for item in targets),
        release_retention,
        backup_retention,
        database_port,
    )


def _lifecycle(records: object) -> dict[str, tuple[str, ...]]:
    return {
        "releases": tuple(item.release_id for item in records.releases),
        "activations": tuple(item.activation_id for item in records.activations),
        "backups": tuple(item.backup_id for item in records.backups),
    }


def _plan(
    store: LifecycleStore,
    records: object,
    *,
    release_retention: int,
    backup_retention: int,
) -> tuple[dict[str, object], ...]:
    current = records.current_release_id
    activations = tuple(
        sorted(
            records.activations,
            key=lambda item: (item.activated_at, item.activation_id),
        )
    )
    protected_releases = set()
    if current is not None:
        protected_releases.add(current)
    if activations:
        previous = activations[-1].previous_release_id
        if previous is not None:
            protected_releases.add(previous)
        protected_releases.update(
            item.candidate_release_id for item in activations[-release_retention:]
        )
    backups = tuple(
        sorted(
            records.backups,
            key=lambda item: (item.created_at, item.backup_id),
            reverse=True,
        )
    )
    protected_backups = {item.backup_id for item in backups[:backup_retention]}
    latest_predeploy: dict[tuple[str | None, str | None], object] = {}
    for item in backups:
        if item.reason == "pre-deploy":
            latest_predeploy.setdefault(
                (item.current_release_id, item.candidate_release_id),
                item,
            )
    protected_backups.update(item.backup_id for item in latest_predeploy.values())

    recovery_targets = _recovery_targets(store)
    for target in recovery_targets:
        authority = target["authority"]
        assert isinstance(authority, Mapping)
        protected_backups.update(
            (
                str(authority["source_backup_id"]),
                str(authority["pre_restore_backup_id"]),
            )
        )
        protected_releases.add(str(authority["intended_release_id"]))
    for item in backups:
        if item.backup_id in protected_backups:
            if item.current_release_id is not None:
                protected_releases.add(item.current_release_id)
            if item.candidate_release_id is not None:
                protected_releases.add(item.candidate_release_id)

    targets: list[dict[str, object]] = []
    for release in records.releases:
        if release.release_id not in protected_releases:
            targets.append(
                {
                    "kind": "release",
                    "identifier": release.release_id,
                    "path": store.release_path(release.release_id).as_posix(),
                    "recoverable": False,
                    "authority": None,
                }
            )
    for backup in backups:
        if backup.backup_id not in protected_backups:
            targets.append(
                {
                    "kind": "backup",
                    "identifier": backup.backup_id,
                    "path": backup.dump_path.as_posix(),
                    "recoverable": False,
                    "authority": None,
                }
            )
    targets.extend(_staging_targets(store))
    targets.extend(recovery_targets)
    targets.sort(
        key=lambda value: (
            bool(value["recoverable"]),
            str(value["kind"]),
            str(value["identifier"]),
            str(value["path"]),
        )
    )
    if len({(item["kind"], item["identifier"]) for item in targets}) != len(
        targets
    ):
        raise LifecycleError("cleanup target authority is duplicated")
    if len(targets) > 64:
        raise LifecycleError("cleanup target inventory exceeds protocol limit")
    return tuple(targets)


def _staging_targets(store: LifecycleStore) -> tuple[dict[str, object], ...]:
    root = store.deployment_root / "uploads"
    targets: list[dict[str, object]] = []
    for path in _entries(root):
        if not path.name.endswith(".json"):
            continue
        identifier = path.name[:-5]
        if _STAGING_RE.fullmatch(identifier) is None:
            continue
        value = _read_owned_json(path, store.owner_uid)
        if (
            set(value) != {"schema_version", "staging_id", "state", "completed_at"}
            or value["schema_version"] != 1
            or value["staging_id"] != identifier
            or value["state"] != "completed"
        ):
            raise LifecycleError("cleanup staging authority is invalid")
        targets.append(
            {
                "kind": "staging",
                "identifier": identifier,
                "path": path.as_posix(),
                "recoverable": False,
                "authority": {
                    "record_path": path.as_posix(),
                    "state": "completed",
                },
            }
        )
    return tuple(targets)


def _recovery_targets(store: LifecycleStore) -> tuple[dict[str, object], ...]:
    root = store.deployment_root / "restores"
    targets: list[dict[str, object]] = []
    for path in _entries(root):
        if not path.name.endswith(".json"):
            continue
        identifier = path.name[:-5]
        if _RECOVERY_RE.fullmatch(identifier) is None:
            continue
        value = _read_owned_json(path, store.owner_uid)
        if (
            set(value)
            != {
                "schema_version",
                "recovery_id",
                "database",
                "recovery_database",
                "source_backup_id",
                "pre_restore_backup_id",
                "intended_release_id",
                "state",
                "created_at",
            }
            or value["schema_version"] != 1
            or value["recovery_id"] != identifier
            or value["state"] != "retained"
            or _DATABASE_RE.fullmatch(str(value["recovery_database"])) is None
            or _BACKUP_RE.fullmatch(str(value["source_backup_id"])) is None
            or _BACKUP_RE.fullmatch(str(value["pre_restore_backup_id"])) is None
        ):
            raise LifecycleError("cleanup recovery authority is invalid")
        targets.append(
            {
                "kind": "database",
                "identifier": identifier,
                "path": f"/database/{identifier}",
                "recoverable": True,
                "authority": {
                    "record_path": path.as_posix(),
                    "source_backup_id": value["source_backup_id"],
                    "pre_restore_backup_id": value["pre_restore_backup_id"],
                    "intended_release_id": value["intended_release_id"],
                    "recovery_database": value["recovery_database"],
                    "state": "retained",
                },
            }
        )
    return tuple(targets)


def _entries(root: Path) -> tuple[Path, ...]:
    try:
        details = root.lstat()
    except FileNotFoundError:
        return ()
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o750
    ):
        raise LifecycleError("cleanup authority directory is unsafe")
    values = tuple(sorted(root.iterdir(), key=lambda item: item.name))
    if len(values) > 64:
        raise LifecycleError("cleanup authority inventory exceeds limit")
    return values


def _read_owned_json(path: Path, owner_uid: int) -> Mapping[str, object]:
    details = path.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != owner_uid
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_size > 64 * 1024
    ):
        raise LifecycleError("cleanup authority file is unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise LifecycleError("cleanup authority record is invalid")
    return value


def _target(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _TARGET_KEYS:
        raise ValueError("cleanup target is invalid")
    target = dict(value)
    kind = target["kind"]
    identifier = target["identifier"]
    path = target["path"]
    recoverable = target["recoverable"]
    authority = target["authority"]
    if (
        kind not in {"release", "backup", "staging", "database"}
        or type(identifier) is not str
        or type(path) is not str
        or not path.startswith("/")
        or any(char in path for char in "*?[]${}\\\n\r")
        or type(recoverable) is not bool
    ):
        raise ValueError("cleanup target is unsafe")
    if kind in {"release", "backup"} and authority is not None:
        raise ValueError("cleanup target has unexpected authority")
    if kind == "staging":
        if (
            _STAGING_RE.fullmatch(identifier) is None
            or not isinstance(authority, Mapping)
            or set(authority) != {"record_path", "state"}
            or authority["record_path"] != path
            or authority["state"] != "completed"
        ):
            raise ValueError("cleanup staging target is invalid")
    if kind == "database":
        if (
            _RECOVERY_RE.fullmatch(identifier) is None
            or path != f"/database/{identifier}"
            or not recoverable
            or not isinstance(authority, Mapping)
            or set(authority) != _RECOVERY_KEYS
            or authority["state"] != "retained"
            or authority["recovery_database"]
            != f"taskman_{identifier.replace('-', '_')}"
        ):
            raise ValueError("cleanup database target is invalid")
    return target


def _inspection(
    request: HostRequest,
    targets: tuple[dict[str, object], ...],
    observed: Mapping[str, object],
) -> HostResult:
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "succeeded",
        "cleanup-inspected",
        (),
        {"lifecycle": observed, "targets": targets},
        {},
        {},
        (),
        (),
        (),
    )


def _completed_rerun(
    request: HostRequest,
    store: LifecycleStore,
    observed: Mapping[str, object],
    targets: tuple[dict[str, object], ...],
    database_port: int,
) -> HostResult | None:
    """Recognize only the exact already-absent filesystem cleanup effect."""

    if not targets:
        return None
    expected = request.expected_state["lifecycle"]
    if not isinstance(expected, Mapping):
        return None
    removed_backups = {
        target["identifier"]
        for target in targets
        if target["kind"] == "backup"
    }
    expected_after = {
        "releases": tuple(expected["releases"]),
        "activations": tuple(expected["activations"]),
        "backups": tuple(
            item
            for item in expected["backups"]
            if item not in removed_backups
        ),
    }
    if observed != expected_after or any(
        not _target_absent(store, target, database_port)
        for target in targets
    ):
        return None
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "no_change",
        "cleanup",
        (),
        {"removed": (), "recoverability": (), "targets": targets},
        {},
        {},
        (),
        (),
        (),
    )


def _target_absent(
    store: LifecycleStore,
    target: Mapping[str, object],
    database_port: int,
) -> bool:
    if target["kind"] == "database":
        authority = target["authority"]
        if not isinstance(authority, Mapping):
            return False
        record = Path(str(authority["record_path"]))
        return (
            not record.exists()
            and not record.is_symlink()
            and _database_absent(
                database_port, str(authority["recovery_database"])
            )
        )
    path = Path(str(target["path"]))
    if path.exists() or path.is_symlink():
        return False
    if target["kind"] == "backup":
        record = (
            store.deployment_root
            / "backups"
            / f"{target['identifier']}.json"
        )
        return not record.exists() and not record.is_symlink()
    if target["kind"] == "staging":
        return True
    if target["kind"] == "release":
        return True
    return False


def _delete(
    request: HostRequest,
    store: LifecycleStore,
    targets: tuple[dict[str, object], ...],
    database_port: int,
) -> HostResult:
    removed: list[dict[str, object]] = []
    recoverability: list[bool] = []
    try:
        for target in targets:
            _validate_target(store, target, database_port)
    except (LifecycleError, OSError):
        return _refused(request)
    for target in targets:
        try:
            target_changed = _delete_target(store, target, database_port)
        except _CleanupFailure as error:
            changed = bool(removed) or error.changed
            lifecycle: dict[str, object] = {
                "removed": removed,
                "recoverability": recoverability,
                "targets": targets,
            }
            if error.database_state is not None:
                lifecycle["database_state"] = error.database_state
            return HostResult(
                PROTOCOL_VERSION,
                request.operation,
                request.operation_id,
                "failed",
                "cleanup",
                ("cleanup",) if changed else (),
                lifecycle,
                {},
                {},
                error.residue,
                error.recovery
                or ("inspect exact cleanup residue before retrying",),
                ("unable to remove operation residue",),
            )
        except (LifecycleError, OSError, subprocess.SubprocessError):
            return HostResult(
                PROTOCOL_VERSION,
                request.operation,
                request.operation_id,
                "failed",
                "cleanup",
                ("cleanup",) if removed else (),
                {
                    "removed": removed,
                    "recoverability": recoverability,
                    "targets": targets,
                },
                {},
                {},
                _target_residue(target),
                ("inspect exact cleanup residue before retrying",),
                ("unable to remove operation residue",),
            )
        if target_changed:
            removed.append(target)
            recoverability.append(bool(target["recoverable"]))
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "succeeded" if removed else "no_change",
        "cleanup",
        ("cleanup",) if removed else (),
        {
            "removed": removed,
            "recoverability": recoverability,
            "targets": targets,
        },
        {},
        {},
        (),
        (),
        (),
    )


def _delete_target(
    store: LifecycleStore,
    target: Mapping[str, object],
    database_port: int,
) -> bool:
    kind = target["kind"]
    path = Path(str(target["path"]))
    if kind == "release":
        if not path.exists() and not path.is_symlink():
            return False
        before = _tree_state(path)
        try:
            shutil.rmtree(path)
            _fsync_directory(path.parent)
        except OSError as error:
            changed = (
                not path.exists()
                and not path.is_symlink()
                or _tree_state(path) != before
            )
            raise _CleanupFailure(
                changed=changed,
                residue=(path.as_posix(),) if path.exists() else (),
            ) from error
        return True
    if kind == "backup":
        identifier = str(target["identifier"])
        record = store.deployment_root / "backups" / f"{identifier}.json"
        dump_changed = False
        if path.exists() or path.is_symlink():
            try:
                path.unlink()
                dump_changed = True
                _fsync_directory(path.parent)
            except OSError as error:
                dump_changed = not path.exists() and not path.is_symlink()
                raise _CleanupFailure(
                    changed=dump_changed,
                    residue=tuple(
                        item.as_posix()
                        for item in (path, record)
                        if item.exists() or item.is_symlink()
                    ),
                ) from error
        record_changed = False
        if record.exists() or record.is_symlink():
            try:
                record.unlink()
                record_changed = True
                _fsync_directory(record.parent)
            except OSError as error:
                record_changed = (
                    not record.exists() and not record.is_symlink()
                )
                raise _CleanupFailure(
                    changed=dump_changed or record_changed,
                    residue=tuple(
                        item.as_posix()
                        for item in (path, record)
                        if item.exists() or item.is_symlink()
                    ),
                ) from error
        return dump_changed or record_changed
    if kind == "staging":
        authority = target["authority"]
        assert isinstance(authority, Mapping)
        record = Path(str(authority["record_path"]))
        if record != path or record.parent != store.deployment_root / "uploads":
            raise LifecycleError("cleanup staging path is not authoritative")
        if not record.exists() and not record.is_symlink():
            return False
        try:
            record.unlink()
            _fsync_directory(record.parent)
        except OSError as error:
            changed = not record.exists() and not record.is_symlink()
            raise _CleanupFailure(
                changed=changed,
                residue=(record.as_posix(),) if not changed else (),
            ) from error
        return True
    authority = target["authority"]
    assert isinstance(authority, Mapping)
    record = Path(str(authority["record_path"]))
    if record.parent != store.deployment_root / "restores":
        raise LifecycleError("cleanup recovery record is not authoritative")
    database = str(authority["recovery_database"])
    database_changed = False
    if not _database_absent(database_port, database):
        try:
            _postgres(
                database_port,
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{database}' AND pid <> pg_backend_pid()",
            )
            _postgres(database_port, f'DROP DATABASE "{database}"')
            database_changed = True
        except (OSError, subprocess.SubprocessError) as error:
            try:
                database_changed = _database_absent(database_port, database)
            except (OSError, subprocess.SubprocessError) as probe_error:
                raise _CleanupFailure(
                    changed=True,
                    residue=(str(target["path"]), record.as_posix()),
                    database_state="unknown",
                    recovery=(
                        f"prove whether {database} exists before retrying exact cleanup",
                    ),
                ) from probe_error
            raise _CleanupFailure(
                changed=database_changed,
                residue=tuple(
                    item
                    for item in (
                        None if database_changed else str(target["path"]),
                        record.as_posix(),
                    )
                    if item is not None
                ),
                database_state="absent" if database_changed else "present",
            ) from error
    record_changed = False
    if record.exists() or record.is_symlink():
        try:
            record.unlink()
            record_changed = True
            _fsync_directory(record.parent)
        except OSError as error:
            record_changed = (
                not record.exists() and not record.is_symlink()
            )
            raise _CleanupFailure(
                changed=database_changed or record_changed,
                residue=(record.as_posix(),)
                if record.exists() or record.is_symlink()
                else (),
                database_state="absent",
            ) from error
    return database_changed or record_changed


def _validate_target(
    store: LifecycleStore,
    target: Mapping[str, object],
    database_port: int,
) -> None:
    kind = target["kind"]
    path = Path(str(target["path"]))
    if kind == "release":
        expected = store.release_path(str(target["identifier"]))
        if path != expected:
            raise LifecycleError("cleanup release path is not authoritative")
        if path.exists() or path.is_symlink():
            _safe_directory_tree(path, store.owner_uid)
        return
    if kind == "backup":
        identifier = str(target["identifier"])
        backup = next(
            (
                item
                for item in store.read().backups
                if item.backup_id == identifier
            ),
            None,
        )
        record = store.deployment_root / "backups" / f"{identifier}.json"
        if (
            backup is None
            or Path(backup.dump_path.as_posix()) != path
            or path.parent != store.backup_root
        ):
            raise LifecycleError("cleanup backup path is not authoritative")
        if path.exists() or path.is_symlink():
            _safe_regular(path, store.owner_uid)
        _safe_regular(record, store.owner_uid)
        return
    authority = target["authority"]
    assert isinstance(authority, Mapping)
    record = Path(str(authority["record_path"]))
    if kind == "staging":
        if record != path or record.parent != store.deployment_root / "uploads":
            raise LifecycleError("cleanup staging path is not authoritative")
    elif record.parent != store.deployment_root / "restores":
        raise LifecycleError("cleanup recovery record is not authoritative")
    _safe_regular(record, store.owner_uid)


def _safe_directory_tree(path: Path, owner_uid: int) -> None:
    root = path.resolve(strict=True)
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != owner_uid
    ):
        raise LifecycleError("cleanup release directory is unsafe")
    for entry in (path, *path.rglob("*")):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != owner_uid:
            raise LifecycleError("cleanup release tree is unsafe")
        entry.resolve(strict=True).relative_to(root)


def _tree_state(path: Path) -> tuple[str, ...]:
    if not path.exists() or path.is_symlink():
        return ()
    return tuple(
        entry.relative_to(path).as_posix()
        for entry in sorted(path.rglob("*"))
    )


def _safe_regular(path: Path, owner_uid: int) -> None:
    details = path.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != owner_uid
    ):
        raise LifecycleError("cleanup file authority is unsafe")


def _postgres(port: int, sql: str) -> None:
    subprocess.run(
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
            str(port),
            "--username",
            "postgres",
            "--dbname=postgres",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            sql,
        ),
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )


def _database_absent(port: int, database: str) -> bool:
    completed = subprocess.run(
        (
            "sudo", "-u", "postgres", "--", "psql", "--no-psqlrc",
            "--host", "/var/run/postgresql", "--port", str(port),
            "--username", "postgres", "--dbname=postgres",
            "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1",
            "--command",
            "SELECT 1 FROM pg_database "
            f"WHERE datname = '{database}'",
        ),
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=60,
        text=True,
    )
    return completed.stdout.strip() == ""


def _target_residue(target: Mapping[str, object]) -> tuple[str, ...]:
    if target["kind"] == "database":
        authority = target["authority"]
        assert isinstance(authority, Mapping)
        return (
            str(target["path"]),
            str(authority["record_path"]),
        )
    return (str(target["path"]),)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _refused(request: HostRequest) -> HostResult:
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "refused",
        "cleanup-preflight",
        (),
        {},
        {},
        {},
        (),
        ("recompute an exact cleanup plan before retrying",),
        (),
    )


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


__all__ = ["cleanup"]
