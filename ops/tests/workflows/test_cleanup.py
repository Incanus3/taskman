from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.cleanup import _inspection


def test_cleanup_inspection_consumes_final_target_projection() -> None:
    target = {
        "authority": "release", "identifier": "release-1", "kind": "release",
        "path": "/opt/taskman/releases/release-1", "recoverable": True,
    }
    result = HostResult(
        2, "cleanup", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {"changed": False, "targets": (target,), "removed": (), "recoverability": ()}, (),
    )

    plan = _inspection(result, {}, "test")

    assert plan.targets == (target,)
