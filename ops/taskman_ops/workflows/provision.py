"""Ordered clean-host provisioning assembled from existing capabilities.

This workflow intentionally owns sequencing and reporting only.  Host state,
credentials, release activation, and readiness stay owned by the focused
capabilities introduced by the earlier deployment tasks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path
import os
import tempfile
from typing import Protocol

from ..build import build_release
from ..config import EnvironmentConfig, load_environment
from ..errors import ExitStatus, OpsError
from ..host.baseline import converge_baseline_host
from ..host.facts import validate_provisionable_host
from ..host.firewall import apply_firewall
from ..manifests import VerifiedArtifact, verify_artifact
from ..output import WorkflowResult, redact, render_human
from ..remote import ChangeSet, connect
from ..secrets import SecretConfig, decrypt_secrets, render_pgpass, render_runtime_environment
from ..services.caddy import CaddyPlan, apply_caddy_install, build_caddy_plan
from ..services.postgresql import (
    apply_postgresql_native_configuration,
    build_postgresql_plan,
    converge_database,
)
from ..services.systemd import apply_systemd_assets, build_systemd_plan, install_runtime_environment
from .deploy import deploy_first_release
from .verify import run_verify


AcceptanceSteps = tuple[str, ...]
_ACCEPTANCE_STEPS: AcceptanceSteps = (
    "create the initial administrator interactively",
    "sign in over HTTPS",
    "send and receive a Resend invitation",
    "create and use an API key",
    "verify a LiveView route remains connected",
    "copy a verified local backup off-host",
)


class DatabaseConvergence(Protocol):
    """The public database capability's keyword-only secret boundary."""

    def __call__(
        self,
        remote: object,
        plan: object,
        *,
        password: str,
        pgpass: bytes,
    ) -> object: ...


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
    baseline: Callable[[object, EnvironmentConfig], object]
    firewall: Callable[[object, EnvironmentConfig], object]
    postgresql_plan: Callable[[EnvironmentConfig], object]
    postgresql_native: Callable[[object, object], object]
    database: DatabaseConvergence
    install_runtime_environment: Callable[[object, bytes], object]
    systemd_plan: Callable[[EnvironmentConfig], object]
    systemd: Callable[[object, object], object]
    caddy_plan: Callable[[EnvironmentConfig], CaddyPlan]
    caddy: Callable[[object, CaddyPlan], object]
    release_transaction: Callable[[object, EnvironmentConfig, VerifiedArtifact], WorkflowResult]
    verify: Callable[[object, EnvironmentConfig, str], WorkflowResult]


def provision(
    invocation: object,
    *,
    capabilities: ProvisionCapabilities | None = None,
) -> WorkflowResult:
    """Converge one supported clean host without expanding lower-level ownership.

    All local validation, artifact construction, secret validation, and plan
    presentation happen before either confirmation or SSH.  A release result
    is deliberately returned unchanged once its transaction has begun: the
    deployment transaction is the authoritative owner of staging, backup,
    activation, and migration failure semantics.
    """

    environment_name = _environment_name(invocation)
    dry_run = _dry_run(invocation)
    cap = capabilities or _default_capabilities()

    config = cap.load_environment(environment_name)
    secrets = cap.decrypt_secrets(environment_name)
    artifact = cap.resolve_artifact(invocation)
    _validate_artifact_target(config, artifact)
    runtime_environment = cap.render_runtime_environment(config, secrets)
    pgpass = cap.render_pgpass(config, secrets)
    # These pure capability constructors perform local template and native
    # validator checks.  Build them before the plan/confirmation boundary so
    # no controller prerequisite can fail after host convergence begins.
    database_plan = cap.postgresql_plan(config)
    systemd_plan = cap.systemd_plan(config)
    caddy_plan = cap.caddy_plan(config)
    expected_caddyfile_sha256 = _caddyfile_sha256(caddy_plan)
    plan = _redacted_plan(cap.render_plan(config, artifact))
    cap.present_plan(plan)

    if not dry_run and not cap.confirm(plan):
        return WorkflowResult(
            command="provision",
            environment=environment_name,
            changed=False,
            stage="confirmation-cancelled",
            facts={"plan": plan},
            next_action="review the redacted plan and confirm a later provisioning run when ready",
            exit_status=ExitStatus.SAFETY,
        )

    remote = cap.connect(config)
    completed: list[str] = []
    try:
        # Discovery is a complete immutable snapshot.  It must precede every
        # convergence call below, including package installation.
        cap.discover(remote, config, expected_caddyfile_sha256=expected_caddyfile_sha256)
        if dry_run:
            return _close_result(remote, WorkflowResult(
                command="provision",
                environment=environment_name,
                changed=False,
                stage="planned",
                facts={"plan": plan, "discovery": "validated"},
                next_action="review the redacted plan and rerun without --dry-run only after confirmation",
            ))

        _record_convergence(completed, "baseline", lambda: cap.baseline(remote, config))
        # apply_firewall owns the mandatory new strict SSH connection itself.
        _record_convergence(completed, "firewall", lambda: cap.firewall(remote, config))

        _record_convergence(
            completed,
            "postgresql",
            lambda: cap.postgresql_native(remote, database_plan),
        )
        _record_convergence(
            completed,
            "database",
            lambda: cap.database(
                remote,
                database_plan,
                password=secrets.database_password,
                pgpass=pgpass,
            ),
        )
        _record_convergence(
            completed,
            "runtime-environment",
            lambda: cap.install_runtime_environment(remote, runtime_environment),
        )

        # The systemd capability installs and enables the validated local
        # backup timer as well as Taskman's unit, so it remains before Caddy
        # and before the first release transaction.
        _record_convergence(
            completed,
            "backups-and-systemd",
            lambda: cap.systemd(remote, systemd_plan),
        )
        _record_convergence(completed, "caddy", lambda: cap.caddy(remote, caddy_plan))
    except OpsError as error:
        return _close_result(remote, _pre_release_failure(environment_name, completed, error))
    except BaseException:
        _close_remote(remote)
        raise

    try:
        release = cap.release_transaction(remote, config, artifact)
    except BaseException:
        _close_remote(remote)
        raise

    if release.exit_status is not ExitStatus.OK:
        return _close_result(remote, release)
    _record_change(completed, "release", release)

    try:
        verification = cap.verify(remote, config, artifact.manifest.release_id)
    except BaseException:
        _close_remote(remote)
        raise
    if verification.exit_status is not ExitStatus.OK:
        return _close_result(remote, WorkflowResult(
            command="provision",
            environment=environment_name,
            changed=bool(completed),
            stage="verification-failed",
            facts={
                "converged_stages": tuple(completed),
                "release": _safe_result_facts(release),
                "verification": _safe_result_facts(verification),
            },
            next_action=verification.next_action
            or "inspect the selected release and verification evidence before retrying",
            exit_status=verification.exit_status,
        ))

    return _close_result(remote, WorkflowResult(
        command="provision",
        environment=environment_name,
        changed=bool(completed),
        stage="provisioned" if completed else "already-provisioned",
        facts={
            "candidate_release_id": artifact.manifest.release_id,
            "converged_stages": tuple(completed),
            "release": _safe_result_facts(release),
            "verification": _safe_result_facts(verification),
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


def _caddyfile_sha256(plan: CaddyPlan) -> str:
    """Bind later remote Caddy ownership checks to the pre-confirmed plan bytes."""

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
        baseline=converge_baseline_host,
        firewall=apply_firewall,
        postgresql_plan=build_postgresql_plan,
        postgresql_native=apply_postgresql_native_configuration,
        database=converge_database,
        install_runtime_environment=install_runtime_environment,
        systemd_plan=build_systemd_plan,
        systemd=apply_systemd_assets,
        caddy_plan=build_caddy_plan,
        caddy=apply_caddy_install,
        release_transaction=deploy_first_release,
        verify=lambda remote, config, release_id: run_verify(remote, config, release_id),
    )


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


def _validate_artifact_target(config: EnvironmentConfig, artifact: VerifiedArtifact) -> None:
    manifest = artifact.manifest
    if manifest.target_os != config.target_os or manifest.architecture != config.architecture:
        raise OpsError(
            ExitStatus.LOCAL_PREREQUISITE,
            "artifact",
            "verified artifact does not target the configured supported host",
            changed=False,
            next_action="build or select an Ubuntu 26.04 amd64 Taskman artifact",
        )


def _render_plan(config: EnvironmentConfig, artifact: VerifiedArtifact) -> Mapping[str, object]:
    return {
        "environment": config.name or "",
        "ssh_destination": f"{config.ssh_user}@{config.ssh_host}:{config.ssh_port}",
        "public_hostname": config.public_hostname,
        "candidate_release_id": artifact.manifest.release_id,
        "artifact_sha256": artifact.sha256,
        "services": ("PostgreSQL", "taskman.service", "taskman-backup.timer", "Caddy"),
        "planned_backup": "validated local PostgreSQL backup timer",
    }


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
                next_action="confirm before connecting to the target host",
            )
        )
    )


def _confirm(_plan: Mapping[str, object]) -> bool:
    return input("Apply this redacted provisioning plan? Type yes to continue: ").strip().lower() == "yes"


def _record_change(completed: list[str], stage: str, result: object) -> None:
    changed = result.changed if isinstance(result, ChangeSet) else result.changed if isinstance(result, WorkflowResult) else result
    if changed is True:
        completed.append(stage)
    elif changed is not False:
        raise TypeError(f"{stage} convergence must return ChangeSet, WorkflowResult, or bool")


def _record_convergence(
    completed: list[str],
    stage: str,
    converge: Callable[[], object],
) -> None:
    """Turn an invalid pre-release boundary into a safe rerunnable result."""

    try:
        _record_change(completed, stage, converge())
    except TypeError as error:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            stage,
            f"{stage} convergence returned invalid capability evidence",
            changed=False,
            next_action="inspect the failed convergent stage and retry after correcting its capability boundary",
        ) from error


def _pre_release_failure(
    environment: str,
    completed: list[str],
    error: OpsError,
) -> WorkflowResult:
    return WorkflowResult(
        command="provision",
        environment=environment,
        changed=bool(completed) or error.changed,
        stage="partial-convergence",
        facts={
            "converged_stages": tuple(completed),
            "failed_stage": error.stage,
            "release_transaction_started": False,
        },
        next_action=error.next_action
        or "retry provisioning after inspecting the failed convergent stage; completed managed state is retained",
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
