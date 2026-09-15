"""Canonical managed paths and identifier containment."""

from pathlib import PurePosixPath

import pytest

from taskman_ops.host_helper.paths import ManagedPaths, PathAuthorityError
from taskman_ops.releases.identifiers import build_release_id


RELEASE = build_release_id("0.2.0", "a" * 40, artifact_sha256="b" * 64, source_dirty=False)
BACKUP = "backup-" + "c" * 32
SELECTION = "selection-" + "d" * 64 + ".json"


def test_derived_record_paths_use_the_configured_roots() -> None:
    paths = ManagedPaths.from_mapping(
        {"install_root": "/srv/custom", "backup_root": "/var/backups/custom"}
    )

    assert paths.lifecycle_lock_path == PurePosixPath("/srv/custom/lifecycle.lock")
    assert paths.release_manifest(RELEASE) == PurePosixPath(
        f"/srv/custom/releases/{RELEASE}/.taskman-release.json"
    )
    assert paths.backup_manifest(BACKUP) == PurePosixPath(
        f"/var/backups/custom/{BACKUP}.json"
    )
    assert paths.backup_protection_root == PurePosixPath(
        "/srv/custom/deployments/backup-protections"
    )
    assert paths.backup_protection(BACKUP) == PurePosixPath(
        f"/srv/custom/deployments/backup-protections/{BACKUP}.json"
    )
    assert paths.restore_target_path == PurePosixPath(
        "/srv/custom/deployments/restore-target.json"
    )
    assert paths.selection_record(SELECTION) == PurePosixPath(
        f"/srv/custom/deployments/selections/{SELECTION}"
    )


@pytest.mark.parametrize(
    "identifier",
    (
        "../outside",
        "/outside",
        "",
        "backup-" + "a" * 31,
        "backup-" + "A" * 32,
    ),
)
def test_manifest_paths_reject_invalid_identifiers(identifier: str) -> None:
    paths = ManagedPaths.from_mapping(
        {"install_root": "/srv/custom", "backup_root": "/var/backups/custom"}
    )

    with pytest.raises(ValueError, match="invalid release identifier"):
        paths.release_manifest(identifier)
    with pytest.raises(PathAuthorityError, match="invalid backup identifier"):
        paths.backup_manifest(identifier)


def test_protection_path_rejects_non_backup_identifiers() -> None:
    paths = ManagedPaths.from_mapping(
        {"install_root": "/srv/custom", "backup_root": "/var/backups/custom"}
    )

    with pytest.raises(PathAuthorityError, match="invalid backup identifier"):
        paths.backup_protection("selection-" + "a" * 64 + ".json")


def test_roots_must_be_disjoint_and_canonical() -> None:
    with pytest.raises(PathAuthorityError, match="overlap"):
        ManagedPaths.from_mapping(
            {"install_root": "/srv/taskman", "backup_root": "/srv/taskman/backups"}
        )
    with pytest.raises(PathAuthorityError, match="invalid install root"):
        ManagedPaths.from_mapping(
            {"install_root": "/srv/../taskman", "backup_root": "/var/backups/taskman"}
        )
