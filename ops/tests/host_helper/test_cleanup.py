from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.host_protocol import HostRequest


def test_cleanup_bridge_projects_the_confirmed_target_facts() -> None:
    request = HostRequest(2, "cleanup", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "cleanup", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "cleanup", (), {"targets": (), "removed": (), "recoverability": ()}, {}, {}, (), (), ())

    assert project_result(request, private).state == {"changed": False, "targets": (), "removed": (), "recoverability": ()}
