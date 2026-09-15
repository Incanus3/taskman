"""Plan and translate final-protocol deployment convergence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import time
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..host_protocol import HostResult
from ..releases.artifacts import CleanInputs, DeploymentTarget, clean_inputs_match
from ..releases.manifests import MigrationFingerprint, VerifiedArtifact
from ..host_helper.records import ReleaseRecord, SelectionRecord
from ..host_helper.backup_protection import (
    BackupProtection,
    protection_prune_ids,
    protection_prune_ids_after_fresh_attempt,
)
from ..releases.source_order import compare_sources
from ..migrations import validate_migration_versions
from ..host_helper.database import release_migration_versions
from ..output import WorkflowResult, redact, render_human
from ..remote import Remote
from ..releases.identifiers import validate_release_id
from .helper import (
    discovery_request,
    mutable,
    mutation_result_facts,
    result_error,
    run_deployment_request,
    run_request,
    successful_verification,
    temporary_scheduled_backup_helper_package,
)
from .operational_preflight import validate_operational_preflight


_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required"})
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class DeploymentAdmissionAuthority:
    """Validated host facts used to resolve one public desired target."""

    installed_records: tuple[ReleaseRecord, ...]
    selected_release_id: str | None
    last_successful_release_id: str | None


def deployment_admission_authority(
    remote: Remote, config: EnvironmentConfig, *, mode: str = "deploy"
) -> DeploymentAdmissionAuthority:
    """Collect complete release authority before resolving an automatic target."""

    from .inventory import collect_inventory

    if mode not in {"deploy", "provision"}:
        raise ValueError("deployment admission mode is invalid")
    result = run_request(remote, discovery_request(config, mode=mode))
    if result.outcome != "succeeded" or not isinstance(result.state, Mapping):
        raise _safety("deployment planning helper refused host state")
    state = mutable(result.state)
    try:
        selected = state["selected_release_id"]
        selected = None if selected is None else validate_release_id(selected)
        last = state["last_successful_selection"]
        last_id = None if last is None else SelectionRecord.from_mapping(last).release_id
        records = tuple(
            ReleaseRecord.from_mapping(record)
            for record in collect_inventory(
                remote, config, "list_releases", deadline=time.monotonic() + 660.0
            )
        )
    except (KeyError, TypeError, ValueError):
        raise _safety("deployment planning helper returned incomplete release authority") from None
    return DeploymentAdmissionAuthority(records, selected, last_id)


def deploy(
    remote: Remote,
    config: EnvironmentConfig,
    target: DeploymentTarget | VerifiedArtifact,
    *,
    migration_policy: str | None = None,
    manual_adoption_confirmed: bool = False,
    present_plan: Callable[[Mapping[str, object]], None] | None = None,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    yes: bool = False,
    allow_downgrade: bool = False,
    repo: Path | None = None,
    clean_inputs: CleanInputs | None = None,
    refresh_clean_target: Callable[[], tuple[DeploymentTarget, CleanInputs]] | None = None,
    dry_run: bool = False,
    interactive: bool = True,
) -> WorkflowResult:
    """Present, confirm, and invoke one replayable host deployment."""

    if not isinstance(config, EnvironmentConfig) or not isinstance(target, (DeploymentTarget, VerifiedArtifact)):
        raise TypeError("deployment requires validated configuration and artifact")
    if not all(
        type(value) is bool
        for value in (dry_run, manual_adoption_confirmed, yes, allow_downgrade, interactive)
    ):
        raise TypeError("deployment flags must be boolean")
    if migration_policy is not None and migration_policy not in _POLICIES:
        raise ValueError("deployment requires a valid migration policy")
    deployment_target = (
        target if isinstance(target, DeploymentTarget)
        else DeploymentTarget(artifact=target, release_record=None, source="explicit")
    )
    candidate = deployment_target.release_id
    try:
        if manual_adoption_confirmed:
            raise _safety("manual lifecycle adoption is not part of replayable deployment")
        if not dry_run and not yes and not interactive:
            raise _safety("unattended deployment requires --yes; JSON is never confirmation")
        validate_operational_preflight(remote, config)
        while True:
            previous, _previous_migrations, applied_versions = _planning_authority(remote, config)
            expected_state = _confirmed_expected_state(remote, config)
            if (
                previous != expected_state["selected_release_id"]
                or applied_versions != tuple(expected_state["applied_migrations"])
            ):
                # Planning itself crossed an observation boundary.  Nothing
                # has been presented or authorized yet, so discard it and
                # collect a coherent admission cycle.
                continue
            pending_versions = _pending_migration_versions(
                applied_versions, deployment_target.manifest.migrations
            )
            if migration_policy is None and pending_versions:
                raise OpsError(
                    ExitStatus.INVALID,
                    "deploy",
                    "changed migrations require an explicit --migration-policy",
                    changed=False,
                    next_action=(
                        "review the changed migrations and rerun with --migration-policy "
                        "backward-compatible or --migration-policy restore-required"
                    ),
                )
            policy = migration_policy or "no-change"
            _validate_migration_policy(applied_versions, deployment_target.manifest.migrations, policy)
            downgrade_required, downgrade_evidence = _downgrade_acknowledgment(
                remote, config, deployment_target, repo
            )
            prune_authority = _planned_prune_backup_ids(
                remote,
                config,
                expected_state,
                fresh_backup_needed=bool(pending_versions),
            )
            prune_backup_ids, recovery_evidence = _prune_plan_authority(prune_authority)
            with temporary_scheduled_backup_helper_package() as scheduler_package:
                scheduler_refresh_required = (
                    expected_state["scheduled_backup_sha256"] != scheduler_package.sha256
                )
                plan = _redacted_plan(
                    _plan(
                        config,
                        deployment_target,
                        previous,
                        policy,
                        expected_state=expected_state,
                        pending_versions=pending_versions,
                        prune_backup_ids=prune_backup_ids,
                        scheduler_sha256=scheduler_package.sha256,
                        scheduler_refresh_required=scheduler_refresh_required,
                        material_evidence=_material_plan_evidence(
                            recovery_evidence["protections"],
                            recovery_evidence["independent_backup_ids"],
                            prune_backup_ids,
                            _downgrade_evidence_rows(downgrade_evidence),
                        ),
                    )
                )
                plan["source_dirty"] = deployment_target.source_dirty
                plan["requires_downgrade_acknowledgment"] = downgrade_required
                plan["downgrade_reasons"] = tuple(
                    sorted({reason for _release_id, _order, reasons in downgrade_evidence for reason in reasons})
                )
                if dry_run:
                    return WorkflowResult(
                        "deploy", config.name or "", False, "planned",
                        {**plan, "previous_release_id": previous, "selected_release_id": previous},
                        next_action="review the redacted deployment plan and rerun without --dry-run only after confirmation",
                    )
                if clean_inputs is not None and repo is not None and not clean_inputs_match(repo, clean_inputs):
                    if yes:
                        raise _safety("clean deployment inputs changed; rerun to acknowledge the refreshed plan")
                    if refresh_clean_target is None:
                        raise _safety("automatic clean source inputs changed before confirmation")
                    deployment_target, clean_inputs = refresh_clean_target()
                    candidate = deployment_target.release_id
                    continue
                if interactive:
                    (present_plan or _present_plan)(plan)
                if not yes and not (confirm or _confirm)(plan):
                    return WorkflowResult(
                        "deploy", config.name or "", False, "confirmation-cancelled",
                        {**plan, "previous_release_id": previous, "selected_release_id": previous,
                         "database_state": "unchanged", "service_state": "unknown"},
                        next_action="review the exact deployment plan and confirm a later run when ready",
                    )
                if downgrade_required and not allow_downgrade:
                    if yes or not interactive:
                        raise _safety("deployment ordering requires --allow-downgrade acknowledgement")
                    if not _confirm_downgrade(plan):
                        return WorkflowResult(
                            "deploy", config.name or "", False, "downgrade-acknowledgment-cancelled",
                            {**plan, "previous_release_id": previous, "selected_release_id": previous},
                            next_action="review the downgrade or unknown-order evidence before retrying",
                        )
                reobserved_state = _confirmed_expected_state(remote, config)
                rechecked_prune_ids, rechecked_recovery = _prune_plan_authority(
                    _planned_prune_backup_ids(
                        remote,
                        config,
                        reobserved_state,
                        fresh_backup_needed=bool(pending_versions),
                    )
                )
                rechecked_downgrade_required, rechecked_downgrade_evidence = _downgrade_acknowledgment(
                    remote, config, deployment_target, repo
                )
                if (
                    reobserved_state != expected_state
                    or rechecked_prune_ids != prune_backup_ids
                    or rechecked_recovery != recovery_evidence
                    or rechecked_downgrade_required != downgrade_required
                    or rechecked_downgrade_evidence != downgrade_evidence
                ):
                    if yes:
                        raise _safety("deployment authority changed after confirmation; rerun to acknowledge a new plan")
                    # The previous consent applies only to its displayed
                    # material facts.  Re-enter through identification,
                    # discovery, resolution, policy, and acknowledgment.
                    if clean_inputs is not None and refresh_clean_target is not None:
                        deployment_target, clean_inputs = refresh_clean_target()
                        candidate = deployment_target.release_id
                    continue
                scheduler_upload = None if not scheduler_refresh_required else "pending-controller-upload"
                result = run_deployment_request(
                    remote,
                    config,
                    deployment_target,
                    expected_state=expected_state,
                    migration_policy=policy,
                    backup_helper={"sha256": scheduler_package.sha256, "upload_path": scheduler_upload},
                    prune_backup_ids=prune_backup_ids,
                    backup_helper_package=scheduler_package,
                )
                break
        if result.outcome != "succeeded":
            raise result_error(result, starting_state=expected_state)
        payload = _payload_result(config, candidate, policy, result, previous_release_id=previous)
        return _with_artifact_source(payload, deployment_target.source)
    except OpsError as error:
        return _failure_result(config, error, candidate=candidate)


def deploy_first_release(
    remote: Remote,
    config: EnvironmentConfig,
    target: DeploymentTarget | VerifiedArtifact,
    *,
    migration_policy: str | None = None,
    yes: bool = False,
    allow_downgrade: bool = False,
    dry_run: bool = False,
    starting_state: Mapping[str, object] | None = None,
    prune_backup_ids: tuple[str, ...] | None = None,
) -> WorkflowResult:
    """Run genesis only for an unfinished install or its exact durable replay."""

    if not isinstance(config, EnvironmentConfig) or not isinstance(target, (DeploymentTarget, VerifiedArtifact)):
        raise TypeError("first release requires validated configuration and target")
    if not all(type(value) is bool for value in (yes, allow_downgrade, dry_run)):
        raise TypeError("first release flags must be boolean")
    if prune_backup_ids is not None and (
        not isinstance(prune_backup_ids, tuple)
        or any(type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None for backup_id in prune_backup_ids)
        or prune_backup_ids != tuple(sorted(set(prune_backup_ids)))
    ):
        raise TypeError("first release prune backup identifiers must be sorted unique IDs")
    deployment_target = (
        target if isinstance(target, DeploymentTarget)
        else DeploymentTarget(artifact=target, release_record=None, source="explicit")
    )
    candidate = deployment_target.release_id
    try:
        expected_state = _confirmed_expected_state(remote, config, mode="provision")
        confirmed_preconvergence = _starting_expected_state(starting_state)
        if (
            confirmed_preconvergence is not None
            and expected_state != confirmed_preconvergence
        ):
            raise _safety(
                "release, schema, protection, or scheduler authority changed during provisioning; rerun to confirm a new plan"
            )
        current = expected_state["selected_release_id"]
        # The first durable successful-selection record, not a physical
        # current link, is the command boundary.  A failed initial attempt may
        # have selected another validated installed release and still belongs
        # to provision; once history exists only its exact replay remains.
        if expected_state["last_successful_selection_id"] is not None and current != candidate:
            raise _safety("a completed installation requires deploy for a different release")
        if expected_state["last_successful_selection_id"] is not None:
            completed = deployment_admission_authority(remote, config, mode="provision")
            if (
                completed.selected_release_id != candidate
                or completed.last_successful_release_id != candidate
            ):
                raise _safety("a completed installation requires deploy for a different release")
        applied = tuple(expected_state["applied_migrations"])
        pending = _pending_migration_versions(applied, deployment_target.manifest.migrations)
        if migration_policy is None:
            if pending and applied:
                raise OpsError(
                    ExitStatus.INVALID,
                    "provision",
                    "partial initial migrations require --migration-policy backward-compatible",
                    changed=False,
                    next_action="review the initial schema and explicitly acknowledge backward-compatible migration continuation",
                )
            policy = "restore-required" if pending and not applied else "no-change"
        else:
            policy = migration_policy
        if policy not in _POLICIES:
            raise ValueError("first release requires a valid migration policy")
        if not (not applied and pending and policy == "restore-required"):
            _validate_migration_policy(applied, deployment_target.manifest.migrations, policy)
        # The null successful-history baseline uses the same bounded recovery
        # retention calculation as an ordinary deploy.  It is material
        # authority: the host may retire only these IDs after a fresh
        # protection exists, never a controller-side empty placeholder.
        if prune_backup_ids is None:
            prune_backup_ids, _recovery_evidence = _prune_plan_authority(
                _planned_prune_backup_ids(
                    remote,
                    config,
                    expected_state,
                    fresh_backup_needed=bool(pending),
                )
            )
        if dry_run:
            return WorkflowResult(
                "deploy",
                config.name or "",
                False,
                "planned",
                {
                    "candidate_release_id": candidate,
                    "migration_policy": policy,
                    "starting_state": dict(starting_state or expected_state),
                    "artifact_source": deployment_target.source,
                },
                next_action="review the redacted provisioning plan and rerun without --dry-run after confirmation",
            )
        with temporary_scheduled_backup_helper_package() as scheduler_package:
            result = run_deployment_request(
                remote,
                config,
                deployment_target,
                expected_state=expected_state,
                migration_policy=policy,
                backup_helper={
                    "sha256": scheduler_package.sha256,
                    "upload_path": (
                        None
                        if expected_state["scheduled_backup_sha256"] == scheduler_package.sha256
                        else "pending-controller-upload"
                    ),
                },
                prune_backup_ids=prune_backup_ids,
                backup_helper_package=scheduler_package,
                genesis=True,
            )
        if result.outcome != "succeeded":
            raise result_error(result)
        return _payload_result(
            config,
            candidate,
            policy,
            result,
            previous_release_id=None,
            genesis=True,
            starting_state=starting_state or expected_state,
        )
    except OpsError as error:
        result = _failure_result(config, error, candidate=candidate, genesis=True)
        if starting_state is None:
            return result
        return WorkflowResult(
            result.command,
            result.environment,
            result.changed,
            result.stage,
            {**result.facts, "starting_state": dict(starting_state)},
            result.warnings,
            result.next_action,
            result.exit_status,
        )


def _planning_authority(
    remote: Remote,
    config: EnvironmentConfig,
) -> tuple[str, tuple[MigrationFingerprint, ...], tuple[int, ...]]:
    """Read only the completed selection and its observed schema authority."""

    result = run_request(remote, discovery_request(config, mode="deploy"))
    if result.outcome != "succeeded" or not isinstance(result.state, Mapping):
        raise _safety("deployment planning helper refused host state")
    state = mutable(result.state)
    if not isinstance(state, Mapping):
        raise _safety("deployment planning helper returned invalid host state")
    try:
        previous = validate_release_id(state["selected_release_id"])
        releases = state.get("releases")
        if isinstance(releases, list):
            release = next(
                item
                for item in releases
                if isinstance(item, Mapping) and item.get("release_id") == previous
            )
            migrations = _migration_fingerprints(release.get("migrations"))
        else:
            from .inventory import collect_inventory

            records = tuple(
                ReleaseRecord.from_mapping(record)
                for record in collect_inventory(
                    remote, config, "list_releases", deadline=time.monotonic() + 660.0
                )
            )
            record = next(record for record in records if record.release_id == previous)
            migrations = _migration_fingerprints(record.migrations)
        applied = validate_migration_versions(state["applied_migrations"])
    except (KeyError, StopIteration, TypeError, ValueError):
        raise _safety("deployment planning helper returned incomplete selection authority") from None
    return previous, migrations, applied


def _confirmed_expected_state(
    remote: Remote, config: EnvironmentConfig, *, mode: str = "deploy"
) -> dict[str, object]:
    """Reobserve exactly the material deploy facts immediately before apply."""

    result = run_request(remote, discovery_request(config, mode=mode))
    if result.outcome != "succeeded" or not isinstance(result.state, Mapping):
        raise _safety("deployment apply helper refused host state")
    state = mutable(result.state)
    required = {
        "selected_release_id",
        "last_successful_selection_id",
        "applied_migrations",
        "backup_protection_sha256",
        "scheduled_backup_sha256",
        "backup_timer_enabled",
        "downgrade_baseline_sha256",
    }
    if not isinstance(state, Mapping) or set(state) < required:
        raise _safety("deployment apply helper returned incomplete expected state")
    expected = {key: state[key] for key in required}
    try:
        if expected["selected_release_id"] is not None:
            validate_release_id(expected["selected_release_id"])
        if expected["last_successful_selection_id"] is not None and not isinstance(expected["last_successful_selection_id"], str):
            raise ValueError
        validate_migration_versions(expected["applied_migrations"])
        expected["applied_migrations"] = tuple(expected["applied_migrations"])
        if expected["scheduled_backup_sha256"] is not None and not isinstance(expected["scheduled_backup_sha256"], str):
            raise ValueError
        if type(expected["backup_timer_enabled"]) is not bool:
            raise ValueError
        for key in ("backup_protection_sha256", "downgrade_baseline_sha256"):
            value = expected[key]
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError
    except (TypeError, ValueError):
        raise _safety("deployment apply helper returned invalid expected state") from None
    return expected


def _starting_expected_state(starting_state: Mapping[str, object] | None) -> dict[str, object] | None:
    """Extract the apply-time authority already confirmed before pyinfra.

    Provisioning convergence may create missing infrastructure, but it does
    not authorize a concurrent change to release, schema, protection, or
    scheduled-helper authority.  Preserve legacy injected tests that do not
    provide host authority by returning ``None`` for that narrow seam.
    """

    if not isinstance(starting_state, Mapping):
        return None
    authority = starting_state.get("host_authority")
    if not isinstance(authority, Mapping):
        return None
    required = {
        "selected_release_id",
        "last_successful_selection_id",
        "applied_migrations",
        "backup_protection_sha256",
        "scheduled_backup_sha256",
        "backup_timer_enabled",
        "downgrade_baseline_sha256",
    }
    if not required.issubset(authority):
        return None
    return {key: authority[key] for key in required}


def _downgrade_acknowledgment(
    remote: Remote,
    config: EnvironmentConfig,
    target: DeploymentTarget,
    repo: Path | None,
    *,
    mode: str = "deploy",
) -> tuple[bool, tuple[tuple[str, str, tuple[str, ...]], ...]]:
    """Classify every validated deploy baseline without treating uncertainty as forward."""

    from .inventory import collect_inventory

    if mode not in {"deploy", "provision"}:
        raise ValueError("downgrade authority mode is invalid")
    result = run_request(remote, discovery_request(config, mode=mode))
    if result.outcome != "succeeded" or not isinstance(result.state, Mapping):
        raise _safety("deployment planning helper refused downgrade authority")
    state = mutable(result.state)
    try:
        selected = state["selected_release_id"]
        selected = None if selected is None else validate_release_id(selected)
        latest = state["last_successful_selection"]
        latest_record = None if latest is None else SelectionRecord.from_mapping(latest)
        latest_id = state["last_successful_selection_id"]
        if (
            (latest_record is None and latest_id is not None)
            or (
                latest_record is not None
                and (type(latest_id) is not str or re.fullmatch(r"selection-[0-9a-f]{64}\.json", latest_id) is None)
            )
        ):
            raise ValueError
        protections = tuple(BackupProtection.from_mapping(item) for item in state["backup_protections"])
        baseline_ids = set()
        if selected is not None:
            baseline_ids.add(selected)
        if latest_record is not None:
            baseline_ids.add(latest_record.release_id)
        baseline_ids.update(item.target_release_id for item in protections)
        records = {
            record.release_id: record
            for record in (
                ReleaseRecord.from_mapping(value)
                for value in collect_inventory(
                    remote, config, "list_releases", deadline=time.monotonic() + 660.0
                )
            )
        }
        # Fresh provision may have applied schema versions without a physical
        # current selection.  Its helper includes every installed record whose
        # immutable migration provenance intersects those versions; mirror that
        # exact bounded baseline set before checking its digest.
        if mode == "provision" and selected is None and latest_record is None:
            applied = frozenset(validate_migration_versions(state["applied_migrations"]))
            baseline_ids.update(
                release_id
                for release_id, record in records.items()
                if applied.intersection(release_migration_versions(record.migrations))
            )
        expected_digest = hashlib.sha256(
            json.dumps(sorted(baseline_ids), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        if state["downgrade_baseline_sha256"] != expected_digest:
            raise ValueError
        baselines = tuple(records[release_id] for release_id in sorted(baseline_ids))
    except (KeyError, TypeError, ValueError):
        raise _safety("deployment planning helper returned invalid downgrade authority") from None

    orders = tuple(
        compare_sources(
            repo or Path.cwd(),
            target_version=target.manifest.application_version,
            target_revision=target.source_revision,
            baseline_version=baseline.artifact_manifest.application_version,
            baseline_revision=baseline.source_revision,
        )
        for baseline in baselines
    )
    evidence = tuple(
        (baseline.release_id, order.kind, order.reasons)
        for baseline, order in zip(baselines, orders, strict=True)
    )
    return any(order.needs_acknowledgment for order in orders), evidence


def _payload_result(
    config: EnvironmentConfig,
    candidate: str,
    policy: str,
    result: HostResult,
    *,
    previous_release_id: str | None,
    genesis: bool = False,
    starting_state: Mapping[str, object] | None = None,
) -> WorkflowResult:
    facts = _facts(
        result,
        candidate,
        policy,
        previous_release_id=previous_release_id,
        genesis=genesis,
        starting_state=starting_state,
    )
    changed = facts["changed"]
    assert isinstance(changed, bool)
    if not changed:
        return WorkflowResult(
            "deploy", config.name or "", False, "already-current", facts, tuple(result.warnings),
            "no deployment selection or migration action is required",
        )
    return WorkflowResult(
        "deploy", config.name or "", True, "deployed", facts, tuple(result.warnings),
        "perform the remaining browser, email, and API acceptance checks",
    )


def _facts(
    result: HostResult,
    candidate: str,
    policy: str,
    *,
    previous_release_id: str | None,
    genesis: bool,
    starting_state: Mapping[str, object] | None,
) -> dict[str, object]:
    try:
        evidence = mutation_result_facts(result)
        observations = evidence["observations"]
        verification = successful_verification(evidence["report"], candidate)
    except (TypeError, ValueError):
        raise _safety("deployment helper returned invalid success evidence")
    if (
        not isinstance(observations, Mapping)
        or evidence["desired_release_id"] != candidate
        or observations["selected_release_id"] != candidate
        or observations["service_state"] != "running"
    ):
        raise _safety("deployment helper returned invalid success evidence") from None
    changed = evidence["mutation_state"] != "unchanged"
    return {
        "starting_state": None if starting_state is None else dict(starting_state),
        "changed": changed,
        "previous_release_id": None if genesis else previous_release_id,
        "candidate_release_id": candidate,
        "selected_release_id": candidate,
        "backup_id": evidence["backup_id"],
        "migration_policy": policy,
        "database_changed": evidence["mutation_state"] == "changed",
        "database_state": observations["database_state"],
        "service_state": observations["service_state"],
        "mutation_state": evidence["mutation_state"],
        "last_successful_selection_id": observations["last_successful_selection_id"],
        "applied_migrations": observations["applied_migrations"],
        "protected_backup_ids": observations["protected_backup_ids"],
        "backup_protection_sha256": observations["backup_protection_sha256"],
        "scheduled_backup_sha256": observations["scheduled_backup_sha256"],
        "backup_timer_enabled": observations["backup_timer_enabled"],
        "backup_timer_state": observations["backup_timer_state"],
        "unavailable_fields": evidence["unavailable_fields"],
        "inspection_error": evidence["inspection_error"],
        "verification": verification,
    }


def _failure_result(
    config: EnvironmentConfig,
    error: OpsError,
    *,
    candidate: str,
    genesis: bool = False,
) -> WorkflowResult:
    state = getattr(error, "state", {})
    if not isinstance(state, Mapping):
        state = {}
    stage = {
        ExitStatus.BACKUP: "backup-failed",
        ExitStatus.MIGRATION: "migration-failed",
        ExitStatus.RELEASE: "deployment-incomplete",
        ExitStatus.READINESS: "verification-failed",
        ExitStatus.SAFETY: "safety-refused",
        ExitStatus.LOCKED: "lock-contended",
        ExitStatus.INVALID: "invalid-input",
    }.get(error.status, f"{error.stage}-failed")
    observations = state.get("observations", {})
    if not isinstance(observations, Mapping):
        observations = {}
    return WorkflowResult(
        "deploy", config.name or "", error.changed, stage,
        {
            "previous_release_id": None if genesis else observations.get("selected_release_id"),
            "candidate_release_id": candidate,
            "selected_release_id": observations.get("selected_release_id"),
            "backup_id": state.get("backup_id"),
            "database_state": observations.get("database_state", "unknown"),
            "service_state": observations.get("service_state", "unknown"),
            "failure_boundary": state.get("failed_boundary", error.stage),
            "verification": state.get("report"),
            "mutation_state": state.get("mutation_state", "unknown"),
            "last_successful_selection_id": observations.get("last_successful_selection_id"),
            "applied_migrations": observations.get("applied_migrations"),
            "protected_backup_ids": observations.get("protected_backup_ids"),
            "backup_protection_sha256": observations.get("backup_protection_sha256"),
            "scheduled_backup_sha256": observations.get("scheduled_backup_sha256"),
            "backup_timer_enabled": observations.get("backup_timer_enabled"),
            "backup_timer_state": observations.get("backup_timer_state"),
            "unavailable_fields": state.get("unavailable_fields", []),
            "inspection_error": state.get("inspection_error"),
        },
        tuple(getattr(error, "warnings", ())),
        error.next_action or "inspect the observed host state and rerun when it is safe",
        error.status,
    )


def _plan(
    config: EnvironmentConfig,
    target: DeploymentTarget,
    previous: str,
    policy: str,
    *,
    expected_state: Mapping[str, object],
    pending_versions: tuple[int, ...],
    prune_backup_ids: tuple[str, ...],
    scheduler_sha256: str,
    scheduler_refresh_required: bool,
    material_evidence: Mapping[str, object],
) -> dict[str, object]:
    return {
        "environment": config.name or "",
        "ssh_destination": f"{config.ssh_user}@{config.ssh_host}:{config.ssh_port}",
        "public_hostname": config.public_hostname,
        "current_release_id": previous,
        "candidate_release_id": target.release_id,
        "source_revision": target.source_revision,
        "artifact_sha256": target.artifact_sha256,
        "artifact_source": target.source,
        "migration_policy": policy,
        "applied_migrations": list(expected_state["applied_migrations"]),
        "pending_migration_versions": list(pending_versions),
        "planned_backup": bool(pending_versions),
        "last_successful_selection_id": expected_state["last_successful_selection_id"],
        "backup_protection_sha256": expected_state["backup_protection_sha256"],
        "prune_backup_ids": list(prune_backup_ids),
        **material_evidence,
        "scheduled_backup": {
            "observed_sha256": expected_state["scheduled_backup_sha256"],
            "desired_sha256": scheduler_sha256,
            "refresh_required": scheduler_refresh_required,
            "timer_enabled": expected_state["backup_timer_enabled"],
        },
        "services_affected": ("taskman.service",),
        "expected_maintenance_window": "brief Taskman service interruption after backup",
    }


def _redacted_plan(plan: Mapping[str, object]) -> dict[str, object]:
    safe = redact(dict(plan))
    if not isinstance(safe, Mapping):
        raise RuntimeError("redacted deployment plan is invalid")
    return dict(safe)


def _present_plan(plan: Mapping[str, object]) -> None:
    print(
        render_human(
            WorkflowResult(
                "deploy", str(plan.get("environment", "")), False, "planned", {"plan": dict(plan)},
                next_action="confirm before starting helper deployment",
            )
        )
    )


def _confirm(_plan: Mapping[str, object]) -> bool:
    return input("Apply this redacted deployment plan? Type yes to continue: ").strip().lower() == "yes"


def _confirm_downgrade(_plan: Mapping[str, object]) -> bool:
    return input("Acknowledge the downgrade or unknown ordering? Type yes to continue: ").strip().lower() == "yes"


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "deploy",
        message,
        changed=False,
        next_action="inspect the completed host state before retrying",
    )


def _migration_fingerprints(value: object) -> tuple[MigrationFingerprint, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("invalid migration fingerprints")
    values = tuple(MigrationFingerprint.from_mapping(item) for item in value)
    if tuple(item.filename for item in values) != tuple(sorted(item.filename for item in values)) or len(
        {item.filename for item in values}
    ) != len(values):
        raise ValueError("invalid migration fingerprints")
    return values


def _pending_migration_versions(
    applied_versions: tuple[int, ...], candidate: tuple[MigrationFingerprint, ...]
) -> tuple[int, ...]:
    """Validate the live schema as a prefix and return only missing versions."""

    candidate_versions = tuple(int(item.filename[:14]) for item in candidate)
    if candidate_versions != tuple(sorted(set(candidate_versions))):
        raise _safety("candidate migration versions are invalid")
    if candidate_versions[: len(applied_versions)] != applied_versions:
        raise _safety("live migrations are not a prefix of the desired release")
    return candidate_versions[len(applied_versions) :]


def _planned_prune_backup_ids(
    remote: Remote,
    config: EnvironmentConfig,
    expected_state: Mapping[str, object],
    *,
    fresh_backup_needed: bool,
) -> tuple[tuple[str, ...], dict[str, object]]:
    """Derive the exact attempt retirements authorized by one displayed plan."""

    result = run_request(remote, discovery_request(config, mode="deploy"))
    if result.outcome != "succeeded" or not isinstance(result.state, Mapping):
        raise _safety("deployment planning helper refused backup-protection authority")
    state = mutable(result.state)
    if not isinstance(state, Mapping):
        raise _safety("deployment planning helper returned invalid backup-protection authority")
    try:
        observed = {key: state[key] for key in expected_state}
        if observed != mutable(dict(expected_state)):
            raise ValueError
        protections = tuple(BackupProtection.from_mapping(item) for item in state["backup_protections"])
        independent_values = state["independently_held_backup_ids"]
        if (
            not isinstance(independent_values, (list, tuple))
            or tuple(independent_values) != tuple(sorted(set(independent_values)))
            or any(type(item) is not str for item in independent_values)
        ):
            raise ValueError
        independent = frozenset(independent_values)
        if not independent.issubset({item.backup_id for item in protections}):
            raise ValueError
        current = protection_prune_ids(
            protections,
            expected_state["last_successful_selection_id"],
            independently_held_backup_ids=independent,
        )
        planned = current
        if not planned and fresh_backup_needed:
            planned = protection_prune_ids_after_fresh_attempt(
                protections,
                expected_state["last_successful_selection_id"],
                independently_held_backup_ids=independent,
            )
        return planned, {"protections": protections, "independent_backup_ids": independent}
    except (KeyError, TypeError, ValueError):
        raise _safety("deployment backup-protection authority changed during planning") from None


def _prune_plan_authority(value: object) -> tuple[tuple[str, ...], dict[str, object]]:
    """Normalize the complete current-or-conditional pruning authority."""

    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], tuple)
        and isinstance(value[1], Mapping)
    ):
        ids = value[0]
        evidence = dict(value[1])
    else:
        raise _safety("deployment backup-protection plan is invalid")
    if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
        raise _safety("deployment backup-protection plan is invalid")
    if set(evidence) != {"protections", "independent_backup_ids"}:
        raise _safety("deployment backup-protection evidence is invalid")
    return ids, evidence


def _downgrade_evidence_rows(
    evidence: tuple[object, ...],
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Validate the named baseline comparisons which ordinary consent binds."""

    rows: list[tuple[str, str, tuple[str, ...]]] = []
    for item in evidence:
        if not (
            isinstance(item, tuple)
            and len(item) == 3
            and isinstance(item[0], str)
            and item[1] in {"downgrade", "forward", "equal", "unknown", "no-baseline"}
            and isinstance(item[2], tuple)
            and all(isinstance(reason, str) for reason in item[2])
        ):
            raise _safety("deployment downgrade evidence is invalid")
        rows.append((item[0], item[1], item[2]))
    return tuple(rows)


def _material_plan_evidence(
    protections: tuple[BackupProtection, ...],
    independently_held_backup_ids: frozenset[str] | set[str],
    prune_backup_ids: tuple[str, ...],
    downgrade_baselines: tuple[tuple[str, str, tuple[str, ...]], ...],
) -> dict[str, object]:
    """Render the bounded protection disposition and named order evidence."""

    held = frozenset(independently_held_backup_ids)
    pruned = frozenset(prune_backup_ids)
    protection_ids = frozenset(item.backup_id for item in protections)
    if not held.issubset(protection_ids) or not pruned.issubset(protection_ids):
        raise _safety("deployment recovery projection is inconsistent")
    return {
        "recovery_protection_points": [
            {
                "backup_id": protection.backup_id,
                "attempt_number": protection.attempt_number,
                "base_selection_id": protection.base_selection_id,
                "target_release_id": protection.target_release_id,
                "independently_referenced": protection.backup_id in held,
                "disposition": (
                    "prune-authorized" if protection.backup_id in pruned else "retained"
                ),
            }
            for protection in sorted(protections, key=lambda item: item.backup_id)
        ],
        "downgrade_baselines": [
            {"release_id": release_id, "order": order, "reasons": list(reasons)}
            for release_id, order, reasons in downgrade_baselines
        ],
    }


def _validate_migration_policy(
    current: tuple[MigrationFingerprint, ...] | tuple[int, ...],
    candidate: tuple[MigrationFingerprint, ...],
    policy: str,
) -> None:
    if current and isinstance(current[0], MigrationFingerprint):
        applied = tuple(int(item.filename[:14]) for item in current)
    else:
        applied = validate_migration_versions(current)
    pending = _pending_migration_versions(applied, candidate)
    if not pending and policy in {"no-change", "backward-compatible"}:
        return
    if pending and policy == "backward-compatible":
        return
    raise _safety("confirmed migration policy does not match completed release authority")


def _with_artifact_source(result: WorkflowResult, source: str) -> WorkflowResult:
    return WorkflowResult(
        result.command,
        result.environment,
        result.changed,
        result.stage,
        {**result.facts, "artifact_source": source},
        result.warnings,
        result.next_action,
        result.exit_status,
        result.process_status,
    )


__all__ = ["DeploymentAdmissionAuthority", "deploy", "deploy_first_release", "deployment_admission_authority"]
