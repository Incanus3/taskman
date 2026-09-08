from __future__ import annotations

from dataclasses import FrozenInstanceError
import json

import pytest

from taskman_ops.host_protocol import (
    MAX_COLLECTION_ITEMS,
    MAX_INPUT_BYTES,
    MAX_NESTING_DEPTH,
    MAX_OUTPUT_BYTES,
    MAX_STRING_BYTES,
    HostRequest,
    HostResult,
    ProtocolError,
    decode_request,
    decode_result,
    encode_request,
    encode_result,
)
from taskman_ops.host_protocol import envelope


def request_mapping(**overrides: object) -> dict[str, object]:
    mapping: dict[str, object] = {
        "protocol_version": 2,
        "operation": "discover",
        "correlation_id": "op-0123456789abcdef0123456789abcdef",
        "expected_state": {"lifecycle": "unknown"},
        "paths": {
            "install_root": "/opt/taskman",
            "backup_root": "/var/backups/taskman",
        },
        "parameters": {"dry_run": False, "attempt": 1},
    }
    mapping.update(overrides)
    return mapping


def result_mapping(**overrides: object) -> dict[str, object]:
    mapping: dict[str, object] = {
        "protocol_version": 2,
        "operation": "discover",
        "correlation_id": "op-0123456789abcdef0123456789abcdef",
        "outcome": "succeeded",
        "message": "discovery completed",
        "state": {},
        "warnings": [],
    }
    mapping.update(overrides)
    return mapping


def test_request_round_trip_is_canonical_and_immutable() -> None:
    """Dropping canonical serialization or frozen validation breaks host correlation."""

    request = HostRequest(**request_mapping())

    encoded = encode_request(request)

    assert encoded == (
        b'{"correlation_id":"op-0123456789abcdef0123456789abcdef",'
        b'"expected_state":{"lifecycle":"unknown"},"operation":"discover",'
        b'"parameters":{"attempt":1,"dry_run":false},'
        b'"paths":{"backup_root":"/var/backups/taskman",'
        b'"install_root":"/opt/taskman"},"protocol_version":2}'
    )
    assert decode_request(encoded) == request
    with pytest.raises(FrozenInstanceError):
        request.operation = "verify"  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.paths["other"] = "/srv/taskman"  # type: ignore[index]


def test_result_round_trip_keeps_the_concise_final_state() -> None:
    """Omitting final result fields makes outcome variants ambiguous."""

    result = HostResult(**result_mapping())

    assert decode_result(encode_result(result)) == result
    encoded_mapping = json.loads(encode_result(result))
    assert set(encoded_mapping) == set(result_mapping())
    assert encoded_mapping["state"] == {}
    assert encoded_mapping["warnings"] == []


@pytest.mark.parametrize("outcome", ("succeeded", "refused", "retryable", "manual"))
def test_result_round_trip_preserves_each_final_outcome(outcome: str) -> None:
    """Removing a final outcome would collapse a distinct operator decision."""

    result = HostResult(**result_mapping(outcome=outcome))

    assert decode_result(encode_result(result)).outcome == outcome


@pytest.mark.parametrize(
    "result_overrides",
    (
        {"operation": "verify"},
        {"correlation_id": "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    ),
)
def test_result_request_matcher_rejects_every_unrelated_final_result(
    result_overrides: dict[str, object],
) -> None:
    """Relaxing version, operation, or correlation matching accepts another helper's result."""

    request = HostRequest(**request_mapping())
    result = HostResult(**result_mapping(**result_overrides))

    with pytest.raises(ProtocolError):
        envelope.validate_result_for_request(request, result)


def test_result_request_matcher_returns_the_exact_correlated_result() -> None:
    """Replacing a validated result would discard the helper's bounded final evidence."""

    request = HostRequest(**request_mapping())
    result = HostResult(**result_mapping())

    assert envelope.validate_result_for_request(request, result) is result


def test_merge_result_warning_keeps_the_newest_bounded_evidence_and_cleanup_warning() -> None:
    """Cleanup evidence stays visible when a valid result has reached its warning bound."""

    warnings = tuple(f"warning-{index}" for index in range(MAX_COLLECTION_ITEMS))
    result = HostResult(**result_mapping(warnings=warnings))

    merged = envelope.merge_result_warning(result, "transient helper cleanup was incomplete")

    assert merged.warnings == (
        *warnings[1:],
        "transient helper cleanup was incomplete",
    )
    assert envelope.merge_result_warning(merged, "transient helper cleanup was incomplete") is merged


@pytest.mark.parametrize(
    ("payload", "decoder"),
    [
        (b'\xff', decode_request),
        (b'{"protocol_version":2', decode_request),
        (encode_request(HostRequest(**request_mapping())) + b"\n", decode_request),
        (b'{"protocol_version":2,"protocol_version":2}', decode_request),
        (b'{"outcome":"succeeded"', decode_result),
    ],
)
def test_decoders_reject_non_single_well_formed_json_value(payload: bytes, decoder: object) -> None:
    """Permissive UTF-8, JSON, or duplicate handling can change the signed request meaning."""

    with pytest.raises(ProtocolError):
        decoder(payload)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol_version", True),
        ("protocol_version", 1),
        ("operation", "shell"),
        ("correlation_id", "op-0123456789abcdef0123456789abcdeg"),
        ("paths", {"install_root": "relative/path"}),
        ("paths", {"install_root": "/opt/../taskman"}),
        ("paths", {"install_root": "/opt//taskman"}),
        ("expected_state", {"bad key": "value"}),
    ],
)
def test_request_rejects_invalid_typed_or_path_values(field: str, value: object) -> None:
    """Relaxing request validation admits values that cannot be safely dispatched on a host."""

    with pytest.raises(ProtocolError):
        HostRequest(**request_mapping(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol_version", False),
        ("outcome", "partial"),
        ("message", 1),
        ("state", {"bad key": "value"}),
        ("warnings", ["same", "same"]),
    ],
)
def test_result_rejects_invalid_typed_or_enum_values(field: str, value: object) -> None:
    """A loose result decoder could turn malformed host evidence into a trusted result."""

    with pytest.raises(ProtocolError):
        HostResult(**result_mapping(**{field: value}))


def test_request_enforces_input_string_collection_and_nesting_limits() -> None:
    """Removing a protocol bound permits an unbounded controller-to-host payload."""

    nested: object = "leaf"
    for _ in range(MAX_NESTING_DEPTH + 1):
        nested = {"child": nested}

    oversized_cases = (
        request_mapping(parameters={"note": "x" * (MAX_STRING_BYTES + 1)}),
        request_mapping(parameters={"items": list(range(MAX_COLLECTION_ITEMS + 1))}),
        request_mapping(expected_state={"tree": nested}),
    )

    for mapping in oversized_cases:
        with pytest.raises(ProtocolError):
            HostRequest(**mapping)

    with pytest.raises(ProtocolError):
        decode_request(b"x" * (MAX_INPUT_BYTES + 1))


def test_result_enforces_output_byte_limit() -> None:
    """A result that exceeds the transport bound must never be emitted by the helper."""

    with pytest.raises(ProtocolError):
        decode_result(b"x" * (MAX_OUTPUT_BYTES + 1))

    with pytest.raises(ProtocolError):
        encode_result(
            HostResult(
                **result_mapping(warnings=["x" * (MAX_STRING_BYTES + 1)])
            )
        )


def test_request_and_result_require_exact_common_key_sets() -> None:
    """Accepting missing or extra envelope fields weakens the versioned contract."""

    incomplete_request = request_mapping()
    incomplete_request.pop("parameters")
    extra_result = result_mapping(unexpected={})

    with pytest.raises(ProtocolError):
        decode_request(json.dumps(incomplete_request).encode())
    with pytest.raises(ProtocolError):
        decode_result(json.dumps(extra_result).encode())


@pytest.mark.parametrize(
    "mapping",
    [
        request_mapping(parameters={"value": "\ud800"}),
        request_mapping(expected_state={"\ud800": "value"}),
        request_mapping(paths={"install_root": "/opt/\ud800"}),
    ],
)
def test_decoder_redacts_json_escaped_surrogates_in_values_keys_and_paths(
    mapping: dict[str, object],
) -> None:
    """UTF-8 length checks must not leak raw surrogate encoding exceptions."""

    payload = json.dumps(mapping, ensure_ascii=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(ProtocolError):
        decode_request(payload)
