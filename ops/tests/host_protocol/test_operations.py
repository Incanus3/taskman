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
)
import taskman_ops.host_protocol as host_protocol


EXPECTED_OPERATIONS = frozenset(
    {
        "backup",
        "cleanup",
        "deploy",
        "discover",
        "genesis",
        "list_backups",
        "list_releases",
        "restore",
        "rollback",
        "verify",
    }
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


def test_operation_vocabulary_is_exact_immutable_and_rejects_unknown_commands() -> None:
    """A missing or arbitrary operation would desynchronize privileged dispatch."""

    assert isinstance(OPERATION_NAMES, frozenset)
    assert OPERATION_NAMES == EXPECTED_OPERATIONS

    with pytest.raises(ProtocolError):
        HostRequest(
            protocol_version=2,
            operation="arbitrary-command",
            correlation_id="op-0123456789abcdef0123456789abcdef",
            expected_state={},
            paths={"install_root": "/opt/taskman"},
            parameters={},
        )


def test_protocol_package_exposes_no_specification_compatibility_aliases() -> None:
    """Reintroducing the deleted registry API would create a second operation authority."""

    assert not {
        "OperationSpec",
        "OPERATION_SPECS",
        "operation_spec",
    } & set(host_protocol.__all__)
