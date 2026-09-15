"""Ordered clean-host provisioning assembled from existing capabilities.

This workflow intentionally owns sequencing and reporting only.  Host state,
credentials, release activation, and readiness stay owned by the focused
capabilities provided by the established deployment workflow.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
import os
import tempfile
from typing import Protocol

from ..releases.build import build_release
from ..config import EnvironmentConfig, load_environment
from ..errors import ExitStatus, OpsError
from ..host.acceptance import validate_provisionable_host
from ..releases.manifests import VerifiedArtifact, verify_artifact
from ..releases.artifacts import CleanInputs, DeploymentTarget, identify_clean_inputs, resolve_deploy_target
from ..output import WorkflowResult, redact, render_human
from ..provisioning import (
    ProvisioningInputs,
    converge_provisioning,
    validate_existing_authority,
)
from ..remote import ChangeSet, connect
from ..secrets import SecretConfig, decrypt_secrets, render_pgpass, render_runtime_environment
from ..services.caddy import CaddyPlan, build_caddy_plan
from ..services.postgresql import (
    build_postgresql_plan,
    render_role_password_input,
)
from .deploy import _confirm_downgrade, _downgrade_acknowledgment, deploy_first_release


AcceptanceSteps = tuple[str, ...]
_ACCEPTANCE_STEPS: AcceptanceSteps = (
    "create the initial administrator interactively",
    "sign in over HTTPS",
    "send and receive a Resend invitation",
    "create and use an API key",
    "verify a LiveView route remains connected",
    "copy a verified local backup off-host",
)


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
    resolve_artifact: Callable[[object], VerifiedArtifact]
    render_runtime_environment: Callable[[EnvironmentConfig, SecretConfig], bytes]
    render_pgpass: Callable[[EnvironmentConfig, SecretConfig], bytes]
    render_plan: Callable[[EnvironmentConfig, VerifiedArtifact], Mapping[str, object]]
    present_plan: Callable[[Mapping[str, object]], None]
    confirm: Callable[[Mapping[str, object]], bool]
    connect: Callable[[EnvironmentConfig], object]
    discover: ProvisionDiscovery
    render_role_password_input: Callable[[object, str], bytes]
    provisioning: Callable[[object, ProvisioningInputs], ChangeSet]
    caddy_plan: Callable[[EnvironmentConfig], CaddyPlan]
    genesis: Callable[..., WorkflowResult]
    preflight: Callable[[object, ProvisioningInputs], Mapping[str, object] | None] | None = None
    target_resolution: Callable[[object, EnvironmentConfig, object, CleanInputs | None], DeploymentTarget] | None = None


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
    artifact: VerifiedArtifact | None = None
    if cap.target_resolution is None:
        artifact = cap.resolve_artifact(invocation)
        _validate_artifact_target(config, artifact)
    elif getattr(invocation, "artifact", None) is None:
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
    inputs = ProvisioningInputs(
        config=config,
        caddy_plan=caddy_plan,
        runtime_environment=runtime_environment,
        pgpass=pgpass,
        role_password_input=role_password_input,
    )

    remote = cap.connect(config)
    try:
        while True:
            # Discovery is a complete immutable snapshot.  It must precede every
            # consequence below, including plan presentation, confirmation, and
            # package installation.
            discovery = cap.discover(remote, config, expected_caddyfile_sha256=expected_caddyfile_sha256)
            release_input = (
                cap.target_resolution(remote, config, invocation, clean_inputs)
                if cap.target_resolution is not None
                else artifact
            )
            assert release_input is not None
            _validate_artifact_target(config, release_input)
            authority = cap.preflight(remote, inputs) if cap.preflight is not None else None
            starting_state = _starting_state(authority, discovery, release_input)
            plan = _redacted_plan(cap.render_plan(config, release_input))
            downgrade_required, downgrade_evidence = _provision_downgrade_authority(
                remote, config, release_input
            )
            plan = {
                **plan,
                "requires_downgrade_acknowledgment": downgrade_required,
                "downgrade_baselines": _downgrade_plan_rows(downgrade_evidence),
            }
            plan = {**plan, "starting_state": starting_state}
            cap.present_plan(plan)
            if dry_run:
                return _close_result(remote, WorkflowResult(
                    command="provision",
                    environment=environment_name,
                    changed=False,
                    stage="planned",
                    facts={"plan": plan, "discovery": "validated"},
                    next_action="review the redacted plan and rerun without --dry-run only after confirmation",
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
                ))

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
                    ))

            # Confirmation authorizes only the observed resource authority. A
            # drifted snapshot is discarded and re-planned before pyinfra can
            # receive any managed-write request, including under --yes.
            refreshed_discovery = cap.discover(
                remote, config, expected_caddyfile_sha256=expected_caddyfile_sha256
            )
            refreshed_input = (
                cap.target_resolution(remote, config, invocation, clean_inputs)
                if cap.target_resolution is not None
                else artifact
            )
            assert refreshed_input is not None
            refreshed_authority = cap.preflight(remote, inputs) if cap.preflight is not None else None
            refreshed_downgrade_required, refreshed_downgrade_evidence = _provision_downgrade_authority(
                remote, config, refreshed_input
            )
            if (
                refreshed_discovery != discovery
                or refreshed_input != release_input
                or refreshed_authority != authority
                or refreshed_downgrade_required != downgrade_required
                or refreshed_downgrade_evidence != downgrade_evidence
            ):
                continue
            provisioning_changed = _changed(cap.provisioning(remote, inputs))
            break
    except OpsError as error:
        return _close_result(remote, _pre_release_failure(environment_name, error))
    except TypeError:
        return _close_result(remote, _pre_release_failure(
            environment_name,
            OpsError(
                ExitStatus.REMOTE_PREFLIGHT,
                "provisioning",
                "provisioning convergence returned invalid capability evidence",
                changed=False,
                next_action="inspect the provisioning boundary and retry",
            ),
        ))
    except BaseException:
        _close_remote(remote)
        raise

    try:
        release = cap.genesis(
            remote,
            config,
            release_input,
            migration_policy=getattr(invocation, "migration_policy", None),
            yes=yes,
            allow_downgrade=getattr(invocation, "allow_downgrade", False),
            dry_run=dry_run,
            starting_state=starting_state,
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
        resolve_artifact=_resolve_artifact,
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


def _resolve_artifact(invocation: object) -> VerifiedArtifact:
    supplied = getattr(invocation, "artifact", None)
    if supplied is not None:
        archive = Path(supplied)
        if not archive.name.endswith(".tar.gz"):
            raise OpsError(
                ExitStatus.LOCAL_PREREQUISITE,
                "artifact",
                "provision artifact must be a release archive",
                changed=False,
                next_action="supply one verified Taskman release archive or omit --artifact to build it",
            )
        stem = archive.name[: -len(".tar.gz")]
        return verify_artifact(
            archive,
            archive.with_name(f"{stem}.manifest.json"),
            archive.with_name(f"{archive.name}.sha256"),
        )

    repo = Path(__file__).resolve().parents[3]
    artifact_root = Path(tempfile.gettempdir()) / f"taskman-artifacts-{os.getuid()}"
    return build_release(repo, artifact_root)


def _validate_artifact_target(config: EnvironmentConfig, artifact: VerifiedArtifact | DeploymentTarget) -> None:
    manifest = artifact.manifest
    if manifest.target_os != config.target_os or manifest.architecture != config.architecture:
        raise OpsError(
            ExitStatus.LOCAL_PREREQUISITE,
            "artifact",
            "verified artifact does not target the configured supported host",
            changed=False,
            next_action="build or select an Ubuntu 26.04 amd64 Taskman artifact",
        )


def _render_plan(config: EnvironmentConfig, artifact: VerifiedArtifact | DeploymentTarget) -> Mapping[str, object]:
    return {
        "environment": config.name or "",
        "ssh_destination": f"{config.ssh_user}@{config.ssh_host}:{config.ssh_port}",
        "public_hostname": config.public_hostname,
        "candidate_release_id": artifact.manifest.release_id,
        "artifact_sha256": _artifact_sha256(artifact),
        "artifact_source": artifact.source if isinstance(artifact, DeploymentTarget) else "explicit",
        "source_dirty": artifact.source_dirty if isinstance(artifact, DeploymentTarget) else artifact.manifest.source_dirty,
        "services": ("PostgreSQL", "taskman.service", "taskman-backup.timer", "Caddy"),
        "planned_backup": "validated local PostgreSQL backup timer",
    }


def _release_id(value: VerifiedArtifact | DeploymentTarget) -> str:
    return value.manifest.release_id


def _artifact_sha256(value: VerifiedArtifact | DeploymentTarget) -> str:
    return value.artifact_sha256 if isinstance(value, DeploymentTarget) else value.sha256


def _provision_downgrade_authority(
    remote: object,
    config: EnvironmentConfig,
    target: VerifiedArtifact | DeploymentTarget,
) -> tuple[bool, tuple[tuple[str, str, tuple[str, ...]], ...]]:
    """Use deploy's complete baseline classifier for public genesis too."""

    if not isinstance(target, DeploymentTarget):
        # Legacy capability fakes intentionally provide only a verified
        # archive and have no paged host-record boundary.  Production's
        # default resolver always produces DeploymentTarget.
        return False, ()
    return _downgrade_acknowledgment(remote, config, target, _repository_root())


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
    authority: Mapping[str, object] | None,
    discovery: object,
    target: VerifiedArtifact | DeploymentTarget,
) -> dict[str, object]:
    """Preserve only the pre-convergence authority that was actually observed.

    Compatibility injectors used by narrow workflow tests predate the bounded
    projection and return ``None``.  Production always supplies the exact
    helper mapping; accepting ``None`` here avoids inventing facts for those
    focused boundaries while retaining their existing contract.
    """

    result: dict[str, object] = {
        "authority": "validated",
        "candidate_release_id": _release_id(target),
    }
    if isinstance(target, DeploymentTarget):
        result.update(
            artifact_sha256=target.artifact_sha256,
            artifact_source=target.source,
            source_dirty=target.source_dirty,
        )
    if isinstance(authority, Mapping):
        result["host_authority"] = dict(authority)
    if isinstance(discovery, Mapping):
        # Host admission contains only checked resource facts; it never
        # contains credentials.  Preserve it so convergence cannot silently
        # change the resource snapshot the operator confirmed.
        result["resource_authority"] = dict(discovery)
    return result


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
) -> WorkflowResult:
    return WorkflowResult(
        command="provision",
        environment=environment,
        changed=error.changed,
        stage="provisioning-incomplete",
        facts={
            "failed_boundary": error.stage,
            "release_started": False,
        },
        next_action=error.next_action or "retry provisioning after correcting the reported boundary",
        exit_status=error.status,
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
