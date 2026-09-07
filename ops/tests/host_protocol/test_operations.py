from __future__ import annotations

import pytest

from taskman_ops.host_protocol import (
    HostRequest,
    HostResult,
    OPERATION_NAMES,
    ProtocolError,
    decode_request,
    decode_result,
    encode_request,
    encode_result,
    operation_spec,
)


def request_for(operation: str) -> HostRequest:
    return HostRequest(
        protocol_version=2,
        operation=operation,
        correlation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={},
        paths={"install_root": "/opt/taskman"},
        parameters={},
    )


def result_for(operation: str) -> HostResult:
    return HostResult(
        protocol_version=2,
        operation=operation,
        correlation_id="op-0123456789abcdef0123456789abcdef",
        outcome="retryable",
        message="helper unavailable",
        state={},
        warnings=(),
    )


@pytest.mark.parametrize("operation", OPERATION_NAMES)
def test_each_declared_operation_round_trips_through_both_codecs(operation: str) -> None:
    """Removing an operation from either codec would strand its later helper policy."""

    assert decode_request(encode_request(request_for(operation))) == request_for(operation)
    assert decode_result(encode_result(result_for(operation))) == result_for(operation)


def test_operation_registry_has_explicit_read_only_and_mutating_boundaries() -> None:
    """A generic or unknown operation must not reach the privileged helper boundary."""

    assert operation_spec("discover").mutating is False
    assert operation_spec("verify").mutating is False
    assert operation_spec("deploy").mutating is True
    assert operation_spec("cleanup").mutating is True

    with pytest.raises(ProtocolError):
        operation_spec("arbitrary-command")
