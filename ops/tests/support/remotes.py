"""Focused test doubles for controller boundaries.

These doubles retain the observable command contract without imitating
pyinfra's implementation.  They are deliberately test-only helpers.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import subprocess
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


class LocalProtectedRemote:
    """Execute protected file commands locally while suppressing their output."""

    def __init__(self, destination: Path, workspace: Path, *, failure: str | None = None) -> None:
        self.destination = destination
        self.failure = failure
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self.results: list[tuple[int, bytes, bytes]] = []
        self._bin = workspace / "protected-command-bin"
        self._bin.mkdir(parents=True)
        self._write_install_wrapper()
        self._write_mktemp_wrapper()
        self._write_rm_wrapper()
        self._write_stat_wrapper()

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        command = tuple(argv[:-1]) + (self.destination.as_posix(),)
        self.calls.append((command, kwargs))
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            input=kwargs.get("stdin"),
            env={
                **os.environ,
                "PATH": f"{self._bin}:{os.environ['PATH']}",
                "TASKMAN_PROTECTED_STAGE_ROOT": str(self._bin.parent),
                "TASKMAN_PROTECTED_FAIL_CLEANUP": "1" if self.failure == "cleanup" else "",
                "TASKMAN_PROTECTED_SEND_TERM": "1" if self.failure == "signal" else "",
            },
        )
        self.results.append((completed.returncode, completed.stdout, completed.stderr))
        return CommandResult(completed.returncode)

    def _write_install_wrapper(self) -> None:
        path = self._bin / "install"
        path.write_text(
            "#!/bin/sh\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    -o|-g) shift 2 ;;\n"
            "    *) break ;;\n"
            "  esac\n"
            "done\n"
            "/usr/bin/install \"$@\"\n"
            "status=$?\n"
            "if [ \"$status\" -eq 0 ] && [ \"${TASKMAN_PROTECTED_SEND_TERM:-}\" = 1 ]; then\n"
            "  kill -TERM \"$PPID\"\n"
            "fi\n"
            "exit \"$status\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_rm_wrapper(self) -> None:
        path = self._bin / "rm"
        path.write_text(
            "#!/bin/sh\n"
            "if [ \"${TASKMAN_PROTECTED_FAIL_CLEANUP:-}\" = 1 ]; then exit 1; fi\n"
            "exec /usr/bin/rm \"$@\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_mktemp_wrapper(self) -> None:
        path = self._bin / "mktemp"
        path.write_text(
            "#!/bin/sh\n"
            "case \"${1:-}\" in\n"
            "  /tmp/taskman-pgpass.XXXXXX|/tmp/taskman-environment.XXXXXX)\n"
            "    exec /usr/bin/mktemp \"$TASKMAN_PROTECTED_STAGE_ROOT/${1##*/}\"\n"
            "    ;;\n"
            "  *) exec /usr/bin/mktemp \"$@\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_stat_wrapper(self) -> None:
        path = self._bin / "stat"
        path.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *--format=%U:%G:%a*)\n"
            "    target=\"\"\n"
            "    for argument in \"$@\"; do target=\"$argument\"; done\n"
            "    mode=$(/usr/bin/stat --format=%a -- \"$target\") || exit $?\n"
            "    printf 'root:root:%s\\n' \"$mode\"\n"
            "    ;;\n"
            "  *) exec /usr/bin/stat \"$@\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(0o755)


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
