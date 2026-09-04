"""Controller acceptance through real workflows and stateful remote boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Callable

import pytest

from taskman_ops.cli import Invocation, main
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.manifests import (
    ArtifactManifest,
    MigrationFingerprint,
    VerifiedArtifact,
    verify_artifact,
)
from taskman_ops.output import clear_secrets, register_secret
from taskman_ops.releases.cleanup import CleanupTarget
from taskman_ops.releases.records import (
    ActivationRecord,
    BackupRecord,
    LifecycleRecords,
    ReleaseRecord,
    RemoteLifecycleStore,
)
from taskman_ops.remote import CommandResult
from taskman_ops.services.backups import BackupContext
from taskman_ops.verification import (
    CheckStatus,
    VerificationCheck,
    VerificationReport,
)
from taskman_ops.workflows.backup import run_backup
from taskman_ops.workflows.cleanup import cleanup
from taskman_ops.workflows.deploy import deploy
from taskman_ops.workflows.releases import list_releases
from taskman_ops.workflows.restore import restore
from taskman_ops.workflows.rollback import rollback


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CANDIDATE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PRE_RESTORE_BACKUP = "backup-cccccccccccccccccccccccccccccccc"
RETAINED_BACKUP = "backup-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
CANARY = "controller-failure-canary-5f5fb156"
RECOVERY_ID = "recovery-" + "d" * 32


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in ("backup", "cleanup", "deploy", "restore", "rollback", "create_admin"):
        monkeypatch.setattr(
            f"taskman_ops.workflows.{module}.validate_operational_preflight",
            lambda *_args: object(),
        )


@dataclass
class HostState:
    selected_release: str = CURRENT
    service_state: str = "active"
    database_state: str = "unchanged"
    backups: set[str] | None = None

    def __post_init__(self) -> None:
        if self.backups is None:
            self.backups = {RETAINED_BACKUP}


@dataclass(frozen=True)
class DeployFailure:
    name: str
    status: ExitStatus
    transaction_stage: str
    result_stage: str
    selected_release: str
    service_state: str
    database_state: str
    backup_id: str | None
    changed_stages: tuple[str, ...]
    verification_check: str | None = None


DEPLOY_FAILURES = (
    DeployFailure(
        "corrupt-remote-checksum",
        ExitStatus.RELEASE,
        "staging",
        "staging-failed",
        CURRENT,
        "active",
        "unchanged",
        None,
        (),
    ),
    DeployFailure(
        "insufficient-capacity",
        ExitStatus.BACKUP,
        "backup",
        "backup-failed",
        CURRENT,
        "active",
        "unchanged",
        None,
        ("staging",),
    ),
    DeployFailure(
        "migration-failure",
        ExitStatus.MIGRATION,
        "migration",
        "migration-failed",
        CURRENT,
        "stopped",
        "unknown",
        BACKUP,
        ("staging", "backup", "stop", "migration"),
    ),
    DeployFailure(
        "startup-failure",
        ExitStatus.RELEASE,
        "start",
        "activation-failed",
        CANDIDATE,
        "stopped",
        "changed",
        BACKUP,
        ("staging", "backup", "stop", "migration", "selection", "start"),
    ),
    DeployFailure(
        "readiness-timeout",
        ExitStatus.READINESS,
        "verification",
        "verification-failed",
        CANDIDATE,
        "active",
        "changed",
        BACKUP,
        (
            "staging",
            "backup",
            "stop",
            "migration",
            "selection",
            "start",
            "verification",
        ),
        "local-readiness",
    ),
    DeployFailure(
        "public-https-failure",
        ExitStatus.READINESS,
        "verification",
        "verification-failed",
        CANDIDATE,
        "active",
        "changed",
        BACKUP,
        (
            "staging",
            "backup",
            "stop",
            "migration",
            "selection",
            "start",
            "verification",
        ),
        "public-readiness",
    ),
)


@pytest.fixture(autouse=True)
def registered_canary() -> None:
    clear_secrets()
    register_secret(CANARY)
    yield
    clear_secrets()


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43,
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@acme.tld",
        }
    )


def _manifest(
    release_id: str,
    *,
    migrations: tuple[MigrationFingerprint, ...] = (),
) -> ArtifactManifest:
    return ArtifactManifest(
        1,
        "taskman",
        "0.2.0",
        ("a" if release_id == CURRENT else "b") * 40,
        release_id,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "ubuntu26.04",
        "amd64",
        "27.3.4.6",
        "1.18.3",
        "22.22.1",
        migrations,
        "taskman",
    )


def _artifact(tmp_path: Path) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"verified release")
    return VerifiedArtifact(
        archive,
        tmp_path / "taskman.manifest.json",
        tmp_path / "taskman.tar.gz.sha256",
        "c" * 64,
        _manifest(CANDIDATE),
    )


def _remote_store(remote: object) -> RemoteLifecycleStore:
    config = _config()
    return RemoteLifecycleStore(
        remote,  # type: ignore[arg-type]
        config.deployment_root,
        config.managed_root,
        config.release_root,
        config.backup_root,
        application_port=config.application_port,
        distribution_port=config.distribution_port,
        database_port=config.database_port,
    )


def _verification_failure(check_name: str) -> dict[str, object]:
    check_names = (
        "taskman-service",
        "release-identity",
        "caddy-service",
        "listener-topology",
        "startup-journal",
        "local-readiness",
        "public-readiness",
        "public-hsts",
    )
    report = VerificationReport(
        ExitStatus.READINESS,
        CANDIDATE,
        CANDIDATE,
        tuple(
            VerificationCheck(
                name,
                CheckStatus.FAILED if name == check_name else CheckStatus.PASSED,
                f"{name} failed" if name == check_name else f"{name} passed",
            )
            for name in check_names
        ),
        "inspect the fixed verification summaries and correct the reported host state before retrying",
    )
    return report.to_mapping()


def _deploy_failure_payload(failure: DeployFailure) -> dict[str, object]:
    return {
        "stage": failure.transaction_stage,
        "previous_release_id": CURRENT,
        "candidate_release_id": CANDIDATE,
        "selected_release_id": failure.selected_release,
        "backup_id": failure.backup_id,
        "activation_id": None,
        "service_state": failure.service_state,
        "database_state": failure.database_state,
        "activation_recorded": False,
        "changed": bool(failure.changed_stages),
        "changed_stages": list(failure.changed_stages),
        "warnings": [f"unexpected deployment-root entry: {CANARY}"],
        "recovery_commands": [
            "systemctl status taskman.service",
            "readlink -f /opt/taskman/current",
            "journalctl --no-pager --unit taskman.service --lines=100",
        ],
        "residue_paths": [],
        "verification": (
            _verification_failure(failure.verification_check)
            if failure.verification_check is not None
            else None
        ),
    }


class DeployRemote:
    """Stateful fake below the real deployment workflow and transaction parser."""

    def __init__(
        self,
        state: HostState,
        *,
        failure: DeployFailure | None = None,
        interrupted_upload: bool = False,
        lock_contention: bool = False,
    ) -> None:
        self.state = state
        self.failure = failure
        self.interrupted_upload = interrupted_upload
        self.lock_contention = lock_contention
        self.transaction_calls = 0

    def put(self, *_args: object, **_kwargs: object) -> None:
        if self.interrupted_upload:
            error = OpsError(
                ExitStatus.RELEASE,
                "staging",
                f"upload interrupted: {CANARY}",
                False,
                "inspect the exact private upload receipt before retrying",
            )
            error.previous_release_id = CURRENT
            error.selected_release_id = CURRENT
            error.backup_id = None
            error.service_state = "active"
            error.database_state = "unchanged"
            error.activation_recorded = False
            error.warnings = (f"unexpected deployment-root entry: {CANARY}",)
            raise error

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        if argv[0] == "rm":
            return CommandResult(0)
        operation = argv[3]
        if operation == "taskman-deploy-upload-prepare":
            return CommandResult(0)
        assert operation == "taskman-deploy-transaction"
        self.transaction_calls += 1
        if self.lock_contention:
            return CommandResult(
                ExitStatus.LOCKED,
                json.dumps(
                    {
                        "schema_version": 1,
                        "holder": {
                            "operation": "backup",
                            "pid": 321,
                            "started_at": "2026-09-05T12:00:00Z",
                            "mode": "exclusive",
                        },
                    }
                ),
            )
        assert self.failure is not None
        payload = _deploy_failure_payload(self.failure)
        self.state.selected_release = self.failure.selected_release
        self.state.service_state = self.failure.service_state
        self.state.database_state = self.failure.database_state
        if self.failure.backup_id is not None:
            self.state.backups.add(self.failure.backup_id)  # type: ignore[union-attr]
        return CommandResult(self.failure.status, json.dumps(payload))


def _through_controller(
    command: str,
    runner: Callable[[Invocation], object],
) -> tuple[int, dict[str, object], str, str]:
    stdout = StringIO()
    stderr = StringIO()
    status = main(
        [*_argv(command), "--json"],
        dispatch_fn=runner,
        stdout=stdout,
        stderr=stderr,
    )
    out = stdout.getvalue()
    err = stderr.getvalue()
    rendered = out or err
    assert bool(out) is not bool(err)
    return status, json.loads(rendered), out, err


def _argv(command: str) -> list[str]:
    if command == "rollback":
        return [command, "production", CURRENT]
    if command == "restore":
        return [command, "production", BACKUP]
    return [command, "production"]


@pytest.mark.parametrize("failure", DEPLOY_FAILURES, ids=lambda item: item.name)
def test_deploy_failures_run_the_real_workflow_and_preserve_observed_host_state(
    failure: DeployFailure,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = HostState()
    remote = DeployRemote(state, failure=failure)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.inspect_deploy_state",
        lambda *_args, **_kwargs: (CURRENT, (), True, "a" * 64),
    )

    status, payload, out, err = _through_controller(
        "deploy",
        lambda _invocation: deploy(
            remote,  # type: ignore[arg-type]
            _config(),
            _artifact(tmp_path),
            migration_policy="no-change",
            lifecycle_store=_remote_store(remote),
            present_plan=lambda _plan: None,
            confirm=lambda _plan: True,
        ),
    )

    assert status == int(failure.status)
    assert payload["status"] == "failed"
    assert payload["stage"] == failure.result_stage
    assert payload["facts"]["failure_stage"] == failure.transaction_stage
    assert payload["facts"]["selected_release_id"] == failure.selected_release
    assert payload["facts"]["service_state"] == failure.service_state
    assert payload["facts"]["database_state"] == failure.database_state
    assert payload["facts"]["backup_id"] == failure.backup_id
    assert payload["next_action"] == (
        "inspect the exact selected release, backup, provisional lifecycle evidence, "
        "and database state before explicit recovery"
    )
    assert state.selected_release == failure.selected_release
    assert state.service_state == failure.service_state
    assert state.database_state == failure.database_state
    assert RETAINED_BACKUP in state.backups  # type: ignore[operator]
    if failure.backup_id is not None:
        assert failure.backup_id in state.backups  # type: ignore[operator]
    assert CANARY not in out
    assert CANARY not in err
    assert remote.transaction_calls == 1


def test_corrupt_local_checksum_refuses_before_remote_or_host_state_changes(
    tmp_path: Path,
) -> None:
    state = HostState()
    archive = tmp_path / "taskman.tar.gz"
    manifest = tmp_path / "taskman.manifest.json"
    checksum = tmp_path / "taskman.tar.gz.sha256"
    archive.write_bytes(b"corrupt local archive")
    manifest.write_text("{}\n", encoding="utf-8")
    checksum.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")

    status, payload, out, err = _through_controller(
        "deploy",
        lambda _invocation: verify_artifact(archive, manifest, checksum),
    )

    assert status == int(ExitStatus.INVALID)
    assert payload["stage"] == "artifact"
    assert payload["next_action"] == (
        "inspect the release artifact and retry with a verified archive"
    )
    assert state == HostState()
    assert CANARY not in out + err


def test_interrupted_upload_retains_selection_database_and_existing_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = HostState()
    remote = DeployRemote(state, interrupted_upload=True)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.inspect_deploy_state",
        lambda *_args, **_kwargs: (CURRENT, (), True, "a" * 64),
    )

    status, payload, out, err = _through_controller(
        "deploy",
        lambda _invocation: deploy(
            remote,  # type: ignore[arg-type]
            _config(),
            _artifact(tmp_path),
            migration_policy="no-change",
            lifecycle_store=_remote_store(remote),
            present_plan=lambda _plan: None,
            confirm=lambda _plan: True,
        ),
    )

    assert status == int(ExitStatus.RELEASE)
    assert payload["stage"] == "staging-failed"
    assert payload["facts"]["selected_release_id"] == CURRENT
    assert payload["facts"]["service_state"] == "active"
    assert payload["facts"]["database_state"] == "unchanged"
    assert payload["next_action"] == (
        "inspect the exact private upload receipt before retrying"
    )
    assert state == HostState()
    assert remote.transaction_calls == 0
    assert CANARY not in out + err


def test_missing_compatibility_declaration_refuses_before_upload_or_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = HostState()
    remote = DeployRemote(state)
    current_migration = MigrationFingerprint(
        "20260905000000_existing.exs", "d" * 64
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.inspect_deploy_state",
        lambda *_args, **_kwargs: (CURRENT, (current_migration,), True, "a" * 64),
    )

    status, payload, out, err = _through_controller(
        "deploy",
        lambda _invocation: deploy(
            remote,  # type: ignore[arg-type]
            _config(),
            _artifact(tmp_path),
            lifecycle_store=_remote_store(remote),
            present_plan=lambda _plan: None,
            confirm=lambda _plan: True,
        ),
    )

    assert status == int(ExitStatus.SAFETY)
    assert payload["stage"] == "safety-refused"
    assert payload["facts"]["selected_release_id"] == CURRENT
    assert payload["facts"]["backup_id"] is None
    assert state == HostState()
    assert remote.transaction_calls == 0
    assert payload["next_action"] == (
        "inspect the managed lifecycle records and immutable release state before retrying"
    )
    assert CANARY not in out + err


def test_lock_contention_runs_the_real_transaction_parser_without_state_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = HostState()
    remote = DeployRemote(state, lock_contention=True)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.inspect_deploy_state",
        lambda *_args, **_kwargs: (CURRENT, (), True, "a" * 64),
    )

    status, payload, out, err = _through_controller(
        "deploy",
        lambda _invocation: deploy(
            remote,  # type: ignore[arg-type]
            _config(),
            _artifact(tmp_path),
            migration_policy="no-change",
            lifecycle_store=_remote_store(remote),
            present_plan=lambda _plan: None,
            confirm=lambda _plan: True,
        ),
    )

    assert status == int(ExitStatus.LOCKED)
    assert payload["stage"] == "lock-contended"
    assert payload["facts"]["selected_release_id"] == "unknown"
    assert payload["facts"]["service_state"] == "unknown"
    assert payload["facts"]["database_state"] == "unchanged"
    assert state == HostState()
    assert payload["next_action"] == (
        "inspect the exact selected release, backup, provisional lifecycle evidence, "
        "and database state before explicit recovery"
    )
    assert CANARY not in out + err


def test_invalid_dump_runs_the_backup_workflow_and_keeps_every_retained_backup() -> None:
    state = HostState()

    class BackupRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            # The installed transaction owns pg_restore validation and reports
            # its dedicated backup status through this real controller parser.
            return CommandResult(ExitStatus.BACKUP, f"invalid dump: {CANARY}")

    remote = BackupRemote()
    status, payload, out, err = _through_controller(
        "backup",
        lambda _invocation: run_backup(
            remote,  # type: ignore[arg-type]
            BackupContext(_config(), _remote_store(remote)),
        ),
    )

    assert status == int(ExitStatus.BACKUP)
    assert payload["stage"] == "backup"
    assert payload["next_action"] == (
        "inspect PostgreSQL and available backup capacity before retrying"
    )
    assert state == HostState()
    assert CANARY not in out + err


def test_contradictory_discovery_runs_the_release_listing_boundary_and_refuses() -> None:
    state = HostState()

    class DiscoveryRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            # A current symlink without an activation edge is contradictory.
            # Include the canary in the untrusted target so the snapshot parser
            # and controller redaction boundary both have to contain it.
            return CommandResult(
                0,
                json.dumps(
                    {
                        "schema_version": 1,
                        "records": {
                            "releases": [],
                            "activations": [],
                            "backups": [],
                            "adoptions": [],
                        },
                        "current_target": f"/opt/taskman/releases/{CANARY}",
                        "manifests": {},
                        "dump_states": {},
                        "warnings": [],
                    }
                ),
            )

    remote = DiscoveryRemote()
    status, payload, out, err = _through_controller(
        "releases",
        lambda _invocation: list_releases(_remote_store(remote)),
    )

    assert status == int(ExitStatus.SAFETY)
    assert payload["stage"] == "lifecycle-records"
    assert payload["next_action"] == (
        "inspect the managed lifecycle state and resolve the contradiction"
    )
    assert state == HostState()
    assert CANARY not in out + err


def _release_record(
    release_id: str,
    *,
    previous: str | None,
    policy: str,
    activated_at: datetime,
) -> ReleaseRecord:
    return ReleaseRecord(
        schema_version=1,
        release_id=release_id,
        artifact_sha256="c" * 64,
        installed_at=activated_at,
        activated_at=activated_at,
        previous_release_id=previous,
        backup_id=None,
        migration_policy=policy,  # type: ignore[arg-type]
    )


def _activation_record(
    release_id: str,
    *,
    previous: str | None,
    policy: str,
    activated_at: datetime,
    suffix: str,
) -> ActivationRecord:
    return ActivationRecord(
        schema_version=1,
        activation_id="activation-" + suffix * 32,
        previous_release_id=previous,
        candidate_release_id=release_id,
        activated_at=activated_at,
        backup_id=None,
        migration_policy=policy,  # type: ignore[arg-type]
    )


def test_incompatible_rollback_uses_real_history_assessment_and_preserves_state() -> None:
    first = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    second = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
    records = LifecycleRecords(
        releases=(
            _release_record(CURRENT, previous=None, policy="no-change", activated_at=first),
            _release_record(
                CANDIDATE,
                previous=CURRENT,
                policy="restore-required",
                activated_at=second,
            ),
        ),
        activations=(
            _activation_record(
                CURRENT,
                previous=None,
                policy="no-change",
                activated_at=first,
                suffix="a",
            ),
            _activation_record(
                CANDIDATE,
                previous=CURRENT,
                policy="restore-required",
                activated_at=second,
                suffix="b",
            ),
        ),
        backups=(),
        adoptions=(),
        warnings=(),
    )
    state = HostState(selected_release=CANDIDATE)
    remote = object()

    class RollbackStore:
        def __init__(self) -> None:
            self.remote = remote

        def read(self, **_kwargs: object) -> tuple[LifecycleRecords, dict[str, object]]:
            return records, {}

    status, payload, out, err = _through_controller(
        "rollback",
        lambda _invocation: rollback(
            remote,  # type: ignore[arg-type]
            _config(),
            CURRENT,
            lifecycle_store=RollbackStore(),  # type: ignore[arg-type]
            confirm=lambda _plan: pytest.fail("unsafe rollback requested confirmation"),
        ),
    )

    assert status == int(ExitStatus.SAFETY)
    assert payload["stage"] == "safety-refused"
    assert payload["facts"]["selected_release_id"] == CANDIDATE
    assert payload["facts"]["database_state"] == "unknown"
    assert state.selected_release == CANDIDATE
    assert state.service_state == "active"
    assert state.database_state == "unchanged"
    assert RETAINED_BACKUP in state.backups  # type: ignore[operator]
    assert payload["next_action"] == (
        "inspect the managed activation history and use restore when rollback is unsafe"
    )
    assert CANARY not in out + err


def _restore_records() -> LifecycleRecords:
    first = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    second = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
    return LifecycleRecords(
        releases=(
            _release_record(CURRENT, previous=None, policy="no-change", activated_at=first),
            _release_record(
                CANDIDATE,
                previous=CURRENT,
                policy="no-change",
                activated_at=second,
            ),
        ),
        activations=(
            _activation_record(
                CURRENT,
                previous=None,
                policy="no-change",
                activated_at=first,
                suffix="a",
            ),
            _activation_record(
                CANDIDATE,
                previous=CURRENT,
                policy="no-change",
                activated_at=second,
                suffix="b",
            ),
        ),
        backups=(
            BackupRecord(
                schema_version=1,
                backup_id=BACKUP,
                created_at=first,
                size_bytes=2048,
                source_database_size_bytes=1_048_576,
                database="taskman_prod",
                current_release_id=CURRENT,
                candidate_release_id=CANDIDATE,
                reason="pre-deploy",
                validated=True,
                dump_path=PurePosixPath("/var/backups/taskman/backup-a.dump"),
            ),
        ),
        adoptions=(),
        warnings=(),
    )


def _restore_snapshot(records: LifecycleRecords) -> dict[str, object]:
    return {
        "schema_version": 1,
        "records": {
            "releases": [record.to_mapping() for record in records.releases],
            "activations": [record.to_mapping() for record in records.activations],
            "backups": [record.to_mapping() for record in records.backups],
            "adoptions": [],
        },
        "current_target": f"/opt/taskman/releases/{CANDIDATE}",
        "manifests": {
            CURRENT: _manifest(CURRENT).to_mapping(),
            CANDIDATE: _manifest(CANDIDATE).to_mapping(),
        },
        "dump_states": {"/var/backups/taskman/backup-a.dump": "present"},
        "warnings": [],
    }


def test_restore_swap_failure_runs_the_real_restore_parser_and_retains_both_backups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = HostState(selected_release=CANDIDATE)
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.uuid4",
        lambda: SimpleNamespace(hex="d" * 32),
    )
    records = _restore_records()
    snapshot = _restore_snapshot(records)
    recovery_commands = [
        "systemctl status taskman.service",
        "readlink -f /opt/taskman/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
        (
            "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql "
            "--port 5432 --username postgres --dbname=postgres --tuples-only "
            "--no-align --set ON_ERROR_STOP=1 --command "
            f"\"SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database "
            f"WHERE datname IN ('taskman_prod', 'taskman_restore_{'d' * 32}', "
            f"'taskman_recovery_{'d' * 32}')\""
        ),
    ]
    failure = {
        "stage": "swap",
        "backup_id": BACKUP,
        "pre_restore_backup_id": PRE_RESTORE_BACKUP,
        "current_release_id": CANDIDATE,
        "intended_release_id": CURRENT,
        "selected_release_id": CANDIDATE,
        "recovery_id": RECOVERY_ID,
        "service_state": "stopped",
        "database_state": "unknown",
        "swap_state": "canonical-moved",
        "restore_recorded": False,
        "changed": True,
        "changed_stages": ["backup", "stop", "restore", "validation", "swap"],
        "warnings": [],
        "recovery_commands": recovery_commands,
        "residue_paths": [],
        "verification": None,
    }

    class RestoreRemote:
        def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            if argv[3] == "taskman-validate-restore-dump":
                return CommandResult(0)
            state.service_state = "stopped"
            state.database_state = "unknown"
            state.backups.add(PRE_RESTORE_BACKUP)  # type: ignore[union-attr]
            return CommandResult(ExitStatus.RESTORE, json.dumps(failure))

    remote = RestoreRemote()

    class RestoreStore(RemoteLifecycleStore):
        def read(
            self, **_kwargs: object
        ) -> tuple[LifecycleRecords, dict[str, object]]:
            return records, snapshot

    base = _remote_store(remote)
    store = RestoreStore(
        remote,  # type: ignore[arg-type]
        base.deployment_root,
        base.managed_root,
        base.release_root,
        base.backup_root,
        application_port=base.application_port,
        distribution_port=base.distribution_port,
        database_port=base.database_port,
    )

    status, payload, out, err = _through_controller(
        "restore",
        lambda _invocation: restore(
            remote,  # type: ignore[arg-type]
            _config(),
            BACKUP,
            lifecycle_store=store,
            confirm=lambda _plan: True,
        ),
    )

    assert status == int(ExitStatus.RESTORE), payload
    assert payload["stage"] == "swap-failed"
    assert payload["facts"]["selected_release_id"] == CANDIDATE
    assert payload["facts"]["service_state"] == "stopped"
    assert payload["facts"]["database_state"] == "unknown"
    assert payload["facts"]["backup_id"] == BACKUP
    assert payload["facts"]["pre_restore_backup_id"] == PRE_RESTORE_BACKUP
    assert state.selected_release == CANDIDATE
    assert state.backups == {RETAINED_BACKUP, PRE_RESTORE_BACKUP}
    assert payload["next_action"] == (
        "use the exact recorded recovery database and the documented manual recovery commands"
    )
    assert CANARY not in out + err


def test_cleanup_race_runs_locked_revalidation_and_removes_nothing() -> None:
    state = HostState()
    target = CleanupTarget(
        "backup",
        BACKUP,
        PurePosixPath("/var/backups/taskman/backup-a.dump"),
        True,
    )

    class CleanupRemote:
        def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            assert argv[3] == "taskman-cleanup-transaction"
            return CommandResult(
                ExitStatus.SAFETY,
                json.dumps(
                    {
                        "stage": "revalidation",
                        "removed": [],
                        "recoverability": [],
                        "changed": False,
                        "warnings": [],
                    }
                ),
            )

    status, payload, out, err = _through_controller(
        "cleanup",
        lambda _invocation: cleanup(
            CleanupRemote(),  # type: ignore[arg-type]
            "production",
            targets=(target,),
            confirm=lambda _plan: True,
        ),
    )

    assert status == int(ExitStatus.SAFETY)
    assert payload["stage"] == "revalidation-failed"
    assert payload["facts"]["removed"] == []
    assert payload["facts"]["recoverability"] == []
    assert state == HostState()
    assert payload["next_action"] == (
        "inspect the exact removed targets and retained recovery artifacts before recomputing cleanup"
    )
    assert CANARY not in out + err
