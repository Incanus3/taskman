"""Bounded stdin/stdout entry point for the transient host helper."""

from __future__ import annotations

import sys
from typing import Callable

from taskman_ops.host_protocol import (
    MAX_INPUT_BYTES,
    OPERATION_NAMES,
    PROTOCOL_VERSION,
    HostRequest,
    HostResult,
    ProtocolError,
    decode_request,
    encode_result,
)
from taskman_ops.host_helper.legacy_result import (
    OperationRequest,
    OperationResult,
    project_result,
)
from taskman_ops.host_helper.operations.discover import discover, list_backups, list_releases
from taskman_ops.host_helper.operations.deploy import deploy, genesis
from taskman_ops.host_helper.operations.backup import backup
from taskman_ops.host_helper.operations.cleanup import cleanup
from taskman_ops.host_helper.operations.rollback import rollback
from taskman_ops.host_helper.operations.restore import restore
from taskman_ops.host_helper.verification import verify


_FALLBACK_OPERATION = "discover"
_FALLBACK_CORRELATION_ID = "op-00000000000000000000000000000000"


def _failure_result(
    *,
    operation: str = _FALLBACK_OPERATION,
    correlation_id: str = _FALLBACK_CORRELATION_ID,
    outcome: str = "retryable",
    message: str,
) -> HostResult:
    """Return fixed redacted state without serializing input or exceptions."""

    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=operation,
        correlation_id=correlation_id,
        outcome=outcome,
        message=message,
        state={},
        warnings=(),
    )


def _unavailable(request: OperationRequest) -> OperationResult:
    """Keep dispatch total until a later slice attaches real host-local behavior."""

    return OperationResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="failed",
        stage="unavailable",
        changed_stages=(),
        lifecycle={},
        runtime_state={},
        verification={},
        residue_paths=(),
        recovery_actions=(),
        warnings=(),
    )


_DISPATCH: dict[str, Callable[[OperationRequest], OperationResult]] = {
    operation: _unavailable for operation in OPERATION_NAMES
}
_DISPATCH.update(
    {
        "discover": discover,
        "list_releases": list_releases,
        "list_backups": list_backups,
        "verify": verify,
        "deploy": deploy,
        "genesis": genesis,
        "backup": backup,
        "cleanup": cleanup,
        "rollback": rollback,
        "restore": restore,
    }
)


def _encode_or_internal_failure(
    result: HostResult,
    *,
    operation: str,
    correlation_id: str,
) -> bytes:
    """Reduce serialization failures to the fixed, known-small internal result."""

    try:
        return encode_result(result)
    except Exception:
        return encode_result(
            _failure_result(
                operation=operation,
                correlation_id=correlation_id,
                message="helper internal failure",
            )
        )


def main() -> int:
    """Read one bounded request and emit exactly one bounded redacted result."""

    payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    operation = _FALLBACK_OPERATION
    correlation_id = _FALLBACK_CORRELATION_ID
    if len(payload) > MAX_INPUT_BYTES:
        result = _failure_result(message="helper protocol failure")
    else:
        try:
            request = decode_request(payload)
        except ProtocolError:
            result = _failure_result(message="helper protocol failure")
        else:
            operation = request.operation
            correlation_id = request.correlation_id
            try:
                result = project_result(
                    request,
                    _DISPATCH[request.operation](OperationRequest.from_request(request)),
                )
            except Exception:
                result = _failure_result(
                    operation=operation,
                    correlation_id=correlation_id,
                    message="helper internal failure",
                )

    sys.stdout.buffer.write(
        _encode_or_internal_failure(
            result,
            operation=operation,
            correlation_id=correlation_id,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
