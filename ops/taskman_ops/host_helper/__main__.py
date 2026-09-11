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


_DISPATCH: dict[str, Callable[[HostRequest], HostResult]] = {
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

if frozenset(_DISPATCH) != frozenset(OPERATION_NAMES):
    raise RuntimeError("helper dispatch does not cover the protocol operations")


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


def _dispatch(request: HostRequest) -> HostResult:
    """Invoke exactly one final-protocol helper operation."""

    try:
        handler = _DISPATCH[request.operation]
    except KeyError as error:
        raise ValueError("helper operation is unsupported") from error
    result = handler(request)
    if not isinstance(result, HostResult):
        raise TypeError("helper returned an invalid result")
    return result


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
                result = _dispatch(request)
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
