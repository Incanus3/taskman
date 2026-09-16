"""Immutable helper archives reused only by public integration controllers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

import pytest

from taskman_ops.helper_client.package import (
    HelperPackage,
    build_helper_package,
    build_scheduled_backup_package,
)


@dataclass(frozen=True)
class _ArchiveSnapshot:
    """Validated archive metadata and bytes kept private to one pytest worker."""

    contents: bytes
    sha256: str
    protocol_version: int
    mode: int

    @classmethod
    def capture(cls, package: HelperPackage) -> _ArchiveSnapshot:
        return cls(
            package.path.read_bytes(),
            package.sha256,
            package.protocol_version,
            stat.S_IMODE(package.path.stat().st_mode),
        )

    def materialize(self, destination: Path) -> HelperPackage:
        """Write one private, independent copy for an integration controller."""

        destination.write_bytes(self.contents)
        destination.chmod(self.mode)
        return HelperPackage(destination, self.sha256, self.protocol_version)


@dataclass(frozen=True)
class IntegrationPackages:
    """Worker-local immutable sources for explicitly opted-in integration tests."""

    helper: _ArchiveSnapshot
    scheduled_backup: _ArchiveSnapshot

    def materialize_helper(self, destination: Path) -> HelperPackage:
        return self.helper.materialize(destination)

    def materialize_scheduled_backup(self, destination: Path) -> HelperPackage:
        return self.scheduled_backup.materialize(destination)


@pytest.fixture(scope="session")
def integration_packages(tmp_path_factory: pytest.TempPathFactory) -> IntegrationPackages:
    """Build the two immutable integration archives once for this pytest worker."""

    directory = tmp_path_factory.mktemp("integration-packages")
    return IntegrationPackages(
        helper=_ArchiveSnapshot.capture(
            build_helper_package(directory / "taskman-host.pyz")
        ),
        scheduled_backup=_ArchiveSnapshot.capture(
            build_scheduled_backup_package(directory / "taskman-backup.pyz")
        ),
    )
