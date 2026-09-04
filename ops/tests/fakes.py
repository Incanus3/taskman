"""Focused test doubles for controller boundaries.

These doubles retain the observable command contract without imitating
pyinfra's implementation.  They are deliberately test-only helpers.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


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
