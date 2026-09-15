"""Filesystem-only inspection and confirmed deletion of stale managed artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Mapping
from pathlib import Path

from taskman_ops.host_protocol import (
    MAX_COLLECTION_ITEMS,
    HostRequest,
    HostResult,
    ProtocolError,
    encode_result,
)
from taskman_ops.releases.identifiers import validate_release_id

from ..backups import BackupAuthorityError, delete_completed_backup, retained_backup_ids
from ..filesystem import fsync_directory
from ..lock import LifecycleLockContention, lifecycle_lock
from ..paths import ManagedPaths, PathAuthorityError
from ..records import BACKUP_ID_RE, BackupRecord
from ..state import (
    HostState,
    StateAmbiguityError,
    mutation_observation_availability,
    mutation_observations,
    observe_host_state,
)

_PARAMETER_KEYS = frozenset(
    {"action", "targets", "release_retention", "backup_retention", "cursor"}
)
_EXPECTED_KEYS = frozenset(
    {
        "selected_release_id",
        "last_successful_selection_id",
        "backup_protection_sha256",
        "restore_target_sha256",
    }
)
_TARGET_KEYS = frozenset({"kind", "identifier", "path"})
_LOCK_TIMEOUT_SECONDS = 5.0


class _InvalidInput(ValueError):
    pass


class _ExpectedState(ValueError):
    pass


def cleanup(request: HostRequest) -> HostResult:
    """Inspect one stable eligible inventory or execute one confirmed batch."""

    state: HostState | None = None
    action = "inspect"
    completed: list[dict[str, object]] = []
    mutation_state = "unchanged"
    warnings: tuple[str, ...] = ()
    try:
        action, requested, release_retention, backup_retention, cursor = _inputs(
            request
        )
        paths = ManagedPaths.from_mapping(request.paths)
        _validate_authoritative_paths(paths)
        with lifecycle_lock(paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
            state = observe_host_state(paths, allow_selection_transition=True)
            planned, plan_warnings = _plan(
                state, paths, release_retention, backup_retention
            )
            warnings = tuple(sorted({*state.warnings, *plan_warnings}))[
                :MAX_COLLECTION_ITEMS
            ]
            facts = mutation_observations(state, "cleanup")
            digest = _inventory_sha256(facts, planned)
            if action == "inspect":
                return _inspection_page(
                    request, planned, facts, digest, cursor, warnings
                )
            if dict(request.expected_state) != facts:
                raise _ExpectedState
            active = _confirmed_targets(requested, planned, paths)
            for target, absent in active:
                if not absent:
                    mutation_state = "unknown"
                    _delete_target(target, state, paths)
                    mutation_state = "changed"
                completed.append(target)
            final_state = observe_host_state(paths, allow_selection_transition=True)
        final_warnings = tuple(sorted({*final_state.warnings, *plan_warnings}))[
            :MAX_COLLECTION_ITEMS
        ]
        return _mutation_result(
            request,
            "succeeded",
            "cleanup completed"
            if mutation_state == "changed"
            else "nothing remains to clean",
            final_state,
            mutation_state=mutation_state,
            completed=completed,
            warnings=final_warnings,
        )
    except LifecycleLockContention:
        return _mutation_result(
            request,
            "retryable",
            "lifecycle lock is unavailable",
            None,
            mutation_state=mutation_state,
            completed=completed,
            boundary="lock",
            inspection_error="lock-unavailable",
            warnings=warnings,
        )
    except _InvalidInput:
        return _mutation_result(
            request,
            "refused",
            "cleanup request is unsafe",
            state,
            mutation_state=mutation_state,
            completed=completed,
            boundary="input",
            warnings=warnings,
        )
    except _ExpectedState:
        return _mutation_result(
            request,
            "refused",
            "cleanup references changed; inspect and confirm a new plan",
            state,
            mutation_state=mutation_state,
            completed=completed,
            boundary="expected_state",
            warnings=warnings,
        )
    except (BackupAuthorityError, PathAuthorityError, StateAmbiguityError):
        observed = None if mutation_state != "unchanged" else state
        return _mutation_result(
            request,
            "manual",
            "cleanup authority is ambiguous",
            observed,
            mutation_state=mutation_state,
            completed=completed,
            boundary="authority",
            warnings=warnings,
        )
    except (OSError, ProtocolError, TypeError, ValueError):
        observed = None if mutation_state != "unchanged" else state
        return _mutation_result(
            request,
            "retryable" if action == "execute" else "refused",
            "cleanup did not complete; inspect and confirm a new plan",
            observed,
            mutation_state=mutation_state,
            completed=completed,
            boundary="cleanup",
            warnings=warnings,
        )


def _inputs(
    request: HostRequest,
) -> tuple[str, tuple[dict[str, object], ...], int, int, Mapping[str, object] | None]:
    if (
        not isinstance(request, HostRequest)
        or set(request.parameters) != _PARAMETER_KEYS
    ):
        raise _InvalidInput
    action = request.parameters["action"]
    raw_targets = request.parameters["targets"]
    release_retention = request.parameters["release_retention"]
    backup_retention = request.parameters["backup_retention"]
    cursor = _cursor(request.parameters["cursor"])
    if (
        action not in {"inspect", "execute"}
        or not isinstance(raw_targets, tuple)
        or len(raw_targets) > MAX_COLLECTION_ITEMS
        or type(release_retention) is not int
        or not 1 <= release_retention <= 64
        or type(backup_retention) is not int
        or not 1 <= backup_retention <= 64
    ):
        raise _InvalidInput
    targets = tuple(_target(item) for item in raw_targets)
    identities = tuple(_identity(item) for item in targets)
    if identities != tuple(sorted(set(identities))):
        raise _InvalidInput
    if action == "inspect":
        if request.expected_state or targets:
            raise _InvalidInput
    elif set(request.expected_state) != _EXPECTED_KEYS or cursor is not None:
        raise _InvalidInput
    return action, targets, release_retention, backup_retention, cursor


def _cursor(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"inventory_sha256", "after_id"}:
        raise _InvalidInput
    digest, after_id = value["inventory_sha256"], value["after_id"]
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or type(after_id) is not str
    ):
        raise _InvalidInput
    try:
        identity = json.loads(after_id)
    except (TypeError, ValueError):
        raise _InvalidInput from None
    if (
        not isinstance(identity, list)
        or len(identity) != 3
        or not all(type(item) is str for item in identity)
        or _canonical_identity(tuple(identity)) != after_id
    ):
        raise _InvalidInput
    return {"inventory_sha256": digest, "after_id": after_id}


def _target(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _TARGET_KEYS:
        raise _InvalidInput
    kind, identifier, path = (value[key] for key in ("kind", "identifier", "path"))
    if (
        kind not in {"release", "backup", "temporary"}
        or type(identifier) is not str
        or type(path) is not str
        or not Path(path).is_absolute()
        or "\x00" in path
    ):
        raise _InvalidInput
    if kind == "release":
        validate_release_id(identifier)
    elif (
        kind == "backup"
        and BACKUP_ID_RE.fullmatch(identifier) is None
        or kind == "temporary"
        and Path(path).name != identifier
    ):
        raise _InvalidInput
    return {"kind": kind, "identifier": identifier, "path": path}


def _plan(
    state: HostState, paths: ManagedPaths, release_retention: int, backup_retention: int
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    unfinished = (
        state.latest_successful_selection is None
        or state.selected_release_id != state.latest_successful_selection.release_id
        or bool(state.backup_protections)
        or bool(state.retiring_backup_protections)
        or state.restore_target is not None
    )
    protected_releases = (
        {record.release_id for record in state.releases}
        if unfinished
        else {state.selected_release_id}
    )
    recent = state.selections[-release_retention:]
    protected_releases.update(record.release_id for record in recent)
    protected_releases.update(
        record.previous_release_id for record in recent if record.previous_release_id
    )
    protected_backups = retained_backup_ids(state, backup_retention)
    protected_releases.update(
        record.source_release_id
        for record in state.backups
        if record.backup_id in protected_backups
    )
    targets: list[dict[str, object]] = []
    warnings: list[str] = []
    for record in state.releases:
        if record.release_id not in protected_releases:
            targets.append(_release_target(paths, record.release_id))
    for record in state.backups:
        if record.backup_id not in protected_backups:
            if _valid_backup_pair(paths, record):
                targets.append(_backup_target(paths, record.backup_id))
            else:
                warnings.append(f"damaged backup preserved: {record.backup_id}")
    for temporary in state.temporary_paths:
        target = _temporary_target(paths, Path(paths.local(temporary)))
        if target is not None:
            targets.append(target)
    return tuple(sorted(targets, key=_identity)), tuple(warnings)


def _valid_backup_pair(paths: ManagedPaths, record: BackupRecord) -> bool:
    root = Path(paths.local(paths.backup_root))
    manifest, dump = (
        root / f"{record.backup_id}.json",
        root / f"{record.backup_id}.dump",
    )
    try:
        for path in (manifest, dump):
            details = path.lstat()
            if (
                stat.S_ISLNK(details.st_mode)
                or not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.geteuid()
                or stat.S_IMODE(details.st_mode) != 0o600
            ):
                return False
        return dump.stat().st_size > 0 and _sha256_file(dump) == record.dump_sha256
    except OSError:
        return False


def _inspection_page(
    request: HostRequest,
    targets: tuple[dict[str, object], ...],
    facts: Mapping[str, object],
    digest: str,
    cursor: Mapping[str, object] | None,
    warnings: tuple[str, ...],
) -> HostResult:
    start = 0
    if cursor is not None:
        if cursor["inventory_sha256"] != digest:
            raise _ExpectedState
        identities = tuple(_canonical_identity(_identity(item)) for item in targets)
        try:
            start = identities.index(cursor["after_id"]) + 1
        except ValueError:
            raise _InvalidInput from None
    page: list[dict[str, object]] = []
    for target in targets[start:]:
        if len(page) == MAX_COLLECTION_ITEMS:
            break
        candidate = [*page, target]
        more = start + len(candidate) < len(targets)
        next_cursor = (
            {
                "inventory_sha256": digest,
                "after_id": _canonical_identity(_identity(candidate[-1])),
            }
            if more
            else None
        )
        result = HostResult.for_request(
            request,
            "succeeded",
            "cleanup targets inspected",
            {
                **facts,
                "targets": candidate,
                "inventory_sha256": digest,
                "next_cursor": next_cursor,
            },
            warnings,
        )
        try:
            encode_result(result)
        except ProtocolError:
            if not page:
                raise StateAmbiguityError(
                    "cleanup target cannot fit one result page"
                ) from None
            break
        page.append(target)
    more = start + len(page) < len(targets)
    next_cursor = (
        {
            "inventory_sha256": digest,
            "after_id": _canonical_identity(_identity(page[-1])),
        }
        if more and page
        else None
    )
    return HostResult.for_request(
        request,
        "succeeded",
        "cleanup targets inspected",
        {
            **facts,
            "targets": tuple(page),
            "inventory_sha256": digest,
            "next_cursor": next_cursor,
        },
        warnings,
    )


def _inventory_sha256(
    facts: Mapping[str, object], targets: tuple[dict[str, object], ...]
) -> str:
    return hashlib.sha256(
        _canonical_ascii({"confirmation_facts": facts, "targets": targets})
    ).hexdigest()


def _confirmed_targets(
    requested: tuple[dict[str, object], ...],
    planned: tuple[dict[str, object], ...],
    paths: ManagedPaths,
) -> tuple[tuple[dict[str, object], bool], ...]:
    planned_by_identity = {_identity(target): target for target in planned}
    result: list[tuple[dict[str, object], bool]] = []
    for target in requested:
        planned_target = planned_by_identity.get(_identity(target))
        if planned_target is not None:
            result.append((planned_target, False))
        elif _target_absent(target, paths):
            result.append((target, True))
        else:
            raise _ExpectedState
    return tuple(result)


def _target_absent(target: Mapping[str, object], paths: ManagedPaths) -> bool:
    path = Path(str(target["path"]))
    identifier = str(target["identifier"])
    if target["kind"] == "release":
        if path != Path(paths.local(paths.release_root / identifier)):
            return False
    elif target["kind"] == "backup":
        if path != Path(paths.local(paths.backup_root / f"{identifier}.dump")):
            return False
        manifest = Path(paths.local(paths.backup_manifest(str(target["identifier"]))))
        return not any(item.exists() or item.is_symlink() for item in (path, manifest))
    elif _temporary_target(paths, path) != dict(target):
        return False
    return not (path.exists() or path.is_symlink())


def _delete_target(
    target: Mapping[str, object], state: HostState, paths: ManagedPaths
) -> None:
    kind, identifier, path = (
        str(target["kind"]),
        str(target["identifier"]),
        Path(str(target["path"])),
    )
    if kind == "release":
        if path != Path(paths.local(paths.release_root / identifier)):
            raise _ExpectedState
        _safe_directory(path)
        shutil.rmtree(path)
    elif kind == "backup":
        if path != Path(paths.local(paths.backup_root / f"{identifier}.dump")):
            raise _ExpectedState
        record = next(
            (item for item in state.backups if item.backup_id == identifier), None
        )
        if record is None:
            raise _ExpectedState
        delete_completed_backup(paths, record)
        return
    else:
        allowed = {
            _identity(item)
            for temporary in state.temporary_paths
            if (item := _temporary_target(paths, Path(paths.local(temporary))))
            is not None
        }
        if _identity(target) not in allowed:
            raise _ExpectedState
        if path.parent == Path(paths.local(paths.release_root)):
            _safe_directory(path)
            shutil.rmtree(path)
        else:
            _safe_file(path)
            path.unlink()
    fsync_directory(path.parent)


def _release_target(paths: ManagedPaths, identifier: str) -> dict[str, object]:
    return {
        "kind": "release",
        "identifier": identifier,
        "path": Path(paths.local(paths.release_root / identifier)).as_posix(),
    }


def _backup_target(paths: ManagedPaths, identifier: str) -> dict[str, object]:
    return {
        "kind": "backup",
        "identifier": identifier,
        "path": Path(paths.local(paths.backup_root / f"{identifier}.dump")).as_posix(),
    }


def _temporary_target(paths: ManagedPaths, path: Path) -> dict[str, object] | None:
    release_root, backup_root = (
        Path(paths.local(paths.release_root)),
        Path(paths.local(paths.backup_root)),
    )
    if path.parent == release_root and path.name.startswith(".release-"):
        return {"kind": "temporary", "identifier": path.name, "path": path.as_posix()}
    if path.parent == backup_root and (
        path.name.endswith(".dump.tmp")
        or (BACKUP_ID_RE.fullmatch(path.stem) is not None and path.suffix == ".dump")
    ):
        return {"kind": "temporary", "identifier": path.name, "path": path.as_posix()}
    return None


def _validate_authoritative_paths(paths: ManagedPaths) -> None:
    try:
        paths.validate_existing(owner_uid=os.geteuid())
    except PathAuthorityError as error:
        raise StateAmbiguityError("managed cleanup authority is unsafe") from error


def _safe_directory(path: Path) -> None:
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_mode & 0o7022
    ):
        raise StateAmbiguityError("cleanup directory is unsafe")


def _safe_file(path: Path) -> None:
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_mode & 0o7022
    ):
        raise StateAmbiguityError("cleanup file is unsafe")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mutation_result(
    request: HostRequest,
    outcome: str,
    message: str,
    state: HostState | None,
    *,
    mutation_state: str,
    completed: list[dict[str, object]],
    boundary: str | None = None,
    inspection_error: str | None = None,
    warnings: tuple[str, ...] = (),
) -> HostResult:
    if state is None:
        observations = {
            "selected_release_id": None,
            "last_successful_selection_id": None,
            "backup_protection_sha256": None,
            "restore_target_sha256": None,
        }
        unavailable = tuple(sorted(observations))
        inspection_error = inspection_error or "inspection-failed"
    else:
        observations = mutation_observations(state, "cleanup")
        unavailable, observed_error = mutation_observation_availability(
            "cleanup", observations
        )
        inspection_error = inspection_error or observed_error
    exit_code = (
        0
        if outcome == "succeeded"
        else {
            "input": 2,
            "lock": 12,
            "authority": 10,
            "expected_state": 10,
            "cleanup": 10,
        }.get(boundary, 10)
    )
    return HostResult.for_request(
        request,
        outcome,
        message,
        {
            "mutation_state": mutation_state,
            "exit_code": exit_code,
            "failed_boundary": None if outcome == "succeeded" else boundary,
            "observations": observations,
            "unavailable_fields": unavailable,
            "inspection_error": inspection_error,
            "report": None,
            "completed_targets": tuple(sorted(completed, key=_identity)),
        },
        warnings,
    )


def _identity(target: Mapping[str, object]) -> tuple[str, str, str]:
    return tuple(str(target[key]) for key in ("kind", "identifier", "path"))


def _canonical_identity(identity: tuple[str, str, str]) -> str:
    return json.dumps(identity, ensure_ascii=True, separators=(",", ":"))


def _canonical_ascii(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


__all__ = ["cleanup"]
