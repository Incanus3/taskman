"""Public deployment controller results and consent boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import OTP_VERSION, MigrationFingerprint
from tests.support.environments import valid_environment
from tests.workflows.support import CANDIDATE, deployment_artifact, successful_verification_report


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
    monkeypatch.setattr("taskman_ops.workflows.deploy._downgrade_acknowledgment", lambda *_args: (False, ()))


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


def test_dry_run_does_not_need_confirmation_or_apply_reobservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._confirmed_expected_state",
        lambda *_args: pytest.fail("dry-run cannot reobserve an apply snapshot"),
    )
    result = deploy(object(), config(), deployment_artifact(tmp_path), dry_run=True)

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "planned"


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


def test_clean_input_drift_reresolves_before_yes_mutates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale clean target never crosses the public confirmation boundary."""
    from taskman_ops.releases.artifacts import CleanInputs, DeploymentTarget
    from taskman_ops.workflows.deploy import deploy

    target = DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built")
    inputs = CleanInputs("b" * 40, "0.2.0", "ubuntu26.04", "amd64", OTP_VERSION, "1.20.4", "22.22.1", "2.5.1", "3.24.0", "tag", "a" * 64, "taskman", ())
    mutations: list[str] = []
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args: (CURRENT, (), ()))
    matches = iter((False, True))
    monkeypatch.setattr("taskman_ops.workflows.deploy.clean_inputs_match", lambda *_args: next(matches))
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_deployment_request", lambda *_args, **_kwargs: mutations.append("apply") or _success())

    result = deploy(object(), config(), target, repo=tmp_path, clean_inputs=inputs, yes=True, refresh_clean_target=lambda: (target, inputs))

    assert result.stage == "deployed"
    assert mutations == ["apply"]


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
    monkeypatch.setattr("taskman_ops.workflows.deploy._downgrade_acknowledgment", lambda *_args: (False, ()))
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_deployment_request", lambda *_args, **_kwargs: _success())

    result = deploy(object(), config(), deployment_artifact(tmp_path), yes=True)

    assert result.stage == "deployed"
