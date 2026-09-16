from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
import json

import pytest

from taskman_ops.cli import Invocation, dispatch, main
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import WorkflowResult, register_secret
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.verify import run_verify
from tests.support.environments import environment_config
from tests.support.secrets import no_registered_secrets_between_tests as clear_output_secrets


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
CHECK_NAMES = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)
FAILED_NEXT_ACTION = "inspect the fixed verification summaries and correct the reported host state before retrying"


def test_cli_renders_failed_verification_inside_the_workflow_envelope() -> None:
    """A direct report renderer would drop command, stage, and redaction handling."""

    report = readiness_report()
    result = workflow_result(report)
    stdout = StringIO()
    stderr = StringIO()

    status = main(["verify", "production"], dispatch_fn=lambda _invocation: result, stdout=stdout, stderr=stderr)

    assert status == ExitStatus.READINESS
    assert "Command: verify" in stdout.getvalue()
    assert "Environment: production" in stdout.getvalue()
    assert "Status: failed" in stdout.getvalue()
    assert "Stage: verification-failed" in stdout.getvalue()
    assert "schema_version" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_human_and_json_use_identical_redacted_verification_facts() -> None:
    """An alternate human renderer could leak a report value hidden from JSON."""

    canary = "verify-envelope-canary-93dc"
    register_secret(canary)
    checks = list(successful_checks())
    checks[0] = {
        "schema_version": 1,
        "name": "taskman-service",
        "status": "passed",
        "summary": f"service active {canary}",
    }
    report = verification_report(0, RELEASE_ID, RELEASE_ID, checks, None)
    result = workflow_result(report)
    human = StringIO()
    machine = StringIO()

    assert main(["verify", "production"], dispatch_fn=lambda _invocation: result, stdout=human) == 0
    assert main(["verify", "production", "--json"], dispatch_fn=lambda _invocation: result, stdout=machine) == 0

    payload = json.loads(machine.getvalue())
    assert payload["command"] == "verify"
    assert payload["environment"] == "production"
    assert payload["changed"] is False
    assert payload["stage"] == "verified"
    assert payload["warnings"] == []
    assert payload["next_action"] is None
    assert payload["facts"]["verification"]["schema_version"] == 1
    assert payload["facts"]["verification"]["next_action"] is None
    assert payload["facts"]["verification"]["checks"][0]["schema_version"] == 1
    assert canary not in machine.getvalue()
    assert canary not in human.getvalue()
    assert json.dumps(payload["facts"], sort_keys=True) in human.getvalue()


def test_verify_dispatch_connects_and_returns_a_read_only_workflow_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI controller must route verify through the concrete workflow."""

    environment = EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43,
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@acme.tld",
        }
    )
    remote = object()
    report = verification_report(0, RELEASE_ID, None, successful_checks(), None)
    expected = workflow_result(report)
    seen: list[tuple[object, EnvironmentConfig]] = []
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda name: environment if name == "production" else None)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda value: remote if value is environment else None)
    monkeypatch.setattr(
        "taskman_ops.workflows.verify.run_verify",
        lambda actual_remote, actual_environment: seen.append((actual_remote, actual_environment)) or expected,
    )

    result = dispatch(Invocation(command="verify", environment="production"))

    assert result is expected
    assert seen == [(remote, environment)]


def successful_checks() -> list[dict[str, object]]:
    return [
        {
            "schema_version": 1,
            "name": name,
            "status": "passed",
            "summary": f"{name} passed",
        }
        for name in CHECK_NAMES
    ]


def readiness_report() -> dict[str, object]:
    checks = successful_checks()[:6]
    checks[-1] = {
        "schema_version": 1,
        "name": "local-readiness",
        "status": "failed",
        "summary": "local readiness failed",
    }
    return verification_report(ExitStatus.READINESS, RELEASE_ID, None, checks, FAILED_NEXT_ACTION)


def release_report() -> dict[str, object]:
    checks = successful_checks()[:5]
    checks[-1] = {
        "schema_version": 1,
        "name": "startup-journal",
        "status": "failed",
        "summary": "startup journal failed",
    }
    return verification_report(ExitStatus.RELEASE, RELEASE_ID, None, checks, FAILED_NEXT_ACTION)


def verification_report(
    exit_status: int,
    release_id: str,
    expected_release_id: str | None,
    checks: list[dict[str, object]],
    next_action: str | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "ok" if exit_status == 0 else "failed",
        "exit_status": int(exit_status),
        "release_id": release_id,
        "expected_release_id": expected_release_id,
        "checks": checks,
        "next_action": next_action,
    }


def workflow_result(report: Mapping[str, object]) -> WorkflowResult:
    return WorkflowResult(
        command="verify",
        environment="production",
        changed=False,
        stage="verified" if report["exit_status"] == 0 else "verification-failed",
        facts={"verification": dict(report)},
        warnings=(),
        next_action=report["next_action"],
        exit_status=ExitStatus(report["exit_status"]),
    )


def test_verify_accepts_completed_report_with_observation_projection_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = verification_report(ExitStatus.OK, RELEASE_ID, RELEASE_ID, successful_checks(), None)

    def invoke(_remote: object, request: object, **_kwargs: object) -> HostResult:
        assert isinstance(request, HostRequest)
        assert request.operation == "verify"
        return HostResult(
            3,
            "verify",
            request.correlation_id,  # type: ignore[attr-defined]
            "succeeded",
            "verification completed",
            {
                "report": report,
                "selected_release_id": RELEASE_ID,
                "service_state": "running",
                "database_state": "ready",
                "future_fact": "ignored",
            },
            ("unknown non-authoritative entry",),
        )

    monkeypatch.setattr("taskman_ops.workflows.verify.run_request", invoke)

    result = run_verify(object(), EnvironmentConfig.model_validate({
        "name": "production",
        "ssh_host": "203.0.113.10",
        "ssh_port": 22,
        "ssh_user": "deployer",
        "host_key_fingerprint": "SHA256:" + "A" * 43,
        "public_hostname": "taskman.acme.tld",
        "public_ipv4": "203.0.113.10",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "application_port": 4000,
        "distribution_port": 6789,
        "database_name": "taskman_prod",
        "database_role": "taskman",
        "mail_from": "no-reply@acme.tld",
    }), expected_release_id=RELEASE_ID)

    assert result.stage == "verified"
    assert result.facts["verification"]["release_id"] == RELEASE_ID
    assert result.warnings == ("unknown non-authoritative entry",)


@pytest.mark.parametrize("report_factory", [release_report, readiness_report])
def test_verify_preserves_failed_reports_and_exit_categories(
    monkeypatch: pytest.MonkeyPatch,
    report_factory,
) -> None:
    report = report_factory()

    def invoke(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        return HostResult(
            3,
            request.operation,
            request.correlation_id,
            "retryable",
            "verification failed",
            {
                "report": report,
                "selected_release_id": RELEASE_ID,
                "service_state": "failed",
                "database_state": "ready",
            },
            ("journal evidence incomplete",),
        )

    monkeypatch.setattr("taskman_ops.workflows.verify.run_request", invoke)

    result = run_verify(object(), EnvironmentConfig.model_validate({
        "name": "production",
        "ssh_host": "203.0.113.10",
        "ssh_port": 22,
        "ssh_user": "deployer",
        "host_key_fingerprint": "SHA256:" + "A" * 43,
        "public_hostname": "taskman.acme.tld",
        "public_ipv4": "203.0.113.10",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "application_port": 4000,
        "distribution_port": 6789,
        "database_name": "taskman_prod",
        "database_role": "taskman",
        "mail_from": "no-reply@acme.tld",
    }))

    assert result.stage == "verification-failed"
    assert result.exit_status is ExitStatus(report["exit_status"])
    assert result.facts["verification"]["checks"][-1]["status"] == "failed"
    assert result.warnings == ("journal evidence incomplete",)


def test_verify_rejects_a_malformed_success_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete helper report must remain a fixed safety failure."""

    report = verification_report(0, RELEASE_ID, RELEASE_ID, [], None)

    def invoke(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        return HostResult(
            3,
            request.operation,
            request.correlation_id,
            "succeeded",
            "verification completed",
            {"report": report},
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.verify.run_request", invoke)

    with pytest.raises(OpsError) as raised:
        run_verify(object(), environment_config(), expected_release_id=RELEASE_ID)

    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.stage == "verification"
    assert raised.value.message == "verification returned invalid observed state"


def test_verify_rejects_a_successful_report_for_another_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid report for another release cannot satisfy the requested proof."""

    other_release = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "c" * 64
    report = verification_report(0, RELEASE_ID, RELEASE_ID, successful_checks(), None)

    def invoke(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        return HostResult(
            3,
            request.operation,
            request.correlation_id,
            "succeeded",
            "verification completed",
            {"report": report},
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.verify.run_request", invoke)

    with pytest.raises(OpsError) as raised:
        run_verify(object(), environment_config(), expected_release_id=other_release)

    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.stage == "verification"
    assert raised.value.message == "verification returned an unrelated release"
