"""Plan and translate final-protocol deployment convergence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..host_protocol import HostResult
from ..manifests import MigrationFingerprint, VerifiedArtifact
from ..output import WorkflowResult, redact, render_human
from ..remote import Remote
from ..releases.identifiers import validate_release_id
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
    """Present, confirm, and invoke one replayable host deployment."""

    if not isinstance(config, EnvironmentConfig) or not isinstance(artifact, VerifiedArtifact):
        raise TypeError("deployment requires validated configuration and artifact")
    if not isinstance(dry_run, bool) or not isinstance(manual_adoption_confirmed, bool):
        raise TypeError("deployment flags must be boolean")
    if migration_policy is not None and migration_policy not in _POLICIES:
        raise ValueError("deployment requires a valid migration policy")
    candidate = artifact.manifest.release_id
    try:
        if manual_adoption_confirmed:
            raise _safety("manual lifecycle adoption is not part of replayable deployment")
        previous, current_migrations, applied_versions = _planning_authority(remote, config)
        policy = migration_policy or (
            "no-change" if current_migrations == artifact.manifest.migrations else "backward-compatible"
        )
        _validate_migration_policy(current_migrations, artifact.manifest.migrations, policy)
        plan = _redacted_plan(_plan(config, artifact, previous, policy))
        if dry_run:
            return WorkflowResult(
                "deploy", config.name or "", False, "planned",
                {**plan, "previous_release_id": previous, "selected_release_id": previous},
                next_action="review the redacted deployment plan and rerun without --dry-run only after confirmation",
            )
        (present_plan or _present_plan)(plan)
        if not (confirm or _confirm)(plan):
            return WorkflowResult(
                "deploy", config.name or "", False, "confirmation-cancelled",
                {
                    **plan,
                    "previous_release_id": previous,
                    "selected_release_id": previous,
                    "database_state": "unchanged",
                    "service_state": "unknown",
                },
                next_action="review the exact deployment plan and confirm a later run when ready",
            )
        result = run_deployment_request(
            remote,
            config,
            artifact,
            migration_policy=policy,
            previous_release_id=previous,
            applied_migrations=applied_versions,
        )
        if result.outcome != "succeeded":
            raise result_error(result)
        return _payload_result(config, candidate, policy, result, previous_release_id=previous)
    except OpsError as error:
        return _failure_result(config, error, candidate=candidate)


def deploy_first_release(remote: Remote, config: EnvironmentConfig, artifact: VerifiedArtifact) -> WorkflowResult:
    """Enter the same helper procedure with the first-release precondition."""

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
            applied_migrations=(),
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
    remote: Remote,
    config: EnvironmentConfig,
) -> tuple[str, tuple[MigrationFingerprint, ...], tuple[int, ...]]:
    """Read only the completed selection and its observed schema authority."""

    result = run_request(remote, helper_request("discover", config))
    if result.outcome != "succeeded" or not isinstance(result.state, Mapping):
        raise _safety("deployment planning helper refused host state")
    state = _mutable_protocol_value(result.state)
    if not isinstance(state, Mapping):
        raise _safety("deployment planning helper returned invalid host state")
    try:
        previous = validate_release_id(state["selected_release_id"])
        releases = state["releases"]
        if not isinstance(releases, list):
            raise ValueError
        release = next(
            item
            for item in releases
            if isinstance(item, Mapping) and item.get("release_id") == previous
        )
        migrations = _migration_fingerprints(release.get("migrations"))
        applied = _migration_versions(state["applied_migrations"])
    except (KeyError, StopIteration, TypeError, ValueError):
        raise _safety("deployment planning helper returned incomplete selection authority") from None
    return previous, migrations, applied


def _payload_result(
    config: EnvironmentConfig,
    candidate: str,
    policy: str,
    result: HostResult,
    *,
    previous_release_id: str | None,
    genesis: bool = False,
) -> WorkflowResult:
    facts = _facts(result.state, candidate, policy, previous_release_id=previous_release_id, genesis=genesis)
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
    state: Mapping[str, object],
    candidate: str,
    policy: str,
    *,
    previous_release_id: str | None,
    genesis: bool,
) -> dict[str, object]:
    required = {"changed", "selected_release_id", "backup_id", "database_state", "service_state", "report"}
    if not isinstance(state, Mapping) or not required <= set(state):
        raise _safety("deployment helper returned incomplete success evidence")
    changed, backup_id, database = state["changed"], state["backup_id"], state["database_state"]
    if (
        type(changed) is not bool
        or state["selected_release_id"] != candidate
        or state["service_state"] != "running"
        or database not in {"changed", "unchanged"}
        or (changed and database == "changed" and (type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None))
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
        "selected_release_id": candidate,
        "backup_id": backup_id,
        "migration_policy": policy,
        "database_changed": database == "changed",
        "database_state": database,
        "service_state": "running",
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
    }.get(error.status, f"{error.stage}-failed")
    return WorkflowResult(
        "deploy", config.name or "", error.changed, stage,
        {
            "previous_release_id": None if genesis else state.get("selected_release_id"),
            "candidate_release_id": candidate,
            "selected_release_id": state.get("selected_release_id"),
            "backup_id": state.get("backup_id"),
            "database_state": state.get("database_state", "unknown"),
            "service_state": state.get("service_state", "unknown"),
            "failure_boundary": state.get("failed_boundary", error.stage),
            "verification": state.get("report"),
        },
        tuple(getattr(error, "warnings", ())),
        error.next_action or "inspect the observed host state and rerun when it is safe",
        error.status,
    )


def _plan(config: EnvironmentConfig, artifact: VerifiedArtifact, previous: str, policy: str) -> dict[str, object]:
    return {
        "environment": config.name or "",
        "ssh_destination": f"{config.ssh_user}@{config.ssh_host}:{config.ssh_port}",
        "public_hostname": config.public_hostname,
        "current_release_id": previous,
        "candidate_release_id": artifact.manifest.release_id,
        "source_revision": artifact.manifest.source_revision,
        "artifact_sha256": artifact.sha256,
        "migration_policy": policy,
        "planned_backup": policy != "no-change",
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


def _migration_versions(value: object) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or any(type(item) is not int or item < 0 for item in value):
        raise ValueError("invalid migration versions")
    versions = tuple(value)
    if versions != tuple(sorted(set(versions))):
        raise ValueError("invalid migration versions")
    return versions


def _validate_migration_policy(
    current: tuple[MigrationFingerprint, ...],
    candidate: tuple[MigrationFingerprint, ...],
    policy: str,
) -> None:
    if current == candidate and policy == "no-change":
        return
    if current != candidate and policy in {"backward-compatible", "restore-required"}:
        return
    raise _safety("confirmed migration policy does not match completed release authority")


def _mutable_protocol_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _mutable_protocol_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_protocol_value(item) for item in value]
    return value


__all__ = ["deploy", "deploy_first_release"]
