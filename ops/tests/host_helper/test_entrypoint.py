from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import subprocess
import sys
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
