from __future__ import annotations

from collections import deque
from pathlib import Path, PurePosixPath
import subprocess

import pytest

from fakes import RecordingPyinfraHost
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.remote import PyinfraRemote, _build_pyinfra_host, connect

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


def test_put_stages_every_upload_in_a_unique_private_directory_and_installs_exact_mode(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime.env"
    source.write_bytes(b"sensitive runtime canary")
    host = RecordingPyinfraHost()
    remote = PyinfraRemote(host, config())

    remote.put(source, PurePosixPath("/etc/taskman/taskman.env"), mode=0o600, sensitive=True)
    remote.put(source, PurePosixPath("/etc/taskman/second.env"), mode=0o600, sensitive=True)

    first_upload, second_upload = host.uploads
    assert first_upload[1] != second_upload[1]
    assert first_upload[1].startswith("/tmp/taskman-upload-")
    assert first_upload[2:4] == (False, False)
    assert all("sensitive runtime canary" not in command.get_raw_value() for command, *_ in host.commands)
    rendered = [command.get_raw_value() for command, *_ in host.commands]
    assert any("install" in command and "700" in command for command in rendered)
    assert any("install" in command and "600" in command and "/etc/taskman/taskman.env" in command for command in rendered)


def test_put_refuses_modes_that_would_expose_private_upload_content(tmp_path: Path) -> None:
    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"release")
    remote = PyinfraRemote(RecordingPyinfraHost(), config())

    with pytest.raises(ValueError):
        remote.put(source, PurePosixPath("/opt/taskman/release.tar.gz"), mode=0o666)


def test_put_uses_sudo_only_for_the_final_install_and_removes_only_its_private_staging_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime.env"
    source.write_bytes(b"private")
    host = RecordingPyinfraHost()
    remote = PyinfraRemote(host, config())

    remote.put(source, PurePosixPath("/etc/taskman/taskman.env"), mode=0o600, sensitive=True)

    commands = [
        (tuple(bit.obj for bit in command.bits[4:]), kwargs)
        for command, _print_output, _print_input, kwargs in host.commands
    ]
    staging_directory = commands[0][0][-1]
    staging_file = commands[1][0][-1]
    assert commands[0] == (
        ("install", "-d", "-m", "700", "--", staging_directory),
        {"_sudo": False, "_stdin": None, "_timeout": 17},
    )
    assert commands[1] == (
        ("chmod", "600", "--", staging_file),
        {"_sudo": False, "_stdin": None, "_timeout": 17},
    )
    assert commands[2] == (
        ("install", "-m", "600", "--", staging_file, "/etc/taskman/taskman.env"),
        {"_sudo": True, "_stdin": None, "_timeout": 17},
    )
    assert commands[3] == (
        ("rm", "-f", "--", staging_file),
        {"_sudo": False, "_stdin": None, "_timeout": 17},
    )
    assert commands[4] == (
        ("rmdir", "--", staging_directory),
        {"_sudo": False, "_stdin": None, "_timeout": 17},
    )


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
    remote = PyinfraRemote(host, config())

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
