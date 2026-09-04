"""Strict SSH transport boundary for the deployment controller.

Only this module knows about pyinfra's programmatic objects.  The rest of the
controller talks to :class:`Remote`, which keeps command construction,
host-key verification, temporary uploads, and transport error classification
in one auditable place.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from pyinfra.api import Config, Inventory, State
from pyinfra.api.command import QuoteString, StringCommand

from .config import EnvironmentConfig
from .errors import ExitStatus, OpsError

if TYPE_CHECKING:
    from .host.facts import HostFacts


_PRIVATE_UPLOAD_ROOT = PurePosixPath("/tmp")
_PRIVATE_UPLOAD_MODE = 0o600
_PRIVATE_DIRECTORY_MODE = 0o700


@dataclass(frozen=True)
class CommandResult:
    """A bounded result from one remote argv command."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class ChangeSet:
    """A small, workflow-facing summary of a convergent operation."""

    changed: bool
    operations: tuple[str, ...] = ()


class Remote(Protocol):
    """Remote capabilities consumed by workflows and host checks."""

    def facts(self) -> HostFacts: ...

    def run(
        self,
        argv: Sequence[str],
        *,
        sudo: bool = False,
        stdin: bytes | None = None,
        sensitive: bool = False,
        timeout: int | None = None,
    ) -> CommandResult: ...

    def put(
        self,
        source: Path,
        destination: PurePosixPath,
        *,
        mode: int,
        sensitive: bool = False,
        timeout: int | None = None,
    ) -> None: ...

    def converge(self, deploy: Callable[..., object], data: Mapping[str, object]) -> ChangeSet: ...


class PyinfraRemote:
    """A strict, one-host adapter around pyinfra's SSH connector.

    ``host`` is intentionally an untyped implementation detail.  It is a
    pyinfra ``Host`` in production and a narrow recorder in tests; no pyinfra
    object crosses this module's public workflow boundary.
    """

    def __init__(self, host: object, config: EnvironmentConfig) -> None:
        self._host = host
        self._config = config
        self._facts: HostFacts | None = None

    def facts(self) -> HostFacts:
        """Collect one immutable fact snapshot for this connected host."""

        if self._facts is None:
            from .host.facts import collect_host_facts

            self._facts = collect_host_facts(self, self._config)
        return self._facts

    def run(
        self,
        argv: Sequence[str],
        *,
        sudo: bool = False,
        stdin: bytes | None = None,
        sensitive: bool = False,
        timeout: int | None = None,
    ) -> CommandResult:
        """Run a fully quoted argv command without displaying sensitive data.

        pyinfra exposes a shell-command API.  Every argument is therefore a
        ``QuoteString`` and the only shell program is a fixed wrapper used to
        retain the command's numeric exit status.  No caller value is ever
        interpolated into that wrapper.
        """

        arguments = _validate_argv(argv)
        effective_timeout = _timeout(timeout, self._config.connection_timeout)
        input_text = _stdin_text(stdin)
        marker = f"__taskman_remote_status_{secrets.token_hex(16)}__:"
        wrapper = '"$@"; status=$?; printf "\\n%s%s\\n" "$0" "$status"; exit 0'
        command = StringCommand(
            *(
                QuoteString(value)
                for value in ("sh", "-c", wrapper, marker, *arguments)
            )
        )

        try:
            succeeded, output = self._run_shell_command(
                command,
                sudo=sudo,
                stdin=input_text,
                timeout=effective_timeout,
            )
            if not succeeded:
                raise _remote_error("remote command transport failed")
            return _command_result(output, marker, sensitive=sensitive)
        except OpsError:
            raise
        except Exception:
            raise _remote_error("remote command transport failed") from None

    def put(
        self,
        source: Path,
        destination: PurePosixPath,
        *,
        mode: int,
        sensitive: bool = False,
        timeout: int | None = None,
    ) -> None:
        """Install a file through a unique mode-0700 staging directory.

        SFTP cannot promise a restrictive mode while it initially creates a
        file.  Staging below a fresh private directory avoids that exposure;
        the final ``install`` applies the caller's exact safe mode atomically
        at the destination.
        """

        local_source = Path(source)
        if not local_source.is_file():
            raise ValueError("upload source must be a regular file")
        remote_destination = _validated_destination(destination)
        final_mode = _validated_mode(mode, sensitive=sensitive)
        effective_timeout = _timeout(timeout, self._config.connection_timeout)
        stage_directory = _PRIVATE_UPLOAD_ROOT / f"taskman-upload-{uuid4().hex}"
        stage_file = stage_directory / "payload"

        try:
            self.run(
                ("install", "-d", "-m", f"{_PRIVATE_DIRECTORY_MODE:o}", "--", str(stage_directory)),
                timeout=effective_timeout,
            )
            uploaded = self._put_file(local_source, stage_file, timeout=effective_timeout)
            if not uploaded:
                raise _remote_error("private remote upload failed")
            self.run(
                ("chmod", f"{_PRIVATE_UPLOAD_MODE:o}", "--", str(stage_file)),
                timeout=effective_timeout,
                sensitive=sensitive,
            )
            self.run(
                (
                    "install",
                    "-m",
                    f"{final_mode:o}",
                    "--",
                    str(stage_file),
                    str(remote_destination),
                ),
                sudo=True,
                timeout=effective_timeout,
                sensitive=sensitive,
            )
        except OpsError:
            raise
        except Exception:
            raise _remote_error("private remote upload failed") from None
        finally:
            self._remove_private_stage(stage_file, stage_directory, timeout=effective_timeout)

    def converge(self, deploy: Callable[..., object], data: Mapping[str, object]) -> ChangeSet:
        """Run a workflow-supplied convergent unit without exposing pyinfra.

        Later convergence capabilities may return a richer ``ChangeSet``;
        this adapter preserves that result and gives a stable no-change result
        to declarative callbacks that return ``None``.
        """

        try:
            result = deploy(self, data)
        except OpsError:
            raise
        except Exception:
            raise _remote_error("remote convergence failed") from None

        if isinstance(result, ChangeSet):
            return result
        if result is None:
            return ChangeSet(changed=False)
        if isinstance(result, bool):
            return ChangeSet(changed=result)
        raise TypeError("convergent deploy must return ChangeSet, bool, or None")

    def close(self) -> None:
        """Disconnect the private pyinfra connection when a workflow is done."""

        disconnect = getattr(self._host, "disconnect", None)
        if callable(disconnect):
            with suppress(Exception):
                disconnect()

    def _run_shell_command(
        self,
        command: StringCommand,
        *,
        sudo: bool,
        stdin: str | None,
        timeout: int,
    ) -> tuple[bool, object]:
        runner = getattr(self._host, "run_shell_command", None)
        if not callable(runner):
            raise TypeError("invalid pyinfra host")
        return runner(
            command,
            print_output=False,
            print_input=False,
            _sudo=sudo,
            _stdin=stdin,
            _timeout=timeout,
        )

    def _put_file(self, source: Path, destination: PurePosixPath, *, timeout: int) -> bool:
        uploader = getattr(self._host, "put_file", None)
        if not callable(uploader):
            raise TypeError("invalid pyinfra host")
        self._set_upload_timeout(timeout)
        return bool(
            uploader(
                str(source),
                str(destination),
                print_output=False,
                print_input=False,
                _timeout=timeout,
            )
        )

    def _set_upload_timeout(self, timeout: int) -> None:
        """Apply a transfer deadline where pyinfra's SFTP client exposes it."""

        connector = getattr(self._host, "connector", None)
        connection = getattr(connector, "get_file_transfer_connection", None)
        if not callable(connection):
            # Test doubles may implement only the public put-file boundary.
            return
        transfer = connection()
        get_channel = getattr(transfer, "get_channel", None)
        if not callable(get_channel):
            return
        channel = get_channel()
        set_timeout = getattr(channel, "settimeout", None)
        if not callable(set_timeout):
            return
        set_timeout(timeout)

    def _remove_private_stage(
        self,
        stage_file: PurePosixPath,
        stage_directory: PurePosixPath,
        *,
        timeout: int,
    ) -> None:
        # These are generated exact paths, never a caller path or wildcard.
        with suppress(Exception):
            self.run(("rm", "-f", "--", str(stage_file)), sensitive=True, timeout=timeout)
        with suppress(Exception):
            self.run(("rmdir", "--", str(stage_directory)), sensitive=True, timeout=timeout)


def connect(
    config: EnvironmentConfig,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    host_factory: Callable[[EnvironmentConfig, Path], object] | None = None,
    temporary_directory: Path | None = None,
) -> PyinfraRemote:
    """Verify an out-of-band fingerprint before constructing the SSH client.

    The scan is untrusted until ``ssh-keygen`` confirms its fingerprint.  The
    resulting one-key known-hosts file is mode 0600, passed to pyinfra with
    ``StrictHostKeyChecking=yes``, loaded during connection, and unlinked
    immediately afterwards.  ``accept-new`` is never enabled.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("connect requires validated EnvironmentConfig")
    factory = host_factory or _build_pyinfra_host
    known_hosts: Path | None = None
    try:
        lines = _verified_host_key_lines(config, command_runner)
        known_hosts = _write_known_hosts(lines, temporary_directory)
        host = factory(config, known_hosts)
        return PyinfraRemote(host, config)
    except OpsError:
        raise
    except Exception:
        raise _remote_error("strict SSH connection setup failed") from None
    finally:
        if known_hosts is not None:
            with suppress(FileNotFoundError):
                known_hosts.unlink()


def _build_pyinfra_host(config: EnvironmentConfig, known_hosts: Path) -> object:
    data = {
        "ssh_hostname": config.ssh_host,
        "ssh_port": config.ssh_port,
        "ssh_user": config.ssh_user,
        "ssh_config_file": "/dev/null",
        "ssh_known_hosts_file": str(known_hosts),
        "ssh_strict_host_key_checking": "yes",
        "ssh_connect_retries": 0,
    }
    inventory = Inventory(([(config.ssh_host, data)], {}))
    state = State(
        inventory,
        Config(CONNECT_TIMEOUT=config.connection_timeout, PARALLEL=1),
        check_for_changes=False,
    )
    host = inventory.get_host(config.ssh_host)
    host.connect(reason="strict preflight", show_errors=False, raise_exceptions=True)
    return host


def _verified_host_key_lines(
    config: EnvironmentConfig,
    command_runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> tuple[str, ...]:
    scan = _run_local(
        command_runner,
        [
            "ssh-keyscan",
            "-T",
            str(config.connection_timeout),
            "-p",
            str(config.ssh_port),
            config.ssh_host,
        ],
    )
    if scan.returncode != 0:
        raise _remote_error("SSH host-key scan failed")

    expected_host = config.ssh_host if config.ssh_port == 22 else f"[{config.ssh_host}]:{config.ssh_port}"
    verified: list[str] = []
    for raw_line in _text(scan.stdout).splitlines():
        line = raw_line.strip()
        fields = line.split()
        if len(fields) != 3 or fields[0] != expected_host:
            continue
        fingerprint = _host_key_fingerprint(command_runner, line)
        if fingerprint == config.host_key_fingerprint:
            verified.append(line)

    if not verified:
        raise _remote_error("SSH host key does not match the configured fingerprint")
    return tuple(verified)


def _host_key_fingerprint(
    command_runner: Callable[..., subprocess.CompletedProcess[bytes]], line: str
) -> str | None:
    result = _run_local(
        command_runner,
        ["ssh-keygen", "-lf", "-", "-E", "sha256"],
        input=line.encode("utf-8") + b"\n",
    )
    if result.returncode != 0:
        return None
    fields = _text(result.stdout).split()
    return fields[1] if len(fields) >= 2 and fields[1].startswith("SHA256:") else None


def _run_local(
    command_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    argv: list[str],
    **kwargs: object,
) -> subprocess.CompletedProcess[bytes]:
    return command_runner(
        argv,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )


def _write_known_hosts(lines: Sequence[str], directory: Path | None) -> Path:
    temp_directory = None if directory is None else str(directory)
    descriptor, filename = tempfile.mkstemp(prefix="taskman-known-hosts-", dir=temp_directory, text=True)
    path = Path(filename)
    try:
        os.fchmod(descriptor, _PRIVATE_UPLOAD_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        with suppress(FileNotFoundError):
            path.unlink()
        raise
    return path


def _command_result(output: object, marker: str, *, sensitive: bool) -> CommandResult:
    stdout = _text(getattr(output, "stdout", ""))
    stderr = _text(getattr(output, "stderr", ""))
    lines = stdout.splitlines()
    if not lines or not lines[-1].startswith(marker):
        raise _remote_error("remote command did not return a valid status")
    status_text = lines[-1][len(marker) :]
    if not status_text.isdecimal():
        raise _remote_error("remote command did not return a valid status")
    result = CommandResult(returncode=int(status_text), stdout="\n".join(lines[:-1]), stderr=stderr)
    if sensitive:
        return CommandResult(returncode=result.returncode)
    return result


def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)):
        raise TypeError("remote commands must be an argv sequence")
    values = tuple(argv)
    if not values:
        raise ValueError("remote command argv must not be empty")
    if any(not isinstance(value, str) or "\x00" in value for value in values):
        raise ValueError("remote command argv contains an invalid argument")
    return values


def _stdin_text(stdin: bytes | None) -> str | None:
    if stdin is None:
        return None
    if not isinstance(stdin, bytes):
        raise TypeError("remote stdin must be bytes")
    try:
        return stdin.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("remote stdin must be UTF-8") from error


def _timeout(value: int | None, default: int) -> int:
    timeout = default if value is None else value
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("remote timeout must be a positive integer")
    return timeout


def _validated_destination(destination: PurePosixPath) -> PurePosixPath:
    if not isinstance(destination, PurePosixPath):
        raise TypeError("remote destination must be a PurePosixPath")
    text = destination.as_posix()
    if not destination.is_absolute() or "\x00" in text or "\\" in text or any(
        part in {"", ".", ".."} for part in destination.parts[1:]
    ):
        raise ValueError("remote destination is not a safe absolute path")
    return destination


def _validated_mode(mode: int, *, sensitive: bool) -> int:
    if not isinstance(mode, int) or isinstance(mode, bool) or not 0 <= mode <= 0o777:
        raise ValueError("remote file mode must be an octal permission")
    if mode & 0o002:
        raise ValueError("remote file mode must not be world-writable")
    if sensitive and mode != _PRIVATE_UPLOAD_MODE:
        raise ValueError("sensitive remote files must use mode 0600")
    return mode


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _remote_error(message: str) -> OpsError:
    return OpsError(
        status=ExitStatus.REMOTE_PREFLIGHT,
        stage="remote",
        message=message,
        changed=False,
        next_action="check the SSH host, host key, and administrator sudo access before retrying",
    )


__all__ = ["ChangeSet", "CommandResult", "PyinfraRemote", "Remote", "connect"]
