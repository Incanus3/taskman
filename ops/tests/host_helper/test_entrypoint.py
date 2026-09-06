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
    MAX_STRING_BYTES,
    HostRequest,
    HostResult,
    ProtocolError,
    decode_result,
    encode_request,
)
from taskman_ops.host_helper import __main__ as entrypoint


def request_bytes(paths: dict[str, str] | None = None) -> bytes:
    return encode_request(
        HostRequest(
            protocol_version=1,
            operation="discover",
            operation_id="op-0123456789abcdef0123456789abcdef",
            expected_state={},
            paths=paths
            or {
                "install_root": "/opt/taskman",
                "backup_root": "/var/backups/taskman",
            },
            parameters={},
        )
    )


class Stream:
    def __init__(self, payload: bytes = b"") -> None:
        self.buffer = BytesIO(payload)


@contextmanager
def invoke_entrypoint(
    payload: bytes,
    handler: Callable[[HostRequest], HostResult],
) -> Iterator[Stream]:
    original_stdin = entrypoint.sys.stdin
    original_stdout = entrypoint.sys.stdout
    original_handler = entrypoint._DISPATCH["discover"]
    stdout = Stream()
    entrypoint.sys.stdin = Stream(payload)
    entrypoint.sys.stdout = stdout
    entrypoint._DISPATCH["discover"] = handler
    try:
        yield stdout
    finally:
        entrypoint.sys.stdin = original_stdin
        entrypoint.sys.stdout = original_stdout
        entrypoint._DISPATCH["discover"] = original_handler


def test_built_zipapp_discovers_an_isolated_empty_host_with_a_bounded_result(tmp_path: Path) -> None:
    """The isolated archive reads only the two roots supplied by its request."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    completed = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=request_bytes(
            {
                "install_root": str(tmp_path / "install"),
                "backup_root": str(tmp_path / "backups"),
            }
        ),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    result = decode_result(completed.stdout)
    assert result.operation == "discover"
    assert result.operation_id == "op-0123456789abcdef0123456789abcdef"
    assert result.outcome == "succeeded"
    assert result.stage == "discovered"
    assert result.lifecycle["state"] == "empty"


def test_built_zipapp_rejects_oversized_input_without_echoing_it(tmp_path: Path) -> None:
    """Reading unbounded stdin or echoing it could leak controller-supplied secret material."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    secret = b"canary-secret-value"
    completed = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=secret + (b"x" * (MAX_INPUT_BYTES + 1)),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    result = decode_result(completed.stdout)
    assert result.outcome == "failed"
    assert result.stage == "protocol"
    assert secret not in completed.stdout


def test_entrypoint_replaces_an_oversized_dispatch_result_with_small_internal_failure() -> None:
    """A valid but oversized operation result must not produce a traceback or empty stdout."""

    request = request_bytes()

    def oversized_result(parsed: HostRequest) -> HostResult:
        return HostResult(
            protocol_version=1,
            operation=parsed.operation,
            operation_id=parsed.operation_id,
            outcome="failed",
            stage="complete",
            changed_stages=(),
            lifecycle={},
            runtime_state={},
            verification={},
            residue_paths=(),
            recovery_actions=(),
            warnings=tuple(
                f"{index:02d}" + ("x" * (MAX_STRING_BYTES - 2))
                for index in range(64)
            ),
        )

    with invoke_entrypoint(request, oversized_result) as stdout:
        try:
            assert entrypoint.main() == 0
        except ProtocolError as error:
            pytest.fail(f"entrypoint leaked encoding failure: {error}")

    result = decode_result(stdout.buffer.getvalue())
    assert len(stdout.buffer.getvalue()) <= MAX_OUTPUT_BYTES
    assert result.outcome == "failed"
    assert result.stage == "internal"
    assert result.operation == "discover"
    assert result.operation_id == "op-0123456789abcdef0123456789abcdef"


def test_entrypoint_preserves_trusted_correlation_after_dispatch_failure() -> None:
    """An internal operation error must remain correlated to the request the controller sent."""

    def fail_dispatch(_parsed: HostRequest) -> HostResult:
        raise RuntimeError("untrusted detail")

    with invoke_entrypoint(request_bytes(), fail_dispatch) as stdout:
        assert entrypoint.main() == 0

    result = decode_result(stdout.buffer.getvalue())
    assert result.outcome == "failed"
    assert result.stage == "internal"
    assert result.operation == "discover"
    assert result.operation_id == "op-0123456789abcdef0123456789abcdef"
