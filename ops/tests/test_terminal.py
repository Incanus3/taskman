"""Exercise credential input in a real VM and PTY, optionally from an extracted release."""

import errno
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import termios
import time

import pytest


def terminal_command(expression: str) -> list[str]:
    repository = Path(__file__).resolve().parents[2]
    release_path = os.environ.get("TASKMAN_TEST_RELEASE")
    if release_path:
        release = Path(release_path).resolve()
        _erts, version = (release / "releases/start_erl.data").read_text().split()
        version_path = release / "releases" / version
        command = [
            str(version_path / "elixir"),
            "--boot", str(version_path / "start_clean"),
            "--boot-var", "RELEASE_LIB", str(release / "lib"),
        ]
    else:
        command = ["elixir"]
        for source in ("terminal", "local_terminal", "credential_prompts"):
            command.extend(["-r", str(repository / "lib/taskman" / f"{source}.ex")])
    return [*command, "-e", expression]


def read_until(fd: int, captured: bytearray, marker: bytes) -> None:
    deadline = time.monotonic() + 15
    while marker not in captured:
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"missing {marker!r}: {captured!r}"
        if not select.select([fd], [], [], remaining)[0]:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            chunk = b""
        assert chunk, f"terminal ended before {marker!r}: {captured!r}"
        captured.extend(chunk)


@pytest.mark.parametrize("interrupt", [False, True], ids=["confirmation", "termination"])
def test_secret_input_is_hidden_and_terminal_is_restored(interrupt: bool) -> None:
    # OTP 27 rejects get_password on the old :user group: both prompts must accept input.
    expression = '''
case Taskman.CredentialPrompts.prompt_for_password(Taskman.LocalTerminal) do
  {:ok, password} when byte_size(password) == 21 -> IO.puts("CREDENTIALS_OK")
  _ -> IO.puts("CREDENTIALS_FAILED"); System.halt(1)
end
case Taskman.LocalTerminal.prompt("Visible: ") do
  "finished" -> IO.puts("VISIBLE_OK")
  _ -> System.halt(2)
end
'''
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    process = subprocess.Popen(
        terminal_command(expression), stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True,
    )
    captured = bytearray()
    try:
        read_until(master, captured, b"Password: ")
        if interrupt:
            os.write(master, b"terminal-canary-7db91")
            process.send_signal(signal.SIGTERM)
        else:
            os.write(master, b"terminal-canary-7db91\r")
            read_until(master, captured, b"Confirm password: ")
            os.write(master, b"terminal-canary-7db91\r")
            read_until(master, captured, b"Visible: ")
            assert b"CREDENTIALS_OK" in captured
            visible_mode = termios.tcgetattr(slave)
            assert visible_mode[3] & (termios.ECHO | termios.ICANON) == original[3] & (
                termios.ECHO | termios.ICANON
            )
            os.write(master, b"finished\n")
            read_until(master, captured, b"VISIBLE_OK")
        status = process.wait(timeout=15)
        while select.select([master], [], [], 0)[0]:
            captured.extend(os.read(master, 4096))
        if not interrupt:
            assert status == 0
        assert b"terminal-canary-7db91" not in captured
        restored = termios.tcgetattr(slave)
        assert restored[3] & (termios.ECHO | termios.ICANON) == original[3] & (
            termios.ECHO | termios.ICANON
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)
        os.close(slave)
