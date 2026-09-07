from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.host_protocol import HostRequest


def test_deploy_bridge_does_not_expose_the_private_operation_identifier() -> None:
    request = HostRequest(2, "deploy", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "deploy", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "deploy", ("deploy",), {"selected_release_id": "2026.9.7-deadbeef"}, {}, {}, (), (), ())

    result = project_result(request, private)

    assert result.state == {"changed": True, "selected_release_id": "2026.9.7-deadbeef"}
    assert "aaaaaaaa" not in repr(result.to_mapping())
