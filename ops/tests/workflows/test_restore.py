"""Guarded restore contracts over the final helper result."""

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


INTENDED = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
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


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
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


def result(request: HostRequest, state: dict[str, object]) -> HostResult:
    return HostResult(
        2,
        request.operation,
        request.correlation_id,
        "succeeded",
        "completed",
        state,
        (),
    )


def discovery(request: HostRequest) -> HostResult:
    return result(
        request,
        {
            "selected_release_id": CURRENT,
            "releases": (),
            "backups": (
                {
                    "backup_id": BACKUP,
                    "dump_sha256": "e" * 64,
                    "source_release_id": INTENDED,
                    "migration_versions": (20260905120000,),
                    "source_database_size_bytes": 1,
                },
            ),
            "release_migrations": (
                {
                    "release_id": INTENDED,
                    "migrations": (
                        {
                            "filename": "20260905120000_create_tasks.exs",
                            "sha256": "e" * 64,
                        },
                    ),
                },
            ),
        },
    )


def inspection(request: HostRequest) -> HostResult:
    return result(
        request,
        {
            "changed": False,
            "backup_id": BACKUP,
            "dump_path": f"/var/backups/taskman/{BACKUP}.dump",
            "dump_size_bytes": 1,
            "source_database_size_bytes": 1,
            "current_release_id": CURRENT,
            "intended_release_id": INTENDED,
            "dump_validated": True,
        },
    )


def verified(release_id: str) -> dict[str, object]:
    return VerificationReport(
        ExitStatus.OK,
        release_id,
        release_id,
        tuple(VerificationCheck(name, CheckStatus.PASSED, "passed") for name in _CHECKS),
        None,
    ).to_mapping()


def test_restore_dry_run_uses_projected_historical_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore planning must reach inspection using discovery's real history projection."""

    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        if request.operation == "discover":
            return discovery(request)
        return inspection(request)

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", invoke)

    planned = restore(object(), config(), BACKUP, dry_run=True)

    assert planned.exit_status is ExitStatus.OK
    assert planned.stage == "planned"
    assert [request.operation for request in requests] == ["discover", "restore"]
    assert requests[-1].parameters["expected_migration_versions"] == (
        "20260905120000",
    )


def test_restore_refuses_unavailable_history_without_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded discovery refusal cannot be mistaken for restore authority."""

    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        return HostResult(
            2,
            request.operation,
            request.correlation_id,
            "refused",
            "helper discovery history exceeds protocol bounds",
            {"history": "unavailable"},
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", invoke)

    outcome = restore(object(), config(), BACKUP, dry_run=True)

    assert outcome.exit_status is ExitStatus.SAFETY
    assert outcome.stage == "safety-refused"
    assert [request.operation for request in requests] == ["discover"]


def test_restore_refuses_success_without_a_verified_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore may not publish restored state without fresh readiness evidence."""

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        if request.operation == "discover":
            return discovery(request)
        if request.parameters["action"] == "inspect":
            return inspection(request)
        return result(
            request,
            {
                "changed": True,
                "backup_id": BACKUP,
                "pre_restore_backup_id": "backup-dddddddddddddddddddddddddddddddd",
                "current_release_id": CURRENT,
                "intended_release_id": INTENDED,
                "selected_release_id": INTENDED,
                "service_state": "active",
                "database_state": "restored-promoted",
                "restore_recorded": True,
            },
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", invoke)

    outcome = restore(object(), config(), BACKUP, confirm=lambda _plan: True)

    assert outcome.exit_status is ExitStatus.SAFETY
    assert outcome.stage == "safety-refused"


def test_restore_publishes_restored_only_after_verifying_the_intended_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore success binds its published selection to fresh readiness proof."""

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        if request.operation == "discover":
            return discovery(request)
        if request.parameters["action"] == "inspect":
            return inspection(request)
        return result(
            request,
            {
                "changed": True,
                "backup_id": BACKUP,
                "pre_restore_backup_id": "backup-dddddddddddddddddddddddddddddddd",
                "current_release_id": CURRENT,
                "intended_release_id": INTENDED,
                "selected_release_id": INTENDED,
                "service_state": "active",
                "database_state": "restored-promoted",
                "restore_recorded": True,
                "report": verified(INTENDED),
            },
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", invoke)

    outcome = restore(object(), config(), BACKUP, confirm=lambda _plan: True)

    assert outcome.exit_status is ExitStatus.OK
    assert outcome.stage == "restored"
    assert outcome.facts["verification"]["release_id"] == INTENDED
