from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.helper import result_error


CORRELATION = "op-0123456789abcdef0123456789abcdef"


def test_read_only_lock_projection_maps_to_locked_exit_status() -> None:
    result = HostResult(3, "discover", CORRELATION, "retryable", "lock unavailable", {"locked": True}, ())

    error = result_error(result)

    assert error.status is ExitStatus.LOCKED
    assert error.stage == "discover"


def test_read_only_refusal_maps_to_safety_without_reconstructing_lifecycle() -> None:
    result = HostResult(3, "list_releases", CORRELATION, "refused", "state is ambiguous", {}, ())

    error = result_error(result)

    assert error.status is ExitStatus.SAFETY
    assert error.stage == "list_releases"


def test_read_only_warnings_remain_bounded_but_do_not_invalidate_state() -> None:
    result = HostResult(
        3,
        "list_backups",
        CORRELATION,
        "succeeded",
        "state observed",
        {"records": (), "inventory_sha256": "a" * 64, "next_cursor": None},
        tuple(f"warning-{index}" for index in range(4)),
    )

    assert tuple(result.warnings) == tuple(f"warning-{index}" for index in range(4))


def test_invalid_inventory_cursor_maps_to_invalid_input_exit_status() -> None:
    """A local pagination syntax error is not ambiguous host authority."""
    result = HostResult(
        3,
        "list_releases",
        CORRELATION,
        "refused",
        "invalid inventory cursor",
        {"invalid_request": True},
        (),
    )

    error = result_error(result)

    assert error.status is ExitStatus.INVALID
