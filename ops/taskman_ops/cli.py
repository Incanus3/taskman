"""Command-line boundary for workstation deployment workflows.

This task establishes parsing and reporting only.  Later workflow modules can
replace :func:`dispatch` while retaining the same typed invocation and exit
contract.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr
from dataclasses import dataclass
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TextIO

from .errors import ExitStatus, OpsError
from .output import WorkflowResult, redact, render_error, render_human, render_json


APPROVED_COMMANDS: tuple[str, ...] = (
    "build",
    "provision",
    "deploy",
    "verify",
    "releases",
    "backups",
    "backup",
    "rollback",
    "restore",
    "create-admin",
    "cleanup",
)

_ENVIRONMENT_COMMANDS = {
    "provision",
    "deploy",
    "verify",
    "releases",
    "backups",
    "backup",
    "create-admin",
    "cleanup",
}


@dataclass(frozen=True)
class Invocation:
    """Validated command arguments passed from the parser to a workflow."""

    command: str
    environment: str | None = None
    release_id: str | None = None
    backup_id: str | None = None
    artifact: Path | None = None
    json: bool = False
    dry_run: bool = False


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    # The root parser accepts options before the subcommand.  Suppressing the
    # subparser defaults keeps a root-level ``--json``/``--dry-run`` value when
    # an operator puts the flags after the command (the natural shell form).
    parser.add_argument(
        "--json",
        dest="json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit the versioned JSON report",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="validate and report without mutating remote state",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskman")
    parser.set_defaults(json=False, dry_run=False)
    parser.add_argument(
        "--json",
        dest="json",
        action="store_true",
        help="emit the versioned JSON report",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="validate and report without mutating remote state",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build a target-compatible release artifact")
    _add_common_options(build)

    for command in (
        command for command in APPROVED_COMMANDS if command in _ENVIRONMENT_COMMANDS
    ):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("environment")
        _add_common_options(subparser)
        if command in {"provision", "deploy"}:
            subparser.add_argument("--artifact", type=Path, metavar="ARCHIVE")

    rollback = subparsers.add_parser("rollback", help="select an installed release")
    rollback.add_argument("environment")
    rollback.add_argument("release_id")
    _add_common_options(rollback)

    restore = subparsers.add_parser("restore", help="restore an exact retained backup")
    restore.add_argument("environment")
    restore.add_argument("backup_id")
    _add_common_options(restore)

    return parser


def parse_invocation(argv: Sequence[str] | None = None) -> Invocation:
    """Parse one approved command into an immutable invocation object.

    ``argparse`` raises ``SystemExit(2)`` for malformed/missing/extra
    identifiers.  :func:`main` translates that into the stable ``2`` return
    code for callers that need a conventional function boundary.
    """

    namespace = build_parser().parse_args(list(argv) if argv is not None else None)
    command = namespace.command
    environment = getattr(namespace, "environment", None)
    release_id = getattr(namespace, "release_id", None)
    backup_id = getattr(namespace, "backup_id", None)
    artifact = getattr(namespace, "artifact", None)
    return Invocation(
        command=command,
        environment=environment,
        release_id=release_id,
        backup_id=backup_id,
        artifact=artifact,
        json=bool(getattr(namespace, "json", False)),
        dry_run=bool(getattr(namespace, "dry_run", False)),
    )


def parse_args(argv: Sequence[str] | None = None) -> Invocation:
    """Compatibility-friendly parser entry point used by controller callers."""

    return parse_invocation(argv)


def dispatch(invocation: Invocation) -> WorkflowResult:
    """Return a safe placeholder until a concrete workflow is implemented.

    The parser is intentionally usable before the later workflow modules land:
    command selection itself performs no external action and reports a stable
    no-op result.  Subsequent tasks replace this function's body while keeping
    its signature.
    """

    if invocation.command == "build":
        from .build import build_release

        repo = Path(__file__).resolve().parents[2]
        artifact_root = Path(tempfile.gettempdir()) / f"taskman-artifacts-{os.getuid()}"
        artifact = build_release(repo, artifact_root)
        return WorkflowResult(
            command="build",
            environment="",
            changed=True,
            stage="built",
            facts={
                "release_id": artifact.manifest.release_id,
                "source_revision": artifact.manifest.source_revision,
                "artifact_sha256": artifact.sha256,
                "archive": str(artifact.archive),
                "manifest": str(artifact.manifest_path),
                "checksum": str(artifact.checksum),
            },
        )
    return WorkflowResult(
        command=invocation.command,
        environment=invocation.environment or "",
        changed=False,
        stage="complete",
        facts={},
    )


def _coerce_result(invocation: Invocation, value: Any) -> WorkflowResult | Mapping[str, Any]:
    if isinstance(value, WorkflowResult) or isinstance(value, Mapping):
        return value
    raise TypeError("workflow dispatch must return a WorkflowResult or mapping")


def main(
    argv: Sequence[str] | None = None,
    *,
    dispatch_fn: Callable[[Invocation], Any] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the command boundary and return one documented exit code."""

    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    parse_errors = StringIO()
    try:
        # argparse writes diagnostics directly to sys.stderr.  Capture that
        # short-lived buffer so even an invalid command containing a registered
        # canary still crosses the redaction boundary before it is emitted.
        with redirect_stderr(parse_errors):
            invocation = parse_invocation(argv)
    except SystemExit as exc:
        # argparse has already emitted its concise usage/error text.  Ensure a
        # caller invoking ``main`` receives the stable integer rather than a
        # Python exception.  Help (0) remains a successful early exit.
        code = exc.code if isinstance(exc.code, int) else ExitStatus.INVALID
        if parse_errors.getvalue():
            error_stream.write(str(redact(parse_errors.getvalue())))
        return int(code)

    runner = dispatch_fn or dispatch
    try:
        result = _coerce_result(invocation, runner(invocation))
        # Result shape validation happens while rendering (including mapping
        # required-field checks). Keep both operations in this protected
        # boundary so malformed reports and renderer failures map to the same
        # secret-free stable error instead of escaping as tracebacks.
        text = render_json(result) if invocation.json else render_human(result)
    except OpsError as error:
        text = render_error(
            error,
            command=invocation.command,
            environment=invocation.environment or "",
            json_output=invocation.json,
        )
        print(text, file=error_stream)
        return int(error.status)
    except Exception:
        # Unexpected implementation failures are classified as local
        # prerequisites while preserving the non-disclosure boundary.  The
        # underlying traceback is deliberately not printed by the launcher.
        error = OpsError(
            status=ExitStatus.LOCAL_PREREQUISITE,
            stage="controller",
            message="controller operation failed",
            changed=False,
            next_action=None,
        )
        text = render_error(
            error,
            command=invocation.command,
            environment=invocation.environment or "",
            json_output=invocation.json,
        )
        print(text, file=error_stream)
        return int(error.status)

    print(text, file=output_stream)
    return int(ExitStatus.OK)


if __name__ == "__main__":  # pragma: no cover - exercised via launcher
    raise SystemExit(main())


__all__ = [
    "APPROVED_COMMANDS",
    "Invocation",
    "build_parser",
    "dispatch",
    "main",
    "parse_args",
    "parse_invocation",
]
