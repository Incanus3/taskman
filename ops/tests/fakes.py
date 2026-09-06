"""Focused test doubles for controller boundaries.

These doubles retain the observable command contract without imitating
pyinfra's implementation.  They are deliberately test-only helpers.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from taskman_ops.remote import CommandResult, UploadReceipt


@dataclass
class FakeCommandOutput:
    """The small output surface consumed by ``PyinfraRemote``."""

    stdout: str = ""
    stderr: str = ""


@dataclass
class RecordingPyinfraHost:
    """Records adapter calls while returning completed wrapped commands."""

    connected: bool = False
    commands: list[tuple[Any, bool, bool, dict[str, Any]]] = field(default_factory=list)
    uploads: list[tuple[str, str, bool, bool, dict[str, Any]]] = field(default_factory=list)
    failures: deque[BaseException] = field(default_factory=deque)

    def connect(self, **_kwargs: Any) -> None:
        self.connected = True

    def run_shell_command(
        self,
        command: Any,
        *,
        print_output: bool,
        print_input: bool,
        **kwargs: Any,
    ) -> tuple[bool, FakeCommandOutput]:
        self.commands.append((command, print_output, print_input, kwargs))
        if self.failures:
            raise self.failures.popleft()

        marker = command.bits[3].obj
        return True, FakeCommandOutput(stdout=f"{marker}0")

    def put_file(
        self,
        source: str,
        destination: str,
        *,
        print_output: bool,
        print_input: bool,
        **kwargs: Any,
    ) -> bool:
        self.uploads.append((source, destination, print_output, print_input, kwargs))
        if self.failures:
            raise self.failures.popleft()
        return True


@dataclass
class ScriptedRemote:
    """A finite ``Remote`` response stream for fact-collection tests."""

    responses: deque[Any]
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = field(default_factory=list)

    @classmethod
    def from_responses(cls, responses: list[Any]) -> ScriptedRemote:
        return cls(deque(responses))

    def run(self, argv: Any, **kwargs: Any) -> Any:
        self.calls.append((tuple(argv), kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected command: {argv!r}")
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return response


@dataclass
class HelperRunnerRemote:
    """A strict response double for the controller's transient-helper boundary.

    It mirrors the small ``Remote`` surface, returns safe metadata by default,
    and lets a test override one exact argv without accepting arbitrary command
    shapes. The real runner and protocol codecs remain under test.
    """

    checksum: str
    helper_result: CommandResult
    administrator_uid: str = "1000"
    administrator_gid: str = "1000"
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = field(default_factory=list)
    uploads: list[tuple[Path, PurePosixPath, dict[str, Any]]] = field(default_factory=list)
    responses: dict[tuple[str, ...], deque[CommandResult | BaseException]] = field(
        default_factory=dict
    )
    put_response: UploadReceipt | BaseException = field(default_factory=UploadReceipt)

    def add_response(self, argv: tuple[str, ...], response: CommandResult | BaseException) -> None:
        self.responses.setdefault(argv, deque()).append(response)

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        command = tuple(argv)
        self.calls.append((command, kwargs))
        queued = self.responses.get(command)
        if queued:
            response = queued.popleft()
            if isinstance(response, BaseException):
                raise response
            return response

        if command == ("id", "-u"):
            return CommandResult(0, f"{self.administrator_uid}\n")
        if command == ("id", "-g"):
            return CommandResult(0, f"{self.administrator_gid}\n")
        if command[:3] == ("stat", "-c", "%u:%g:%a:%F"):
            return CommandResult(0, f"{self._metadata(command[-1])}\n")
        if command[:2] == ("sha256sum", "--"):
            return CommandResult(0, f"{self.checksum}  {command[-1]}\n")
        if command[:3] in {
            ("sudo", "--", "python3"),
            ("sudo", "--preserve-env=SSH_CONNECTION", "--"),
        }:
            return self.helper_result
        return CommandResult(0)

    def put(self, source: Path, destination: PurePosixPath, **kwargs: Any) -> UploadReceipt:
        self.uploads.append((source, destination, kwargs))
        if isinstance(self.put_response, BaseException):
            raise self.put_response
        if not isinstance(self.put_response, UploadReceipt):
            raise AssertionError("helper runner put response must be an upload receipt or exception")
        return self.put_response

    def _metadata(self, path: str) -> str:
        if path.endswith("taskman-host.pyz"):
            if path.startswith("/run/taskman-ops/"):
                return "0:0:500:regular file"
            return f"{self.administrator_uid}:{self.administrator_gid}:600:regular file"
        if path.startswith("/run/taskman-ops"):
            return "0:0:700:directory"
        return f"{self.administrator_uid}:{self.administrator_gid}:700:directory"
