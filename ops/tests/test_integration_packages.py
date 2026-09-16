"""Contracts for immutable archives shared only by public integration tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import subprocess

from tests.support.integration_packages import integration_packages


def test_integration_packages_materialize_private_independent_executable_copies(
    integration_packages, tmp_path: Path
) -> None:
    """Every consumer gets private bytes while retaining the validated archive identity."""

    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    first = (
        integration_packages.materialize_helper(first_directory / "helper.pyz"),
        integration_packages.materialize_scheduled_backup(first_directory / "backup.pyz"),
    )
    second = (
        integration_packages.materialize_helper(second_directory / "helper.pyz"),
        integration_packages.materialize_scheduled_backup(second_directory / "backup.pyz"),
    )

    for first_package, second_package, expected_code in zip(
        first, second, (0, 2), strict=True
    ):
        assert first_package.path != second_package.path
        assert first_package.path.read_bytes() == second_package.path.read_bytes()
        assert hashlib.sha256(first_package.path.read_bytes()).hexdigest() == first_package.sha256
        assert hashlib.sha256(second_package.path.read_bytes()).hexdigest() == second_package.sha256
        assert first_package.sha256 == second_package.sha256
        assert first_package.protocol_version == second_package.protocol_version
        assert stat.S_IMODE(first_package.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(second_package.path.stat().st_mode) == 0o600

        first_package.path.write_bytes(b"mutated private copy")
        assert second_package.path.read_bytes() != b"mutated private copy"

        completed = subprocess.run(
            ["python3", "-I", "-S", str(second_package.path)],
            input=b"",
            capture_output=True,
            check=False,
        )
        assert completed.returncode == expected_code
        assert completed.stderr == b""
