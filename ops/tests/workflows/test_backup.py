from __future__ import annotations

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.backup import run_backup
from tests.support.environments import environment_config


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP = "backup-0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.validate_operational_preflight",
        lambda *_args: None,
    )


def test_backup_requests_discovery_then_final_backup_and_merges_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping discovery or omitting final facts would lose the controller's backup boundary."""

    config = environment_config()
    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        if request.operation == "discover":
            return HostResult(2, "discover", request.correlation_id, "succeeded", "observed", {"selected_release_id": RELEASE}, ("discovery warning",))
        return HostResult(
            2,
            "backup",
            request.correlation_id,
            "succeeded",
            "completed",
            {
                "backup_id": BACKUP,
                "dump_path": f"/var/backups/taskman/{BACKUP}.dump",
                "size_bytes": 1024,
                "source_database_size_bytes": 2048,
                "selected_release_id": RELEASE,
                "service_state": "running",
                "database_state": "ready",
            },
            ("backup warning",),
        )

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", invoke)

    result = run_backup(object(), config)

    assert [request.operation for request in requests] == ["discover", "backup"]
    assert requests[1].expected_state == {}
    assert requests[1].parameters == {
        "credentials_path": "/etc/taskman/pgpass",
        "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman_prod"},
        "purpose": "scheduled",
    }
    assert result.stage == "backed-up"
    assert result.exit_status is ExitStatus.OK
    assert result.facts["backup_id"] == BACKUP
    assert result.warnings == ("discovery warning", "backup warning")


def test_backup_refuses_preflight_before_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.validate_operational_preflight",
        lambda *_args: (_ for _ in ()).throw(
            OpsError(ExitStatus.REMOTE_PREFLIGHT, "preflight", "unsafe")
        ),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.run_request",
        lambda *_args: pytest.fail("preflight refusal must precede discovery"),
    )

    result = run_backup(object(), environment_config())

    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.stage == "preflight-failed"


def test_backup_maps_final_retryable_result_to_backup_exit_and_preserves_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discarding planning warnings or changing BACKUP classification hides actionable final state."""

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        if request.operation == "discover":
            return HostResult(2, "discover", request.correlation_id, "succeeded", "observed", {"selected_release_id": RELEASE}, ("discovery warning",))
        return HostResult(2, "backup", request.correlation_id, "retryable", "rerun backup", {}, ("backup warning",))

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", invoke)

    result = run_backup(object(), environment_config())

    assert result.stage == "backup-failed"
    assert result.exit_status is ExitStatus.BACKUP
    assert result.warnings == ("discovery warning", "backup warning")
