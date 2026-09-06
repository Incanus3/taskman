"""Controller contracts for helper-owned guarded restore."""

from __future__ import annotations

from typing import Any

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.helper_recovery import SUCCESSFUL_VERIFICATION_CHECKS
from taskman_ops.workflows.restore import restore


INTENDED = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP = "backup-" + "c" * 32


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


def _discovery() -> dict[str, object]:
    return {
        "records": {
            "releases": (), "adoptions": (),
            "activations": ({"candidate_release_id": CURRENT},),
            "backups": ({
                "backup_id": BACKUP, "current_release_id": INTENDED,
            },),
        },
        "manifests": {
            INTENDED: {
                "migrations": (
                    {"filename": "20260905120000_create_tasks.exs"},
                )
            }
        },
    }


def _inspection(
    request: Any, warnings: tuple[str, ...] = ()
) -> HostResult:
    return HostResult(
        1, "restore", request.operation_id, "succeeded", "restore-inspected", (),
        {
            "backup_id": BACKUP,
            "dump_path": f"/var/backups/taskman/{BACKUP}.dump",
            "dump_size_bytes": 17, "source_database_size_bytes": 1024,
            "current_release_id": CURRENT, "intended_release_id": INTENDED,
            "dump_validated": True,
        },
        {}, {"format": "custom", "validated": True}, (), (), warnings,
    )


def _verified() -> dict[str, object]:
    return {
        "schema_version": 1, "status": "ok", "exit_status": 0,
        "release_id": INTENDED, "expected_release_id": INTENDED,
        "checks": SUCCESSFUL_VERIFICATION_CHECKS, "next_action": None,
    }


def test_restore_validates_before_confirmation_then_uses_a_fresh_execute_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []
    confirmed_after_validation = False
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.discover_lifecycle",
        lambda *_args: (_discovery(), ("discovery cleanup warning",)),
    )

    def run(_remote: object, request: Any) -> HostResult:
        requests.append(request)
        if request.parameters["action"] == "inspect":
            return _inspection(request, ("inspection cleanup warning",))
        token = request.operation_id.removeprefix("op-")
        return HostResult(
            1, "restore", request.operation_id, "succeeded", "records",
            ("backup", "stop", "restore", "validation", "swap", "selection",
             "start", "verification", "records"),
            {
                "backup_id": BACKUP, "pre_restore_backup_id": f"backup-{token}",
                "current_release_id": CURRENT, "intended_release_id": INTENDED,
                "selected_release_id": INTENDED, "recovery_id": f"recovery-{token}",
                "recovery_database": f"taskman_recovery_{token}",
                "service_state": "active", "database_state": "restored-promoted",
                "restore_recorded": True,
            },
            {}, _verified(), (),
            ("retain recovery database until accepted",),
            ("execution cleanup warning",),
        )

    def confirm(_plan: object) -> bool:
        nonlocal confirmed_after_validation
        confirmed_after_validation = len(requests) == 1
        return True

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", run)
    result = restore(object(), _config(), BACKUP, confirm=confirm)

    assert result.stage == "restored"
    assert confirmed_after_validation is True
    assert [item.parameters["action"] for item in requests] == ["inspect", "execute"]
    assert requests[0].operation_id != requests[1].operation_id
    assert result.warnings == (
        "discovery cleanup warning",
        "inspection cleanup warning",
        "execution cleanup warning",
    )


def test_restore_cancellation_never_sends_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.run_request",
        lambda _remote, request: requests.append(request) or _inspection(request),
    )

    result = restore(object(), _config(), BACKUP, confirm=lambda _plan: False)

    assert result.stage == "confirmation-cancelled"
    assert len(requests) == 1


def test_restore_preserves_swap_failure_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        if request.parameters["action"] == "inspect":
            return _inspection(request)
        return HostResult(
            1, "restore", request.operation_id, "failed", "swap",
            ("backup", "stop", "restore", "validation", "swap"),
            {
                "backup_id": BACKUP, "pre_restore_backup_id": "backup-" + "d" * 32,
                "current_release_id": CURRENT, "intended_release_id": INTENDED,
                "selected_release_id": CURRENT,
                "recovery_id": "recovery-" + "d" * 32,
                "recovery_database": "taskman_recovery_" + "d" * 32,
                "service_state": "stopped", "database_state": "unknown",
                "restore_recorded": False,
            },
            {}, {}, ("/database/taskman_recovery_" + "d" * 32,),
            ("leave taskman.service stopped",),
            ("restore operation did not complete",),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", run)
    result = restore(object(), _config(), BACKUP, confirm=lambda _plan: True)

    assert result.stage == "swap-failed"
    assert result.changed is True
    assert result.facts["database_state"] == "unknown"
    assert result.facts["recovery_database"] == "taskman_recovery_" + "d" * 32
    assert result.facts["service_state"] == "stopped"
    assert result.facts["recovery_commands"] == ("leave taskman.service stopped",)


def test_restore_preserves_unknown_service_state_from_failed_live_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The controller must not turn failed service evidence back into active."""

    monkeypatch.setattr(
        "taskman_ops.workflows.restore.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        if request.parameters["action"] == "inspect":
            return _inspection(request)
        token = request.operation_id.removeprefix("op-")
        return HostResult(
            1,
            "restore",
            request.operation_id,
            "failed",
            "verification",
            (),
            {
                "backup_id": BACKUP,
                "pre_restore_backup_id": f"backup-{token}",
                "current_release_id": CURRENT,
                "intended_release_id": INTENDED,
                "selected_release_id": INTENDED,
                "recovery_id": f"recovery-{token}",
                "recovery_database": f"taskman_recovery_{token}",
                "service_state": "unknown",
                "database_state": "restored-promoted",
                "restore_recorded": True,
            },
            {},
            {
                "schema_version": 1,
                "status": "failed",
                "exit_status": 8,
                "release_id": INTENDED,
                "expected_release_id": INTENDED,
                "checks": ({
                    "schema_version": 1,
                    "name": "taskman-service",
                    "status": "failed",
                    "summary": "taskman.service is not active with a usable MainPID",
                },),
                "next_action": "inspect",
            },
            (f"/database/taskman_recovery_{token}",),
            (f"retain taskman_recovery_{token}",),
            ("restore operation did not complete",),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", run)

    result = restore(object(), _config(), BACKUP, confirm=lambda _plan: True)

    assert result.stage == "verification-failed"
    assert result.changed is False
    assert result.facts["service_state"] == "unknown"
    assert result.facts["verification"]["checks"][0]["status"] == "failed"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        (
            "dump_path",
            f"/var/backups/taskman/nested/{BACKUP}.dump",
        ),
        ("intended_release_id", CURRENT),
    ),
)
def test_restore_rejects_inspection_that_does_not_match_discovered_backup_authority(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    """The inspected dump and intended release must be the exact planned backup."""

    monkeypatch.setattr(
        "taskman_ops.workflows.restore.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        if request.parameters["action"] == "inspect":
            result = _inspection(request)
            lifecycle = {**result.lifecycle, field: value}
            return HostResult(
                result.protocol_version,
                result.operation,
                result.operation_id,
                result.outcome,
                result.stage,
                result.changed_stages,
                lifecycle,
                result.runtime_state,
                result.verification,
                result.residue_paths,
                result.recovery_actions,
                result.warnings,
            )
        token = request.operation_id.removeprefix("op-")
        return HostResult(
            1,
            "restore",
            request.operation_id,
            "succeeded",
            "records",
            (
                "backup",
                "stop",
                "restore",
                "validation",
                "swap",
                "selection",
                "start",
                "verification",
                "records",
            ),
            {
                "backup_id": BACKUP,
                "pre_restore_backup_id": f"backup-{token}",
                "current_release_id": CURRENT,
                "intended_release_id": INTENDED,
                "selected_release_id": INTENDED,
                "recovery_id": f"recovery-{token}",
                "recovery_database": f"taskman_recovery_{token}",
                "service_state": "active",
                "database_state": "restored-promoted",
                "restore_recorded": True,
            },
            {},
            _verified(),
            (),
            (),
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", run)

    result = restore(object(), _config(), BACKUP, confirm=lambda _plan: True)

    assert result.stage == "safety-refused"
    assert result.changed is False


def test_restore_no_change_requires_fresh_successful_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact rerun may not claim success from lifecycle records alone."""

    monkeypatch.setattr(
        "taskman_ops.workflows.restore.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        if request.parameters["action"] == "inspect":
            return _inspection(request)
        token = request.operation_id.removeprefix("op-")
        return HostResult(
            1,
            "restore",
            request.operation_id,
            "no_change",
            "already-restored",
            (),
            {
                "backup_id": BACKUP,
                "pre_restore_backup_id": f"backup-{token}",
                "current_release_id": CURRENT,
                "intended_release_id": INTENDED,
                "selected_release_id": INTENDED,
                "recovery_id": f"recovery-{token}",
                "recovery_database": f"taskman_recovery_{token}",
                "service_state": "active",
                "database_state": "restored-promoted",
                "restore_recorded": True,
            },
            {},
            {},
            (),
            (),
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", run)

    result = restore(object(), _config(), BACKUP, confirm=lambda _plan: True)

    assert result.stage == "safety-refused"
    assert result.changed is False


def test_restore_accepts_correlated_record_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publishing the missing recovery record is a truthful changed success."""

    monkeypatch.setattr(
        "taskman_ops.workflows.restore.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        if request.parameters["action"] == "inspect":
            return _inspection(request)
        token = request.operation_id.removeprefix("op-")
        return HostResult(
            1,
            "restore",
            request.operation_id,
            "succeeded",
            "records-finalized",
            ("records",),
            {
                "backup_id": BACKUP,
                "pre_restore_backup_id": f"backup-{token}",
                "current_release_id": CURRENT,
                "intended_release_id": INTENDED,
                "selected_release_id": INTENDED,
                "recovery_id": f"recovery-{token}",
                "recovery_database": f"taskman_recovery_{token}",
                "service_state": "active",
                "database_state": "restored-promoted",
                "restore_recorded": True,
            },
            {},
            _verified(),
            (),
            ("retain recovery database until accepted",),
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", run)

    result = restore(object(), _config(), BACKUP, confirm=lambda _plan: True)

    assert result.stage == "restored"
    assert result.changed is True
    assert result.facts["changed_stages"] == ("records",)
