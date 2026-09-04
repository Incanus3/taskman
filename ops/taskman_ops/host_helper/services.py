"""Exact Taskman service transitions used by host procedures."""

from __future__ import annotations

from .commands import run_command


_COMMAND_TIMEOUT_SECONDS = 60.0


def change_service(action: str) -> None:
    """Start or stop only the managed Taskman systemd unit."""

    if action not in {"start", "stop"}:
        raise ValueError("service action is invalid")
    run_command(("systemctl", action, "taskman.service"), timeout_seconds=_COMMAND_TIMEOUT_SECONDS)


__all__ = ["change_service"]
