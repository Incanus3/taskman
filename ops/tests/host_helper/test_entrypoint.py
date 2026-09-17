from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Callable, Iterator
import zipfile

import pytest

from taskman_ops.helper_client.package import build_helper_package
from taskman_ops.host_protocol import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    HostRequest,
    HostResult,
    ProtocolError,
    decode_result,
    encode_request,
    unavailable_observations,
    validate_cleanup_completion,
    validate_mutation_state,
    validate_verification_report,
)
from taskman_ops.host_helper import __main__ as entrypoint
from taskman_ops.host_helper.operations import deploy as deploy_module
from taskman_ops.host_helper.records import SelectionRecord
from taskman_ops.host_helper.state import (
    HostState,
    mutation_observation_availability,
    mutation_observations as project_mutation_observations,
)


CORRELATION = "op-0123456789abcdef0123456789abcdef"
RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
SELECTION = "selection-" + "c" * 64 + ".json"


def mutation_observations() -> dict[str, object]:
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


def passing_report() -> dict[str, object]:
    names = (
        "taskman-service",
        "release-identity",
        "caddy-service",
        "listener-topology",
        "startup-journal",
        "local-readiness",
        "public-readiness",
        "public-hsts",
    )
    report = {
        "schema_version": 1,
        "status": "ok",
        "exit_status": 0,
        "release_id": RELEASE,
        "expected_release_id": RELEASE,
        "checks": [
            {
                "schema_version": 1,
                "name": name,
                "status": "passed",
                "summary": "checked",
            }
            for name in names
        ],
        "next_action": None,
    }
    return validate_verification_report(report)


def request_bytes(
    paths: dict[str, str] | None = None,
    *,
    operation: str = "discover",
) -> bytes:
    return encode_request(
        HostRequest(
            protocol_version=3,
            operation=operation,
            correlation_id=CORRELATION,
            expected_state={},
            paths=paths or {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
            parameters={"cursor": None} if operation in {"list_releases", "list_backups"} else {},
        )
    )


class Stream:
    def __init__(self, payload: bytes = b"") -> None:
        self.buffer = BytesIO(payload)


@contextmanager
def invoke_entrypoint(
    payload: bytes,
    handler: Callable[[object], object],
    *,
    operation: str = "discover",
) -> Iterator[Stream]:
    original_stdin = entrypoint.sys.stdin
    original_stdout = entrypoint.sys.stdout
    original_argv = entrypoint.sys.argv
    original_handler = entrypoint._DISPATCH[operation]
    stdout = Stream()
    entrypoint.sys.stdin = Stream(payload)
    entrypoint.sys.stdout = stdout
    entrypoint.sys.argv = [entrypoint.__file__]
    entrypoint._DISPATCH[operation] = handler
    try:
        yield stdout
    finally:
        entrypoint.sys.stdin = original_stdin
        entrypoint.sys.stdout = original_stdout
        entrypoint.sys.argv = original_argv
        entrypoint._DISPATCH[operation] = original_handler


def test_built_zipapp_emits_a_final_read_only_envelope(tmp_path: Path) -> None:
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(package.path)],
        input=request_bytes(
            {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
            operation="list_releases",
        ),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    result = decode_result(completed.stdout)
    assert result.operation == "list_releases"
    assert result.correlation_id == CORRELATION
    assert result.outcome == "succeeded"
    assert result.state["records"] == ()
    assert result.state["next_cursor"] is None
    assert set(result.to_mapping()) == {
        "protocol_version", "operation", "correlation_id", "outcome", "message", "state", "warnings"
    }


def test_built_zipapp_private_pgpass_entry_is_exit_only_and_finite(tmp_path: Path) -> None:
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    malformed = subprocess.run(
        [sys.executable, "-I", "-S", str(package.path), "provision-pgpass-authority", "127.0.0.1"],
        input=b"secret-canary\n", capture_output=True, check=False,
    )
    mismatched = subprocess.run(
        [
            sys.executable, "-I", "-S", str(package.path), "provision-pgpass-authority",
            "127.0.0.1", "5432", "taskman", "taskman_prod",
            "ready",
        ],
        input=b"127.0.0.1:5432:other:taskman:secret-canary\n",
        capture_output=True, check=False,
    )

    assert malformed.returncode == 2
    assert mismatched.returncode == 10
    assert malformed.stdout == malformed.stderr == b""
    assert mismatched.stdout == mismatched.stderr == b""


def test_built_zipapp_refuses_unknown_private_entry_without_protocol_output(tmp_path: Path) -> None:
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(package.path), "unknown-private-entry"],
        input=b"secret-canary\n",
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == completed.stderr == b""


def test_entrypoint_dispatches_bounded_preconvergence_authority_without_mutation() -> None:
    request = HostRequest(
        3,
        "provision_authority",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {
            "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
            "postgres_package_track": None,
            "resource_digests": {
                "taskman_service": "1" * 64,
                "backup_environment": "2" * 64,
                "backup_service": "3" * 64,
                "backup_timer": "4" * 64,
            },
        },
    )
    received: list[HostRequest] = []

    def observed(value: object) -> HostResult:
        assert isinstance(value, HostRequest)
        received.append(value)
        return HostResult.for_request(value, "succeeded", "authority observed", {"authority": "validated"})

    with invoke_entrypoint(encode_request(request), observed, operation="provision_authority") as stdout:
        assert entrypoint.main() == 0

    assert decode_result(stdout.buffer.getvalue()).state == {"authority": "validated"}
    assert received == [request]


def test_entrypoint_rejects_oversized_input_without_echoing_it(tmp_path: Path) -> None:
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    secret = b"canary-secret-value"
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(package.path)],
        input=secret + (b"x" * (MAX_INPUT_BYTES + 1)),
        capture_output=True,
        check=False,
    )

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert result.outcome == "retryable"
    assert result.message == "helper protocol failure"
    assert secret not in completed.stdout


def test_entrypoint_rejects_a_v2_transient_request() -> None:
    """Accepting the retired wire version could dispatch a stale helper contract."""

    payload = (
        b'{"correlation_id":"op-0123456789abcdef0123456789abcdef",'
        b'"expected_state":{},"operation":"discover","parameters":{},'
        b'"paths":{"install_root":"/opt/taskman"},"protocol_version":2}'
    )

    with invoke_entrypoint(payload, lambda request: request) as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    assert result.outcome == "retryable"
    assert result.message == "helper protocol failure"


def test_entrypoint_dispatches_backup_on_the_final_protocol_without_a_legacy_bridge() -> None:
    """Routing backup through OperationRequest would reintroduce deleted recovery evidence."""

    received: list[HostRequest] = []

    def final_result(request: object) -> HostResult:
        assert isinstance(request, HostRequest)
        received.append(request)
        return HostResult(
            3,
            "backup",
            request.correlation_id,
            "succeeded",
            "backup completed",
            {"backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
            (),
        )

    with invoke_entrypoint(request_bytes(operation="backup"), final_result, operation="backup") as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    assert result.correlation_id == CORRELATION
    assert result.state == {"backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    assert received[0].operation == "backup"


@pytest.mark.parametrize("operation", ["rollback", "restore"])
def test_entrypoint_dispatches_final_rollback_and_restore_without_a_legacy_bridge(
    operation: str,
) -> None:
    """A final host request must never be converted back to an operation ID record."""

    received: list[HostRequest] = []

    def final_result(request: object) -> HostResult:
        assert isinstance(request, HostRequest)
        received.append(request)
        if operation == "restore":
            observations, unavailable = unavailable_observations("restore")
            state = {
                "mutation_state": "unchanged",
                "exit_code": 2,
                "failed_boundary": "input",
                "observations": observations,
                "unavailable_fields": unavailable,
                "inspection_error": "lock-unavailable",
                "report": None,
                "desired_release_id": None,
                "backup_id": None,
                "pre_restore_backup_id": None,
            }
            validate_mutation_state("restore", "refused", state)
        else:
            state = {}
        return HostResult(3, operation, request.correlation_id, "refused", "unsafe", state, ())

    with invoke_entrypoint(request_bytes(operation=operation), final_result, operation=operation) as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    assert result.outcome == "refused"
    if operation == "restore":
        state = validate_mutation_state("restore", result.outcome, result.state)
        assert state["mutation_state"] == "unchanged"
        assert state["exit_code"] == 2
        assert state["failed_boundary"] == "input"
    assert received[0].operation == operation


def test_entrypoint_replaces_an_invalid_final_handler_result_with_small_final_result() -> None:
    def invalid_final_result(request: object) -> object:
        assert isinstance(request, HostRequest)
        return object()

    with invoke_entrypoint(
        request_bytes(operation="backup"), invalid_final_result, operation="backup"
    ) as stdout:
        try:
            assert entrypoint.main() == 0
        except ProtocolError as error:
            pytest.fail(f"entrypoint leaked encoding failure: {error}")

    payload = stdout.buffer.getvalue()
    result = decode_result(payload)
    assert len(payload) <= MAX_OUTPUT_BYTES
    assert result.outcome == "retryable"
    assert result.message == "helper internal failure"
    assert result.correlation_id == CORRELATION


def test_mutation_exception_emits_exact_unknown_evidence_after_one_final_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def observe(request: HostRequest):
        calls.append(request.operation)
        return mutation_observations(), (), None

    monkeypatch.setattr(entrypoint, "_observe_final_mutation", observe)

    def fail(_request: object) -> object:
        raise RuntimeError("secret failure")

    with invoke_entrypoint(request_bytes(operation="deploy"), fail, operation="deploy") as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    state = validate_mutation_state("deploy", result.outcome, result.state)
    assert calls == ["deploy"]
    assert state["mutation_state"] == "unknown"
    assert state["exit_code"] == 8
    assert state["failed_boundary"] == "inspection"
    assert state["desired_release_id"] is None
    assert state["observations"]["selected_release_id"] == RELEASE
    assert state["report"] is None
    assert result.message == "helper internal failure"


def test_entrypoint_preserves_verification_report_on_later_history_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer_calls: list[str] = []

    def observe(request: HostRequest):
        observer_calls.append(request.operation)
        return mutation_observations(), (), None

    monkeypatch.setattr(
        entrypoint,
        "_observe_final_mutation",
        observe,
    )

    def history_failure(request: HostRequest) -> HostResult:
        return HostResult.for_request(
            request,
            "retryable",
            "deployment history publication failed",
            {
                "mutation_state": "changed",
                "exit_code": 8,
                "failed_boundary": "history",
                "observations": mutation_observations(),
                "unavailable_fields": (),
                "inspection_error": None,
                "backup_id": None,
                "desired_release_id": RELEASE,
                "report": passing_report(),
            },
        )

    with invoke_entrypoint(
        request_bytes(operation="deploy"), history_failure, operation="deploy"
    ) as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    state = validate_mutation_state("deploy", result.outcome, result.state)
    assert state["mutation_state"] == "changed"
    assert state["exit_code"] == 8
    assert state["failed_boundary"] == "history"
    assert state["report"] == passing_report()
    assert state["desired_release_id"] == RELEASE
    assert observer_calls == []


def test_failed_cleanup_inspection_is_exact_and_cannot_claim_a_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = {
        key: mutation_observations()[key]
        for key in (
            "selected_release_id",
            "last_successful_selection_id",
            "backup_protection_sha256",
            "restore_target_sha256",
        )
    }
    monkeypatch.setattr(
        entrypoint,
        "_observe_final_mutation",
        lambda _request: (observations, (), None),
    )
    request = HostRequest(
        3,
        "cleanup",
        CORRELATION,
        {"selected_release_id": RELEASE},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"action": "inspect", "targets": (), "release_retention": 3, "backup_retention": 7},
    )

    with invoke_entrypoint(
        encode_request(request),
        lambda _request: (_ for _ in ()).throw(OSError("failed")),
        operation="cleanup",
    ) as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    state = validate_mutation_state("cleanup", result.outcome, result.state)
    assert state["mutation_state"] == "unchanged"
    assert state["completed_targets"] == []
    assert state["exit_code"] == 10
    assert state["failed_boundary"] == "inspection"


def test_unknown_observation_domains_are_marked_without_discarding_independent_facts() -> None:
    observed = mutation_observations()
    observed.update(
        database_state="unknown",
        service_state="unknown",
        backup_timer_state="unknown",
    )

    unavailable, inspection_error = entrypoint._observation_unavailable("deploy", observed)

    assert unavailable == ("backup_timer_state", "database_state", "service_state")
    assert inspection_error == "unsafe-observation"
    assert observed["selected_release_id"] == RELEASE


def test_scheduler_failure_marks_its_checksum_unavailable_without_erasing_independent_facts() -> None:
    observed = mutation_observations()
    observed.update(
        scheduled_backup_sha256=None,
        backup_timer_enabled=None,
        backup_timer_state="unknown",
    )

    unavailable, inspection_error = entrypoint._observation_unavailable("deploy", observed)

    assert unavailable == (
        "backup_timer_enabled",
        "backup_timer_state",
        "scheduled_backup_sha256",
    )
    assert inspection_error == "unsafe-observation"
    assert observed["selected_release_id"] == RELEASE


def test_cleanup_observation_round_trips_without_scheduler_unavailable_fields() -> None:
    final_state = HostState(
        selected_release_id=None,
        releases=(),
        backups=(),
        selections=(),
        applied_migrations=(),
        service_state="unknown",
        database_state="unknown",
        temporary_paths=(),
        warnings=(),
    )
    observations = project_mutation_observations(final_state, "cleanup")
    unavailable, inspection_error = mutation_observation_availability(
        "cleanup", observations
    )
    mutation_state = {
        "mutation_state": "unchanged",
        "exit_code": 0,
        "failed_boundary": None,
        "observations": observations,
        "unavailable_fields": unavailable,
        "inspection_error": inspection_error,
        "report": None,
        "completed_targets": (),
    }

    assert "scheduled_backup_sha256" not in observations
    assert unavailable == ()
    assert validate_mutation_state("cleanup", "succeeded", mutation_state)[
        "observations"
    ] == observations


def test_deploy_post_history_observation_proves_success_without_entrypoint_reinspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = SelectionRecord(
        RELEASE,
        None,
        None,
        datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        2,
        None,
        (),
    )
    final_state = HostState(
        selected_release_id=RELEASE,
        releases=(),
        backups=(),
        selections=(selection,),
        applied_migrations=(),
        service_state="running",
        database_state="ready",
        temporary_paths=(),
        warnings=(),
    )
    inputs = deploy_module._Inputs(
        paths=object(),
        expected_state={},
        previous_release_id=None,
        expected_migrations=(),
        candidate=SimpleNamespace(release_id=RELEASE),
        candidate_versions=(),
        artifact_path=Path("/tmp/release.tar.gz"),
        artifact_sha256="a" * 64,
        migration_policy="no-change",
        credentials=Path("/etc/taskman/pgpass"),
        database={},
        verification={},
        backup_helper={},
        prune_backup_ids=(),
    )
    observation_calls: list[dict[str, object]] = []

    def observe(_inputs: object, **kwargs: object) -> HostState:
        observation_calls.append(kwargs)
        return final_state

    monkeypatch.setattr(deploy_module, "_observe", observe)
    observed, recorded, backup = deploy_module._record_successful_selection(
        inputs, final_state, None
    )

    assert observed is final_state
    assert recorded is False
    assert backup is None
    assert observation_calls == [
        {"allow_selection_transition": False, "include_runtime": True}
    ]

    observations = project_mutation_observations(
        observed,
        "deploy",
        scheduler={
            "scheduled_backup_sha256": "e" * 64,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
        },
    )
    unavailable, inspection_error = mutation_observation_availability(
        "deploy", observations
    )
    request = HostRequest(
        3,
        "deploy",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"target": {"kind": "upload", "manifest": {"release_id": RELEASE}}},
    )
    raw_result = deploy_module._result(
        request,
        "succeeded",
        "deployment converged",
        observed,
        changed=False,
        database_state="unchanged",
        service_state="running",
        report=passing_report(),
        final_observations=observations,
        final_unavailable=unavailable,
        final_inspection_error=inspection_error,
    )
    raw_result = replace(
        raw_result,
        state={**raw_result.state, "desired_release_id": RELEASE},
    )
    entrypoint_observer_calls: list[str] = []

    def observe_final(request: HostRequest):
        entrypoint_observer_calls.append(request.operation)
        return mutation_observations(), (), None

    monkeypatch.setattr(entrypoint, "_observe_final_mutation", observe_final)

    monkeypatch.setitem(entrypoint._DISPATCH, "deploy", lambda _request: raw_result)
    result = entrypoint._dispatch(request)

    state = validate_mutation_state("deploy", "succeeded", result.state)
    assert state["observations"]["service_state"] == "running"
    assert state["desired_release_id"] == RELEASE
    assert entrypoint_observer_calls == []


def test_exact_failure_reuses_operation_owned_final_observation_without_reacquiring_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer_calls: list[str] = []

    def observe_final(request: HostRequest):
        observer_calls.append(request.operation)
        return mutation_observations(), (), None

    monkeypatch.setattr(entrypoint, "_observe_final_mutation", observe_final)

    def operation_result(request: HostRequest) -> HostResult:
        return HostResult.for_request(
            request,
            "retryable",
            "history failed",
            {
                "mutation_state": "changed",
                "exit_code": 8,
                "failed_boundary": "history",
                "observations": mutation_observations(),
                "unavailable_fields": (),
                "inspection_error": None,
                "backup_id": None,
                "desired_release_id": RELEASE,
                "report": passing_report(),
            },
        )

    with invoke_entrypoint(
        request_bytes(operation="deploy"), operation_result, operation="deploy"
    ) as stdout:
        assert entrypoint.main() == 0

    state = validate_mutation_state(
        "deploy", "retryable", decode_result(stdout.buffer.getvalue()).state
    )
    assert state["observations"]["selected_release_id"] == RELEASE
    assert state["desired_release_id"] == RELEASE
    assert observer_calls == []


def _history_failure_state(*, observations: object = None, report: object = None) -> dict[str, object]:
    return {
        "mutation_state": "changed",
        "exit_code": 8,
        "failed_boundary": "history",
        "observations": mutation_observations() if observations is None else observations,
        "unavailable_fields": (),
        "inspection_error": None,
        "report": passing_report() if report is None else report,
        "desired_release_id": RELEASE,
        "backup_id": None,
    }


def test_invalid_exact_state_preserves_independent_changed_report_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed fact group must not erase matching failure proof from its siblings."""

    observer_calls: list[str] = []

    def observe(request: HostRequest):
        observer_calls.append(request.operation)
        return mutation_observations(), (), None

    monkeypatch.setattr(entrypoint, "_observe_final_mutation", observe)

    def malformed_result(request: HostRequest) -> HostResult:
        return HostResult.for_request(
            request,
            "retryable",
            "history publication failed",
            _history_failure_state(observations={"not": "the complete group"}),
            ("untrusted handler warning",),
        )

    with invoke_entrypoint(
        request_bytes(operation="deploy"), malformed_result, operation="deploy"
    ) as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    state = validate_mutation_state("deploy", result.outcome, result.state)
    assert result.outcome == "retryable"
    assert result.message == "history publication failed"
    assert state["mutation_state"] == "changed"
    assert state["failed_boundary"] == "history"
    assert state["report"] == passing_report()
    assert state["desired_release_id"] == RELEASE
    assert observer_calls == ["deploy"]
    assert result.warnings == ("invalid internal mutation evidence was rejected",)


def test_mismatched_mutation_result_contributes_no_handler_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Correlation is checked before any malformed-result recovery projection."""

    observer_calls: list[str] = []

    def observe(request: HostRequest):
        observer_calls.append(request.operation)
        return mutation_observations(), (), None

    monkeypatch.setattr(entrypoint, "_observe_final_mutation", observe)

    def mismatched(request: HostRequest) -> HostResult:
        return HostResult(
            3,
            "deploy",
            "op-ffffffffffffffffffffffffffffffff",
            "retryable",
            "foreign failure",
            _history_failure_state(),
            (),
        )

    with invoke_entrypoint(request_bytes(operation="deploy"), mismatched, operation="deploy") as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    state = validate_mutation_state("deploy", result.outcome, result.state)
    assert result.correlation_id == CORRELATION
    assert result.message == "helper internal failure"
    assert state["mutation_state"] == "unknown"
    assert state["report"] is None
    assert observer_calls == ["deploy"]


def test_invalid_success_cannot_be_reconstructed_from_passing_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deploy_request()
    monkeypatch.setattr(entrypoint, "_observe_final_mutation", lambda _value: (mutation_observations(), (), None))
    state = _history_failure_state(observations={"invalid": True})
    state.update(exit_code=0, failed_boundary=None)
    recovered = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "succeeded", "claimed success", state)
    )
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert recovered.outcome == "retryable"
    assert recovered.message == "helper internal failure"
    assert exact["failed_boundary"] == "inspection"
    assert exact["report"] == passing_report()


def test_wrong_operation_result_contributes_no_matching_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deploy_request()
    monkeypatch.setattr(entrypoint, "_observe_final_mutation", lambda _value: (mutation_observations(), (), None))
    foreign = HostResult(3, "genesis", CORRELATION, "retryable", "foreign", _history_failure_state(), ())
    monkeypatch.setitem(entrypoint._DISPATCH, "deploy", lambda _request: foreign)
    recovered = entrypoint._dispatch(request)
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert recovered.message == "helper internal failure"
    assert exact["mutation_state"] == "unknown"
    assert exact["report"] is None


def test_wrong_protocol_result_contributes_no_matching_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deploy_request()
    monkeypatch.setattr(entrypoint, "_observe_final_mutation", lambda _value: (mutation_observations(), (), None))
    foreign = HostResult.for_request(request, "retryable", "foreign", _history_failure_state())
    object.__setattr__(foreign, "protocol_version", 2)
    monkeypatch.setitem(entrypoint._DISPATCH, "deploy", lambda _request: foreign)
    recovered = entrypoint._dispatch(request)
    assert recovered.message == "helper internal failure"
    assert validate_mutation_state("deploy", recovered.outcome, recovered.state)["report"] is None


def test_encoding_failure_of_valid_success_keeps_report_and_identity_without_observing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second encoding attempt retains validated proof while dropping only final facts."""

    request = HostRequest(
        3,
        "deploy",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"target": {"kind": "upload", "manifest": {"release_id": RELEASE}}},
    )
    result = HostResult.for_request(
        request,
        "succeeded",
        "deployment completed",
        {
            **_history_failure_state(),
            "mutation_state": "changed",
            "exit_code": 0,
            "failed_boundary": None,
        },
    )
    validate_mutation_state("deploy", "succeeded", result.state)

    original_encode = entrypoint.encode_result
    calls = 0

    def fail_once(value: HostResult) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProtocolError("simulated serializer failure")
        return original_encode(value)

    monkeypatch.setattr(entrypoint, "encode_result", fail_once)

    encoded = entrypoint._encode_or_internal_failure(
        result,
        operation=request.operation,
        correlation_id=request.correlation_id,
        request=request,
    )

    recovered = decode_result(encoded)
    state = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert calls == 2
    assert recovered.outcome == "retryable"
    assert recovered.message == "helper internal failure"
    assert recovered.warnings == ("helper result encoding failed; final observations are unavailable",)
    assert state["mutation_state"] == "changed"
    assert state["report"] == passing_report()
    assert state["desired_release_id"] == RELEASE
    assert set(state["unavailable_fields"]) == set(state["observations"])


def test_reduced_encoding_with_maximum_escaped_report_drops_unencodable_facts() -> None:
    """The serializer boundary, rather than a mock, proves large integers lose only fact evidence."""

    request = HostRequest(
        3,
        "deploy",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"target": {"kind": "upload", "manifest": {"release_id": RELEASE}}},
    )
    report = passing_report()
    report["checks"] = [
        {**check, "summary": ('\\\\"' * 1365)} for check in report["checks"]
    ]
    report = validate_verification_report(report)
    observations = mutation_observations()
    observations["applied_migrations"] = (10**4300,)
    result = HostResult.for_request(
        request,
        "retryable",
        "history publication failed",
        _history_failure_state(observations=observations, report=report),
    )
    validate_mutation_state("deploy", result.outcome, result.state)

    payload = entrypoint._encode_or_internal_failure(
        result,
        operation=request.operation,
        correlation_id=request.correlation_id,
        request=request,
    )

    recovered = decode_result(payload)
    state = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert len(payload) <= MAX_OUTPUT_BYTES
    assert state["report"] == report
    assert set(state["unavailable_fields"]) == set(state["observations"])


def test_isolated_zipapp_recovers_invalid_cooperative_handler_without_losing_report(
    tmp_path: Path,
) -> None:
    """The copied archive exercises recovery without a production injection seam."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    state = {**_history_failure_state(), "backup_id": "not-a-backup-id"}
    injection = (
        "\n_DISPATCH['deploy'] = lambda request: HostResult.for_request(\n"
        "    request, 'retryable', 'history publication failed', "
        f"{state!r}, ('untrusted handler warning',)\n)\n\n"
    ).encode()
    copied = tmp_path / "injected-taskman-host.pyz"
    with zipfile.ZipFile(package.path) as original, copied.open("wb") as output:
        output.write(b"#!/usr/bin/python3\n")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for info in original.infolist():
                source = original.read(info.filename)
                if info.filename == "taskman_ops/host_helper/__main__.py":
                    source = source.replace(
                        b'if __name__ == "__main__":', injection + b'if __name__ == "__main__":'
                    )
                archive.writestr(info.filename, source)

    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(copied)],
        input=request_bytes(operation="deploy"),
        capture_output=True,
        check=False,
    )

    result = decode_result(completed.stdout)
    state = validate_mutation_state("deploy", result.outcome, result.state)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "retryable"
    assert result.message == "history publication failed"
    assert state["mutation_state"] == "changed"
    assert state["report"] == passing_report()
    assert state["backup_id"] is None
    assert result.warnings == ("invalid internal mutation evidence was rejected",)


def _deploy_request() -> HostRequest:
    return HostRequest(
        3, "deploy", CORRELATION, {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"target": {"kind": "upload", "manifest": {"release_id": RELEASE}}},
    )


def _failed_report(*, exit_status: int = 9) -> dict[str, object]:
    report = passing_report()
    count = 5 if exit_status == 8 else len(report["checks"])
    report.update(
        status="failed",
        exit_status=exit_status,
        checks=[
            {**check, "status": "failed" if index == count - 1 else "passed"}
            for index, check in enumerate(report["checks"][:count])
        ],
        next_action="inspect the fixed verification summaries and correct the reported host state before retrying",
    )
    return validate_verification_report(report)


@pytest.mark.parametrize("classification", ("changed", "unchanged", "unknown"))
def test_invalid_fact_group_retains_each_independent_mutation_classification(
    monkeypatch: pytest.MonkeyPatch, classification: str
) -> None:
    request = _deploy_request()
    calls: list[str] = []
    monkeypatch.setattr(
        entrypoint, "_observe_final_mutation",
        lambda value: (calls.append(value.operation) or mutation_observations(), (), None),
    )
    state = _history_failure_state(observations={"invalid": True})
    state["mutation_state"] = classification
    recovered = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "retryable", "history failed", state)
    )
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert exact["mutation_state"] == classification
    assert exact["desired_release_id"] == RELEASE
    assert exact["report"] == passing_report()
    assert calls == ["deploy"]


def test_failed_report_precedence_repairs_only_an_incompatible_primary_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deploy_request()
    monkeypatch.setattr(entrypoint, "_observe_final_mutation", lambda _value: (mutation_observations(), (), None))
    state = _history_failure_state(observations={"invalid": True}, report=_failed_report())
    recovered = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "retryable", "history failed", state)
    )
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert (exact["failed_boundary"], exact["exit_code"]) == ("verification", 9)
    assert recovered.message == "helper internal failure"
    assert exact["report"] == _failed_report()


def test_invalid_report_is_omitted_without_reobserving_valid_final_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deploy_request()
    calls: list[str] = []
    monkeypatch.setattr(entrypoint, "_observe_final_mutation", lambda value: calls.append(value.operation))
    state = _history_failure_state(report={"not": "a report"})
    recovered = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "retryable", "history failed", state)
    )
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert exact["report"] is None
    assert exact["failed_boundary"] == "history"
    assert calls == []


def test_coherent_lock_and_failed_verification_messages_survive_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deploy_request()
    monkeypatch.setattr(entrypoint, "_observe_final_mutation", lambda _value: (mutation_observations(), (), None))
    lock_state = _history_failure_state(observations={"invalid": True})
    lock_state.update(mutation_state="unchanged", exit_code=12, failed_boundary="lock")
    lock = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "retryable", "lock held", lock_state)
    )
    assert validate_mutation_state("deploy", lock.outcome, lock.state)["exit_code"] == 12
    assert lock.message == "lock held"

    report = _failed_report()
    verification_state = _history_failure_state(observations={"invalid": True}, report=report)
    verification_state.update(exit_code=9, failed_boundary="verification")
    verification = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "retryable", "readiness failed", verification_state)
    )
    assert validate_mutation_state("deploy", verification.outcome, verification.state)["report"] == report
    assert verification.message == "readiness failed"


def _cleanup_request(targets: tuple[dict[str, object], ...]) -> HostRequest:
    return HostRequest(
        3, "cleanup", CORRELATION, {"selected_release_id": RELEASE},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"action": "execute", "targets": targets, "release_retention": 3, "backup_retention": 7},
    )


def _cleanup_observations() -> dict[str, object]:
    observed = mutation_observations()
    return {key: observed[key] for key in ("selected_release_id", "last_successful_selection_id", "backup_protection_sha256", "restore_target_sha256")}


@pytest.mark.parametrize(
    ("completed", "report", "expected"),
    (
        ("valid", None, "valid"),
        ("duplicate", None, "empty"),
        ("outside", None, "empty"),
        ("valid", "passing", "valid"),
        ("valid", "failed", "valid"),
    ),
)
def test_cleanup_recovery_keeps_only_request_authorized_completion_proof(
    monkeypatch: pytest.MonkeyPatch, completed: str, report: str | None, expected: str
) -> None:
    target = {"kind": "backup", "identifier": "backup-" + "a" * 32, "path": "/var/backups/taskman/backup-" + "a" * 32 + ".dump"}
    request = _cleanup_request((target,))
    entries: object = [target]
    if completed == "duplicate":
        entries = [target, target]
    elif completed == "outside":
        entries = [{**target, "identifier": "backup-" + "b" * 32, "path": "/var/backups/taskman/backup-" + "b" * 32 + ".dump"}]
    state = {
        "mutation_state": "changed", "exit_code": 10, "failed_boundary": "cleanup",
        "observations": {"malformed": True}, "unavailable_fields": (), "inspection_error": None,
        "report": passing_report() if report == "passing" else _failed_report() if report else None,
        "completed_targets": entries,
    }
    monkeypatch.setattr(entrypoint, "_observe_final_mutation", lambda _value: (_cleanup_observations(), (), None))
    recovered = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "retryable", "cleanup failed", state)
    )
    exact = validate_mutation_state("cleanup", recovered.outcome, recovered.state)
    assert exact["report"] is None
    assert exact["completed_targets"] == ([target] if expected == "valid" else [])


@pytest.mark.parametrize("failure", ("lock", "error", "invalid"))
def test_invalid_final_observer_output_uses_unavailable_group_once(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    request = _deploy_request()
    calls: list[str] = []

    def observe(value: HostRequest):
        calls.append(value.operation)
        if failure == "lock":
            raise entrypoint.LifecycleLockContention("locked")
        if failure == "error":
            raise OSError("secret")
        return {"invalid": True}, (), None

    monkeypatch.setattr(entrypoint, "_observe_final_mutation", observe)
    state = _history_failure_state(observations={"invalid": True})
    recovered = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "retryable", "history failed", state)
    )
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert calls == ["deploy"]
    assert set(exact["unavailable_fields"]) == set(exact["observations"])
    assert exact["inspection_error"] == ("lock-unavailable" if failure == "lock" else "inspection-failed")


def test_restore_dependent_database_arrangement_is_reobserved_as_one_fact_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = "backup-" + "a" * 32
    request = HostRequest(
        3, "restore", CORRELATION, {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"backup_id": backup, "credentials_path": "/etc/taskman/pgpass", "database": {}},
    )
    observations = {**mutation_observations(), "restore_database_state": {
        "canonical": {"oid": 42, "owner": "taskman", "migration_table_present": True, "applied_migrations": [7]},
        "temporary": None, "retired": None,
    }}
    state = {
        "mutation_state": "changed", "exit_code": 11, "failed_boundary": "history",
        "observations": observations, "unavailable_fields": (), "inspection_error": None, "report": None,
        "desired_release_id": None, "backup_id": backup, "pre_restore_backup_id": None,
    }
    calls: list[str] = []
    unavailable, fields = unavailable_observations("restore")
    monkeypatch.setattr(
        entrypoint, "_observe_final_mutation",
        lambda value: (calls.append(value.operation) or unavailable, tuple(fields), "inspection-failed"),
    )
    recovered = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, "retryable", "history failed", state)
    )
    exact = validate_mutation_state("restore", recovered.outcome, recovered.state)
    assert calls == ["restore"]
    assert exact["backup_id"] == backup
    assert set(exact["unavailable_fields"]) == set(exact["observations"])


def _maximum_cleanup_targets() -> tuple[dict[str, object], ...]:
    targets = []
    for index in range(64):
        identifier = f"{index:03d}" + "\x01" * 252
        path = "/" + "/".join(("\x01" * 255, "\x01" * 255, "\x01" * 255, identifier))
        targets.append({"kind": "temporary", "identifier": identifier, "path": path})
    return tuple(targets)


def test_reduced_cleanup_encoding_fits_64_maximum_path_completion_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = _maximum_cleanup_targets()
    assert all(len(target["path"].encode()) == 1024 for target in targets)
    request = _cleanup_request(targets)
    state = {
        "mutation_state": "changed", "exit_code": 10, "failed_boundary": "cleanup",
        "observations": _cleanup_observations(), "unavailable_fields": (), "inspection_error": None,
        "report": None, "completed_targets": list(targets),
    }
    result = HostResult.for_request(request, "retryable", "cleanup failed", state)
    validate_mutation_state("cleanup", result.outcome, result.state)
    original_encode = entrypoint.encode_result
    attempts = 0

    def fail_once(value: HostResult) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProtocolError("simulated first serialization failure")
        return original_encode(value)

    monkeypatch.setattr(entrypoint, "encode_result", fail_once)
    payload = entrypoint._encode_or_internal_failure(
        result, operation="cleanup", correlation_id=CORRELATION, request=request
    )
    recovered = decode_result(payload)
    exact = validate_mutation_state("cleanup", recovered.outcome, recovered.state)
    validate_cleanup_completion(request, recovered.outcome, exact)
    assert attempts == 2
    assert len(payload) <= MAX_OUTPUT_BYTES
    assert exact["completed_targets"] == list(targets)


def test_cleanup_inspection_encoding_failure_uses_small_exact_failure_without_observing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = HostRequest(
        3, "cleanup", CORRELATION, {}, _deploy_request().paths,
        {"action": "inspect", "targets": (), "release_retention": 3, "backup_retention": 7},
    )
    inspected = HostResult.for_request(
        request, "succeeded", "cleanup inspected", {"authority": {}, "targets": (), "next_cursor": None}
    )
    original_encode = entrypoint.encode_result
    attempts = 0

    def fail_once(value: HostResult) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProtocolError("simulated encoding failure")
        return original_encode(value)

    monkeypatch.setattr(entrypoint, "encode_result", fail_once)
    payload = entrypoint._encode_or_internal_failure(
        inspected, operation="cleanup", correlation_id=CORRELATION, request=request
    )
    recovered = decode_result(payload)
    exact = validate_mutation_state("cleanup", recovered.outcome, recovered.state)
    assert attempts == 2
    assert recovered.outcome == "refused"
    assert (exact["failed_boundary"], exact["exit_code"], exact["mutation_state"]) == (
        "inspection", 10, "unchanged"
    )
    assert exact["completed_targets"] == []
    assert exact["report"] is None


@pytest.mark.parametrize(
    ("outcome", "exit_status", "expected_outcome", "expected_message"),
    (
        ("manual", 8, "manual", "original primary message"),
        ("refused", 9, "retryable", "helper internal failure"),
    ),
)
def test_failed_report_primary_preserves_only_full_validator_coherent_tuple(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    exit_status: int,
    expected_outcome: str,
    expected_message: str,
) -> None:
    request = _deploy_request()
    monkeypatch.setattr(entrypoint, "_observe_final_mutation", lambda _value: (mutation_observations(), (), None))
    state = _history_failure_state(observations={"invalid": True}, report=_failed_report(exit_status=exit_status))
    state.update(exit_code=exit_status, failed_boundary="verification")
    recovered = entrypoint._invalid_mutation_result(
        request, HostResult.for_request(request, outcome, "original primary message", state)
    )
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert recovered.outcome == expected_outcome
    assert recovered.message == expected_message
    assert (exact["failed_boundary"], exact["exit_code"]) == ("verification", exit_status)


def test_invalid_observer_shapes_and_facts_retain_primary_evidence_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def observe(request: HostRequest):
        calls.append(request.operation)
        return mutation_observations(), (), None, "unexpected"

    monkeypatch.setattr(entrypoint, "_observe_final_mutation", observe)
    request = _deploy_request()
    result = HostResult.for_request(
        request, "retryable", "history failed", _history_failure_state(observations={"invalid": True})
    )
    with invoke_entrypoint(encode_request(request), lambda _request: result, operation="deploy") as output:
        assert entrypoint.main() == 0
    recovered = decode_result(output.buffer.getvalue())
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert calls == ["deploy"]
    assert exact["mutation_state"] == "changed"
    assert exact["report"] == passing_report()
    assert set(exact["unavailable_fields"]) == set(exact["observations"])


@pytest.mark.parametrize("classification", ("changed", "unknown"))
def test_cleanup_inspection_invalid_classification_is_reset_before_one_observer(
    monkeypatch: pytest.MonkeyPatch, classification: str
) -> None:
    calls: list[str] = []
    request = HostRequest(
        3, "cleanup", CORRELATION, {}, _deploy_request().paths,
        {"action": "inspect", "targets": (), "release_retention": 3, "backup_retention": 7},
    )
    state = {
        "mutation_state": classification, "exit_code": 10, "failed_boundary": "inspection",
        "observations": {"invalid": True}, "unavailable_fields": (), "inspection_error": None,
        "report": None, "completed_targets": ({"invalid": True},),
    }
    monkeypatch.setattr(
        entrypoint, "_observe_final_mutation",
        lambda value: (calls.append(value.operation) or _cleanup_observations(), (), None),
    )
    with invoke_entrypoint(
        encode_request(request),
        lambda request: HostResult.for_request(request, "refused", "inspection failed", state),
        operation="cleanup",
    ) as output:
        assert entrypoint.main() == 0
    recovered = decode_result(output.buffer.getvalue())
    exact = validate_mutation_state("cleanup", recovered.outcome, recovered.state)
    assert calls == ["cleanup"]
    assert exact["mutation_state"] == "unchanged"
    assert exact["completed_targets"] == []


def test_mismatched_mutation_result_with_invalid_observer_emits_valid_unavailable_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deploy_request()
    calls: list[str] = []
    monkeypatch.setattr(
        entrypoint, "_observe_final_mutation",
        lambda value: (calls.append(value.operation) or {"invalid": True}, (), None),
    )
    foreign = HostResult(3, "deploy", "op-" + "f" * 32, "retryable", "foreign", _history_failure_state(), ())
    with invoke_entrypoint(encode_request(request), lambda _request: foreign, operation="deploy") as output:
        assert entrypoint.main() == 0
    recovered = decode_result(output.buffer.getvalue())
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert calls == ["deploy"]
    assert exact["mutation_state"] == "unknown"
    assert exact["report"] is None
    assert set(exact["unavailable_fields"]) == set(exact["observations"])


def test_wrong_type_mutation_result_with_invalid_observer_emits_valid_unavailable_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deploy_request()
    calls: list[str] = []
    monkeypatch.setattr(
        entrypoint, "_observe_final_mutation",
        lambda value: (calls.append(value.operation) or {"invalid": True}, (), None),
    )
    with invoke_entrypoint(encode_request(request), lambda _request: object(), operation="deploy") as output:
        assert entrypoint.main() == 0
    recovered = decode_result(output.buffer.getvalue())
    exact = validate_mutation_state("deploy", recovered.outcome, recovered.state)
    assert calls == ["deploy"]
    assert exact["mutation_state"] == "unknown"
    assert exact["report"] is None
    assert set(exact["unavailable_fields"]) == set(exact["observations"])


def test_reduced_restore_encoding_fits_true_escaped_report_and_identity_maxima(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = RELEASE.replace("0.2.0", "1" * (255 - len(RELEASE) + 1) + ".1.1")
    assert len(release) == 255
    backup = "backup-" + "a" * 32
    request = HostRequest(3, "restore", CORRELATION, {}, _deploy_request().paths, {"backup_id": backup})
    base = entrypoint._operation_failure_base(request, observe=False)
    report = passing_report()
    report.update(
        release_id=release,
        expected_release_id=release,
        checks=[{**check, "summary": "\x01" * 4096} for check in report["checks"]],
    )
    state = {
        **base.state,
        "mutation_state": "changed",
        "desired_release_id": release,
        "backup_id": backup,
        "pre_restore_backup_id": "backup-" + "b" * 32,
        "report": validate_verification_report(report),
    }
    result = HostResult.for_request(
        request, "retryable", "history failed", validate_mutation_state("restore", "retryable", state)
    )
    original_encode = entrypoint.encode_result
    attempts = 0

    def fail_once(value: HostResult) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProtocolError("simulated first serialization failure")
        return original_encode(value)

    monkeypatch.setattr(entrypoint, "encode_result", fail_once)
    payload = entrypoint._encode_or_internal_failure(
        result, operation="restore", correlation_id=CORRELATION, request=request
    )
    recovered = decode_result(payload)
    exact = validate_mutation_state("restore", recovered.outcome, recovered.state)
    assert attempts == 2
    assert len(payload) <= MAX_OUTPUT_BYTES
    assert exact["report"] == report
    assert exact["desired_release_id"] == release
    assert exact["pre_restore_backup_id"] == "backup-" + "b" * 32
