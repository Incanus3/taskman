"""Canonical managed paths and identifier containment."""

from pathlib import PurePosixPath

import pytest

from taskman_ops.host_helper.paths import ManagedPaths, PathAuthorityError


def test_manifest_and_lock_paths_use_the_configured_roots() -> None:
    paths = ManagedPaths.from_mapping(
        {"install_root": "/srv/custom", "backup_root": "/var/backups/custom"}
    )

    assert paths.lifecycle_lock_path == PurePosixPath("/srv/custom/lifecycle.lock")
    assert paths.release_manifest("0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6") == PurePosixPath(
        "/srv/custom/releases/0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6/.taskman-release.json"
    )
    assert paths.backup_manifest("backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") == PurePosixPath(
        "/var/backups/custom/backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
    )


@pytest.mark.parametrize("identifier", ("../outside", "/outside", "", "backup-" + "a" * 31))
def test_manifest_paths_reject_invalid_identifiers(identifier: str) -> None:
    paths = ManagedPaths.from_mapping(
        {"install_root": "/srv/custom", "backup_root": "/var/backups/custom"}
    )

    with pytest.raises(ValueError, match="invalid release identifier"):
        paths.release_manifest(identifier)
    with pytest.raises(PathAuthorityError, match="invalid backup identifier"):
        paths.backup_manifest(identifier)
