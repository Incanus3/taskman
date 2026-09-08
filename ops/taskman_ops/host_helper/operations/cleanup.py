"""Confirmed deletion of exact stale Taskman-owned host artifacts."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import shutil
import stat

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION
from taskman_ops.releases.identifiers import validate_release_id

from ..backups import retained_backup_ids
from ..lock import LifecycleLockContention, lifecycle_lock
from ..paths import ManagedPaths, PathAuthorityError
from ..records import BACKUP_ID_RE
from ..state import HostState, StateAmbiguityError, observe_host_state


_PARAMETER_KEYS = frozenset({"action", "targets", "release_retention", "backup_retention"})
_EXPECTED_KEYS = frozenset({"selected_release_id"})
_TARGET_KEYS = frozenset({"kind", "identifier", "path"})
_LOCK_TIMEOUT_SECONDS = 5.0
_INCOMPLETE_DUMP_RE = re.compile(r"backup-[0-9a-f]{32}\.dump\Z")


def cleanup(request: HostRequest) -> HostResult:
    """Inspect or delete only targets derived from one coherent host state."""

    state: HostState | None = None
    try:
        action, requested, release_retention, backup_retention, expected_selected = _inputs(request)
        paths = ManagedPaths.from_mapping(request.paths)
        _validate_authoritative_paths(paths)
        with lifecycle_lock(paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
            state = observe_host_state(paths)
            _normalize_incomplete_backups(paths, state)
            state = observe_host_state(paths)
            if state.selected_release_id != expected_selected:
                return _result(
                    request,
                    "refused",
                    "current selection changed; inspect and confirm a new cleanup plan",
                    _state_projection(state),
                    state.warnings,
                )
            planned = _plan(state, paths, release_retention, backup_retention)
            if action == "inspect":
                if requested:
                    return _result(request, "refused", "inspection must not include cleanup targets", _state_projection(state))
                return _result(
                    request,
                    "succeeded",
                    "cleanup targets inspected",
                    {**_state_projection(state), "targets": planned},
                    state.warnings,
                )
            active = _confirmed_remaining(requested, planned, paths)
            if active is None:
                return _result(
                    request,
                    "refused",
                    "cleanup targets changed; inspect and confirm a new plan",
                    _state_projection(state),
                    state.warnings,
                )
            changed = False
            for target in active:
                changed = _delete_target(target, state, paths) or changed
            final_state = observe_host_state(paths)
    except LifecycleLockContention:
        return _result(request, "retryable", "lifecycle lock is unavailable", {"locked": True})
    except StateAmbiguityError:
        return _result(request, "manual", "cleanup authority is ambiguous", _state_projection(state))
    except (PathAuthorityError, TypeError, ValueError):
        return _result(request, "refused", "cleanup request is unsafe", _state_projection(state))
    except OSError:
        return _result(
            request,
            "retryable",
            "cleanup did not complete; rerun to converge",
            _state_projection(state),
        )

    return _result(
        request,
        "succeeded",
        "cleanup completed" if changed else "nothing remains to clean",
        {**_state_projection(final_state), "changed": changed},
        final_state.warnings,
    )


def _inputs(
    request: HostRequest,
) -> tuple[str, tuple[dict[str, object], ...], int, int, str | None]:
    if not isinstance(request, HostRequest):
        raise TypeError("cleanup needs a host request")
    if set(request.parameters) != _PARAMETER_KEYS or set(request.expected_state) != _EXPECTED_KEYS:
        raise ValueError("cleanup request is incomplete")
    action = request.parameters["action"]
    raw_targets = request.parameters["targets"]
    release_retention = request.parameters["release_retention"]
    backup_retention = request.parameters["backup_retention"]
    expected_selected = request.expected_state["selected_release_id"]
    if (
        action not in {"inspect", "execute"}
        or not isinstance(raw_targets, tuple)
        or len(raw_targets) > 64
        or type(release_retention) is not int
        or not 1 <= release_retention <= 64
        or type(backup_retention) is not int
        or not 1 <= backup_retention <= 64
        or (expected_selected is not None and type(expected_selected) is not str)
    ):
        raise ValueError("cleanup request is invalid")
    targets = tuple(_target(item) for item in raw_targets)
    identities = tuple(_identity(item) for item in targets)
    if len(set(identities)) != len(identities):
        raise ValueError("cleanup target is duplicated")
    return action, targets, release_retention, backup_retention, expected_selected


def _target(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _TARGET_KEYS:
        raise ValueError("cleanup target is invalid")
    kind = value["kind"]
    identifier = value["identifier"]
    path = value["path"]
    if (
        kind not in {"release", "backup", "temporary"}
        or type(identifier) is not str
        or type(path) is not str
        or not Path(path).is_absolute()
        or "\x00" in path
    ):
        raise ValueError("cleanup target is invalid")
    if kind == "release":
        validate_release_id(identifier)
    elif kind == "backup" and BACKUP_ID_RE.fullmatch(identifier) is None:
        raise ValueError("cleanup backup identifier is invalid")
    elif kind == "temporary" and Path(path).name != identifier:
        raise ValueError("cleanup temporary target is invalid")
    return {"kind": kind, "identifier": identifier, "path": path}


def _plan(
    state: HostState,
    paths: ManagedPaths,
    release_retention: int,
    backup_retention: int,
) -> tuple[dict[str, object], ...]:
    protected_releases = {state.selected_release_id} if state.selected_release_id else set()
    recent_selections = state.selections[-release_retention:]
    protected_releases.update(record.release_id for record in recent_selections)
    protected_releases.update(
        record.previous_release_id for record in recent_selections if record.previous_release_id
    )
    protected_backups = retained_backup_ids(state, backup_retention)
    protected_releases.update(
        record.source_release_id for record in state.backups if record.backup_id in protected_backups
    )

    targets: list[dict[str, object]] = []
    for record in state.releases:
        if record.release_id not in protected_releases:
            targets.append(
                {
                    "kind": "release",
                    "identifier": record.release_id,
                    "path": paths.local(paths.release_root / record.release_id).as_posix(),
                }
            )
    for record in state.backups:
        if record.backup_id not in protected_backups:
            targets.append(
                {
                    "kind": "backup",
                    "identifier": record.backup_id,
                    "path": paths.local(paths.backup_root / f"{record.backup_id}.dump").as_posix(),
                }
            )
    for temporary in state.temporary_paths:
        path = paths.local(temporary)
        targets.append({"kind": "temporary", "identifier": path.name, "path": path.as_posix()})
    return tuple(sorted(targets, key=lambda item: _identity(item)))


def _confirmed_remaining(
    requested: tuple[dict[str, object], ...],
    planned: tuple[dict[str, object], ...],
    paths: ManagedPaths,
) -> tuple[dict[str, object], ...] | None:
    planned_by_identity = {_identity(target): target for target in planned}
    requested_identities = {_identity(target) for target in requested}
    if not set(planned_by_identity).issubset(requested_identities):
        return None
    active: list[dict[str, object]] = []
    for target in requested:
        planned_target = planned_by_identity.get(_identity(target))
        if planned_target is not None:
            active.append(planned_target)
        elif not _target_absent(target, paths):
            return None
    return tuple(active)


def _target_absent(target: Mapping[str, object], paths: ManagedPaths) -> bool:
    path = Path(str(target["path"]))
    if target["kind"] == "backup":
        manifest = Path(paths.local(paths.backup_manifest(str(target["identifier"]))))
        return not (path.exists() or path.is_symlink() or manifest.exists() or manifest.is_symlink())
    return not (path.exists() or path.is_symlink())


def _normalize_incomplete_backups(paths: ManagedPaths, state: HostState) -> None:
    """Remove only observed, selection-safe manifest-less backup dumps."""

    if not isinstance(state, HostState):
        raise TypeError("incomplete backup normalization needs observed host state")
    root = Path(paths.local(paths.backup_root))
    for temporary in state.temporary_paths:
        entry = Path(paths.local(temporary))
        if entry.parent != root or not _INCOMPLETE_DUMP_RE.fullmatch(entry.name):
            continue
        manifest = root / f"{entry.stem}.json"
        if manifest.exists() or manifest.is_symlink():
            continue
        _safe_file(entry)
        entry.unlink()
        _fsync_directory(root)


def _validate_authoritative_paths(paths: ManagedPaths) -> None:
    """Classify unsafe managed roots as ambiguity before lock acquisition."""

    try:
        paths.validate_existing(owner_uid=os.geteuid())
    except PathAuthorityError as error:
        raise StateAmbiguityError("managed cleanup authority is unsafe") from error


def _delete_target(target: Mapping[str, object], state: HostState, paths: ManagedPaths) -> bool:
    kind = str(target["kind"])
    identifier = str(target["identifier"])
    path = Path(str(target["path"]))
    if kind == "release":
        expected = Path(paths.local(paths.release_root / identifier))
        if path != expected or identifier == state.selected_release_id:
            raise ValueError("cleanup release target is not authoritative")
        if not (path.exists() or path.is_symlink()):
            return False
        _safe_directory(path)
        shutil.rmtree(path)
        _fsync_directory(path.parent)
        return True
    if kind == "backup":
        expected = Path(paths.local(paths.backup_root / f"{identifier}.dump"))
        manifest = Path(paths.local(paths.backup_manifest(identifier)))
        if path != expected or not any(record.backup_id == identifier for record in state.backups):
            raise ValueError("cleanup backup target is not authoritative")
        changed = False
        if manifest.exists() or manifest.is_symlink():
            _safe_file(manifest)
            manifest.unlink()
            changed = True
        if path.exists() or path.is_symlink():
            _safe_file(path)
            path.unlink()
            changed = True
        if changed:
            _fsync_directory(path.parent)
        return changed
    if path.as_posix() not in {item.as_posix() for item in state.temporary_paths}:
        raise ValueError("cleanup temporary target is not authoritative")
    if not (path.exists() or path.is_symlink()):
        return False
    _safe_file(path)
    path.unlink()
    _fsync_directory(path.parent)
    return True


def _safe_directory(path: Path) -> None:
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_mode & 0o7022
    ):
        raise StateAmbiguityError("cleanup release path is unsafe")


def _safe_file(path: Path) -> None:
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_mode & 0o7022
    ):
        raise StateAmbiguityError("cleanup file path is unsafe")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _identity(target: Mapping[str, object]) -> tuple[str, str, str]:
    return (str(target["kind"]), str(target["identifier"]), str(target["path"]))


def _state_projection(state: HostState | None) -> dict[str, object]:
    if state is None:
        return {}
    return {
        "selected_release_id": state.selected_release_id,
        "service_state": state.service_state,
        "database_state": state.database_state,
    }


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


__all__ = ["cleanup"]
