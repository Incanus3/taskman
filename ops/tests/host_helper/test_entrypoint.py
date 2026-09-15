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
    validate_mutation_state,
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
    return {
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
    original_handler = entrypoint._DISPATCH[operation]
    stdout = Stream()
    entrypoint.sys.stdin = Stream(payload)
    entrypoint.sys.stdout = stdout
    entrypoint._DISPATCH[operation] = handler
    try:
        yield stdout
    finally:
        entrypoint.sys.stdin = original_stdin
        entrypoint.sys.stdout = original_stdout
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
        return HostResult(3, operation, request.correlation_id, "refused", "unsafe", {}, ())

    with invoke_entrypoint(request_bytes(operation=operation), final_result, operation=operation) as stdout:
        assert entrypoint.main() == 0

    assert decode_result(stdout.buffer.getvalue()).outcome == "refused"
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
    monkeypatch.setattr(
        entrypoint,
        "_observe_final_mutation",
        lambda _request: (mutation_observations(), (), None),
    )

    def history_failure(request: HostRequest) -> HostResult:
        return HostResult.for_request(
            request,
            "retryable",
            "deployment history publication failed",
            {
                "changed": True,
                "failed_boundary": "history",
                "backup_id": None,
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
    monkeypatch.setattr(
        entrypoint,
        "_observe_final_mutation",
        lambda _request: pytest.fail("success must reuse the post-history observation"),
    )

    monkeypatch.setitem(entrypoint._DISPATCH, "deploy", lambda _request: raw_result)
    result = entrypoint._dispatch(request)

    assert validate_mutation_state("deploy", "succeeded", result.state)[
        "observations"
    ]["service_state"] == "running"


def test_translator_reuses_operation_owned_final_observation_without_reacquiring_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        entrypoint,
        "_observe_final_mutation",
        lambda _request: pytest.fail("translator must reuse the operation observation"),
    )

    def operation_result(request: HostRequest) -> HostResult:
        return HostResult.for_request(
            request,
            "retryable",
            "history failed",
            {
                "changed": True,
                "failed_boundary": "history",
                "backup_id": None,
                "report": passing_report(),
                "final_observations": mutation_observations(),
                "final_unavailable_fields": (),
                "final_inspection_error": None,
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
