"""Controller contracts for helper-owned rollback."""

from __future__ import annotations

from typing import Any

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.helper_recovery import SUCCESSFUL_VERIFICATION_CHECKS
from taskman_ops.workflows.rollback import rollback


OLD = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate({
        "name": "production", "ssh_host": "203.0.113.10", "ssh_port": 22,
        "ssh_user": "deployer", "host_key_fingerprint": "SHA256:" + "A" * 43,
        "public_hostname": "taskman.acme.tld", "public_ipv4": "203.0.113.10",
        "target_os": "ubuntu26.04", "architecture": "amd64",
        "application_port": 4000, "distribution_port": 6789,
        "database_name": "taskman_prod", "database_role": "taskman",
        "mail_from": "no-reply@acme.tld",
    })


def _release(identifier: str, previous: str | None) -> dict[str, object]:
    return {
        "schema_version": 1, "release_id": identifier,
        "artifact_sha256": "a" * 64, "installed_at": "2026-09-05T12:00:00Z",
        "activated_at": "2026-09-05T12:00:00Z",
        "previous_release_id": previous, "backup_id": None,
        "migration_policy": "no-change",
    }


def _discovery(policy: str = "backward-compatible") -> dict[str, object]:
    return {
        "records": {
            "releases": (_release(OLD, None), _release(CURRENT, OLD)),
            "activations": (
                {
                    "schema_version": 1, "activation_id": "activation-" + "a" * 32,
                    "previous_release_id": None, "candidate_release_id": OLD,
                    "activated_at": "2026-09-05T12:00:00Z",
                    "backup_id": None, "migration_policy": "no-change",
                },
                {
                    "schema_version": 1, "activation_id": "activation-" + "b" * 32,
                    "previous_release_id": OLD, "candidate_release_id": CURRENT,
                    "activated_at": "2026-09-05T12:01:00Z",
                    "backup_id": "backup-" + "b" * 32,
                    "migration_policy": policy,
                },
            ),
            "backups": (), "adoptions": (),
        }
    }


def _verified(release: str) -> dict[str, object]:
    return {
        "schema_version": 1, "status": "ok", "exit_status": 0,
        "release_id": release, "expected_release_id": release,
        "checks": SUCCESSFUL_VERIFICATION_CHECKS, "next_action": None,
    }


def test_rollback_confirms_then_sends_one_complete_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        requests.append(request)
        token = request.operation_id.removeprefix("op-")
        return HostResult(
            1, "rollback", request.operation_id, "succeeded", "records",
            ("backup", "stop", "selection", "start", "verification", "records"),
            {
                "previous_release_id": CURRENT, "target_release_id": OLD,
                "selected_release_id": OLD, "backup_id": f"backup-{token}",
                "activation_id": f"activation-{token}", "service_state": "active",
                "database_state": "unchanged", "activation_recorded": True,
            },
            {}, _verified(OLD), (), (), (),
        )

    monkeypatch.setattr("taskman_ops.workflows.rollback.run_request", run)
    result = rollback(object(), _config(), OLD, confirm=lambda _plan: True)

    assert result.stage == "rolled-back"
    assert len(requests) == 1
    assert requests[0].parameters["target_release_id"] == OLD
    assert set(requests[0].parameters) == {
        "target_release_id", "credentials_path", "database", "verification"
    }


def test_rollback_refuses_restore_required_edge_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confirmed = False
    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.discover_lifecycle",
        lambda *_args: (_discovery("restore-required"), ()),
    )

    def confirm(_plan: object) -> bool:
        nonlocal confirmed
        confirmed = True
        return True

    result = rollback(object(), _config(), OLD, confirm=confirm)

    assert result.stage == "safety-refused"
    assert confirmed is False


def test_rollback_preserves_bounded_failure_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.run_request",
        lambda _remote, request: HostResult(
            1, "rollback", request.operation_id, "failed", "verification",
            ("backup", "stop", "selection", "start"),
            {
                "previous_release_id": CURRENT, "target_release_id": OLD,
                "selected_release_id": OLD, "backup_id": "backup-" + "c" * 32,
                "activation_id": None, "service_state": "active",
                "database_state": "unchanged", "activation_recorded": False,
            },
            {}, {"status": "failed"}, ("/safe/residue",),
            ("inspect rollback state",), ("rollback operation did not complete",),
        ),
    )

    result = rollback(object(), _config(), OLD, confirm=lambda _plan: True)

    assert result.stage == "verification-failed"
    assert result.changed is True
    assert result.facts["selected_release_id"] == OLD
    assert result.facts["residue_paths"] == ("/safe/residue",)


def test_rollback_no_change_requires_fresh_successful_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An activation record cannot substitute for current runtime verification."""

    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        token = request.operation_id.removeprefix("op-")
        return HostResult(
            1,
            "rollback",
            request.operation_id,
            "no_change",
            "already-current",
            (),
            {
                "previous_release_id": CURRENT,
                "target_release_id": OLD,
                "selected_release_id": OLD,
                "backup_id": f"backup-{token}",
                "activation_id": f"activation-{token}",
                "service_state": "active",
                "database_state": "unchanged",
                "activation_recorded": True,
            },
            {},
            {},
            (),
            (),
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.rollback.run_request", run)

    result = rollback(object(), _config(), OLD, confirm=lambda _plan: True)

    assert result.stage == "safety-refused"
    assert result.changed is False
