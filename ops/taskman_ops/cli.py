"""Command-line boundary for workstation deployment workflows."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TextIO

from .errors import ExitStatus, OpsError
from .output import (
    WorkflowResult,
    redact,
    render_error,
    render_human,
    render_json,
    result_exit_status,
    result_process_status,
)


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

_COMMAND_HELP = {
    "build": "build a target-compatible release artifact",
    "provision": "provision a supported clean host",
    "deploy": "deploy an immutable release",
    "verify": "verify the deployed topology",
    "releases": "list managed releases",
    "backups": "list retained backups",
    "backup": "create a validated local backup",
    "rollback": "select a compatible installed release",
    "restore": "restore an exact retained backup",
    "create-admin": "create an administrator through an SSH TTY",
    "cleanup": "remove only eligible managed artifacts",
}


@dataclass(frozen=True)
class Invocation:
    """Validated command arguments passed from the parser to a workflow."""

    command: str
    environment: str | None = None
    release_id: str | None = None
    backup_id: str | None = None
    artifact: Path | None = None
    migration_policy: str | None = None
    manual_adoption_confirmed: bool = False
    yes: bool = False
    allow_dirty: bool = False
    allow_downgrade: bool = False
    replace_unfinished: bool = False
    reapply: bool = False
    json: bool = False
    dry_run: bool = False
    interactive: bool | None = None


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
    parser = argparse.ArgumentParser(
        prog="taskman",
        description="Build, provision, deploy, inspect, and recover one supported Taskman host.",
    )
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

    build = subparsers.add_parser("build", help=_COMMAND_HELP["build"])
    _add_common_options(build)
    build.add_argument("--allow-dirty", action="store_true", help="allow a frozen dirty source snapshot")

    for command in (
        command for command in APPROVED_COMMANDS if command in _ENVIRONMENT_COMMANDS
    ):
        subparser = subparsers.add_parser(command, help=_COMMAND_HELP[command])
        subparser.add_argument("environment")
        _add_common_options(subparser)
        if command in {"provision", "deploy"}:
            subparser.add_argument("--artifact", type=Path, metavar="ARCHIVE")
            subparser.add_argument("--yes", action="store_true", help="confirm the displayed plan")
            subparser.add_argument(
                "--allow-dirty", action="store_true", help="allow a frozen dirty source snapshot"
            )
            subparser.add_argument(
                "--allow-downgrade", action="store_true", help="acknowledge a downgrade or unknown source order"
            )
        if command in {"provision", "deploy"}:
            subparser.add_argument(
                "--migration-policy",
                choices=("backward-compatible", "restore-required"),
                help="required when the candidate changes migrations",
            )
            subparser.add_argument(
                "--adopt-manual-current",
                action="store_true",
                help="unsupported; manual adoption is refused",
            )

    rollback = subparsers.add_parser("rollback", help=_COMMAND_HELP["rollback"])
    rollback.add_argument("environment")
    rollback.add_argument("release_id")
    _add_common_options(rollback)

    restore = subparsers.add_parser("restore", help=_COMMAND_HELP["restore"])
    restore.add_argument("environment")
    restore.add_argument("backup_id")
    _add_common_options(restore)
    restore_modes = restore.add_mutually_exclusive_group()
    restore_modes.add_argument(
        "--replace-unfinished",
        action="store_true",
        help="replace a different unfinished restore target after renewed confirmation",
    )
    restore_modes.add_argument(
        "--reapply",
        action="store_true",
        help="intentionally load a previously completed backup again",
    )

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
    migration_policy = getattr(namespace, "migration_policy", None)
    manual_adoption_confirmed = bool(getattr(namespace, "adopt_manual_current", False))
    return Invocation(
        command=command,
        environment=environment,
        release_id=release_id,
        backup_id=backup_id,
        artifact=artifact,
        migration_policy=migration_policy,
        manual_adoption_confirmed=manual_adoption_confirmed,
        yes=bool(getattr(namespace, "yes", False)),
        allow_dirty=bool(getattr(namespace, "allow_dirty", False)),
        allow_downgrade=bool(getattr(namespace, "allow_downgrade", False)),
        replace_unfinished=bool(getattr(namespace, "replace_unfinished", False)),
        reapply=bool(getattr(namespace, "reapply", False)),
        json=bool(getattr(namespace, "json", False)),
        dry_run=bool(getattr(namespace, "dry_run", False)),
    )


def parse_args(argv: Sequence[str] | None = None) -> Invocation:
    """Compatibility-friendly parser entry point used by controller callers."""

    return parse_invocation(argv)


def dispatch(invocation: Invocation) -> WorkflowResult:
    """Run one validated invocation through its concrete workflow."""

    if (invocation.reapply or invocation.replace_unfinished) and invocation.command != "restore":
        raise OpsError(
            ExitStatus.INVALID,
            "arguments",
            "restore recovery flags are valid only for restore",
            changed=False,
            next_action="remove the restore-only recovery flag",
        )
    if invocation.reapply and invocation.replace_unfinished:
        raise OpsError(
            ExitStatus.INVALID,
            "arguments",
            "--reapply and --replace-unfinished are mutually exclusive",
            changed=False,
            next_action="select exactly one restore recovery mode",
        )

    if invocation.command in {"rollback", "restore"}:
        from .releases.identifiers import validate_release_id

        try:
            if invocation.command == "rollback":
                validate_release_id(invocation.release_id)
            elif not isinstance(invocation.backup_id, str) or re.fullmatch(
                r"backup-[0-9a-f]{32}", invocation.backup_id
            ) is None:
                raise ValueError("invalid backup identifier")
        except ValueError:
            kind, listing = (
                ("release", "releases") if invocation.command == "rollback"
                else ("backup", "backups")
            )
            raise OpsError(
                ExitStatus.INVALID,
                "arguments",
                f"invalid {kind} identifier",
                changed=False,
                next_action=f"use an exact {kind} ID from the {listing} command",
            ) from None

    if invocation.command == "build":
        from .releases.build import build_release, default_artifact_root

        repo = Path(__file__).resolve().parents[2]
        artifact_root = default_artifact_root()
        artifact = build_release(repo, artifact_root, allow_dirty=invocation.allow_dirty)
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
    if invocation.command == "provision":
        from .workflows.provision import provision

        return provision(invocation)
    if invocation.command == "verify":
        from .config import load_environment
        from .remote import connect
        from .workflows.verify import run_verify

        if invocation.environment is None:
            raise ValueError("verify requires an environment")
        environment = load_environment(invocation.environment)
        remote = connect(environment)
        return run_verify(remote, environment)
    if invocation.command in {"releases", "backups"}:
        from .config import load_environment
        from .remote import connect
        from .workflows.backups import list_backups
        from .workflows.releases import list_releases

        if invocation.environment is None:
            raise ValueError(f"{invocation.command} requires an environment")
        environment = load_environment(invocation.environment)
        remote = connect(environment)
        discovery = list_releases(remote, environment) if invocation.command == "releases" else list_backups(remote, environment)
        return WorkflowResult(
            command=invocation.command,
            environment=environment.name or invocation.environment,
            changed=False,
            stage="listed",
            facts={"records": discovery.records},
            warnings=discovery.warnings,
        )
    if invocation.command == "backup":
        from .config import load_environment
        from .remote import connect
        from .workflows.backup import run_backup

        if invocation.environment is None:
            raise ValueError("backup requires an environment")
        environment = load_environment(invocation.environment)
        remote = connect(environment)
        return run_backup(
            remote,
            environment,
            dry_run=invocation.dry_run,
        )
    if invocation.command == "deploy":
        from .releases.artifacts import clean_inputs_drifted, identify_clean_inputs, resolve_deploy_target
        from .config import load_environment
        from .remote import connect
        from .workflows.deploy import _MAX_CLEAN_INPUT_RERESOLUTIONS, deploy

        if invocation.environment is None:
            raise ValueError("deploy requires an environment")
        environment = load_environment(invocation.environment)
        repo = Path(__file__).resolve().parents[2]
        if invocation.artifact is not None:
            target = resolve_deploy_target(
                repo,
                invocation.artifact,
                installed_records=(),
                selected_release_id=None,
                last_successful_release_id=None,
            )
            if invocation.allow_dirty and not target.source_dirty:
                raise OpsError(
                    ExitStatus.INVALID,
                    "arguments",
                    "--allow-dirty is not valid with an explicitly selected clean artifact",
                    changed=False,
                    next_action="omit --allow-dirty or select a dirty-provenance artifact",
                )
            remote = connect(environment)
            result = deploy(
                remote, environment, target,
                migration_policy=invocation.migration_policy,
                manual_adoption_confirmed=invocation.manual_adoption_confirmed,
                yes=invocation.yes,
                allow_downgrade=invocation.allow_downgrade,
                dry_run=invocation.dry_run,
                interactive=(not invocation.json if invocation.interactive is None else invocation.interactive),
            )
            return replace(
                result,
                facts={**result.facts, "artifact_source": result.facts.get("artifact_source", target.source)},
            )
        remote = connect(environment)
        clean_inputs = None
        if invocation.artifact is None:
            try:
                clean_inputs = identify_clean_inputs(repo)
            except OpsError:
                if not invocation.allow_dirty:
                    raise

        def resolve_current():
            from .workflows.deploy import deployment_admission_authority

            authority = deployment_admission_authority(remote, environment)
            return (
                resolve_deploy_target(
                    repo,
                    invocation.artifact,
                    installed_records=authority.installed_records,
                    selected_release_id=authority.selected_release_id,
                    last_successful_release_id=authority.last_successful_release_id,
                    allow_dirty=invocation.allow_dirty,
                    clean_inputs=clean_inputs,
                ),
                authority,
            )

        initial_clean_reresolutions = 0
        while True:
            try:
                target, _authority = resolve_current()
                break
            except OpsError as error:
                if clean_inputs is None or not clean_inputs_drifted(error):
                    raise
                if initial_clean_reresolutions >= _MAX_CLEAN_INPUT_RERESOLUTIONS:
                    raise OpsError(
                        ExitStatus.SAFETY,
                        "deploy",
                        "clean deployment inputs did not stabilize while preparing a plan",
                        changed=False,
                        next_action="restore a stable intended clean checkout and rerun deploy",
                    ) from None
                clean_inputs = identify_clean_inputs(repo)
                initial_clean_reresolutions += 1

        def refresh_clean_target():
            nonlocal clean_inputs
            clean_inputs = identify_clean_inputs(repo)
            return resolve_current()[0], clean_inputs

        result = deploy(
            remote,
            environment,
            target,
            migration_policy=invocation.migration_policy,
            manual_adoption_confirmed=invocation.manual_adoption_confirmed,
            yes=invocation.yes,
            allow_downgrade=invocation.allow_downgrade,
            repo=repo if clean_inputs is not None else None,
            clean_inputs=clean_inputs,
            refresh_clean_target=refresh_clean_target if clean_inputs is not None else None,
            initial_clean_reresolutions=initial_clean_reresolutions,
            dry_run=invocation.dry_run,
            interactive=(not invocation.json if invocation.interactive is None else invocation.interactive),
        )
        return replace(
            result,
            facts={**result.facts, "artifact_source": result.facts.get("artifact_source", target.source)},
        )
    if invocation.command == "rollback":
        from .config import load_environment
        from .remote import connect
        from .workflows.rollback import rollback

        if invocation.environment is None or invocation.release_id is None:
            raise ValueError("rollback requires an environment and release identifier")
        environment = load_environment(invocation.environment)
        remote = connect(environment)
        return rollback(remote, environment, invocation.release_id, dry_run=invocation.dry_run)
    if invocation.command == "restore":
        from .config import load_environment
        from .remote import connect
        from .workflows.restore import restore

        if invocation.environment is None or invocation.backup_id is None:
            raise ValueError("restore requires an environment and backup identifier")
        environment = load_environment(invocation.environment)
        remote = connect(environment)
        return restore(
            remote,
            environment,
            invocation.backup_id,
            dry_run=invocation.dry_run,
            replace_unfinished=invocation.replace_unfinished,
            reapply=invocation.reapply,
        )
    if invocation.command == "cleanup":
        from .config import load_environment
        from .remote import connect
        from .workflows.cleanup import cleanup

        if invocation.environment is None:
            raise ValueError("cleanup requires an environment")
        environment = load_environment(invocation.environment)
        remote = connect(environment)
        return cleanup(remote, environment, dry_run=invocation.dry_run)
    if invocation.command == "create-admin":
        from .config import load_environment
        from .remote import connect
        from .workflows.create_admin import run_create_admin

        if invocation.environment is None:
            raise ValueError("create-admin requires an environment")
        environment = load_environment(invocation.environment)
        remote = connect(environment)
        return run_create_admin(remote, environment, dry_run=invocation.dry_run)
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
    stdin: TextIO | None = None,
) -> int:
    """Run the command boundary and return one documented exit code."""

    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    input_stream = stdin or sys.stdin
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

    interactive = not invocation.json and bool(getattr(input_stream, "isatty", lambda: False)())
    invocation = replace(invocation, interactive=interactive)
    runner = dispatch_fn or dispatch
    try:
        if (
            invocation.command in {"deploy", "provision"}
            and not invocation.dry_run
            and not invocation.yes
            and not invocation.interactive
        ):
            raise OpsError(
                ExitStatus.SAFETY,
                "confirmation",
                "unattended deployment or provisioning requires --yes; JSON is never confirmation",
                changed=False,
                next_action="review the plan interactively or rerun with --yes after independent review",
            )
        dispatched = runner(invocation)
        result = _coerce_result(invocation, dispatched)
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
    process_status = result_process_status(result)
    if process_status is not None:
        return process_status
    return int(result_exit_status(result))


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
