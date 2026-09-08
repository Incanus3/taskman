"""Architecture boundaries that prevent controller-path regression."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, (Path(__file__).resolve().parents[1] / "scripts").as_posix())
from check_architecture import _executable_string_violations, _import_violations, check


_REMOVED_HOST_HELPER_MODULES = frozenset(
    {
        "taskman_ops.host_helper.lifecycle",
        "taskman_ops.host_helper.lifecycle_records",
        "taskman_ops.host_helper.runtime",
        "taskman_ops.host_helper.facts",
        "taskman_ops.host_helper.legacy_result",
        "taskman_ops.host_helper.operations.legacy_backup",
    }
)
_REMOVED_TRANSACTION_NAMES = frozenset(
    {
        "LifecycleStore",
        "LifecycleRecords",
        "TransactionRuntime",
        "TransactionStage",
        "OperationRequest",
        "OperationResult",
        "project_result",
        "validate_private_operation_id",
        "_LegacyVerificationResult",
    }
)
_REMOVED_TRANSACTION_FIELDS = frozenset(
    {
        "operation_id",
        "changed_stages",
        "stage_history",
        "stage_histories",
        "residue_paths",
        "recovery_actions",
    }
)


def _imports_removed_transaction_module(relative_path: str, node: ast.Import | ast.ImportFrom) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name in _REMOVED_HOST_HELPER_MODULES for alias in node.names)

    module = node.module or ""
    if node.level:
        parts = relative_path.removesuffix(".py").split("/")
        package = parts[:-1]
        parent = package[: len(package) - node.level + 1]
        module = ".".join((*parent, module)) if module else ".".join(parent)

    imported = (module, *(f"{module}.{alias.name}" for alias in node.names))
    return any(name in _REMOVED_HOST_HELPER_MODULES for name in imported)


def _removed_transaction_concept_violations(
    relative_path: str, tree: ast.AST
) -> tuple[str, ...]:
    """Find only structural remnants of the removed helper transaction model."""

    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if _imports_removed_transaction_module(relative_path, node):
                violations.append(
                    f"{relative_path}:{node.lineno}: imports removed transaction module"
                )
        elif isinstance(node, ast.Name) and node.id in _REMOVED_TRANSACTION_NAMES:
            violations.append(f"{relative_path}:{node.lineno}: uses removed transaction symbol {node.id}")
        elif isinstance(node, ast.Name) and node.id in _REMOVED_TRANSACTION_FIELDS:
            violations.append(f"{relative_path}:{node.lineno}: uses removed transaction field {node.id}")
        elif isinstance(node, ast.arg) and node.arg in _REMOVED_TRANSACTION_FIELDS:
            violations.append(f"{relative_path}:{node.lineno}: uses removed transaction field {node.arg}")
        elif isinstance(node, ast.Attribute) and node.attr in _REMOVED_TRANSACTION_FIELDS:
            violations.append(f"{relative_path}:{node.lineno}: uses removed transaction field {node.attr}")
        elif isinstance(node, ast.keyword) and node.arg in _REMOVED_TRANSACTION_FIELDS:
            violations.append(f"{relative_path}:{node.value.lineno}: uses removed transaction field {node.arg}")
        elif isinstance(node, ast.Constant) and node.value in _REMOVED_TRANSACTION_FIELDS:
            violations.append(f"{relative_path}:{node.lineno}: uses removed transaction field {node.value}")
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _REMOVED_TRANSACTION_NAMES:
            violations.append(f"{relative_path}:{node.lineno}: defines removed transaction symbol {node.name}")
        elif isinstance(node, ast.ClassDef):
            lowered = node.name.lower()
            if (
                any(marker in lowered for marker in ("pending", "provisional", "finalization", "recovery"))
                and any(marker in lowered for marker in ("record", "publication", "action"))
            ):
                violations.append(f"{relative_path}:{node.lineno}: defines removed transaction record {node.name}")

    return tuple(violations)


def test_deleted_transaction_concepts_are_absent_from_production_ast() -> None:
    """Restoring the journal/runtime model would reintroduce deleted policy."""

    package = Path(__file__).resolve().parents[1] / "taskman_ops"
    violations: list[str] = []

    for source in sorted(package.rglob("*.py")):
        relative_path = source.relative_to(package.parent).as_posix()
        module_name = source.relative_to(package).with_suffix("").as_posix().replace("/", ".")
        if module_name in {module.removeprefix("taskman_ops.") for module in _REMOVED_HOST_HELPER_MODULES}:
            violations.append(f"{relative_path}: removed transaction module remains")
            continue
        violations.extend(
            _removed_transaction_concept_violations(
                relative_path, ast.parse(source.read_text(encoding="utf-8"))
            )
        )

    assert violations == []


def test_removed_transaction_guard_rejects_imports_fields_and_record_shapes() -> None:
    tree = ast.parse(
        "from taskman_ops.host_helper.lifecycle import LifecycleStore\n"
        "result.operation_id\n"
        "OperationResult(changed_stages=(), residue_paths=(), recovery_actions=())\n"
        "class PendingRecoveryRecord:\n"
        "    pass\n"
        "from .host_helper import legacy_result\n"
        "def project_result(operation_id):\n"
        "    return {'changed_stages': (), 'residue_paths': (), 'recovery_actions': ()}\n"
    )

    assert set(_removed_transaction_concept_violations("taskman_ops/example.py", tree)) == {
        "taskman_ops/example.py:1: imports removed transaction module",
        "taskman_ops/example.py:2: uses removed transaction field operation_id",
        "taskman_ops/example.py:3: uses removed transaction symbol OperationResult",
        "taskman_ops/example.py:3: uses removed transaction field changed_stages",
        "taskman_ops/example.py:3: uses removed transaction field residue_paths",
        "taskman_ops/example.py:3: uses removed transaction field recovery_actions",
        "taskman_ops/example.py:4: defines removed transaction record PendingRecoveryRecord",
        "taskman_ops/example.py:6: imports removed transaction module",
        "taskman_ops/example.py:7: defines removed transaction symbol project_result",
        "taskman_ops/example.py:7: uses removed transaction field operation_id",
        "taskman_ops/example.py:8: uses removed transaction field changed_stages",
        "taskman_ops/example.py:8: uses removed transaction field residue_paths",
        "taskman_ops/example.py:8: uses removed transaction field recovery_actions",
    }


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

    from check_architecture import _scheduled_asset_violations

    shell = tmp_path / "ops" / "backup" / "taskman-backup"
    shell.parent.mkdir(parents=True)
    shell.write_text("#!/bin/sh\n", encoding="utf-8")
    adapter = tmp_path / "ops" / "taskman_ops" / "scheduled_backup.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(
        "from taskman_ops.host_helper.lifecycle_records import BackupLifecycleRecord\n",
        encoding="utf-8",
    )
    package = tmp_path / "ops" / "taskman_ops" / "helper_package.py"
    package.write_text(
        "_FIXED_BACKUP_MAIN = b''\n"
        "_FIXED_NAMESPACE = b'from .host_helper.lifecycle import LifecycleStore\\n'\n"
        "BACKUP_ARCHIVE_MEMBERS = (\n"
        "    '__main__.py',\n"
        "    'taskman_ops/__init__.py',\n"
        "    'taskman_ops/host_helper/__init__.py',\n"
        "    'taskman_ops/host_helper/backups.py',\n"
        "    'taskman_ops/scheduled_backup.py',\n"
        ")\n",
        encoding="utf-8",
    )
    backup_capability = tmp_path / "ops" / "taskman_ops" / "host_helper" / "backups.py"
    backup_capability.parent.mkdir(parents=True)
    backup_capability.write_text(
        "from taskman_ops.host_helper.lifecycle import LifecycleStore\n",
        encoding="utf-8",
    )
    package_initializer = tmp_path / "ops" / "taskman_ops" / "host_helper" / "__init__.py"
    package_initializer.write_text(
        "from .lifecycle import LifecycleStore\n",
        encoding="utf-8",
    )

    assert set(_scheduled_asset_violations(tmp_path)) == {
        "ops/backup/taskman-backup: legacy scheduled backup shell remains",
        "ops/taskman_ops/helper_package.py:1: imports legacy scheduled backup record module",
        "ops/taskman_ops/host_helper/__init__.py:1: imports legacy scheduled backup record module",
        "ops/taskman_ops/host_helper/backups.py:1: imports legacy scheduled backup record module",
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
