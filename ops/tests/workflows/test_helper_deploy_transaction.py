"""Controller mapping of exact deployment mutation failures."""

from __future__ import annotations

from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION
from taskman_ops.workflows.helper import result_error


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64


def test_deployment_refusal_preserves_exact_final_observation_and_unknown_mutation() -> None:
    """A later lost reply cannot erase a potentially changed prior helper dispatch."""
    state = {
        "mutation_state": "unknown",
        "exit_code": 8,
        "failed_boundary": "service",
        "observations": {
            "selected_release_id": RELEASE,
            "last_successful_selection_id": "selection-" + "e" * 64 + ".json",
            "applied_migrations": [],
            "protected_backup_ids": [],
            "backup_protection_sha256": "1" * 64,
            "restore_target_sha256": None,
            "database_state": "unknown",
            "service_state": "unknown",
            "scheduled_backup_sha256": "2" * 64,
            "backup_timer_enabled": True,
            "backup_timer_state": "unknown",
        },
        "unavailable_fields": ["backup_timer_state", "database_state", "service_state"],
        "inspection_error": "inspection-failed",
        "report": None,
        "desired_release_id": RELEASE,
        "backup_id": None,
    }
    result = HostResult(
        PROTOCOL_VERSION, "deploy", "op-0123456789abcdef0123456789abcdef", "retryable", "lost reply", state, ()
    )

    error = result_error(result)

    assert error.status is ExitStatus.RELEASE
    assert error.changed is True
    assert error.state["mutation_state"] == "unknown"
    assert error.state["observations"]["selected_release_id"] == RELEASE
