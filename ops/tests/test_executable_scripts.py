"""Structural protection for reviewed executable-script ownership."""

from __future__ import annotations

import ast

from tests.support.architecture import _executable_string_violations


def test_native_configuration_programs_require_host_helper_ownership() -> None:
    """Substantial native programs belong behind the helper boundary."""

    tree = ast.parse(
        "def render_postgresql_native_configuration_script():\n"
        "    return "
        + repr("\n".join(["set -eu"] * 12))
        + "\n"
    )

    assert tuple(_executable_string_violations("ops/taskman_ops/services/postgresql.py", tree)) == (
        "ops/taskman_ops/services/postgresql.py:2: substantial executable controller string belongs in a helper or declarative operation",
    )
