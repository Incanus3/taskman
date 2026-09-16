"""Ordered clean-host provisioning assembled from existing capabilities.

This workflow intentionally owns sequencing and reporting only.  Host state,
credentials, release activation, and readiness stay owned by the focused
capabilities provided by the established deployment workflow.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass, replace
import hashlib
from pathlib import Path
from typing import Protocol

from ..config import EnvironmentConfig, load_environment
from ..errors import ExitStatus, OpsError
from ..migrations import versions_from_filenames
from ..host.acceptance import validate_provisionable_host
from ..host_helper.backup_protection import (
    BackupProtection,
    protection_prune_ids,
    protection_prune_ids_after_fresh_attempt,
)
from ..host_helper.records import RecordError
from ..releases.artifacts import (
    CleanInputs,
    DeploymentTarget,
    clean_inputs_drifted,
    clean_inputs_match,
    identify_clean_inputs,
    resolve_deploy_target,
)
from ..output import WorkflowResult, redact, render_human
from ..provisioning import (
    ProvisioningInputs,
    converge_provisioning,
    validate_existing_authority,
)
from ..remote import ChangeSet, connect
from ..secrets import SecretConfig, decrypt_secrets, render_pgpass, render_runtime_environment
from ..services.caddy import CaddyPlan, build_caddy_plan
from ..services.systemd import build_systemd_plan
from ..services.postgresql import (
    build_postgresql_plan,
    render_role_password_input,
)
from .deploy import _confirm_downgrade, _downgrade_acknowledgment, deploy_first_release
from .helper import merge_warnings


AcceptanceSteps = tuple[str, ...]
_ACCEPTANCE_STEPS: AcceptanceSteps = (
    "create the initial administrator interactively",
    "sign in over HTTPS",
    "send and receive a Resend invitation",
    "create and use an API key",
    "verify a LiveView route remains connected",
    "copy a verified local backup off-host",
)
_MAX_CLEAN_INPUT_RERESOLUTIONS = 3


class ProvisionDiscovery(Protocol):
    """The provisioning-only discovery boundary bound to its Caddy plan."""

    def __call__(
        self,
        remote: object,
        config: EnvironmentConfig,
        *,
        expected_caddyfile_sha256: str,
    ) -> object: ...


@dataclass(frozen=True)
class ProvisionCapabilities:
    """The existing public capabilities that provisioning orders.

    Injection is deliberately at capability boundaries: the stateful fake in
    the workflow contract exercises real orchestration without mocking a
    lower-level command or repeating any deployment implementation here.
    """

    load_environment: Callable[[str], EnvironmentConfig]
    decrypt_secrets: Callable[[str], SecretConfig]
    render_runtime_environment: Callable[[EnvironmentConfig, SecretConfig], bytes]
    render_pgpass: Callable[[EnvironmentConfig, SecretConfig], bytes]
    render_plan: Callable[[EnvironmentConfig, DeploymentTarget], Mapping[str, object]]
    present_plan: Callable[[Mapping[str, object]], None]
    confirm: Callable[[Mapping[str, object]], bool]
    connect: Callable[[EnvironmentConfig], object]
    discover: ProvisionDiscovery
    render_role_password_input: Callable[[object, str], bytes]
    provisioning: Callable[[object, ProvisioningInputs], ChangeSet]
    caddy_plan: Callable[[EnvironmentConfig], CaddyPlan]
    genesis: Callable[..., WorkflowResult]
    preflight: Callable[[object, ProvisioningInputs], Mapping[str, object]]
    target_resolution: Callable[[object, EnvironmentConfig, object, CleanInputs | None], DeploymentTarget]
    downgrade_authority: Callable[[object, EnvironmentConfig, DeploymentTarget], tuple[bool, tuple[tuple[str, str, tuple[str, ...]], ...]]]


def provision(
    invocation: object,
    *,
    capabilities: ProvisionCapabilities | None = None,
) -> WorkflowResult:
    """Converge one supported clean host without expanding lower-level ownership.

    Local validation and plan construction happen before SSH. Immutable host
    admission occurs before plan presentation and confirmation, while the
    deployment transaction remains the authoritative owner of staging,
    backup, activation, and migration failure semantics.
    """

    environment_name = _environment_name(invocation)
    dry_run = _dry_run(invocation)
    yes = _yes(invocation)
    cap = capabilities or _default_capabilities()

    config = cap.load_environment(environment_name)
    secrets = cap.decrypt_secrets(environment_name)
    # Capture clean source identity before *any* host observation.  The
    # resolver consumes this frozen value only after it has received validated
    # installed-release authority; a later clean-input mismatch restarts the
    # whole material-plan cycle below.
    clean_inputs: CleanInputs | None = None
    if getattr(invocation, "artifact", None) is None:
        try:
            clean_inputs = identify_clean_inputs(_repository_root())
        except OpsError:
            if not getattr(invocation, "allow_dirty", False):
                raise
    runtime_environment = cap.render_runtime_environment(config, secrets)
    pgpass = cap.render_pgpass(config, secrets)
    # Build locally before confirmation. The role-password exchange is already
    # rendered bytes when it reaches the custom PostgreSQL boundary, so raw
    # credential text never enters pyinfra command construction or reporting.
    database_plan = build_postgresql_plan(config)
    role_password_input = cap.render_role_password_input(database_plan.role, secrets.database_password)
    caddy_plan = cap.caddy_plan(config)
    expected_caddyfile_sha256 = _caddyfile_sha256(caddy_plan)

    remote = cap.connect(config)
    confirmed_starting_state: dict[str, object] | None = None
    warnings: tuple[str, ...] = ()
    clean_reresolutions = 0
    try:
        while True:
            # Discovery is a complete immutable snapshot.  It must precede every
            # consequence below, including plan presentation, confirmation, and
            # package installation.
            discovery = cap.discover(remote, config, expected_caddyfile_sha256=expected_caddyfile_sha256)
            try:
                release_input = cap.target_resolution(remote, config, invocation, clean_inputs)
            except OpsError as error:
                if clean_inputs is None or not clean_inputs_drifted(error):
                    raise
                if clean_reresolutions >= _MAX_CLEAN_INPUT_RERESOLUTIONS:
                    raise OpsError(
                        ExitStatus.SAFETY,
                        "provision",
                        "clean provisioning inputs did not stabilize while preparing a plan",
                        changed=False,
                        next_action="restore a stable intended clean checkout and rerun provision",
                    ) from None
                clean_inputs = identify_clean_inputs(_repository_root())
                clean_reresolutions += 1
                continue
            _validate_artifact_target(config, release_input)
            if clean_inputs is not None and not clean_inputs_match(_repository_root(), clean_inputs):
                # Unlike an explicit artifact or frozen dirty source, a clean
                # target has no independent identity until the source inputs
                # are freshly re-identified.  A previous confirmation cannot
                # authorize whichever checkout happens to be present now.
                if clean_reresolutions >= _MAX_CLEAN_INPUT_RERESOLUTIONS:
                    raise OpsError(
                        ExitStatus.SAFETY,
                        "provision",
                        "clean provisioning inputs did not stabilize while preparing a plan",
                        changed=False,
                        next_action="restore a stable intended clean checkout and rerun provision",
                    )
                clean_inputs = identify_clean_inputs(_repository_root())
                clean_reresolutions += 1
                continue
            # A stable material plan owns these rendered assets through refreshed
            # authority and installation; source retries above have no asset plan.
            inputs = ProvisioningInputs(
                config=config,
                caddy_plan=caddy_plan,
                systemd_plan=build_systemd_plan(config),
                runtime_environment=runtime_environment,
                pgpass=pgpass,
                role_password_input=role_password_input,
            )
            authority_evidence = cap.preflight(remote, inputs)
            warnings = merge_warnings(
                warnings, tuple(getattr(authority_evidence, "warnings", ()))
            )
            authority = _required_authority(authority_evidence)
            convergence_inputs = _scheduler_authorized_inputs(inputs, authority)
            starting_state = _starting_state(authority, discovery, release_input)
            plan_effects = _provision_plan_effects(starting_state, release_input, invocation)
            plan = _redacted_plan(cap.render_plan(config, release_input))
            downgrade_required, downgrade_evidence = cap.downgrade_authority(remote, config, release_input)
            plan = {
                **plan,
                "requires_downgrade_acknowledgment": downgrade_required,
                "downgrade_baselines": _downgrade_plan_rows(downgrade_evidence),
            }
            plan = {**plan, **plan_effects, "starting_state": starting_state}
            cap.present_plan(plan)
            if dry_run:
                return _close_result(remote, WorkflowResult(
                    command="provision",
                    environment=environment_name,
                    changed=False,
                    stage="planned",
                    facts={"plan": plan, "discovery": "validated"},
                    next_action="review the redacted plan and rerun without --dry-run only after confirmation",
                    warnings=warnings,
                ))

            if not yes and not cap.confirm(plan):
                return _close_result(remote, WorkflowResult(
                    command="provision",
                    environment=environment_name,
                    changed=False,
                    stage="confirmation-cancelled",
                    facts={"plan": plan},
                    next_action="review the redacted plan and confirm a later provisioning run when ready",
                    exit_status=ExitStatus.SAFETY,
                    warnings=warnings,
                ))

            # This is the only state ordinary confirmation authorizes.  Keep
            # an owned immutable-value copy before any post-confirmation
            # boundary so even a pyinfra refusal can report the exact plan
            # that was confirmed rather than a later observation.
            confirmed_starting_state = _copy_authority(starting_state)

            if downgrade_required and not getattr(invocation, "allow_downgrade", False):
                if yes or _noninteractive(invocation):
                    raise OpsError(
                        ExitStatus.SAFETY,
                        "provision",
                        "provision ordering requires --allow-downgrade acknowledgement",
                        changed=False,
                        next_action="review the displayed downgrade or unknown-order evidence and rerun with --allow-downgrade",
                    )
                if not _confirm_downgrade(plan):
                    return _close_result(remote, WorkflowResult(
                        command="provision",
                        environment=environment_name,
                        changed=False,
                        stage="downgrade-acknowledgment-cancelled",
                        facts={"plan": plan},
                        next_action="review the downgrade or unknown-order evidence before retrying",
                        exit_status=ExitStatus.SAFETY,
                        warnings=warnings,
                    ))

            # Confirmation authorizes only the observed resource authority. A
            # drifted snapshot is refused; a later invocation must collect and
            # confirm a new plan before pyinfra receives a managed-write request.
            refreshed_discovery = cap.discover(
                remote, config, expected_caddyfile_sha256=expected_caddyfile_sha256
            )
            try:
                refreshed_input = cap.target_resolution(remote, config, invocation, clean_inputs)
            except OpsError as error:
                if clean_inputs is None or not clean_inputs_drifted(error):
                    raise
                raise OpsError(
                    ExitStatus.SAFETY,
                    "provision",
                    "clean provisioning inputs changed after confirmation; rerun to acknowledge the refreshed plan",
                    changed=False,
                    next_action="restore the intended clean checkout and rerun provision to review a new plan",
                ) from None
            if clean_inputs is not None and not clean_inputs_match(_repository_root(), clean_inputs):
                raise OpsError(
                    ExitStatus.SAFETY,
                    "provision",
                    "clean provisioning inputs changed after confirmation; rerun to acknowledge the refreshed plan",
                    changed=False,
                    next_action="restore the intended clean checkout and rerun provision to review a new plan",
                )
            refreshed_authority_evidence = cap.preflight(remote, inputs)
            warnings = merge_warnings(
                warnings, tuple(getattr(refreshed_authority_evidence, "warnings", ()))
            )
            refreshed_authority = _required_authority(refreshed_authority_evidence)
            _scheduler_authorized_inputs(inputs, refreshed_authority)
            refreshed_downgrade_required, refreshed_downgrade_evidence = cap.downgrade_authority(
                remote, config, refreshed_input
            )
            refreshed_starting_state = _starting_state(
                refreshed_authority, refreshed_discovery, refreshed_input
            )
            refreshed_plan_effects = _provision_plan_effects(
                refreshed_starting_state, refreshed_input, invocation
            )
            if (
                refreshed_discovery != discovery
                or refreshed_input != release_input
                or refreshed_authority != authority
                or refreshed_downgrade_required != downgrade_required
                or refreshed_downgrade_evidence != downgrade_evidence
                or refreshed_plan_effects != plan_effects
            ):
                raise OpsError(
                    ExitStatus.SAFETY,
                    "provision",
                    "provision authority changed after confirmation; rerun to acknowledge the refreshed plan",
                    changed=False,
                    next_action="inspect the refreshed plan and rerun provision with a new confirmation",
                )
            provisioning_changed = _changed(cap.provisioning(remote, convergence_inputs))
            break
    except OpsError as error:
        return _close_result(
            remote,
            replace(
                _pre_release_failure(
                    environment_name, error, starting_state=confirmed_starting_state
                ),
                warnings=merge_warnings(warnings, error.warnings),
            ),
        )
    except TypeError:
        return _close_result(remote, replace(
            _pre_release_failure(
                environment_name,
                OpsError(
                    ExitStatus.REMOTE_PREFLIGHT,
                    "provisioning",
                    "provisioning convergence returned invalid capability evidence",
                    changed=False,
                    next_action="inspect the provisioning boundary and retry",
                ),
                starting_state=confirmed_starting_state,
            ),
            warnings=warnings,
        ))
    except BaseException:
        _close_remote(remote)
        raise

    try:
        genesis_kwargs: dict[str, object] = {
            "migration_policy": getattr(invocation, "migration_policy", None),
            "yes": yes,
            "allow_downgrade": getattr(invocation, "allow_downgrade", False),
            "dry_run": dry_run,
            "starting_state": starting_state,
        }
        planned_prune_ids = plan_effects.get("prune_backup_ids")
        if isinstance(planned_prune_ids, tuple):
            genesis_kwargs["prune_backup_ids"] = planned_prune_ids
        genesis_kwargs["scheduler_create"] = _scheduler_create_delta(authority)
        release = cap.genesis(
            remote,
            config,
            release_input,
            **genesis_kwargs,
        )
    except BaseException:
        _close_remote(remote)
        raise

    if release.exit_status is not ExitStatus.OK:
        return _close_result(
            remote,
            replace(
                release,
                command="provision",
                changed=provisioning_changed or release.changed,
                facts={
                    **release.facts,
                    "starting_state": starting_state,
                    "mutation_state": _aggregate_mutation_state(
                        "changed" if provisioning_changed else "unchanged",
                        release.facts.get(
                            "mutation_state",
                            "changed" if release.changed else "unchanged",
                        ),
                    ),
                },
                warnings=merge_warnings(warnings, release.warnings),
            ),
        )
    return _close_result(remote, WorkflowResult(
        command="provision",
        environment=environment_name,
        changed=provisioning_changed or release.changed,
        stage="provisioned" if provisioning_changed or release.changed else "already-provisioned",
        facts={
            "candidate_release_id": _release_id(release_input),
            "starting_state": starting_state,
            "provisioning_changed": provisioning_changed,
            "mutation_state": _aggregate_mutation_state(
                "changed" if provisioning_changed else "unchanged",
                release.facts.get(
                    "mutation_state", "changed" if release.changed else "unchanged"
                ),
            ),
            "release": _safe_result_facts(release),
            "verification": release.facts.get("verification", {}),
            "acceptance_steps": _ACCEPTANCE_STEPS,
        },
        next_action="complete the listed interactive acceptance steps; they are not automated",
        warnings=merge_warnings(warnings, release.warnings),
    ))


def _environment_name(invocation: object) -> str:
    command = getattr(invocation, "command", None)
    environment = getattr(invocation, "environment", None)
    if command != "provision" or not isinstance(environment, str) or not environment:
        raise OpsError(
            ExitStatus.INVALID,
            "provision",
            "provision requires a validated environment invocation",
            changed=False,
            next_action="invoke provision with one configured environment name",
        )
    return environment


def _dry_run(invocation: object) -> bool:
    value = getattr(invocation, "dry_run", False)
    if not isinstance(value, bool):
        raise OpsError(
            ExitStatus.INVALID,
            "provision",
            "provision dry-run flag must be boolean",
            changed=False,
            next_action="correct the provision command arguments and retry",
        )
    return value


def _yes(invocation: object) -> bool:
    value = getattr(invocation, "yes", False)
    if type(value) is not bool:
        raise OpsError(
            ExitStatus.INVALID,
            "provision",
            "provision confirmation flag must be boolean",
            changed=False,
            next_action="correct the provision command arguments and retry",
        )
    return value


def _caddyfile_sha256(plan: CaddyPlan) -> str:
    """Bind later remote Caddy ownership checks to the rendered plan bytes."""

    caddyfile = plan.caddyfile
    if not isinstance(caddyfile, str):  # pragma: no cover - static CaddyPlan contract
        raise TypeError("Caddy plan must contain text Caddyfile bytes")
    return hashlib.sha256(caddyfile.encode("utf-8")).hexdigest()


def _default_capabilities() -> ProvisionCapabilities:
    return ProvisionCapabilities(
        load_environment=load_environment,
        decrypt_secrets=decrypt_secrets,
        render_runtime_environment=render_runtime_environment,
        render_pgpass=render_pgpass,
        render_plan=_render_plan,
        present_plan=_present_plan,
        confirm=_confirm,
        connect=connect,
        discover=validate_provisionable_host,
        render_role_password_input=render_role_password_input,
        provisioning=converge_provisioning,
        caddy_plan=build_caddy_plan,
        genesis=deploy_first_release,
        preflight=validate_existing_authority,
        target_resolution=_resolve_deployment_target,
        downgrade_authority=_provision_downgrade_authority,
    )


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_deployment_target(
    remote: object,
    config: EnvironmentConfig,
    invocation: object,
    clean_inputs: CleanInputs | None,
) -> DeploymentTarget:
    """Resolve provision's desired release from paged validated host authority."""

    from .deploy import deployment_admission_authority

    authority = deployment_admission_authority(remote, config, mode="provision")
    target = resolve_deploy_target(
        _repository_root(),
        getattr(invocation, "artifact", None),
        installed_records=authority.installed_records,
        selected_release_id=authority.selected_release_id,
        last_successful_release_id=authority.last_successful_release_id,
        allow_dirty=bool(getattr(invocation, "allow_dirty", False)),
        clean_inputs=clean_inputs,
    )
    if getattr(invocation, "allow_dirty", False) and getattr(invocation, "artifact", None) is not None and not target.source_dirty:
        raise OpsError(
            ExitStatus.INVALID,
            "arguments",
            "--allow-dirty is not valid with an explicitly selected clean artifact",
            changed=False,
            next_action="omit --allow-dirty or select a dirty-provenance artifact",
        )
    return target


def _validate_artifact_target(config: EnvironmentConfig, artifact: DeploymentTarget) -> None:
    manifest = artifact.manifest
    if manifest.target_os != config.target_os or manifest.architecture != config.architecture:
        raise OpsError(
            ExitStatus.LOCAL_PREREQUISITE,
            "artifact",
            "verified artifact does not target the configured supported host",
            changed=False,
            next_action="build or select an Ubuntu 26.04 amd64 Taskman artifact",
        )


def _render_plan(config: EnvironmentConfig, artifact: DeploymentTarget) -> Mapping[str, object]:
    return {
        "environment": config.name or "",
        "ssh_destination": f"{config.ssh_user}@{config.ssh_host}:{config.ssh_port}",
        "public_hostname": config.public_hostname,
        "candidate_release_id": artifact.manifest.release_id,
        "artifact_sha256": _artifact_sha256(artifact),
        "artifact_source": artifact.source,
        "source_dirty": artifact.source_dirty,
        "services": ("PostgreSQL", "taskman.service", "taskman-backup.timer", "Caddy"),
        "planned_backup": "validated local PostgreSQL backup timer",
    }


def _release_id(value: DeploymentTarget) -> str:
    return value.manifest.release_id


def _artifact_sha256(value: DeploymentTarget) -> str:
    return value.artifact_sha256


def _provision_downgrade_authority(
    remote: object,
    config: EnvironmentConfig,
    target: DeploymentTarget,
) -> tuple[bool, tuple[tuple[str, str, tuple[str, ...]], ...]]:
    """Use deploy's complete baseline classifier for public genesis too."""

    return _downgrade_acknowledgment(
        remote, config, target, _repository_root(), mode="provision"
    )


def _downgrade_plan_rows(
    evidence: tuple[tuple[str, str, tuple[str, ...]], ...],
) -> list[dict[str, object]]:
    return [
        {"release_id": release_id, "order": order, "reasons": list(reasons)}
        for release_id, order, reasons in evidence
    ]


def _noninteractive(invocation: object) -> bool:
    return bool(getattr(invocation, "json", False)) or getattr(invocation, "interactive", None) is False


def _starting_state(
    authority: Mapping[str, object],
    discovery: object,
    target: DeploymentTarget,
) -> dict[str, object]:
    """Preserve the pre-convergence authority that was actually observed."""

    result: dict[str, object] = {
        "authority": "validated",
        "candidate_release_id": _release_id(target),
    }
    result.update(
        artifact_sha256=target.artifact_sha256,
        artifact_source=target.source,
        source_dirty=target.source_dirty,
        host_authority=_copy_authority(authority),
        resource_authority=_required_resource_authority(discovery),
    )
    return result


def _provision_plan_effects(
    starting_state: Mapping[str, object],
    target: DeploymentTarget,
    invocation: object,
) -> dict[str, object]:
    """Project the bounded recovery consequences the operator confirms.

    The helper remains the authority for applying the plan.  This projection
    deliberately contains no credential bytes and is derived solely from the
    exact resource/host snapshots already admitted before pyinfra.
    """

    authority = starting_state.get("host_authority")
    effects: dict[str, object] = {
        "target": {
            "release_id": _release_id(target),
            "artifact_sha256": _artifact_sha256(target),
            "provenance": target.source,
            "source_dirty": target.source_dirty,
        },
        "resource_convergence": {
            "host": starting_state.get("resource_authority"),
            "scheduler_create": list(_scheduler_create_delta(authority)),
            "scheduler_refresh": "genesis-owned",
        },
    }
    if not isinstance(authority, Mapping):  # pragma: no cover - _starting_state requires mapping
        raise TypeError("provisioning authority must be a mapping")
    try:
        applied = authority["applied_migrations"]
        protections = _plan_protections(authority)
        independent_ids = tuple(authority["independently_held_backup_ids"])
        baseline = authority["last_successful_selection_id"]
        selected_release_id = authority["selected_release_id"]
        latest_selection = authority["last_successful_selection"]
        previous_selection = authority["previous_successful_selection"]
        scheduled_backup_sha256 = authority["scheduled_backup_sha256"]
        backup_timer_enabled = authority["backup_timer_enabled"]
        backup_timer_state = authority["backup_timer_state"]
    except (KeyError, TypeError, RecordError):
        raise _projection_error() from None
    candidate_versions = versions_from_filenames(tuple(item.filename for item in target.manifest.migrations))
    pending = candidate_versions[len(applied) :] if candidate_versions[: len(applied)] == applied else ()
    try:
        prune_backup_ids = (
            protection_prune_ids_after_fresh_attempt(
                protections, baseline, independently_held_backup_ids=independent_ids
            )
            if pending
            else protection_prune_ids(
                protections, baseline, independently_held_backup_ids=independent_ids
            )
        )
    except RecordError:
        raise _projection_error() from None
    effects.update(
        {
            "physical_current_release_id": selected_release_id,
            "durable_history": {
                "latest_selection_id": baseline,
                "latest_selection": latest_selection,
                "previous_selection": previous_selection,
                "first_history_publication": baseline is None,
            },
            "applied_migrations": list(applied),
            "pending_migration_versions": list(pending),
            "migration_policy": getattr(invocation, "migration_policy", None),
            "recovery": {
                "backup_protections": [item.to_mapping() for item in protections],
                "independently_held_backup_ids": list(independent_ids),
                "prune_backup_ids": list(prune_backup_ids),
                "fresh_backup_required": bool(pending),
            },
            "prune_backup_ids": prune_backup_ids,
            "scheduled_backup": {
                "observed_sha256": scheduled_backup_sha256,
                "timer_enabled": backup_timer_enabled,
                "timer_state": backup_timer_state,
                "effects": ("pause", "refresh-if-needed", "resume-if-enabled"),
            },
        }
    )
    return effects


def _scheduler_create_delta(authority: Mapping[str, object]) -> tuple[str, ...]:
    """Read the validated, explicitly planned create-only scheduler delta."""

    value = authority.get("scheduler_create")
    if (
        not isinstance(value, tuple)
        or value != tuple(sorted(set(value)))
        or any(type(item) is not str for item in value)
    ):
        raise OpsError(
            ExitStatus.SAFETY,
            "provision",
            "pre-convergence scheduler delta is invalid",
            changed=False,
            next_action="rerun provisioning after inspecting scheduler authority",
        )
    return value


def _with_scheduler_create_delta(
    inputs: ProvisioningInputs, authority: Mapping[str, object]
) -> ProvisioningInputs:
    delta = _scheduler_create_delta(authority)
    return replace(inputs, scheduler_create=frozenset(delta))


def _scheduler_authorized_inputs(
    inputs: ProvisioningInputs, authority: Mapping[str, object]
) -> ProvisioningInputs:
    try:
        return _with_scheduler_create_delta(inputs, authority)
    except ValueError:
        raise OpsError(
            ExitStatus.SAFETY,
            "authority-preflight",
            "pre-convergence scheduler creation authority is invalid",
            changed=False,
            next_action="inspect the existing scheduler resources before retrying",
        ) from None


def _projection_error() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "authority-preflight",
        "pre-convergence authority observation returned invalid projection evidence",
        changed=False,
        next_action="inspect the existing record and PostgreSQL authority before retrying",
    )


def _plan_protections(authority: Mapping[str, object]) -> tuple[BackupProtection, ...]:
    return tuple(BackupProtection.from_mapping(value) for value in authority["backup_protections"])


def _authority_mapping(value: object) -> dict[str, object] | None:
    """Normalize bounded, value-shaped discovery evidence for a plan."""

    if isinstance(value, Mapping):
        return _copy_authority(value)
    if is_dataclass(value) and not isinstance(value, type):
        projected = asdict(value)
        if isinstance(projected, dict):
            return _copy_authority(projected)
    return None


def _required_authority(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OpsError(
            ExitStatus.SAFETY,
            "authority-preflight",
            "pre-convergence authority observation returned missing evidence",
            changed=False,
            next_action="inspect the existing record and PostgreSQL authority before retrying",
        )
    _scheduler_create_delta(value)
    return value


def _required_resource_authority(value: object) -> dict[str, object]:
    authority = _authority_mapping(value)
    if authority is None:
        raise OpsError(
            ExitStatus.SAFETY,
            "host-preflight",
            "provision host observation returned missing resource evidence",
            changed=False,
            next_action="inspect the supported host resources before retrying",
        )
    return authority


def _copy_authority(value: Mapping[str, object]) -> dict[str, object]:
    """Copy nested bounded plan evidence without retaining mutable helpers."""

    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("authority evidence keys must be strings")
        if isinstance(item, Mapping):
            copied[key] = _copy_authority(item)
        elif isinstance(item, tuple):
            copied[key] = tuple(
                _copy_authority(member) if isinstance(member, Mapping) else member
                for member in item
            )
        elif isinstance(item, list):
            copied[key] = [
                _copy_authority(member) if isinstance(member, Mapping) else member
                for member in item
            ]
        else:
            copied[key] = item
    return copied


def _redacted_plan(plan: Mapping[str, object]) -> Mapping[str, object]:
    safe = redact(dict(plan))
    if not isinstance(safe, Mapping):  # pragma: no cover - defensive redaction boundary
        raise RuntimeError("redacted provisioning plan is invalid")
    return dict(safe)


def _present_plan(plan: Mapping[str, object]) -> None:
    print(
        render_human(
            WorkflowResult(
                command="provision",
                environment=str(plan.get("environment", "")),
                changed=False,
                stage="planned",
                facts={"plan": dict(plan)},
                next_action="confirm before host convergence mutates the target host",
            )
        )
    )


def _confirm(_plan: Mapping[str, object]) -> bool:
    return input("Apply this redacted provisioning plan? Type yes to continue: ").strip().lower() == "yes"


def _changed(result: object) -> bool:
    if isinstance(result, (ChangeSet, WorkflowResult)):
        return result.changed
    if isinstance(result, bool):
        return result
    raise TypeError("provisioning convergence must return ChangeSet, WorkflowResult, or bool")


def _aggregate_mutation_state(*states: object) -> str:
    """Retain proved provisioning consequences through a later genesis loss."""

    if any(state not in {"unchanged", "changed", "unknown"} for state in states):
        raise TypeError("provisioning mutation evidence is invalid")
    if "changed" in states:
        return "changed"
    if "unknown" in states:
        return "unknown"
    return "unchanged"


def _pre_release_failure(
    environment: str,
    error: OpsError,
    *,
    starting_state: Mapping[str, object] | None = None,
) -> WorkflowResult:
    return WorkflowResult(
        command="provision",
        environment=environment,
        changed=error.changed,
        stage="provisioning-incomplete",
        facts={
            "failed_boundary": error.stage,
            "release_started": False,
            **(
                {"starting_state": _copy_authority(starting_state)}
                if starting_state is not None
                else {}
            ),
        },
        next_action=error.next_action or "retry provisioning after correcting the reported boundary",
        exit_status=error.status,
        warnings=error.warnings,
    )


def _safe_result_facts(result: WorkflowResult) -> Mapping[str, object]:
    return dict(redact(dict(result.facts)))


def _close_result(remote: object, result: WorkflowResult) -> WorkflowResult:
    _close_remote(remote)
    return result


def _close_remote(remote: object) -> None:
    close = getattr(remote, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # A completed workflow result must retain the actual operation
            # outcome; transport cleanup cannot rewrite deployment evidence.
            pass


__all__ = ["ProvisionCapabilities", "provision"]
