"""Deterministic allowlisted construction of the transient host helper zipapp."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ast
import hashlib
from pathlib import Path
import stat
import sys
import tempfile
import zipfile
from typing import Iterator

from .host_protocol import PROTOCOL_VERSION


_ARCHIVE_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
_ARCHIVE_MODE = stat.S_IFREG | 0o644
_ZIPAPP_MODE = 0o600
_ZIPAPP_SHEBANG = b"#!/usr/bin/python3\n"
_FIXED_MAIN = b"from taskman_ops.host_helper.__main__ import main\n\nraise SystemExit(main())\n"
_FIXED_BACKUP_MAIN = b"from taskman_ops.scheduled_backup import main\n\nraise SystemExit(main())\n"
_FIXED_NAMESPACE = b'"""Bundled Taskman host-helper namespace."""\n'
_SOURCE_ROOT = Path(__file__).resolve().parent

ARCHIVE_MEMBERS = (
    "__main__.py",
    "taskman_ops/__init__.py",
    "taskman_ops/host_helper/__init__.py",
    "taskman_ops/host_helper/__main__.py",
    "taskman_ops/host_helper/backups.py",
    "taskman_ops/host_helper/commands.py",
    "taskman_ops/host_helper/credentials.py",
    "taskman_ops/host_helper/database.py",
    "taskman_ops/host_helper/filesystem.py",
    "taskman_ops/host_helper/lock.py",
    "taskman_ops/host_helper/operations/__init__.py",
    "taskman_ops/host_helper/operations/backup.py",
    "taskman_ops/host_helper/operations/cleanup.py",
    "taskman_ops/host_helper/operations/deploy.py",
    "taskman_ops/host_helper/operations/discover.py",
    "taskman_ops/host_helper/operations/restore.py",
    "taskman_ops/host_helper/operations/rollback.py",
    "taskman_ops/host_helper/paths.py",
    "taskman_ops/host_helper/records.py",
    "taskman_ops/host_helper/selection.py",
    "taskman_ops/host_helper/services.py",
    "taskman_ops/host_helper/state.py",
    "taskman_ops/host_helper/verification.py",
    "taskman_ops/host_helper/verification_requests.py",
    "taskman_ops/host_protocol/__init__.py",
    "taskman_ops/host_protocol/envelope.py",
    "taskman_ops/host_protocol/identifiers.py",
    "taskman_ops/host_protocol/operations.py",
    "taskman_ops/releases/__init__.py",
    "taskman_ops/releases/identifiers.py",
)

BACKUP_ARCHIVE_MEMBERS = (
    "__main__.py",
    "taskman_ops/__init__.py",
    "taskman_ops/host_helper/__init__.py",
    "taskman_ops/host_helper/backups.py",
    "taskman_ops/host_helper/commands.py",
    "taskman_ops/host_helper/credentials.py",
    "taskman_ops/host_helper/database.py",
    "taskman_ops/host_helper/filesystem.py",
    "taskman_ops/host_helper/lock.py",
    "taskman_ops/host_helper/paths.py",
    "taskman_ops/host_helper/records.py",
    "taskman_ops/host_helper/state.py",
    "taskman_ops/releases/__init__.py",
    "taskman_ops/releases/identifiers.py",
    "taskman_ops/scheduled_backup.py",
)

_SOURCE_MEMBERS = {
    "taskman_ops/host_helper/__init__.py": _SOURCE_ROOT / "host_helper" / "__init__.py",
    "taskman_ops/host_helper/backups.py": _SOURCE_ROOT / "host_helper" / "backups.py",
    "taskman_ops/host_helper/__main__.py": _SOURCE_ROOT / "host_helper" / "__main__.py",
    "taskman_ops/host_helper/commands.py": _SOURCE_ROOT / "host_helper" / "commands.py",
    "taskman_ops/host_helper/credentials.py": _SOURCE_ROOT / "host_helper" / "credentials.py",
    "taskman_ops/host_helper/database.py": _SOURCE_ROOT / "host_helper" / "database.py",
    "taskman_ops/host_helper/filesystem.py": _SOURCE_ROOT / "host_helper" / "filesystem.py",
    "taskman_ops/host_helper/lock.py": _SOURCE_ROOT / "host_helper" / "lock.py",
    "taskman_ops/host_helper/operations/__init__.py": _SOURCE_ROOT / "host_helper" / "operations" / "__init__.py",
    "taskman_ops/host_helper/operations/backup.py": _SOURCE_ROOT / "host_helper" / "operations" / "backup.py",
    "taskman_ops/host_helper/operations/cleanup.py": _SOURCE_ROOT / "host_helper" / "operations" / "cleanup.py",
    "taskman_ops/host_helper/operations/discover.py": _SOURCE_ROOT / "host_helper" / "operations" / "discover.py",
    "taskman_ops/host_helper/operations/deploy.py": _SOURCE_ROOT / "host_helper" / "operations" / "deploy.py",
    "taskman_ops/host_helper/operations/restore.py": _SOURCE_ROOT / "host_helper" / "operations" / "restore.py",
    "taskman_ops/host_helper/operations/rollback.py": _SOURCE_ROOT / "host_helper" / "operations" / "rollback.py",
    "taskman_ops/host_helper/paths.py": _SOURCE_ROOT / "host_helper" / "paths.py",
    "taskman_ops/host_helper/records.py": _SOURCE_ROOT / "host_helper" / "records.py",
    "taskman_ops/host_helper/selection.py": _SOURCE_ROOT / "host_helper" / "selection.py",
    "taskman_ops/host_helper/services.py": _SOURCE_ROOT / "host_helper" / "services.py",
    "taskman_ops/host_helper/state.py": _SOURCE_ROOT / "host_helper" / "state.py",
    "taskman_ops/host_helper/verification.py": _SOURCE_ROOT / "host_helper" / "verification.py",
    "taskman_ops/host_helper/verification_requests.py": _SOURCE_ROOT / "host_helper" / "verification_requests.py",
    "taskman_ops/releases/__init__.py": _SOURCE_ROOT / "releases" / "__init__.py",
    "taskman_ops/releases/identifiers.py": _SOURCE_ROOT / "releases" / "identifiers.py",
    "taskman_ops/scheduled_backup.py": _SOURCE_ROOT / "scheduled_backup.py",
    "taskman_ops/host_protocol/__init__.py": _SOURCE_ROOT / "host_protocol" / "__init__.py",
    "taskman_ops/host_protocol/envelope.py": _SOURCE_ROOT / "host_protocol" / "envelope.py",
    "taskman_ops/host_protocol/identifiers.py": _SOURCE_ROOT / "host_protocol" / "identifiers.py",
    "taskman_ops/host_protocol/operations.py": _SOURCE_ROOT / "host_protocol" / "operations.py",
}


@dataclass(frozen=True)
class HelperPackage:
    """The immutable local archive identity used by later transfer logic."""

    path: Path
    sha256: str
    protocol_version: int

    @property
    def identity(self) -> str:
        """Return the versioned identity without relying on a mutable filename."""

        return f"v{self.protocol_version}-{self.sha256}"


def _member_bytes(name: str, main: bytes) -> bytes:
    if name == "__main__.py":
        return main
    if name == "taskman_ops/__init__.py":
        return _FIXED_NAMESPACE
    try:
        return _SOURCE_MEMBERS[name].read_bytes()
    except (KeyError, OSError) as error:
        raise RuntimeError("helper package source is unavailable") from error


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ARCHIVE_TIMESTAMP)
    info.create_system = 3
    info.external_attr = _ARCHIVE_MODE << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _build_package(destination: Path, members: tuple[str, ...], main: bytes) -> HelperPackage:
    """Write one lexical standard-library-only allowlist as a reproducible zipapp."""

    if not isinstance(destination, Path):
        raise TypeError("helper package destination must be a Path")
    if tuple(sorted(members)) != members:
        raise RuntimeError("helper package allowlist is not lexical")
    _validate_imports(members)

    with destination.open("wb") as output:
        output.write(_ZIPAPP_SHEBANG)
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for name in members:
                archive.writestr(
                    _zip_info(name),
                    _member_bytes(name, main),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )

    destination.chmod(_ZIPAPP_MODE)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return HelperPackage(
        path=destination,
        sha256=digest,
        protocol_version=PROTOCOL_VERSION,
    )


def build_helper_package(destination: Path) -> HelperPackage:
    """Write the exact transient helper allowlist as a reproducible zipapp."""

    return _build_package(destination, ARCHIVE_MEMBERS, _FIXED_MAIN)


def build_scheduled_backup_package(destination: Path) -> HelperPackage:
    """Write the persistent least-authority scheduled-backup zipapp."""

    return _build_package(destination, BACKUP_ARCHIVE_MEMBERS, _FIXED_BACKUP_MAIN)


def _validate_imports(members: tuple[str, ...]) -> None:
    """Refuse dependencies outside Taskman itself and the Python standard library."""

    allowed = set(getattr(sys, "stdlib_module_names", ())) | {"taskman_ops", "__future__"}
    for name in members:
        if name in {"__main__.py", "taskman_ops/__init__.py", "taskman_ops/host_helper/__init__.py", "taskman_ops/releases/__init__.py"}:
            continue
        try:
            tree = ast.parse(_member_bytes(name, _FIXED_MAIN), filename=name)
        except SyntaxError as error:
            raise RuntimeError("helper package source is invalid") from error
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = (alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
                roots = (node.module.split(".", 1)[0],)
            else:
                continue
            if any(root not in allowed for root in roots):
                raise RuntimeError("helper package contains a third-party import")


@contextmanager
def temporary_helper_package() -> Iterator[HelperPackage]:
    """Build one transient local archive for exactly one helper invocation."""

    with tempfile.TemporaryDirectory(prefix="taskman-host-helper-") as directory:
        yield build_helper_package(Path(directory) / "taskman-host.pyz")


__all__ = [
    "ARCHIVE_MEMBERS",
    "BACKUP_ARCHIVE_MEMBERS",
    "HelperPackage",
    "build_helper_package",
    "build_scheduled_backup_package",
    "temporary_helper_package",
]
