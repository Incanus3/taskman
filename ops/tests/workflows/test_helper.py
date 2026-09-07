from __future__ import annotations

import pytest

from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.helper import result_error


@pytest.mark.parametrize(
    ("operation", "outcome", "state", "status"),
    (
        ("deploy", "retryable", {"failed_boundary": "migration"}, ExitStatus.MIGRATION),
        ("deploy", "retryable", {"failed_boundary": "verification"}, ExitStatus.READINESS),
        ("rollback", "retryable", {"failed_boundary": "release"}, ExitStatus.RELEASE),
        ("backup", "retryable", {}, ExitStatus.BACKUP),
        ("restore", "manual", {}, ExitStatus.RESTORE),
        ("cleanup", "retryable", {}, ExitStatus.SAFETY),
        ("verify", "retryable", {}, ExitStatus.READINESS),
        ("discover", "refused", {}, ExitStatus.SAFETY),
        ("backup", "refused", {"locked": True}, ExitStatus.LOCKED),
    ),
)
def test_result_error_maps_only_final_outcome_and_concise_state(
    operation: str, outcome: str, state: dict[str, object], status: ExitStatus
) -> None:
    result = HostResult(2, operation, "op-0123456789abcdef0123456789abcdef", outcome, "not completed", state, ())

    error = result_error(result)

    assert error.status is status
    assert getattr(error, "state") == state
