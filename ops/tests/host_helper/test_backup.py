from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.host_protocol import HostRequest


def test_backup_bridge_projects_backup_facts_only() -> None:
    request = HostRequest(2, "backup", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "backup", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "backup", ("backup",), {"backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "reason": "scheduled"}, {}, {}, (), (), ())

    assert project_result(request, private).state == {"changed": True, "backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "reason": "scheduled"}
