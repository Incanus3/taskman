from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.helper import result_error


def test_read_only_lock_projection_maps_to_locked_exit_status() -> None:
    result = HostResult(
        2, "discover", "op-0123456789abcdef0123456789abcdef", "retryable", "locked",
        {"locked": True}, (),
    )

    assert result_error(result).stage == "discover"
