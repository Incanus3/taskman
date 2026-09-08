from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import zipfile

from taskman_ops.helper_package import ARCHIVE_MEMBERS, build_helper_package, temporary_helper_package


def test_builder_produces_identical_allowlisted_zipapps(tmp_path: Path) -> None:
    """Environment-dependent archive metadata must not change a transferred helper's checksum."""

    first = build_helper_package(tmp_path / "first.pyz")
    second = build_helper_package(tmp_path / "second.pyz")
    first_bytes = first.path.read_bytes()
    second_bytes = second.path.read_bytes()

    assert first_bytes == second_bytes
    assert first.sha256 == hashlib.sha256(first_bytes).hexdigest()
    assert second.sha256 == first.sha256
    assert first.protocol_version == 2
    assert first.identity == f"v2-{first.sha256}"
    assert stat.S_IMODE(first.path.stat().st_mode) == 0o600


def test_builder_writes_only_lexical_fixed_metadata_members(tmp_path: Path) -> None:
    """Archive discovery could otherwise transfer tests, secrets, or controller configuration."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    with zipfile.ZipFile(package.path) as archive:
        members = archive.infolist()

    names = [member.filename for member in members]
    assert names == sorted(names)
    assert names == list(ARCHIVE_MEMBERS)
    assert "taskman_ops/host_helper/commands.py" in names
    assert "taskman_ops/host_helper/records.py" in names
    assert "taskman_ops/host_helper/state.py" in names
    assert not any(
        name in names
        for name in (
            "taskman_ops/host_helper/facts.py",
            "taskman_ops/host_helper/legacy_result.py",
            "taskman_ops/host_helper/lifecycle.py",
            "taskman_ops/host_helper/lifecycle_records.py",
            "taskman_ops/host_helper/operations/legacy_backup.py",
            "taskman_ops/host_helper/runtime.py",
        )
    )
    assert all(member.date_time == (2020, 1, 1, 0, 0, 0) for member in members)
    assert all((member.external_attr >> 16) == (stat.S_IFREG | 0o644) for member in members)
    assert not any(
        forbidden in name.lower()
        for name in names
        for forbidden in ("config", "secret", "test", "cache", "pyc", ".git", "metadata")
    )


def test_temporary_package_is_removed_after_its_invocation_scope() -> None:
    """The controller must not leave its local helper archive behind after use."""

    with temporary_helper_package() as package:
        path = package.path
        assert path.is_file()

    assert not path.exists()
