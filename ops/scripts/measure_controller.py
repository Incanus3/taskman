"""Measure the tracked Python surface of the dedicated-host controller.

The measurement intentionally uses Git's tracked-file list instead of walking
the working tree.  Caches, virtual environments, generated files, and this
script itself therefore cannot silently change the baseline.  Paths are
reported relative to the repository argument, while an ``ops/`` prefix is
handled transparently when the script is run from the operations directory.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Sequence
import json
from pathlib import Path
import subprocess
import sys


_AREAS = ("controller", "protocol", "helper", "workflow", "release", "host", "service")
_EXECUTABLE_MARKERS = (
    "#!/bin/",
    "#!/usr/bin/env",
    "set -e",
    "set -u",
    "safe_",
    "apt-get ",
    "systemctl ",
    "journalctl ",
    "python3 -",
    "psql ",
    "pg_",
    "ufw ",
    "sudo ",
    "printf ",
    "install ",
    "mktemp ",
    "readlink ",
    "trap ",
    "case ",
    "exec ",
    "test ",
    "changed=",
    "LC_ALL=",
    "BEGIN;",
    "CREATE ",
    "ALTER ",
    "DROP ",
    "OnCalendar=",
)
_COMMENT_PREFIXES = ("#", "--")
_OPS_DIRECTORY = "ops"


def executable_string_lines(source: str) -> int:
    """Count non-documentation lines in multiline executable string literals.

    Python docstrings, blank lines, and comment-only lines are excluded.  A
    string is considered executable when it contains one of the fixed command
    or protocol markers used by the controller's shell/Python host programs.
    This conservative lexical check keeps human-facing templates and prose
    out of the metric without importing or executing source files.
    """

    if not isinstance(source, str):
        raise TypeError("source must be a string")

    tree = ast.parse(source)
    docstring_ids = _docstring_ids(tree)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if "\n" not in value or id(node) in docstring_ids or not _looks_executable(value):
            continue
        count += sum(
            bool(line.strip()) and not line.lstrip().startswith(_COMMENT_PREFIXES)
            for line in value.splitlines()
        )
    return count


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    containers = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, containers) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _looks_executable(value: str) -> bool:
    return any(marker in value for marker in _EXECUTABLE_MARKERS)


def _tracked_python_files(repository: Path) -> tuple[Path, ...]:
    """Return tracked controller/test Python files in deterministic order."""

    completed = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "-z", "--", "*.py"],
        check=True,
        capture_output=True,
    )
    names = tuple(
        name
        for name in completed.stdout.decode("utf-8").split("\0")
        if name.endswith(".py")
    )
    files: list[Path] = []
    for name in names:
        relative = Path(name)
        logical = _logical_path(relative)
        if not _is_measurement_scope(logical):
            continue
        path = repository / relative
        if path.is_file() and not path.is_symlink():
            files.append(relative)
    return tuple(sorted(files, key=lambda path: path.as_posix()))


def _logical_path(relative: Path) -> Path:
    parts = relative.parts
    if parts and parts[0] == _OPS_DIRECTORY:
        return Path(*parts[1:])
    return relative


def _is_measurement_scope(logical: Path) -> bool:
    return bool(logical.parts) and logical.parts[0] in {"taskman_ops", "tests"}


def _area(logical: Path) -> str:
    if logical.parts and logical.parts[0] == "taskman_ops":
        if len(logical.parts) > 1:
            return {
                "host_protocol": "protocol",
                "host_helper": "helper",
                "workflows": "workflow",
                "releases": "release",
                "host": "host",
                "services": "service",
            }.get(logical.parts[1], "controller")
        return "controller"
    return "controller"


def _physical_lines(source: str) -> int:
    return len(source.splitlines())


def measure(repository: Path) -> dict[str, object]:
    """Measure tracked production/test Python files below ``repository``."""

    if not isinstance(repository, Path):
        raise TypeError("repository must be a pathlib.Path")
    repository = repository.resolve()
    if not repository.is_dir():
        raise ValueError("repository must be a directory")

    files = _tracked_python_files(repository)
    production: list[tuple[Path, int, str]] = []
    tests: list[tuple[Path, int, str]] = []
    areas = {name: 0 for name in _AREAS}
    executable_lines = 0

    for relative in files:
        source = (repository / relative).read_text(encoding="utf-8")
        lines = _physical_lines(source)
        logical = _logical_path(relative)
        label = relative.as_posix()
        if logical.parts and logical.parts[0] == "taskman_ops":
            production.append((relative, lines, label))
            areas[_area(logical)] += lines
            executable_lines += executable_string_lines(source)
        elif logical.parts and logical.parts[0] == "tests":
            tests.append((relative, lines, label))

    production.sort(key=lambda entry: (-entry[1], entry[2]))
    largest_modules = [
        {"path": label, "lines": lines}
        for _relative, lines, label in production[:10]
    ]
    production_lines = sum(lines for _relative, lines, _label in production)
    test_lines = sum(lines for _relative, lines, _label in tests)
    verification_lines = sum(
        lines
        for relative, lines, _label in production
        if _logical_path(relative).parts == ("taskman_ops", "verification.py")
    )

    # ``controller`` is the residual foundation area, while these two
    # roll-up values mirror the baseline categories in the approved design.
    rollups: dict[str, int] = {
        "workflow_orchestration": areas["workflow"],
        "release_state_and_primitives": areas["release"],
        "host_discovery_and_service_convergence": areas["host"] + areas["service"],
        "controller_foundation": areas["controller"],
        "installation_verification": verification_lines,
    }

    return {
        "areas": areas,
        "executable_string_lines": executable_lines,
        "largest_modules": largest_modules,
        "production_lines": production_lines,
        "test_lines": test_lines,
        "tracked_python_files": len(files),
        "rollups": rollups,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Render stable, sorted JSON metrics and return a process status."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repository",
        nargs="?",
        type=Path,
        default=Path("."),
        help="repository root containing the tracked controller files (default: current directory)",
    )
    args = parser.parse_args(argv)
    try:
        result = measure(args.repository)
    except (OSError, UnicodeError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"measurement failed: {error}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI command
    raise SystemExit(main())
