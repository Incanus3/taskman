from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.cleanup import _inspection


def test_cleanup_inspection_keeps_only_exact_final_targets() -> None:
    """Accepting recovery annotations would preserve removed cleanup machinery."""

    target = {
        "identifier": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
        "kind": "release",
        "path": "/opt/taskman/releases/0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
    }
    result = HostResult(
        2,
        "cleanup",
        "op-0123456789abcdef0123456789abcdef",
        "succeeded",
        "completed",
        {
            "selected_release_id": "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
            "targets": (target,),
            "service_state": "running",
            "database_state": "ready",
        },
        (),
    )

    plan = _inspection(result, "production")

    assert plan.targets == (target,)
    assert "recoverability" not in plan.targets[0]
