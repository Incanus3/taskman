from __future__ import annotations

import pytest

from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.helper import result_error, run_request


CORRELATION = "op-0123456789abcdef0123456789abcdef"


def test_run_request_consumes_the_direct_correlated_host_result() -> None:
    """Keeping a transport wrapper would make the workflow reconstruct the final result."""

    request = HostRequest(
        2,
        "discover",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman"},
        {},
    )
    result = HostResult(2, "discover", CORRELATION, "succeeded", "observed", {}, ())

    returned = run_request(
        object(),
        request,
        package=object(),  # type: ignore[arg-type]
        invoker=lambda _remote, _package, _request: result,
    )

    assert returned is result


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
        (
            "deploy",
            "retryable",
            {
                "failed_boundary": "migration",
                "applied_migrations": (20260905120000,),
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
    result = HostResult(2, operation, CORRELATION, outcome, "not completed", state, ())

    error = result_error(result)

    assert error.status is status
    assert getattr(error, "state") == state


def test_result_error_does_not_infer_migration_from_an_unobserved_boundary() -> None:
    """A legacy stage label alone must not turn a retry into migration exit 7."""

    result = HostResult(
        2,
        "deploy",
        CORRELATION,
        "retryable",
        "not completed",
        {"failed_boundary": "migration"},
        (),
    )

    assert result_error(result).status is ExitStatus.RELEASE
