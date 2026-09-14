from __future__ import annotations

from types import MappingProxyType
import hashlib

import pytest

from taskman_ops.errors import ExitStatus, HelperTransportError, OpsError
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.output import WorkflowResult
from taskman_ops.workflows.helper import (
    aggregate_mutation_state,
    merge_warnings,
    mutable,
    mutation_result_facts,
    result_error,
    run_request,
    temporary_scheduled_backup_helper_package,
)


CORRELATION = "op-0123456789abcdef0123456789abcdef"
RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
SELECTION = "selection-" + "c" * 64 + ".json"


def test_temporary_scheduled_backup_helper_package_is_the_uploadable_persistent_artifact() -> None:
    """Using the transient helper archive here would replace the timer with the wrong entrypoint."""

    with temporary_scheduled_backup_helper_package() as package:
        path = package.path
        assert path.name == "taskman-backup.pyz"
        assert package.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    assert not path.exists()


def _mutation_observations() -> dict[str, object]:
    return {
        "selected_release_id": RELEASE,
        "last_successful_selection_id": SELECTION,
        "applied_migrations": (),
        "protected_backup_ids": (),
        "backup_protection_sha256": "d" * 64,
        "restore_target_sha256": None,
        "database_state": "ready",
        "service_state": "running",
        "scheduled_backup_sha256": "e" * 64,
        "backup_timer_enabled": True,
        "backup_timer_state": "active",
    }


def _failed_mutation_state() -> dict[str, object]:
    return {
        "mutation_state": "changed",
        "exit_code": 8,
        "failed_boundary": "history",
        "observations": _mutation_observations(),
        "unavailable_fields": (),
        "inspection_error": None,
        "report": None,
        "desired_release_id": RELEASE,
        "backup_id": None,
    }


def _failed_report() -> dict[str, object]:
    names = (
        "taskman-service",
        "release-identity",
        "caddy-service",
        "listener-topology",
        "startup-journal",
        "local-readiness",
    )
    return {
        "schema_version": 1,
        "status": "failed",
        "exit_status": 9,
        "release_id": RELEASE,
        "expected_release_id": RELEASE,
        "checks": [
            {
                "schema_version": 1,
                "name": name,
                "status": "failed" if name == "local-readiness" else "passed",
                "summary": "checked",
            }
            for name in names
        ],
        "next_action": "inspect the fixed verification summaries and correct the reported host state before retrying",
    }


def _exact_failure_state(operation: str, boundary: str, exit_code: int) -> dict[str, object]:
    if operation == "cleanup":
        return {
            "mutation_state": "unchanged",
            "exit_code": exit_code,
            "failed_boundary": boundary,
            "observations": {
                "selected_release_id": RELEASE,
                "last_successful_selection_id": SELECTION,
                "backup_protection_sha256": "d" * 64,
                "restore_target_sha256": None,
            },
            "unavailable_fields": (),
            "inspection_error": None,
            "report": None,
            "completed_targets": (),
        }
    observed = _mutation_observations()
    state: dict[str, object] = {
        "mutation_state": "changed",
        "exit_code": exit_code,
        "failed_boundary": boundary,
        "observations": observed,
        "unavailable_fields": (),
        "inspection_error": None,
        "report": _failed_report() if boundary == "verification" else None,
        "desired_release_id": RELEASE,
        "backup_id": None,
    }
    if operation == "restore":
        observed["restore_database_state"] = {
            "canonical": {
                "oid": 42,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": (),
            },
            "temporary": None,
            "retired": None,
        }
        state["pre_restore_backup_id"] = None
    return state


def _deploy_request() -> HostRequest:
    return HostRequest(
        3,
        "deploy",
        CORRELATION,
        {"selected_release_id": None},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"candidate_release_id": RELEASE},
    )


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
        3,
        "discover",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman"},
        {},
    )
    result = HostResult(3, "discover", CORRELATION, "succeeded", "observed", {}, ())

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
        3,
        "discover",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman"},
        {},
    )
    unrelated = HostResult(
        3,
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
            _exact_failure_state("deploy", "migration", 7),
            ExitStatus.MIGRATION,
        ),
        ("deploy", "retryable", _exact_failure_state("deploy", "verification", 9), ExitStatus.READINESS),
        ("restore", "retryable", _exact_failure_state("restore", "verification", 9), ExitStatus.READINESS),
        ("deploy", "manual", _exact_failure_state("deploy", "backup", 6), ExitStatus.BACKUP),
        ("rollback", "retryable", {"failed_boundary": "backup"}, ExitStatus.BACKUP),
        ("restore", "manual", _exact_failure_state("restore", "backup", 6), ExitStatus.BACKUP),
        ("rollback", "retryable", {"failed_boundary": "release"}, ExitStatus.RELEASE),
        ("backup", "retryable", {}, ExitStatus.BACKUP),
        ("restore", "manual", _exact_failure_state("restore", "restore", 11), ExitStatus.RESTORE),
        ("cleanup", "refused", _exact_failure_state("cleanup", "cleanup", 10), ExitStatus.SAFETY),
        ("verify", "retryable", {}, ExitStatus.READINESS),
        ("discover", "refused", {}, ExitStatus.SAFETY),
        ("backup", "refused", {"locked": True}, ExitStatus.LOCKED),
    ),
)
def test_result_error_maps_only_final_outcome_and_concise_state(
    operation: str, outcome: str, state: dict[str, object], status: ExitStatus
) -> None:
    result = HostResult(3, operation, CORRELATION, outcome, "not completed", state, ())

    error = result_error(result)

    assert error.status is status
    if operation in {"deploy", "genesis", "restore", "cleanup"}:
        assert error.state["failed_boundary"] == state["failed_boundary"]
        assert error.state["mutation_state"] == state["mutation_state"]
    else:
        assert getattr(error, "state") == state


def test_result_error_rejects_an_unobserved_legacy_mutation_boundary() -> None:
    """A legacy stage label must not bypass the exact mutation-state contract."""

    result = HostResult(
        3,
        "deploy",
        CORRELATION,
        "retryable",
        "not completed",
        {"failed_boundary": "migration"},
        (),
    )

    with pytest.raises(ValueError, match="mutation evidence"):
        result_error(result)


def test_mutation_result_facts_copy_validated_failure_evidence_and_starting_state() -> None:
    """Controller mapping must not rename, default, or discard helper evidence."""

    result = HostResult(
        3,
        "deploy",
        CORRELATION,
        "retryable",
        "history failed",
        _failed_mutation_state(),
        (),
    )

    facts = mutation_result_facts(
        result,
        starting_state={"selected_release_id": None},
    )

    assert facts == {
        "starting_state": {"selected_release_id": None},
        "mutation_state": "changed",
        "exit_code": 8,
        "failed_boundary": "history",
        "observations": mutable(_mutation_observations()),
        "unavailable_fields": [],
        "inspection_error": None,
        "report": None,
        "desired_release_id": RELEASE,
        "backup_id": None,
    }
    error = result_error(result, starting_state={"selected_release_id": None})
    assert error.status is ExitStatus.RELEASE
    assert error.changed is True
    assert error.state == facts


@pytest.mark.parametrize(
    ("states", "expected"),
    (
        (("unchanged", "unchanged"), "unchanged"),
        (("unchanged", "unknown"), "unknown"),
        (("unknown", "changed"), "changed"),
        (("changed", "unknown", "unchanged"), "changed"),
    ),
)
def test_command_mutation_aggregation_keeps_the_strongest_evidence(
    states: tuple[str, ...], expected: str
) -> None:
    assert aggregate_mutation_state(*states) == expected


def test_first_lost_mutating_dispatch_is_unknown_with_no_fabricated_observations() -> None:
    """Transport loss after helper entry must not default a mutation to unchanged."""

    request = _deploy_request()
    original = OpsError(ExitStatus.SAFETY, "helper", "host helper returned invalid result")

    with pytest.raises(HelperTransportError) as raised:
        run_request(
            object(),
            request,
            package=object(),  # type: ignore[arg-type]
            invoker=lambda *_args: (_ for _ in ()).throw(
                HelperTransportError(original, helper_entry_dispatched=True)
            ),
        )

    error = raised.value
    assert error.changed is True
    assert error.state["mutation_state"] == "unknown"
    assert error.state["observations"]["selected_release_id"] is None
    assert "selected_release_id" in error.state["unavailable_fields"]
    assert error.state["desired_release_id"] == RELEASE
    assert error.state["report"] is None


def test_earlier_proved_mutation_survives_a_lost_later_mutating_reply() -> None:
    """A possible later change must not weaken proved controller convergence."""

    request = _deploy_request()
    original = OpsError(ExitStatus.SAFETY, "helper", "host helper returned invalid result")

    with pytest.raises(HelperTransportError) as raised:
        run_request(
            object(),
            request,
            package=object(),  # type: ignore[arg-type]
            prior_mutation_state="changed",
            invoker=lambda *_args: (_ for _ in ()).throw(
                HelperTransportError(original, helper_entry_dispatched=True)
            ),
        )

    error = raised.value
    public_result = WorkflowResult(
        command="provision",
        environment="production",
        changed=error.changed,
        stage="deployment-incomplete",
        facts=error.state,
        exit_status=error.status,
    )
    assert public_result.changed is True
    assert public_result.facts["mutation_state"] == "changed"
    assert public_result.facts["observations"]["selected_release_id"] is None
    assert "selected_release_id" in public_result.facts["unavailable_fields"]


def test_malformed_mutation_reply_after_dispatch_is_unknown() -> None:
    """A correlated envelope with malformed mutation state is still a lost mutation result."""

    request = _deploy_request()
    malformed = HostResult(3, "deploy", CORRELATION, "retryable", "failed", {}, ())

    with pytest.raises(OpsError) as raised:
        run_request(
            object(),
            request,
            package=object(),  # type: ignore[arg-type]
            invoker=lambda *_args: malformed,
        )

    assert raised.value.changed is True
    assert raised.value.state["mutation_state"] == "unknown"
    assert raised.value.state["report"] is None


def test_read_only_transport_loss_remains_unchanged() -> None:
    """Dispatch uncertainty alone must not imply that a read-only request mutated the host."""

    request = HostRequest(
        3,
        "discover",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman"},
        {},
    )
    original = OpsError(ExitStatus.SAFETY, "helper", "host helper returned invalid result")

    with pytest.raises(HelperTransportError) as raised:
        run_request(
            object(),
            request,
            package=object(),  # type: ignore[arg-type]
            invoker=lambda *_args: (_ for _ in ()).throw(
                HelperTransportError(original, helper_entry_dispatched=True)
            ),
        )

    assert raised.value.changed is False
    assert raised.value.state == {}


def test_cleanup_failure_retains_only_validated_current_and_earlier_completions() -> None:
    earlier = {
        "kind": "temporary",
        "identifier": ".release-stale.tmp",
        "path": "/opt/taskman/releases/.release-stale.tmp",
    }
    current = {
        "kind": "backup",
        "identifier": "backup-" + "a" * 32,
        "path": "/var/backups/taskman/backup-" + "a" * 32 + ".dump",
    }
    pending = {
        "kind": "backup",
        "identifier": "backup-" + "b" * 32,
        "path": "/var/backups/taskman/backup-" + "b" * 32 + ".dump",
    }
    request = HostRequest(
        3,
        "cleanup",
        CORRELATION,
        {"selected_release_id": RELEASE},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {
            "action": "execute",
            "targets": (current, pending),
            "release_retention": 3,
            "backup_retention": 7,
        },
    )
    state = _exact_failure_state("cleanup", "cleanup", 10)
    state.update(mutation_state="changed", completed_targets=(current,))
    result = HostResult.for_request(request, "retryable", "cleanup failed", state)

    returned = run_request(
        object(),
        request,
        package=object(),  # type: ignore[arg-type]
        invoker=lambda *_args: result,
    )
    facts = mutation_result_facts(returned, completed_targets=(earlier,))

    assert facts["mutation_state"] == "changed"
    assert facts["completed_targets"] == [current, earlier]
    assert facts["observations"] == mutable(state["observations"])


def test_cleanup_reply_cannot_claim_completion_outside_the_confirmed_batch() -> None:
    requested = {
        "kind": "temporary",
        "identifier": ".release-one.tmp",
        "path": "/opt/taskman/releases/.release-one.tmp",
    }
    unrelated = {
        "kind": "temporary",
        "identifier": ".release-two.tmp",
        "path": "/opt/taskman/releases/.release-two.tmp",
    }
    request = HostRequest(
        3,
        "cleanup",
        CORRELATION,
        {"selected_release_id": RELEASE},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {
            "action": "execute",
            "targets": (requested,),
            "release_retention": 3,
            "backup_retention": 7,
        },
    )
    state = _exact_failure_state("cleanup", "cleanup", 10)
    state.update(mutation_state="changed", completed_targets=(unrelated,))
    result = HostResult.for_request(request, "retryable", "cleanup failed", state)

    with pytest.raises(OpsError) as raised:
        run_request(
            object(),
            request,
            package=object(),  # type: ignore[arg-type]
            invoker=lambda *_args: result,
        )

    assert raised.value.state["mutation_state"] == "unknown"
    assert raised.value.state["completed_targets"] == []
