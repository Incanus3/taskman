import sys
import time

import pytest

from taskman_ops.host_helper.commands import (
    MAX_COMMAND_OUTPUT_BYTES,
    CommandError,
    CommandTimeout,
    run_command,
)
from taskman_ops.output import clear_secrets, register_secret


def test_run_command_accepts_only_a_bounded_argv_tuple() -> None:
    """A shell string or mutable argv must not become a host command."""

    with pytest.raises(TypeError, match="argv"):
        run_command(  # type: ignore[arg-type]
            [sys.executable, "-c", "print('never run')"],
            timeout_seconds=1,
        )


def test_run_command_passes_optional_stdin_without_exposing_its_value() -> None:
    """Removing protected stdin support would break credential-safe commands."""

    completed = run_command(
        (sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        stdin=b"protected-input",
        timeout_seconds=1,
    )

    assert completed.stdout == b"protected-input"
    assert completed.stderr == b""


def test_run_command_stops_one_subprocess_at_its_timeout() -> None:
    """Removing the subprocess timeout would leave a helper invocation hung."""

    with pytest.raises(CommandTimeout, match="timed out"):
        run_command(
            (sys.executable, "-c", "while True: pass"),
            timeout_seconds=0.01,
        )


def test_run_command_terminates_a_child_that_holds_its_inherited_pipes(tmp_path) -> None:
    """Killing only the immediate process would let its child block output draining past the deadline."""

    marker = tmp_path / "escaped-child"
    child = (
        "import pathlib, sys, time; "
        "time.sleep(1); "
        "pathlib.Path(sys.argv[1]).write_text('still running', encoding='utf-8')"
    )
    parent = "import subprocess, sys; subprocess.Popen((sys.executable, '-c', sys.argv[1], sys.argv[2]))"
    started = time.monotonic()

    with pytest.raises(CommandTimeout, match="timed out"):
        run_command(
            (sys.executable, "-c", parent, child, str(marker)),
            timeout_seconds=0.1,
        )

    assert time.monotonic() - started < 0.7
    time.sleep(0.2)
    assert not marker.exists()


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_run_command_rejects_non_finite_timeouts(timeout: float) -> None:
    """A non-finite deadline cannot provide a bounded external-call contract."""

    with pytest.raises(ValueError, match="timeout_seconds"):
        run_command((sys.executable, "-c", "print('never run')"), timeout_seconds=timeout)


def test_run_command_refuses_unbounded_captured_output() -> None:
    """Dropping the output cap would allow one command to exhaust helper memory."""

    with pytest.raises(CommandError, match="output exceeds"):
        run_command(
            (
                sys.executable,
                "-c",
                f"import sys; sys.stdout.buffer.write(b'x' * {MAX_COMMAND_OUTPUT_BYTES + 1})",
            ),
            timeout_seconds=1,
        )


def test_run_command_honors_a_callers_smaller_output_limit() -> None:
    """Removing a verification-specific cap would admit oversized service evidence."""

    with pytest.raises(CommandError, match="output exceeds"):
        run_command(
            (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 9)"),
            timeout_seconds=1,
            output_limit=8,
        )


def test_run_command_redacts_protected_stdin_from_a_failure() -> None:
    """Including stdin in a failure would leak a database credential."""

    secret = "database-password-canary"
    register_secret(secret)
    try:
        with pytest.raises(CommandError) as raised:
            run_command(
                (sys.executable, "-c", "import sys; sys.stderr.write(sys.stdin.read()); raise SystemExit(1)"),
                stdin=secret.encode(),
                timeout_seconds=1,
            )
    finally:
        clear_secrets()

    assert secret not in str(raised.value)
