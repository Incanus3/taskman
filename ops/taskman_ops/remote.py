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
import queue
import select
import subprocess
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from pyinfra.api import Config, Inventory, State
from pyinfra.api.command import QuoteString, StringCommand
from pyinfra.api.deploy import add_deploy
from pyinfra.api.operations import run_ops

from .config import EnvironmentConfig
from .errors import ExitStatus, OpsError

if TYPE_CHECKING:
    from .host.facts import HostFacts


_PRIVATE_UPLOAD_ROOT = PurePosixPath("/tmp")
_PRIVATE_UPLOAD_MODE = 0o600
_PRIVATE_DIRECTORY_MODE = 0o700
_UPLOAD_STDOUT_LIMIT = 1024
_UPLOAD_STDERR_LIMIT = 4096
_DEFAULT_STDOUT_LIMIT = 16 * 1024
_DEFAULT_STDERR_LIMIT = 16 * 1024
_CHANNEL_READ_CHUNK_BYTES = 64 * 1024
_SETUP_CANCEL_GRACE_SECONDS = 0.2


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
class UploadReceipt:
    """A private upload completed, with at most a generic cleanup warning."""

    cleanup_warning: bool = False

    def __post_init__(self) -> None:
        if type(self.cleanup_warning) is not bool:
            raise ValueError("upload receipt cleanup warning must be boolean")


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
        stdout_limit: int | None = None,
        stderr_limit: int | None = None,
    ) -> CommandResult: ...

    def put(
        self,
        source: Path,
        destination: PurePosixPath,
        *,
        mode: int,
        sensitive: bool = False,
        timeout: int | None = None,
        sudo: bool = True,
    ) -> UploadReceipt: ...


def run_interactive(
    config: EnvironmentConfig,
    argv: Sequence[str],
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    temporary_directory: Path | None = None,
) -> int:
    """Run one fixed remote argv with the caller's terminal attached.

    The known-hosts file is rebuilt from the configured out-of-band
    fingerprint for this separate SSH process.  Standard streams and the
    process environment are deliberately not supplied to ``subprocess.run``:
    OpenSSH inherits the real terminal, so interactive release input never
    crosses a controller data structure.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("interactive SSH requires validated EnvironmentConfig")
    arguments = _validate_argv(argv)
    known_hosts: Path | None = None
    try:
        lines = _verified_host_key_lines(config, command_runner)
        known_hosts = _write_known_hosts(lines, temporary_directory)
        command = [
            "ssh",
            "-tt",
            "-F",
            "/dev/null",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            f"ConnectTimeout={config.connection_timeout}",
            "-p",
            str(config.ssh_port),
            "-l",
            config.ssh_user,
            "--",
            config.ssh_host,
            *arguments,
        ]
        completed = command_runner(command, check=False)
        return int(completed.returncode)
    except OpsError:
        raise
    except (OSError, ValueError, TypeError):
        raise _remote_error("interactive SSH command failed") from None
    finally:
        if known_hosts is not None:
            with suppress(FileNotFoundError):
                known_hosts.unlink()


class PyinfraRemote:
    """A strict, one-host adapter around pyinfra's SSH connector.

    ``host`` is intentionally an untyped implementation detail.  It is a
    pyinfra ``Host`` in production and a narrow recorder in tests; no pyinfra
    object crosses this module's public workflow boundary.
    """

    def __init__(
        self,
        host: object,
        config: EnvironmentConfig,
        *,
        inventory: Inventory | None = None,
        state: State | None = None,
    ) -> None:
        self._host = host
        self._config = config
        self._inventory = inventory or getattr(host, "inventory", None)
        self._state = state or getattr(host, "state", None)
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
        stdout_limit: int | None = None,
        stderr_limit: int | None = None,
    ) -> CommandResult:
        """Run a fully quoted argv command without displaying sensitive data.

        pyinfra exposes a shell-command API. Every argument is therefore a
        ``QuoteString``. Every call uses the already-authenticated channel so
        stdout and stderr have finite defaults and the numeric exit status is
        available without a shell status wrapper. No caller value is
        interpolated into a shell program.
        """

        arguments = _validate_argv(argv)
        effective_timeout = _timeout(timeout, self._config.connection_timeout)
        maximum_stdout = _output_limit(stdout_limit, default=_DEFAULT_STDOUT_LIMIT)
        maximum_stderr = _output_limit(stderr_limit, default=_DEFAULT_STDERR_LIMIT)
        input_text = _stdin_text(stdin)
        try:
            return self._run_bounded_command(
                arguments,
                sudo=sudo,
                stdin=input_text,
                sensitive=sensitive,
                timeout=effective_timeout,
                stdout_limit=maximum_stdout,
                stderr_limit=maximum_stderr,
            )
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
        sudo: bool = True,
    ) -> UploadReceipt:
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
        if not isinstance(sudo, bool):
            raise TypeError("remote upload sudo must be a boolean")
        effective_timeout = _timeout(timeout, self._config.connection_timeout)
        stage_directory = _PRIVATE_UPLOAD_ROOT / f"taskman-upload-{uuid4().hex}"
        stage_file = stage_directory / "payload"

        cleanup_warning = False
        try:
            try:
                created = self.run(
                    (
                        "install",
                        "-d",
                        "-m",
                        f"{_PRIVATE_DIRECTORY_MODE:o}",
                        "--",
                        str(stage_directory),
                    ),
                    sudo=False,
                    timeout=effective_timeout,
                    sensitive=True,
                    stdout_limit=_UPLOAD_STDOUT_LIMIT,
                    stderr_limit=_UPLOAD_STDERR_LIMIT,
                )
                if not created.succeeded:
                    raise _remote_error("private remote upload failed")
                uploaded = self._put_file(local_source, stage_file, timeout=effective_timeout)
                if not uploaded:
                    raise _remote_error("private remote upload failed")
                self._require_upload_success(
                    ("chmod", f"{_PRIVATE_UPLOAD_MODE:o}", "--", str(stage_file)),
                    timeout=effective_timeout,
                    sensitive=sensitive,
                )
                self._require_upload_success(
                    (
                        "install",
                        "-m",
                        f"{final_mode:o}",
                        "--",
                        str(stage_file),
                        str(remote_destination),
                    ),
                    sudo=sudo,
                    timeout=effective_timeout,
                    sensitive=sensitive,
                )
            finally:
                cleanup_warning = self._remove_private_stage(
                    stage_file,
                    stage_directory,
                    timeout=effective_timeout,
                )
        except OpsError as error:
            if cleanup_warning:
                error.warnings = ("transient upload cleanup was incomplete",)  # type: ignore[attr-defined]
            raise
        except Exception:
            error = _remote_error("private remote upload failed")
            if cleanup_warning:
                error.warnings = ("transient upload cleanup was incomplete",)  # type: ignore[attr-defined]
            raise error from None
        return UploadReceipt(cleanup_warning=cleanup_warning)

    def run_deploy(self, deploy: Callable[..., object], *args: object, **kwargs: object) -> ChangeSet:
        """Add, execute, and summarize the one connected pyinfra deploy.

        The inventory, state, and host all come from the already strict
        pinned-host-key connection created by :func:`connect`. Results are
        read from pyinfra's completed operation state; human console output is
        never an input to Taskman's change reporting.
        """

        if not callable(deploy):
            raise TypeError("pyinfra deploy must be callable")
        if not isinstance(self._inventory, Inventory) or not isinstance(self._state, State):
            raise TypeError("connected pyinfra inventory and state are unavailable")
        if self._state.is_executing:
            raise RuntimeError("connected pyinfra state has already executed a deploy")
        try:
            add_deploy(self._state, deploy, *args, **kwargs)
            run_ops(self._state)
            return summarize_deploy(self._state)
        except OpsError:
            raise
        except Exception:
            categorized_error = getattr(self._state, "_taskman_categorized_error", None)
            if isinstance(categorized_error, OpsError):
                raise categorized_error
            raise _remote_error("programmatic pyinfra deployment failed") from None

    def close(self) -> None:
        """Disconnect the private pyinfra connection when a workflow is done."""

        disconnect = getattr(self._host, "disconnect", None)
        if callable(disconnect):
            with suppress(Exception):
                disconnect()

    def _run_bounded_command(
        self,
        argv: tuple[str, ...],
        *,
        sudo: bool,
        stdin: str | None,
        sensitive: bool,
        timeout: int,
        stdout_limit: int | None,
        stderr_limit: int | None,
    ) -> CommandResult:
        """Use the authenticated SSH channel only where pyinfra cannot cap reads.

        pyinfra 3.10 exposes only completed ``CommandOutput`` values from
        ``Host.run_shell_command``.  This narrow adapter retains pyinfra for
        normal command and deployment execution, but drains Paramiko's two
        already-authenticated channel streams with byte caps before either
        stream is materialized as text.
        """

        adapter = _BoundedSSHChannelAdapter(self._host, self._state)
        return adapter.run(
            argv,
            sudo=sudo,
            stdin=stdin,
            sensitive=sensitive,
            timeout=timeout,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
        )

    def _require_upload_success(
        self,
        argv: Sequence[str],
        *,
        sudo: bool = False,
        sensitive: bool,
        timeout: int,
    ) -> None:
        result = self.run(
            argv,
            sudo=sudo,
            sensitive=sensitive,
            timeout=timeout,
            stdout_limit=_UPLOAD_STDOUT_LIMIT,
            stderr_limit=_UPLOAD_STDERR_LIMIT,
        )
        if not result.succeeded:
            raise _remote_error("private remote upload failed")

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
    ) -> bool:
        """Return whether the generated private stage could not be removed."""

        file_cleanup_uncertain = False
        try:
            self._require_upload_success(
                ("rm", "-f", "--", str(stage_file)),
                sensitive=True,
                timeout=timeout,
            )
        except Exception:
            file_cleanup_uncertain = True

        try:
            self._require_upload_success(
                ("rmdir", "--", str(stage_directory)),
                sensitive=True,
                timeout=timeout,
            )
        except Exception:
            return True
        return file_cleanup_uncertain


class _BoundedSSHChannelAdapter:
    """Private pyinfra 3.10 seam for bounded reads from an SSH channel.

    ``Host.run_shell_command`` intentionally returns only a completed
    ``CommandOutput``.  The SSH connector retains its authenticated Paramiko
    client, so bounded controller commands can use that one connection without
    constructing another SSH transport or changing pyinfra deployment paths.
    Keep all private connector access in this adapter for the next pyinfra
    compatibility review.
    """

    def __init__(self, host: object, state: object | None) -> None:
        self._host = host
        self._state = state

    def run(
        self,
        argv: tuple[str, ...],
        *,
        sudo: bool,
        stdin: str | None,
        sensitive: bool,
        timeout: int,
        stdout_limit: int | None,
        stderr_limit: int | None,
    ) -> CommandResult:
        from pyinfra.connectors.util import make_unix_command_for_host

        connector = getattr(self._host, "connector", None)
        client = getattr(connector, "client", None)
        execute = getattr(client, "exec_command", None)
        if self._state is None or not callable(execute):
            raise TypeError("bounded SSH channel is unavailable")

        command = StringCommand(*(QuoteString(value) for value in argv))
        rendered = make_unix_command_for_host(
            self._state,
            self._host,
            command,
            _sudo=sudo,
        ).get_raw_value()
        deadline = time.monotonic() + timeout
        channel = _open_bounded_channel(
            connector,
            client,
            execute,
            rendered,
            stdin=stdin,
            timeout=timeout,
            deadline=deadline,
        )

        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _close_channel(channel)
                _close_ssh_connection(connector, client)
                raise _remote_error("remote command transport failed")
            stdout, stderr, returncode = _read_bounded_channel(
                channel,
                timeout=remaining,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
            )
            result = CommandResult(
                returncode=returncode,
                stdout=_channel_text(stdout),
                stderr=_channel_text(stderr),
            )
            if sensitive:
                return CommandResult(returncode=result.returncode)
            return result
        except OpsError:
            _close_channel(channel)
            _close_ssh_connection(connector, client)
            raise
        except Exception:
            _close_channel(channel)
            _close_ssh_connection(connector, client)
            raise _remote_error("remote command transport failed") from None


def _open_bounded_channel(
    connector: object,
    client: object,
    execute: Callable[..., object],
    command: str,
    *,
    stdin: str | None,
    timeout: int,
    deadline: float,
) -> object:
    """Bound synchronous Paramiko command and stdin setup independently."""

    results: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
    cancelled = threading.Event()

    def setup() -> None:
        try:
            value = execute(command, get_pty=False, timeout=timeout)
            if not isinstance(value, tuple) or len(value) != 3:
                raise TypeError("bounded SSH channel is unavailable")
            stdin_buffer, stdout_buffer, _stderr_buffer = value
            channel = getattr(stdout_buffer, "channel", None)
            if channel is None:
                raise TypeError("bounded SSH channel is unavailable")
            if cancelled.is_set():
                _close_channel(channel)
                return
            _write_channel_stdin(stdin, stdin_buffer)
            if cancelled.is_set():
                _close_channel(channel)
                return
            results.put((True, channel))
        except Exception as error:
            if not cancelled.is_set():
                results.put((False, error))

    worker = threading.Thread(
        target=setup,
        name="taskman-bounded-ssh-setup",
        daemon=True,
    )
    worker.start()
    remaining = deadline - time.monotonic()
    try:
        succeeded, value = results.get(timeout=max(0.0, remaining))
    except queue.Empty:
        cancelled.set()
        _close_ssh_connection(connector, client)
        worker.join(_SETUP_CANCEL_GRACE_SECONDS)
        raise _remote_error("remote command transport failed") from None
    if not succeeded:
        _close_ssh_connection(connector, client)
        if isinstance(value, Exception):
            raise value
        raise TypeError("bounded SSH channel is unavailable")
    return value


def _write_channel_stdin(stdin: str | None, buffer: object) -> None:
    write = getattr(buffer, "write", None)
    close = getattr(buffer, "close", None)
    if not callable(write) or not callable(close):
        raise TypeError("bounded SSH stdin is unavailable")
    if stdin:
        write(stdin.encode("utf-8"))
    close()


def _read_bounded_channel(
    channel: object,
    *,
    timeout: float,
    stdout_limit: int | None,
    stderr_limit: int | None,
) -> tuple[bytes, bytes, int]:
    """Drain stdout and stderr fairly, retaining at most each limit plus one."""
    recv_ready = getattr(channel, "recv_ready", None)
    recv_stderr_ready = getattr(channel, "recv_stderr_ready", None)
    recv = getattr(channel, "recv", None)
    recv_stderr = getattr(channel, "recv_stderr", None)
    exit_status_ready = getattr(channel, "exit_status_ready", None)
    recv_exit_status = getattr(channel, "recv_exit_status", None)
    channel_methods = (
        recv_ready,
        recv_stderr_ready,
        recv,
        recv_stderr,
        exit_status_ready,
        recv_exit_status,
    )
    if not all(callable(value) for value in channel_methods):
        raise TypeError("bounded SSH channel is unavailable")

    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout

    while True:
        made_progress = False
        if recv_ready():
            _append_channel_output(stdout, recv(_channel_read_size(stdout, stdout_limit)), stdout_limit, channel)
            made_progress = True
        if recv_stderr_ready():
            _append_channel_output(
                stderr,
                recv_stderr(_channel_read_size(stderr, stderr_limit)),
                stderr_limit,
                channel,
            )
            made_progress = True

        if exit_status_ready() and not recv_ready() and not recv_stderr_ready():
            return bytes(stdout), bytes(stderr), int(recv_exit_status())

        if made_progress:
            continue

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _close_channel(channel)
            raise _remote_error("remote command transport failed")
        try:
            select.select([channel], [], [], remaining)
        except Exception:
            _close_channel(channel)
            raise _remote_error("remote command transport failed") from None


def _channel_read_size(output: bytearray, limit: int | None) -> int:
    if limit is None:
        return _CHANNEL_READ_CHUNK_BYTES
    return min(_CHANNEL_READ_CHUNK_BYTES, limit - len(output) + 1)


def _append_channel_output(output: bytearray, chunk: object, limit: int | None, channel: object) -> None:
    if not isinstance(chunk, bytes) or not chunk:
        raise _remote_error("remote command transport failed")
    output.extend(chunk)
    if limit is not None and len(output) > limit:
        _close_channel(channel)
        raise _remote_error("remote command output exceeded its bound")


def _channel_text(value: bytes) -> str:
    try:
        return value.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise _remote_error("remote command transport failed") from None


def _close_channel(channel: object) -> None:
    close = getattr(channel, "close", None)
    if callable(close):
        with suppress(Exception):
            close()


def _close_ssh_connection(connector: object, client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        with suppress(Exception):
            close()
    disconnect = getattr(connector, "disconnect", None)
    if callable(disconnect):
        with suppress(Exception):
            disconnect()


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
    # Programmatic pyinfra callers own activation explicitly. The Host.connect
    # API establishes transport but does not add the host to State.active_hosts.
    state.activate_host(host)
    return host


def summarize_deploy(state: State) -> ChangeSet:
    """Return completed pyinfra operation results for Taskman's one host.

    ``OperationMeta.did_change`` is the public pyinfra operation-result API.
    It records what actually executed, unlike prepare-time estimates. The
    state host results are also inspected so an incomplete/failed execution is
    never rendered as successful convergence.
    """

    if not isinstance(state, State):
        raise TypeError("pyinfra deployment summary requires State")
    categorized_error = getattr(state, "_taskman_categorized_error", None)
    if isinstance(categorized_error, OpsError):
        raise categorized_error
    operations: list[str] = []
    for host in state.inventory:
        result = state.get_results_for_host(host)
        if result.error_ops or result.partial_ops:
            raise _remote_error("programmatic pyinfra deployment failed")
        for op_hash in host.op_hash_order:
            operation_data = state.get_op_data_for_host(host, op_hash)
            operation_result = operation_data.operation_meta
            if not operation_result.is_complete():
                raise _remote_error("programmatic pyinfra deployment produced incomplete results")
            if not operation_result.did_change():
                continue
            for name in sorted(state.get_op_meta(op_hash).names):
                operations.append(name)
    return ChangeSet(changed=bool(operations), operations=tuple(dict.fromkeys(operations)))


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


def _output_limit(value: int | None, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("remote output limit must be a non-negative integer")
    return value


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


__all__ = [
    "ChangeSet",
    "CommandResult",
    "PyinfraRemote",
    "Remote",
    "connect",
    "run_interactive",
    "summarize_deploy",
]
