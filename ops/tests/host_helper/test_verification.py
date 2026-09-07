from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.host_protocol import HostRequest


def test_verify_bridge_projects_only_the_verification_report() -> None:
    request = HostRequest(2, "verify", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "verify", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "verification", (), {}, {}, {"readiness": "passed"}, (), (), ())

    assert project_result(request, private).state == {"report": {"readiness": "passed"}}
