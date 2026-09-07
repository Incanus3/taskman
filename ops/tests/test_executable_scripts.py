"""Structural protection for reviewed executable-script ownership."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


sys.path.insert(0, (Path(__file__).resolve().parents[1] / "scripts").as_posix())
from check_architecture import _executable_string_violations


def test_reviewed_firewall_script_is_approved_by_its_bounded_owner_not_a_source_line() -> None:
    """Moving the reviewed renderer must not turn it into an accidental scanner failure."""

    tree = ast.parse(
        "def render_firewall_convergence_script():\n"
        "    return "
        + repr("\n".join(["set -eu"] * 12))
        + "\n"
    )

    assert tuple(_executable_string_violations("ops/taskman_ops/host/firewall.py", tree)) == ()
