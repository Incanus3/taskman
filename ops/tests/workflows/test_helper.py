from __future__ import annotations

import pytest

from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.helper import result_error


@pytest.mark.parametrize(
    ("operation", "outcome", "state", "status"),
    (
        (
            "deploy",
            "retryable",
            {
                "failed_boundary": "migration",
                "applied_migrations": (
                    {
                        "filename": "20260905120000_create_tasks.exs",
                        "sha256": "e" * 64,
                    },
                ),
            },
            ExitStatus.MIGRATION,
        ),
        ("deploy", "retryable", {"failed_boundary": "verification"}, ExitStatus.READINESS),
        ("restore", "retryable", {"failed_boundary": "verification"}, ExitStatus.READINESS),
        ("deploy", "manual", {"failed_boundary": "backup"}, ExitStatus.BACKUP),
        ("rollback", "retryable", {"failed_boundary": "backup"}, ExitStatus.BACKUP),
        ("restore", "manual", {"failed_boundary": "backup"}, ExitStatus.BACKUP),
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


def test_result_error_does_not_infer_migration_from_an_unobserved_boundary() -> None:
    """A legacy stage label alone must not turn a retry into migration exit 7."""

    result = HostResult(
        2,
        "deploy",
        "op-0123456789abcdef0123456789abcdef",
        "retryable",
        "not completed",
        {"failed_boundary": "migration"},
        (),
    )

    assert result_error(result).status is ExitStatus.RELEASE
