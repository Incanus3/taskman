"""Explicit helper-owned deployment and clean-host genesis transactions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import time
from uuid import uuid4

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION

from ..facts import LifecycleObservation, _observe_current
from ..lifecycle import (
    ActivationRecord,
    BackupRecord,
    LifecycleError,
    LifecycleFinalizationEffect,
    LifecycleFinalizationFailure,
    LifecycleLockContention,
    LifecycleRecords,
    LifecycleStore,
    LifecycleWriteFailure,
    ManualAdoptionCandidate,
    ReleaseRecord,
    StagedRelease,
    validate_lifecycle_records,
)
from ..paths import ManagedPaths, PathAuthorityError
from ..runtime import RuntimeFailure, RuntimeResult, TransactionRuntime, TransactionStage
from ..verification import available_bytes, host_preflight, verify


_RELEASE_RE = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26\.04-amd64-otp27\.3\.4\.6\Z"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required"})
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version", "application", "application_version", "source_revision", "release_id",
        "built_at", "target_os", "architecture", "otp_version", "elixir_version", "node_version",
        "hex_version", "rebar3_version", "builder_base_tag", "builder_base_digest", "migrations", "top_level",
    }
)
_MIGRATION_FIELDS = frozenset({"filename", "sha256"})
_MIGRATION_FILENAME_RE = re.compile(r"[0-9]{14}_[a-z0-9_]+\.exs\Z")
_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_EXPANDED_ARCHIVE_BYTES = 10 * 1024 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 16_384
_MAX_COMMAND_SECONDS = 30.0
# The common runtime reserves every stage's bounded command/filesystem work
# plus an evidence/cleanup margin before beginning it.  The controller runner
# keeps a further minute for transport and protocol decoding.  The full
# worst-case stage program is 435 seconds including its final evidence margin;
# this total leaves bounded preflight and cleanup time without letting the
# runner terminate a stage before it can return structured evidence.
_OPERATION_DEADLINE_SECONDS = 600.0
_RUNTIME_ENVIRONMENT = Path("/etc/taskman/taskman.env")
_PGPASS = Path("/etc/taskman/pgpass")
_STAGING_RECOVERY = ("inspect the immutable release staging evidence before retrying",)
_BACKUP_PUBLICATION_RECOVERY = (
    "preserve the validated deployment backup and inspect its lifecycle record before retrying",
)
_SELECTION_RECOVERY = ("inspect the current release selection before retrying",)
_RECORDS_RECOVERY = (
    "inspect published lifecycle records and the provisional activation before retrying",
)


@dataclass(frozen=True)
class _Inputs:
    paths: ManagedPaths
    previous_release_id: str | None
    candidate_release_id: str
    artifact_sha256: str
    artifact_path: Path
    manifest: Mapping[str, object]
    migration_policy: str
    current_migrations: tuple[Mapping[str, object], ...]
    candidate_migrations: tuple[Mapping[str, object], ...]
    settings: Mapping[str, object]
    manual_adoption: ManualAdoptionCandidate | None


def deploy(request: HostRequest) -> HostResult:
    """Run an existing-host deploy after helper-owned request validation.

    The complete transaction is attached below in this module; validating the
    bounded non-secret envelope first ensures malformed controller data never
    creates roots, records, uploads, or an activation edge.
    """

    try:
        inputs = _inputs(request, genesis=False)
    except ValueError:
        return _preflight_refusal(request)
    return _run(request, inputs, genesis=False)


def genesis(request: HostRequest) -> HostResult:
    """Run the explicit clean-host deploy with an absent predecessor."""

    try:
        inputs = _inputs(request, genesis=True)
    except ValueError:
        return _preflight_refusal(request)
    return _run(request, inputs, genesis=True)


def _inputs(request: HostRequest, *, genesis: bool) -> _Inputs:
    expected = request.expected_state
    if not isinstance(expected, Mapping) or set(expected) != {"previous_release_id", "current_migrations"}:
        raise ValueError("invalid expected deployment state")
    previous = expected["previous_release_id"]
    current_migrations = _migration_fingerprints(expected["current_migrations"])
    if genesis:
        if previous is not None or current_migrations:
            raise ValueError("genesis predecessor is not absent")
    elif type(previous) is not str or _RELEASE_RE.fullmatch(previous) is None:
        raise ValueError("invalid expected previous release")

    parameters = request.parameters
    required = {
        "candidate_release_id",
        "artifact_sha256",
        "artifact_path",
        "manifest",
        "migration_policy",
        "verification",
        "manual_adoption",
    }
    if not isinstance(parameters, Mapping) or set(parameters) != required:
        raise ValueError("invalid deployment parameters")
    candidate = parameters["candidate_release_id"]
    artifact_sha256 = parameters["artifact_sha256"]
    artifact_path = parameters["artifact_path"]
    manifest = parameters["manifest"]
    policy = parameters["migration_policy"]
    if (
        type(candidate) is not str
        or _RELEASE_RE.fullmatch(candidate) is None
        or type(artifact_sha256) is not str
        or _SHA256_RE.fullmatch(artifact_sha256) is None
        or type(artifact_path) is not str
        or not artifact_path.startswith("/")
        or "/../" in artifact_path
        or not isinstance(manifest, Mapping)
        or type(policy) is not str
        or policy not in _POLICIES
    ):
        raise ValueError("invalid deployment parameters")
    candidate_migrations = _validate_manifest(manifest, candidate)
    _validate_migration_policy(current_migrations, candidate_migrations, policy)
    try:
        paths = ManagedPaths.from_mapping(request.paths)
    except PathAuthorityError as error:
        raise ValueError("invalid deployment paths") from error
    if not PurePosixPath(artifact_path).is_relative_to(paths.deployment_root / "uploads"):
        raise ValueError("artifact is outside the derived upload authority")
    settings = _settings(parameters["verification"])
    manual = parameters["manual_adoption"]
    if manual is None:
        manual_candidate = None
    else:
        try:
            manual_candidate = ManualAdoptionCandidate.from_mapping(_mutable_mapping(manual))
        except (LifecycleError, TypeError, ValueError) as error:
            raise ValueError("invalid manual-adoption authority") from error
        if genesis or manual_candidate.release_id != previous:
            raise ValueError("invalid manual-adoption authority")
    return _Inputs(
        paths=paths,
        previous_release_id=previous if isinstance(previous, str) else None,
        candidate_release_id=candidate,
        artifact_sha256=artifact_sha256,
        artifact_path=Path(artifact_path),
        manifest=_mutable_mapping(manifest),
        migration_policy=policy,
        current_migrations=current_migrations,
        candidate_migrations=candidate_migrations,
        settings=settings,
        manual_adoption=manual_candidate,
    )


def _preflight_refusal(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="refused",
        stage="deploy-preflight",
        changed_stages=(),
        lifecycle={},
        runtime_state={},
        verification={},
        residue_paths=(),
        recovery_actions=("correct the confirmed deployment input before retrying",),
        warnings=(),
    )


def _run(request: HostRequest, inputs: _Inputs, *, genesis: bool) -> HostResult:
    """Execute one explicit deploy policy through the common runtime."""

    try:
        inputs.paths.validate_existing(owner_uid=os.geteuid())
        store = LifecycleStore(inputs.paths)
        state = _DeploymentState(request, inputs, store, genesis=genesis)
        runtime = TransactionRuntime(
            operation="genesis" if genesis else "deploy",
            stages=state.stages(),
            acquire_lock=state.acquire_lock,
            discover=state.rediscover,
            deadline=time.monotonic() + _OPERATION_DEADLINE_SECONDS,
        )
        state.runtime = runtime
        runtime.register_cleanup(inputs.artifact_path.as_posix(), state.cleanup_upload)
        runtime.register_cleanup(state.stage_path.as_posix(), state.cleanup_staging)
        runtime.register_cleanup(state.selection_path.as_posix(), state.cleanup_selection)
        result = runtime.run()
    except LifecycleLockContention as error:
        return _lock_failure(request, error)
    except (LifecycleError, PathAuthorityError, ValueError):
        return _preflight_refusal(request)
    return _result(request, result, state)


class _DeploymentState:
    """One operation's explicit state; this is not a reusable workflow DSL."""

    def __init__(self, request: HostRequest, inputs: _Inputs, store: LifecycleStore, *, genesis: bool) -> None:
        self.request = request
        self.inputs = inputs
        self.store = store
        self.genesis = genesis
        self.records: LifecycleRecords | None = None
        self.backup: BackupRecord | None = None
        self.staged: StagedRelease | None = None
        self.activated_at: datetime | None = None
        self.verification: Mapping[str, object] = {}
        self.service_state = "unknown"
        self.database_state = "unchanged"
        self.noop = False
        self.database_size = 0
        self.expanded_archive_size = 0
        self.selected_release_id: str | None = None
        self.backup_id: str | None = None
        self.activation_recorded = False
        self.activation_id: str | None = None
        self.resuming = False
        self.selection_applied = False
        self.manual_adoption_pending: ManualAdoptionCandidate | None = None
        self.residue_paths: list[str] = []
        self.runtime: TransactionRuntime | None = None
        self._lock: AbstractContextManager[None] | None = None

    @property
    def stage_path(self) -> Path:
        return self.store.release_root / f".stage-{self.request.operation_id}"

    @property
    def selection_path(self) -> Path:
        return self.store.paths.local(self.store.paths.install_root) / f".current-{self.request.operation_id}"

    def acquire_lock(self) -> Callable[[], None]:
        self._lock = self.store.exclusive_lifecycle_lock(operation=self.request.operation)
        self._lock.__enter__()

        def release() -> None:
            assert self._lock is not None
            self._lock.__exit__(None, None, None)

        return release

    def rediscover(self) -> Mapping[str, object]:
        self.inputs.paths.validate_existing(owner_uid=self.store.owner_uid)
        verification = {
            name: self.inputs.settings[name]
            for name in (
                "application_port", "distribution_port", "database_port", "public_hostname",
                "public_ipv4", "public_ipv6", "ssh_port", "ssh_user", "readiness_timeout",
                "connection_timeout",
            )
        }
        authority = host_preflight(self.inputs.paths, verification)
        if authority is not None:
            raise RuntimeFailure(
                "deploy-preflight",
                "host deployment prerequisites failed",
                outcome="refused",
                recovery_actions=("correct the reported host prerequisite before retrying",),
            )
        try:
            _safe_secret_file(_PGPASS, self.store.owner_uid)
            self.database_size = _database_size(self.inputs.settings)
        except RuntimeFailure as error:
            raise RuntimeFailure(
                "deploy-preflight", "database backup prerequisites failed", outcome="refused",
                recovery_actions=("correct the database backup prerequisite before retrying",),
            ) from error
        if self.database_size <= 0:
            raise RuntimeFailure(
                "deploy-preflight", "database size evidence is invalid", outcome="refused",
                recovery_actions=("correct the database backup prerequisite before retrying",),
            )
        try:
            _safe_upload(self.inputs.artifact_path, self.store.owner_uid, self.store.deployment_root / "uploads")
            _require_digest(self.inputs.artifact_path, self.inputs.artifact_sha256)
            self.expanded_archive_size = _archive_expanded_size(self.inputs.artifact_path)
        except (LifecycleError, OSError) as error:
            raise RuntimeFailure(
                "deploy-preflight", "release archive preflight failed", outcome="refused",
                recovery_actions=("correct the verified release artifact before retrying",),
            ) from error
        if available_bytes(self.store.release_root) < self.expanded_archive_size:
            raise RuntimeFailure(
                "deploy-preflight", "release filesystem capacity is insufficient", outcome="refused",
                recovery_actions=("free release storage before retrying",),
            )
        if available_bytes(self.store.backup_root) < self.database_size:
            raise RuntimeFailure(
                "deploy-preflight", "backup filesystem capacity is insufficient", outcome="refused",
                recovery_actions=("free backup storage before retrying",),
            )
        manual_current: ManualAdoptionCandidate | None = None
        if self.inputs.manual_adoption is not None:
            observed = self.store.inspect_manual_current_locked()
            if observed != self.inputs.manual_adoption:
                raise RuntimeFailure(
                    "deploy-preflight",
                    "confirmed manual adoption no longer matches host authority",
                    outcome="refused",
                    recovery_actions=("review the current release and confirm a new deployment plan",),
                )
            self._validate_current_migrations(observed.migrations)
            manual_current = observed
            self.manual_adoption_pending = observed
            # A manual current link deliberately has no lifecycle activation
            # yet, so acceptance through ``read`` would reject it.  Adoption
            # remains an active staging mutation; this is only a lock-held
            # observation used to bind the confirmed predecessor.
            records = self.store.observe()
            staged = None
            previous = observed.release_id
        else:
            staged = self._resumable_stage()
            try:
                records = self.store.read()
            except LifecycleError as error:
                if staged is None:
                    raise RuntimeFailure(
                        "deploy-preflight", "lifecycle rediscovery is unsafe", outcome="refused",
                        recovery_actions=("inspect the interrupted lifecycle before retrying",),
                    ) from error
                records = self.store.observe()
            if staged is not None:
                self._accept_resume(staged, records)
            previous = records.current_release_id
        if self.genesis:
            # A retained matching provisional is an incomplete finalization,
            # even when all of its final records already describe a completed
            # genesis. Finish retirement before accepting ordinary no-op.
            if self.resuming:
                pass
            elif self._matching_completed_genesis(records):
                self.noop = True
                self.selected_release_id = self.inputs.candidate_release_id
                activation = records.activations[-1]
                self.activation_id = activation.activation_id
                self.activation_recorded = True
            elif previous is not None or records.releases or records.activations or records.adoptions:
                raise RuntimeFailure(
                    "deploy-preflight",
                    "genesis lifecycle is not empty",
                    outcome="refused",
                    recovery_actions=("inspect the existing lifecycle before choosing deployment instead of genesis",),
                )
        elif previous != self.inputs.previous_release_id:
            raise RuntimeFailure(
                "deploy-preflight",
                "confirmed previous release no longer matches host authority",
                outcome="refused",
                recovery_actions=("review the current release and confirm a new deployment plan",),
            )
        elif manual_current is None:
            self._validate_current_migrations(self._current_migrations(records))
            if previous == self.inputs.candidate_release_id and records.activations:
                activation = records.activations[-1]
                self.activation_id = activation.activation_id
                self.activation_recorded = True
        self.records = records
        if self.selected_release_id is None:
            self.selected_release_id = previous
        return {
            "previous_release_id": previous,
            "candidate_release_id": self.inputs.candidate_release_id,
        }

    def _current_migrations(self, records: LifecycleRecords) -> tuple[Mapping[str, object], ...]:
        previous = records.current_release_id
        if previous is None:
            return ()
        adoption = next((record for record in records.adoptions if record.release_id == previous), None)
        if adoption is not None:
            return tuple(dict(item) for item in adoption.migrations)
        manifest = self.store.read_manifest(previous)
        if not isinstance(manifest, Mapping):
            raise RuntimeFailure("deploy-preflight", "current release manifest is invalid", outcome="refused")
        return _validate_manifest(manifest, previous)

    def _validate_current_migrations(self, observed: tuple[Mapping[str, object], ...]) -> None:
        if observed != self.inputs.current_migrations:
            raise RuntimeFailure(
                "deploy-preflight",
                "confirmed migration fingerprints no longer match host authority",
                outcome="refused",
                recovery_actions=("review the current release and confirm a new deployment plan",),
            )
        try:
            _validate_migration_policy(observed, self.inputs.candidate_migrations, self.inputs.migration_policy)
        except ValueError as error:
            raise RuntimeFailure(
                "deploy-preflight",
                "confirmed migration policy no longer matches host authority",
                outcome="refused",
                recovery_actions=("review migration fingerprints and confirm a matching deployment policy",),
            ) from error

    def stages(self) -> tuple[TransactionStage, ...]:
        stages: list[TransactionStage] = [
            TransactionStage("staging", self.stage),
            TransactionStage("backup", self.backup_database),
        ]
        if not self.genesis:
            stages.append(TransactionStage("stop", self.stop))
        stages.extend(
            (
                TransactionStage("migration", self.migrate),
                TransactionStage("selection", self.select),
                TransactionStage("start", self.start),
                TransactionStage("verification", self.verify),
                TransactionStage("records", self.publish_records),
            )
        )
        return tuple(stages)

    def stage(self) -> bool:
        if self.records is None:
            raise RuntimeFailure("staging", "lifecycle rediscovery is unavailable")
        self._ensure_roots()
        if self.manual_adoption_pending is not None:
            # Manual adoption publishes a release/activation bundle before
            # ordinary candidate staging.  It is a real host mutation and is
            # deliberately represented by the already-active staging stage.
            self._mark_changed("staging")
            try:
                self.store.adopt_manual_current_locked(self.manual_adoption_pending)
                self.records = self.store.read()
            except LifecycleWriteFailure as error:
                # A marker may have reached its final name before its parent
                # fsync failed; staging was already marked conservatively.
                raise RuntimeFailure("staging", "unable to publish manual adoption") from error
            except LifecycleError as error:
                raise RuntimeFailure("staging", "unable to publish manual adoption") from error
            self.manual_adoption_pending = None
            self.selected_release_id = self.records.current_release_id
        if self.resuming:
            # A retained provisional remains mutable transaction authority even
            # when its exact final records are already present.  The records
            # stage must revalidate that prefix and retire the provisional;
            # treating it as an already-current no-op would strand residue.
            self.store.require_release_directory(self.store.release_path(self.inputs.candidate_release_id))
            return False
        existing = next((record for record in self.records.releases if record.release_id == self.inputs.candidate_release_id), None)
        if existing is not None:
            if existing.artifact_sha256 != self.inputs.artifact_sha256:
                raise RuntimeFailure("staging", "existing immutable release does not match artifact", outcome="refused", recovery_actions=("select a new release identifier for different artifact content",))
            self.store.require_release_directory(self.store.release_path(self.inputs.candidate_release_id))
            if self.records.current_release_id == self.inputs.candidate_release_id:
                self.noop = True
                self.selected_release_id = self.inputs.candidate_release_id
            return False
        _safe_upload(self.inputs.artifact_path, self.store.owner_uid, self.store.deployment_root / "uploads")
        _require_digest(self.inputs.artifact_path, self.inputs.artifact_sha256)
        release_root = self.store.release_root
        target = self.store.release_path(self.inputs.candidate_release_id)
        stage = self.stage_path
        if stage.exists() or stage.is_symlink():
            raise RuntimeFailure("staging", "operation staging path already exists", outcome="refused")
        try:
            stage.mkdir(mode=0o750)
            _extract_release(self.inputs.artifact_path, stage)
            content = stage / "taskman"
            _validate_release_tree(content)
            _normalize_release_tree(content, self.store.owner_uid)
            _write_marker(content, self.inputs.candidate_release_id, self.inputs.artifact_sha256, self.store.owner_uid)
            if target.exists() or target.is_symlink():
                raise LifecycleError("immutable release identifier already exists")
            os.rename(content, target)
            self._mark_changed("staging")
            _fsync_directory(release_root)
            return True
        except RuntimeFailure:
            raise
        except (OSError, tarfile.TarError, LifecycleError) as error:
            raise RuntimeFailure(
                "staging", "unable to stage immutable release", recovery_actions=_STAGING_RECOVERY
            ) from error
        finally:
            # Runtime cleanup owns the exact generated path.  It records
            # residue rather than silently discarding a failed removal.
            pass

    def backup_database(self) -> bool:
        if self.noop or self.resuming:
            return False
        if self.records is None:
            raise RuntimeFailure("backup", "lifecycle rediscovery is unavailable")
        backup_id = f"backup-{self.request.operation_id.removeprefix('op-')}"
        self.backup_id = backup_id
        dump_path = self.store.backup_root / f"{backup_id}.dump"
        if dump_path.exists() or dump_path.is_symlink():
            raise RuntimeFailure("backup", "deployment backup path already exists", outcome="refused")
        _safe_secret_file(_PGPASS, self.store.owner_uid)
        source_size = self.database_size
        if source_size <= 0:
            raise RuntimeFailure("backup", "database size evidence is invalid")
        try:
            _run_command("backup",
                (
                    "pg_dump", "--format=custom", "--file", dump_path.as_posix(),
                    "--host", str(self.inputs.settings["database_host"]),
                    "--port", str(self.inputs.settings["database_port"]),
                    "--username", str(self.inputs.settings["database_role"]),
                    str(self.inputs.settings["database_name"]),
                ),
                env={"PGPASSFILE": _PGPASS.as_posix()},
            )
        except RuntimeFailure:
            if dump_path.exists() or dump_path.is_symlink():
                self._mark_changed("backup")
            raise
        self._mark_changed("backup")
        try:
            details = dump_path.lstat()
            if not stat.S_ISREG(details.st_mode) or details.st_uid != self.store.owner_uid or details.st_size <= 0:
                raise LifecycleError("backup dump is unsafe")
            os.chmod(dump_path, 0o600)
            _run_command("backup", ("pg_restore", "--list", dump_path.as_posix()), env={"PGPASSFILE": _PGPASS.as_posix()})
            dump_sha256 = _file_sha256(dump_path)
        except RuntimeFailure:
            if dump_path.exists() or dump_path.is_symlink():
                self.residue_paths.append(dump_path.as_posix())
            raise
        except (OSError, LifecycleError) as error:
            if dump_path.exists() or dump_path.is_symlink():
                self.residue_paths.append(dump_path.as_posix())
            raise RuntimeFailure("backup", "backup validation failed") from error
        self.backup = BackupRecord(
            1, backup_id, datetime.now(UTC).replace(microsecond=0), details.st_size,
            source_size, str(self.inputs.settings["database_name"]),
            self.inputs.previous_release_id, self.inputs.candidate_release_id,
            "pre-deploy", True, PurePosixPath(dump_path.as_posix()),
            dump_sha256,
        )
        try:
            self.store.write_backup(self.backup)
        except LifecycleError as error:
            # The valid dump is a recovery artifact even when the no-replace
            # record publication failed after it was created and validated.
            if dump_path.exists() or dump_path.is_symlink():
                self.residue_paths.append(dump_path.as_posix())
            raise RuntimeFailure(
                "backup",
                "unable to publish validated backup",
                recovery_actions=_BACKUP_PUBLICATION_RECOVERY,
            ) from error
        return True

    def stop(self) -> bool:
        if self.noop or self.resuming:
            return False
        try:
            _run_command("stop", ("systemctl", "stop", "taskman.service"))
        except RuntimeFailure:
            self.service_state = _observed_service_state()
            if self.service_state != "active":
                self._mark_changed("stop")
            raise
        self.service_state = _observed_service_state()
        self._mark_changed("stop")
        return True

    def migrate(self) -> bool:
        if self.noop or self.resuming:
            return False
        candidate = self.store.release_path(self.inputs.candidate_release_id) / "bin" / "migrate"
        if self.inputs.migration_policy != "no-change":
            # The migration runner can commit before it exits or reports a
            # failure. The exact terminal database state is not observable
            # here, so retain conservative changed-stage evidence.
            self.database_state = "unknown"
            self._mark_changed("migration")
        _run_command("migration",
            ("systemd-run", "--wait", "--quiet", "--collect", "--property=User=taskman", "--property=Group=taskman", f"--property=EnvironmentFile={_RUNTIME_ENVIRONMENT}", candidate.as_posix())
        )
        self.database_state = "changed" if self.inputs.migration_policy != "no-change" else "unchanged"
        return True

    def select(self) -> bool:
        if self.noop:
            return False
        if self.resuming:
            assert self.staged is not None
            if self.selection_applied:
                return False
            now = self.staged.activated_at
        else:
            assert self.backup is not None
            now = datetime.now(UTC).replace(microsecond=0)
            activation_id = f"activation-{self.request.operation_id.removeprefix('op-')}"
            self.staged = StagedRelease(
                1, activation_id, self.inputs.previous_release_id, self.inputs.candidate_release_id,
                self.backup.backup_id, self.inputs.migration_policy, self.inputs.artifact_sha256,
                now, now, self.inputs.manifest,
            )
            self.activation_id = activation_id
        try:
            if not self.resuming:
                self.store.stage_immutable_locked(self.staged)
                self._mark_changed("selection")
            temporary = self.selection_path
            if temporary.exists() or temporary.is_symlink():
                raise LifecycleError("activation selection path already exists")
            temporary.symlink_to(self.store.release_path(self.inputs.candidate_release_id))
            os.replace(temporary, self.store.current_link)
            self._mark_changed("selection")
            self.selected_release_id = self.inputs.candidate_release_id
            _fsync_directory(self.store.paths.local(self.store.paths.install_root))
        except LifecycleWriteFailure as error:
            if error.effect.published:
                self._mark_changed("selection")
            self.selected_release_id = self._observed_selected_release()
            raise RuntimeFailure(
                "selection",
                "unable to atomically select staged release",
                recovery_actions=_SELECTION_RECOVERY,
            ) from error
        except LifecycleError as error:
            self.selected_release_id = self._observed_selected_release()
            raise RuntimeFailure(
                "selection",
                "unable to atomically select staged release",
                recovery_actions=_SELECTION_RECOVERY,
            ) from error
        except OSError as error:
            self.selected_release_id = self._observed_selected_release()
            raise RuntimeFailure(
                "selection",
                "unable to atomically select staged release",
                recovery_actions=_SELECTION_RECOVERY,
            ) from error
        self.activated_at = now
        return True

    def start(self) -> bool:
        if self.noop:
            return False
        try:
            _run_command("start", ("systemctl", "start", "taskman.service"))
        except RuntimeFailure:
            self.service_state = _observed_service_state()
            if self.service_state == "active":
                self._mark_changed("start")
            raise
        self.service_state = _observed_service_state()
        self._mark_changed("start")
        return True

    def verify(self) -> bool:
        request = HostRequest(
            protocol_version=PROTOCOL_VERSION,
            operation="verify",
            operation_id=self.request.operation_id,
            expected_state={"expected_release_id": self.inputs.candidate_release_id},
            paths={"install_root": self.inputs.paths.install_root.as_posix(), "backup_root": self.inputs.paths.backup_root.as_posix()},
            parameters={
                name: self.inputs.settings[name]
                for name in (
                    "application_port", "distribution_port", "database_port", "public_hostname",
                    "public_ipv4", "public_ipv6", "ssh_port", "ssh_user", "readiness_timeout",
                    "connection_timeout",
                )
            },
        )
        result = verify(request, lifecycle_locked=True)
        self.verification = result.verification
        if result.outcome != "succeeded" or result.stage != "verified":
            if result.stage == "host-preflight":
                raise RuntimeFailure("verification", "host verification prerequisites failed", recovery_actions=result.recovery_actions)
            if result.stage == "lifecycle-lock":
                raise RuntimeFailure("lifecycle-lock", "lifecycle lock contention", recovery_actions=result.recovery_actions)
            raise RuntimeFailure("verification", "selected release verification failed", recovery_actions=result.recovery_actions)
        self.service_state = "active"
        return False

    def publish_records(self) -> bool:
        if self.noop:
            return False
        if self.staged is None or self.activated_at is None:
            raise RuntimeFailure("records", "activation staging authority is unavailable")
        release = ReleaseRecord(
            1, self.inputs.candidate_release_id, self.inputs.artifact_sha256,
            self.staged.installed_at, self.activated_at, self.inputs.previous_release_id,
            self.staged.backup_id, self.inputs.migration_policy,
        )
        activation = ActivationRecord(
            1, self.staged.activation_id, self.inputs.previous_release_id,
            self.inputs.candidate_release_id, self.activated_at, self.staged.backup_id,
            self.inputs.migration_policy,
        )
        try:
            self.store.finalize_staged_locked(self.staged, release, activation)
        except LifecycleFinalizationFailure as error:
            self._record_finalization_effect(error.effect)
            raise RuntimeFailure(
                "records",
                "unable to publish verified lifecycle records",
                recovery_actions=_RECORDS_RECOVERY,
            ) from error
        except LifecycleError as error:
            self.activation_recorded = self._activation_marker_exists(self.staged.activation_id)
            if self.activation_recorded:
                self._mark_changed("records")
            raise RuntimeFailure(
                "records",
                "unable to publish verified lifecycle records",
                recovery_actions=_RECORDS_RECOVERY,
            ) from error
        self.activation_recorded = True
        self._mark_changed("records")
        return True

    def _record_finalization_effect(self, effect: object) -> None:
        """Keep every known partial record actionable after finalization fails."""

        if not isinstance(effect, LifecycleFinalizationEffect):
            raise RuntimeFailure("records", "lifecycle finalization effect is invalid")
        if effect.changed:
            self._mark_changed("records")
        self.residue_paths.extend(path.as_posix() for path in effect.published_paths)
        self.residue_paths.extend(path.as_posix() for path in effect.residue_paths)
        if effect.provisional_path is not None:
            self.residue_paths.append(effect.provisional_path.as_posix())
        self.activation_recorded = self._activation_marker_exists(self.staged.activation_id) if self.staged else False

    def _mark_changed(self, stage: str) -> None:
        if self.runtime is not None:
            self.runtime.mark_changed(stage)

    def _activation_marker_exists(self, activation_id: str) -> bool:
        path = self.store.deployment_root / "activations" / f"{activation_id}.json"
        try:
            details = path.lstat()
        except OSError:
            return False
        return stat.S_ISREG(details.st_mode) and not stat.S_ISLNK(details.st_mode)

    def _observed_selected_release(self) -> str | None:
        """Return the selected candidate only after reobserving the symlink."""

        try:
            target, is_symlink = _observe_current(self.store)
        except LifecycleError:
            return None
        candidate_path = self.store.release_path(self.inputs.candidate_release_id).as_posix()
        if is_symlink and target == candidate_path:
            return self.inputs.candidate_release_id
        return self.records.current_release_id if self.records is not None else None

    def _resumable_stage(self) -> StagedRelease | None:
        activation_id = f"activation-{self.request.operation_id.removeprefix('op-')}"
        path = self.store.deployment_root / "provisionals" / f"{activation_id}.json"
        if not path.exists() and not path.is_symlink():
            try:
                return self.store.resume_matching_immutable_locked(
                    self.inputs.candidate_release_id, self.inputs.artifact_sha256
                )
            except LifecycleError as error:
                raise RuntimeFailure(
                    "deploy-preflight", "immutable resume authority is invalid", outcome="refused",
                    recovery_actions=("inspect the interrupted lifecycle before retrying",),
                ) from error
        try:
            return self.store.resume_immutable_locked(
                activation_id, self.inputs.candidate_release_id, self.inputs.artifact_sha256
            )
        except LifecycleError as error:
            raise RuntimeFailure(
                "deploy-preflight", "immutable resume authority is invalid", outcome="refused",
                recovery_actions=("inspect the interrupted lifecycle before retrying",),
            ) from error

    def _accept_resume(self, staged: StagedRelease, records: LifecycleRecords) -> None:
        if (
            staged.previous_release_id != self.inputs.previous_release_id
            or staged.migration_policy != self.inputs.migration_policy
            or dict(staged.manifest) != dict(self.inputs.manifest)
        ):
            raise RuntimeFailure(
                "deploy-preflight", "immutable resume authority does not match the confirmed plan",
                outcome="refused",
                recovery_actions=("review the interrupted lifecycle and confirm a new deployment plan",),
            )
        if staged.backup_id is None or not any(record.backup_id == staged.backup_id for record in records.backups):
            raise RuntimeFailure(
                "deploy-preflight", "immutable resume backup authority is absent", outcome="refused",
                recovery_actions=("inspect the interrupted lifecycle before retrying",),
            )
        try:
            target, is_symlink = _observe_current(self.store)
        except LifecycleError as error:
            raise RuntimeFailure("deploy-preflight", "immutable resume selection is invalid", outcome="refused") from error
        candidate_path = self.store.release_path(self.inputs.candidate_release_id).as_posix()
        if target not in {None, candidate_path} or not is_symlink and target is not None:
            raise RuntimeFailure(
                "deploy-preflight", "immutable resume selection no longer matches host authority", outcome="refused",
                recovery_actions=("inspect the interrupted lifecycle before retrying",),
            )
        self.staged = staged
        self.backup = next(record for record in records.backups if record.backup_id == staged.backup_id)
        self._validate_resumable_backup(self.backup)
        self.backup_id = staged.backup_id
        self.activation_id = staged.activation_id
        self.activated_at = staged.activated_at
        self.database_state = "unchanged" if staged.migration_policy == "no-change" else "changed"
        self.resuming = True
        self.selection_applied = target == candidate_path
        self.selected_release_id = self.inputs.candidate_release_id if self.selection_applied else records.current_release_id

    def _validate_resumable_backup(self, backup: BackupRecord) -> None:
        """Revalidate a historical dump before a fresh-ID retry can start.

        The provisional only proves the plan that once selected this candidate;
        it cannot authorize a later service start if the recorded recovery
        dump was removed, swapped, made unsafe, or became unparsable.
        """

        dump = Path(backup.dump_path.as_posix())
        expected_dump = (
            self.store.backup_root / f"{backup.backup_id}.dump"
        )
        try:
            details = dump.lstat()
            if (
                dump != expected_dump
                or stat.S_ISLNK(details.st_mode)
                or not stat.S_ISREG(details.st_mode)
                or details.st_uid != self.store.owner_uid
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_size <= 0
                or details.st_size != backup.size_bytes
                or backup.dump_sha256 is None
                or _file_sha256(dump) != backup.dump_sha256
            ):
                raise LifecycleError("recorded backup dump is unsafe")
            _run_command(
                "backup",
                ("pg_restore", "--list", dump.as_posix()),
                env={"PGPASSFILE": _PGPASS.as_posix()},
            )
        except RuntimeFailure as error:
            raise RuntimeFailure(
                "deploy-preflight",
                "immutable resume backup authority is invalid",
                outcome="refused",
                recovery_actions=("inspect the interrupted lifecycle before retrying",),
            ) from error
        except (OSError, LifecycleError) as error:
            raise RuntimeFailure(
                "deploy-preflight",
                "immutable resume backup authority is invalid",
                outcome="refused",
                recovery_actions=("inspect the interrupted lifecycle before retrying",),
            ) from error

    def _matching_completed_genesis(self, records: LifecycleRecords) -> bool:
        if records.current_release_id != self.inputs.candidate_release_id or len(records.activations) != 1:
            return False
        activation = records.activations[-1]
        release = next((item for item in records.releases if item.release_id == self.inputs.candidate_release_id), None)
        if release is None:
            return False
        try:
            migrations = self._current_migrations(records)
        except (LifecycleError, RuntimeFailure):
            return False
        return (
            activation.previous_release_id is None
            and activation.candidate_release_id == self.inputs.candidate_release_id
            and activation.migration_policy == self.inputs.migration_policy
            and release.artifact_sha256 == self.inputs.artifact_sha256
            and migrations == self.inputs.candidate_migrations
        )

    def cleanup_upload(self) -> None:
        path = self.inputs.artifact_path
        if not path.exists() and not path.is_symlink():
            return
        _safe_upload(path, self.store.owner_uid, self.store.deployment_root / "uploads")
        path.unlink()
        _fsync_directory(path.parent)

    def cleanup_staging(self) -> None:
        stage = self.stage_path
        if not stage.exists() and not stage.is_symlink():
            return
        try:
            details = stage.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != self.store.owner_uid:
                raise LifecycleError("operation staging residue is unsafe")
            shutil.rmtree(stage)
            _fsync_directory(stage.parent)
        except OSError as error:
            raise LifecycleError("unable to remove operation staging residue") from error

    def cleanup_selection(self) -> None:
        """Remove only this operation's uninstalled temporary current symlink."""

        temporary = self.selection_path
        if not temporary.exists() and not temporary.is_symlink():
            return
        try:
            details = temporary.lstat()
            if not stat.S_ISLNK(details.st_mode):
                raise LifecycleError("operation selection residue is unsafe")
            if temporary.readlink() != self.store.release_path(self.inputs.candidate_release_id):
                raise LifecycleError("operation selection residue is unsafe")
            temporary.unlink()
            _fsync_directory(temporary.parent)
        except OSError as error:
            raise LifecycleError("unable to remove operation selection residue") from error

    def _ensure_roots(self) -> None:
        for directory in (self.store.paths.local(self.store.paths.install_root), self.store.release_root, self.store.deployment_root, self.store.backup_root):
            if directory.exists() or directory.is_symlink():
                continue
            try:
                directory.mkdir(parents=True, mode=0o750)
                self._mark_changed("staging")
                os.chmod(directory, 0o750)
                os.chown(directory, self.store.owner_uid, -1)
            except OSError as error:
                raise RuntimeFailure(
                    "staging",
                    "unable to prepare managed deployment roots",
                    recovery_actions=_STAGING_RECOVERY,
                ) from error


def _result(request: HostRequest, result: RuntimeResult, state: _DeploymentState) -> HostResult:
    lifecycle = {
        **dict(result.lifecycle),
        "selected_release_id": state.selected_release_id,
        "backup_id": state.backup.backup_id if state.backup is not None else state.backup_id,
        "activation_id": state.staged.activation_id if state.staged is not None else state.activation_id,
        "service_state": state.service_state,
        "database_state": state.database_state,
        "activation_recorded": state.activation_recorded,
    }
    if result.outcome == "no_change" and state.noop:
        # A no-op reports the confirmed comparison predecessor, not the
        # candidate that is already selected on the host. This differs only
        # for a repeated genesis: its original predecessor remains absent.
        # No new backup or migration occurred, regardless of the candidate's
        # declared migration policy.
        lifecycle["previous_release_id"] = state.inputs.previous_release_id
        lifecycle["backup_id"] = None
        lifecycle["database_state"] = "unchanged"
    runtime_state = dict(result.runtime_state)
    if state.resuming and state.activation_id is not None:
        runtime_state["resumed_activation_id"] = state.activation_id
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome=result.outcome,
        stage="already-current" if result.outcome == "no_change" and state.noop else result.stage,
        changed_stages=result.changed_stages,
        lifecycle=lifecycle,
        runtime_state=runtime_state,
        verification=state.verification,
        residue_paths=tuple(dict.fromkeys((*result.residue_paths, *state.residue_paths))),
        recovery_actions=result.recovery_actions,
        warnings=result.warnings,
    )


def _lock_failure(request: HostRequest, error: LifecycleLockContention) -> HostResult:
    holder = None if error.holder is None else error.holder.to_mapping()
    return HostResult(
        protocol_version=PROTOCOL_VERSION, operation=request.operation, operation_id=request.operation_id,
        outcome="failed", stage="lifecycle-lock", changed_stages=(), lifecycle={},
        runtime_state={} if holder is None else {"lock_holder": holder}, verification={},
        residue_paths=(), recovery_actions=("wait for the recorded lifecycle operation to finish and retry",), warnings=(),
    )


def _validate_manifest(value: Mapping[str, object], candidate: str) -> tuple[Mapping[str, object], ...]:
    if set(value) != _MANIFEST_FIELDS:
        raise ValueError("invalid artifact manifest")
    if value.get("schema_version") != 2 or value.get("release_id") != candidate:
        raise ValueError("invalid artifact manifest")
    if type(value.get("application_version")) is not str or type(value.get("source_revision")) is not str:
        raise ValueError("invalid artifact manifest")
    if _SHA256_RE.fullmatch(str(value.get("source_revision"))) is None and re.fullmatch(r"[0-9a-f]{40}", str(value.get("source_revision"))) is None:
        raise ValueError("invalid artifact manifest")
    return _migration_fingerprints(value.get("migrations"))


def _migration_fingerprints(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("invalid migration fingerprints")
    fingerprints: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _MIGRATION_FIELDS:
            raise ValueError("invalid migration fingerprints")
        filename, digest = item["filename"], item["sha256"]
        if type(filename) is not str or _MIGRATION_FILENAME_RE.fullmatch(filename) is None or type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
            raise ValueError("invalid migration fingerprints")
        fingerprints.append({"filename": filename, "sha256": digest})
    if [item["filename"] for item in fingerprints] != sorted(item["filename"] for item in fingerprints) or len({item["filename"] for item in fingerprints}) != len(fingerprints):
        raise ValueError("invalid migration fingerprints")
    return tuple(fingerprints)


def _validate_migration_policy(
    current: tuple[Mapping[str, object], ...],
    candidate: tuple[Mapping[str, object], ...],
    policy: str,
) -> None:
    if current == candidate and policy == "no-change":
        return
    if current != candidate and policy in {"backward-compatible", "restore-required"}:
        return
    raise ValueError("migration policy does not match migration fingerprints")


def _safe_upload(path: Path, owner_uid: int, root: Path) -> None:
    try:
        path.relative_to(root)
        details = path.lstat()
    except (OSError, ValueError) as error:
        raise LifecycleError("deployment artifact is unavailable") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_uid != owner_uid or stat.S_IMODE(details.st_mode) != 0o600 or details.st_size <= 0 or details.st_size > _MAX_ARCHIVE_BYTES:
        raise LifecycleError("deployment artifact is unsafe")


def _require_digest(path: Path, expected: str) -> None:
    if _file_sha256(path) != expected:
        raise RuntimeFailure("staging", "deployment artifact checksum does not match", outcome="refused")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_release(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        _validate_archive_members(members)
        source.extractall(destination, members=members, filter="data")


def _archive_expanded_size(archive: Path) -> int:
    try:
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            return _validate_archive_members(members)
    except (OSError, tarfile.TarError) as error:
        raise RuntimeFailure("deploy-preflight", "release archive inventory is invalid", outcome="refused") from error


def _validate_archive_members(members: list[tarfile.TarInfo]) -> int:
    if not members or len(members) > _MAX_ARCHIVE_MEMBERS:
        raise LifecycleError("release archive inventory is invalid")
    expanded = 0
    for member in members:
        target = PurePosixPath(member.name)
        if target.is_absolute() or not target.parts or target.parts[0] != "taskman" or ".." in target.parts or member.isdev() or member.isfifo() or member.islnk():
            raise LifecycleError("release archive member is unsafe")
        if member.issym() and (PurePosixPath(member.linkname).is_absolute() or ".." in PurePosixPath(member.linkname).parts):
            raise LifecycleError("release archive link is unsafe")
        if member.isfile():
            expanded += member.size
            if expanded > _MAX_EXPANDED_ARCHIVE_BYTES:
                raise LifecycleError("release archive expanded size is unsafe")
    return expanded


def _validate_release_tree(path: Path) -> None:
    if not path.is_dir() or path.is_symlink() or not (path / "bin" / "server").is_file() or not (path / "bin" / "migrate").is_file() or not (path / "lib").is_dir() or not (path / "releases").is_dir():
        raise LifecycleError("release archive has incomplete runtime tree")
    for item in path.rglob("*"):
        details = item.lstat()
        if not (stat.S_ISDIR(details.st_mode) or stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode)) or details.st_mode & 0o7022:
            raise LifecycleError("release archive tree is unsafe")


def _normalize_release_tree(path: Path, owner_uid: int) -> None:
    for item in (path, *path.rglob("*")):
        if item.is_symlink():
            continue
        os.chown(item, owner_uid, -1)
        os.chmod(item, 0o750 if item.is_dir() or os.access(item, os.X_OK) else 0o640)


def _write_marker(path: Path, release_id: str, checksum: str, owner_uid: int) -> None:
    marker = path / ".taskman-release.json"
    encoded = (f'{{"schema_version":1,"release_id":"{release_id}","artifact_sha256":"{checksum}"}}\n').encode()
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(marker, owner_uid, -1)
    os.chmod(marker, 0o640)


def _safe_secret_file(path: Path, owner_uid: int) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise RuntimeFailure("backup", "database credential input is unavailable") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_uid != owner_uid or stat.S_IMODE(details.st_mode) != 0o600:
        raise RuntimeFailure("backup", "database credential input is unsafe")


def _database_size(settings: Mapping[str, object]) -> int:
    output = _run_command("backup", ("psql", "--no-psqlrc", "--tuples-only", "--no-align", "--host", str(settings["database_host"]), "--port", str(settings["database_port"]), "--username", str(settings["database_role"]), "--dbname", str(settings["database_name"]), "--command", "SELECT pg_database_size(current_database())"), env={"PGPASSFILE": _PGPASS.as_posix()}, capture=True)
    value = output.strip()
    if not value.isdecimal():
        return 0
    return int(value)


def _run_command(stage: str, argv: tuple[str, ...], *, env: Mapping[str, str] | None = None, capture: bool = False) -> str:
    try:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if capture else subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, **(dict(env) if env else {})}, timeout=_MAX_COMMAND_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeFailure(stage, "host command failed") from error
    if completed.returncode != 0:
        raise RuntimeFailure(stage, "host command failed")
    output = completed.stdout or b""
    if len(output) > 4096:
        raise RuntimeFailure(stage, "host command output is oversized")
    return output.decode("utf-8", "replace")


def _observed_service_state() -> str:
    """Return only a bounded, non-sensitive post-command service fact."""

    try:
        completed = subprocess.run(
            ("systemctl", "show", "taskman.service", "--property=ActiveState", "--value"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_MAX_COMMAND_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    value = (completed.stdout or b"").decode("utf-8", "replace").strip()
    return value if completed.returncode == 0 and value in {"active", "inactive", "failed"} else "unknown"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _settings(value: object) -> Mapping[str, object]:
    """Validate the non-secret deploy settings before any lifecycle write."""

    verification = {
        "application_port", "distribution_port", "database_port", "public_hostname", "public_ipv4",
        "public_ipv6", "ssh_port", "ssh_user", "readiness_timeout", "connection_timeout",
    }
    database = {"database_host", "database_role", "database_name"}
    if not isinstance(value, Mapping) or set(value) != verification | database:
        raise ValueError("invalid deployment settings")
    for name in ("application_port", "distribution_port", "database_port", "ssh_port"):
        if type(value[name]) is not int or not 1 <= int(value[name]) <= 65_535:
            raise ValueError("invalid deployment settings")
    for name in ("database_host", "database_role", "database_name", "public_hostname", "ssh_user"):
        if type(value[name]) is not str or not value[name]:
            raise ValueError("invalid deployment settings")
    return dict(value)


def _mutable_mapping(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _mutable_mapping(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_mapping(item) for item in value]
    return value


__all__ = ["deploy", "genesis"]
