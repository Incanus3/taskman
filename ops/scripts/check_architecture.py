"""Check the one-controller deployment architecture without importing it."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Sequence
from pathlib import Path
import re
import sys


_EXECUTABLE_MARKERS = (
    "apt-get ",
    "systemctl ",
    "pg_",
    "psql ",
    "sudo ",
    "set -e",
    "ufw ",
)
# These are the reviewed controller-side executable-program owners. They are
# matched by stable owner rather than source line so ordinary maintenance cannot
# accidentally invalidate the architecture scan. The transient helper is
# intentionally excluded because it owns stateful host programs rather than
# controller orchestration.
_REVIEWED_EXECUTABLE_OWNERS = frozenset(
    {
        ("taskman_ops/host/facts.py", "_CADDY_EVIDENCE_SCRIPT"),
        ("taskman_ops/host/facts.py", "_RUNTIME_PREFLIGHT"),
        ("taskman_ops/host/facts.py", "_DATABASE_PREFLIGHT"),
        ("taskman_ops/host/firewall.py", "render_firewall_convergence_script"),
        ("taskman_ops/host/firewall.py", "_render_firewall_change_probe"),
        ("taskman_ops/services/postgresql.py", "_render_postgresql_native_configuration_probe"),
        ("taskman_ops/services/postgresql.py", "render_postgresql_native_configuration_script"),
    }
)
_FORBIDDEN_MODULES = (
    "releases.activation",
    "releases.adoption",
    "releases.cleanup",
    "releases.locking",
    "releases.records",
    "releases.remote_adoption",
    "releases.remote_locking",
    "releases.remote_snapshot",
    "releases.staging",
    "taskman_ops.verification",
    "workflows.deploy_transaction",
)
_REMOVED_PATHS = (
    "taskman_ops/releases/activation.py",
    "taskman_ops/releases/adoption.py",
    "taskman_ops/releases/cleanup.py",
    "taskman_ops/releases/locking.py",
    "taskman_ops/releases/records.py",
    "taskman_ops/releases/remote_adoption.py",
    "taskman_ops/releases/remote_locking.py",
    "taskman_ops/releases/remote_snapshot.py",
    "taskman_ops/releases/staging.py",
    "taskman_ops/verification.py",
    "taskman_ops/pyinfra.py",
    "taskman_ops/workflows/deploy_transaction.py",
)
_PYINFRA_IMPORT_PATHS = frozenset(
    {
        "taskman_ops/host/baseline.py",
        "taskman_ops/host/firewall.py",
        "taskman_ops/provisioning.py",
        "taskman_ops/remote.py",
        "taskman_ops/services/caddy.py",
        "taskman_ops/services/postgresql.py",
        "taskman_ops/services/systemd.py",
    }
)
_CUSTOM_PYINFRA_CONSUMERS = frozenset(
    {
        "taskman_ops/host/firewall.py",
        "taskman_ops/services/caddy.py",
        "taskman_ops/services/postgresql.py",
    }
)
_APPROVED_DIRECT_OPERATIONS = {
    "taskman_ops/services/caddy.py": frozenset({"_validate_and_install_caddy"}),
    "taskman_ops/host/firewall.py": frozenset({"_activate_firewall_with_fresh_ssh"}),
    "taskman_ops/services/postgresql.py": frozenset({"_configure_postgresql_cluster"}),
}
_PLANNING_TERMS = re.compile(r"\b(?:task|tasks|phase|phases|milestone|milestones)\b", re.IGNORECASE)

_LEGACY_SCHEDULED_RECORD_MODULES = frozenset(
    {
        "taskman_ops.host_helper.lifecycle",
        "taskman_ops.host_helper.lifecycle_records",
    }
)


def check(repository: Path) -> tuple[str, ...]:
    """Return stable source-boundary violations for a repository root."""

    source_root = repository / "ops" / "taskman_ops"
    if not source_root.is_dir():
        raise ValueError("repository does not contain ops/taskman_ops")

    violations: list[str] = []
    python_files = tuple(sorted(source_root.rglob("*.py")))
    for path in python_files:
        relative = path.relative_to(repository).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as error:
            violations.append(f"{relative}: invalid Python ({error.msg})")
            continue
        violations.extend(_executable_string_violations(relative, tree))
        violations.extend(_import_violations(relative, tree))
        violations.extend(_planning_term_violations(relative, source))

    for relative in _REMOVED_PATHS:
        if (repository / "ops" / relative).exists():
            violations.append(f"{relative}: superseded production path remains")

    violations.extend(_convergence_violations(repository, python_files))
    violations.extend(_scheduled_asset_violations(repository))
    return tuple(sorted(violations))


def _executable_string_violations(relative: str, tree: ast.AST) -> Iterable[str]:
    source_path = relative.removeprefix("ops/")
    if not source_path.startswith("taskman_ops/") or source_path.startswith("taskman_ops/host_helper/"):
        return ()
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        lines = [line for line in node.value.splitlines() if line.strip()]
        if (
            len(lines) >= 12
            and any(marker in node.value for marker in _EXECUTABLE_MARKERS)
            and (source_path, _executable_string_owner(node, parents)) not in _REVIEWED_EXECUTABLE_OWNERS
        ):
            violations.append(
                f"{relative}:{node.lineno}: substantial executable controller string belongs in a helper or declarative operation"
            )
    return violations


def _executable_string_owner(node: ast.AST, parents: dict[int, ast.AST]) -> str | None:
    current = node
    while parent := parents.get(id(current)):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        if isinstance(parent, ast.Assign):
            for target in parent.targets:
                if isinstance(target, ast.Name):
                    return target.id
        if isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
            return parent.target.id
        current = parent
    return None


def _import_violations(relative: str, tree: ast.AST) -> Iterable[str]:
    violations: list[str] = []
    imports_pyinfra = False
    imports_custom_pyinfra = False
    for node in ast.walk(tree):
        module = ""
        if isinstance(node, ast.Import):
            names = tuple(alias.name for alias in node.names)
            imports_pyinfra = imports_pyinfra or any(name == "pyinfra" or name.startswith("pyinfra.") for name in names)
            imports_custom_pyinfra = imports_custom_pyinfra or any(name.endswith(".pyinfra") for name in names)
            candidates = names
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports_pyinfra = imports_pyinfra or module == "pyinfra" or module.startswith("pyinfra.")
            imports_custom_pyinfra = imports_custom_pyinfra or (
                node.level > 0 and module == "pyinfra"
            ) or module.endswith(".pyinfra")
            aliases = tuple(alias.name for alias in node.names)
            candidates = _from_import_candidates(relative, node.level, module, aliases)
        else:
            continue
        if any(_forbidden_module(candidate) for candidate in candidates):
            violations.append(f"{relative}:{node.lineno}: imports a superseded deployment module")

    source_path = relative.removeprefix("ops/")
    direct_operations, direct_import_lines = _direct_pyinfra_operations(tree)
    if imports_pyinfra and source_path not in _PYINFRA_IMPORT_PATHS and not direct_import_lines:
        violations.append(f"{relative}: pyinfra import is outside declarative convergence or transport")
    if imports_custom_pyinfra and source_path not in _CUSTOM_PYINFRA_CONSUMERS:
        violations.append(f"{relative}: custom pyinfra operation is outside its justified consumers")
    if "/host_helper/" in relative or relative.endswith("/host_helper/__main__.py"):
        violations.extend(_helper_import_violations(relative, tree))
    violations.extend(_direct_operation_violations(relative, source_path, direct_operations, direct_import_lines))
    return violations


def _direct_pyinfra_operations(tree: ast.AST) -> tuple[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...], tuple[int, ...]]:
    names: set[str] = set()
    module_aliases: set[str] = set()
    import_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"pyinfra.api", "pyinfra.api.operation"}:
            for alias in node.names:
                if alias.name == "operation":
                    names.add(alias.asname or alias.name)
                    import_lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"pyinfra.api", "pyinfra.api.operation"}:
                    module_aliases.add(alias.asname or alias.name)
                    import_lines.append(node.lineno)

    _expand_operation_aliases(tree, names, module_aliases)

    operations: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_operation_decorator(decorator, names, module_aliases) for decorator in node.decorator_list):
            operations.append(node)
    return tuple(operations), tuple(import_lines)


def _expand_operation_aliases(tree: ast.AST, names: set[str], module_aliases: set[str]) -> None:
    """Follow only direct-name aliases of pyinfra's operation decorator."""

    found_alias = True
    while found_alias:
        found_alias = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = tuple(target.id for target in node.targets if isinstance(target, ast.Name))
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = (node.target.id,)
                value = node.value
            else:
                continue
            if not _operation_decorator(value, names, module_aliases):
                continue
            for target in targets:
                if target not in names:
                    names.add(target)
                    found_alias = True


def _operation_decorator(node: ast.expr, names: set[str], module_aliases: set[str]) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id in names
    if not isinstance(target, ast.Attribute) or target.attr != "operation":
        return False
    dotted = _dotted_name(target.value)
    return dotted == "pyinfra.api" or dotted in module_aliases


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _direct_operation_violations(
    relative: str,
    source_path: str,
    operations: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...],
    import_lines: tuple[int, ...],
) -> Iterable[str]:
    approved = _APPROVED_DIRECT_OPERATIONS.get(source_path, frozenset())
    if operations:
        return tuple(
            f"{relative}:{min(decorator.lineno for decorator in operation.decorator_list)}: direct pyinfra operation is outside approved actions"
            for operation in operations
            if operation.name not in approved
        )
    if import_lines and source_path not in _APPROVED_DIRECT_OPERATIONS:
        return (f"{relative}:{import_lines[0]}: direct pyinfra operation is outside approved actions",)
    return ()


def _forbidden_module(candidate: str) -> bool:
    return any(candidate == name or candidate.endswith(f".{name}") for name in _FORBIDDEN_MODULES)


def _from_import_candidates(
    relative: str,
    level: int,
    module: str,
    aliases: tuple[str, ...],
) -> tuple[str, ...]:
    candidates = {module}
    if module:
        candidates.update(f"{module}.{alias}" for alias in aliases)
    if level:
        package = list(Path(relative.removeprefix("ops/")).with_suffix("").parts[:-1])
        parent = package[: len(package) - level + 1]
        resolved = ".".join((*parent, module)) if module else ".".join(parent)
        if resolved:
            candidates.add(resolved)
            candidates.update(f"{resolved}.{alias}" for alias in aliases)
    return tuple(sorted(candidate for candidate in candidates if candidate))


def _helper_import_violations(relative: str, tree: ast.AST) -> Iterable[str]:
    standard = sys.stdlib_module_names
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            modules = (node.module or "",)
        else:
            continue
        for module in modules:
            root = module.partition(".")[0]
            if root and root not in standard and root != "taskman_ops":
                violations.append(f"{relative}:{node.lineno}: helper imports third-party module {root}")
    return violations


def _planning_term_violations(relative: str, source: str) -> Iterable[str]:
    return tuple(
        f"{relative}:{source.count(chr(10), 0, match.start()) + 1}: planning term {match.group(0)!r} leaked into production"
        for match in _PLANNING_TERMS.finditer(source)
    )


def _convergence_violations(repository: Path, files: Iterable[Path]) -> Iterable[str]:
    callers: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run_deploy":
                callers.append(path.relative_to(repository).as_posix())
    return () if callers == ["ops/taskman_ops/provisioning.py"] else (
        "deployment convergence must have exactly one pyinfra invocation path",
    )


def _scheduled_asset_violations(repository: Path) -> Iterable[str]:
    violations: list[str] = []
    shell = repository / "ops" / "backup" / "taskman-backup"
    if shell.exists():
        violations.append("ops/backup/taskman-backup: legacy scheduled backup shell remains")

    for member in _scheduled_backup_members(repository):
        source = _scheduled_backup_member_source(repository, member)
        if source is None:
            violations.append(f"ops/{member}: scheduled backup archive member is unavailable")
            continue
        content, relative = source
        tree = ast.parse(content, filename=relative)
        module = member.removesuffix(".py").replace("/", ".")
        package = module if member.endswith("/__init__.py") else module.rpartition(".")[0]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if any(_legacy_scheduled_import(imported) for imported in _imported_modules(node, package)):
                violations.append(
                    f"{relative}:{node.lineno}: imports legacy scheduled backup record module"
                )
    return tuple(violations)


def _scheduled_backup_members(repository: Path) -> tuple[str, ...]:
    """Read the archive allowlist as syntax so the guard does not import production code."""

    package = repository / "ops" / "taskman_ops" / "helper_package.py"
    if not package.is_file():
        return ()
    tree = ast.parse(package.read_text(encoding="utf-8"), filename=package.as_posix())
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if not any(isinstance(target, ast.Name) and target.id == "BACKUP_ARCHIVE_MEMBERS" for target in targets):
            continue
        try:
            members = ast.literal_eval(node.value)
        except (TypeError, ValueError) as error:
            raise ValueError("scheduled backup archive allowlist is invalid") from error
        if (
            not isinstance(members, tuple)
            or any(
                type(member) is not str
                or (
                    member not in {"__main__.py", "taskman_ops/__init__.py"}
                    and (
                        not member.startswith("taskman_ops/")
                        or not member.endswith(".py")
                        or ".." in member.split("/")
                    )
                )
                for member in members
            )
        ):
            raise ValueError("scheduled backup archive allowlist is invalid")
        return members
    raise ValueError("scheduled backup archive allowlist is unavailable")


def _scheduled_backup_member_source(repository: Path, member: str) -> tuple[str, str] | None:
    package = repository / "ops" / "taskman_ops" / "helper_package.py"
    generated_members = {
        "__main__.py": "_FIXED_BACKUP_MAIN",
        "taskman_ops/__init__.py": "_FIXED_NAMESPACE",
    }
    if member in generated_members:
        return _generated_member_source(package, generated_members[member])
    source = repository / "ops" / member
    if not source.is_file():
        return None
    return source.read_text(encoding="utf-8"), f"ops/{member}"


def _generated_member_source(package: Path, name: str) -> tuple[str, str] | None:
    """Return generated archive Python with its defining source location."""

    tree = ast.parse(package.read_text(encoding="utf-8"), filename=package.as_posix())
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        try:
            content = ast.literal_eval(node.value)
        except (TypeError, ValueError) as error:
            raise ValueError("scheduled backup generated member is invalid") from error
        if type(content) is not bytes:
            raise ValueError("scheduled backup generated member is invalid")
        try:
            return content.decode("utf-8"), "ops/taskman_ops/helper_package.py"
        except UnicodeDecodeError as error:
            raise ValueError("scheduled backup generated member is invalid") from error
    return None


def _imported_modules(node: ast.Import | ast.ImportFrom, package: str) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    base = node.module or ""
    if node.level:
        parts = package.split(".")
        base = ".".join(parts[: len(parts) - node.level + 1] + ([base] if base else []))
    return tuple(filter(None, (base, *(f"{base}.{alias.name}" for alias in node.names))))


def _legacy_scheduled_import(imported: str) -> bool:
    return any(
        imported == legacy or imported.startswith(f"{legacy}.")
        for legacy in _LEGACY_SCHEDULED_RECORD_MODULES
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    args = parser.parse_args(argv)
    try:
        violations = check(args.repository.resolve())
    except (OSError, UnicodeError, ValueError) as error:
        print(f"architecture scan failed: {error}", file=sys.stderr)
        return 2
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
