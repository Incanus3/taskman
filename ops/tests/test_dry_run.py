"""Dry-run results remain ordinary public workflow output."""

import pytest

from taskman_ops.cli import parse_invocation
from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.output import WorkflowResult
from taskman_ops.workflows.backup import run_backup
from tests.support.environments import valid_environment


@pytest.fixture(autouse=True)
def _valid_operational_preflights(monkeypatch: pytest.MonkeyPatch) -> None:
    for workflow in ("backup", "deploy"):
        monkeypatch.setattr(
            f"taskman_ops.workflows.{workflow}.validate_operational_preflight",
            lambda *_args: None,
        )


def test_dry_run_result_has_no_helper_protocol_evidence() -> None:
    result = WorkflowResult("backup", "test", False, "planned", {"planned_backup": True}, (), "rerun")

    assert result.changed is False
    assert result.stage == "planned"
    assert "operation_id" not in repr(result.facts)


@pytest.mark.parametrize(
    ("command", "identifiers"),
    (
        ("deploy", ("production",)),
        ("backup", ("production",)),
        ("rollback", ("production", "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6")),
        ("restore", ("production", "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")),
        ("cleanup", ("production",)),
    ),
)
def test_mutating_cli_commands_preserve_the_common_dry_run_contract(
    command: str, identifiers: tuple[str, ...]
) -> None:
    invocation = parse_invocation([command, *identifiers, "--dry-run"])

    assert invocation.command == command
    assert invocation.dry_run is True


def test_backup_dry_run_reads_only_discovery_and_never_requests_a_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real backup workflow may plan from host facts but cannot mutate in dry-run."""

    config = EnvironmentConfig.model_validate(valid_environment(name="production"))
    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        return HostResult(
            2,
            request.operation,
            request.correlation_id,
            "succeeded",
            "completed",
            {"backups": (), "activations": ()},
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", invoke)

    outcome = run_backup(object(), config, dry_run=True)

    assert outcome.stage == "planned"
    assert outcome.changed is False
    assert [request.operation for request in requests] == ["discover"]


def test_deploy_dry_run_reads_completed_authority_without_uploading_or_deploying(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployment preserves plan-only mode at the final helper boundary."""

    from taskman_ops.workflows.deploy import deploy
    from tests.workflows.support import deployment_artifact

    current_release = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    config = EnvironmentConfig.model_validate(valid_environment(name="production"))
    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        return HostResult(
            2,
            request.operation,
            request.correlation_id,
            "succeeded",
            "observed",
            {
                "selected_release_id": current_release,
                "applied_migrations": (),
                "releases": ({"release_id": current_release, "migrations": ()},),
            },
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.deploy.run_request", invoke)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not upload or deploy"),
    )

    outcome = deploy(object(), config, deployment_artifact(tmp_path), dry_run=True)

    assert outcome.stage == "planned"
    assert outcome.changed is False
    assert [request.operation for request in requests] == ["discover"]
