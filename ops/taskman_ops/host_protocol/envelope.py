"""Strict bounded codecs shared unchanged by controller and transient helper."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Any

from .identifiers import (
    MAX_STRING_BYTES,
    ProtocolError,
    validate_absolute_path,
    validate_identifier,
    validate_correlation_id,
    validate_string,
)
from .operations import validate_operation


PROTOCOL_VERSION = 2
MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_COLLECTION_ITEMS = 64
MAX_NESTING_DEPTH = 8

_REQUEST_KEYS = frozenset(
    {
        "protocol_version",
        "operation",
        "correlation_id",
        "expected_state",
        "paths",
        "parameters",
    }
)
_RESULT_KEYS = frozenset(
    {
        "protocol_version",
        "operation",
        "correlation_id",
        "outcome",
        "message",
        "state",
        "warnings",
    }
)
_OUTCOMES = frozenset({"succeeded", "refused", "retryable", "manual"})


def _require_exact_keys(mapping: object, expected: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(mapping, Mapping) or set(mapping) != expected:
        raise ProtocolError("invalid envelope keys")
    return mapping


def _validate_version(value: object) -> int:
    if type(value) is not int or value != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    return value


def _freeze_json(value: object, *, depth: int) -> object:
    if depth > MAX_NESTING_DEPTH:
        raise ProtocolError("maximum nesting depth exceeded")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is str:
        return validate_string(value)
    if isinstance(value, Mapping):
        return _freeze_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ProtocolError("maximum collection size exceeded")
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    raise ProtocolError("unsupported JSON value")


def _freeze_mapping(value: object, *, depth: int = 1) -> Mapping[str, object]:
    if depth > MAX_NESTING_DEPTH:
        raise ProtocolError("maximum nesting depth exceeded")
    if not isinstance(value, Mapping) or len(value) > MAX_COLLECTION_ITEMS:
        raise ProtocolError("invalid mapping")

    frozen: dict[str, object] = {}
    for key, item in value.items():
        frozen[validate_identifier(key)] = _freeze_json(item, depth=depth)
    return MappingProxyType(frozen)


def _freeze_paths(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or len(value) > MAX_COLLECTION_ITEMS:
        raise ProtocolError("invalid paths")
    frozen: dict[str, str] = {}
    for key, path in value.items():
        frozen[validate_identifier(key)] = validate_absolute_path(path)
    return MappingProxyType(frozen)


def _freeze_string_sequence(
    value: object,
    *,
    validator: Callable[[object], str] = validate_string,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_COLLECTION_ITEMS:
        raise ProtocolError("invalid sequence")
    frozen = tuple(validator(item) for item in value)
    if len(set(frozen)) != len(frozen):
        raise ProtocolError("duplicate sequence value")
    return frozen


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class HostRequest:
    """One bounded controller-to-helper request."""

    protocol_version: int
    operation: str
    correlation_id: str
    expected_state: Mapping[str, object]
    paths: Mapping[str, str]
    parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "protocol_version", _validate_version(self.protocol_version))
        object.__setattr__(self, "operation", validate_operation(self.operation))
        object.__setattr__(self, "correlation_id", validate_correlation_id(self.correlation_id))
        object.__setattr__(self, "expected_state", _freeze_mapping(self.expected_state))
        object.__setattr__(self, "paths", _freeze_paths(self.paths))
        object.__setattr__(self, "parameters", _freeze_mapping(self.parameters))

    def to_mapping(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "operation": self.operation,
            "correlation_id": self.correlation_id,
            "expected_state": _json_value(self.expected_state),
            "paths": _json_value(self.paths),
            "parameters": _json_value(self.parameters),
        }

    @classmethod
    def from_mapping(cls, mapping: object) -> "HostRequest":
        value = _require_exact_keys(mapping, _REQUEST_KEYS)
        return cls(
            protocol_version=value["protocol_version"],
            operation=value["operation"],
            correlation_id=value["correlation_id"],
            expected_state=value["expected_state"],
            paths=value["paths"],
            parameters=value["parameters"],
        )


@dataclass(frozen=True)
class HostResult:
    """One bounded helper-to-controller result with concise final state."""

    protocol_version: int
    operation: str
    correlation_id: str
    outcome: str
    message: str
    state: Mapping[str, object]
    warnings: tuple[str, ...]
    local_cleanup_incomplete: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.outcome) is not str or self.outcome not in _OUTCOMES:
            raise ProtocolError("invalid outcome")
        object.__setattr__(self, "protocol_version", _validate_version(self.protocol_version))
        object.__setattr__(self, "operation", validate_operation(self.operation))
        object.__setattr__(self, "correlation_id", validate_correlation_id(self.correlation_id))
        object.__setattr__(self, "message", validate_string(self.message))
        object.__setattr__(self, "state", _freeze_mapping(self.state))
        object.__setattr__(self, "warnings", _freeze_string_sequence(self.warnings))
        if type(self.local_cleanup_incomplete) is not bool:
            raise ProtocolError("invalid local cleanup state")

    def to_mapping(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "operation": self.operation,
            "correlation_id": self.correlation_id,
            "outcome": self.outcome,
            "message": self.message,
            "state": _json_value(self.state),
            "warnings": _json_value(self.warnings),
        }

    @classmethod
    def from_mapping(cls, mapping: object) -> "HostResult":
        value = _require_exact_keys(mapping, _RESULT_KEYS)
        return cls(
            protocol_version=value["protocol_version"],
            operation=value["operation"],
            correlation_id=value["correlation_id"],
            outcome=value["outcome"],
            message=value["message"],
            state=value["state"],
            warnings=value["warnings"],
        )


def _reject_non_integer_number(_value: str) -> None:
    raise ProtocolError("non-integer JSON number")


def _reject_json_constant(_value: str) -> None:
    raise ProtocolError("invalid JSON constant")


def _object_pairs(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    mapping: dict[str, object] = {}
    for key, value in pairs:
        if key in mapping:
            raise ProtocolError("duplicate JSON object key")
        mapping[key] = value
    return mapping


_JSON_DECODER = json.JSONDecoder(
    object_pairs_hook=_object_pairs,
    parse_float=_reject_non_integer_number,
    parse_constant=_reject_json_constant,
)


def _decode_json(payload: object, *, maximum: int) -> object:
    if type(payload) is not bytes or len(payload) > maximum:
        raise ProtocolError("protocol payload exceeds byte limit")
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ProtocolError("protocol payload is not UTF-8") from error
    try:
        value, end = _JSON_DECODER.raw_decode(text)
    except ProtocolError:
        raise
    except (TypeError, ValueError) as error:
        raise ProtocolError("protocol payload is not valid JSON") from error
    if end != len(text):
        raise ProtocolError("protocol payload has trailing bytes")
    return value


def _encode_json(mapping: Mapping[str, object], *, maximum: int) -> bytes:
    try:
        payload = json.dumps(
            mapping,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolError("protocol value cannot be encoded") from error
    if len(payload) > maximum:
        raise ProtocolError("protocol payload exceeds byte limit")
    return payload


def encode_request(request: HostRequest) -> bytes:
    """Encode one validated request with canonical JSON bytes."""

    if not isinstance(request, HostRequest):
        raise ProtocolError("invalid request")
    return _encode_json(request.to_mapping(), maximum=MAX_INPUT_BYTES)


def decode_request(payload: bytes) -> HostRequest:
    """Decode exactly one bounded request, rejecting malformed or trailing input."""

    return HostRequest.from_mapping(_decode_json(payload, maximum=MAX_INPUT_BYTES))


def encode_result(result: HostResult) -> bytes:
    """Encode one validated result with canonical JSON bytes."""

    if not isinstance(result, HostResult):
        raise ProtocolError("invalid result")
    return _encode_json(result.to_mapping(), maximum=MAX_OUTPUT_BYTES)


def decode_result(payload: bytes) -> HostResult:
    """Decode exactly one bounded result, rejecting malformed or trailing output."""

    return HostResult.from_mapping(_decode_json(payload, maximum=MAX_OUTPUT_BYTES))


def validate_result_for_request(request: object, result: object) -> HostResult:
    """Return only the final result correlated to its exact helper request."""

    if not isinstance(request, HostRequest) or not isinstance(result, HostResult):
        raise ProtocolError("invalid helper result")
    if (
        result.protocol_version != request.protocol_version
        or result.operation != request.operation
        or result.correlation_id != request.correlation_id
    ):
        raise ProtocolError("helper result does not match its request")
    return result


def merge_result_warning(result: object, warning: object) -> HostResult:
    """Attach one warning, retaining newest evidence when the fixed bound is full."""

    if not isinstance(result, HostResult):
        raise ProtocolError("invalid helper result")
    warning = validate_string(warning)
    if warning in result.warnings:
        return result
    warnings = (
        result.warnings[1:]
        if len(result.warnings) == MAX_COLLECTION_ITEMS
        else result.warnings
    )
    return HostResult(
        protocol_version=result.protocol_version,
        operation=result.operation,
        correlation_id=result.correlation_id,
        outcome=result.outcome,
        message=result.message,
        state=result.state,
        warnings=(*warnings, warning),
    )


__all__ = [
    "MAX_COLLECTION_ITEMS",
    "MAX_INPUT_BYTES",
    "MAX_NESTING_DEPTH",
    "MAX_OUTPUT_BYTES",
    "PROTOCOL_VERSION",
    "HostRequest",
    "HostResult",
    "decode_request",
    "decode_result",
    "encode_request",
    "encode_result",
    "merge_result_warning",
    "validate_result_for_request",
]
