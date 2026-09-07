"""Controller plan, confirmation, and translation for helper deployments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..manifests import MigrationFingerprint, VerifiedArtifact
from ..output import WorkflowResult, redact, render_human
from ..remote import Remote
from ..releases.identifiers import validate_release_id
from ..host_helper.lifecycle import ManualAdoptionCandidate
from .helper import (
    request as helper_request,
    result_error,
    run_deployment_request,
    run_request,
    successful_verification,
)


_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required"})
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")


def deploy(
    remote: Remote,
    config: EnvironmentConfig,
    artifact: VerifiedArtifact,
    *,
    migration_policy: str | None = None,
    manual_adoption_confirmed: bool = False,
    present_plan: Callable[[Mapping[str, object]], None] | None = None,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Present one redacted plan then delegate all host mutation to the helper."""

    if not isinstance(config, EnvironmentConfig) or not isinstance(artifact, VerifiedArtifact):
        raise TypeError("deployment requires validated configuration and artifact")
    if not isinstance(dry_run, bool) or not isinstance(manual_adoption_confirmed, bool):
        raise TypeError("deployment flags must be boolean")
    if migration_policy is not None and migration_policy not in _POLICIES:
        raise ValueError("deployment requires a valid migration policy")
    candidate = artifact.manifest.release_id
    try:
        previous, manual, current_migrations = _planning_authority(remote, config, manual_adoption_confirmed)
        policy = migration_policy or (
            "no-change"
            if current_migrations == artifact.manifest.migrations
            else "backward-compatible"
        )
        _validate_migration_policy(current_migrations, artifact.manifest.migrations, policy)
        plan = _redacted_plan(_plan(config, artifact, previous, policy))
        if dry_run:
            return WorkflowResult("deploy", config.name or "", False, "planned", {**plan, "previous_release_id": previous, "selected_release_id": previous}, next_action="review the redacted deployment plan and rerun without --dry-run only after confirmation")
        (present_plan or _present_plan)(plan)
        if not (confirm or _confirm)(plan):
            return WorkflowResult("deploy", config.name or "", False, "confirmation-cancelled", {**plan, "previous_release_id": previous, "selected_release_id": previous, "database_state": "unchanged", "service_state": "unknown"}, next_action="review the exact deployment plan and confirm a later run when ready")
        result = run_deployment_request(
            remote, config, artifact, migration_policy=policy, previous_release_id=previous,
            current_migrations=current_migrations,
            manual_adoption=None if manual is None else manual.to_mapping(),
        )
        if result.outcome != "succeeded":
            raise result_error(result)
        return _payload_result(
            config,
            candidate,
            policy,
            result,
            previous_release_id=previous,
        )
    except OpsError as error:
        return _failure_result(config, error, candidate=candidate)


def deploy_first_release(
    remote: Remote,
    config: EnvironmentConfig,
    artifact: VerifiedArtifact,
) -> WorkflowResult:
    """Invoke explicit helper genesis; it owns clean-host state validation."""

    if not isinstance(config, EnvironmentConfig) or not isinstance(artifact, VerifiedArtifact):
        raise TypeError("first release requires validated configuration and artifact")
    policy = "no-change" if not artifact.manifest.migrations else "restore-required"
    try:
        result = run_deployment_request(
            remote,
            config,
            artifact,
            migration_policy=policy,
            previous_release_id=None,
            current_migrations=(),
            genesis=True,
        )
        if result.outcome != "succeeded":
            raise result_error(result)
        return _payload_result(
            config,
            artifact.manifest.release_id,
            policy,
            result,
            previous_release_id=None,
            genesis=True,
        )
    except OpsError as error:
        return _failure_result(config, error, candidate=artifact.manifest.release_id, genesis=True)


def _planning_authority(
    remote: Remote, config: EnvironmentConfig, manual_confirmed: bool
) -> tuple[str, ManualAdoptionCandidate | None, tuple[MigrationFingerprint, ...]]:
    """Read plan authority through the helper, never a controller remote snapshot."""

    result = run_request(
        remote,
        helper_request(
            "discover",
            config,
            parameters={"include_manual_adoption": True} if manual_confirmed else {},
        ),
    )
    if result.outcome != "succeeded" or not isinstance(result.state, Mapping):
        raise _safety("deployment planning helper refused lifecycle authority")
    state = _mutable_protocol_value(result.state)
    if not isinstance(state, Mapping):
        raise _safety("deployment planning helper returned invalid lifecycle evidence")
    host_kind = state.get("host_kind")
    if host_kind == "manual":
        if not manual_confirmed:
            raise _safety("manual current release requires explicit adoption confirmation before deployment")
        try:
            authority = ManualAdoptionCandidate.from_mapping(state.get("manual_adoption"))
        except (TypeError, ValueError):
            raise _safety("deployment planning helper returned invalid manual-adoption authority") from None
        return authority.release_id, authority, _migration_fingerprints(authority.migrations)
    if manual_confirmed:
        raise _safety("manual-adoption confirmation is only valid for a manual current release")
    activations = state.get("activations")
    if host_kind != "managed" or not isinstance(activations, list) or not activations:
        raise _safety("no managed current release is recorded")
    current = activations[-1]
    try:
        previous = validate_release_id(current.get("candidate_release_id") if isinstance(current, Mapping) else None)
        migrations = _migration_fingerprints(state.get("applied_migrations"))
        return previous, None, migrations
    except (TypeError, ValueError):
        raise _safety("deployment planning helper returned invalid current release") from None


def _payload_result(
    config: EnvironmentConfig,
    candidate: str,
    policy: str,
    result: object,
    *,
    previous_release_id: str | None,
    genesis: bool = False,
) -> WorkflowResult:
    facts = _facts(
        result.state,
        candidate,
        policy,
        previous_release_id=previous_release_id,
        genesis=genesis,
    )
    changed = facts["changed"]
    assert isinstance(changed, bool)
    if not changed:
        return WorkflowResult(
            "deploy", config.name or "", False, "already-current",
            facts, tuple(result.warnings),
            "no deployment action is required",
        )
    return WorkflowResult(
        "deploy", config.name or "", True, "deployed",
        facts, tuple(result.warnings),
        "perform the remaining browser, email, and API acceptance checks",
    )


def _facts(
    state: Mapping[str, object],
    candidate: str,
    policy: str,
    *,
    previous_release_id: str | None,
    genesis: bool,
) -> dict[str, object]:
    required = {
        "changed",
        "selected_release_id",
        "backup_id",
        "database_state",
        "activation_recorded",
        "service_state",
        "report",
    }
    if not isinstance(state, Mapping) or not required <= set(state):
        raise _safety("deployment helper returned incomplete success evidence")
    changed = state["changed"]
    backup_id = state["backup_id"]
    database = state["database_state"]
    if (
        type(changed) is not bool
        or state["selected_release_id"] != candidate
        or state["activation_recorded"] is not True
        or state["service_state"] != "active"
        or database not in {"changed", "unchanged"}
        or (changed and (type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None))
        or (not changed and backup_id is not None)
    ):
        raise _safety("deployment helper returned invalid success evidence")
    try:
        verification = successful_verification(state["report"], candidate)
    except ValueError:
        raise _safety("deployment helper returned invalid success evidence") from None
    return {
        "changed": changed,
        "previous_release_id": None if genesis else previous_release_id,
        "candidate_release_id": candidate,
        "selected_release_id": state.get("selected_release_id"),
        "backup_id": backup_id,
        "migration_policy": policy,
        "database_changed": database == "changed",
        "database_state": database,
        "activation_recorded": state.get("activation_recorded", False),
        "service_state": state.get("service_state", "unknown"),
        "verification": verification,
    }


def _failure_result(config: EnvironmentConfig, error: OpsError, *, candidate: str, genesis: bool = False) -> WorkflowResult:
    state = getattr(error, "state", {})
    if not isinstance(state, Mapping):
        state = {}
    stage = {
        ExitStatus.BACKUP: "backup-failed", ExitStatus.MIGRATION: "migration-failed",
        ExitStatus.RELEASE: "activation-failed", ExitStatus.READINESS: "verification-failed",
        ExitStatus.SAFETY: "safety-refused", ExitStatus.LOCKED: "lock-contended",
    }.get(error.status, f"{error.stage}-failed")
    if error.stage == "staging":
        stage = "staging-failed"
    return WorkflowResult(
        "deploy", config.name or "", error.changed, stage,
        {
            "previous_release_id": None if genesis else state.get("previous_release_id"),
            "candidate_release_id": candidate,
            "selected_release_id": state.get("selected_release_id"),
            "backup_id": state.get("backup_id"),
            "database_changed": "unknown", "database_state": state.get("database_state", "unknown"),
            "activation_recorded": state.get("activation_recorded", False),
            "service_state": state.get("service_state", "unknown"), "failure_stage": state.get("failed_boundary", error.stage),
            "verification": state.get("report"),
        }, tuple(getattr(error, "warnings", ())),
        error.next_action or "inspect helper deployment evidence before retrying", error.status,
    )


def _plan(config: EnvironmentConfig, artifact: VerifiedArtifact, previous: str, policy: str) -> dict[str, object]:
    return {
        "environment": config.name or "", "ssh_destination": f"{config.ssh_user}@{config.ssh_host}:{config.ssh_port}",
        "public_hostname": config.public_hostname, "current_release_id": previous,
        "candidate_release_id": artifact.manifest.release_id, "source_revision": artifact.manifest.source_revision,
        "artifact_sha256": artifact.sha256, "migration_policy": policy, "planned_backup": True,
        "services_affected": ("taskman.service",), "expected_maintenance_window": "brief Taskman service interruption after backup",
    }


def _redacted_plan(plan: Mapping[str, object]) -> dict[str, object]:
    safe = redact(dict(plan))
    if not isinstance(safe, Mapping):
        raise RuntimeError("redacted deployment plan is invalid")
    return dict(safe)


def _present_plan(plan: Mapping[str, object]) -> None:
    print(render_human(WorkflowResult("deploy", str(plan.get("environment", "")), False, "planned", {"plan": dict(plan)}, next_action="confirm before starting helper deployment")))


def _confirm(_plan: Mapping[str, object]) -> bool:
    return input("Apply this redacted deployment plan? Type yes to continue: ").strip().lower() == "yes"


def _safety(message: str) -> OpsError:
    return OpsError(ExitStatus.SAFETY, "deploy", message, changed=False, next_action="inspect managed lifecycle records before retrying")


def _migration_fingerprints(value: object) -> tuple[MigrationFingerprint, ...]:
    """Decode helper-derived protocol migrations for the mutation request."""

    if not isinstance(value, (list, tuple)):
        raise _safety("deployment planning helper returned invalid migration fingerprints")
    try:
        values = tuple(MigrationFingerprint.from_mapping(item) for item in value)
    except ValueError:
        raise _safety("deployment planning helper returned invalid migration fingerprints") from None
    if tuple(item.filename for item in values) != tuple(sorted(item.filename for item in values)) or len(
        {item.filename for item in values}
    ) != len(values):
        raise _safety("deployment planning helper returned invalid migration fingerprints")
    return values


def _validate_migration_policy(
    current: tuple[MigrationFingerprint, ...],
    candidate: tuple[MigrationFingerprint, ...],
    policy: str,
) -> None:
    if current == candidate and policy == "no-change":
        return
    if current != candidate and policy in {"backward-compatible", "restore-required"}:
        return
    raise _safety("confirmed migration policy does not match helper-derived fingerprints")


def _mutable_protocol_value(value: object) -> object:
    """Convert frozen protocol JSON containers at the controller boundary.

    The shared protocol uses tuples and read-only mappings after decoding.  The
    remaining controller record validators deliberately consume ordinary JSON
    lists, so this narrow conversion preserves the wire shape without making
    controller-side host state authoritative again.
    """

    if isinstance(value, Mapping):
        return {key: _mutable_protocol_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_protocol_value(item) for item in value]
    return value


__all__ = ["deploy", "deploy_first_release"]
