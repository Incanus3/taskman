"""Deterministic allowlisted construction of the transient host helper zipapp."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat
import tempfile
import zipfile
from typing import Iterator

from .host_protocol import PROTOCOL_VERSION


_ARCHIVE_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
_ARCHIVE_MODE = stat.S_IFREG | 0o644
_ZIPAPP_MODE = 0o600
_FIXED_MAIN = b"from taskman_ops.host_helper.__main__ import main\n\nraise SystemExit(main())\n"
_FIXED_NAMESPACE = b'"""Bundled Taskman host-helper namespace."""\n'
_SOURCE_ROOT = Path(__file__).resolve().parent

ARCHIVE_MEMBERS = (
    "__main__.py",
    "taskman_ops/__init__.py",
    "taskman_ops/host_helper/__init__.py",
    "taskman_ops/host_helper/__main__.py",
    "taskman_ops/host_helper/commands.py",
    "taskman_ops/host_helper/facts.py",
    "taskman_ops/host_helper/legacy_result.py",
    "taskman_ops/host_helper/lifecycle.py",
    "taskman_ops/host_helper/lifecycle_records.py",
    "taskman_ops/host_helper/lock.py",
    "taskman_ops/host_helper/operations/__init__.py",
    "taskman_ops/host_helper/operations/backup.py",
    "taskman_ops/host_helper/operations/cleanup.py",
    "taskman_ops/host_helper/operations/deploy.py",
    "taskman_ops/host_helper/operations/discover.py",
    "taskman_ops/host_helper/operations/legacy_backup.py",
    "taskman_ops/host_helper/operations/restore.py",
    "taskman_ops/host_helper/operations/rollback.py",
    "taskman_ops/host_helper/paths.py",
    "taskman_ops/host_helper/records.py",
    "taskman_ops/host_helper/runtime.py",
    "taskman_ops/host_helper/state.py",
    "taskman_ops/host_helper/verification.py",
    "taskman_ops/host_protocol/__init__.py",
    "taskman_ops/host_protocol/envelope.py",
    "taskman_ops/host_protocol/identifiers.py",
    "taskman_ops/host_protocol/operations.py",
    "taskman_ops/releases/__init__.py",
    "taskman_ops/releases/identifiers.py",
)

_SOURCE_MEMBERS = {
    "taskman_ops/host_helper/__init__.py": _SOURCE_ROOT / "host_helper" / "__init__.py",
    "taskman_ops/host_helper/__main__.py": _SOURCE_ROOT / "host_helper" / "__main__.py",
    "taskman_ops/host_helper/commands.py": _SOURCE_ROOT / "host_helper" / "commands.py",
    "taskman_ops/host_helper/facts.py": _SOURCE_ROOT / "host_helper" / "facts.py",
    "taskman_ops/host_helper/lifecycle.py": _SOURCE_ROOT / "host_helper" / "lifecycle.py",
    "taskman_ops/host_helper/lock.py": _SOURCE_ROOT / "host_helper" / "lock.py",
    "taskman_ops/host_helper/lifecycle_records.py": _SOURCE_ROOT / "host_helper" / "lifecycle_records.py",
    "taskman_ops/host_helper/legacy_result.py": _SOURCE_ROOT / "host_helper" / "legacy_result.py",
    "taskman_ops/host_helper/operations/__init__.py": _SOURCE_ROOT / "host_helper" / "operations" / "__init__.py",
    "taskman_ops/host_helper/operations/backup.py": _SOURCE_ROOT / "host_helper" / "operations" / "backup.py",
    "taskman_ops/host_helper/operations/cleanup.py": _SOURCE_ROOT / "host_helper" / "operations" / "cleanup.py",
    "taskman_ops/host_helper/operations/discover.py": _SOURCE_ROOT / "host_helper" / "operations" / "discover.py",
    "taskman_ops/host_helper/operations/deploy.py": _SOURCE_ROOT / "host_helper" / "operations" / "deploy.py",
    "taskman_ops/host_helper/operations/legacy_backup.py": _SOURCE_ROOT / "host_helper" / "operations" / "legacy_backup.py",
    "taskman_ops/host_helper/operations/restore.py": _SOURCE_ROOT / "host_helper" / "operations" / "restore.py",
    "taskman_ops/host_helper/operations/rollback.py": _SOURCE_ROOT / "host_helper" / "operations" / "rollback.py",
    "taskman_ops/host_helper/paths.py": _SOURCE_ROOT / "host_helper" / "paths.py",
    "taskman_ops/host_helper/records.py": _SOURCE_ROOT / "host_helper" / "records.py",
    "taskman_ops/host_helper/runtime.py": _SOURCE_ROOT / "host_helper" / "runtime.py",
    "taskman_ops/host_helper/state.py": _SOURCE_ROOT / "host_helper" / "state.py",
    "taskman_ops/host_helper/verification.py": _SOURCE_ROOT / "host_helper" / "verification.py",
    "taskman_ops/releases/__init__.py": _SOURCE_ROOT / "releases" / "__init__.py",
    "taskman_ops/releases/identifiers.py": _SOURCE_ROOT / "releases" / "identifiers.py",
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


def _member_bytes(name: str) -> bytes:
    if name == "__main__.py":
        return _FIXED_MAIN
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


def build_helper_package(destination: Path) -> HelperPackage:
    """Write the exact allowlist as a reproducible Python zipapp at ``destination``."""

    if not isinstance(destination, Path):
        raise TypeError("helper package destination must be a Path")
    if tuple(sorted(ARCHIVE_MEMBERS)) != ARCHIVE_MEMBERS:
        raise RuntimeError("helper package allowlist is not lexical")

    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name in ARCHIVE_MEMBERS:
            archive.writestr(
                _zip_info(name),
                _member_bytes(name),
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


@contextmanager
def temporary_helper_package() -> Iterator[HelperPackage]:
    """Build one transient local archive for exactly one helper invocation."""

    with tempfile.TemporaryDirectory(prefix="taskman-host-helper-") as directory:
        yield build_helper_package(Path(directory) / "taskman-host.pyz")


__all__ = ["ARCHIVE_MEMBERS", "HelperPackage", "build_helper_package", "temporary_helper_package"]
