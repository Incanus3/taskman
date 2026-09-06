"""Architecture boundaries that prevent controller-path regression."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, (Path(__file__).resolve().parents[1] / "scripts").as_posix())
from check_architecture import _executable_string_violations, _import_violations


def test_architecture_scan_accepts_only_the_supported_deployment_boundaries() -> None:
    repository = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, (repository / "ops" / "scripts" / "check_architecture.py").as_posix(), repository.as_posix()],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_architecture_scan_rejects_a_custom_pyinfra_utility_outside_reviewed_boundaries() -> None:
    tree = ast.parse("from ..pyinfra import conditional_convergence\n")

    assert (
        "ops/taskman_ops/workflows/unapproved.py: custom pyinfra operation is outside its justified consumers"
        in tuple(_import_violations("ops/taskman_ops/workflows/unapproved.py", tree))
    )


def test_architecture_scan_rejects_a_substantial_controller_program_outside_reviewed_paths() -> None:
    tree = ast.parse("PROGRAM = " + repr("\n".join(["systemctl status taskman.service"] * 12)))

    assert tuple(_executable_string_violations("ops/taskman_ops/services/unapproved.py", tree)) == (
        "ops/taskman_ops/services/unapproved.py:1: substantial executable controller string belongs in a helper or declarative operation",
    )


@pytest.mark.parametrize(
    "source",
    (
        "from taskman_ops.releases import remote_snapshot\n",
        "from ..releases import remote_snapshot\n",
    ),
)
def test_architecture_scan_rejects_superseded_module_import_aliases(source: str) -> None:
    tree = ast.parse(source)

    assert tuple(_import_violations("ops/taskman_ops/workflows/unapproved.py", tree)) == (
        "ops/taskman_ops/workflows/unapproved.py:1: imports a superseded deployment module",
    )
