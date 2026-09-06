"""Controller plan, confirmation, and translation for helper deployments."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import temporary_helper_package
from ..helper_runner import invoke_helper, new_operation_id
from ..host_protocol import HostRequest
from ..manifests import MigrationFingerprint, VerifiedArtifact
from ..output import WorkflowResult, redact, render_human
from ..remote import Remote
from ..releases.identifiers import validate_release_id
from ..host_helper.lifecycle import ManualAdoptionCandidate
from .helper_deploy import run_helper_deployment


_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required"})


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
        payload = run_helper_deployment(
            remote, config, artifact, migration_policy=policy, previous_release_id=previous,
            current_migrations=current_migrations,
            manual_adoption=None if manual is None else manual.to_mapping(),
        )
        return _payload_result(config, candidate, policy, payload)
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
        payload = run_helper_deployment(
            remote,
            config,
            artifact,
            migration_policy=policy,
            previous_release_id=None,
            current_migrations=(),
            genesis=True,
        )
        return _payload_result(config, artifact.manifest.release_id, policy, payload, genesis=True)
    except OpsError as error:
        return _failure_result(config, error, candidate=artifact.manifest.release_id, genesis=True)


def _planning_authority(
    remote: Remote, config: EnvironmentConfig, manual_confirmed: bool
) -> tuple[str, ManualAdoptionCandidate | None, tuple[MigrationFingerprint, ...]]:
    """Read plan authority through the helper, never a controller remote snapshot."""

    request = HostRequest(
        protocol_version=1, operation="discover", operation_id=new_operation_id(), expected_state={},
        paths={"install_root": config.install_root.as_posix(), "backup_root": config.backup_root.as_posix()},
        parameters={"include_manual_adoption": True} if manual_confirmed else {},
    )
    with temporary_helper_package() as package:
        invocation = invoke_helper(remote, package, request)
    result = invocation.result
    if (result.protocol_version, result.operation, result.operation_id) != (1, "discover", request.operation_id):
        raise _safety("deployment planning helper returned unrelated lifecycle evidence")
    if result.stage == "lifecycle-lock":
        raise OpsError(ExitStatus.LOCKED, "lifecycle-lock", "deployment planning lifecycle lock is held", changed=False, next_action="wait for the lifecycle operation to finish and retry")
    if result.outcome != "succeeded" or result.stage != "discovered" or not isinstance(result.lifecycle, Mapping):
        raise _safety("deployment planning helper refused lifecycle authority")
    lifecycle = _mutable_protocol_value(result.lifecycle)
    if not isinstance(lifecycle, Mapping):
        raise _safety("deployment planning helper returned invalid lifecycle evidence")
    state, records = lifecycle.get("state"), lifecycle.get("records")
    if state == "manual":
        if not manual_confirmed:
            raise _safety("manual current release requires explicit adoption confirmation before deployment")
        try:
            authority = ManualAdoptionCandidate.from_mapping(lifecycle.get("manual_adoption"))
        except (TypeError, ValueError):
            raise _safety("deployment planning helper returned invalid manual-adoption authority") from None
        return authority.release_id, authority, _migration_fingerprints(authority.migrations)
    if manual_confirmed:
        raise _safety("manual-adoption confirmation is only valid for a manual current release")
    if state != "managed" or not isinstance(records, Mapping) or not isinstance(records.get("activations"), list) or not records["activations"]:
        raise _safety("no managed current release is recorded")
    current = records["activations"][-1]
    try:
        previous = validate_release_id(current.get("candidate_release_id") if isinstance(current, Mapping) else None)
        migrations = _migration_fingerprints(lifecycle.get("current_migrations"))
        return previous, None, migrations
    except (TypeError, ValueError):
        raise _safety("deployment planning helper returned invalid current release") from None


def _payload_result(config: EnvironmentConfig, candidate: str, policy: str, payload: Mapping[str, object], *, genesis: bool = False) -> WorkflowResult:
    if payload.get("stage") == "already-current":
        return WorkflowResult(
            "deploy", config.name or "", False, "already-current",
            _facts(payload, candidate, policy, changed=False, genesis=genesis), tuple(payload.get("warnings", ())),
            "no deployment action is required",
        )
    return WorkflowResult(
        "deploy", config.name or "", True, "deployed",
        _facts(payload, candidate, policy, changed=True, genesis=genesis), tuple(payload.get("warnings", ())),
        "perform the remaining browser, email, and API acceptance checks",
    )


def _facts(payload: Mapping[str, object], candidate: str, policy: str, *, changed: bool, genesis: bool) -> dict[str, object]:
    database = payload.get("database_state", "unknown")
    return {
        "previous_release_id": None if genesis else payload.get("previous_release_id"),
        "candidate_release_id": candidate,
        "selected_release_id": payload.get("selected_release_id"),
        "backup_id": payload.get("backup_id"),
        "migration_policy": policy,
        "database_changed": database == "changed",
        "database_state": database,
        "activation_recorded": payload.get("activation_recorded", False),
        "service_state": payload.get("service_state", "unknown"),
        "changed_stages": tuple(payload.get("changed_stages", ())),
        "recovery_commands": tuple(payload.get("recovery_commands", ())),
        "residue_paths": tuple(payload.get("residue_paths", ())),
        "verification": payload.get("verification", {}),
    }


def _failure_result(config: EnvironmentConfig, error: OpsError, *, candidate: str, genesis: bool = False) -> WorkflowResult:
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
            "previous_release_id": None if genesis else getattr(error, "previous_release_id", None),
            "candidate_release_id": candidate,
            "selected_release_id": getattr(error, "selected_release_id", None),
            "backup_id": getattr(error, "backup_id", None),
            "database_changed": "unknown", "database_state": getattr(error, "database_state", "unknown"),
            "activation_recorded": getattr(error, "activation_recorded", False),
            "service_state": getattr(error, "service_state", "unknown"), "failure_stage": error.stage,
            "changed_stages": tuple(getattr(error, "changed_stages", ())),
            "recovery_commands": tuple(getattr(error, "recovery_commands", ())),
            "residue_paths": tuple(getattr(error, "residue_paths", ())),
            "verification": getattr(error, "verification", None),
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
