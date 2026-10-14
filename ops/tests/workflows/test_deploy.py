"""Public deployment controller results and consent boundaries."""

from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime
import json

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import OTP_VERSION, MigrationFingerprint
from tests.support.environments import valid_environment
from tests.workflows.support import CANDIDATE, deployment_artifact, successful_verification_report
from taskman_ops.workflows.deploy import (
    _confirmed_expected_state as observed_expected_state,
    _downgrade_acknowledgment as observed_downgrade_authority,
)


CURRENT = build_release_id(
    "0.2.0", "a" * 40, artifact_sha256="a" * 64, source_dirty=False, otp_version=OTP_VERSION
)
_EXPECTED = {
    "selected_release_id": CURRENT,
    "last_successful_selection_id": "selection-" + "a" * 64 + ".json",
    "applied_migrations": (),
    "backup_protection_sha256": "b" * 64,
    "scheduled_backup_sha256": "c" * 64,
    "backup_timer_enabled": True,
    "downgrade_baseline_sha256": "d" * 64,
}


def config() -> EnvironmentConfig:
    values = valid_environment()
    values["ssh_port"] = 22
    return EnvironmentConfig.model_validate(values)


@pytest.fixture(autouse=True)
def _valid_operational_preflights(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("taskman_ops.workflows.deploy.validate_operational_preflight", lambda *_args: None)


@pytest.fixture(autouse=True)
def _confirmed_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("taskman_ops.workflows.deploy._confirmed_expected_state", lambda *_args: _EXPECTED)
    monkeypatch.setattr("taskman_ops.workflows.deploy._downgrade_acknowledgment", lambda *_args, **_kwargs: (False, ()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planned_prune_backup_ids",
        lambda *_args, **_kwargs: ((), {"protections": (), "independent_backup_ids": frozenset()}),
    )


def _success(*, mutation_state: str = "changed") -> HostResult:
    return HostResult(
        PROTOCOL_VERSION,
        "deploy",
        "op-0123456789abcdef0123456789abcdef",
        "succeeded",
        "completed",
        {
            "mutation_state": mutation_state,
            "exit_code": 0,
            "failed_boundary": None,
            "observations": {
                "selected_release_id": CANDIDATE,
                "last_successful_selection_id": "selection-" + "e" * 64 + ".json",
                "applied_migrations": [],
                "protected_backup_ids": [],
                "backup_protection_sha256": "f" * 64,
                "restore_target_sha256": None,
                "database_state": "ready",
                "service_state": "running",
                "scheduled_backup_sha256": "c" * 64,
                "backup_timer_enabled": True,
                "backup_timer_state": "active",
            },
            "unavailable_fields": [],
            "inspection_error": None,
            "report": successful_verification_report(CANDIDATE),
            "desired_release_id": CANDIDATE,
            "backup_id": None,
        },
        (),
    )


def test_changed_migrations_require_explicit_policy_before_confirmation_or_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("missing policy must prevent upload"),
    )
    result = deploy(
        object(),
        config(),
        deployment_artifact(tmp_path, migrations=(MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64),)),
        present_plan=lambda _plan: pytest.fail("missing policy must prevent confirmation"),
    )

    assert result.exit_status is ExitStatus.INVALID


def test_restore_required_plans_pending_deploy_migrations_with_a_protected_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejecting this declaration strands releases whose schema cannot roll back in place."""
    from taskman_ops.workflows.deploy import deploy

    migration = MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64)
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))

    result = deploy(
        object(),
        config(),
        deployment_artifact(tmp_path, migrations=(migration,)),
        migration_policy="restore-required",
        dry_run=True,
    )

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "planned"
    assert result.facts["migration_policy"] == "restore-required"
    assert result.facts["pending_migration_versions"] == [20260905120000]
    assert result.facts["planned_backup"] is True


def test_restore_required_refuses_when_deploy_has_no_pending_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore-required is a declaration about a real forward schema transition."""
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))

    result = deploy(
        object(),
        config(),
        deployment_artifact(tmp_path),
        migration_policy="restore-required",
        dry_run=True,
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_restore_required_does_not_continue_partial_first_install_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partially migrated first installation still requires backward-compatible."""
    from taskman_ops.workflows.deploy import deploy_first_release

    first = MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64)
    second = MigrationFingerprint("20260906120000_add_projects.exs", "e" * 64)
    partial = {
        **_EXPECTED,
        "selected_release_id": None,
        "last_successful_selection_id": None,
        "applied_migrations": (20260905120000,),
        "scheduled_backup_sha256": None,
    }
    monkeypatch.setattr("taskman_ops.workflows.deploy._confirmed_expected_state", lambda *_args, **_kwargs: partial)

    result = deploy_first_release(
        object(),
        config(),
        deployment_artifact(tmp_path, migrations=(first, second)),
        migration_policy="restore-required",
        dry_run=True,
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_dry_run_observes_material_authority_but_does_not_need_confirmation_or_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    observed: list[str] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._confirmed_expected_state",
        lambda *_args: observed.append("observed") or _EXPECTED,
    )
    result = deploy(object(), config(), deployment_artifact(tmp_path), dry_run=True)

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "planned"
    assert observed == ["observed"]


def test_first_release_binds_the_exact_fresh_expected_state_to_genesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provisioning must not fabricate a selected release or scheduler checksum."""

    from taskman_ops.workflows.deploy import deploy_first_release

    fresh = {
        **_EXPECTED,
        "selected_release_id": None,
        "last_successful_selection_id": None,
        "scheduled_backup_sha256": None,
        "downgrade_baseline_sha256": "e" * 64,
    }
    captured: dict[str, object] = {}
    genesis_result = _success()
    genesis_result = HostResult(
        genesis_result.protocol_version,
        "genesis",
        genesis_result.correlation_id,
        genesis_result.outcome,
        genesis_result.message,
        genesis_result.state,
        genesis_result.warnings,
    )
    monkeypatch.setattr("taskman_ops.workflows.deploy._confirmed_expected_state", lambda *_args, **_kwargs: fresh)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **kwargs: captured.update(kwargs) or genesis_result,
    )

    result = deploy_first_release(object(), config(), deployment_artifact(tmp_path))

    assert captured["expected_state"] == fresh
    assert captured["operation"] == "genesis"
    assert result.facts["starting_state"] == fresh


def test_first_release_sends_confirmed_null_baseline_pruning_to_genesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interrupted first-install recovery must not replace its planned pruning with ()."""

    from taskman_ops.workflows.deploy import deploy_first_release

    fresh = {
        **_EXPECTED,
        "selected_release_id": None,
        "last_successful_selection_id": None,
        "scheduled_backup_sha256": None,
    }
    prune_ids = ("backup-00000000000000000000000000000001",)
    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr("taskman_ops.workflows.deploy._confirmed_expected_state", lambda *_args, **_kwargs: fresh)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planned_prune_backup_ids",
        lambda *_args, **_kwargs: (prune_ids, {"protections": (), "independent_backup_ids": frozenset()}),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **kwargs: sent.append(kwargs["prune_backup_ids"]) or _success(),
    )

    result = deploy_first_release(object(), config(), deployment_artifact(tmp_path))

    assert result.exit_status is ExitStatus.OK
    assert sent == [prune_ids]


def test_first_release_refuses_post_pyinfra_release_authority_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genesis may observe diagnostics after pyinfra but cannot adopt changed release authority."""

    from taskman_ops.workflows.deploy import deploy_first_release

    confirmed = {
        **_EXPECTED,
        "selected_release_id": None,
        "last_successful_selection_id": None,
        "scheduled_backup_sha256": None,
    }
    drifted = {**confirmed, "selected_release_id": CANDIDATE}
    monkeypatch.setattr("taskman_ops.workflows.deploy._confirmed_expected_state", lambda *_args, **_kwargs: drifted)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("drifted authority must not reach genesis"),
    )

    result = deploy_first_release(
        object(),
        config(),
        deployment_artifact(tmp_path),
        starting_state={"host_authority": confirmed},
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.facts["starting_state"] == {"host_authority": confirmed}


@pytest.mark.parametrize("operation", ("deploy_first_release", "prepare_first_release"))
def test_first_release_refuses_a_changed_target_after_durable_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    """Provision is idempotent only for its exact completed genesis target."""

    from taskman_ops.workflows.deploy import deploy_first_release

    monkeypatch.setattr("taskman_ops.workflows.deploy._confirmed_expected_state", lambda *_args, **_kwargs: _EXPECTED)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("a changed completed target must require deploy"),
    )

    from taskman_ops.workflows import deploy as module
    result = getattr(module, operation)(object(), config(), deployment_artifact(tmp_path))

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


@pytest.mark.parametrize("operation", ("deploy_first_release", "prepare_first_release"))
def test_first_release_refuses_when_history_names_a_different_completed_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    """A physical current link cannot turn a changed completed target into provision."""

    from taskman_ops.workflows.deploy import DeploymentAdmissionAuthority, deploy_first_release

    completed_current = {
        **_EXPECTED,
        "selected_release_id": CANDIDATE,
    }
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._confirmed_expected_state",
        lambda *_args, **_kwargs: completed_current,
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.deployment_admission_authority",
        lambda *_args, **_kwargs: DeploymentAdmissionAuthority((), CANDIDATE, CURRENT),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("changed completed target must require deploy"),
    )

    from taskman_ops.workflows import deploy as module
    result = getattr(module, operation)(object(), config(), deployment_artifact(tmp_path))

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_deploy_consumes_exact_v3_mutation_success_and_preserves_final_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping mutation evidence must prevent a public deployed result."""
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_deployment_request", lambda *_args, **_kwargs: _success())

    result = deploy(object(), config(), deployment_artifact(tmp_path), present_plan=lambda _plan: None, confirm=lambda _plan: True)

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "deployed"
    assert result.facts["mutation_state"] == "changed"
    assert result.facts["selected_release_id"] == CANDIDATE
    assert result.facts["verification"]["expected_release_id"] == CANDIDATE


def test_deploy_preserves_unknown_lost_reply_evidence_in_the_public_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost reply after dispatch must not be rewritten as an unchanged retry."""
    from taskman_ops.workflows.deploy import deploy

    failed = _success(mutation_state="unknown")
    failed = HostResult(
        failed.protocol_version, failed.operation, failed.correlation_id, "retryable", "lost reply",
        {**failed.state, "exit_code": 8, "failed_boundary": "service", "report": None}, (),
    )
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_deployment_request", lambda *_args, **_kwargs: failed)

    result = deploy(object(), config(), deployment_artifact(tmp_path), present_plan=lambda _plan: None, confirm=lambda _plan: True)

    assert result.exit_status is ExitStatus.RELEASE
    assert result.changed is True
    assert result.facts["mutation_state"] == "unknown"
    assert result.facts["selected_release_id"] == CANDIDATE


@pytest.mark.parametrize("genesis", (False, True))
def test_public_lifecycle_verification_failure_preserves_exit_eight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, genesis: bool
) -> None:
    """Deploy and genesis keep the helper's lifecycle report and release exit category."""
    from taskman_ops.workflows.deploy import deploy, deploy_first_release

    report = successful_verification_report(CANDIDATE)
    checks = [dict(check) for check in report["checks"][:5]]
    checks[0]["status"] = "failed"
    report.update(
        status="failed",
        exit_status=8,
        checks=checks,
        next_action="inspect the fixed verification summaries and correct the reported host state before retrying",
    )
    helper_result = _success()
    helper_result = HostResult(
        helper_result.protocol_version,
        "genesis" if genesis else helper_result.operation,
        helper_result.correlation_id,
        "retryable",
        "lifecycle verification failed",
        {**helper_result.state, "exit_code": 8, "failed_boundary": "verification", "report": report},
        (),
    )
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request", lambda *_args, **_kwargs: helper_result
    )

    if genesis:
        monkeypatch.setattr(
            "taskman_ops.workflows.deploy._confirmed_expected_state",
            lambda *_args, **_kwargs: {
                **_EXPECTED,
                "selected_release_id": None,
                "last_successful_selection_id": None,
                "scheduled_backup_sha256": None,
            },
        )
        result = deploy_first_release(object(), config(), deployment_artifact(tmp_path))
    else:
        result = deploy(
            object(), config(), deployment_artifact(tmp_path), present_plan=lambda _plan: None, confirm=lambda _plan: True
        )

    assert result.exit_status is ExitStatus.RELEASE
    assert result.facts["verification"]["exit_status"] == 8


def test_clean_input_drift_reresolves_before_yes_mutates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale clean target never crosses the public confirmation boundary."""
    from taskman_ops.releases.artifacts import CleanInputs, DeploymentTarget
    from taskman_ops.workflows.deploy import deploy

    target = DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built")
    inputs = CleanInputs("b" * 40, "0.2.0", "ubuntu26.04", "amd64", OTP_VERSION, "1.20.4", "22.22.1", "2.5.1", "3.24.0", "tag", "a" * 64, "taskman", ())
    mutations: list[str] = []
    refreshed: list[str] = []
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    matches = iter((False, True, True))
    monkeypatch.setattr("taskman_ops.workflows.deploy.clean_inputs_match", lambda *_args: next(matches))
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_deployment_request", lambda *_args, **_kwargs: mutations.append("apply") or _success())

    result = deploy(
        object(),
        config(),
        target,
        repo=tmp_path,
        clean_inputs=inputs,
        yes=True,
        refresh_clean_target=lambda: refreshed.append("resolved") or (target, inputs),
    )

    assert result.exit_status is ExitStatus.OK
    assert refreshed == ["resolved"]
    assert mutations == ["apply"]


def test_clean_build_drift_during_refresh_retries_within_the_deploy_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source change reported by production resolution restarts the full clean cycle."""
    from taskman_ops.releases.artifacts import CleanInputs, DeploymentTarget
    from taskman_ops.workflows.deploy import deploy

    target = DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built")
    inputs = CleanInputs("b" * 40, "0.2.0", "ubuntu26.04", "amd64", OTP_VERSION, "1.20.4", "22.22.1", "2.5.1", "3.24.0", "tag", "a" * 64, "taskman", ())
    refreshed = 0
    mutations: list[str] = []
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    matches = iter((False, True, True))
    monkeypatch.setattr("taskman_ops.workflows.deploy.clean_inputs_match", lambda *_args: next(matches))
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_deployment_request", lambda *_args, **_kwargs: mutations.append("apply") or _success())

    def refresh() -> tuple[DeploymentTarget, CleanInputs]:
        nonlocal refreshed
        refreshed += 1
        if refreshed == 1:
            raise OpsError(
                ExitStatus.INVALID,
                "artifact",
                "source inputs changed before the fresh build completed",
                changed=False,
            )
        return target, inputs

    result = deploy(
        object(), config(), target, repo=tmp_path, clean_inputs=inputs, yes=True,
        refresh_clean_target=refresh,
    )

    assert result.exit_status is ExitStatus.OK
    assert refreshed == 2
    assert mutations == ["apply"]


def test_interactive_deploy_refuses_authority_drift_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interactive consent cannot be reused after the displayed host authority changes."""
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    changed = {**_EXPECTED, "backup_protection_sha256": "e" * 64}
    observed = iter((_EXPECTED, changed, changed, changed))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._confirmed_expected_state", lambda *_args: next(observed)
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("drifted authority must not reach the helper"),
    )

    result = deploy(
        object(), config(), deployment_artifact(tmp_path),
        present_plan=lambda _plan: None, confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_deploy_refuses_clean_source_drift_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean checkout changed after consent cannot reach the deployment helper."""
    from taskman_ops.releases.artifacts import CleanInputs, DeploymentTarget
    from taskman_ops.workflows.deploy import deploy

    target = DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built")
    inputs = CleanInputs("b" * 40, "0.2.0", "ubuntu26.04", "amd64", OTP_VERSION, "1.20.4", "22.22.1", "2.5.1", "3.24.0", "tag", "a" * 64, "taskman", ())
    matches = iter((True, False))
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr("taskman_ops.workflows.deploy.clean_inputs_match", lambda *_args: next(matches))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("post-confirmation source drift must not mutate"),
    )

    result = deploy(
        object(), config(), target, repo=tmp_path, clean_inputs=inputs,
        present_plan=lambda _plan: None, confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_apply_time_authority_drift_after_yes_requires_a_new_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A --yes confirmation cannot silently adopt a changed host authority."""
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    observed = iter((_EXPECTED, {**_EXPECTED, "backup_protection_sha256": "e" * 64}))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._confirmed_expected_state", lambda *_args: next(observed)
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("drifted authority must not reach the helper"),
    )

    result = deploy(object(), config(), deployment_artifact(tmp_path), yes=True)

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_apply_time_named_recovery_or_baseline_drift_requires_a_new_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan binds recovery IDs and comparisons, not only their digest or reasons."""
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    recovery = {"protections": (), "independent_backup_ids": frozenset()}
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planned_prune_backup_ids",
        lambda *_args, **_kwargs: ((), recovery),
    )
    evidence = iter(
        (
            (True, ((CURRENT, "unknown", ("source-unavailable",)),)),
            (True, ((CURRENT, "downgrade", ("source-ancestor",)),)),
        )
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._downgrade_acknowledgment", lambda *_args: next(evidence)
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("changed material acknowledgment must not reach the helper"),
    )

    result = deploy(object(), config(), deployment_artifact(tmp_path), yes=True, allow_downgrade=True)

    assert result.exit_status is ExitStatus.SAFETY


def test_apply_time_protection_projection_drift_requires_a_new_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing a bounded recovery row cannot retain the former confirmation."""
    from taskman_ops.host_helper.backup_protection import BackupProtection
    from taskman_ops.workflows.deploy import deploy

    baseline = _EXPECTED["last_successful_selection_id"]
    assert isinstance(baseline, str)
    first = BackupProtection(
        1,
        "backup-" + "1" * 32,
        baseline,
        CURRENT,
        0,
        datetime(2026, 9, 7, tzinfo=UTC),
    )
    changed = BackupProtection(
        1,
        first.backup_id,
        baseline,
        CURRENT,
        1,
        datetime(2026, 9, 7, tzinfo=UTC),
    )
    evidence = iter(
        (
            ((), {"protections": (first,), "independent_backup_ids": frozenset()}),
            ((), {"protections": (changed,), "independent_backup_ids": frozenset()}),
        )
    )
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planned_prune_backup_ids", lambda *_args, **_kwargs: next(evidence)
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("changed protection projection must not reach the helper"),
    )

    result = deploy(object(), config(), deployment_artifact(tmp_path), yes=True)

    assert result.exit_status is ExitStatus.SAFETY


def test_deploy_sends_the_exact_prune_ids_shown_in_the_material_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty request must not stand in for a planned protection retirement."""
    from taskman_ops.workflows.deploy import deploy
    from taskman_ops.host_helper.backup_protection import BackupProtection

    prune_ids = (
        "backup-00000000000000000000000000000001",
        "backup-00000000000000000000000000000002",
    )
    sent: list[tuple[str, ...]] = []
    displayed: list[dict[str, object]] = []
    protections = tuple(
        BackupProtection(
            1,
            backup_id,
            _EXPECTED["last_successful_selection_id"],
            CURRENT,
            index,
            datetime(2026, 9, 7, 12, index, tzinfo=UTC),
        )
        for index, backup_id in enumerate(prune_ids)
    )
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planned_prune_backup_ids",
        lambda *_args, **_kwargs: (
            prune_ids,
            {"protections": protections, "independent_backup_ids": frozenset()},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **kwargs: sent.append(kwargs["prune_backup_ids"]) or _success(),
    )

    result = deploy(
        object(),
        config(),
        deployment_artifact(tmp_path),
        yes=True,
        present_plan=lambda plan: displayed.append(dict(plan)),
    )

    assert result.stage == "deployed"
    assert sent == [prune_ids]
    assert displayed[0]["prune_backup_ids"] == list(prune_ids)
    assert displayed[0]["applied_migrations"] == []
    assert displayed[0]["scheduled_backup"]["refresh_required"] is True


def test_material_plan_names_retained_recovery_points_and_each_acknowledged_baseline() -> None:
    """A digest or deduplicated reason cannot tell an operator what was retained or compared."""
    from taskman_ops.host_helper.backup_protection import BackupProtection
    from taskman_ops.output import WorkflowResult, render_human, render_json
    from taskman_ops.workflows.deploy import _material_plan_evidence

    first = "backup-00000000000000000000000000000001"
    second = "backup-00000000000000000000000000000002"
    baseline = "selection-" + "a" * 64 + ".json"
    protections = (
        BackupProtection(1, first, baseline, CURRENT, 0, datetime(2026, 9, 7, tzinfo=UTC)),
        BackupProtection(1, second, baseline, CURRENT, 1, datetime(2026, 9, 7, 0, 1, tzinfo=UTC)),
    )

    evidence = _material_plan_evidence(
        protections,
        {second},
        (first,),
        ((CURRENT, "unknown", ("source-unavailable",)),),
    )

    assert evidence["recovery_protection_points"] == [
        {"backup_id": first, "attempt_number": 0, "base_selection_id": baseline, "target_release_id": CURRENT, "independently_referenced": False, "disposition": "prune-authorized"},
        {"backup_id": second, "attempt_number": 1, "base_selection_id": baseline, "target_release_id": CURRENT, "independently_referenced": True, "disposition": "retained"},
    ]
    assert evidence["downgrade_baselines"] == [
        {"release_id": CURRENT, "order": "unknown", "reasons": ["source-unavailable"]}
    ]
    rendered_json = json.loads(render_json(WorkflowResult("deploy", "test", False, "planned", evidence)))
    assert rendered_json["facts"] == evidence
    human = render_human(WorkflowResult("deploy", "test", False, "planned", evidence))
    assert first in human
    assert "prune-authorized" in human


def test_controller_plans_the_real_post_backup_retirement_set_with_independent_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current eligibility is empty at five; the future sixth must retire attempt one."""
    from datetime import UTC, datetime
    from taskman_ops.host_helper.backup_protection import BackupProtection
    from taskman_ops.workflows import deploy as deploy_module

    monkeypatch.undo()
    baseline = _EXPECTED["last_successful_selection_id"]
    assert isinstance(baseline, str)
    protections = tuple(
        BackupProtection(
            1,
            f"backup-{index:032x}",
            baseline,
            CURRENT,
            index,
            datetime(2026, 9, 7, 12, index, tzinfo=UTC),
        )
        for index in range(5)
    )
    state = {
        **_EXPECTED,
        "backup_protections": [item.to_mapping() for item in protections],
        "independently_held_backup_ids": [],
    }
    monkeypatch.setattr(
        deploy_module,
        "run_request",
        lambda *_args, **_kwargs: HostResult(
            PROTOCOL_VERSION, "discover", "op-0123456789abcdef0123456789abcdef", "succeeded", "observed", state, ()
        ),
    )

    prune_ids, evidence = deploy_module._planned_prune_backup_ids(
        object(), config(), _EXPECTED, fresh_backup_needed=True
    )

    assert prune_ids == ("backup-00000000000000000000000000000001",)
    assert evidence["independent_backup_ids"] == frozenset()


def test_migration_policy_uses_the_live_applied_prefix_not_the_previous_release_schema() -> None:
    """A target that omits a live migration is never backward compatible."""
    from taskman_ops.workflows.deploy import _validate_migration_policy

    first = MigrationFingerprint("20260905120000_create_tasks.exs", "a" * 64)
    second = MigrationFingerprint("20260906120000_add_projects.exs", "b" * 64)

    with pytest.raises(Exception) as raised:
        _validate_migration_policy((first,), (second,), "backward-compatible")

    assert getattr(raised.value, "status", None) is ExitStatus.SAFETY

    with pytest.raises(Exception) as raised:
        _validate_migration_policy((first,), (first, second), "restore-required")

    assert getattr(raised.value, "status", None) is ExitStatus.SAFETY


def test_json_mode_requires_a_downgrade_flag_without_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A JSON request cannot obtain its separate downgrade consent from stdin."""
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._downgrade_acknowledgment",
        lambda *_args: (True, ("source-unavailable",)),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._confirm_downgrade",
        lambda _plan: pytest.fail("JSON mode must never call input"),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("missing downgrade consent must prevent mutation"),
    )

    result = deploy(
        object(), config(), deployment_artifact(tmp_path), yes=True, interactive=False
    )

    assert result.exit_status is ExitStatus.SAFETY


def test_unattended_unknown_baseline_requires_independent_downgrade_acknowledgment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordinary --yes cannot silently acknowledge an unorderable replacement."""
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._downgrade_acknowledgment",
        lambda *_args: (True, ("source-unavailable",)),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("missing downgrade acknowledgment must prevent mutation"),
    )

    result = deploy(object(), config(), deployment_artifact(tmp_path), yes=True)

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_equal_source_version_rebuild_needs_only_ordinary_acknowledgment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same source/version archive bytes are a rebuild, not unknown order."""
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr("taskman_ops.workflows.deploy._downgrade_acknowledgment", lambda *_args, **_kwargs: (False, ()))
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_deployment_request", lambda *_args, **_kwargs: _success())

    result = deploy(object(), config(), deployment_artifact(tmp_path), yes=True)

    assert result.stage == "deployed"


def test_prepare_first_release_uses_shared_admission_and_local_proof(tmp_path, monkeypatch):
    import taskman_ops.workflows.deploy as module
    fresh = {**_EXPECTED, "selected_release_id": None, "last_successful_selection_id": None,
             "scheduled_backup_sha256": None, "downgrade_baseline_sha256": "e" * 64}
    artifact = deployment_artifact(tmp_path)
    success = _success()
    proof = dict(success.state["report"])
    proof["scope"] = "application"
    proof["checks"] = [c for c in proof["checks"] if c["name"] not in {"caddy-service", "public-readiness", "public-hsts"}]
    state = {k: v for k, v in success.state.items() if k != "report"}
    state.update(local_report=proof, public_probe="not-ready", observations={**state["observations"], "last_successful_selection_id": None})
    prepared = HostResult(3, "prepare_genesis", success.correlation_id, "succeeded", "prepared", state, ())
    captured = {}
    monkeypatch.setattr(module, "_confirmed_expected_state", lambda *_a, **_k: fresh)
    monkeypatch.setattr(module, "run_deployment_request", lambda *_a, **kw: captured.update(kw) or prepared)
    handler = getattr(module, "prepare_first_release", None)
    assert callable(handler), "controller preparation capability is missing"
    result = handler(object(), config(), artifact)
    assert result.exit_status == 0
    assert result.stage == "application-prepared"
    assert captured["operation"] == "prepare_genesis"
    assert captured["expected_state"] == fresh
    assert result.facts["starting_state"] == fresh
    assert result.facts["local_report"] == proof
    assert result.facts["public_probe"] == "not-ready"
    assert result.facts["last_successful_selection_id"] is None


def test_prepare_first_release_preserves_local_failure_report_and_probe(tmp_path, monkeypatch):
    import taskman_ops.workflows.deploy as module
    fresh = {**_EXPECTED,"selected_release_id":None,"last_successful_selection_id":None}
    success = _success()
    proof = dict(success.state["report"])
    proof.update(scope="application", status="failed",exit_status=9,
        next_action="inspect the fixed verification summaries and correct the reported host state before retrying")
    proof["checks"] = [dict(c) for c in proof["checks"] if c["name"] not in {"caddy-service","public-readiness","public-hsts"}]
    proof["checks"][-1]["status"] = "failed"
    state = {k:v for k,v in success.state.items() if k != "report"}
    state.update(exit_code=9,failed_boundary="verification",local_report=proof,public_probe="not-attempted")
    failed = HostResult(3,"prepare_genesis",success.correlation_id,"retryable","readiness failed",state,())
    monkeypatch.setattr(module,"_confirmed_expected_state",lambda *_a,**_kw:fresh)
    monkeypatch.setattr(module,"run_deployment_request",lambda *_a,**_kw:failed)
    result = module.prepare_first_release(object(),config(),deployment_artifact(tmp_path))
    assert result.exit_status == 9
    assert result.facts["local_report"] == proof
    assert result.facts["public_probe"] == "not-attempted"
    assert result.facts["starting_state"] == {**fresh, "applied_migrations": []}
    assert result.facts["mutation_state"] == "changed"


def _prepared_completion(tmp_path):
    import hashlib
    from taskman_ops.output import WorkflowResult
    artifact = deployment_artifact(tmp_path)
    original = {**_EXPECTED, "selected_release_id": None,
                "last_successful_selection_id": None, "applied_migrations": (),
                "backup_protections": (), "initial_database_empty": True, "database_state": "ready",
                "last_successful_selection": None}
    empty = hashlib.sha256(b"[]").hexdigest()
    original["backup_protection_sha256"] = empty
    observation = {**dict(_success().state["observations"]),
                   "last_successful_selection_id": None,
                   "backup_protection_sha256": empty}
    proof = dict(_success().state["report"])
    proof.update(scope="application")
    proof["checks"] = [c for c in proof["checks"] if c["name"] not in {"caddy-service", "public-readiness", "public-hsts"}]
    prepared = WorkflowResult("deploy", "production", True, "application-prepared",
        {**observation, "observations": observation, "candidate_release_id": CANDIDATE,
         "local_report": proof, "public_probe": "ready", "backup_id": None,
         "unavailable_fields": [], "inspection_error": None, "mutation_state": "changed"})
    fresh = {**original, **observation,
             "downgrade_baseline_sha256": hashlib.sha256(json.dumps([CANDIDATE],separators=(",", ":")).encode()).hexdigest()}
    return artifact, {"host_authority": original, "candidate_release_id": artifact.manifest.release_id,
                      "artifact_sha256": artifact.sha256}, prepared, fresh


def test_prepared_completion_uses_fresh_exact_authority_and_original_public_start(tmp_path, monkeypatch):
    import taskman_ops.workflows.deploy as module
    from contextlib import contextmanager
    from types import SimpleNamespace
    artifact, starting, prepared, fresh = _prepared_completion(tmp_path)
    @contextmanager
    def package():
        yield SimpleNamespace(sha256="c" * 64)
    monkeypatch.setattr(module, "temporary_scheduled_backup_helper_package", package)
    monkeypatch.setattr(module, "run_request", lambda *_a: HostResult(3,"discover", "op-"+"1"*32,"succeeded","discovered",fresh,()))
    monkeypatch.setattr(module,"_confirmed_expected_state",lambda *_a,**_kw:{key:fresh[key] for key in _EXPECTED})
    calls = []
    monkeypatch.setattr(module,"run_deployment_request",lambda *_a,**kw:calls.append(kw) or _success())
    result = module.finalize_first_release(object(), config(), artifact, preparation=prepared,
                                          starting_state=starting, migration_policy="restore-required",
                                          prune_backup_ids=(), scheduler_create=())
    assert result.exit_status == 0
    assert calls[0]["expected_state"]["selected_release_id"] == CANDIDATE
    assert calls[0]["migration_policy"] == "no-change"
    assert calls[0]["operation"] == "genesis"
    assert result.facts["starting_state"] == starting


@pytest.mark.parametrize("field,value", [("selected_release_id",CURRENT), ("applied_migrations",(20260905120000,)),
    ("backup_protection_sha256","f"*64),("scheduled_backup_sha256","e"*64),
    ("backup_timer_enabled",False),("last_successful_selection_id","selection-"+"e"*64+".json"),
    ("downgrade_baseline_sha256","f"*64)])
def test_prepared_completion_refuses_drift_without_claiming_old_observations(tmp_path,monkeypatch,field,value):
    import taskman_ops.workflows.deploy as module
    from contextlib import contextmanager
    from types import SimpleNamespace
    artifact, starting, prepared, fresh = _prepared_completion(tmp_path)
    @contextmanager
    def package():
        yield SimpleNamespace(sha256="c" * 64)
    monkeypatch.setattr(module,"temporary_scheduled_backup_helper_package",package)
    fresh[field] = value
    monkeypatch.setattr(module,"run_request",lambda *_a:HostResult(3,"discover","op-"+"1"*32,"succeeded","discovered",fresh,()))
    monkeypatch.setattr(module,"run_deployment_request",lambda *_a,**_kw:pytest.fail("drift must prevent full dispatch"))
    result=module.finalize_first_release(object(),config(),artifact,preparation=prepared,starting_state=starting,
                                         prune_backup_ids=(),scheduler_create=())
    assert result.exit_status == ExitStatus.SAFETY
    assert result.facts["selected_release_id"] is None
    assert result.facts["applied_migrations"] is None
    assert result.facts["inspection_error"]
    assert "selected_release_id" in result.facts["unavailable_fields"]
    assert result.facts["starting_state"] == starting


@pytest.mark.parametrize("database_state,initial_empty", [
    ("ready", False), ("unknown", False), ("unknown", True), ("absent", True)])
def test_completion_without_migration_backup_requires_positive_empty_authority(
    tmp_path, monkeypatch, database_state, initial_empty):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from taskman_ops.output import WorkflowResult
    import taskman_ops.workflows.deploy as module
    artifact, starting, prepared, fresh = _prepared_completion(tmp_path)
    migration = MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64)
    artifact = deployment_artifact(tmp_path, migrations=(migration,))
    starting["host_authority"].update(database_state=database_state, initial_database_empty=initial_empty)
    facts = dict(prepared.facts)
    facts["observations"] = {**facts["observations"], "applied_migrations": [20260905120000]}
    prepared = WorkflowResult(prepared.command, prepared.environment, prepared.changed, prepared.stage, facts)
    fresh["applied_migrations"] = [20260905120000]
    @contextmanager
    def package():
        yield SimpleNamespace(sha256="c" * 64)
    monkeypatch.setattr(module, "temporary_scheduled_backup_helper_package", package)
    from taskman_ops.releases.artifacts import DeploymentTarget
    with pytest.raises(OpsError) as raised:
        module._prepared_completion_authority(
            DeploymentTarget(artifact=artifact, release_record=None, source="explicit"),
            starting, prepared, fresh, prune_backup_ids=(), scheduler_create=())
    assert raised.value.status == ExitStatus.SAFETY


@pytest.mark.parametrize("site", ["admission", "confirmation", "downgrade"])
def test_postgresql_discovery_failure_preserves_category_check_and_cleanup_warning(monkeypatch, site):
    from taskman_ops.workflows import deploy as module
    observed = []
    def failed(_remote, request):
        observed.append(request)
        return HostResult.for_request(request, "manual", "PostgreSQL client must support require_auth",
            {"failed_boundary": "postgresql", "exit_code": 5}, ("transient helper cleanup was incomplete",))
    monkeypatch.setattr(module, "run_request", failed)
    with pytest.raises(OpsError) as raised:
        if site == "admission":
            module.deployment_admission_authority(object(), config(), mode="provision", force_postgresql_config=True)
        elif site == "confirmation":
            observed_expected_state(object(), config(), mode="provision", force_postgresql_config=True)
        else:
            observed_downgrade_authority(object(), config(), object(), None, mode="provision", force_postgresql_config=True)
    error = raised.value
    assert error.status is ExitStatus.REMOTE_PREFLIGHT
    assert error.stage == "postgresql"
    assert error.message == "PostgreSQL client must support require_auth"
    assert error.state == {"failed_boundary": "postgresql", "exit_code": 5}
    assert error.warnings == ("transient helper cleanup was incomplete",)
    assert error.changed is False
    assert observed[0].parameters["force_postgresql_config"] is True


@pytest.mark.parametrize("completed_preparation", [False, True], ids=["fresh-first-release", "prepared-completion"])
def test_post_convergence_first_release_discovery_failure_preserves_postgresql_result(tmp_path, monkeypatch, completed_preparation):
    from taskman_ops.workflows import deploy as module
    from taskman_ops.output import WorkflowResult
    calls = []
    def failed(_remote, request):
        calls.append(request)
        return HostResult.for_request(request, "manual", "PostgreSQL SCRAM access proof failed",
            {"failed_boundary": "postgresql", "exit_code": 5}, ("transient helper cleanup was incomplete",))
    monkeypatch.setattr(module, "run_request", failed)
    monkeypatch.setattr(module, "_confirmed_expected_state", observed_expected_state)
    monkeypatch.setattr(module, "run_deployment_request", lambda *_args, **_kwargs: pytest.fail("failed SCRAM proof must stop release effects"))
    target = deployment_artifact(tmp_path)
    if completed_preparation:
        result = module.finalize_first_release(object(), config(), target,
            preparation=WorkflowResult("provision", "production", True, "prepared", {}),
            starting_state={"confirmed": "before-convergence"})
    else:
        result = module.deploy_first_release(object(), config(), target)
    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.stage == "postgresql-failed"
    assert result.facts["failure_boundary"] == "postgresql"
    assert result.warnings == ("transient helper cleanup was incomplete",)
    assert result.changed is False
    assert calls[0].parameters["force_postgresql_config"] is False
    if completed_preparation:
        assert result.facts["mutation_state"] == "unchanged"
        assert result.facts["starting_state"] == {"confirmed": "before-convergence"}
