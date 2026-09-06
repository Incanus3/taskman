"""Execution-time conditional pyinfra operations with truthful change results.

pyinfra records an operation as changed whenever its generator yields a
command.  A convergence script that discovers a no-op only while executing
therefore needs an execution-time predicate: it probes the remote state after
earlier staged-file operations, then yields the mutating command only when the
probe reports ``changed=1``.  This avoids preparing decisions from stale facts
while preserving pyinfra's normal changed/no-change reporting.
"""

from __future__ import annotations

from .errors import OpsError


def conditional_convergence(
    *,
    name: str,
    probe: str,
    script: str,
    sudo: bool = True,
    failure: OpsError | None = None,
) -> object:
    """Declare ``script`` only when its execution-time probe reports drift.

    Both scripts run with sudo.  The probe must be read-only and print exactly
    one ``changed=0`` or ``changed=1`` line; unexpected probe output is an
    operation error rather than an unsafe best-effort mutation.
    """

    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(probe, str)
        or not probe.strip()
        or not isinstance(script, str)
        or not script.strip()
        or not isinstance(sudo, bool)
        or (failure is not None and not isinstance(failure, OpsError))
    ):
        raise ValueError("conditional pyinfra convergence requires non-empty scripts")

    from pyinfra.api import operation
    from pyinfra.api.command import FunctionCommand

    @operation(is_idempotent=True)
    def run_when_changed(command: str, custom_failure: OpsError | None):
        if custom_failure is None:
            yield command
        else:
            yield FunctionCommand(_run_categorized_command, (command, sudo, custom_failure), {})

    return run_when_changed(
        script,
        failure,
        name=name,
        _sudo=sudo,
        _if=_change_predicate(probe, sudo=sudo),
    )


def _change_predicate(probe: str, *, sudo: bool):
    """Build pyinfra's execution-only predicate for a validated marker probe."""

    def changed() -> bool:
        from pyinfra.api.command import StringCommand
        from pyinfra.context import host

        succeeded, output = host.run_shell_command(
            StringCommand(probe),
            print_output=False,
            print_input=False,
            _sudo=sudo,
        )
        if not succeeded:
            raise RuntimeError("conditional convergence probe failed")
        markers = [line for line in output.stdout_lines if line.startswith("changed=")]
        if markers == ["changed=0"]:
            return False
        if markers == ["changed=1"]:
            return True
        raise RuntimeError("conditional convergence probe returned an invalid change result")

    return changed


def _run_categorized_command(state: object, host: object, command: str, sudo: bool, failure: OpsError) -> None:
    """Execute one guarded script and preserve its typed refusal outside logs.

    pyinfra's normal command runner reports only success/failure. This callback
    wraps the fixed script in a machine-only status protocol, records a
    preconstructed public error on ``State``, and raises it to halt later
    operations. ``summarize_deploy`` consumes that typed value; no console
    rendering or sensitive diagnostic text is parsed.
    """

    from pyinfra.api.command import QuoteString, StringCommand

    runner = getattr(host, "run_shell_command", None)
    if not callable(runner):
        raise RuntimeError("invalid pyinfra host for categorized operation")
    wrapped = StringCommand(
        "sh",
        "-c",
        QuoteString(
            f"({command}); taskman_status=$?; "
            "printf '__taskman_command_status=%s\\n' \"$taskman_status\"; exit 0"
        ),
    )
    succeeded, output = runner(
        wrapped,
        print_output=False,
        print_input=False,
        _sudo=sudo,
    )
    if not succeeded:
        raise RuntimeError("categorized convergence transport failed")
    status = _captured_status(output)
    if status == 0:
        return
    if status == int(failure.status):
        setattr(state, "_taskman_categorized_error", failure)
        raise failure
    raise RuntimeError("categorized convergence command failed")


def _captured_status(output: object) -> int:
    lines = getattr(output, "stdout_lines", ())
    markers = [line.removeprefix("__taskman_command_status=") for line in lines if line.startswith("__taskman_command_status=")]
    if len(markers) != 1 or not markers[0].isdecimal():
        raise RuntimeError("categorized convergence command returned an invalid status")
    return int(markers[0])


__all__ = ["conditional_convergence"]
