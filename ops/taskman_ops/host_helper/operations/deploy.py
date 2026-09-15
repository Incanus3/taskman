"""Replayable deployment convergence for deploy and first-release genesis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
import grp
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION
from taskman_ops.host_protocol.mutation_results import unavailable_observations
from taskman_ops.releases.manifests import ArtifactManifest
from taskman_ops.releases.identifiers import validate_release_id

from ..commands import CommandError, run_command
from ..credentials import validate_credentials
from ..database import (
    DatabaseObservationError,
    database_mapping,
    migration_versions,
    observe_database_state_or_empty,
)
from ...checksums import sha256_file
from ..filesystem import fsync_directory
from ..lock import LifecycleLockContention, lifecycle_lock
from ..operations.backup import create_validated_backup
from ..operations.discover import _scheduler_facts
from ..backup_helper import BackupHelperError, converge_backup_helper
from ..backup_protection import (
    complete_successful_selection,
    independent_backup_ids,
    protection_prune_ids,
    register_backup_protection,
    retire_protection_attempts,
)
from ..paths import ManagedPaths, PathAuthorityError
from ..records import BackupRecord, RecordError, ReleaseRecord, SelectionRecord, append_selection
from ..selection import SelectionAmbiguityError, select_current
from ..services import change_service
from ..state import (
    HostState,
    StateAmbiguityError,
    mutation_observation_availability,
    mutation_observations,
    observe_host_state,
)
from ..verification import host_preflight, verification_request, verify


_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required"})
_PARAMETERS = frozenset(
    {
        "target",
        "migration_policy",
        "credentials_path",
        "database",
        "verification",
        "backup_helper",
        "prune_backup_ids",
    }
)
_EXPECTED_STATE = frozenset({
    "selected_release_id", "last_successful_selection_id", "applied_migrations",
    "backup_protection_sha256", "scheduled_backup_sha256", "backup_timer_enabled",
    "downgrade_baseline_sha256",
})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SELECTION_FILENAME_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")
_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_EXPANDED_ARCHIVE_BYTES = 10 * 1024 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 16_384
_LOCK_TIMEOUT_SECONDS = 5.0
_COMMAND_TIMEOUT_SECONDS = 60.0
_RUNTIME_ENVIRONMENT = Path("/etc/taskman/taskman.env")


class DeploymentManualError(RuntimeError):
    """The observed host facts cannot prove one safe forward transition."""


class _RetryableError(RuntimeError):
    def __init__(self, boundary: str, *, may_have_mutated: bool = True) -> None:
        super().__init__(boundary)
        self.boundary = boundary
        self.may_have_mutated = may_have_mutated


@dataclass(frozen=True)
class _Inputs:
    paths: ManagedPaths
    expected_state: Mapping[str, object]
    previous_release_id: str | None
    expected_migrations: tuple[int, ...]
    candidate: ReleaseRecord
    candidate_versions: tuple[int, ...]
    artifact_path: Path | None
    artifact_sha256: str | None
    migration_policy: str
    credentials: Path
    database: Mapping[str, object]
    verification: Mapping[str, object]
    backup_helper: Mapping[str, object]
    prune_backup_ids: tuple[str, ...]


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

    inputs: _Inputs | None = None
    state: HostState | None = None
    backup: BackupRecord | None = None
    genesis_source_ids: frozenset[str] | None = None
    changed = False
    database_changed = False
    report: object | None = None
    final_observations: Mapping[str, object] | None = None
    final_unavailable: tuple[str, ...] = ()
    final_inspection_error: str | None = None
    try:
        inputs = _inputs(request, first_release=first_release)
        _validate_request_operation(request, first_release)
        validate_credentials(inputs.credentials)
        _safe_artifact(inputs)
        authority = host_preflight(inputs.paths, inputs.verification)
        if authority is not None:
            return _result(
                request,
                "refused",
                "host deployment prerequisites are unsafe",
                state,
                changed=changed,
                report=report,
            )

        with lifecycle_lock(inputs.paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS) as lock:
            _prepare_release_roots(inputs.paths)
            state = _observe(inputs)
            _validate_expected_state(state, inputs, first_release=first_release)
            try:
                scheduler = converge_backup_helper(
                    inputs.paths,
                    inputs.backup_helper,
                    confirmed_checksum=request.expected_state["scheduled_backup_sha256"],
                    confirmed_enabled=request.expected_state["backup_timer_enabled"],
                    revalidate=lambda: _validate_expected_state(
                        _observe(inputs), inputs, first_release=first_release
                    ),
                    timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
                    lock=lock,
                )
            except BackupHelperError as error:
                changed = changed or any((error.mutation.paused, error.mutation.replaced, error.mutation.restarted))
                raise _RetryableError("backup_helper") from error
            changed = changed or any((scheduler.mutation.paused, scheduler.mutation.replaced, scheduler.mutation.restarted))
            state = _observe(inputs)
            state, repaired_selection = _repair_recorded_selection(
                inputs, state, first_release=first_release
            )
            changed = changed or repaired_selection
            _validate_starting_state(state, inputs, first_release=first_release)
            if first_release and state.applied_migrations:
                # Preserve the provenance that existed before this invocation
                # stages its requested target.  A new archive must never
                # prove migrations that were already committed.
                genesis_source_ids = frozenset(item.release_id for item in state.releases)
            changed = _normalize_staging(inputs) or changed

            staged = _stage_or_reuse(inputs, state)
            changed = changed or staged
            state = _observe(inputs)

            migration_needed = inputs.expected_migrations != inputs.candidate_versions
            migration_done = state.applied_migrations == inputs.candidate_versions
            if migration_needed and not migration_done:
                if state.applied_migrations != inputs.expected_migrations:
                    raise DeploymentManualError("applied migrations do not identify a safe candidate transition")
                if not (first_release and state.initial_database_empty):
                    try:
                        reusable_backup, pruned = _finish_pending_pruning_or_reuse(inputs, state)
                        changed = changed or pruned
                        state = _observe(inputs)
                    except (RecordError, OSError, ValueError) as error:
                        raise _RetryableError("protection") from error
                    if pruned:
                        reusable_backup = _newest_reusable_protection_backup(inputs, state)
                    if reusable_backup is not None:
                        backup = reusable_backup
                    elif pruned:
                        # The prior confirmed retirement changed durable
                        # authority.  A fresh backup would create a different
                        # conditional retirement set, so return a fresh
                        # retryable observation for controller re-planning.
                        raise _RetryableError("protection", may_have_mutated=False)
                    else:
                        try:
                            backup = create_validated_backup(
                                state,
                                inputs.paths,
                                inputs.database,
                                inputs.credentials,
                                purpose="pre-deploy",
                                allowed_source_release_ids=(
                                    genesis_source_ids if first_release else None
                                ),
                            )
                        except (CommandError, RecordError, OSError, ValueError) as error:
                            raise _RetryableError("backup") from error
                        changed = True
                        try:
                            register_backup_protection(
                                inputs.paths,
                                state.backup_protections,
                                backup_id=backup.backup_id,
                                base_selection_id=state.latest_successful_selection_filename,
                                target_release_id=inputs.candidate.release_id,
                            )
                            state = _observe(inputs)
                            _retire_newly_eligible_protections(inputs, state)
                            state = _observe(inputs)
                        except (RecordError, OSError, ValueError) as error:
                            raise _RetryableError("protection") from error

            # The previous release is never restarted after its schema may
            # have advanced.  Stop before the candidate migration and before
            # any atomic selection, including no-schema release updates.
            if (migration_needed and not migration_done) or (
                state.selected_release_id is not None
                and state.selected_release_id != inputs.candidate.release_id
            ):
                try:
                    change_service("stop")
                except CommandError as error:
                    raise _RetryableError("stop") from error
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
                    select_current(inputs.paths, inputs.candidate.release_id)
                except SelectionAmbiguityError as error:
                    raise DeploymentManualError("selection temporary is unsafe") from error
                except (OSError, RecordError, ValueError) as error:
                    raise _RetryableError("selection") from error
                changed = True

            # Verification is required before publication, but a fresh
            # runtime observation avoids needlessly restarting an already
            # healthy completed target on an ordinary retry.
            state = _observe(inputs, include_runtime=True)
            if state.service_state != "running":
                try:
                    change_service("start")
                except CommandError as error:
                    raise _RetryableError("start") from error
                changed = True
            verification = _verify(request, inputs)
            report = verification.state.get("report") or None
            if verification.outcome != "succeeded":
                raise _RetryableError("verification", may_have_mutated=False)
            state = _observe(inputs)
            state, recorded_selection, backup = _record_successful_selection(inputs, state, backup)
            changed = changed or recorded_selection
            try:
                scheduler = _scheduler_facts(inputs.paths)
            except (CommandError, OSError, StateAmbiguityError):
                scheduler = {
                    "scheduled_backup_sha256": None,
                    "backup_timer_enabled": None,
                    "backup_timer_state": "unknown",
                }
            final_observations = mutation_observations(
                state,
                request.operation,
                scheduler=scheduler,
            )
            final_unavailable, final_inspection_error = mutation_observation_availability(
                request.operation, final_observations
            )
    except LifecycleLockContention:
        return _result(
            request,
            "retryable",
            "lifecycle lock is unavailable",
            state,
            locked=True,
            changed=changed,
            backup_id=None if backup is None else backup.backup_id,
            report=report,
        )
    except DeploymentManualError:
        return _failure_result(
            request, "manual", "deployment state is contradictory", state, inputs,
            changed=changed, backup_id=None if backup is None else backup.backup_id, report=report,
        )
    except _RetryableError as error:
        return _failure_result(
            request, "retryable", "deployment did not complete; rerun to converge", state, inputs,
            boundary=error.boundary, changed=changed, possibly_changed=error.may_have_mutated,
            backup_id=None if backup is None else backup.backup_id, report=report,
        )
    except StateAmbiguityError:
        return _failure_result(
            request, "manual", "deployment authority is contradictory", state, inputs,
            changed=changed, backup_id=None if backup is None else backup.backup_id, report=report,
        )
    except CommandError:
        return _failure_result(
            request, "retryable", "deployment observation did not complete; rerun to converge", state, inputs,
            boundary="observation", changed=changed, backup_id=None if backup is None else backup.backup_id,
            report=report,
        )
    except (PathAuthorityError, TypeError, ValueError):
        return _result(
            request,
            "refused",
            "deployment request is unsafe",
            state,
            changed=changed,
            backup_id=None if backup is None else backup.backup_id,
            report=report,
        )
    except (OSError, tarfile.TarError, RecordError):
        return _result(
            request,
            "manual",
            "deployment authority is contradictory",
            state,
            changed=changed,
            backup_id=None if backup is None else backup.backup_id,
            report=report,
        )

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
        final_observations=final_observations,
        final_unavailable=final_unavailable,
        final_inspection_error=final_inspection_error,
    )


def _inputs(request: HostRequest, *, first_release: bool) -> _Inputs:
    if not isinstance(request, HostRequest) or not isinstance(first_release, bool):
        raise ValueError("invalid deployment request")
    if set(request.expected_state) != _EXPECTED_STATE or set(request.parameters) != _PARAMETERS:
        raise ValueError("invalid deployment request")
    previous = request.expected_state["selected_release_id"]
    if previous is not None:
        previous = validate_release_id(previous)
    expected_migrations = migration_versions(request.expected_state["applied_migrations"])
    last_selection = request.expected_state["last_successful_selection_id"]
    if last_selection is not None and (
        type(last_selection) is not str or _SELECTION_FILENAME_RE.fullmatch(last_selection) is None
    ):
        raise ValueError("invalid successful selection authority")
    for key in ("backup_protection_sha256", "downgrade_baseline_sha256"):
        value = request.expected_state[key]
        if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
            raise ValueError("invalid expected deployment digest")
    scheduler_sha256 = request.expected_state["scheduled_backup_sha256"]
    if (
        scheduler_sha256 is not None
        and (type(scheduler_sha256) is not str or _SHA256_RE.fullmatch(scheduler_sha256) is None)
    ) or type(request.expected_state["backup_timer_enabled"]) is not bool:
        raise ValueError("invalid expected backup scheduler state")
    if not first_release and previous is None:
        raise ValueError("deploy requires a selected release")

    paths = ManagedPaths.from_mapping(request.paths)
    target = request.parameters["target"]
    if not isinstance(target, Mapping):
        raise ValueError("invalid deployment target")
    kind = target.get("kind")
    artifact_path: Path | None
    artifact_sha256: str | None
    if kind == "upload" and set(target) == {"kind", "manifest", "artifact_sha256", "artifact_path"}:
        manifest = ArtifactManifest.from_mapping(_mutable(target["manifest"]))
        artifact_sha256 = target["artifact_sha256"]
        artifact_path_value = target["artifact_path"]
        if type(artifact_sha256) is not str or _SHA256_RE.fullmatch(artifact_sha256) is None:
            raise ValueError("invalid artifact checksum")
        if type(artifact_path_value) is not str:
            raise ValueError("invalid artifact path")
        upload = PurePosixPath(artifact_path_value)
        if not upload.is_absolute() or not upload.is_relative_to(paths.deployment_root / "uploads"):
            raise ValueError("artifact is outside the derived upload authority")
        artifact_path = Path(artifact_path_value)
        record = ReleaseRecord(
            manifest.release_id, manifest.source_revision, artifact_sha256,
            tuple(item.to_mapping() for item in manifest.migrations), 2, manifest,
        )
    elif kind == "installed" and set(target) == {"kind", "release_record"}:
        record = ReleaseRecord.from_mapping(_mutable(target["release_record"]))
        manifest = record.artifact_manifest
        artifact_path = None
        artifact_sha256 = None
    else:
        raise ValueError("invalid deployment target")
    policy = request.parameters["migration_policy"]
    if type(policy) is not str or policy not in _POLICIES:
        raise ValueError("invalid migration policy")
    candidate_versions = _migration_versions_from_manifest(manifest)
    if first_release:
        if tuple(candidate_versions[: len(expected_migrations)]) != expected_migrations:
            raise ValueError("first release schema is not a candidate prefix")
    _validate_migration_policy(expected_migrations, candidate_versions, policy, first_release=first_release)
    credentials = request.parameters["credentials_path"]
    if type(credentials) is not str or not Path(credentials).is_absolute():
        raise ValueError("invalid credentials path")
    database = database_mapping(request.parameters["database"])
    verification = request.parameters["verification"]
    if not isinstance(verification, Mapping):
        raise ValueError("invalid verification settings")
    helper = request.parameters["backup_helper"]
    prune = request.parameters["prune_backup_ids"]
    if (
        not isinstance(helper, Mapping) or set(helper) != {"sha256", "upload_path"}
        or type(helper["sha256"]) is not str or _SHA256_RE.fullmatch(helper["sha256"]) is None
        or helper["upload_path"] is not None and type(helper["upload_path"]) is not str
        or not isinstance(prune, (tuple, list))
    ):
        raise ValueError("invalid scheduler or backup-pruning authority")
    prune_ids = tuple(prune)
    if (
        prune_ids != tuple(sorted(set(prune_ids)))
        or any(type(item) is not str or not re.fullmatch(r"backup-[0-9a-f]{32}", item) for item in prune_ids)
    ):
        raise ValueError("invalid backup-pruning authority")
    return _Inputs(
        paths=paths,
        expected_state=dict(request.expected_state),
        previous_release_id=previous,
        expected_migrations=expected_migrations,
        candidate=record,
        candidate_versions=candidate_versions,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        migration_policy=policy,
        credentials=Path(credentials),
        database=database,
        verification=verification,
        backup_helper=dict(helper),
        prune_backup_ids=prune_ids,
    )


def _validate_request_operation(request: HostRequest, first_release: bool) -> None:
    if request.operation != ("genesis" if first_release else "deploy"):
        raise ValueError("invalid deployment operation")


def _migration_versions_from_manifest(manifest: ArtifactManifest) -> tuple[int, ...]:
    try:
        return migration_versions(tuple(int(item.filename.split("_", 1)[0]) for item in manifest.migrations))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid candidate migrations") from error


def _validate_migration_policy(
    current: tuple[int, ...], candidate: tuple[int, ...], policy: str, *, first_release: bool
) -> None:
    if policy == "no-change" and current == candidate:
        return
    if policy == "backward-compatible" and candidate[: len(current)] == current:
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
        _validate_genesis_starting_state(state, inputs)
        return
    if state.selected_release_id not in {inputs.previous_release_id, candidate}:
        raise ValueError("confirmed current release changed")
    if state.applied_migrations not in {inputs.expected_migrations, inputs.candidate_versions}:
        raise DeploymentManualError("current schema is not compatible with this candidate")
    existing = next((item for item in state.releases if item.release_id == candidate), None)
    if existing is not None and existing != inputs.candidate:
        raise DeploymentManualError("installed candidate identity contradicts the artifact")


def _validate_expected_state(state: HostState, inputs: _Inputs, *, first_release: bool) -> None:
    """Bind every material deploy fact again under the lifecycle lock."""

    protections = tuple(
        sorted((*state.backup_protections, *state.retiring_backup_protections), key=lambda item: item.backup_id)
    )
    protection_digest = __import__("hashlib").sha256(
        json.dumps(
            [item.to_mapping() for item in protections],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    baseline_ids = {
        *(() if state.selected_release_id is None else (state.selected_release_id,)),
        *(
            ()
            if state.latest_successful_selection is None
            else (state.latest_successful_selection.release_id,)
        ),
        *(item.target_release_id for item in protections),
    }
    downgrade_baseline_digest = __import__("hashlib").sha256(
        json.dumps(
            sorted(baseline_ids),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    scheduler = _scheduler_facts(inputs.paths)
    expected = inputs.expected_state
    expected_baselines = {
        *(() if expected["selected_release_id"] is None else (expected["selected_release_id"],)),
        *(item.target_release_id for item in protections),
    }
    expected_baseline_digest = __import__("hashlib").sha256(
        json.dumps(
            sorted(expected_baselines),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    # The one supported replay is an interrupted genesis before first history.
    # Only the independently attributable candidate selection and candidate
    # schema prefix may have advanced; history, the originally confirmed
    # baseline, protections, and scheduler remain exact.
    genesis_transition = (
        first_release
        and expected["last_successful_selection_id"] is None
        and state.latest_successful_selection_filename is None
        and expected["downgrade_baseline_sha256"] == expected_baseline_digest
        and state.selected_release_id in {expected["selected_release_id"], inputs.candidate.release_id}
        and state.applied_migrations in {inputs.expected_migrations, inputs.candidate_versions}
    )
    if (
        (not genesis_transition and state.selected_release_id != expected["selected_release_id"])
        or state.latest_successful_selection_filename != expected["last_successful_selection_id"]
        or (not genesis_transition and state.applied_migrations != inputs.expected_migrations)
        or protection_digest != expected["backup_protection_sha256"]
        or (not genesis_transition and downgrade_baseline_digest != expected["downgrade_baseline_sha256"])
        or scheduler["scheduled_backup_sha256"] != expected["scheduled_backup_sha256"]
        or scheduler["backup_timer_enabled"] != expected["backup_timer_enabled"]
    ):
        raise DeploymentManualError("confirmed deployment state changed")


def _validate_genesis_starting_state(state: HostState, inputs: _Inputs) -> None:
    """Accept only the empty host or exact completed facts from this genesis.

    Applied migration versions do not identify who installed them.  A rerun
    after the candidate migration can continue only when the immutable
    candidate record proves this procedure owns the matching schema.
    """

    candidate = inputs.candidate.release_id
    candidate_staging = PurePosixPath(_staging_path(inputs).as_posix())
    if state.selections:
        if (
                len(state.selections) == 1
                and state.selections[0].release_id == candidate
                and state.selections[0].previous_release_id is None
                and state.selected_release_id == candidate
                and state.applied_migrations == inputs.candidate_versions
            ):
            return
        raise DeploymentManualError("a completed installation requires deploy for a different release")

    # Before the first durable selection, several validated release records
    # and a physical failed candidate are ordinary interruption evidence, not
    # conflicting history.  Every committed migration must nevertheless be
    # proved by one of those immutable records; a requested archive never
    # supplies that provenance by itself.
    if state.selected_release_id is not None and not any(
        item.release_id == state.selected_release_id for item in state.releases
    ):
        raise DeploymentManualError("genesis current release is not managed")
    if state.applied_migrations not in {inputs.expected_migrations, inputs.candidate_versions}:
        raise DeploymentManualError("genesis records do not prove the observed schema")
    if state.applied_migrations and not _genesis_migration_provenance_matches(state, inputs):
        raise DeploymentManualError("genesis migrations lack installed provenance")
    if any(path != candidate_staging for path in state.temporary_paths):
        raise DeploymentManualError("genesis staging is not attributable to the candidate")


def _genesis_migration_provenance_matches(state: HostState, inputs: _Inputs) -> bool:
    """Require every observed installed provenance record to match the live prefix.

    The database exposes version numbers only.  First-history admission must
    therefore match the candidate's exact filename/checksum pairs against the
    immutable records that existed independently of the requested archive;
    integer membership alone can silently adopt edited migration source.
    """

    prefix_size = len(state.applied_migrations)
    candidate_prefix = tuple(
        item.to_mapping() for item in inputs.candidate.artifact_manifest.migrations[:prefix_size]
    )
    relevant_records = tuple(
        item
        for item in state.releases
        if set(_migration_versions_from_manifest(item.artifact_manifest)).intersection(state.applied_migrations)
    )
    complete_records = tuple(
        record for record in relevant_records
        if _migration_versions_from_manifest(record.artifact_manifest)[:prefix_size] == state.applied_migrations
    )
    return bool(complete_records) and all(
        tuple(dict(item) for item in record.migrations if int(item["filename"][:14]) in state.applied_migrations)
        == tuple(item for item in candidate_prefix if int(item["filename"][:14]) in _migration_versions_from_manifest(record.artifact_manifest))
        for record in relevant_records
    )


def _observe(
    inputs: _Inputs,
    *,
    allow_selection_transition: bool = True,
    include_runtime: bool = False,
) -> HostState:
    try:
        database = observe_database_state_or_empty(inputs.database, inputs.credentials)
    except DatabaseObservationError as error:
        raise DeploymentManualError("database migration authority is invalid") from error
    return observe_host_state(
        inputs.paths,
        database=database,
        include_runtime=include_runtime,
        allow_selection_transition=allow_selection_transition,
    )


def _repair_recorded_selection(
    inputs: _Inputs, state: HostState, *, first_release: bool
) -> tuple[HostState, bool]:
    """Repair only a recorded selection whose current-link replacement was lost.

    The normal sequence selects, starts, verifies, then records the successful
    selection.  A link that already names the candidate but lacks that record
    therefore remains unrecorded until verification succeeds on this rerun.
    The reverse arrangement can only resume the same completed selection.
    """

    if first_release and state.latest_successful_selection is None:
        # A physical selection without history is a valid failed first-install
        # attempt.  Genesis may replace it, but must not fabricate history for
        # it while reconciling the newly confirmed desired target.
        return state, False

    recorded = state.selections[-1].release_id if state.selections else None
    selected = state.selected_release_id
    if recorded == selected:
        return state, False
    allowed = {
        inputs.previous_release_id,
        inputs.candidate.release_id,
        *( () if state.latest_successful_selection is None else (state.latest_successful_selection.release_id,) ),
    }
    if recorded not in allowed or selected not in allowed:
        raise DeploymentManualError("selection transition is not attributable to this deployment")
    if selected == inputs.candidate.release_id and recorded != inputs.candidate.release_id:
        return state, False
    if selected == inputs.previous_release_id and recorded != inputs.candidate.release_id:
        # A failed prior candidate can remain physically selected without a
        # completed record.  A newly confirmed desired target must replace it
        # from the last verified predecessor, never invent that prior success.
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
        select_current(inputs.paths, inputs.candidate.release_id)
    except SelectionAmbiguityError as error:
        raise DeploymentManualError("selection temporary is unsafe") from error
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
            _observe(
                inputs,
                allow_selection_transition=False,
                include_runtime=True,
            ),
            False,
            _selection_backup(inputs, state, selection.backup_id, backup),
        )
    selection_backup = _selection_backup(inputs, state, None, backup)
    # Logical history starts at null, but recovery must retain the exact
    # physical selection observed at the confirmed start of this genesis.
    observed_previous = inputs.previous_release_id if state.latest_successful_selection is None else recorded
    try:
        _append_selection_with_previous(
            inputs.paths,
            state,
            inputs.candidate.release_id,
            observed_previous,
            selection_backup,
        )
    except (OSError, RecordError, ValueError) as error:
        raise _RetryableError("history") from error
    return (
        _observe(
            inputs,
            allow_selection_transition=False,
            include_runtime=True,
        ),
        True,
        selection_backup,
    )


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
    if not migration_needed:
        return None
    by_id = {item.backup_id: item for item in state.backups}
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
    protected = tuple(
        protection
        for protection in state.backup_protections
        if protection.base_selection_id == state.latest_successful_selection_filename
        and protection.target_release_id == inputs.candidate.release_id
    )
    if not protected:
        if inputs.previous_release_id is None and state.latest_successful_selection is None:
            # No null-baseline protection can only be the directly proved
            # empty initialization path: partial recovery publishes it before
            # migration and therefore reaches this point with durable
            # protection authority to transfer.
            return None
        raise DeploymentManualError("migration backup has no target protection authority")
    newest = max(protected, key=lambda protection: protection.attempt_number)
    try:
        return by_id[newest.backup_id]
    except KeyError as error:
        raise DeploymentManualError("target-protected migration backup is unavailable") from error


def _finish_confirmed_pruning(inputs: _Inputs, state: HostState) -> bool:
    """Complete a prior confirmed retirement before creating another backup.

    A previous interruption can leave active excess protections or durable
    retirement markers.  The controller's exact confirmation is the only
    authority to finish either state; never create another protection until it
    has converged.
    """

    pending = tuple(state.retiring_backup_protections)
    expected = (
        tuple(sorted(item.backup_id for item in pending))
        if pending
        else protection_prune_ids(
            state.backup_protections,
            state.latest_successful_selection_filename,
            independently_held_backup_ids=independent_backup_ids(state),
        )
    )
    if expected:
        retire_protection_attempts(
            inputs.paths,
            state,
            state.latest_successful_selection_filename,
            inputs.prune_backup_ids,
        )
    return bool(expected)


def _finish_pending_pruning_or_reuse(
    inputs: _Inputs, state: HostState
) -> tuple[BackupRecord | None, bool]:
    """Finish a prior authorized retirement before reobserving its authority."""

    if not _finish_confirmed_pruning(inputs, state):
        return None, False
    return None, True


def _newest_reusable_protection_backup(inputs: _Inputs, state: HostState) -> BackupRecord | None:
    protected = tuple(
        item
        for item in state.backup_protections
        if item.base_selection_id == state.latest_successful_selection_filename
    )
    if not protected:
        return None
    newest = max(protected, key=lambda item: item.attempt_number)
    if newest.target_release_id != inputs.candidate.release_id:
        return None
    backup = next((item for item in state.backups if item.backup_id == newest.backup_id), None)
    if backup is None or backup.migration_versions != inputs.expected_migrations:
        return None
    return backup


def _retire_newly_eligible_protections(inputs: _Inputs, state: HostState) -> None:
    """Retire only a sixth-attempt protection already named by the plan."""

    eligible = protection_prune_ids(
        state.backup_protections,
        state.latest_successful_selection_filename,
        independently_held_backup_ids=independent_backup_ids(state),
    )
    if eligible:
        retire_protection_attempts(
            inputs.paths,
            state,
            state.latest_successful_selection_filename,
            inputs.prune_backup_ids,
        )


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


def _normalize_staging(inputs: _Inputs) -> bool:
    path = _staging_path(inputs)
    if not path.exists() and not path.is_symlink():
        return False
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_mode & 0o7022
    ):
        raise DeploymentManualError("release staging directory is unsafe")
    try:
        shutil.rmtree(path)
        fsync_directory(path.parent)
    except OSError as error:
        raise _RetryableError("staging") from error
    return True


def _stage_or_reuse(inputs: _Inputs, state: HostState) -> bool:
    existing = next((item for item in state.releases if item.release_id == inputs.candidate.release_id), None)
    if existing is not None:
        if existing != inputs.candidate:
            raise DeploymentManualError("installed candidate identity contradicts the artifact")
        return False
    if inputs.artifact_path is None:
        raise DeploymentManualError("requested installed release is unavailable")
    root = Path(inputs.paths.local(inputs.paths.release_root))
    target = root / inputs.candidate.release_id
    if target.exists() or target.is_symlink():
        raise DeploymentManualError("release path exists without a completed manifest")
    staging = _staging_path(inputs)
    owner_uid = os.geteuid()
    owner_gid = _taskman_gid()
    try:
        staging.mkdir(mode=0o750)
        _extract_release(inputs.artifact_path, staging)
        content = staging / "taskman"
        _validate_release_tree(content)
        _normalize_release_tree(content, owner_uid, owner_gid)
        _write_release_manifest(content, inputs.candidate, owner_uid=owner_uid, owner_gid=owner_gid)
        os.replace(content, target)
        staging.rmdir()
        fsync_directory(root)
    except (OSError, tarfile.TarError, ValueError) as error:
        raise _RetryableError("staging") from error
    return True


def _staging_path(inputs: _Inputs) -> Path:
    return Path(inputs.paths.local(inputs.paths.release_root / f".release-{inputs.candidate.release_id}.tmp"))


def _taskman_gid() -> int:
    try:
        return grp.getgrnam("taskman").gr_gid
    except KeyError as error:
        raise DeploymentManualError("taskman service group is unavailable") from error


def _write_release_manifest(directory: Path, record: ReleaseRecord, *, owner_uid: int, owner_gid: int) -> None:
    target = directory / ".taskman-release.json"
    payload = json.dumps(record.to_mapping(), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(target, owner_uid, owner_gid)
    os.chmod(target, 0o600)


def _safe_artifact(inputs: _Inputs) -> None:
    if inputs.artifact_path is None:
        return
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
        or sha256_file(inputs.artifact_path) != inputs.artifact_sha256
    ):
        raise ValueError("deployment artifact is unsafe")
    try:
        with tarfile.open(inputs.artifact_path, "r:gz") as archive:
            _validate_archive_members(archive.getmembers())
    except (OSError, tarfile.TarError, ValueError) as error:
        raise ValueError("deployment artifact inventory is unsafe") from error


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


def _append_selection_with_previous(
    paths: ManagedPaths,
    state: HostState,
    candidate: str,
    previous: str | None,
    backup: BackupRecord | None = None,
) -> None:
    complete_successful_selection(
        paths,
        state,
        release_id=candidate,
        observed_previous_release_id=previous,
        backup_id=None if backup is None else backup.backup_id,
        recovery_backup_ids=() if backup is None else (backup.backup_id,),
    )


def _verify(request: HostRequest, inputs: _Inputs) -> HostResult:
    return verify(
        verification_request(request, inputs.candidate.release_id, inputs.verification),
        lifecycle_locked=True,
    )


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


def _normalize_release_tree(path: Path, owner_uid: int, owner_gid: int) -> None:
    for item in (path, *path.rglob("*")):
        details = item.lstat()
        if stat.S_ISLNK(details.st_mode):
            os.lchown(item, owner_uid, owner_gid)
            continue
        os.chown(item, owner_uid, owner_gid)
        os.chmod(item, 0o750 if stat.S_ISDIR(details.st_mode) or details.st_mode & 0o111 else 0o640)


def _failure_result(
    request: HostRequest,
    outcome: str,
    message: str,
    state: HostState | None,
    inputs: _Inputs | None,
    *,
    boundary: str | None = None,
    changed: bool = False,
    possibly_changed: bool = False,
    backup_id: str | None = None,
    report: object | None = None,
) -> HostResult:
    """Return one fresh final observation without treating a retry as success."""

    final_state, observations, unavailable, inspection_error = _final_failure_observation(
        request, inputs, state
    )
    return _result(
        request,
        outcome,
        message,
        final_state,
        boundary=boundary,
        changed=changed,
        possibly_changed=possibly_changed,
        backup_id=backup_id,
        report=report,
        final_observations=observations,
        final_unavailable=unavailable,
        final_inspection_error=inspection_error,
    )


def _final_failure_observation(
    request: HostRequest,
    inputs: _Inputs | None,
    state: HostState | None,
) -> tuple[HostState | None, Mapping[str, object] | None, tuple[str, ...], str | None]:
    """Observe physical authority once after failure, never using the plan as fact."""

    if inputs is None:
        return state, None, (), None
    try:
        with lifecycle_lock(inputs.paths, timeout_seconds=_LOCK_TIMEOUT_SECONDS):
            observed = _observe(inputs, include_runtime=True)
            try:
                scheduler = _scheduler_facts(inputs.paths)
            except (CommandError, OSError, StateAmbiguityError):
                scheduler = {
                    "scheduled_backup_sha256": None,
                    "backup_timer_enabled": None,
                    "backup_timer_state": "unknown",
                }
            observations = mutation_observations(observed, request.operation, scheduler=scheduler)
            unavailable, inspection_error = mutation_observation_availability(
                request.operation, observations
            )
            return observed, observations, unavailable, inspection_error
    except (
        LifecycleLockContention,
        CommandError,
        DatabaseObservationError,
        DeploymentManualError,
        OSError,
        PathAuthorityError,
        RecordError,
        StateAmbiguityError,
        ValueError,
    ):
        return state, None, (), None


def _result(
    request: HostRequest,
    outcome: str,
    message: str,
    state: HostState | None,
    *,
    boundary: str | None = None,
    locked: bool = False,
    changed: bool | None = None,
    possibly_changed: bool = False,
    backup_id: str | None = None,
    database_state: str | None = None,
    service_state: str | None = None,
    report: object | None = None,
    final_observations: Mapping[str, object] | None = None,
    final_unavailable: tuple[str, ...] = (),
    final_inspection_error: str | None = None,
) -> HostResult:
    if outcome == "succeeded":
        exit_code, failed_boundary = 0, None
    elif locked:
        exit_code, failed_boundary = 12, "lock"
    else:
        failed_boundary = boundary or ("authority" if outcome == "manual" else "input")
        exit_code = {
            "input": 2,
            "authority": 10,
            "expected_state": 10,
            "backup_helper": 8,
            "staging": 8,
            "backup": 6,
            "protection": 8,
            "migration": 7,
            "selection": 8,
            "start": 8,
            "service": 8,
            "verification": 9,
            "history": 8,
            "observation": 5,
            "inspection": 5,
        }.get(failed_boundary, 10)
        if failed_boundary == "start":
            failed_boundary = "service"
    if final_observations is None:
        observations, unavailable = unavailable_observations(request.operation)
        inspection_error = "unsafe-observation"
    else:
        observations = dict(final_observations)
        unavailable = list(final_unavailable)
        inspection_error = final_inspection_error
    mutation_state = "changed" if changed else ("unknown" if possibly_changed else "unchanged")
    desired = _requested_release_id(request)
    facts: dict[str, object] = {
        "mutation_state": mutation_state,
        "exit_code": exit_code,
        "failed_boundary": failed_boundary,
        "observations": observations,
        "unavailable_fields": unavailable,
        "inspection_error": inspection_error,
        "report": report,
        "desired_release_id": desired,
        "backup_id": backup_id,
    }
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.correlation_id,
        outcome,
        message,
        facts,
        () if state is None else state.warnings,
    )


def _requested_release_id(request: HostRequest) -> str | None:
    """Best-effort identity for a result emitted after request parsing fails."""

    try:
        target = request.parameters["target"]
        if not isinstance(target, Mapping):
            return None
        if target.get("kind") == "upload":
            return ArtifactManifest.from_mapping(_mutable(target["manifest"])).release_id
        if target.get("kind") == "installed":
            return ReleaseRecord.from_mapping(_mutable(target["release_record"])).release_id
    except (KeyError, TypeError, ValueError, RecordError):
        pass
    return None


def _mutable(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _mutable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable(item) for item in value]
    return value


__all__ = ["converge_deployment", "deploy", "genesis"]
