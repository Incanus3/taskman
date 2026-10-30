"""Canonical managed paths and identifier containment."""

from pathlib import Path, PurePosixPath

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


@pytest.mark.parametrize("root,expected", [("/opt/taskman", "/opt/taskman/uploads"), ("/srv/custom", "/srv/custom/uploads")])
def test_upload_root_is_derived_from_installation(root, expected):
    roots = {"install_root": root, "backup_root": "/var/backups/taskman"}
    paths = ManagedPaths.from_mapping(roots)
    assert paths.upload_root == PurePosixPath(expected)
    assert set(vars(paths)) == {"install_root", "backup_root"}


@pytest.fixture
def upload_paths(tmp_path, monkeypatch):
    from taskman_ops.host_helper import paths
    import os
    import stat
    metadata = Path.lstat
    ancestors = set(tmp_path.parents)
    def synthetic_metadata(path):
        values = list(metadata(path))
        values[4:6] = [0, 0]
        if path in ancestors:
            values[0] = stat.S_IFDIR | 0o755
        return os.stat_result(values)
    # Synthetic ownership only, on real disposable pathname operations.
    monkeypatch.setattr(paths, '_upload_metadata', synthetic_metadata, raising=False)
    monkeypatch.setattr(paths.os if hasattr(paths, 'os') else __import__('os'), 'chown', lambda *_a: None)
    return paths, tmp_path / 'installation'


def test_prepare_upload_root_metadata(upload_paths):
    import stat
    module, root = upload_paths
    assert module.prepare_upload_root(PurePosixPath(root)) is True
    assert stat.S_IMODE(root.stat().st_mode) == 0o750
    assert stat.S_IMODE((root / 'uploads').stat().st_mode) == 0o700
    root.chmod(0o711)
    before = root.stat()
    assert module.prepare_upload_root(PurePosixPath(root)) is False
    assert root.stat() == before
    with pytest.raises(PathAuthorityError):
        module.prepare_upload_root(PurePosixPath(root / 'absent' / 'installation'))
    assert not (root / 'absent').exists()


@pytest.mark.parametrize('bad', ['root-mode', 'root-symlink', 'upload-mode', 'upload-gid', 'file-mode', 'file-uid', 'file-symlink', 'fifo', 'intermediate-symlink', 'escape', 'old-root'])
def test_upload_path_refuses_unsafe_authority(upload_paths, monkeypatch, bad):
    import os
    module, root = upload_paths
    module.prepare_upload_root(PurePosixPath(root))
    nested = root / 'uploads' / 'nested'
    nested.mkdir(mode=0o700)
    target = nested / 'payload'
    target.write_bytes(b'private')
    target.chmod(0o600)
    module.validate_upload_path(PurePosixPath(root), PurePosixPath(target))
    if bad in {'root-mode', 'upload-mode', 'file-mode'}:
        {'root-mode': root, 'upload-mode': root / 'uploads', 'file-mode': target}[bad].chmod(0o777)
    elif bad in {'root-symlink', 'file-symlink', 'intermediate-symlink'}:
        path = {'root-symlink': root, 'file-symlink': target, 'intermediate-symlink': nested}[bad]
        retained = path.with_name(path.name + '-retained')
        path.rename(retained)
        path.symlink_to(retained)
    elif bad == 'fifo':
        target.unlink()
        os.mkfifo(target, 0o600)
    elif bad in {'upload-gid', 'file-uid'}:
        metadata = module._upload_metadata
        changed = root / 'uploads' if bad == 'upload-gid' else target
        def wrong_owner(path):
            values = list(metadata(path))
            if path == changed:
                values[5 if bad == 'upload-gid' else 4] = 12345
            return os.stat_result(values)
        monkeypatch.setattr(module, '_upload_metadata', wrong_owner)
    else:
        target = root / ('uploads/../outside' if bad == 'escape' else 'deployments/uploads/payload')
    with pytest.raises(PathAuthorityError):
        module.validate_upload_path(PurePosixPath(root), PurePosixPath(target))


@pytest.mark.parametrize('kind', ['file', 'symlink', 'directory'])
def test_prepare_preserves_existing_destination(upload_paths, kind):
    module, root = upload_paths
    module.prepare_upload_root(PurePosixPath(root))
    target = root / 'uploads' / 'payload'
    if kind == 'file':
        target.write_bytes(b'preserved')
        target.chmod(0o640)
    elif kind == 'symlink':
        target.symlink_to(root / 'absent')
    else:
        target.mkdir()
    before = target.lstat()
    with pytest.raises(PathAuthorityError):
        module.prepare_upload_root(PurePosixPath(root), ('payload',))
    assert target.lstat() == before
    if kind == 'file':
        assert target.read_bytes() == b'preserved'


@pytest.mark.parametrize('boundary', ['ancestor', 'install', 'uploads'])
@pytest.mark.parametrize('bad', ['uid', 'write', 'special', 'symlink', 'file'])
def test_prepare_rejects_unsafe_existing_directories(upload_paths, monkeypatch, boundary, bad):
    import os
    module, root = upload_paths
    module.prepare_upload_root(PurePosixPath(root))
    path = {'ancestor': root.parent, 'install': root, 'uploads': root / 'uploads'}[boundary]
    if bad == 'uid':
        metadata = module._upload_metadata
        def wrong_owner(candidate):
            values = list(metadata(candidate))
            if candidate == path:
                values[4] = 12345
            return os.stat_result(values)
        monkeypatch.setattr(module, '_upload_metadata', wrong_owner)
    elif bad in {'write', 'special'}:
        path.chmod(0o722 if bad == 'write' else 0o2700)
    else:
        retained = path.with_name(path.name + '-retained')
        path.rename(retained)
        if bad == 'symlink':
            path.symlink_to(retained)
        else:
            path.write_bytes(b'preserved')
    before = path.lstat()
    try:
        with pytest.raises(PathAuthorityError):
            module.prepare_upload_root(PurePosixPath(root))
        assert path.lstat() == before
    finally:
        if boundary == 'ancestor' and bad in {'symlink', 'file'}:
            path.unlink()
            retained.rename(path)


def test_prepare_access_and_creation_failures_are_os_errors(upload_paths, monkeypatch):
    module, root = upload_paths
    def inaccessible(_path):
        raise PermissionError('private access canary')
    with monkeypatch.context() as patch:
        patch.setattr(module, '_upload_metadata', inaccessible)
        with pytest.raises(OSError):
            module.prepare_upload_root(PurePosixPath(root))
    mkdir = Path.mkdir
    def interrupted(path, *args, **kwargs):
        if path.name == 'uploads':
            raise PermissionError('private creation canary')
        return mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', interrupted)
    with pytest.raises(OSError):
        module.prepare_upload_root(PurePosixPath(root))
    assert root.is_dir()


def test_upload_path_accepts_direct_private_file(upload_paths):
    module, root = upload_paths
    module.prepare_upload_root(PurePosixPath(root))
    target = root / 'uploads' / 'payload'
    target.write_bytes(b'private')
    target.chmod(0o600)
    module.validate_upload_path(PurePosixPath(root), PurePosixPath(target))
