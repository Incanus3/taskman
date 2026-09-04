"""Stable error categories for the workstation deployment controller.

The controller deliberately exposes a small, numeric error surface.  Workflow
modules can attach enough state for an operator to recover without exposing
the command's internal exception or any protected value.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
from typing import Any


class ExitStatus(IntEnum):
    """Exit codes shared by every controller command."""

    OK = 0
    INVALID = 2
    LOCAL_PREREQUISITE = 3
    SECRET = 4
    REMOTE_PREFLIGHT = 5
    BACKUP = 6
    MIGRATION = 7
    RELEASE = 8
    READINESS = 9
    SAFETY = 10
    RESTORE = 11
    LOCKED = 12


class OpsError(Exception):
    """An expected, operator-facing controller failure.

    ``message`` and ``next_action`` are kept as supplied so callers can make a
    structured report.  Their string rendering is passed through the shared
    redaction boundary at the point of output (and by :meth:`__str__`).
    """

    status: ExitStatus
    stage: str
    message: str
    changed: bool
    next_action: str | None
    state: Mapping[str, object]
    warnings: tuple[str, ...]

    def __init__(
        self,
        status: ExitStatus | int,
        stage: str,
        message: str,
        changed: bool = False,
        next_action: str | None = None,
        *,
        state: Mapping[str, object] | None = None,
        warnings: tuple[str, ...] = (),
    ) -> None:
        try:
            self.status = status if isinstance(status, ExitStatus) else ExitStatus(status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown controller exit status: {status!r}") from exc

        if not isinstance(stage, str) or not stage:
            raise ValueError("stage must be a non-empty string")
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        if next_action is not None and not isinstance(next_action, str):
            raise TypeError("next_action must be a string or None")
        if state is not None and not isinstance(state, Mapping):
            raise TypeError("state must be a mapping or None")
        if not isinstance(warnings, tuple) or not all(isinstance(warning, str) for warning in warnings):
            raise TypeError("warnings must be a tuple of strings")

        self.stage = stage
        self.message = message
        self.changed = bool(changed)
        self.next_action = next_action
        self.state = {} if state is None else dict(state)
        self.warnings = warnings
        # Do not pass structured metadata as Exception.args: traceback and
        # default reprs should have only the operator message to redact.
        super().__init__(message)

    def __str__(self) -> str:
        # Import lazily to keep errors.py independent from output.py's data
        # structures and avoid an import cycle.
        try:
            from .output import redact

            message = redact(self.message)
        except Exception:  # pragma: no cover - defensive output boundary
            message = "controller operation failed"
        return str(message)

    def __repr__(self) -> str:
        # ``Exception.__repr__`` uses args and can bypass the redaction layer.
        # Keep the diagnostic representation intentionally small and safe.
        return (
            f"{type(self).__name__}(status={self.status.value!r}, "
            f"stage={self._safe_text(self.stage)!r}, "
            f"message={self._safe_text(self.message)!r}, "
            f"changed={self.changed!r}, next_action={self._safe_text(self.next_action)!r})"
        )

    @staticmethod
    def _safe_text(value: Any) -> str | None:
        if value is None:
            return None
        try:
            from .output import redact

            return str(redact(str(value)))
        except Exception:  # pragma: no cover - defensive output boundary
            return "[REDACTED]"


class HelperTransportError(OpsError):
    """A helper transport failure with the only dispatch fact callers need."""

    helper_entry_dispatched: bool

    def __init__(self, error: OpsError, *, helper_entry_dispatched: bool) -> None:
        if type(helper_entry_dispatched) is not bool:
            raise TypeError("helper_entry_dispatched must be a boolean")
        super().__init__(
            error.status,
            error.stage,
            error.message,
            error.changed,
            error.next_action,
            state=error.state,
            warnings=error.warnings,
        )
        self.helper_entry_dispatched = helper_entry_dispatched


__all__ = ["ExitStatus", "HelperTransportError", "OpsError"]
