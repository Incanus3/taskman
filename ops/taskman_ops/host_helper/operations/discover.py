"""Read-only projections of the completed host state."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from taskman_ops.host_protocol import (
    MAX_COLLECTION_ITEMS,
    HostRequest,
    HostResult,
    PROTOCOL_VERSION,
    ProtocolError,
    encode_result,
)

from ..commands import CommandError
from ...checksums import sha256_file
from ..credentials import validate_credentials
from ..database import database_mapping, observe_database_state, release_migration_versions
from ..lock import LifecycleLockContention, lifecycle_lock
from ..paths import ManagedPaths, PathAuthorityError
from ..state import HostState, StateAmbiguityError, observe_host_state


_SNAPSHOT_TIMEOUT_SECONDS = 5.0
_DISCOVERY_PARAMETERS = frozenset({"credentials_path", "database", "mode"})
_RESTORE_DISCOVERY_PARAMETERS = _DISCOVERY_PARAMETERS | {"backup_id"}
_DISCOVERY_MODES = frozenset({"strict", "deploy", "provision", "restore"})
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SCHEDULED_BACKUP = Path("/usr/local/lib/taskman/taskman-backup.pyz")
_BACKUP_TIMER = "taskman-backup.timer"


class _InvalidCursor(ValueError):
    """A syntactically invalid page position, distinct from host ambiguity."""


def discover(request: HostRequest) -> HostResult:
    """Return one bounded projection of completed records and physical state."""

    try:
        state, scheduler = _observe(request)
    except LifecycleLockContention:
        return _locked(request)
    except (CommandError, PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
        return _refused(request)
    mode = request.parameters["mode"]
    if mode == "restore":
        # Refuse until native restore-database identity can be observed;
        # unknown authority cannot be represented as absence.
        return _refused(request)
    projection = _discovery_state(state)
    if mode in {"deploy", "provision"}:
        assert scheduler is not None
        projection.update(_deployment_projection(state, scheduler, mode=mode))
    return _success(request, projection, state.warnings)


def list_releases(request: HostRequest) -> HostResult:
    """Return completed release manifests from one coherent snapshot."""

    try:
        state, _scheduler = _observe(request)
        cursor = _inventory_cursor(request, "list_releases")
    except _InvalidCursor:
        return _invalid_cursor(request)
    except LifecycleLockContention:
        return _locked(request)
    except (PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
        return _refused(request)
    records = tuple(
        {"id": record.release_id, "record": record.to_mapping()}
        for record in state.releases
    )
    return _inventory_page(request, records, cursor, state.warnings)


def list_backups(request: HostRequest) -> HostResult:
    """Return validated backup manifests from one coherent snapshot."""

    try:
        state, _scheduler = _observe(request)
        cursor = _inventory_cursor(request, "list_backups")
    except _InvalidCursor:
        return _invalid_cursor(request)
    except LifecycleLockContention:
        return _locked(request)
    except (PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
        return _refused(request)
    records = tuple(
        {"id": record.backup_id, "record": record.to_mapping()}
        for record in state.backups
    )
    return _inventory_page(request, records, cursor, state.warnings)


def _observe(request: HostRequest) -> tuple[HostState, dict[str, object] | None]:
    paths = ManagedPaths.from_mapping(request.paths)
    # The lock is held only while the completed records and physical selection
    # are read.  Health/readiness commands run after this snapshot is released.
    with lifecycle_lock(paths, timeout_seconds=_SNAPSHOT_TIMEOUT_SECONDS):
        if request.operation == "discover":
            if set(request.expected_state):
                raise ValueError("discovery request is incomplete")
            mode = request.parameters.get("mode")
            if type(mode) is not str or mode not in _DISCOVERY_MODES:
                raise ValueError("discovery mode is invalid")
            expected_parameters = (
                _RESTORE_DISCOVERY_PARAMETERS if mode == "restore" else _DISCOVERY_PARAMETERS
            )
            if set(request.parameters) != expected_parameters:
                raise ValueError("discovery request is incomplete")
            backup_id = request.parameters.get("backup_id")
            if mode == "restore":
                if type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None:
                    raise ValueError("restore backup identifier is invalid")
            elif backup_id is not None:
                raise ValueError("discovery backup identifier is invalid")
            credentials = request.parameters["credentials_path"]
            if type(credentials) is not str or not credentials.startswith("/"):
                raise ValueError("discovery credentials path is invalid")
            credentials_path = Path(credentials)
            validate_credentials(credentials_path)
            database = database_mapping(request.parameters["database"])
            observation = observe_database_state(database, credentials_path)
            state = observe_host_state(
                paths,
                database=observation,
                allow_selection_transition=mode in {"deploy", "provision", "restore"},
            )
            scheduler = (
                _scheduler_facts(paths) if mode in {"deploy", "provision"} else None
            )
            return state, scheduler
        if request.expected_state or set(request.parameters) != {"cursor"}:
            raise ValueError("listing request is invalid")
        return observe_host_state(
            paths,
            allow_selection_transition=request.operation == "list_releases",
        ), None


def _inventory_cursor(request: HostRequest, operation: str) -> Mapping[str, object] | None:
    if request.operation != operation or request.expected_state or set(request.parameters) != {"cursor"}:
        raise _InvalidCursor("listing request is invalid")
    value = request.parameters["cursor"]
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"inventory_sha256", "after_id"}:
        raise _InvalidCursor("inventory cursor is invalid")
    digest = value["inventory_sha256"]
    after_id = value["after_id"]
    if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None or type(after_id) is not str:
        raise _InvalidCursor("inventory cursor is invalid")
    try:
        if operation == "list_releases":
            from taskman_ops.releases.identifiers import validate_release_id

            validate_release_id(after_id)
        elif _BACKUP_ID_RE.fullmatch(after_id) is None:
            raise _InvalidCursor("inventory cursor is invalid")
    except (TypeError, ValueError) as error:
        raise _InvalidCursor("inventory cursor is invalid") from error
    return value


def _inventory_page(
    request: HostRequest,
    records: tuple[dict[str, object], ...],
    cursor: Mapping[str, object] | None,
    warnings: tuple[str, ...],
) -> HostResult:
    if tuple(item["id"] for item in records) != tuple(sorted(item["id"] for item in records)):
        return _refused(request)
    digest = _inventory_sha256(request.operation, records)
    start = 0
    if cursor is not None:
        if cursor["inventory_sha256"] != digest:
            return HostResult.for_request(request, "refused", "inventory-changed", {}, ())
        ids = tuple(item["id"] for item in records)
        try:
            start = ids.index(cursor["after_id"]) + 1
        except ValueError:
            return HostResult.for_request(
                request,
                "refused",
                "invalid inventory cursor",
                {"invalid_request": True},
                (),
            )

    page: list[dict[str, object]] = []
    for entry in records[start:]:
        if len(page) == MAX_COLLECTION_ITEMS:
            break
        candidate = [*page, entry]
        more = start + len(candidate) < len(records)
        next_cursor = (
            {"inventory_sha256": digest, "after_id": candidate[-1]["id"]}
            if more
            else None
        )
        result = HostResult.for_request(
            request,
            "succeeded",
            "host inventory observed",
            {
                "records": candidate,
                "inventory_sha256": digest,
                "next_cursor": next_cursor,
            },
            warnings,
        )
        try:
            encode_result(result)
        except ProtocolError:
            if not page:
                return _projection_refusal(request)
            break
        page.append(entry)

    more = start + len(page) < len(records)
    next_cursor = (
        {"inventory_sha256": digest, "after_id": page[-1]["id"]}
        if more and page
        else None
    )
    return _success(
        request,
        {"records": tuple(page), "inventory_sha256": digest, "next_cursor": next_cursor},
        warnings,
    )


def _inventory_sha256(operation: str, records: tuple[dict[str, object], ...]) -> str:
    digest = hashlib.sha256()
    digest.update(b'{"operation":')
    digest.update(_canonical_ascii(operation))
    digest.update(b',"records":[')
    for index, record in enumerate(records):
        if index:
            digest.update(b",")
        digest.update(_canonical_ascii(record))
    digest.update(b"]}")
    return digest.hexdigest()


def _canonical_ascii(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _deployment_projection(
    state: HostState, scheduler: Mapping[str, object], *, mode: str
) -> dict[str, object]:
    protections = tuple(
        sorted(
            (*state.backup_protections, *state.retiring_backup_protections),
            key=lambda item: item.backup_id,
        )
    )
    protection_rows = tuple(item.to_mapping() for item in protections)
    baseline_ids = {
        *(
            (state.selected_release_id,)
            if state.selected_release_id is not None
            else ()
        ),
        *(
            (state.latest_successful_selection.release_id,)
            if state.latest_successful_selection is not None
            else ()
        ),
        *(item.target_release_id for item in protections),
    }
    if (
        mode == "provision"
        and state.selected_release_id is None
        and state.latest_successful_selection is None
        and state.applied_migrations
    ):
        applied = frozenset(state.applied_migrations)
        baseline_ids.update(
            record.release_id
            for record in state.releases
            if applied.intersection(release_migration_versions(record.migrations))
        )
    return {
        "backup_protections": protection_rows,
        "backup_protection_sha256": hashlib.sha256(_canonical_ascii(protection_rows)).hexdigest(),
        "downgrade_baseline_sha256": hashlib.sha256(
            _canonical_ascii(sorted(baseline_ids))
        ).hexdigest(),
        **scheduler,
    }


def _scheduler_facts(paths: ManagedPaths) -> dict[str, object]:
    package = Path(paths.local(_SCHEDULED_BACKUP))
    try:
        details = package.lstat()
    except FileNotFoundError:
        package_sha256: str | None = None
    else:
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o750
        ):
            raise StateAmbiguityError("scheduled backup executable is unsafe")
        package_sha256 = sha256_file(package)
    enabled = _systemd_property("UnitFileState")
    if enabled not in {"enabled", "disabled"}:
        raise StateAmbiguityError("backup timer enablement is ambiguous")
    active = _systemd_property("ActiveState")
    timer_state = active if active in {"active", "inactive"} else "unknown"
    return {
        "scheduled_backup_sha256": package_sha256,
        "backup_timer_enabled": enabled == "enabled",
        "backup_timer_state": timer_state,
    }


def _systemd_property(name: str) -> str:
    from ..commands import run_command

    result = run_command(
        ("systemctl", "show", f"--property={name}", "--value", _BACKUP_TIMER),
        timeout_seconds=_SNAPSHOT_TIMEOUT_SECONDS,
        output_limit=64,
    )
    try:
        return result.stdout.decode("ascii", "strict").strip()
    except UnicodeDecodeError as error:
        raise StateAmbiguityError("backup timer state is ambiguous") from error


def _discovery_state(state: HostState) -> dict[str, object]:
    return {
        "selected_release_id": state.selected_release_id,
        "last_successful_selection_id": state.latest_successful_selection_filename,
        "last_successful_selection": (
            None
            if state.latest_successful_selection is None
            else state.latest_successful_selection.to_mapping()
        ),
        "previous_successful_selection": (
            None
            if state.previous_successful_selection is None
            else state.previous_successful_selection.to_mapping()
        ),
        "applied_migrations": state.applied_migrations,
        "service_state": state.service_state,
        "database_state": state.database_state,
    }


def _success(
    request: HostRequest,
    state: Mapping[str, object],
    warnings: tuple[str, ...],
) -> HostResult:
    try:
        result = HostResult(
            protocol_version=PROTOCOL_VERSION,
            operation=request.operation,
            correlation_id=request.correlation_id,
            outcome="succeeded",
            message="host state observed",
            state=state,
            warnings=warnings,
        )
        # HostResult validates item counts and nesting, while the final wire
        # envelope also has a byte bound.  Check both before returning so a
        # valid HostState never falls through the helper's generic exception
        # path when its authoritative projection is too large.
        encode_result(result)
    except ProtocolError:
        return _projection_refusal(request)
    return result


def _projection_refusal(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="refused",
        message="host state projection exceeds helper bounds",
        state={},
        warnings=(),
    )


def _refused(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="refused",
        message="authoritative host state is ambiguous",
        state={},
        warnings=(),
    )


def _invalid_cursor(request: HostRequest) -> HostResult:
    return HostResult.for_request(
        request,
        "refused",
        "invalid inventory cursor",
        {"invalid_request": True},
        (),
    )


def _locked(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="retryable",
        message="lifecycle lock is unavailable",
        state={"locked": True},
        warnings=(),
    )


__all__ = ["discover", "list_backups", "list_releases"]
