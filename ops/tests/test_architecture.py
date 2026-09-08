"""Architecture boundaries that prevent controller-path regression."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, (Path(__file__).resolve().parents[1] / "scripts").as_posix())
from check_architecture import _executable_string_violations, _import_violations, check


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


def test_architecture_scan_rejects_a_direct_pyinfra_operation_outside_the_three_approved_actions() -> None:
    """A new custom decorator must not bypass the bounded-operation inventory."""

    tree = ast.parse(
        "from pyinfra.api import operation\n"
        "@operation(\n"
        "    is_idempotent=True,\n"
        ")\n"
        "def unapproved():\n"
        "    yield 'true'\n"
    )

    assert tuple(_import_violations("ops/taskman_ops/workflows/unapproved.py", tree)) == (
        "ops/taskman_ops/workflows/unapproved.py:2: direct pyinfra operation is outside approved actions",
    )


def test_architecture_scan_rejects_an_aliased_direct_operation_in_an_approved_module() -> None:
    """Aliasing the decorator must not add an action beside the approved owner."""

    tree = ast.parse(
        "from pyinfra.api import operation\n"
        "custom_operation = operation\n"
        "@custom_operation(is_idempotent=True)\n"
        "def unapproved():\n"
        "    yield 'true'\n"
    )

    assert tuple(_import_violations("ops/taskman_ops/host/firewall.py", tree)) == (
        "ops/taskman_ops/host/firewall.py:3: direct pyinfra operation is outside approved actions",
    )


def test_architecture_scan_rejects_the_removed_generic_pyinfra_module(tmp_path: Path) -> None:
    """Recreating the deleted policy engine must be a structural failure."""

    module = tmp_path / "ops" / "taskman_ops" / "pyinfra.py"
    module.parent.mkdir(parents=True)
    module.write_text("pass\n", encoding="utf-8")
    (tmp_path / "ops" / "backup").mkdir()

    assert "taskman_ops/pyinfra.py: superseded production path remains" in check(tmp_path)


def test_scheduled_backup_guard_rejects_the_shell_and_legacy_record_imports(tmp_path: Path) -> None:
    """The installed zipapp must not regain the superseded lifecycle protocol."""

    shell = tmp_path / "ops" / "backup" / "taskman-backup"
    shell.parent.mkdir(parents=True)
    shell.write_text("#!/bin/sh\n", encoding="utf-8")
    adapter = tmp_path / "ops" / "taskman_ops" / "scheduled_backup.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(
        "from taskman_ops.host_helper.lifecycle_records import BackupLifecycleRecord\n",
        encoding="utf-8",
    )

    assert set(_scheduled_asset_violations(tmp_path)) == {
        "ops/backup/taskman-backup: legacy scheduled backup shell remains",
        "ops/taskman_ops/scheduled_backup.py:1: imports legacy scheduled backup record module",
    }


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
