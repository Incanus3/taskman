"""Execution-time conditional pyinfra operations with truthful change results.

pyinfra records an operation as changed whenever its generator yields a
command.  A convergence script that discovers a no-op only while executing
therefore needs an execution-time predicate: it probes the remote state after
earlier staged-file operations, then yields the mutating command only when the
probe reports ``changed=1``.  This avoids preparing decisions from stale facts
while preserving pyinfra's normal changed/no-change reporting.
"""

from __future__ import annotations


def conditional_convergence(*, probe: str, script: str, sudo: bool = True) -> None:
    """Declare ``script`` only when its execution-time probe reports drift.

    Both scripts run with sudo.  The probe must be read-only and print exactly
    one ``changed=0`` or ``changed=1`` line; unexpected probe output is an
    operation error rather than an unsafe best-effort mutation.
    """

    if (
        not isinstance(probe, str)
        or not probe.strip()
        or not isinstance(script, str)
        or not script.strip()
        or not isinstance(sudo, bool)
    ):
        raise ValueError("conditional pyinfra convergence requires non-empty scripts")

    from pyinfra.api import operation

    @operation(is_idempotent=True)
    def run_when_changed(command: str):
        yield command

    run_when_changed(script, _sudo=sudo, _if=_change_predicate(probe, sudo=sudo))


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


__all__ = ["conditional_convergence"]
