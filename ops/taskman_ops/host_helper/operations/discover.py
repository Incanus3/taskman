"""Read-only projections of the completed host state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import grp
import pwd
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

from ..commands import CommandError, run_command
from ...checksums import sha256_file
from ..credentials import validate_credentials
from ..database import (
    database_mapping,
    observe_database_state,
    observe_database_state_or_empty,
    observe_database_state_or_empty_as_admin,
    release_migration_versions,
)
from ..restore_database import observe_restore_databases
from ..restore_target import restore_target_sha256
from ..lock import LifecycleLockContention, lifecycle_lock
from ..backup_protection import independent_backup_ids
from ..paths import ManagedPaths, PathAuthorityError
from ..records import MAX_RECORD_BYTES, BackupRecord, RecordError
from ..state import HostState, StateAmbiguityError, observe_host_state


_SNAPSHOT_TIMEOUT_SECONDS = 5.0
_DISCOVERY_PARAMETERS = frozenset({"credentials_path", "database", "mode"})
_RESTORE_DISCOVERY_PARAMETERS = _DISCOVERY_PARAMETERS | {"backup_id"}
_DISCOVERY_MODES = frozenset({"strict", "deploy", "provision", "restore"})
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SCHEDULED_BACKUP = Path("/usr/local/lib/taskman/taskman-backup.pyz")
_BACKUP_TIMER = "taskman-backup.timer"
_SCHEDULER_RESOURCES = {
    "helper": Path("/usr/local/lib/taskman/taskman-backup.pyz"),
    "service": Path("/etc/systemd/system/taskman-backup.service"),
    "timer": Path("/etc/systemd/system/taskman-backup.timer"),
    "environment": Path("/etc/taskman/taskman-backup.env"),
}
_RESOURCE_DIGEST_KEYS = frozenset(
    {"taskman_service", "backup_environment", "backup_service", "backup_timer"}
)


class _InvalidCursor(ValueError):
    """A syntactically invalid page position, distinct from host ambiguity."""


class _RestoreBackupFailure(ValueError):
    """The specifically requested restore input failed read-only validation."""


def discover(request: HostRequest) -> HostResult:
    """Return one bounded projection of completed records and physical state."""

    try:
        state, scheduler, restore_database_state = _observe(request)
    except LifecycleLockContention:
        return _locked(request)
    except _RestoreBackupFailure:
        return _backup_failure(request)
    except (CommandError, PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
        return _refused(request)
    mode = request.parameters["mode"]
    projection = _discovery_state(state)
    if mode in {"deploy", "provision"}:
        assert scheduler is not None
        projection.update(_deployment_projection(state, scheduler, mode=mode))
    elif mode == "restore":
        assert scheduler is not None and restore_database_state is not None
        projection.update(
            _restore_projection(state, scheduler, restore_database_state)
        )
    return _success(request, projection, state.warnings)


def provision_authority(request: HostRequest) -> HostResult:
    """Validate pre-pyinfra records/current and PostgreSQL authority read-only.

    ``observe_host_state`` is deliberately reused here: it applies the exact
    derived-path and supported-record validators to releases, selections,
    protections, restore binding, and current rather than duplicating JSON
    checks at the provisioning boundary.
    """

    try:
        if request.operation != "provision_authority" or request.expected_state:
            raise ValueError("provision authority request is incomplete")
        if set(request.parameters) != {"database", "postgres_package_track", "resource_digests"}:
            raise ValueError("provision authority request is incomplete")
        package_track = request.parameters["postgres_package_track"]
        if package_track is not None and (type(package_track) is not str or not package_track.isdecimal()):
            raise ValueError("PostgreSQL package track is invalid")
        database = database_mapping(request.parameters["database"])
        resource_digests = request.parameters["resource_digests"]
        if (
            not isinstance(resource_digests, Mapping)
            or set(resource_digests) != _RESOURCE_DIGEST_KEYS
            or any(type(value) is not str or _SHA256_RE.fullmatch(value) is None for value in resource_digests.values())
        ):
            raise ValueError("managed resource digests are invalid")
        paths = ManagedPaths.from_mapping(request.paths)
        install_root_was_absent = not paths.local(paths.install_root).exists()
        with lifecycle_lock(paths, timeout_seconds=_SNAPSHOT_TIMEOUT_SECONDS):
            _validate_managed_resources(
                paths, resource_digests,
                allow_lock_created_install_root=install_root_was_absent,
            )
            state = observe_host_state(paths, allow_selection_transition=True)
            database_state = _observe_postgresql_authority(database, package_track)
            # A provision plan may only call an existing database empty when
            # the controlled-template probe proved it.  Preserve observed
            # migrations as authority; missing observation is never an empty
            # tuple for the convenience of first-install planning.
            if database_state == "absent":
                # A missing cluster/database is a planned provisioning delta,
                # not an observed empty schema.  Do not touch absent
                # credentials or manufacture controlled-template evidence.
                database_observation = {
                    "state": "absent",
                    "applied_migrations": (),
                    "initial_empty": False,
                }
            else:
                credentials = Path("/etc/taskman/pgpass")
                try:
                    credentials.lstat()
                except FileNotFoundError:
                    # The controller proves its supplied pgpass through a
                    # sensitive, read-only authentication command before
                    # pyinfra. Keep this catalog observation secret-free so
                    # supplied credential bytes never enter the wire result,
                    # request, or diagnostics.
                    database_observation = observe_database_state_or_empty_as_admin(database)
                else:
                    validate_credentials(credentials)
                    database_observation = observe_database_state_or_empty(database, credentials)
                if database_observation["state"] != database_state:
                    raise ValueError("PostgreSQL authority observations disagree")
            state = replace(
                state,
                database_state=database_state,
                applied_migrations=tuple(database_observation["applied_migrations"]),
                initial_database_empty=database_observation["initial_empty"],
            )
    except LifecycleLockContention:
        return _locked(request)
    except (CommandError, PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
        return _refused(request)
    # The controller separately obtains complete installed records through the
    # paged inventory operation.  This projection binds the material summary
    # to that inventory without putting an unbounded release list into a
    # privileged read-only response.
    projection = _discovery_state(state)
    projection.update(_deployment_projection(state, _scheduler_facts(paths), mode="provision"))
    release_rows = tuple(record.to_mapping() for record in state.releases)
    projection.update(
        {
            "authority": "validated",
            "initial_database_empty": database_observation["initial_empty"],
            "installed_release_count": len(release_rows),
            "installed_release_sha256": hashlib.sha256(_canonical_ascii(release_rows)).hexdigest(),
            # Per-resource presence is the only scheduler state generic
            # convergence may act on. Existing resources remain under the
            # locked scheduler refresh protocol during genesis.
            "scheduler_resources": _scheduler_resources(paths),
        }
    )
    return _success(request, projection, state.warnings)


def _validate_managed_resources(
    paths: ManagedPaths, digests: Mapping[str, object], *,
    allow_lock_created_install_root: bool = False,
) -> None:
    resources = (
        (paths.local(paths.install_root), "directory", "root", "root", 0o755, None),
        (paths.local(paths.release_root), "directory", "root", "root", 0o755, None),
        (paths.local(paths.deployment_root), "directory", "root", "root", 0o700, None),
        (paths.local(paths.selection_root), "directory", "root", "root", 0o750, None),
        (paths.local(paths.backup_protection_root), "directory", "root", "root", 0o750, None),
        (paths.local(paths.backup_root), "directory", "root", "root", 0o700, None),
        (Path("/etc/taskman"), "directory", "root", "taskman", 0o750, None),
        (Path("/var/lib/taskman"), "directory", "taskman", "taskman", 0o700, None),
        (Path("/var/lock/taskman"), "directory", "root", "root", 0o700, None),
        (Path("/usr/local/lib/taskman"), "directory", "root", "root", 0o755, None),
        (paths.local(paths.lifecycle_lock_path), "file", "root", "root", 0o600, None),
        (Path("/etc/taskman/taskman-backup.env"), "file", "root", "root", 0o600, digests["backup_environment"]),
        (Path("/etc/systemd/system/taskman.service"), "file", "root", "root", 0o644, digests["taskman_service"]),
        (Path("/etc/systemd/system/taskman-backup.service"), "file", "root", "root", 0o644, digests["backup_service"]),
        (Path("/etc/systemd/system/taskman-backup.timer"), "file", "root", "root", 0o644, digests["backup_timer"]),
        (Path("/usr/local/lib/taskman/taskman-backup.pyz"), "file", "root", "root", 0o750, None),
    )
    for path, kind, owner, group, mode, digest in resources:
        try:
            details = path.lstat()
        except FileNotFoundError:
            continue
        expected_uid = pwd.getpwnam(owner).pw_uid
        expected_gid = grp.getgrnam(group).gr_gid
        valid_kind = stat.S_ISDIR(details.st_mode) if kind == "directory" else stat.S_ISREG(details.st_mode)
        if (
            stat.S_ISLNK(details.st_mode)
            or not valid_kind
            or details.st_uid != expected_uid
            or details.st_gid != expected_gid
            or (
                stat.S_IMODE(details.st_mode) != mode
                and not (
                    allow_lock_created_install_root
                    and path == paths.local(paths.install_root)
                    and stat.S_IMODE(details.st_mode) == 0o750
                )
            )
            or (digest is not None and sha256_file(path) != digest)
        ):
            raise ValueError("managed resource authority is invalid")


def _observe_postgresql_authority(
    database: Mapping[str, object], package_track: str | None,
) -> str:
    """Observe a selected cluster, its listener process, and database identities."""

    script = "\n".join(
        (
            "set -eu",
            "track=$1 port=$2 role=$3 database=$4",
            "command -v pg_lsclusters >/dev/null 2>&1 || { printf '%s\\n' absent; exit 0; }",
            "candidates=$(pg_lsclusters --no-header 2>/dev/null | awk -v track=\"$track\" '(track == \"\" || $1 == track) { if (NF < 5 || $1 !~ /^[0-9]+$/ || $2 !~ /^[A-Za-z0-9_][A-Za-z0-9_-]*$/ || $3 !~ /^[0-9]+$/ || ($4 != \"online\" && $4 != \"down\") || $5 != \"postgres\") exit 2; print $1, $2, $3, $4 }')",
            "count=$(printf '%s\\n' \"$candidates\" | sed '/^$/d' | wc -l | tr -d ' ')",
            "[ \"$count\" -le 1 ] || exit 1",
            "[ \"$count\" -eq 0 ] && { printf '%s\\n' absent; exit 0; }",
            "set -- $candidates",
            "version=$1 cluster=$2 configured_port=$3 state=$4",
            "[ \"$state\" = online ] && [ \"$configured_port\" = \"$port\" ] || exit 1",
            "data=$(pg_conftool -s \"$version\" \"$cluster\" show data_directory 2>/dev/null) || exit 1",
            "pid=$(sed -n '1p' \"$data/postmaster.pid\" 2>/dev/null || true)",
            "case \"$pid\" in ''|*[!0-9]*) exit 1 ;; esac",
            "[ \"$(readlink -f \"/proc/$pid/exe\" 2>/dev/null || true)\" = \"/usr/lib/postgresql/$version/bin/postgres\" ] || exit 1",
            "[ \"$(stat --format='%U:%G' \"/proc/$pid\" 2>/dev/null || true)\" = postgres:postgres ] || exit 1",
            "admin() { runuser -u postgres -- psql --no-psqlrc --tuples-only --no-align --host /var/run/postgresql --port \"$port\" --username postgres --dbname postgres \"$@\"; }",
            "role_ok=$(admin --set=role=\"$role\" --command \"SELECT 1 FROM pg_roles WHERE rolname = :'role' AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND rolinherit AND NOT EXISTS (SELECT 1 FROM pg_auth_members membership JOIN pg_roles member ON member.oid = membership.member WHERE member.rolname = :'role')\" 2>/dev/null || true)",
            "database_ok=$(admin --set=database=\"$database\" --set=role=\"$role\" --command \"SELECT 1 FROM pg_database WHERE datname = :'database' AND pg_get_userbyid(datdba) = :'role'\" 2>/dev/null || true)",
            "case \"$role_ok:$database_ok\" in :) printf '%s\\n' absent ;; 1:1) printf '%s\\n' ready ;; *) exit 1 ;; esac",
        )
    )
    result = run_command(
        (
            "sh", "-ceu", script, "taskman-provision-authority",
            "" if package_track is None else package_track,
            str(database["port"]), str(database["role"]), str(database["name"]),
        ),
        timeout_seconds=_SNAPSHOT_TIMEOUT_SECONDS,
        output_limit=1024,
    )
    if result.stdout == b"ready\n":
        return "ready"
    if result.stdout == b"absent\n":
        return "absent"
    raise ValueError("PostgreSQL authority observation is incomplete")


def list_releases(request: HostRequest) -> HostResult:
    """Return completed release manifests from one coherent snapshot."""

    try:
        state, _scheduler, _restore_database_state = _observe(request)
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
        state, _scheduler, _restore_database_state = _observe(request)
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


def _observe(
    request: HostRequest,
) -> tuple[HostState, dict[str, object] | None, dict[str, object] | None]:
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
            requested_backup = (
                _validate_restore_backup(paths, backup_id)
                if mode == "restore"
                else None
            )
            restore_database_state = None
            if mode == "restore":
                restore_database_state = observe_restore_databases(
                    database, credentials_path
                )
                canonical = restore_database_state["canonical"]
                if isinstance(canonical, Mapping):
                    canonical_versions = canonical["applied_migrations"]
                    observation = {
                        "state": "ready",
                        "applied_migrations": (
                            () if canonical_versions is None else canonical_versions
                        ),
                    }
                else:
                    observation = {"state": "absent", "applied_migrations": ()}
            else:
                observation = (
                    observe_database_state_or_empty(database, credentials_path)
                    if mode == "provision"
                    else observe_database_state(database, credentials_path)
                )
            state = observe_host_state(
                paths,
                database=observation,
                allow_selection_transition=mode in {"deploy", "provision", "restore"},
            )
            if mode == "restore":
                _validate_restore_required_safety(paths, state)
            if requested_backup is not None:
                observed = next(
                    (
                        item
                        for item in state.backups
                        if item.backup_id == requested_backup.backup_id
                    ),
                    None,
                )
                source = next(
                    (
                        item
                        for item in state.releases
                        if item.release_id == requested_backup.source_release_id
                    ),
                    None,
                )
                if (
                    observed != requested_backup
                    or source is None
                    or release_migration_versions(source.migrations)
                    != requested_backup.migration_versions
                ):
                    raise _RestoreBackupFailure(
                        "restore input record or source authority changed"
                    )
            scheduler = (
                _scheduler_facts(paths)
                if mode in {"deploy", "provision", "restore"}
                else None
            )
            return state, scheduler, restore_database_state
        if request.expected_state or set(request.parameters) != {"cursor"}:
            raise ValueError("listing request is invalid")
        return observe_host_state(
            paths,
            allow_selection_transition=request.operation
            in {"list_releases", "list_backups"},
        ), None, None


def _validate_restore_backup(paths: ManagedPaths, backup_id: object) -> BackupRecord:
    """Validate one immutable restore record and dump without changing either."""

    if type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None:
        raise _RestoreBackupFailure("restore backup identity is invalid")
    root = Path(paths.local(paths.backup_root))
    manifest = root / f"{backup_id}.json"
    dump = root / f"{backup_id}.dump"
    try:
        manifest_details = manifest.lstat()
        if (
            stat.S_ISLNK(manifest_details.st_mode)
            or not stat.S_ISREG(manifest_details.st_mode)
            or manifest_details.st_uid != os.geteuid()
            or stat.S_IMODE(manifest_details.st_mode) != 0o600
            or not 0 < manifest_details.st_size <= MAX_RECORD_BYTES
        ):
            raise _RestoreBackupFailure("restore backup record is unsafe")
        record = BackupRecord.from_mapping(json.loads(manifest.read_text("utf-8")))
        if record.backup_id != backup_id:
            raise _RestoreBackupFailure("restore backup record identity is inconsistent")
        before = dump.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or sha256_file(dump) != record.dump_sha256
        ):
            raise _RestoreBackupFailure("restore backup dump is invalid")
        run_command(
            ("pg_restore", "--list", dump.as_posix()),
            timeout_seconds=_SNAPSHOT_TIMEOUT_SECONDS,
        )
        after = dump.lstat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise _RestoreBackupFailure("restore backup dump changed during validation")
        return record
    except _RestoreBackupFailure:
        raise
    except (CommandError, OSError, UnicodeError, ValueError, RecordError) as error:
        raise _RestoreBackupFailure("restore backup cannot be validated") from error


def _validate_restore_required_safety(
    paths: ManagedPaths, state: HostState
) -> None:
    """Fully validate every backup whose current role is restore safety."""

    target = state.restore_target
    if target is None:
        return
    required_ids = {
        target.safety_backup_id,
        *(str(item["backup_id"]) for item in target.safety_backup_attempts),
    }
    observed = {item.backup_id: item for item in state.backups}
    for backup_id in sorted(required_ids):
        if observed.get(backup_id) != _validate_restore_backup(paths, backup_id):
            raise _RestoreBackupFailure(
                "restore safety backup authority changed"
            )


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
    protected_backup_ids = frozenset(item.backup_id for item in protections)
    independently_held_protection_ids = tuple(
        sorted(independent_backup_ids(state).intersection(protected_backup_ids))
    )
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
        # Successful history and restore records may grow independently of
        # deploy's bounded protection projection.  Only their intersection
        # can alter an attempt-retirement decision, so do not duplicate the
        # whole history as an ordinary protocol array.
        "independently_held_backup_ids": independently_held_protection_ids,
        "backup_protection_sha256": hashlib.sha256(_canonical_ascii(protection_rows)).hexdigest(),
        "downgrade_baseline_sha256": hashlib.sha256(
            _canonical_ascii(sorted(baseline_ids))
        ).hexdigest(),
        **scheduler,
    }


def _restore_projection(
    state: HostState,
    scheduler: Mapping[str, object],
    restore_database_state: Mapping[str, object],
) -> dict[str, object]:
    """Project restore-only identity without a deployment downgrade baseline."""

    deployment = _deployment_projection(state, scheduler, mode="restore")
    target = state.restore_target
    attempt_ids = (
        frozenset()
        if target is None
        else frozenset(
            str(item["backup_id"]) for item in target.safety_backup_attempts
        )
    )
    independently_held_attempt_ids = tuple(
        sorted(
            attempt_ids.intersection(
                {
                    *state.successful_backup_ids,
                    *(item.backup_id for item in state.backup_protections),
                    *(
                        item.backup_id
                        for item in state.retiring_backup_protections
                    ),
                }
            )
        )
    )
    canonical = restore_database_state["canonical"]
    applied_migrations = (
        canonical["applied_migrations"] if isinstance(canonical, Mapping) else None
    )
    return {
        "applied_migrations": applied_migrations,
        "backup_protections": deployment["backup_protections"],
        # Full successful history remains host-local. Only safety attempts
        # whose retention changes because of another durable role are needed
        # to derive the exact bounded prune plan.
        "independently_held_backup_ids": independently_held_attempt_ids,
        "backup_protection_sha256": deployment["backup_protection_sha256"],
        "scheduled_backup_sha256": deployment["scheduled_backup_sha256"],
        "backup_timer_enabled": deployment["backup_timer_enabled"],
        "backup_timer_state": deployment["backup_timer_state"],
        "restore_target": (
            None
            if state.restore_target is None
            else {
                **state.restore_target.to_mapping(),
                "sha256": restore_target_sha256(state.restore_target),
            }
        ),
        "restore_database_state": dict(restore_database_state),
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
    if enabled == "not-found":
        # Absence is a valid pre-convergence fact.  The first provision plan
        # owns creating the package and timer; it is not ambiguous authority.
        return {
            "scheduled_backup_sha256": package_sha256,
            "backup_timer_enabled": False,
            "backup_timer_state": "inactive",
        }
    if enabled not in {"enabled", "disabled"}:
        raise StateAmbiguityError("backup timer enablement is ambiguous")
    active = _systemd_property("ActiveState")
    timer_state = active if active in {"active", "inactive"} else "unknown"
    return {
        "scheduled_backup_sha256": package_sha256,
        "backup_timer_enabled": enabled == "enabled",
        "backup_timer_state": timer_state,
    }


def _scheduler_resources(paths: ManagedPaths) -> dict[str, bool]:
    """Return bounded create-only scheduler presence without adopting bytes."""

    observed: dict[str, bool] = {}
    for name, resource in _SCHEDULER_RESOURCES.items():
        candidate = Path(paths.local(resource))
        try:
            details = candidate.lstat()
        except FileNotFoundError:
            observed[name] = False
            continue
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise StateAmbiguityError("scheduled resource is unsafe")
        observed[name] = True
    return observed


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


def _backup_failure(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="manual",
        message="selected restore backup is invalid",
        state={"failed_boundary": "backup"},
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


__all__ = ["discover", "list_backups", "list_releases", "provision_authority"]
