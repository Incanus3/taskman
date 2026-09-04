"""Public bounded protocol interface shared by controller and host helper."""

from .envelope import (
    MAX_COLLECTION_ITEMS,
    MAX_INPUT_BYTES,
    MAX_NESTING_DEPTH,
    MAX_OUTPUT_BYTES,
    PROTOCOL_VERSION,
    HostRequest,
    HostResult,
    decode_request,
    decode_result,
    encode_request,
    encode_result,
)
from .identifiers import MAX_STRING_BYTES, ProtocolError, validate_correlation_id
from .operations import OPERATION_NAMES


__all__ = [
    "MAX_COLLECTION_ITEMS",
    "MAX_INPUT_BYTES",
    "MAX_NESTING_DEPTH",
    "MAX_OUTPUT_BYTES",
    "MAX_STRING_BYTES",
    "OPERATION_NAMES",
    "PROTOCOL_VERSION",
    "HostRequest",
    "HostResult",
    "ProtocolError",
    "validate_correlation_id",
    "decode_request",
    "decode_result",
    "encode_request",
    "encode_result",
]
