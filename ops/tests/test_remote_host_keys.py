"""Local known-host selection and strict transport trust boundaries."""

from pathlib import Path
import subprocess

import paramiko
import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.remote import connect, run_interactive
from tests.support.environments import environment_config
from tests.support.remotes import RecordingPyinfraHost
from tests.support.ssh_keys import generate_host_key, write_known_hosts


@pytest.mark.parametrize("transport,port,hashed", [
    pytest.param("pyinfra", 22, False, id="pyinfra-default-plain"),
    pytest.param("pyinfra", 22, True, id="pyinfra-default-hashed"),
    pytest.param("pyinfra", 2202, False, id="pyinfra-custom-plain"),
    pytest.param("pyinfra", 2202, True, id="pyinfra-custom-hashed"),
    pytest.param("interactive", 22, False, id="interactive-default-plain"),
    pytest.param("interactive", 2202, True, id="interactive-custom-hashed"),
])
def test_local_pinned_key_is_the_only_client_trust_and_operator_file_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str, port: int, hashed: bool,
) -> None:
    """Scanning, wrong port matching, or retaining extra keys broadens/breaks client trust."""
    pinned = generate_host_key(tmp_path, "pinned")
    other = generate_host_key(tmp_path, "other")
    other_algorithm = generate_host_key(tmp_path, "other-algorithm", "ecdsa")
    config = environment_config(ssh_port=port, host_key_fingerprint=pinned.fingerprint)
    host = "203.0.113.10" if port == 22 else "[203.0.113.10]:2202"
    source = write_known_hosts(tmp_path, f"{host} {other.public}\n{host} {pinned.public}\n{host} {other_algorithm.public}\n", hashed=hashed)
    before = source.read_bytes()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    observed: list[Path] = []

    def inspect_trust(path: Path) -> None:
        observed.append(path)
        assert path.stat().st_mode & 0o777 == 0o600
        keys = paramiko.HostKeys(str(path))
        pinned_key = paramiko.Ed25519Key.from_path(tmp_path / "pinned")
        other_key = paramiko.Ed25519Key.from_path(tmp_path / "other")
        assert keys.check(host, pinned_key)
        assert not keys.check(host, other_key)
        assert set(keys.lookup(host)) == {"ssh-ed25519"}
        assert len(path.read_text().splitlines()) == 1

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[0] == "ssh":
            path = Path(next(value.split("=", 1)[1] for value in argv if value.startswith("UserKnownHostsFile=")))
            inspect_trust(path)
            assert "GlobalKnownHostsFile=/dev/null" in argv
            assert "StrictHostKeyChecking=yes" in argv
            assert kwargs == {"check": False}
            return subprocess.CompletedProcess(argv, 37)
        assert argv[0] == "ssh-keygen"
        return subprocess.run(argv, **kwargs)

    def factory(_config: object, path: Path) -> RecordingPyinfraHost:
        inspect_trust(path)
        return RecordingPyinfraHost()

    if transport == "pyinfra":
        remote = connect(config, command_runner=runner, host_factory=factory, temporary_directory=tmp_path)
        remote.close()
    else:
        assert run_interactive(config, ("true",), command_runner=runner, temporary_directory=tmp_path) == 37
    assert len(observed) == 1
    assert not observed[0].exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize("transport,case", [
    pytest.param("pyinfra", "missing", id="pyinfra-missing"),
    pytest.param("pyinfra", "no-host", id="pyinfra-no-host"),
    pytest.param("pyinfra", "wrong-port", id="pyinfra-wrong-port"),
    pytest.param("pyinfra", "mismatch", id="pyinfra-mismatch"),
    pytest.param("pyinfra", "malformed", id="pyinfra-malformed"),
    pytest.param("pyinfra", "revoked", id="pyinfra-revoked"),
    pytest.param("pyinfra", "revoked-duplicate", id="pyinfra-revoked-duplicate"),
    pytest.param("pyinfra", "revoked-hashed", id="pyinfra-revoked-hashed"),
    pytest.param("pyinfra", "ca-only", id="pyinfra-ca-only"),
    pytest.param("interactive", "missing", id="interactive-missing"),
    pytest.param("interactive", "revoked", id="interactive-revoked"),
])
def test_invalid_local_trust_fails_before_any_connection_with_actionable_generic_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str, case: str,
) -> None:
    """Missing, invalid, unpinned or revoked local trust must never reach a client."""
    pinned = generate_host_key(tmp_path, "pinned")
    other = generate_host_key(tmp_path, "other")
    config = environment_config(host_key_fingerprint=pinned.fingerprint)
    host = "[203.0.113.10]:2202"
    cases = {
        "no-host": f"unrelated.invalid {pinned.public}\n",
        "wrong-port": f"203.0.113.10 {pinned.public}\n",
        "mismatch": f"{host} {other.public}\n",
        "malformed": f"{host} ssh-ed25519 AAAA\n{host} {pinned.public}\n",
        "revoked": f"@revoked {host} {pinned.public}\n",
        "revoked-duplicate": f"{host} {pinned.public}\n@revoked {host} {pinned.public}\n",
        "revoked-hashed": f"{host} {pinned.public}\n@revoked {host} {pinned.public}\n",
        "ca-only": f"@cert-authority {host} {pinned.public}\n",
    }
    source = None if case == "missing" else write_known_hosts(tmp_path, cases[case], hashed=case == "revoked-hashed")
    before = None if source is None else source.read_bytes()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert argv[0] == "ssh-keygen", "local preflight attempted a connection or scan"
        return subprocess.run(argv, **kwargs)

    def factory(_config: object, _path: Path) -> object:
        pytest.fail("invalid trust reached the client")

    with pytest.raises(OpsError) as raised:
        if transport == "pyinfra":
            connect(config, command_runner=runner, host_factory=factory, temporary_directory=tmp_path)
        else:
            run_interactive(config, ("true",), command_runner=runner, temporary_directory=tmp_path)
    error = raised.value
    assert error.status is ExitStatus.REMOTE_PREFLIGHT
    assert error.changed is False
    assert "known_hosts" in error.next_action
    assert "fingerprint" in error.next_action
    assert pinned.fingerprint not in str(error)
    assert str(tmp_path) not in str(error)
    assert not list(tmp_path.glob("taskman-known-hosts-*"))
    if source is not None:
        assert source.read_bytes() == before


@pytest.mark.parametrize("transport", ["pyinfra", "interactive"])
def test_local_lookup_os_failure_is_sanitized_and_does_not_attempt_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str,
) -> None:
    """An unreadable store/tool failure must not leak diagnostics or fall back to scanning."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        raise PermissionError("private filesystem diagnostic canary")

    with pytest.raises(OpsError) as raised:
        if transport == "pyinfra":
            connect(environment_config(), command_runner=runner)
        else:
            run_interactive(environment_config(), ("true",), command_runner=runner)
    assert len(calls) == 1
    assert calls[0][:3] == ["ssh-keygen", "-F", "[203.0.113.10]:2202"]
    assert "canary" not in str(raised.value)
    assert "known_hosts" in raised.value.next_action


@pytest.mark.parametrize("transport", ["pyinfra", "interactive"])
def test_pinned_temporary_trust_is_removed_after_client_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str,
) -> None:
    """Connection exceptions must not leave transient trust files behind."""
    pinned = generate_host_key(tmp_path, "pinned")
    write_known_hosts(tmp_path, f"[203.0.113.10]:2202 {pinned.public}\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    paths: list[Path] = []

    def factory(_config: object, path: Path) -> object:
        paths.append(path)
        raise OSError("client diagnostic canary")

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[0] == "ssh":
            factory(None, Path(next(value.split("=", 1)[1] for value in argv if value.startswith("UserKnownHostsFile="))))
        assert argv[0] == "ssh-keygen"
        return subprocess.run(argv, **kwargs)

    config = environment_config(host_key_fingerprint=pinned.fingerprint)
    with pytest.raises(OpsError):
        if transport == "pyinfra":
            connect(config, command_runner=runner, host_factory=factory, temporary_directory=tmp_path)
        else:
            run_interactive(config, ("true",), command_runner=runner, temporary_directory=tmp_path)
    assert len(paths) == 1
    assert not paths[0].exists()


@pytest.mark.parametrize("host_pattern", ["[203.0.113.*]:2202", "![203.0.113.11]:2202,[203.0.113.*]:2202"])
def test_native_pattern_matching_is_normalized_for_the_exact_client_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_pattern: str,
) -> None:
    """Passing wildcard records straight to Paramiko loses native host matching."""
    pinned = generate_host_key(tmp_path, "pinned")
    write_known_hosts(tmp_path, f"{host_pattern} {pinned.public}\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    observed: list[str] = []

    def factory(_config: object, path: Path) -> RecordingPyinfraHost:
        observed.append(path.read_text())
        return RecordingPyinfraHost()

    connect(environment_config(host_key_fingerprint=pinned.fingerprint), host_factory=factory, temporary_directory=tmp_path)
    assert observed == [f"[203.0.113.10]:2202 {pinned.public}\n"]


def test_other_revoked_keys_and_ca_records_do_not_replace_the_ordinary_pinned_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignoring unrelated markers must preserve the pin without granting CA trust."""
    pinned = generate_host_key(tmp_path, "pinned")
    other = generate_host_key(tmp_path, "other")
    host = "[203.0.113.10]:2202"
    write_known_hosts(tmp_path, f"@revoked {host} {other.public}\n@cert-authority {host} {pinned.public}\n{host} {pinned.public}\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    observed: list[str] = []

    def factory(_config: object, path: Path) -> RecordingPyinfraHost:
        observed.append(path.read_text())
        return RecordingPyinfraHost()

    connect(environment_config(host_key_fingerprint=pinned.fingerprint), host_factory=factory, temporary_directory=tmp_path)
    assert observed == [f"{host} {pinned.public}\n"]


@pytest.mark.parametrize("stage", ["lookup", "fingerprint"])
def test_each_native_host_key_process_has_a_deadline_and_timeout_is_local_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    """Unbounded native-tool execution can stall preflight indefinitely."""
    pinned = generate_host_key(tmp_path, "pinned")
    write_known_hosts(tmp_path, f"[203.0.113.10]:2202 {pinned.public}\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = environment_config(host_key_fingerprint=pinned.fingerprint, connection_timeout=17)
    observed: list[object] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append(kwargs.get("timeout"))
        if (argv[1] == "-F") == (stage == "lookup"):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"), output=b"private canary")
        return subprocess.run(argv, **kwargs)

    with pytest.raises(OpsError) as raised:
        connect(config, command_runner=runner, host_factory=lambda _config, _path: pytest.fail("timeout reached client"))
    assert observed == ([17] if stage == "lookup" else [17, 17])
    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert "known_hosts" in raised.value.next_action
    assert "canary" not in str(raised.value)
