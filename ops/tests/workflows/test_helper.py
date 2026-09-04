from __future__ import annotations

from types import MappingProxyType

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.helper import merge_warnings, mutable, result_error, run_request


CORRELATION = "op-0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("groups, expected", (
    ((), ()),
    (((), ()), ()),
    ((("second", "first", "second"), (), ("first", "third")), ("second", "first", "third")),
    ((("Warning",), ("warning",)), ("Warning", "warning")),
))
def test_merge_warnings_preserves_first_occurrence_order(groups, expected) -> None:
    assert merge_warnings(*groups) == expected


def test_mutable_restores_nested_protocol_values_without_changing_source() -> None:
    item = MappingProxyType({"version": 42, "optional": None})
    source = MappingProxyType({"migrations": (item,), "ready": True})

    restored = mutable(source)

    assert restored == {"migrations": [{"version": 42, "optional": None}], "ready": True}
    restored["migrations"][0]["version"] = 43
    assert source["migrations"] == (item,)
    assert item["version"] == 42


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


def test_run_request_refuses_an_unrelated_injected_result() -> None:
    """The workflow is the single consumer that trusts a helper's final result."""

    request = HostRequest(
        2,
        "discover",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman"},
        {},
    )
    unrelated = HostResult(
        2,
        "verify",
        CORRELATION,
        "succeeded",
        "observed",
        {},
        (),
    )

    with pytest.raises(OpsError) as raised:
        run_request(
            object(),
            request,
            package=object(),  # type: ignore[arg-type]
            invoker=lambda _remote, _package, _request: unrelated,
        )

    assert raised.value.status is ExitStatus.SAFETY


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
