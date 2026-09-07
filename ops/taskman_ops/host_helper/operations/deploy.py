"""Replayable deployment convergence for deploy and first-release genesis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION
from taskman_ops.manifests import ArtifactManifest
from taskman_ops.releases.identifiers import validate_release_id

from ..commands import CommandError, run_command
from ..lock import LifecycleLockContention, lifecycle_lock
from ..operations.backup import create_validated_backup
from ..paths import ManagedPaths, PathAuthorityError
from ..records import BackupRecord, RecordError, ReleaseRecord, SelectionRecord, append_selection
from ..state import HostState, StateAmbiguityError, observe_host_state
from ..verification import host_preflight, verify


_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required"})
_DATABASE_FIELDS = frozenset({"host", "port", "role", "name"})
_PARAMETERS = frozenset(
    {
        "candidate_release_id",
        "artifact_sha256",
        "artifact_path",
        "manifest",
        "migration_policy",
        "credentials_path",
        "database",
        "verification",
    }
)
_EXPECTED_STATE = frozenset({"selected_release_id", "applied_migrations"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_EXPANDED_ARCHIVE_BYTES = 10 * 1024 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 16_384
_LOCK_TIMEOUT_SECONDS = 5.0
_COMMAND_TIMEOUT_SECONDS = 60.0
_RUNTIME_ENVIRONMENT = Path("/etc/taskman/taskman.env")


class DeploymentManualError(RuntimeError):
    """The observed host facts cannot prove one safe forward transition."""


class _RetryableError(RuntimeError):
    def __init__(self, boundary: str) -> None:
        super().__init__(boundary)
        self.boundary = boundary


@dataclass(frozen=True)
class _Inputs:
    paths: ManagedPaths
    previous_release_id: str | None
    expected_migrations: tuple[int, ...]
    candidate: ReleaseRecord
    candidate_versions: tuple[int, ...]
    artifact_path: Path
    artifact_sha256: str
    migration_policy: str
    credentials: Path
    database: Mapping[str, object]
    verification: Mapping[str, object]


def deploy(request: HostRequest) -> HostResult:
    """Converge an existing host on the requested immutable release."""

    return converge_deployment(request)


def genesis(request: HostRequest) -> HostResult:
    """Converge an empty host through the same release procedure."""

    return converge_deployment(request, first_release=True)


def converge_deployment(request: HostRequest, *, first_release: bool = False) -> HostResult:
    """Replay the explicit deploy procedure from authoritative completed facts.

    There is deliberately no operation record or stage journal.  A retry reads
    the selected release, completed manifests, applied migration versions, and
    deterministic release staging directory, then repeats only still-safe work.
    """

    state: HostState | None = None
    backup: BackupRecord | None = None
    changed = False
    database_changed = False
    try:
        inputs = _inputs(request, first_release=first_release)
        _validate_request_operation(request, first_release)
        _safe_credentials(inputs.credentials)
        _safe_artifact(inputs)
        authority = host_preflight(inputs.paths, inputs.verification)
        if authority is not None:
            return _result(request, "refused", "host deployment prerequisites are unsafe", state)

        with lifecycle_lock(inputs.paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
            _prepare_release_roots(inputs.paths)
            state = _observe(inputs)
            state, repaired_selection = _repair_recorded_selection(inputs, state)
            changed = changed or repaired_selection
            _validate_starting_state(state, inputs, first_release=first_release)
            _normalize_staging(inputs)

            staged = _stage_or_reuse(inputs, state)
            changed = changed or staged
            state = _observe(inputs)

            migration_needed = inputs.expected_migrations != inputs.candidate_versions
            migration_done = state.applied_migrations == inputs.candidate_versions
            if migration_needed and not migration_done and not first_release:
                if state.applied_migrations != inputs.expected_migrations:
                    raise DeploymentManualError("applied migrations do not identify a safe candidate transition")
                try:
                    backup = create_validated_backup(
                        state,
                        inputs.paths,
                        inputs.database,
                        inputs.credentials,
                        purpose="pre-deploy",
                    )
                except (CommandError, RecordError, OSError, ValueError) as error:
                    raise _RetryableError("backup") from error
                changed = True

            # The previous release is never restarted after its schema may
            # have advanced.  Stop before the candidate migration and before
            # any atomic selection, including no-schema release updates.
            if not first_release and state.selected_release_id != inputs.candidate.release_id:
                _service("stop")
                changed = True

            if migration_needed and not migration_done:
                try:
                    _migrate(inputs)
                except CommandError as error:
                    # Migration commands may commit before their result is
                    # lost.  Capture the real versions so the next rerun can
                    # continue only the same compatible candidate forward.
                    state = _observe(inputs)
                    raise _RetryableError("migration") from error
                state = _observe(inputs)
                if state.applied_migrations != inputs.candidate_versions:
                    raise DeploymentManualError("candidate migration result is contradictory")
                changed = True
                database_changed = True
            elif migration_needed:
                database_changed = True

            state = _observe(inputs)
            selected = state.selected_release_id
            if selected not in {inputs.previous_release_id, inputs.candidate.release_id}:
                raise DeploymentManualError("selected release changed outside the confirmed deployment")
            if selected != inputs.candidate.release_id:
                try:
                    _atomically_select(inputs.paths, inputs.candidate.release_id)
                except (OSError, RecordError, ValueError) as error:
                    raise _RetryableError("selection") from error
                changed = True

            # Starting again and verifying again are intentionally safe on a
            # rerun after either call lost its result.
            _service("start")
            verification = _verify(request, inputs)
            if verification.outcome != "succeeded":
                raise _RetryableError("verification")
            report = verification.state.get("report", {})
            state = _observe(inputs)
            state, recorded_selection, backup = _record_successful_selection(inputs, state, backup)
            changed = changed or recorded_selection
    except LifecycleLockContention:
        return _result(request, "retryable", "lifecycle lock is unavailable", state, locked=True)
    except DeploymentManualError:
        return _result(request, "manual", "deployment state is contradictory", state)
    except _RetryableError as error:
        return _result(
            request,
            "retryable",
            "deployment did not complete; rerun to converge",
            state,
            boundary=error.boundary,
        )
    except StateAmbiguityError:
        return _result(request, "manual", "deployment authority is contradictory", state)
    except CommandError:
        return _result(
            request,
            "retryable",
            "deployment observation did not complete; rerun to converge",
            state,
            boundary="observation",
        )
    except (PathAuthorityError, TypeError, ValueError):
        return _result(request, "refused", "deployment request is unsafe", state)
    except (OSError, tarfile.TarError, RecordError):
        return _result(request, "manual", "deployment authority is contradictory", state)

    return _result(
        request,
        "succeeded",
        "deployment converged",
        state,
        changed=changed,
        backup_id=None if backup is None else backup.backup_id,
        database_state="changed" if database_changed else "unchanged",
        service_state="running",
        report=report,
    )


def _inputs(request: HostRequest, *, first_release: bool) -> _Inputs:
    if not isinstance(request, HostRequest) or not isinstance(first_release, bool):
        raise ValueError("invalid deployment request")
    if set(request.expected_state) != _EXPECTED_STATE or set(request.parameters) != _PARAMETERS:
        raise ValueError("invalid deployment request")
    previous = request.expected_state["selected_release_id"]
    if previous is not None:
        previous = validate_release_id(previous)
    expected_migrations = _migration_versions(request.expected_state["applied_migrations"])
    if first_release:
        if previous is not None or expected_migrations:
            raise ValueError("first release requires an empty confirmed host")
    elif previous is None:
        raise ValueError("deploy requires a selected release")

    paths = ManagedPaths.from_mapping(request.paths)
    candidate = request.parameters["candidate_release_id"]
    if type(candidate) is not str:
        raise ValueError("invalid candidate release")
    manifest = ArtifactManifest.from_mapping(_mutable(request.parameters["manifest"]))
    if candidate != manifest.release_id:
        raise ValueError("artifact manifest does not match candidate release")
    artifact_sha256 = request.parameters["artifact_sha256"]
    if type(artifact_sha256) is not str or _SHA256_RE.fullmatch(artifact_sha256) is None:
        raise ValueError("invalid artifact checksum")
    artifact_path = request.parameters["artifact_path"]
    if type(artifact_path) is not str:
        raise ValueError("invalid artifact path")
    upload = PurePosixPath(artifact_path)
    if not upload.is_absolute() or not upload.is_relative_to(paths.deployment_root / "uploads"):
        raise ValueError("artifact is outside the derived upload authority")
    policy = request.parameters["migration_policy"]
    if type(policy) is not str or policy not in _POLICIES:
        raise ValueError("invalid migration policy")
    candidate_versions = _migration_versions_from_manifest(manifest)
    _validate_migration_policy(expected_migrations, candidate_versions, policy, first_release=first_release)
    credentials = request.parameters["credentials_path"]
    if type(credentials) is not str or not Path(credentials).is_absolute():
        raise ValueError("invalid credentials path")
    database = _database(request.parameters["database"])
    verification = request.parameters["verification"]
    if not isinstance(verification, Mapping):
        raise ValueError("invalid verification settings")
    record = ReleaseRecord(
        manifest.release_id,
        manifest.source_revision,
        artifact_sha256,
        tuple(item.to_mapping() for item in manifest.migrations),
    )
    return _Inputs(
        paths=paths,
        previous_release_id=previous,
        expected_migrations=expected_migrations,
        candidate=record,
        candidate_versions=candidate_versions,
        artifact_path=Path(artifact_path),
        artifact_sha256=artifact_sha256,
        migration_policy=policy,
        credentials=Path(credentials),
        database=database,
        verification=verification,
    )


def _validate_request_operation(request: HostRequest, first_release: bool) -> None:
    if request.operation != ("genesis" if first_release else "deploy"):
        raise ValueError("invalid deployment operation")


def _database(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != _DATABASE_FIELDS:
        raise ValueError("invalid database settings")
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
        raise ValueError("invalid database settings")
    return value


def _migration_versions(value: object) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or any(type(item) is not int or item < 0 for item in value):
        raise ValueError("invalid migration versions")
    versions = tuple(value)
    if versions != tuple(sorted(set(versions))):
        raise ValueError("invalid migration versions")
    return versions


def _migration_versions_from_manifest(manifest: ArtifactManifest) -> tuple[int, ...]:
    try:
        return _migration_versions(tuple(int(item.filename.split("_", 1)[0]) for item in manifest.migrations))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid candidate migrations") from error


def _validate_migration_policy(
    current: tuple[int, ...], candidate: tuple[int, ...], policy: str, *, first_release: bool
) -> None:
    if policy == "no-change" and current == candidate:
        return
    if policy == "backward-compatible" and not first_release and candidate[: len(current)] == current:
        return
    if policy == "restore-required" and current != candidate:
        if first_release and not current:
            # A clean host has no predecessor database to restore.  The
            # controller keeps the declared policy visible, while this one
            # procedure still applies the initial schema directly.
            return
        # A deployment never restores a database as an implicit side effect.
        raise ValueError("the confirmed migration policy requires restore")
    raise ValueError("migration policy does not match the candidate")


def _validate_starting_state(state: HostState, inputs: _Inputs, *, first_release: bool) -> None:
    candidate = inputs.candidate.release_id
    if first_release:
        if state.selected_release_id is None and not state.releases and not state.selections:
            return
        if (
            state.selected_release_id == candidate
            and len(state.releases) == 1
            and (
                not state.selections
                or (
                    len(state.selections) == 1
                    and state.selections[0].previous_release_id is None
                )
            )
        ):
            return
        raise ValueError("first release host is not empty")
    if state.selected_release_id not in {inputs.previous_release_id, candidate}:
        raise ValueError("confirmed current release changed")
    if state.applied_migrations not in {inputs.expected_migrations, inputs.candidate_versions}:
        raise DeploymentManualError("current schema is not compatible with this candidate")
    existing = next((item for item in state.releases if item.release_id == candidate), None)
    if existing is not None and existing != inputs.candidate:
        raise DeploymentManualError("installed candidate identity contradicts the artifact")


def _observe(inputs: _Inputs, *, allow_selection_transition: bool = True) -> HostState:
    return observe_host_state(
        inputs.paths,
        database=_observe_database(inputs.database, inputs.credentials),
        allow_selection_transition=allow_selection_transition,
    )


def _repair_recorded_selection(inputs: _Inputs, state: HostState) -> tuple[HostState, bool]:
    """Repair only a recorded selection whose current-link replacement was lost.

    The normal sequence selects, starts, verifies, then records the successful
    selection.  A link that already names the candidate but lacks that record
    therefore remains unrecorded until verification succeeds on this rerun.
    The reverse arrangement can only resume the same completed selection.
    """

    recorded = state.selections[-1].release_id if state.selections else None
    selected = state.selected_release_id
    if recorded == selected:
        return state, False
    allowed = {inputs.previous_release_id, inputs.candidate.release_id}
    if recorded not in allowed or selected not in allowed:
        raise DeploymentManualError("selection transition is not attributable to this deployment")
    if selected == inputs.candidate.release_id and recorded != inputs.candidate.release_id:
        return state, False
    if recorded == inputs.candidate.release_id:
        # Selection records are create-once completed authority.  A current
        # link moved back to the predecessor cannot be the unfinished atomic
        # selection transition emitted by this procedure, so never replace it
        # automatically.
        raise DeploymentManualError("recorded candidate conflicts with current selection")
    if recorded != inputs.candidate.release_id:
        raise DeploymentManualError("selection transition does not prove a completed candidate")
    if not any(item.release_id == inputs.candidate.release_id for item in state.releases):
        raise DeploymentManualError("selection transition lacks the candidate release")
    try:
        _atomically_select(inputs.paths, inputs.candidate.release_id)
    except OSError as error:
        raise _RetryableError("selection") from error
    return _observe(inputs, allow_selection_transition=False), True


def _record_successful_selection(
    inputs: _Inputs,
    state: HostState,
    backup: BackupRecord | None,
) -> tuple[HostState, bool, BackupRecord | None]:
    """Publish exactly the verified selection the current procedure proves."""

    recorded = state.selections[-1].release_id if state.selections else None
    selected = state.selected_release_id
    if selected != inputs.candidate.release_id:
        raise DeploymentManualError("verification did not retain the candidate selection")
    if recorded == inputs.candidate.release_id:
        selection = state.selections[-1]
        return (
            _observe(inputs, allow_selection_transition=False),
            False,
            _selection_backup(inputs, state, selection.backup_id, backup),
        )
    if recorded != inputs.previous_release_id:
        raise DeploymentManualError("selection history is not attributable to this deployment")
    selection_backup = _selection_backup(inputs, state, None, backup)
    try:
        _append_selection_with_previous(
            inputs.paths,
            state,
            inputs.candidate.release_id,
            recorded,
            selection_backup,
        )
    except (OSError, RecordError, ValueError) as error:
        raise _RetryableError("selection") from error
    return _observe(inputs, allow_selection_transition=False), True, selection_backup


def _selection_backup(
    inputs: _Inputs,
    state: HostState,
    recorded_backup_id: str | None,
    local_backup: BackupRecord | None,
) -> BackupRecord | None:
    """Resolve the completed pre-migration backup without retry metadata.

    A fresh invocation may name the backup it just created.  A replay that
    lost its migration or record-publication result has no such in-memory
    handle, so it can recover only one authoritative backup matching the
    predecessor release and schema.  Completed selection history itself is
    sufficient to identify its already-recorded backup.
    """

    migration_needed = inputs.expected_migrations != inputs.candidate_versions
    if not migration_needed or inputs.previous_release_id is None:
        if recorded_backup_id is not None:
            raise DeploymentManualError("selection backup conflicts with the deployment transition")
        return None

    candidates = tuple(
        item
        for item in state.backups
        if item.source_release_id == inputs.previous_release_id
        and item.migration_versions == inputs.expected_migrations
    )
    by_id = {item.backup_id: item for item in candidates}
    if recorded_backup_id is not None:
        try:
            return by_id[recorded_backup_id]
        except KeyError as error:
            raise DeploymentManualError("recorded selection backup is not valid for this migration") from error
    if local_backup is not None:
        try:
            return by_id[local_backup.backup_id]
        except KeyError as error:
            raise DeploymentManualError("created backup is not present in completed state") from error
    if len(candidates) != 1:
        raise DeploymentManualError("migration backup cannot be recovered unambiguously")
    return candidates[0]


def _observe_database(database: Mapping[str, object], credentials: Path) -> Mapping[str, object]:
    """Observe schema versions without accepting a controller-side database fact."""

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
    if table == b"":
        return {"state": "ready", "applied_migrations": ()}
    if table != b"1":
        raise DeploymentManualError("database migration table identity is ambiguous")
    versions = run_command(
        (*common, "--command", "SELECT version FROM schema_migrations ORDER BY version"),
        env=environment,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout.splitlines()
    try:
        return {"state": "ready", "applied_migrations": _migration_versions(tuple(int(item) for item in versions if item))}
    except ValueError as error:
        raise DeploymentManualError("database migration versions are invalid") from error


def _prepare_release_roots(paths: ManagedPaths) -> None:
    owner_uid = os.geteuid()
    paths.validate_existing(owner_uid=owner_uid)
    for value in (paths.install_root, paths.release_root, paths.deployment_root):
        path = Path(paths.local(value))
        path.mkdir(mode=0o750, parents=True, exist_ok=True)
        details = path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != owner_uid
            or details.st_mode & 0o7022
        ):
            raise DeploymentManualError("managed release directory is unsafe")


def _normalize_staging(inputs: _Inputs) -> None:
    path = _staging_path(inputs)
    if not path.exists() and not path.is_symlink():
        return
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_mode & 0o7022
    ):
        raise DeploymentManualError("release staging directory is unsafe")
    shutil.rmtree(path)
    _fsync_directory(path.parent)


def _stage_or_reuse(inputs: _Inputs, state: HostState) -> bool:
    existing = next((item for item in state.releases if item.release_id == inputs.candidate.release_id), None)
    if existing is not None:
        if existing != inputs.candidate:
            raise DeploymentManualError("installed candidate identity contradicts the artifact")
        return False
    root = Path(inputs.paths.local(inputs.paths.release_root))
    target = root / inputs.candidate.release_id
    if target.exists() or target.is_symlink():
        raise DeploymentManualError("release path exists without a completed manifest")
    staging = _staging_path(inputs)
    try:
        staging.mkdir(mode=0o750)
        _extract_release(inputs.artifact_path, staging)
        content = staging / "taskman"
        _validate_release_tree(content)
        _normalize_release_tree(content, os.geteuid())
        _write_release_manifest(content, inputs.candidate)
        os.replace(content, target)
        staging.rmdir()
        _fsync_directory(root)
    except (OSError, tarfile.TarError, ValueError) as error:
        raise _RetryableError("staging") from error
    return True


def _staging_path(inputs: _Inputs) -> Path:
    return Path(inputs.paths.local(inputs.paths.release_root / f".release-{inputs.candidate.release_id}.tmp"))


def _write_release_manifest(directory: Path, record: ReleaseRecord) -> None:
    target = directory / ".taskman-release.json"
    payload = json.dumps(record.to_mapping(), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(target, os.geteuid(), -1)
    os.chmod(target, 0o640)


def _safe_artifact(inputs: _Inputs) -> None:
    try:
        details = inputs.artifact_path.lstat()
        inputs.artifact_path.relative_to(Path(inputs.paths.local(inputs.paths.deployment_root / "uploads")))
    except (OSError, ValueError) as error:
        raise ValueError("deployment artifact is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
        or not 0 < details.st_size <= _MAX_ARCHIVE_BYTES
        or _sha256(inputs.artifact_path) != inputs.artifact_sha256
    ):
        raise ValueError("deployment artifact is unsafe")
    try:
        with tarfile.open(inputs.artifact_path, "r:gz") as archive:
            _validate_archive_members(archive.getmembers())
    except (OSError, tarfile.TarError, ValueError) as error:
        raise ValueError("deployment artifact inventory is unsafe") from error


def _safe_credentials(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise ValueError("database credentials are unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise ValueError("database credentials are unsafe")


def _migrate(inputs: _Inputs) -> None:
    candidate = Path(inputs.paths.local(inputs.paths.release_root / inputs.candidate.release_id)) / "bin" / "migrate"
    run_command(
        (
            "systemd-run", "--wait", "--quiet", "--collect", "--property=User=taskman",
            "--property=Group=taskman", f"--property=EnvironmentFile={_RUNTIME_ENVIRONMENT}",
            candidate.as_posix(),
        ),
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )


def _service(action: str) -> None:
    try:
        run_command(("systemctl", action, "taskman.service"), timeout_seconds=_COMMAND_TIMEOUT_SECONDS)
    except CommandError as error:
        raise _RetryableError("start" if action == "start" else "stop") from error


def _atomically_select(paths: ManagedPaths, release_id: str) -> None:
    root = Path(paths.local(paths.install_root))
    target = Path(paths.local(paths.release_root / release_id))
    temporary = root / f".current-{release_id}.tmp"
    if temporary.exists() or temporary.is_symlink():
        details = temporary.lstat()
        if not stat.S_ISLNK(details.st_mode) or temporary.resolve(strict=False) != target:
            raise DeploymentManualError("selection temporary is unsafe")
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, Path(paths.local(paths.current_link)))
    _fsync_directory(root)


def _append_selection_with_previous(
    paths: ManagedPaths,
    state: HostState,
    candidate: str,
    previous: str | None,
    backup: BackupRecord | None = None,
) -> None:
    if previous is None:
        selected_at = datetime.now(UTC).replace(microsecond=0)
    else:
        latest = state.selections[-1].selected_at if state.selections else datetime.now(UTC).replace(microsecond=0)
        selected_at = max(datetime.now(UTC).replace(microsecond=0), latest + timedelta(seconds=1))
    append_selection(paths, SelectionRecord(candidate, previous, None if backup is None else backup.backup_id, selected_at))


def _verify(request: HostRequest, inputs: _Inputs) -> HostResult:
    verification_request = HostRequest(
        PROTOCOL_VERSION,
        "verify",
        request.correlation_id,
        {"expected_release_id": inputs.candidate.release_id},
        request.paths,
        inputs.verification,
    )
    return verify(verification_request, lifecycle_locked=True)


def _extract_release(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        _validate_archive_members(members)
        source.extractall(destination, members=members, filter="data")


def _validate_archive_members(members: list[tarfile.TarInfo]) -> None:
    if not members or len(members) > _MAX_ARCHIVE_MEMBERS:
        raise ValueError("release archive inventory is invalid")
    expanded = 0
    for member in members:
        target = PurePosixPath(member.name)
        if (
            target.is_absolute()
            or not target.parts
            or target.parts[0] != "taskman"
            or ".." in target.parts
            or member.isdev()
            or member.isfifo()
            or member.islnk()
        ):
            raise ValueError("release archive member is unsafe")
        if member.issym() and (PurePosixPath(member.linkname).is_absolute() or ".." in PurePosixPath(member.linkname).parts):
            raise ValueError("release archive link is unsafe")
        if member.isfile():
            expanded += member.size
            if expanded > _MAX_EXPANDED_ARCHIVE_BYTES:
                raise ValueError("release archive expanded size is unsafe")


def _validate_release_tree(path: Path) -> None:
    if (
        not path.is_dir()
        or path.is_symlink()
        or not (path / "bin" / "server").is_file()
        or not (path / "bin" / "migrate").is_file()
        or not (path / "lib").is_dir()
        or not (path / "releases").is_dir()
    ):
        raise ValueError("release archive has incomplete runtime tree")
    for item in path.rglob("*"):
        details = item.lstat()
        if not (
            stat.S_ISDIR(details.st_mode)
            or stat.S_ISREG(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
        ) or details.st_mode & 0o7022:
            raise ValueError("release archive tree is unsafe")


def _normalize_release_tree(path: Path, owner_uid: int) -> None:
    for item in (path, *path.rglob("*")):
        if item.is_symlink():
            continue
        os.chown(item, owner_uid, -1)
        os.chmod(item, 0o750 if item.is_dir() or os.access(item, os.X_OK) else 0o640)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
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
    *,
    boundary: str | None = None,
    locked: bool = False,
    changed: bool | None = None,
    backup_id: str | None = None,
    database_state: str | None = None,
    service_state: str | None = None,
    report: object | None = None,
) -> HostResult:
    facts: dict[str, object] = {
        "selected_release_id": None if state is None else state.selected_release_id,
        "applied_migrations": () if state is None else state.applied_migrations,
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
                "database_state": database_state,
                "service_state": service_state,
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


def _mutable(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _mutable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable(item) for item in value]
    return value


__all__ = ["converge_deployment", "deploy", "genesis"]
