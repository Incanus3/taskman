"""Public restore planning over final helper facts."""

from __future__ import annotations

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.restore import restore
from taskman_ops.workflows.verification_results import (
    CheckStatus,
    VerificationCheck,
    VerificationReport,
)
from tests.test_config import valid_environment


CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
TARGET = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP = "backup-cccccccccccccccccccccccccccccccc"
SAFETY_BACKUP = "backup-dddddddddddddddddddddddddddddddd"
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
            "backups": (
                {
                    "backup_id": BACKUP,
                    "source_release_id": TARGET,
                    "source_database_size_bytes": 1024,
                },
            ),
        },
        ("discovery warning",),
    )


def test_restore_dry_run_confirms_the_exact_backup_without_an_inspection_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final restore procedure has no separate inspect/recovery plane."""

    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        return _discovery(request)

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", invoke)

    outcome = restore(object(), _config(), BACKUP, dry_run=True)

    assert outcome.exit_status is ExitStatus.OK
    assert outcome.stage == "planned"
    assert outcome.facts["backup_id"] == BACKUP
    assert outcome.facts["intended_release_id"] == TARGET
    assert outcome.facts["typed_confirmation"] == "restore production " + BACKUP
    assert [request.operation for request in requests] == ["discover"]


def test_restore_sends_the_confirmed_source_and_returns_final_database_release_and_service_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restore result must bind the selected release to the exact backup confirmation."""

    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        if request.operation == "discover":
            return _discovery(request)
        return HostResult(
            2,
            "restore",
            request.correlation_id,
            "succeeded",
            "restore converged",
            {
                "changed": True,
                "backup_id": BACKUP,
                "pre_restore_backup_id": SAFETY_BACKUP,
                "current_release_id": CURRENT,
                "intended_release_id": TARGET,
                "selected_release_id": TARGET,
                "service_state": "running",
                "database_state": "restored",
                "report": _report(TARGET),
            },
            ("restore warning",),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", invoke)

    outcome = restore(object(), _config(), BACKUP, confirm=lambda plan: plan["backup_id"] == BACKUP)

    assert [request.operation for request in requests] == ["discover", "restore"]
    request = requests[-1]
    assert request.expected_state == {"selected_release_id": CURRENT, "backup_id": BACKUP}
    assert request.parameters["backup_id"] == BACKUP
    assert set(request.parameters) == {"backup_id", "credentials_path", "database", "verification"}
    assert outcome.exit_status is ExitStatus.OK
    assert outcome.stage == "restored"
    assert outcome.facts["selected_release_id"] == TARGET
    assert outcome.facts["database_state"] == "restored"
    assert outcome.warnings == ("discovery warning", "restore warning")


def test_restore_refuses_success_without_fresh_target_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publishing a restore without readiness proof would expose an unverified service."""

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        if request.operation == "discover":
            return _discovery(request)
        return HostResult(
            2,
            "restore",
            request.correlation_id,
            "succeeded",
            "restore converged",
            {
                "changed": True,
                "backup_id": BACKUP,
                "pre_restore_backup_id": SAFETY_BACKUP,
                "current_release_id": CURRENT,
                "intended_release_id": TARGET,
                "selected_release_id": TARGET,
                "service_state": "running",
                "database_state": "restored",
                "report": {},
            },
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", invoke)

    outcome = restore(object(), _config(), BACKUP, confirm=lambda _plan: True)

    assert outcome.exit_status is ExitStatus.SAFETY
    assert outcome.stage == "safety-refused"
