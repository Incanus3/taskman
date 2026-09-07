from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.host_protocol import HostRequest


def test_discovery_bridge_flattens_selected_public_facts() -> None:
    request = HostRequest(2, "discover", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "discover", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "discovered", (), {"state": "empty", "records": {"releases": [], "backups": [], "activations": []}}, {}, {}, (), (), ())

    assert project_result(request, private).state == {"host_kind": "empty", "releases": (), "backups": (), "activations": ()}
