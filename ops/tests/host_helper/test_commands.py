import os
import select
import signal
import socket
import subprocess
import sys
from threading import Event, Thread
import time

import pytest

from taskman_ops.host_helper import commands
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


def test_run_command_terminates_a_child_that_holds_its_inherited_pipes(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Killing only the immediate process would let its child block output draining past the deadline."""

    marker = tmp_path / "escaped-child"
    socket_path = tmp_path / "child-started.socket"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    listener.settimeout(2)
    child = (
        "import pathlib, socket, sys, time\n"
        "connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "connection.connect(sys.argv[1])\n"
        "connection.sendall(b'started')\n"
        "connection.close()\n"
        "time.sleep(0.15)\n"
        "pathlib.Path(sys.argv[2]).write_text('still running', encoding='utf-8')\n"
    )
    parent = (
        "import subprocess, sys\n"
        "subprocess.Popen((sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]))\n"
        "while True: pass\n"
    )
    real_popen = subprocess.Popen

    def synchronized_popen(*args: object, **kwargs: object):
        process = real_popen(*args, **kwargs)
        connection, _address = listener.accept()
        with connection:
            assert connection.recv(7) == b"started"
        return process

    monkeypatch.setattr("taskman_ops.host_helper.commands.subprocess.Popen", synchronized_popen)
    started = time.monotonic()

    try:
        with pytest.raises(CommandTimeout, match="timed out"):
            run_command(
                (sys.executable, "-c", parent, child, str(socket_path), str(marker)),
                timeout_seconds=0.1,
            )
    finally:
        listener.close()

    assert time.monotonic() - started < 0.7
    time.sleep(0.25)
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


def test_cancelled_command_is_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignoring an already-set cancellation event would launch an unwanted command."""

    cancellation = Event()
    cancellation.set()
    started = []

    def record_start(*args, **kwargs):
        started.append(args)
        raise AssertionError("cancelled command was started")

    monkeypatch.setattr(commands.subprocess, "Popen", record_start)
    with pytest.raises(CommandError, match="^command cancelled$") as raised:
        run_command(
            (sys.executable, "-c", "argv-canary"),
            stdin=b"stdin-canary",
            timeout_seconds=2,
            cancellation=cancellation,
        )

    assert isinstance(raised.value, commands.CommandCancelled)
    assert not started


@pytest.fixture
def cancel_ready_command(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Use child and worker acknowledgements to cancel without timing sleeps."""

    socket_path = tmp_path / "command-ready.socket"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    listener.settimeout(2)
    cancellation = Event()
    processes = []
    descendant_fds = []
    workers = []
    finished = []
    started = []
    parent_exits = False
    cancel_on_wait = True
    hold_worker = False
    release_worker = Event()
    real_popen = subprocess.Popen

    def start_ready(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        connection, _ = listener.accept()
        with connection:
            descendant = int(connection.recv(64))
            descendant_fds.append(os.pidfd_open(descendant))
            connection.sendall(b"ack")
        real_wait = process.wait

        def wait(*args, **kwargs):
            assert all(event.wait(1) for event in started)
            if not parent_exits and cancel_on_wait:
                cancellation.set()
            return real_wait(*args, **kwargs)

        process.wait = wait
        return process

    def tracked_thread(*, target, args=(), daemon):
        done = Event()
        finished.append(done)
        ready = Event()
        started.append(ready)

        first_worker = not workers

        def acknowledged():
            ready.set()
            try:
                target(*args)
            finally:
                if hold_worker and first_worker:
                    assert release_worker.wait(2)
                done.set()

        worker = Thread(target=acknowledged, daemon=daemon)
        workers.append(worker)
        real_join = worker.join

        def join(timeout=None):
            if parent_exits:
                cancellation.set()
            return real_join(timeout)

        worker.join = join
        return worker

    monkeypatch.setattr(commands.subprocess, "Popen", start_ready)
    monkeypatch.setattr(commands, "Thread", tracked_thread)

    def execute(script, *, stdin=None, exited=False, hold=False, cancel=True, timeout=2):
        nonlocal parent_exits, hold_worker, cancel_on_wait
        parent_exits = exited
        hold_worker = hold
        cancel_on_wait = cancel
        try:
            return run_command(
                (sys.executable, "-c", script, str(socket_path), "argv-canary"),
                stdin=stdin,
                timeout_seconds=timeout,
                cancellation=cancellation,
            )
        finally:
            release_worker.set()
            if hold:
                for worker in workers:
                    worker.join(1)

    yield execute, processes, descendant_fds, workers, finished
    listener.close()
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
    for worker in workers:
        worker.join(1)
    for fd in descendant_fds:
        os.close(fd)


@pytest.mark.parametrize("parent_exits", [False, True])
def test_cancellation_reaps_process_group_before_return(cancel_ready_command, parent_exits) -> None:
    """Killing only the parent, or ignoring cancellation during drain, leaves a live descendant."""

    execute, processes, descendant_fds, _workers, _finished = cancel_ready_command
    script = (
        "import os, signal, socket, sys\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "    connection.connect(sys.argv[1])\n"
        "    connection.sendall(str(os.getpid()).encode())\n"
        "    assert connection.recv(3) == b'ack'\n"
        "    connection.close()\n"
        "    signal.pause()\n"
        "else:\n"
        + ("    os._exit(0)\n" if parent_exits else "    signal.pause()\n")
    )

    started = time.monotonic()
    with pytest.raises(CommandError, match="^command cancelled$") as raised:
        execute(script, exited=parent_exits)

    assert time.monotonic() - started < 1

    assert isinstance(raised.value, commands.CommandCancelled)
    assert processes[0].returncode is not None
    assert select.select(descendant_fds, [], [], 1)[0] == descendant_fds


def test_cancellation_finishes_pipe_workers(cancel_ready_command) -> None:
    """Returning before pipe workers finish leaks blocked readers or protected-stdin writers."""

    execute, processes, _descendant_fds, workers, finished = cancel_ready_command
    script = (
        "import os, signal, socket, sys\n"
        "sys.stderr.write('native-diagnostic-canary')\n"
        "sys.stderr.flush()\n"
        "connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "connection.connect(sys.argv[1])\n"
        "connection.sendall(str(os.getpid()).encode())\n"
        "assert connection.recv(3) == b'ack'\n"
        "connection.close()\n"
        "signal.pause()\n"
    )

    with pytest.raises(CommandError, match="^command cancelled$") as raised:
        execute(script, stdin=b"stdin-canary" * (1024 * 1024))

    assert isinstance(raised.value, commands.CommandCancelled)
    assert len(workers) == 3
    assert all(done.is_set() for done in finished)
    assert all(not worker.is_alive() for worker in workers)
    assert processes[0].stdin.closed
    assert processes[0].stdout.closed
    assert processes[0].stderr.closed
    assert str(raised.value) == "command cancelled"


def test_cancellation_refuses_to_report_completion_with_an_unfinished_worker(cancel_ready_command) -> None:
    """Ignoring a cleanup deadline must not claim that cancellation finished."""

    execute, _processes, _descendant_fds, _workers, _finished = cancel_ready_command
    script = (
        "import os, signal, socket, sys\n"
        "connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "connection.connect(sys.argv[1])\n"
        "connection.sendall(str(os.getpid()).encode())\n"
        "assert connection.recv(3) == b'ack'\n"
        "connection.close()\n"
        "signal.pause()\n"
    )

    with pytest.raises(CommandError, match="^command cancellation cleanup failed$") as raised:
        execute(script, hold=True)

    assert isinstance(raised.value, commands.CommandCleanupError)


@pytest.mark.parametrize(
    "trigger,stdin",
    [
        pytest.param("cancel", None, id="cancel-reader"),
        pytest.param("cancel", b"stdin-canary" * (1024 * 1024), id="cancel-writer"),
        pytest.param("timeout", None, id="timeout"),
        pytest.param("overflow", None, id="overflow"),
    ],
)
def test_cancellation_cleanup_is_bounded_with_an_escaped_pipe_holder(cancel_ready_command, trigger, stdin) -> None:
    """Closing a worker-owned buffered pipe must not prevent the bounded cleanup error."""

    execute, processes, descendant_fds, workers, _finished = cancel_ready_command
    script = (
        "import os, signal, socket, sys\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.setsid()\n"
        "    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "    connection.connect(sys.argv[1])\n"
        "    connection.sendall(str(os.getpid()).encode())\n"
        "    assert connection.recv(3) == b'ack'\n"
        "    connection.close()\n"
        + (
            f"    sys.stdout.buffer.write(b'x' * {MAX_COMMAND_OUTPUT_BYTES + 1})\n"
            "    sys.stdout.buffer.flush()\n"
            if trigger == "overflow" else ""
        )
        + "    signal.pause()\n"
        "else:\n"
        "    signal.pause()\n"
    )
    returned = Event()
    errors = []

    def run():
        try:
            execute(script, stdin=stdin, cancel=trigger == "cancel", timeout=0.25)
        except Exception as error:
            errors.append(error)
        finally:
            returned.set()

    runner = Thread(target=run, daemon=True)
    runner.start()
    try:
        assert returned.wait(1), "cleanup blocked before reaching its bounded decision"
        assert len(errors) == 1
        assert isinstance(errors[0], commands.CommandCleanupError)
        assert str(errors[0]) == "command cancellation cleanup failed"
        assert processes[0].returncode is not None
        assert any(worker.is_alive() for worker in workers)
    finally:
        for fd in descendant_fds:
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
        runner.join(2)
        assert not runner.is_alive()
