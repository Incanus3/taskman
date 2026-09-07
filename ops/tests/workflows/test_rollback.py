"""Public rollback planning over final helper facts."""

from __future__ import annotations

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.rollback import rollback
from taskman_ops.workflows.verification_results import (
    CheckStatus,
    VerificationCheck,
    VerificationReport,
)
from tests.test_config import valid_environment


CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
TARGET = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP = "backup-cccccccccccccccccccccccccccccccc"
_CHECKS = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(name="production"))


def _report(release_id: str) -> dict[str, object]:
    return VerificationReport(
        ExitStatus.OK,
        release_id,
        release_id,
        tuple(VerificationCheck(name, CheckStatus.PASSED, "passed") for name in _CHECKS),
        None,
    ).to_mapping()


def _discovery(request: HostRequest) -> HostResult:
    return HostResult(
        2,
        "discover",
        request.correlation_id,
        "succeeded",
        "observed",
        {
            "selected_release_id": CURRENT,
            "releases": ({"release_id": CURRENT}, {"release_id": TARGET}),
            "backups": (),
            "selections": (),
        },
        ("discovery warning",),
    )


def test_rollback_dry_run_presents_the_exact_target_without_a_mutating_helper_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dry run that reaches rollback could select a release despite no confirmation."""

    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        return _discovery(request)

    monkeypatch.setattr("taskman_ops.workflows.rollback.run_request", invoke)

    outcome = rollback(object(), _config(), TARGET, dry_run=True)

    assert outcome.exit_status is ExitStatus.OK
    assert outcome.stage == "planned"
    assert outcome.facts["current_release_id"] == CURRENT
    assert outcome.facts["target_release_id"] == TARGET
    assert outcome.facts["typed_confirmation"] == f"rollback production {TARGET}"
    assert [request.operation for request in requests] == ["discover"]


def test_rollback_confirms_the_discovered_target_and_exposes_only_final_public_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing the target after confirmation or exposing recovery metadata is unsafe."""

    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        if request.operation == "discover":
            return _discovery(request)
        return HostResult(
            2,
            "rollback",
            request.correlation_id,
            "succeeded",
            "rollback converged",
            {
                "changed": True,
                "previous_release_id": CURRENT,
                "target_release_id": TARGET,
                "selected_release_id": TARGET,
                "backup_id": BACKUP,
                "service_state": "running",
                "database_state": "unchanged",
                "report": _report(TARGET),
            },
            ("rollback warning",),
        )

    monkeypatch.setattr("taskman_ops.workflows.rollback.run_request", invoke)

    outcome = rollback(object(), _config(), TARGET, confirm=lambda plan: plan["target_release_id"] == TARGET)

    assert [request.operation for request in requests] == ["discover", "rollback"]
    request = requests[-1]
    assert request.expected_state == {"selected_release_id": CURRENT}
    assert request.parameters["target_release_id"] == TARGET
    assert set(request.parameters) == {"target_release_id", "credentials_path", "database", "verification"}
    assert outcome.exit_status is ExitStatus.OK
    assert outcome.stage == "rolled-back"
    assert outcome.facts == {
        "changed": True,
        "previous_release_id": CURRENT,
        "target_release_id": TARGET,
        "selected_release_id": TARGET,
        "backup_id": BACKUP,
        "service_state": "running",
        "database_state": "unchanged",
        "verification": _report(TARGET),
    }
    assert outcome.warnings == ("discovery warning", "rollback warning")


def test_rollback_preserves_final_manual_warning_and_release_exit_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manual state must not be misreported as completed rollback evidence."""

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        if request.operation == "discover":
            return _discovery(request)
        return HostResult(
            2,
            "rollback",
            request.correlation_id,
            "manual",
            "rollback state is contradictory",
            {"selected_release_id": TARGET, "failed_boundary": "selection"},
            ("manual warning",),
        )

    monkeypatch.setattr("taskman_ops.workflows.rollback.run_request", invoke)

    outcome = rollback(object(), _config(), TARGET, confirm=lambda _plan: True)

    assert outcome.exit_status is ExitStatus.RELEASE
    assert outcome.stage == "selection-failed"
    assert outcome.facts["selected_release_id"] == TARGET
    assert outcome.warnings == ("discovery warning", "manual warning")
