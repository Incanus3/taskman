"""Interactive first-administrator workflow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess
import sys
from typing import TextIO

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..output import WorkflowResult
from ..remote import run_interactive
from .operational_preflight import validate_operational_preflight


def _command_for_managed_root(managed_root: str) -> tuple[str, ...]:
    current = f"{managed_root}/current"
    return (
        "sudo",
        "--",
        "systemd-run",
        "--wait",
        "--pipe",
        "--collect",
        "--property=User=taskman",
        "--property=Group=taskman",
        f"--property=WorkingDirectory={current}",
        "--property=EnvironmentFile=/etc/taskman/taskman.env",
        "--",
        f"{current}/bin/create-admin",
    )


CREATE_ADMIN_COMMAND: tuple[str, ...] = _command_for_managed_root("/opt/taskman")


def create_admin_command(config: EnvironmentConfig) -> tuple[str, ...]:
    """Return the sole command allowed for this validated installation root."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("create-admin command requires a validated environment configuration")
    return _command_for_managed_root(config.managed_root.as_posix())


def run_create_admin(
    remote: object,
    config: EnvironmentConfig,
    *,
    dry_run: bool = False,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    session_runner: Callable[[EnvironmentConfig], int] | None = None,
) -> WorkflowResult:
    """Validate the host, then attach the local terminal to create-admin."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("create-admin requires a validated environment configuration")
    if not isinstance(dry_run, bool):
        raise TypeError("create-admin dry-run flag must be boolean")
    if not dry_run and not all(_attached(stream) for stream in (stdin, stdout, stderr)):
        raise OpsError(
            ExitStatus.LOCAL_PREREQUISITE,
            "create-admin",
            "create-admin requires a real local terminal on stdin, stdout, and stderr",
            changed=False,
            next_action="rerun create-admin from an interactive terminal",
        )

    validate_operational_preflight(remote, config)
    if dry_run:
        return WorkflowResult(
            command="create-admin",
            environment=config.name or "",
            changed=False,
            stage="planned",
            facts={
                "command": create_admin_command(config)[-1],
                "service_user": "taskman",
                "environment_file": "/etc/taskman/taskman.env",
            },
            next_action="run without --dry-run from a real local terminal",
        )

    status = (session_runner or run_create_admin_session)(config)
    if type(status) is not int or not 0 <= status <= 255:
        raise TypeError("interactive SSH returned an invalid process status")
    succeeded = status == 0
    return WorkflowResult(
        command="create-admin",
        environment=config.name or "",
        changed=succeeded,
        stage="administrator-created" if succeeded else "administrator-command-failed",
        facts={"remote_status": status},
        next_action=(
            "sign in over HTTPS and complete the remaining acceptance checks"
            if succeeded
            else "inspect the interactive command diagnostics before retrying"
        ),
        exit_status=ExitStatus.OK if succeeded else ExitStatus.RELEASE,
        process_status=status,
    )


def run_create_admin_session(
    config: EnvironmentConfig,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    temporary_directory: Path | None = None,
) -> int:
    """Invoke the only administrator command accepted by this workflow."""

    command = create_admin_command(config)
    return run_interactive(
        config,
        command,
        command_runner=command_runner,
        temporary_directory=temporary_directory,
    )


def _attached(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return isatty() is True
    except OSError:
        return False


__all__ = ["CREATE_ADMIN_COMMAND", "create_admin_command", "run_create_admin", "run_create_admin_session"]
