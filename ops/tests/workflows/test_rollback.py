from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.rollback import _validate_success


def test_rollback_success_consumes_final_projection() -> None:
    request = type(
        "Request",
        (),
        {
            "expected_state": {"current_release_id": "2026.9.7-deadbeef"},
            "parameters": {"target_release_id": "2026.9.6-feedface"},
        },
    )()
    result = HostResult(
        2, "rollback", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {
            "changed": True,
            "previous_release_id": "2026.9.7-deadbeef",
            "target_release_id": "2026.9.6-feedface",
            "selected_release_id": "2026.9.6-feedface",
            "backup_id": "backup-0123456789abcdef0123456789abcdef",
            "activation_id": "activation-0123456789abcdef0123456789abcdef",
            "service_state": "active",
            "database_state": "unchanged",
            "activation_recorded": True,
        },
        (),
    )

    assert _validate_success(result, request)["selected_release_id"] == "2026.9.6-feedface"
