from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import time

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import render_json
from taskman_ops.workflows.create_admin import (
    CREATE_ADMIN_COMMAND,
    create_admin_command,
    run_create_admin,
    run_create_admin_session,
)

from test_config import valid_environment


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(name="production"))


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.create_admin.validate_operational_preflight",
        lambda remote, _config: remote.facts(),
    )


@dataclass
class Terminal:
    attached: bool

    def isatty(self) -> bool:
        return self.attached

    def fileno(self) -> int:
        return 0


class FactRemote:
    def __init__(self) -> None:
        self.fact_calls = 0
        self.command_calls: list[tuple[str, ...]] = []

    def facts(self) -> object:
        self.fact_calls += 1
        return object()

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> object:
        from taskman_ops.remote import CommandResult

        self.command_calls.append(argv)
        return CommandResult(0)


def test_create_admin_refuses_before_connecting_when_any_local_stream_is_not_a_tty() -> None:
    remote = FactRemote()
    sessions: list[EnvironmentConfig] = []

    with pytest.raises(OpsError) as raised:
        run_create_admin(
            remote,
            _config(),
            stdin=Terminal(True),
            stdout=Terminal(False),
            stderr=Terminal(True),
            session_runner=lambda config: sessions.append(config) or 0,
        )

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE
    assert remote.fact_calls == 0
    assert sessions == []


def test_create_admin_uses_only_the_fixed_constrained_release_command_and_propagates_status() -> None:
    remote = FactRemote()
    calls: list[EnvironmentConfig] = []

    result = run_create_admin(
        remote,
        _config(),
        stdin=Terminal(True),
        stdout=Terminal(True),
        stderr=Terminal(True),
        session_runner=lambda config: calls.append(config) or 37,
    )

    assert remote.fact_calls == 1
    assert calls == [_config()]
    assert CREATE_ADMIN_COMMAND == (
        "sudo",
        "--",
        "systemd-run",
        "--wait",
        "--pipe",
        "--collect",
        "--property=User=taskman",
        "--property=Group=taskman",
        "--property=WorkingDirectory=/opt/taskman/current",
        "--property=EnvironmentFile=/etc/taskman/taskman.env",
        "--",
        "/opt/taskman/current/bin/create-admin",
    )
    assert result.command == "create-admin"
    assert result.stage == "administrator-command-failed"
    assert result.exit_status is ExitStatus.RELEASE
    assert result.process_status == 37
    assert result.facts == {"remote_status": 37}


def test_create_admin_session_allocates_a_strict_ssh_tty_without_credential_arguments_or_environment(
    tmp_path: Path,
) -> None:
    config = _config()
    canary_email = "admin-canary@example.invalid"
    canary_password = "create-admin-password-canary-c12ec4"
    key_line = "[203.0.113.10]:2202 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEexamplekey"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        if argv[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(argv, 0, key_line.encode() + b"\n", b"")
        if argv[0] == "ssh-keygen":
            fingerprint = config.host_key_fingerprint
            return subprocess.CompletedProcess(
                argv,
                0,
                f"256 {fingerprint} test (ED25519)\n".encode(),
                b"",
            )
        return subprocess.CompletedProcess(argv, 37)

    status = run_create_admin_session(
        config,
        command_runner=runner,
        temporary_directory=tmp_path,
    )

    ssh_argv, ssh_kwargs = calls[-1]
    assert status == 37
    assert ssh_argv[:4] == ["ssh", "-tt", "-F", "/dev/null"]
    assert "StrictHostKeyChecking=yes" in ssh_argv
    assert f"ConnectTimeout={config.connection_timeout}" in ssh_argv
    assert ssh_argv[-len(CREATE_ADMIN_COMMAND) :] == list(CREATE_ADMIN_COMMAND)
    assert ssh_kwargs == {"check": False}
    assert canary_email not in repr(calls)
    assert canary_password not in repr(calls)
    assert "env" not in ssh_kwargs
    assert "input" not in ssh_kwargs
    assert list(tmp_path.iterdir()) == []


def test_create_admin_derives_the_still_fixed_command_from_the_validated_managed_root(
    tmp_path: Path,
) -> None:
    """Configurable roots must not silently launch the default installation."""

    config = EnvironmentConfig.model_validate(
        valid_environment(
            name="production",
            managed_root="/srv/taskman",
            release_root="/srv/taskman/artifacts",
            deployment_root="/srv/taskman/control",
            backup_root="/srv/taskman-backups",
        )
    )
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        if argv[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(
                argv,
                0,
                b"[203.0.113.10]:2202 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEexamplekey\n",
                b"",
            )
        if argv[0] == "ssh-keygen":
            return subprocess.CompletedProcess(
                argv,
                0,
                f"256 {config.host_key_fingerprint} test (ED25519)\n".encode(),
                b"",
            )
        return subprocess.CompletedProcess(argv, 0)

    assert run_create_admin_session(
        config,
        command_runner=runner,
        temporary_directory=tmp_path,
    ) == 0

    expected = create_admin_command(config)
    assert expected[-1] == "/srv/taskman/current/bin/create-admin"
    assert "--property=WorkingDirectory=/srv/taskman/current" in expected
    assert calls[-1][-len(expected) :] == list(expected)
    assert "/opt/taskman/current/bin/create-admin" not in calls[-1]


def test_real_pty_keeps_the_packaged_password_prompt_out_of_every_bridge_boundary(
    tmp_path: Path,
) -> None:
    """Removing ssh -tt, the fixed command, or terminal raw mode must fail this acceptance."""

    config_values = valid_environment(name="production")
    config = EnvironmentConfig.model_validate(config_values)
    commands = tmp_path / "commands"
    commands.mkdir()
    invocation_log = tmp_path / "ssh-invocation.json"
    bridge_log = tmp_path / "bridge.jsonl"
    repository = Path(__file__).resolve().parents[3]
    release_bin = tmp_path / "release" / "bin"
    release_bin.mkdir(parents=True)
    create_admin_wrapper = release_bin / "create-admin"
    shutil.copy2(
        repository / "rel" / "overlays" / "bin" / "create-admin",
        create_admin_wrapper,
    )
    create_admin_wrapper.chmod(0o700)
    email = "pty-admin@example.invalid"
    password = "pty-password-canary-7d3fc2"
    key_line = (
        "[203.0.113.10]:2202 ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIEexamplekey"
    )

    _write_executable(
        commands / "ssh-keyscan",
        f"#!/bin/sh\nprintf '%s\\n' '{key_line}'\n",
    )
    _write_executable(
        commands / "ssh-keygen",
        (
            "#!/bin/sh\n"
            f"printf '%s\\n' '256 {config.host_key_fingerprint} test (ED25519)'\n"
        ),
    )
    _write_executable(
        commands / "ssh",
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

expected = json.loads(os.environ["EXPECTED_CREATE_ADMIN_COMMAND"])
terminal_fds = [os.isatty(descriptor) for descriptor in (0, 1, 2)]
invocation = {
    "argv": sys.argv,
    "environment": dict(os.environ),
    "terminal_fds": terminal_fds,
}
Path(os.environ["SSH_INVOCATION_LOG"]).write_text(
    json.dumps(invocation, sort_keys=True),
    encoding="utf-8",
)
with Path(os.environ["CREATE_ADMIN_BRIDGE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"stage": "ssh", **invocation}, sort_keys=True) + "\\n")
try:
    separator = sys.argv.index("--")
except ValueError:
    raise SystemExit(90)
remote_command = sys.argv[separator + 2:]
if (
    sys.argv[1] != "-tt"
    or sys.argv[separator + 1] != os.environ["EXPECTED_SSH_HOST"]
    or remote_command != expected
    or terminal_fds != [True, True, True]
):
    raise SystemExit(90)
os.execvpe(remote_command[0], remote_command, os.environ)
""",
    )
    _write_executable(
        commands / "sudo",
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

event = {
    "stage": "sudo",
    "argv": sys.argv,
    "environment": dict(os.environ),
    "terminal_fds": [os.isatty(descriptor) for descriptor in (0, 1, 2)],
}
with Path(os.environ["CREATE_ADMIN_BRIDGE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(event, sort_keys=True) + "\\n")
if sys.argv[1:3] != ["--", "systemd-run"] or event["terminal_fds"] != [True, True, True]:
    raise SystemExit(91)
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
""",
    )
    _write_executable(
        commands / "systemd-run",
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

expected = [
    "--wait",
    "--pipe",
    "--collect",
    "--property=User=taskman",
    "--property=Group=taskman",
    "--property=WorkingDirectory=/opt/taskman/current",
    "--property=EnvironmentFile=/etc/taskman/taskman.env",
    "--",
    "/opt/taskman/current/bin/create-admin",
]
event = {
    "stage": "systemd-run-start",
    "argv": sys.argv,
    "environment": dict(os.environ),
    "terminal_fds": [os.isatty(descriptor) for descriptor in (0, 1, 2)],
}
with Path(os.environ["CREATE_ADMIN_BRIDGE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(event, sort_keys=True) + "\\n")
if sys.argv[1:] != expected or event["terminal_fds"] != [True, True, True]:
    raise SystemExit(92)
completed = subprocess.run(
    [os.environ["CREATE_ADMIN_WRAPPER_PATH"]],
    check=False,
)
exit_event = {
    **event,
    "stage": "systemd-run-exit",
    "status": completed.returncode,
}
with Path(os.environ["CREATE_ADMIN_BRIDGE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(exit_event, sort_keys=True) + "\\n")
raise SystemExit(completed.returncode)
""",
    )
    _write_executable(
        release_bin / "taskman",
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

event = {
    "stage": "release-launcher",
    "argv": sys.argv,
    "environment": dict(os.environ),
    "working_directory": os.getcwd(),
    "terminal_fds": [os.isatty(descriptor) for descriptor in (0, 1, 2)],
}
with Path(os.environ["CREATE_ADMIN_BRIDGE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(event, sort_keys=True) + "\\n")
if (
    sys.argv[1:] != ["eval", "Taskman.Release.create_admin"]
    or event["terminal_fds"] != [True, True, True]
):
    raise SystemExit(93)
os.chdir(os.environ["TASKMAN_REPOSITORY"])
expression = '''
result = {
  Taskman.CredentialPrompts.prompt_for_email(Taskman.LocalTerminal),
  Taskman.CredentialPrompts.prompt_for_password(Taskman.LocalTerminal)
}
case result do
  {{:ok, email}, {:ok, password}}
  when byte_size(email) > 3 and byte_size(password) >= 8 -> System.halt(37)
  _ -> System.halt(41)
end
'''
os.execvpe(
    "mix",
    ["mix", "run", "--no-start", "-e", expression],
    {**os.environ, "MIX_ENV": "test"},
)
""",
    )

    child_source = """
from taskman_ops.output import render_json
from taskman_ops.workflows.create_admin import run_create_admin
import taskman_ops.workflows.create_admin as create_admin_workflow
from taskman_ops.config import EnvironmentConfig
import json
import os

class Remote:
    def facts(self):
        return object()

create_admin_workflow.validate_operational_preflight = lambda remote, config: remote.facts()
result = run_create_admin(
    Remote(),
    EnvironmentConfig.model_validate(json.loads(os.environ["CREATE_ADMIN_CONFIG"])),
)
print("CREATE_ADMIN_RESULT=" + render_json(result), flush=True)
raise SystemExit(result.process_status if result.process_status is not None else 99)
"""
    environment = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "PYTHONPATH": str(repository / "ops"),
        "CREATE_ADMIN_CONFIG": json.dumps(config_values),
        "CREATE_ADMIN_BRIDGE_LOG": str(bridge_log),
        "CREATE_ADMIN_WRAPPER_PATH": str(create_admin_wrapper),
        "EXPECTED_CREATE_ADMIN_COMMAND": json.dumps(list(CREATE_ADMIN_COMMAND)),
        "EXPECTED_SSH_HOST": config.ssh_host,
        "SSH_INVOCATION_LOG": str(invocation_log),
        "TASKMAN_REPOSITORY": str(repository),
    }
    pid, master = pty.fork()
    if pid == 0:  # pragma: no cover - assertions execute in the parent
        os.execve(
            sys.executable,
            [sys.executable, "-c", child_source],
            environment,
        )

    captured = bytearray()
    waited = False
    try:
        _read_pty_until(master, captured, b"Email: ")
        os.write(master, email.encode("utf-8") + b"\n")
        _read_pty_until(master, captured, b"Password: ")
        os.write(master, password.encode("utf-8") + b"\n")
        _read_pty_until(master, captured, b"Confirm password: ")
        assert password.encode("utf-8") not in captured
        os.write(master, password.encode("utf-8") + b"\n")
        _read_pty_until(master, captured, b"CREATE_ADMIN_RESULT=")
        _drain_pty(master, captured)
        _waited_pid, wait_status = os.waitpid(pid, 0)
        waited = True
    finally:
        os.close(master)
        if not waited:
            os.kill(pid, 9)
            os.waitpid(pid, 0)

    terminal_output = captured.decode("utf-8", errors="replace")
    invocation = json.loads(invocation_log.read_text(encoding="utf-8"))
    bridge_events = [
        json.loads(line)
        for line in bridge_log.read_text(encoding="utf-8").splitlines()
    ]
    result_text = terminal_output.split("CREATE_ADMIN_RESULT=", 1)[1].strip()
    result = json.loads(result_text)

    assert os.waitstatus_to_exitcode(wait_status) == 37
    assert invocation["argv"][1:5] == ["-tt", "-F", "/dev/null", "-o"]
    assert invocation["argv"][-len(CREATE_ADMIN_COMMAND) :] == list(
        CREATE_ADMIN_COMMAND
    )
    assert result["stage"] == "administrator-command-failed"
    assert result["facts"] == {"remote_status": 37}
    assert [event["stage"] for event in bridge_events] == [
        "ssh",
        "sudo",
        "systemd-run-start",
        "release-launcher",
        "systemd-run-exit",
    ]
    assert bridge_events[1]["argv"][1:] == list(CREATE_ADMIN_COMMAND[1:])
    assert bridge_events[2]["argv"][1:] == list(CREATE_ADMIN_COMMAND[3:])
    assert bridge_events[3]["argv"][1:] == [
        "eval",
        "Taskman.Release.create_admin",
    ]
    assert bridge_events[3]["working_directory"] == str(release_bin)
    assert bridge_events[4]["status"] == 37
    assert all(
        event["terminal_fds"] == [True, True, True] for event in bridge_events
    )
    assert password not in terminal_output
    assert password not in repr(invocation["argv"])
    assert password not in repr(invocation["environment"])
    assert password not in repr(bridge_events)
    assert password not in repr(result)


def test_create_admin_dry_run_discovers_and_plans_without_a_tty_or_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskman_ops.workflows.operational_preflight import (
        validate_operational_preflight as validate_real_preflight,
    )

    remote = FactRemote()
    monkeypatch.setattr(
        "taskman_ops.workflows.create_admin.validate_operational_preflight",
        lambda actual_remote, actual_config: validate_real_preflight(
            actual_remote,
            actual_config,
            host_validator=lambda checked_remote, _config: checked_remote.facts(),
        ),
    )

    result = run_create_admin(
        remote,
        _config(),
        dry_run=True,
        stdin=Terminal(False),
        stdout=Terminal(False),
        stderr=Terminal(False),
        session_runner=lambda _config: pytest.fail("dry-run opened an interactive session"),
    )

    assert remote.fact_calls == 1
    assert len(remote.command_calls) == 2
    assert "/etc/taskman/taskman.env" in remote.command_calls[0]
    assert "pg_database_size" in remote.command_calls[1][2]
    assert result.changed is False
    assert result.stage == "planned"
    assert result.facts == {
        "command": "/opt/taskman/current/bin/create-admin",
        "service_user": "taskman",
        "environment_file": "/etc/taskman/taskman.env",
    }
    assert result.process_status is None
    assert "password" not in render_json(result).lower()


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o700)


def _read_pty_until(
    descriptor: int,
    captured: bytearray,
    marker: bytes,
    *,
    timeout_seconds: float = 30,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while marker not in captured:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                f"timed out waiting for {marker!r}; captured={captured!r}"
            )
        readable, _writable, _exceptional = select.select(
            [descriptor], [], [], remaining
        )
        if not readable:
            continue
        try:
            chunk = os.read(descriptor, 4096)
        except OSError as error:
            raise AssertionError(
                f"PTY closed before {marker!r}; captured={captured!r}"
            ) from error
        if not chunk:
            raise AssertionError(
                f"PTY ended before {marker!r}; captured={captured!r}"
            )
        captured.extend(chunk)


def _drain_pty(
    descriptor: int, captured: bytearray, *, timeout_seconds: float = 5
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        readable, _writable, _exceptional = select.select(
            [descriptor], [], [], max(0, deadline - time.monotonic())
        )
        if not readable:
            return
        try:
            chunk = os.read(descriptor, 4096)
        except OSError:
            return
        if not chunk:
            return
        captured.extend(chunk)
