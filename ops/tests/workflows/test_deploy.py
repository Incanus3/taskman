"""Deployment outcomes over validated artifacts and final helper evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostResult
from taskman_ops.releases.manifests import MigrationFingerprint
from tests.workflows.support import CANDIDATE, deployment_artifact, successful_verification_report
from tests.support.environments import valid_environment


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


def config() -> EnvironmentConfig:
    values = valid_environment()
    values["ssh_port"] = 22
    return EnvironmentConfig.model_validate(values)


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.validate_operational_preflight",
        lambda *_args: None,
    )


def test_deploy_final_result_has_no_private_operation_identifier() -> None:
    result = HostResult(
        2, "deploy", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {"changed": True, "selected_release_id": "2026.9.7-deadbeef"}, (),
    )

    assert result.to_mapping()["correlation_id"] == result.correlation_id


@pytest.mark.parametrize("dry_run", [False, True])
def test_changed_migrations_require_explicit_policy_before_confirmation_or_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: (CURRENT, (), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("missing policy must prevent upload"),
    )
    artifact = deployment_artifact(
        tmp_path, migrations=(MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64),)
    )
    result = deploy(
        object(), config(), artifact, dry_run=dry_run,
        present_plan=lambda _plan: pytest.fail("missing policy must prevent plan presentation"),
        confirm=lambda _plan: pytest.fail("missing policy must prevent confirmation"),
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"
    assert "--migration-policy backward-compatible" in result.next_action
    assert "--migration-policy restore-required" in result.next_action


@pytest.mark.parametrize("policy", ["backward-compatible", "restore-required"])
def test_changed_migrations_preserve_explicit_policy_in_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: (CURRENT, (), ()),
    )
    artifact = deployment_artifact(
        tmp_path, migrations=(MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64),)
    )
    result = deploy(object(), config(), artifact, migration_policy=policy, dry_run=True)

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "planned"
    assert result.facts["migration_policy"] == policy


def test_deploy_refuses_success_without_proven_ready_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protocol-valid but partial deployment result cannot claim success."""

    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: (CURRENT, (), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: HostResult(
            2,
            "deploy",
            "op-0123456789abcdef0123456789abcdef",
            "succeeded",
            "completed",
            {"changed": True, "selected_release_id": CANDIDATE},
            (),
        ),
    )

    result = deploy(
        object(),
        config(),
        deployment_artifact(tmp_path),
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_deploy_refuses_preflight_before_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.validate_operational_preflight",
        lambda *_args: (_ for _ in ()).throw(
            OpsError(ExitStatus.REMOTE_PREFLIGHT, "preflight", "unsafe")
        ),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: pytest.fail("preflight refusal must precede planning"),
    )

    result = deploy(object(), config(), deployment_artifact(tmp_path))

    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.stage == "preflight-failed"


def test_deploy_publishes_only_a_complete_verified_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal deployed result is available only after all published facts validate."""

    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: (CURRENT, (), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: HostResult(
            2,
            "deploy",
            "op-0123456789abcdef0123456789abcdef",
            "succeeded",
            "completed",
            {
                "changed": True,
                "selected_release_id": CANDIDATE,
                "backup_id": "backup-cccccccccccccccccccccccccccccccc",
                "database_state": "unchanged",
                "service_state": "running",
                "report": successful_verification_report(CANDIDATE),
            },
            (),
        ),
    )

    result = deploy(
        object(),
        config(),
        deployment_artifact(tmp_path),
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "deployed"
    assert result.facts["verification"]["expected_release_id"] == CANDIDATE
    assert "activation_recorded" not in result.facts


def test_deploy_failure_keeps_the_observed_selected_release_as_a_public_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coarse retry results retain the final helper's observable authority."""

    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: (CURRENT, (), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: HostResult(
            2,
            "deploy",
            "op-0123456789abcdef0123456789abcdef",
            "retryable",
            "migration result lost",
            {
                "failed_boundary": "migration",
                "selected_release_id": CURRENT,
                "applied_migrations": (),
            },
            (),
        ),
    )

    result = deploy(
        object(),
        config(),
        deployment_artifact(tmp_path),
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.RELEASE
    assert result.facts["previous_release_id"] == CURRENT
    assert result.facts["selected_release_id"] == CURRENT


def test_first_release_with_migrations_preserves_restore_required_without_a_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskman_ops.workflows.deploy import deploy_first_release

    migration = MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64)
    captured: dict[str, object] = {}

    def run_deployment(*_args: object, **kwargs: object) -> HostResult:
        captured.update(kwargs)
        return HostResult(
            2,
            "genesis",
            "op-0123456789abcdef0123456789abcdef",
            "succeeded",
            "completed",
            {
                "changed": True,
                "selected_release_id": CANDIDATE,
                "backup_id": None,
                "database_state": "changed",
                "service_state": "running",
                "report": successful_verification_report(CANDIDATE),
            },
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.deploy.run_deployment_request", run_deployment)

    result = deploy_first_release(object(), config(), deployment_artifact(tmp_path, migrations=(migration,)))

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "deployed"
    assert result.facts["migration_policy"] == "restore-required"
    assert result.facts["backup_id"] is None
    assert captured == {
        "migration_policy": "restore-required",
        "previous_release_id": None,
        "applied_migrations": (),
        "genesis": True,
    }


def test_first_release_surfaces_a_manual_unowned_schema_without_claiming_deployment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskman_ops.workflows.deploy import deploy_first_release

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: HostResult(
            2,
            "genesis",
            "op-0123456789abcdef0123456789abcdef",
            "manual",
            "deployment state is contradictory",
            {"selected_release_id": None, "applied_migrations": (999,)},
            (),
        ),
    )

    result = deploy_first_release(object(), config(), deployment_artifact(tmp_path, migrations=(MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64),)))

    assert result.exit_status is ExitStatus.RELEASE
    assert result.stage == "deployment-incomplete"
    assert result.changed is False
    assert result.facts["previous_release_id"] is None
    assert result.facts["selected_release_id"] is None
