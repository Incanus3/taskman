from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import subprocess
from typing import Callable, Iterator

import pytest

from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    HostRequest,
    HostResult,
    ProtocolError,
    decode_result,
    encode_request,
)
from taskman_ops.host_helper import __main__ as entrypoint
from taskman_ops.host_helper.legacy_result import OperationRequest, OperationResult, project_result


CORRELATION = "op-0123456789abcdef0123456789abcdef"


def request_bytes(
    paths: dict[str, str] | None = None,
    *,
    operation: str = "discover",
) -> bytes:
    return encode_request(
        HostRequest(
            protocol_version=2,
            operation=operation,
            correlation_id=CORRELATION,
            expected_state={},
            paths=paths or {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
            parameters={},
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


def test_built_zipapp_emits_the_final_discovery_envelope(tmp_path: Path) -> None:
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    completed = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=request_bytes({"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    result = decode_result(completed.stdout)
    assert result.operation == "discover"
    assert result.correlation_id == CORRELATION
    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] is None
    assert result.state["releases"] == ()
    assert result.state["backups"] == ()
    assert set(result.to_mapping()) == {
        "protocol_version", "operation", "correlation_id", "outcome", "message", "state", "warnings"
    }


def test_entrypoint_rejects_oversized_input_without_echoing_it(tmp_path: Path) -> None:
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    secret = b"canary-secret-value"
    completed = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=secret + (b"x" * (MAX_INPUT_BYTES + 1)),
        capture_output=True,
        check=False,
    )

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert result.outcome == "retryable"
    assert result.message == "helper protocol failure"
    assert secret not in completed.stdout


def test_entrypoint_dispatches_backup_on_the_final_protocol_without_a_legacy_bridge() -> None:
    """Routing backup through OperationRequest would reintroduce deleted recovery evidence."""

    received: list[HostRequest] = []

    def final_result(request: object) -> HostResult:
        assert isinstance(request, HostRequest)
        received.append(request)
        return HostResult(
            2,
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
        return HostResult(2, operation, request.correlation_id, "refused", "unsafe", {}, ())

    with invoke_entrypoint(request_bytes(operation=operation), final_result, operation=operation) as stdout:
        assert entrypoint.main() == 0

    assert decode_result(stdout.buffer.getvalue()).outcome == "refused"
    assert received[0].operation == operation


def test_entrypoint_replaces_projection_failure_with_small_final_result() -> None:
    def invalid_private_result(request: OperationRequest) -> OperationResult:
        return OperationResult(
            2, request.operation, request.operation_id, "unknown", "internal", (), {}, {}, {}, (), (), (),
        )

    with invoke_entrypoint(
        request_bytes(operation="backup"), invalid_private_result, operation="backup"
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
