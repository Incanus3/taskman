"""Narrow, non-secret protocol identifiers and POSIX path validation."""

from __future__ import annotations

from pathlib import PurePosixPath
import re


MAX_IDENTIFIER_BYTES = 64
MAX_PATH_BYTES = 1024
MAX_STRING_BYTES = 4096

_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_OPERATION_ID_RE = re.compile(r"op-[0-9a-f]{32}\Z")


class ProtocolError(ValueError):
    """Raised when an untrusted protocol value is outside the fixed contract."""


def _utf8_length(value: str) -> int:
    """Return a wire-string byte length without leaking surrogate encoding errors."""

    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ProtocolError("invalid UTF-8 string") from error


def validate_identifier(value: object) -> str:
    """Return one bounded lower-case identifier without echoing rejected input."""

    if (
        type(value) is not str
        or _utf8_length(value) > MAX_IDENTIFIER_BYTES
        or _IDENTIFIER_RE.fullmatch(value) is None
    ):
        raise ProtocolError("invalid identifier")
    return value


def validate_operation_id(value: object) -> str:
    """Return the controller-generated operation correlation identifier."""

    if type(value) is not str or _OPERATION_ID_RE.fullmatch(value) is None:
        raise ProtocolError("invalid operation identifier")
    return value


def validate_string(value: object, *, maximum: int = MAX_STRING_BYTES) -> str:
    """Return a UTF-8 bounded string accepted by the wire format."""

    if type(value) is not str or "\x00" in value or _utf8_length(value) > maximum:
        raise ProtocolError("invalid string")
    return value


def validate_absolute_path(value: object) -> str:
    """Return an absolute, lexical-normalized POSIX path."""

    if type(value) is not str or "\x00" in value or "\\" in value:
        raise ProtocolError("invalid absolute path")
    if _utf8_length(value) > MAX_PATH_BYTES or not value.startswith("/"):
        raise ProtocolError("invalid absolute path")
    if value.startswith("//"):
        raise ProtocolError("invalid absolute path")

    path = PurePosixPath(value)
    if str(path) != value or ".." in path.parts:
        raise ProtocolError("invalid absolute path")
    return value


__all__ = [
    "MAX_IDENTIFIER_BYTES",
    "MAX_PATH_BYTES",
    "MAX_STRING_BYTES",
    "ProtocolError",
    "validate_absolute_path",
    "validate_identifier",
    "validate_operation_id",
    "validate_string",
]
