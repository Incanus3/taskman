"""Ordered, fail-closed immutable Taskman release deployment workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..manifests import ArtifactManifest, MigrationFingerprint, VerifiedArtifact
from ..output import WorkflowResult, redact, render_human
from ..remote import Remote
from ..releases.activation import ActivationResult, MigrationPolicy, resolve_migration_policy
from ..releases.records import LifecycleRecords, ManualAdoptionCandidate, RemoteLifecycleStore
from .deploy_transaction import run_locked_deployment
from .operational_preflight import validate_operational_preflight
from ..verification import VerificationReport, verify_installation


def deploy(
    remote: Remote,
    config: EnvironmentConfig,
    artifact: VerifiedArtifact,
    *,
    migration_policy: str | None = None,
    lifecycle_store: RemoteLifecycleStore | None = None,
    lock_timeout_seconds: float = 5,
    manual_adoption_confirmed: bool = False,
    present_plan: Callable[[Mapping[str, object]], None] | None = None,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Stage, back up, migrate, activate, and verify one exact artifact.

    Expected operational failures return the standard redacted workflow result
    rather than exposing transport output. No failure path starts old code or
    claims that database migration effects were reversed.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("deployment requires a validated environment configuration")
    if not isinstance(artifact, VerifiedArtifact):
        raise TypeError("deployment requires a verified artifact")
    if not isinstance(dry_run, bool):
        raise TypeError("deployment dry-run flag must be boolean")
    store = lifecycle_store or RemoteLifecycleStore(
        remote,
        config.deployment_root,
        config.managed_root,
        config.release_root,
        config.backup_root,
        application_port=config.application_port,
        distribution_port=config.distribution_port,
        database_port=config.database_port,
    )
    if store.remote is not remote:
        raise ValueError("deployment lifecycle store does not match remote")

    candidate = artifact.manifest.release_id
    current: str | None = None
    backup_id: str | None = None
    activation_recorded: bool | str = False
    try:
        validate_operational_preflight(remote, config)
        manual_authority: ManualAdoptionCandidate | None = None
        if manual_adoption_confirmed:
            # Resolve the complete manual baseline under the canonical lock
            # without writing records. The mutating transaction re-derives
            # and compares this same authority before it may publish adoption.
            manual_authority = store.inspect_manual_current_release(
                lock_timeout_seconds=lock_timeout_seconds
            )
            current = manual_authority.release_id
            policy = resolve_migration_policy(
                manual_authority.migrations,
                artifact.manifest.migrations,
                migration_policy,
            )
            plan = _deployment_plan(
                config,
                artifact,
                current=current,
                migration_policy=policy,
                new_migrations=manual_authority.migrations != artifact.manifest.migrations,
            )
        else:
            current, current_migrations, healthy, current_sha256 = inspect_deploy_state(
                remote,
                config,
                store,
                lock_timeout_seconds=lock_timeout_seconds,
            )
            if dry_run and current == candidate and current_sha256 == artifact.sha256 and healthy:
                return WorkflowResult(
                    command="deploy",
                    environment=config.name or "",
                    changed=False,
                    stage="already-current",
                    facts={"previous_release_id": current, "candidate_release_id": candidate, "selected_release_id": current, "backup_id": None, "database_changed": False, "activation_recorded": True, "service_state": "active"},
                    next_action="no deployment action is required",
                )
            policy = resolve_migration_policy(current_migrations, artifact.manifest.migrations, migration_policy)
            plan = _deployment_plan(
                config,
                artifact,
                current=current,
                migration_policy=policy,
                new_migrations=current_migrations != artifact.manifest.migrations,
            )

        if dry_run:
            return WorkflowResult(
                command="deploy",
                environment=config.name or "",
                changed=False,
                stage="planned",
                facts={**plan, "previous_release_id": current, "selected_release_id": current},
                next_action="review the redacted deployment plan and run without --dry-run only after confirmation",
            )

        safe_plan = _redacted_plan(plan)
        (present_plan or _present_plan)(safe_plan)
        if not (confirm or _confirm)(safe_plan):
            return WorkflowResult(
                command="deploy",
                environment=config.name or "",
                changed=False,
                stage="confirmation-cancelled",
                facts={
                    **safe_plan,
                    "previous_release_id": current,
                    "selected_release_id": current,
                    "database_state": "unchanged",
                    "service_state": "active" if current is not None else "unknown",
                },
                next_action="review the exact deployment plan and confirm a later run when ready",
            )

        payload = run_locked_deployment(
            remote,
            config,
            artifact,
            store,
            migration_policy=policy,
            lock_timeout_seconds=lock_timeout_seconds,
            manual_adoption_confirmed=manual_adoption_confirmed,
            expected_previous_release_id=current,
            expected_manual_adoption=manual_authority,
        )
        current = payload.get("previous_release_id") if isinstance(payload.get("previous_release_id"), str) else None
        backup_id = payload.get("backup_id") if isinstance(payload.get("backup_id"), str) else None
        if payload["stage"] == "already-current":
            return WorkflowResult(
                command="deploy", environment=config.name or "", changed=False, stage="already-current",
                facts={
                    "previous_release_id": current,
                    "candidate_release_id": candidate,
                    "selected_release_id": payload["selected_release_id"],
                    "backup_id": None,
                    "database_changed": False,
                    "database_state": payload["database_state"],
                    "activation_recorded": payload["activation_recorded"],
                    "service_state": payload["service_state"],
                    "changed_stages": tuple(payload["changed_stages"]),
                    "recovery_commands": tuple(payload["recovery_commands"]),
                    "residue_paths": tuple(payload["residue_paths"]),
                    "verification": payload["verification"],
                },
                warnings=tuple(payload["warnings"]),
                next_action="no deployment action is required",
            )
        activation_recorded = payload["activation_recorded"]
        database_state = payload["database_state"]
        return WorkflowResult(
            command="deploy",
            environment=config.name or "",
            changed=payload["changed"],
            stage="deployed",
            facts={
                "previous_release_id": current,
                "candidate_release_id": candidate,
                "selected_release_id": payload["selected_release_id"],
                "backup_id": backup_id,
                "migration_policy": policy,
                "database_changed": database_state == "changed",
                "database_state": database_state,
                "activation_recorded": activation_recorded,
                "service_state": payload["service_state"],
                "changed_stages": tuple(payload["changed_stages"]),
                "recovery_commands": tuple(payload["recovery_commands"]),
                "residue_paths": tuple(payload["residue_paths"]),
                "verification": payload["verification"],
            },
            warnings=tuple(payload["warnings"]),
            next_action="perform the remaining browser, email, and API acceptance checks",
        )
    except OpsError as error:
        observed_current = getattr(error, "previous_release_id", current)
        observed_backup = getattr(error, "backup_id", backup_id)
        return _failure_result(
            config,
            error,
            current=observed_current if isinstance(observed_current, str) else current,
            candidate=candidate,
            backup_id=observed_backup if isinstance(observed_backup, str) else backup_id,
            activation_recorded=activation_recorded,
        )


def deploy_first_release(
    remote: Remote,
    config: EnvironmentConfig,
    artifact: VerifiedArtifact,
    *,
    lifecycle_store: RemoteLifecycleStore | None = None,
    lock_timeout_seconds: float = 5,
) -> WorkflowResult:
    """Install the genesis release through the canonical locked transaction.

    Provisioning has already presented and confirmed its complete plan before
    host convergence. This integration therefore has no second confirmation
    boundary, and genesis authority is re-derived only inside the exclusive
    lifecycle lock. A completed exact genesis release is a verified no-op;
    every other pre-existing lifecycle is refused by the transaction.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("first release requires a validated environment configuration")
    if not isinstance(artifact, VerifiedArtifact):
        raise TypeError("first release requires a verified artifact")
    store = lifecycle_store or RemoteLifecycleStore(
        remote,
        config.deployment_root,
        config.managed_root,
        config.release_root,
        config.backup_root,
        application_port=config.application_port,
        distribution_port=config.distribution_port,
        database_port=config.database_port,
    )
    if store.remote is not remote:
        raise ValueError("first-release lifecycle store does not match remote")

    candidate = artifact.manifest.release_id
    policy: MigrationPolicy = (
        "no-change" if not artifact.manifest.migrations else "restore-required"
    )
    try:
        payload = run_locked_deployment(
            remote,
            config,
            artifact,
            store,
            migration_policy=policy,
            lock_timeout_seconds=lock_timeout_seconds,
            genesis=True,
        )
    except OpsError as error:
        # This entry point is authorized only after clean-host convergence,
        # where taskman.service is enabled without being started. A completed
        # preparation refusal therefore retains that known stopped state.
        # Transport uncertainty or any later failure without structured
        # transaction evidence must remain unknown.
        service_state_fallback = (
            "stopped"
            if error.stage == "staging" and error.changed is False
            else "unknown"
        )
        return _failure_result(
            config,
            error,
            current=None,
            candidate=candidate,
            backup_id=None,
            activation_recorded=False,
            service_state_fallback=service_state_fallback,
        )

    if payload["stage"] == "already-current":
        return WorkflowResult(
            command="deploy",
            environment=config.name or "",
            changed=False,
            stage="already-current",
            facts={
                "previous_release_id": candidate,
                "candidate_release_id": candidate,
                "selected_release_id": candidate,
                "backup_id": None,
                "migration_policy": policy,
                "database_changed": False,
                "database_state": "unchanged",
                "activation_recorded": True,
                "service_state": "active",
                "changed_stages": (),
                "recovery_commands": tuple(payload["recovery_commands"]),
                "residue_paths": tuple(payload["residue_paths"]),
                "verification": payload["verification"],
            },
            warnings=tuple(payload["warnings"]),
            next_action="no first-release action is required",
        )

    database_state = payload["database_state"]
    return WorkflowResult(
        command="deploy",
        environment=config.name or "",
        changed=True,
        stage="deployed",
        facts={
            "previous_release_id": None,
            "candidate_release_id": candidate,
            "selected_release_id": candidate,
            "backup_id": payload["backup_id"],
            "migration_policy": policy,
            "database_changed": database_state == "changed",
            "database_state": database_state,
            "activation_recorded": payload["activation_recorded"],
            "service_state": payload["service_state"],
            "changed_stages": tuple(payload["changed_stages"]),
            "recovery_commands": tuple(payload["recovery_commands"]),
            "residue_paths": tuple(payload["residue_paths"]),
            "verification": payload["verification"],
        },
        warnings=tuple(payload["warnings"]),
        next_action="complete provisioning verification and interactive acceptance checks",
    )


def inspect_deploy_state(
    remote: Remote,
    config: EnvironmentConfig,
    store: RemoteLifecycleStore,
    *,
    lock_timeout_seconds: float,
) -> tuple[str, tuple[MigrationFingerprint, ...], bool, str]:
    """Read one authoritative lifecycle snapshot and current health evidence."""

    records, snapshot = store.read(
        operation="deploy", lock_timeout_seconds=lock_timeout_seconds
    )
    if records.current_release_id is None:
        raise _safety(
            "a manual current release requires exact inspection and explicit "
            "adoption confirmation before deployment"
        )
    current = records.current_release_id
    if current is None:
        raise _safety("no current release is recorded")
    migrations, sha256 = _current_metadata(records, snapshot, current)
    report = verify_installation(remote, config, current, lifecycle_store=store)
    return current, migrations, report.successful, sha256


def finalize_activation(activation: ActivationResult, report: VerificationReport) -> None:
    """Make successful readiness evidence an explicit activation completion gate."""

    if not isinstance(activation, ActivationResult) or not isinstance(report, VerificationReport):
        raise TypeError("activation finalization requires typed activation and verification evidence")
    if not report.successful or report.release_id != activation.candidate_release_id:
        raise _safety("candidate verification cannot finalize the activation record")


def _current_metadata(
    records: LifecycleRecords,
    snapshot: Mapping[str, object],
    current: str,
) -> tuple[tuple[MigrationFingerprint, ...], str]:
    adopted = {record.release_id: record for record in records.adoptions}.get(current)
    if adopted is not None:
        # An adopted manual baseline has no exact archive checksum. It can be
        # compared for migrations but never treated as a matching artifact.
        return adopted.migrations, "unknown"
    manifests = snapshot.get("manifests")
    try:
        manifest = ArtifactManifest.from_mapping(manifests.get(current) if isinstance(manifests, Mapping) else None)
    except ValueError:
        raise _safety("current release manifest is invalid") from None
    release = next((record for record in records.releases if record.release_id == current), None)
    if release is None or release.artifact_sha256 is None:
        raise _safety("current release has no authoritative artifact checksum")
    return manifest.migrations, release.artifact_sha256


def _verification_failure(
    config: EnvironmentConfig,
    current: str,
    candidate: str,
    backup_id: str,
    report: VerificationReport,
) -> WorkflowResult:
    return WorkflowResult(
        command="deploy",
        environment=config.name or "",
        changed=True,
        stage="verification-failed",
        facts={
            "previous_release_id": current,
            "candidate_release_id": candidate,
            "selected_release_id": candidate,
            "backup_id": backup_id,
            "database_changed": True,
            "activation_recorded": True,
            "service_state": "unknown",
            "verification": report.to_mapping(),
        },
        next_action="keep Taskman stopped if unhealthy and inspect the selected release, database, and verification evidence before explicit recovery",
        exit_status=report.exit_status,
    )


def _failure_result(
    config: EnvironmentConfig,
    error: OpsError,
    *,
    current: str | None,
    candidate: str,
    backup_id: str | None,
    activation_recorded: bool,
    service_state_fallback: str | None = None,
) -> WorkflowResult:
    stages: dict[ExitStatus, str] = {
        ExitStatus.BACKUP: "backup-failed",
        ExitStatus.MIGRATION: "migration-failed",
        ExitStatus.RELEASE: "activation-failed",
        ExitStatus.READINESS: "verification-failed",
        ExitStatus.SAFETY: "safety-refused",
        ExitStatus.LOCKED: "lock-contended",
    }
    stage = "staging-failed" if error.stage == "staging" else stages.get(error.status, f"{error.stage}-failed")
    selected = getattr(error, "selected_release_id", current)
    database_state = getattr(error, "database_state", None)
    if database_state not in {"unchanged", "changed", "unknown"}:
        database_state = "unknown"
    changed_database: bool | str = (
        False if database_state == "unchanged" else True if database_state == "changed" else "unknown"
    )
    recorded = getattr(error, "activation_recorded", activation_recorded)
    if recorded not in {True, False, "unknown"}:
        recorded = "unknown"
    if service_state_fallback is None:
        service_state_fallback = (
            "running"
            if error.status
            in {ExitStatus.BACKUP, ExitStatus.SAFETY, ExitStatus.LOCKED}
            else "unknown"
        )
    return WorkflowResult(
        command="deploy",
        environment=config.name or "",
        changed=error.changed,
        stage=stage,
        facts={
            "previous_release_id": current,
            "candidate_release_id": candidate,
            "selected_release_id": selected,
            "backup_id": getattr(error, "backup_id", backup_id),
            "database_changed": changed_database,
            "database_state": database_state,
            "activation_recorded": recorded,
            "service_state": getattr(error, "service_state", service_state_fallback),
            "failure_stage": error.stage,
            "changed_stages": tuple(getattr(error, "changed_stages", ())),
            "recovery_commands": tuple(getattr(error, "recovery_commands", ())),
            "residue_paths": tuple(getattr(error, "residue_paths", ())),
            "verification": getattr(error, "verification", None),
        },
        warnings=tuple(getattr(error, "warnings", ())),
        next_action=error.next_action or "inspect the selected release and database state before retrying",
        exit_status=error.status,
        )


def _deployment_plan(
    config: EnvironmentConfig,
    artifact: VerifiedArtifact,
    *,
    current: str | None,
    migration_policy: str,
    new_migrations: bool | str,
) -> dict[str, object]:
    return {
        "environment": config.name or "",
        "ssh_destination": f"{config.ssh_user}@{config.ssh_host}:{config.ssh_port}",
        "public_hostname": config.public_hostname,
        "current_release_id": current,
        "candidate_release_id": artifact.manifest.release_id,
        "source_revision": artifact.manifest.source_revision,
        "artifact_sha256": artifact.sha256,
        "new_migrations": new_migrations,
        "migration_policy": migration_policy,
        "planned_backup": True,
        "services_affected": ("taskman.service",),
        "expected_maintenance_window": "brief Taskman service interruption after backup",
    }


def _redacted_plan(plan: Mapping[str, object]) -> dict[str, object]:
    safe = redact(dict(plan))
    if not isinstance(safe, Mapping):  # pragma: no cover - defensive redaction boundary
        raise RuntimeError("redacted deployment plan is invalid")
    return dict(safe)


def _present_plan(plan: Mapping[str, object]) -> None:
    print(
        render_human(
            WorkflowResult(
                command="deploy",
                environment=str(plan.get("environment", "")),
                changed=False,
                stage="planned",
                facts={"plan": dict(plan)},
                next_action="confirm before starting the deployment transaction",
            )
        )
    )


def _confirm(_plan: Mapping[str, object]) -> bool:
    return input("Apply this redacted deployment plan? Type yes to continue: ").strip().lower() == "yes"


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "deploy",
        message,
        changed=False,
        next_action="inspect the managed lifecycle records and resolve the safety refusal before retrying",
    )


__all__ = [
    "deploy",
    "deploy_first_release",
    "finalize_activation",
    "inspect_deploy_state",
]
