"""Structured reports and the controller's single redaction boundary.

All values that cross the controller boundary (terminal output, JSON, and
exception rendering) pass through this module.  The redactor intentionally
works on structured values first and only falls back to text for objects that
do not have a supported structured representation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime, time
from enum import Enum
import copy
import json as json_module
from pathlib import Path, PurePath
from threading import RLock
from typing import Any, TypeVar


REDACTED = "[REDACTED]"
_T = TypeVar("_T")


class Redactor:
    """A small process-local registry of protected string values."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = RLock()

    def register(self, value: object) -> None:
        """Register a secret value for subsequent recursive redaction.

        Empty values are ignored: replacing an empty string would otherwise
        corrupt every output value.  Byte values are decoded losslessly where
        possible so a secret loaded from a protected byte channel is covered
        too.
        """

        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return
        if not isinstance(value, str) or not value:
            return
        with self._lock:
            self._secrets.add(value)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def values(self) -> tuple[str, ...]:
        with self._lock:
            # Replacing longer values first prevents a short registered value
            # from exposing the remainder of a longer credential.
            return tuple(sorted(self._secrets, key=len, reverse=True))

    def text(self, value: str) -> str:
        if not isinstance(value, str):
            value = str(value)
        for secret in self.values():
            value = value.replace(secret, REDACTED)
        return value

    def redact(self, value: _T) -> _T:
        return _redact_value(value, self, {})


_GLOBAL_REDACTOR = Redactor()


def register_secret(value: object) -> None:
    """Register a canary/credential in the process-wide output registry."""

    _GLOBAL_REDACTOR.register(value)


def clear_secrets() -> None:
    """Clear process-local registrations (primarily useful between tests)."""

    _GLOBAL_REDACTOR.clear()


def redact(value: _T) -> _T:
    """Recursively redact registered values in a structured object."""

    return _GLOBAL_REDACTOR.redact(value)


def _redact_value(value: Any, redactor: Redactor, seen: dict[int, Any]) -> Any:
    if isinstance(value, str):
        return redactor.text(value)
    if isinstance(value, bytes):
        # Bytes cannot be safely displayed as-is: they may contain a secret
        # that is not valid UTF-8.  Keep a deterministic, non-sensitive marker.
        return REDACTED.encode("utf-8")
    if value is None or isinstance(value, (bool, int, float)):
        return value

    identity = id(value)
    if identity in seen:
        # A dataclass may contain a direct self-reference.  During its first
        # pass we use a marker rather than returning the original object (which
        # could still contain a secret).
        prior = seen[identity]
        return REDACTED if prior is value else prior

    if isinstance(value, BaseException):
        return _redact_exception(value, redactor, seen)

    if is_dataclass(value) and not isinstance(value, type):
        return _redact_dataclass(value, redactor, seen)

    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        seen[identity] = result
        for key, item in value.items():
            safe_key = _redact_value(key, redactor, seen)
            safe_item = _redact_value(item, redactor, seen)
            try:
                result[safe_key] = safe_item
            except TypeError:
                # A redacted structured key should remain representable even
                # when its original type was not hashable.
                result[str(safe_key)] = safe_item
        return result

    if isinstance(value, list):
        result_list: list[Any] = []
        seen[identity] = result_list
        result_list.extend(_redact_value(item, redactor, seen) for item in value)
        return result_list

    if isinstance(value, tuple):
        # Named tuples are uncommon in controller facts; retaining their tuple
        # shape is more useful than invoking an arbitrary constructor.
        result_tuple = tuple(_redact_value(item, redactor, seen) for item in value)
        seen[identity] = result_tuple
        return result_tuple

    if isinstance(value, (set, frozenset)):
        result_set = type(value)(_redact_value(item, redactor, seen) for item in value)
        seen[identity] = result_set
        return result_set

    if isinstance(value, (Path, PurePath)):
        return redactor.text(str(value))

    if isinstance(value, Enum):
        return value

    # Date/time values are rendered by _json_safe, but redacting their text
    # representation here protects unusual subclasses with sensitive fields.
    if isinstance(value, (datetime, date, time)):
        return value

    # Unknown objects should never leak their repr into a report.  Rendering a
    # redacted string is deterministic and avoids invoking arbitrary serializers.
    try:
        rendered = str(value)
    except Exception:  # pragma: no cover - defensive boundary for bad reprs
        return REDACTED
    return redactor.text(rendered)


def _redact_exception(value: BaseException, redactor: Redactor, seen: dict[int, Any]) -> BaseException:
    try:
        result = copy.copy(value)
    except Exception:  # pragma: no cover - exotic exception classes
        result = RuntimeError(redactor.text(str(value)))

    seen[id(value)] = result
    try:
        result.args = tuple(_redact_value(item, redactor, seen) for item in value.args)
    except Exception:
        # ``args`` is writable on normal Exception classes; use a fixed safe
        # fallback for an unusual immutable exception implementation.
        try:
            result.args = (REDACTED,)
        except Exception:
            pass

    try:
        for key, item in vars(value).items():
            try:
                setattr(result, key, _redact_value(item, redactor, seen))
            except Exception:
                continue
    except TypeError:
        pass

    # Chained exceptions and notes are not always present in ``vars`` but are
    # included by common traceback/diagnostic renderers.
    for attribute in ("__cause__", "__context__", "__notes__"):
        try:
            nested = getattr(value, attribute)
            if nested is not None:
                setattr(result, attribute, _redact_value(nested, redactor, seen))
        except Exception:
            continue
    return result


def _redact_dataclass(value: Any, redactor: Redactor, seen: dict[int, Any]) -> Any:
    # Build constructor fields first.  ``dataclasses.replace`` preserves the
    # concrete type and works for frozen dataclasses used as typed facts.
    seen[id(value)] = REDACTED
    init_values: dict[str, Any] = {}
    non_init_values: list[tuple[str, Any]] = []
    for field in fields(value):
        safe = _redact_value(getattr(value, field.name), redactor, seen)
        if field.init:
            init_values[field.name] = safe
        else:
            non_init_values.append((field.name, safe))

    try:
        result = replace(value, **init_values)
    except Exception:
        try:
            result = copy.copy(value)
        except Exception:  # pragma: no cover - unusual dataclass implementation
            return redactor.text(str(value))

        for name, safe in init_values.items():
            try:
                object.__setattr__(result, name, safe)
            except Exception:
                continue

    seen[id(value)] = result
    for name, safe in non_init_values:
        try:
            object.__setattr__(result, name, safe)
        except Exception:
            continue
    return result


@dataclass(frozen=True)
class WorkflowResult:
    """The stable success report shared by all workflow entry points."""

    command: str
    environment: str
    changed: bool
    stage: str
    facts: Mapping[str, object]
    warnings: tuple[str, ...] = ()
    next_action: str | None = None


def _result_fields(result: WorkflowResult | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(result, WorkflowResult):
        return {
            "command": result.command,
            "environment": result.environment,
            "changed": result.changed,
            "stage": result.stage,
            "facts": result.facts,
            "warnings": result.warnings,
            "next_action": result.next_action,
        }
    if isinstance(result, Mapping):
        fields_map = dict(result)
        required = ("command", "environment", "changed", "stage", "facts")
        missing = [field for field in required if field not in fields_map]
        if missing:
            raise TypeError(f"result is missing required fields: {', '.join(missing)}")
        fields_map.setdefault("warnings", ())
        fields_map.setdefault("next_action", None)
        return fields_map
    raise TypeError("result must be a WorkflowResult or mapping")


def _json_safe(value: Any) -> Any:
    """Convert a redacted structured value into JSON-compatible data."""

    safe = redact(value)
    if safe is None or isinstance(safe, (str, bool, int, float)):
        return safe
    if isinstance(safe, Enum):
        return _json_safe(safe.value)
    if isinstance(safe, (datetime, date, time)):
        return safe.isoformat()
    if isinstance(safe, (Path, PurePath)):
        return str(safe)
    if isinstance(safe, bytes):
        return REDACTED
    if is_dataclass(safe) and not isinstance(safe, type):
        return {field.name: _json_safe(getattr(safe, field.name)) for field in fields(safe)}
    if isinstance(safe, Mapping):
        return {str(_json_safe(key)): _json_safe(item) for key, item in safe.items()}
    if isinstance(safe, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in safe]
    if isinstance(safe, BaseException):
        return redactor_text(str(safe))
    return redactor_text(str(safe))


def redactor_text(value: str) -> str:
    return _GLOBAL_REDACTOR.text(value)


def result_payload(result: WorkflowResult | Mapping[str, Any]) -> dict[str, Any]:
    """Return the schema-versioned success payload before JSON encoding."""

    fields_map = _result_fields(result)
    return {
        "schema_version": 1,
        "command": _json_safe(fields_map["command"]),
        "environment": _json_safe(fields_map["environment"]),
        "status": "ok",
        "changed": bool(fields_map["changed"]),
        "stage": _json_safe(fields_map["stage"]),
        "facts": _json_safe(fields_map["facts"]),
        "warnings": _json_safe(tuple(fields_map.get("warnings") or ())),
        "next_action": _json_safe(fields_map.get("next_action")),
    }


def render_json(result: WorkflowResult | Mapping[str, Any]) -> str:
    """Render a compact, deterministic, secret-free success JSON document."""

    return json_module.dumps(result_payload(result), ensure_ascii=False)


def _human_value(value: Any) -> str:
    safe = _json_safe(value)
    if isinstance(safe, (dict, list)):
        return json_module.dumps(safe, ensure_ascii=False, sort_keys=True)
    if safe is None:
        return "none"
    if safe is True:
        return "yes"
    if safe is False:
        return "no"
    return str(safe)


def render_human(result: WorkflowResult | Mapping[str, Any]) -> str:
    """Render the same fields as :func:`render_json` as an operator table."""

    payload = result_payload(result)
    lines = [
        f"Command: {_human_value(payload['command'])}",
        f"Environment: {_human_value(payload['environment'])}",
        f"Status: {_human_value(payload['status'])}",
        f"Changed: {_human_value(payload['changed'])}",
        f"Stage: {_human_value(payload['stage'])}",
        f"Facts: {_human_value(payload['facts'])}",
        f"Warnings: {_human_value(payload['warnings'])}",
        f"Next action: {_human_value(payload['next_action'])}",
    ]
    return "\n".join(redactor_text(line) for line in lines)


def render_table(rows: Mapping[str, Any] | Sequence[Any]) -> str:
    """Render arbitrary report rows through the same redaction boundary.

    This intentionally stays a plain, dependency-free table: later discovery
    workflows can supply lists of records without coupling this foundation to
    a terminal-table package.
    """

    if isinstance(rows, Mapping):
        rows = [rows]
    # Redact each row before collecting headers so a secret used as a key is
    # handled consistently with a secret used as a value.
    rows_list = []
    for row in rows:
        safe_row = redact(row)
        if isinstance(safe_row, Mapping):
            rows_list.append(dict(safe_row))
        elif is_dataclass(safe_row) and not isinstance(safe_row, type):
            rows_list.append({field.name: getattr(safe_row, field.name) for field in fields(safe_row)})
        elif isinstance(safe_row, BaseException):
            rows_list.append({"error": str(safe_row)})
        else:
            rows_list.append({"value": safe_row})
    if not rows_list:
        return ""
    headers: list[str] = []
    for row in rows_list:
        for key in row:
            text_key = redactor_text(str(key))
            if text_key not in headers:
                headers.append(text_key)
    values = [[_human_value(row.get(header, "")) for header in headers] for row in rows_list]
    widths = [max(len(header), *(len(row[index]) for row in values)) for index, header in enumerate(headers)]
    header_line = " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    separator = "-+-".join("-" * width for width in widths)
    body = [" | ".join(row[index].ljust(widths[index]) for index in range(len(headers))) for row in values]
    return "\n".join(redactor_text(line) for line in [header_line, separator, *body])


def error_payload(
    error: Any,
    *,
    command: str = "unknown",
    environment: str = "",
) -> dict[str, Any]:
    """Build the versioned JSON shape for an :class:`~taskman_ops.errors.OpsError`."""

    # Keep this import lazy to avoid errors.py/output.py import cycles.
    from .errors import ExitStatus, OpsError

    if not isinstance(error, OpsError):
        error = OpsError(
            status=ExitStatus.LOCAL_PREREQUISITE,
            stage="controller",
            message="controller operation failed",
            changed=False,
            next_action=None,
        )
    return {
        "schema_version": 1,
        "command": _json_safe(command),
        "environment": _json_safe(environment),
        "status": "error",
        "changed": bool(error.changed),
        "stage": _json_safe(error.stage),
        "facts": {},
        "warnings": [],
        "next_action": _json_safe(error.next_action),
        "error": {
            "code": error.status.value,
            "category": error.status.name.lower(),
            "message": _json_safe(error.message),
        },
    }


def render_error(
    error: Any,
    *,
    command: str = "unknown",
    environment: str = "",
    json_output: bool = False,
    json: bool | None = None,
) -> str:
    """Render an expected controller error without disclosing its details."""

    if json is not None:
        json_output = json
    if json_output:
        return json_module.dumps(error_payload(error, command=command, environment=environment), ensure_ascii=False)

    from .errors import ExitStatus, OpsError

    if not isinstance(error, OpsError):
        error = OpsError(ExitStatus.LOCAL_PREREQUISITE, "controller", "controller operation failed")
    lines = [
        f"Command: {redactor_text(command)}",
        f"Environment: {redactor_text(environment)}",
        f"Status: error ({error.status.value})",
        f"Changed: {_human_value(error.changed)}",
        f"Stage: {redactor_text(error.stage)}",
        f"Message: {redactor_text(error.message)}",
        f"Next action: {_human_value(error.next_action)}",
    ]
    return "\n".join(redactor_text(line) for line in lines)


__all__ = [
    "REDACTED",
    "Redactor",
    "WorkflowResult",
    "clear_secrets",
    "error_payload",
    "redact",
    "register_secret",
    "render_error",
    "render_human",
    "render_json",
    "render_table",
    "result_payload",
]
