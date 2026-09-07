from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.restore import _success


def test_restore_success_does_not_require_recovery_artifacts_in_final_state() -> None:
    request = type(
        "Request",
        (),
        {
            "parameters": {"backup_id": "backup-0123456789abcdef0123456789abcdef"},
            "expected_state": {"current_release_id": "2026.9.7-deadbeef"},
        },
    )()
    result = HostResult(
        2, "restore", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {
            "changed": True,
            "backup_id": "backup-0123456789abcdef0123456789abcdef",
            "pre_restore_backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "current_release_id": "2026.9.7-deadbeef",
            "intended_release_id": "2026.9.6-feedface",
            "selected_release_id": "2026.9.6-feedface",
            "service_state": "active",
            "database_state": "restored-promoted",
            "restore_recorded": True,
        },
        (),
    )

    assert _success(result, request, "2026.9.6-feedface")["restore_recorded"] is True
