from __future__ import annotations

from io import StringIO
import json

import pytest

from taskman_ops.cli import Invocation, dispatch, main
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus
from taskman_ops.output import WorkflowResult, clear_secrets, register_secret
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.verify import run_verify
from taskman_ops.workflows.verification_results import (
    CheckStatus,
    VerificationCheck,
    VerificationReport,
)


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
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


@pytest.fixture(autouse=True)
def clear_output_secrets() -> None:
    clear_secrets()
    yield
    clear_secrets()


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
    checks[0] = VerificationCheck("taskman-service", CheckStatus.PASSED, f"service active {canary}")
    report = VerificationReport(ExitStatus.OK, RELEASE_ID, RELEASE_ID, tuple(checks), None)
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
    report = VerificationReport(ExitStatus.OK, RELEASE_ID, None, successful_checks(), None)
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


def successful_checks() -> tuple[VerificationCheck, ...]:
    return tuple(VerificationCheck(name, CheckStatus.PASSED, f"{name} passed") for name in CHECK_NAMES)


def readiness_report() -> VerificationReport:
    checks = list(successful_checks()[:6])
    checks[-1] = VerificationCheck("local-readiness", CheckStatus.FAILED, "local readiness failed")
    return VerificationReport(ExitStatus.READINESS, RELEASE_ID, None, tuple(checks), FAILED_NEXT_ACTION)


def workflow_result(report: VerificationReport) -> WorkflowResult:
    return WorkflowResult(
        command="verify",
        environment="production",
        changed=False,
        stage="verified" if report.successful else "verification-failed",
        facts={"verification": report.to_mapping()},
        warnings=(),
        next_action=report.next_action,
        exit_status=report.exit_status,
    )


def test_verify_accepts_completed_report_with_observation_projection_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = VerificationReport(ExitStatus.OK, RELEASE_ID, RELEASE_ID, successful_checks(), None)

    def invoke(_remote: object, request: object, **_kwargs: object) -> HostResult:
        assert isinstance(request, HostRequest)
        assert request.operation == "verify"
        return HostResult(
            2,
            "verify",
            request.correlation_id,  # type: ignore[attr-defined]
            "succeeded",
            "verification completed",
            {
                "report": report.to_mapping(),
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
