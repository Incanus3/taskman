from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import subprocess
import threading
import time

import pytest

from fakes import RecordingPyinfraHost
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import (
    HostRequest,
    HostResult,
    decode_request,
    decode_result,
    encode_request,
    encode_result,
)
from taskman_ops.remote import CommandResult, PyinfraRemote, _build_pyinfra_host, connect

from test_config import valid_environment


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(connection_timeout=17))


def test_run_quotes_each_argument_and_keeps_untrusted_text_out_of_the_shell() -> None:
    host = RecordingPyinfraHost()
    remote = PyinfraRemote(host, config())

    result = remote.run(("printf", "%s", "$(touch /tmp/not-run); value with spaces"))

    command, print_output, print_input, kwargs = host.commands[-1]
    rendered = command.get_raw_value()
    assert result.returncode == 0
    assert "'$(touch /tmp/not-run); value with spaces'" in rendered
    assert print_output is False
    assert print_input is False
    assert kwargs["_timeout"] == 17


def test_sensitive_stdin_is_not_requested_for_display_or_retained_in_result() -> None:
    host = RecordingPyinfraHost()
    remote = PyinfraRemote(host, config())
    canary = b"sensitive stdin canary"

    result = remote.run(("cat",), stdin=canary, sensitive=True, timeout=4)

    _command, print_output, print_input, kwargs = host.commands[-1]
    assert print_output is False
    assert print_input is False
    assert kwargs["_stdin"] == canary.decode()
    assert kwargs["_timeout"] == 4
    assert result.stdout == ""
    assert result.stderr == ""
    assert canary.decode() not in repr(result)


def test_run_refuses_explicit_limits_when_the_authenticated_channel_is_unavailable() -> None:
    """Falling back to a completed Host result would falsely claim a transport cap."""

    host = RecordingPyinfraHost()
    remote = PyinfraRemote(host, config())

    with pytest.raises(OpsError) as raised:
        remote.run(("true",), stdout_limit=4, stderr_limit=4)

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert host.commands == []


@dataclass
class StreamingChannel:
    """A Paramiko-shaped channel that exposes exactly how many bytes were read."""

    stdout: bytearray
    stderr: bytearray
    stdout_read: int = 0
    stderr_read: int = 0
    closed: bool = False

    def recv_ready(self) -> bool:
        return bool(self.stdout)

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr)

    def recv(self, size: int) -> bytes:
        value = bytes(self.stdout[:size])
        del self.stdout[:size]
        self.stdout_read += len(value)
        return value

    def recv_stderr(self, size: int) -> bytes:
        value = bytes(self.stderr[:size])
        del self.stderr[:size]
        self.stderr_read += len(value)
        return value

    def exit_status_ready(self) -> bool:
        return not self.stdout and not self.stderr

    def recv_exit_status(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


@dataclass
class StreamingStdin:
    writes: list[bytes] = field(default_factory=list)
    closed: bool = False

    def write(self, value: bytes) -> None:
        self.writes.append(value)

    def close(self) -> None:
        self.closed = True


@dataclass
class StreamingOutput:
    channel: StreamingChannel


@dataclass
class StreamingClient:
    channel: StreamingChannel
    stdin: StreamingStdin = field(default_factory=StreamingStdin)
    commands: list[tuple[str, bool]] = field(default_factory=list)
    timeouts: list[int | float | None] = field(default_factory=list)

    def exec_command(
        self, command: str, *, get_pty: bool, timeout: int | float | None = None
    ) -> tuple[StreamingStdin, StreamingOutput, StreamingOutput]:
        self.commands.append((command, get_pty))
        self.timeouts.append(timeout)
        return self.stdin, StreamingOutput(self.channel), StreamingOutput(self.channel)


@dataclass
class StreamingConnector:
    client: StreamingClient


@dataclass
class StreamingHost:
    connector: StreamingConnector
    connector_data: dict[str, object] = field(default_factory=dict)
    state: object = field(default_factory=object)
    host_api_calls: int = 0

    def run_shell_command(self, *_args: object, **_kwargs: object) -> object:
        self.host_api_calls += 1
        raise AssertionError("bounded commands must not buffer through Host.run_shell_command")


@dataclass
class DeadlineStdin:
    phase: str
    entered: threading.Event
    connection_closed: threading.Event
    worker_finished: threading.Event
    writes: list[bytes] = field(default_factory=list)

    def write(self, value: bytes) -> None:
        self.writes.append(value)
        if self.phase == "stdin":
            self.entered.set()
            try:
                self.connection_closed.wait(2.0)
                raise OSError("secret setup failure canary")
            finally:
                self.worker_finished.set()

    def close(self) -> None:
        return


@dataclass
class DeadlineClient:
    phase: str
    channel: StreamingChannel
    entered: threading.Event = field(default_factory=threading.Event)
    connection_closed: threading.Event = field(default_factory=threading.Event)
    worker_finished: threading.Event = field(default_factory=threading.Event)
    stdin: DeadlineStdin = field(init=False)

    def __post_init__(self) -> None:
        self.stdin = DeadlineStdin(
            self.phase,
            self.entered,
            self.connection_closed,
            self.worker_finished,
        )

    def exec_command(
        self, _command: str, *, get_pty: bool, timeout: int | float | None = None
    ) -> tuple[DeadlineStdin, StreamingOutput, StreamingOutput]:
        assert get_pty is False
        assert timeout == 1
        if self.phase == "setup":
            self.entered.set()
            try:
                self.connection_closed.wait(2.0)
                raise OSError("secret setup failure canary")
            finally:
                self.worker_finished.set()
        output = StreamingOutput(self.channel)
        return self.stdin, output, output

    def close(self) -> None:
        self.connection_closed.set()


@dataclass
class DeadlineConnector:
    client: DeadlineClient
    disconnected: bool = False

    def disconnect(self) -> None:
        self.disconnected = True


@pytest.mark.parametrize("phase", ("setup", "stdin"))
def test_bounded_run_interrupts_blocking_setup_at_the_single_deadline(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    """Blocking command or stdin setup cannot outlive the controller deadline."""

    client = DeadlineClient(
        phase,
        StreamingChannel(stdout=bytearray(b"must not be read"), stderr=bytearray()),
    )
    connector = DeadlineConnector(client)
    remote = PyinfraRemote(StreamingHost(connector), config())
    read_started = False

    def read(*_args: object, **_kwargs: object) -> tuple[bytes, bytes, int]:
        nonlocal read_started
        read_started = True
        raise AssertionError("channel draining must not start after setup timeout")

    monkeypatch.setattr("taskman_ops.remote._read_bounded_channel", read)
    started = time.monotonic()

    with pytest.raises(OpsError) as raised:
        remote.run(
            ("secret-command-canary",),
            stdin=b"secret-stdin-canary",
            timeout=1,
            stdout_limit=64,
            stderr_limit=64,
        )

    elapsed = time.monotonic() - started
    assert 0.8 <= elapsed < 1.5
    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert "secret-command-canary" not in str(raised.value)
    assert "secret setup failure canary" not in str(raised.value)
    assert client.entered.is_set()
    assert client.connection_closed.is_set()
    assert connector.disconnected is True
    assert client.worker_finished.wait(0.2)
    assert read_started is False
    assert not any(
        worker.name == "taskman-bounded-ssh-setup" and worker.is_alive()
        for worker in threading.enumerate()
    )


@pytest.mark.parametrize(
    ("stream", "counter"), [("stdout", "stdout_read"), ("stderr", "stderr_read")]
)
def test_run_stops_streaming_output_at_limit_plus_one_bytes(stream: str, counter: str) -> None:
    """A buffered connector result could consume unbounded controller memory before rejection."""

    channel = StreamingChannel(
        stdout=bytearray(b"abcdefgh") if stream == "stdout" else bytearray(),
        stderr=bytearray(b"abcdefgh") if stream == "stderr" else bytearray(),
    )
    client = StreamingClient(channel)
    host = StreamingHost(StreamingConnector(client))
    remote = PyinfraRemote(host, config())

    with pytest.raises(OpsError) as raised:
        remote.run(
            ("printf", "%s", "bounded"),
            stdin=b"request-body",
            timeout=3,
            stdout_limit=4,
            stderr_limit=4,
        )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert getattr(channel, counter) == 5
    assert channel.closed is True
    assert client.stdin.writes == [b"request-body"]
    assert host.host_api_calls == 0


def test_run_with_explicit_limits_drains_both_streams_without_using_the_buffering_host_api() -> None:
    """Reading only one channel can deadlock an SSH command whose other channel fills first."""

    channel = StreamingChannel(stdout=bytearray(b"stdout"), stderr=bytearray(b"stderr"))
    client = StreamingClient(channel)
    host = StreamingHost(StreamingConnector(client))
    remote = PyinfraRemote(host, config())

    result = remote.run(
        ("printf", "%s", "value"),
        stdin=b"request-body",
        sudo=True,
        timeout=3,
        stdout_limit=6,
        stderr_limit=6,
    )

    assert result.returncode == 0
    assert result.stdout == "stdout"
    assert result.stderr == "stderr"
    assert channel.stdout_read == 6
    assert channel.stderr_read == 6
    assert client.stdin.writes == [b"request-body"]
    assert client.commands[0][1] is False
    assert "sudo" in client.commands[0][0]
    assert host.host_api_calls == 0


def test_bounded_run_passes_requested_timeout_to_ssh_channel_setup() -> None:
    """Paramiko setup must not be able to exceed the controller's command bound."""

    client = StreamingClient(
        StreamingChannel(stdout=bytearray(b"ok"), stderr=bytearray())
    )
    remote = PyinfraRemote(StreamingHost(StreamingConnector(client)), config())

    remote.run(("true",), timeout=4, stdout_limit=8, stderr_limit=8)

    assert client.timeouts == [4]


def test_bounded_run_reduces_channel_read_budget_by_setup_elapsed_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSH setup and channel draining share one monotonic deadline."""

    client = StreamingClient(
        StreamingChannel(stdout=bytearray(b"ok"), stderr=bytearray())
    )
    remote = PyinfraRemote(StreamingHost(StreamingConnector(client)), config())
    observed: dict[str, float] = {}

    monkeypatch.setattr(
        "taskman_ops.remote.time.monotonic",
        iter((10.0, 10.0, 11.25)).__next__,
    )

    def read(
        _channel: object,
        *,
        timeout: float,
        stdout_limit: int | None,
        stderr_limit: int | None,
    ) -> tuple[bytes, bytes, int]:
        observed["timeout"] = timeout
        assert stdout_limit == 8
        assert stderr_limit == 8
        return b"ok", b"", 0

    monkeypatch.setattr("taskman_ops.remote._read_bounded_channel", read)

    remote.run(("true",), timeout=4, stdout_limit=8, stderr_limit=8)

    assert observed["timeout"] == pytest.approx(2.75)


@pytest.mark.parametrize(
    ("argv", "payload"),
    [
        (("id", "-u"), b"1000\n"),
        (("stat", "-c", "%u:%g:%a:%F", "--", "/tmp/taskman-ops"), b"1000:1000:700:directory\n"),
        (("sha256sum", "--", "/tmp/taskman-ops/taskman-host.pyz"), b"f" * 64 + b"  /tmp/taskman-ops/taskman-host.pyz\n"),
    ],
)
def test_bounded_run_preserves_real_control_command_output_bytes(
    argv: tuple[str, ...], payload: bytes
) -> None:
    """Control parsers, not transport, own their one-terminal-newline contract."""

    channel = StreamingChannel(stdout=bytearray(payload), stderr=bytearray())
    client = StreamingClient(channel)
    host = StreamingHost(StreamingConnector(client))
    remote = PyinfraRemote(host, config())

    result = remote.run(argv, timeout=3, stdout_limit=128, stderr_limit=128)

    assert result.stdout.encode("utf-8") == payload
    assert channel.stdout_read == len(payload)
    assert host.host_api_calls == 0


def test_bounded_run_preserves_helper_protocol_request_and_result_bytes() -> None:
    """Newlines or Unicode line splitting would make a strict helper frame undecodable."""

    request = HostRequest(
        protocol_version=2,
        operation="discover",
        correlation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"lifecycle": "unknown"},
        paths={"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        parameters={"dry_run": False},
    )
    helper_result = HostResult(
        protocol_version=2,
        operation="discover",
        correlation_id="op-0123456789abcdef0123456789abcdef",
        outcome="succeeded",
        message="helper operation completed",
        state={},
        warnings=("unicode separator \u2028 remains protocol data",),
    )
    request_payload = encode_request(request)
    result_payload = encode_result(helper_result)
    channel = StreamingChannel(stdout=bytearray(result_payload), stderr=bytearray())
    client = StreamingClient(channel)
    remote = PyinfraRemote(StreamingHost(StreamingConnector(client)), config())

    result = remote.run(
        ("sudo", "--", "python3", "/run/taskman-ops/op-0123456789abcdef0123456789abcdef/taskman-host.pyz"),
        stdin=request_payload,
        timeout=3,
        stdout_limit=64 * 1024,
        stderr_limit=4 * 1024,
    )

    assert result.stdout.encode("utf-8") == result_payload
    assert decode_result(result.stdout.encode("utf-8")) == helper_result
    assert b"".join(client.stdin.writes) == request_payload
    assert decode_request(b"".join(client.stdin.writes)) == request


def test_put_reports_private_staging_residue_when_cleanup_is_uncertain(tmp_path: Path) -> None:
    """A successful final install must not hide a remaining private upload copy."""

    class CleanupFailureRemote(PyinfraRemote):
        def __init__(self) -> None:
            super().__init__(object(), config())
            self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def run(self, argv: object, **kwargs: object) -> CommandResult:
            command = tuple(argv)  # type: ignore[arg-type]
            self.calls.append((command, kwargs))
            return CommandResult(1 if command[0] in {"rm", "rmdir"} else 0)

        def _put_file(self, _source: Path, _destination: PurePosixPath, *, timeout: int) -> bool:
            assert timeout == 17
            return True

    source = tmp_path / "helper.pyz"
    source.write_bytes(b"helper")
    remote = CleanupFailureRemote()

    receipt = remote.put(
        source, PurePosixPath("/tmp/taskman-ops/helper.pyz"), mode=0o600, sensitive=True
    )

    assert receipt.cleanup_warning is True
    assert all(
        kwargs.get("stdout_limit") is not None and kwargs.get("stderr_limit") is not None
        for _command, kwargs in remote.calls
    )


def test_put_keeps_its_primary_error_when_private_stage_cleanup_is_uncertain(tmp_path: Path) -> None:
    """An upload failure remains generic even if private cleanup is uncertain."""

    class FailureAndCleanupRemote(PyinfraRemote):
        def __init__(self) -> None:
            super().__init__(object(), config())
            self.calls: list[tuple[str, ...]] = []

        def run(self, argv: object, **_kwargs: object) -> CommandResult:
            command = tuple(argv)  # type: ignore[arg-type]
            self.calls.append(command)
            if command[0] in {"install", "rm", "rmdir"} and "-d" not in command:
                return CommandResult(1)
            return CommandResult(0)

        def _put_file(self, _source: Path, _destination: PurePosixPath, *, timeout: int) -> bool:
            assert timeout == 17
            return True

    source = tmp_path / "helper.pyz"
    source.write_bytes(b"helper")
    remote = FailureAndCleanupRemote()

    with pytest.raises(OpsError) as raised:
        remote.put(source, PurePosixPath("/tmp/taskman-ops/helper.pyz"), mode=0o600, sensitive=True)

    assert raised.value.message == "private remote upload failed"
    assert not hasattr(raised.value, "residue_paths")


class RecordingUploadRemote(PyinfraRemote):
    """Exercise ``PyinfraRemote.put`` without a real SSH transport."""

    def __init__(self, host: object | None = None) -> None:
        super().__init__(object() if host is None else host, config())
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.uploads: list[tuple[Path, PurePosixPath, int]] = []

    def run(self, argv: object, **kwargs: object) -> CommandResult:
        command = tuple(argv)  # type: ignore[arg-type]
        self.calls.append((command, kwargs))
        return CommandResult(0)

    def _put_file(self, source: Path, destination: PurePosixPath, *, timeout: int) -> bool:
        self._set_upload_timeout(timeout)
        self.uploads.append((source, destination, timeout))
        return True


def test_put_stages_every_upload_in_a_unique_private_directory_and_installs_exact_mode(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime.env"
    source.write_bytes(b"sensitive runtime canary")
    remote = RecordingUploadRemote()

    remote.put(source, PurePosixPath("/etc/taskman/taskman.env"), mode=0o600, sensitive=True)
    remote.put(source, PurePosixPath("/etc/taskman/second.env"), mode=0o600, sensitive=True)

    first_upload, second_upload = remote.uploads
    assert first_upload[1] != second_upload[1]
    assert first_upload[1].as_posix().startswith("/tmp/taskman-upload-")
    assert all("sensitive runtime canary" not in repr(command) for command, _kwargs in remote.calls)
    assert any(command[:4] == ("install", "-d", "-m", "700") for command, _kwargs in remote.calls)
    assert any(
        command[0:3] == ("install", "-m", "600") and command[-1] == "/etc/taskman/taskman.env"
        for command, _kwargs in remote.calls
    )


def test_put_refuses_modes_that_would_expose_private_upload_content(tmp_path: Path) -> None:
    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"release")
    remote = RecordingUploadRemote()

    with pytest.raises(ValueError):
        remote.put(source, PurePosixPath("/opt/taskman/release.tar.gz"), mode=0o666)


def test_put_uses_sudo_only_for_the_final_install_and_removes_only_its_private_staging_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime.env"
    source.write_bytes(b"private")
    remote = RecordingUploadRemote()

    remote.put(source, PurePosixPath("/etc/taskman/taskman.env"), mode=0o600, sensitive=True)

    commands = remote.calls
    staging_directory = commands[0][0][-1]
    staging_file = commands[1][0][-1]
    assert commands[0] == (
        ("install", "-d", "-m", "700", "--", staging_directory),
        {
            "sudo": False,
            "sensitive": True,
            "timeout": 17,
            "stdout_limit": 1024,
            "stderr_limit": 4096,
        },
    )
    assert commands[1] == (
        ("chmod", "600", "--", staging_file),
        {
            "sudo": False,
            "sensitive": True,
            "timeout": 17,
            "stdout_limit": 1024,
            "stderr_limit": 4096,
        },
    )
    assert commands[2] == (
        ("install", "-m", "600", "--", staging_file, "/etc/taskman/taskman.env"),
        {
            "sudo": True,
            "sensitive": True,
            "timeout": 17,
            "stdout_limit": 1024,
            "stderr_limit": 4096,
        },
    )
    assert commands[3] == (
        ("rm", "-f", "--", staging_file),
        {
            "sudo": False,
            "sensitive": True,
            "timeout": 17,
            "stdout_limit": 1024,
            "stderr_limit": 4096,
        },
    )
    assert commands[4] == (
        ("rmdir", "--", staging_directory),
        {
            "sudo": False,
            "sensitive": True,
            "timeout": 17,
            "stdout_limit": 1024,
            "stderr_limit": 4096,
        },
    )


def test_put_can_preserve_administrator_ownership_for_a_private_transfer(tmp_path: Path) -> None:
    """Forcing the helper transfer through sudo would make its pre-root check meaningless."""

    source = tmp_path / "taskman-host.pyz"
    source.write_bytes(b"helper")
    remote = RecordingUploadRemote()

    remote.put(
        source,
        PurePosixPath("/tmp/taskman-ops/op-0123456789abcdef0123456789abcdef/taskman-host.pyz"),
        mode=0o600,
        sensitive=True,
        sudo=False,
    )

    _command, kwargs = remote.calls[2]
    assert kwargs["sudo"] is False


def test_put_applies_the_requested_timeout_to_pyinfra_sftp_before_transfer(tmp_path: Path) -> None:
    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"release")
    host = RecordingPyinfraHost()
    observed: list[int] = []

    class Channel:
        def settimeout(self, value: int) -> None:
            observed.append(value)

    class Transfer:
        def get_channel(self) -> Channel:
            return Channel()

    class Connector:
        def get_file_transfer_connection(self) -> Transfer:
            return Transfer()

    host.connector = Connector()  # type: ignore[attr-defined]
    remote = RecordingUploadRemote(host)

    remote.put(source, PurePosixPath("/opt/taskman/release.tar.gz"), mode=0o600, timeout=4)

    assert observed == [4]


def test_connect_uses_only_a_fingerprint_verified_temporary_known_hosts_file(tmp_path: Path) -> None:
    config_value = config()
    key_line = "[203.0.113.10]:2202 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEexamplekey"
    calls: list[tuple[list[str], dict[str, object]]] = []
    observed: dict[str, object] = {}

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        if argv[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(argv, 0, key_line.encode() + b"\n", b"")
        assert argv[:4] == ["ssh-keygen", "-lf", "-", "-E"]
        return subprocess.CompletedProcess(argv, 0, f"256 {config_value.host_key_fingerprint} test (ED25519)\n".encode(), b"")

    def host_factory(factory_config: EnvironmentConfig, known_hosts: Path) -> RecordingPyinfraHost:
        observed["config"] = factory_config
        observed["path"] = known_hosts
        observed["contents"] = known_hosts.read_text(encoding="utf-8")
        observed["mode"] = known_hosts.stat().st_mode & 0o777
        return RecordingPyinfraHost()

    remote = connect(config_value, command_runner=runner, host_factory=host_factory, temporary_directory=tmp_path)

    assert isinstance(remote, PyinfraRemote)
    assert observed["config"] is config_value
    assert observed["contents"] == key_line + "\n"
    assert observed["mode"] == 0o600
    assert not Path(observed["path"]).exists()
    assert calls[0][0] == ["ssh-keyscan", "-T", "17", "-p", "2202", "203.0.113.10"]
    assert all(kwargs.get("shell") is not True for _argv, kwargs in calls)


def test_connect_rejects_unverified_host_key_before_constructing_a_remote(tmp_path: Path) -> None:
    config_value = config()

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(argv, 0, b"host ssh-ed25519 AAAA\n", b"")
        return subprocess.CompletedProcess(argv, 0, b"256 SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB test (ED25519)\n", b"")

    with pytest.raises(OpsError) as raised:
        connect(config_value, command_runner=runner, temporary_directory=tmp_path)

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert list(tmp_path.iterdir()) == []


def test_pyinfra_host_ignores_user_ssh_configuration_and_requires_known_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("203.0.113.10 ssh-ed25519 AAAA\n", encoding="utf-8")
    monkeypatch.setattr("pyinfra.api.host.Host.connect", lambda self, **_kwargs: None)

    host = _build_pyinfra_host(config(), known_hosts)

    assert host.connector.data["ssh_config_file"] == "/dev/null"
    assert host.connector.data["ssh_known_hosts_file"] == str(known_hosts)
    assert host.connector.data["ssh_strict_host_key_checking"] == "yes"
    assert host.connector.data["ssh_connect_retries"] == 0


def test_transport_failures_map_to_five_without_reclassifying_explicit_lock_contention() -> None:
    host = RecordingPyinfraHost(failures=deque([OSError("network down"), OpsError(ExitStatus.LOCKED, "lock", "held")]))
    remote = PyinfraRemote(host, config())

    with pytest.raises(OpsError) as transport_error:
        remote.run(("true",))
    with pytest.raises(OpsError) as lock_error:
        remote.run(("true",))

    assert transport_error.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert lock_error.value.status is ExitStatus.LOCKED
