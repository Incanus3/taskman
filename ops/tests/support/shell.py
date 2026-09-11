"""Executable POSIX shell scripts for command-level tests."""

from pathlib import Path


def write_shell_script(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
