"""Stable error categories for the workstation deployment controller.

The controller deliberately exposes a small, numeric error surface.  Workflow
modules can attach enough state for an operator to recover without exposing
the command's internal exception or any protected value.
"""

from __future__ import annotations

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

    def __init__(
        self,
        status: ExitStatus | int,
        stage: str,
        message: str,
        changed: bool = False,
        next_action: str | None = None,
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

        self.stage = stage
        self.message = message
        self.changed = bool(changed)
        self.next_action = next_action
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

    def as_dict(self) -> dict[str, object]:
        """Return stable, structured fields for report rendering."""

        try:
            from .output import redact

            stage = redact(self.stage)
            message = redact(self.message)
            next_action = redact(self.next_action)
        except Exception:  # pragma: no cover - defensive output boundary
            stage = "controller"
            message = "controller operation failed"
            next_action = None

        return {
            "status": self.status.name.lower(),
            "stage": stage,
            "message": message,
            "changed": self.changed,
            "next_action": next_action,
        }


__all__ = ["ExitStatus", "OpsError"]
