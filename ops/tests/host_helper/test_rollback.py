from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.host_protocol import HostRequest


def test_rollback_bridge_projects_selected_release() -> None:
    request = HostRequest(2, "rollback", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "rollback", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "records", ("select",), {"selected_release_id": "2026.9.6-feedface"}, {}, {}, (), (), ())

    assert project_result(request, private).state == {"changed": True, "selected_release_id": "2026.9.6-feedface"}
