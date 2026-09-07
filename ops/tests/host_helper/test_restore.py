from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.host_protocol import HostRequest


def test_restore_bridge_never_projects_recovery_actions() -> None:
    request = HostRequest(2, "restore", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "restore", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "failed", "restore", ("restore",), {"recovery_id": "recovery-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, {}, {}, ("/private",), ("recover",), ())

    result = project_result(request, private)

    assert result.outcome == "manual"
    assert result.state == {"changed": True, "failed_boundary": "restore"}
