"""Structural protection for reviewed executable-script ownership."""

from __future__ import annotations

import ast

from tests.support.architecture import _executable_string_violations


def test_reviewed_postgresql_script_is_approved_by_its_bounded_owner_not_a_source_line() -> None:
    """Moving the reviewed renderer must not turn it into an accidental scanner failure."""

    tree = ast.parse(
        "def render_postgresql_native_configuration_script():\n"
        "    return "
        + repr("\n".join(["set -eu"] * 12))
        + "\n"
    )

    assert tuple(_executable_string_violations("ops/taskman_ops/services/postgresql.py", tree)) == ()
