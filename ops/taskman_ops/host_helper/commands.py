"""Small bounded subprocess capability for helper-owned host procedures."""

from __future__ import annotations

from collections.abc import Mapping
import math
import os
import signal
import subprocess
from threading import Event, Thread
import time


MAX_COMMAND_OUTPUT_BYTES = 16 * 1024
_TERMINATION_GRACE_SECONDS = 0.2


class CommandError(RuntimeError):
    """A command could not complete without exposing its arguments or input."""


class CommandTimeout(CommandError):
    """A command exceeded its single external-call deadline."""


def run_command(
    argv: tuple[str, ...],
    *,
    stdin: bytes | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float,
    output_limit: int = MAX_COMMAND_OUTPUT_BYTES,
) -> subprocess.CompletedProcess[bytes]:
    """Run one non-shell command with protected optional stdin and bounded output."""

    _validate_inputs(argv, stdin, env, timeout_seconds, output_limit)
    deadline = time.monotonic() + timeout_seconds
    try:
        process = subprocess.Popen(
            argv,
            env=None if env is None else dict(env),
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise CommandError("command could not be started") from error

    assert process.stdout is not None
    assert process.stderr is not None
    overflow = Event()
    stopping = Event()
    read_failed = Event()
    stdout = bytearray()
    stderr = bytearray()
    process_group = process.pid
    readers = (
        Thread(
            target=_read_bounded,
            args=(process, process_group, process.stdout, stdout, overflow, stopping, read_failed, output_limit),
            daemon=True,
        ),
        Thread(
            target=_read_bounded,
            args=(process, process_group, process.stderr, stderr, overflow, stopping, read_failed, output_limit),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    writer = _write_stdin(process, stdin)
    workers = (*readers, *((writer,) if writer is not None else ()))
    timed_out = False
    drain_timed_out = False
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        if timed_out or overflow.is_set() or read_failed.is_set():
            _abort_process_tree(process, process_group, stopping)
            _join_workers(workers, time.monotonic() + _TERMINATION_GRACE_SECONDS)
        else:
            drain_timed_out = _join_workers(workers, deadline)
            if drain_timed_out:
                _abort_process_tree(process, process_group, stopping)
                _join_workers(workers, time.monotonic() + _TERMINATION_GRACE_SECONDS)

    if timed_out or drain_timed_out:
        raise CommandTimeout("command timed out")
    if overflow.is_set():
        raise CommandError("command output exceeds the allowed bound")
    if read_failed.is_set():
        raise CommandError("command output could not be read")
    if process.returncode != 0:
        raise CommandError("command exited unsuccessfully")
    return subprocess.CompletedProcess(argv, process.returncode, bytes(stdout), bytes(stderr))


def _read_bounded(
    process: subprocess.Popen[bytes],
    process_group: int,
    stream: object,
    buffer: bytearray,
    overflow: Event,
    stopping: Event,
    read_failed: Event,
    output_limit: int,
) -> None:
    try:
        while chunk := stream.read(4096):  # type: ignore[union-attr]
            if len(buffer) + len(chunk) > output_limit:
                overflow.set()
                _terminate_process_tree(process, process_group)
                return
            buffer.extend(chunk)
    except (OSError, ValueError):
        if not stopping.is_set():
            read_failed.set()
            _terminate_process_tree(process, process_group)


def _write_stdin(process: subprocess.Popen[bytes], stdin: bytes | None) -> Thread | None:
    if stdin is None:
        return None

    def write() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(stdin)
            process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()

    writer = Thread(target=write, daemon=True)
    writer.start()
    return writer


def _join_workers(workers: tuple[Thread, ...], deadline: float) -> bool:
    """Join concurrent pipe workers only until their command deadline."""

    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    return any(worker.is_alive() for worker in workers)


def _abort_process_tree(process: subprocess.Popen[bytes], process_group: int, stopping: Event) -> None:
    """Force the command group down, close its pipes, and reap its direct child."""

    stopping.set()
    _terminate_process_tree(process, process_group)
    _close_process_streams(process)
    try:
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _terminate_process_tree(process: subprocess.Popen[bytes], process_group: int) -> None:
    """Force terminate the session created for one bounded command."""

    if os.name == "posix":
        try:
            os.killpg(process_group, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _validate_inputs(
    argv: tuple[str, ...],
    stdin: bytes | None,
    env: Mapping[str, str] | None,
    timeout_seconds: float,
    output_limit: int,
) -> None:
    if (
        not isinstance(argv, tuple)
        or not argv
        or any(type(item) is not str or not item or "\x00" in item for item in argv)
    ):
        raise TypeError("argv must be a non-empty tuple of non-empty strings")
    if stdin is not None and type(stdin) is not bytes:
        raise TypeError("stdin must be bytes or None")
    if env is not None and (
        not isinstance(env, Mapping)
        or any(type(key) is not str or type(value) is not str for key, value in env.items())
    ):
        raise TypeError("env must map strings to strings")
    if (
        type(timeout_seconds) not in {int, float}
        or timeout_seconds <= 0
        or type(timeout_seconds) is float and not math.isfinite(timeout_seconds)
    ):
        raise ValueError("timeout_seconds must be positive")
    if not isinstance(output_limit, int) or isinstance(output_limit, bool) or output_limit < 0:
        raise ValueError("output_limit must be a non-negative integer")


__all__ = [
    "CommandError",
    "CommandTimeout",
    "MAX_COMMAND_OUTPUT_BYTES",
    "run_command",
]
