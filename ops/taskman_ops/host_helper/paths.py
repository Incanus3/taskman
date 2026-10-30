"""Exact host-local path authority derived from the two protocol roots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import os
import stat
import re


class PathAuthorityError(ValueError):
    """Raised when a request tries to supply derived or unsafe host paths."""


_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SELECTION_FILE_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")


def _absolute(value: object, label: str) -> PurePosixPath:
    if type(value) is not str or not value.startswith("/") or "\\" in value or "\x00" in value:
        raise PathAuthorityError(f"invalid {label}")
    path = PurePosixPath(value)
    if path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise PathAuthorityError(f"invalid {label}")
    return path


def _overlaps(left: PurePosixPath, right: PurePosixPath) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


@dataclass(frozen=True)
class ManagedPaths:
    """The only host paths a request may select directly.

    Release, deployment, and current-selection paths are intentionally
    properties: accepting them from controller input would make a derived
    authority replaceable by remote or operator data.
    """

    install_root: PurePosixPath
    backup_root: PurePosixPath

    @classmethod
    def from_mapping(cls, value: object) -> "ManagedPaths":
        if not isinstance(value, Mapping) or set(value) != {"install_root", "backup_root"}:
            raise PathAuthorityError("request paths must contain exactly install_root and backup_root")
        install_root = _absolute(value["install_root"], "install root")
        backup_root = _absolute(value["backup_root"], "backup root")
        if _overlaps(install_root, backup_root):
            raise PathAuthorityError("managed roots overlap")
        return cls(install_root=install_root, backup_root=backup_root)

    @property
    def release_root(self) -> PurePosixPath:
        return self.install_root / "releases"

    @property
    def upload_root(self) -> PurePosixPath:
        return self.install_root / "uploads"

    @property
    def deployment_root(self) -> PurePosixPath:
        return self.install_root / "deployments"

    @property
    def current_link(self) -> PurePosixPath:
        return self.install_root / "current"

    @property
    def selection_root(self) -> PurePosixPath:
        """Directory containing create-once successful selection records."""

        return self.deployment_root / "selections"

    @property
    def backup_protection_root(self) -> PurePosixPath:
        """Directory containing migration-attempt backup protections."""

        return self.deployment_root / "backup-protections"

    @property
    def restore_target_path(self) -> PurePosixPath:
        """The one durable binding for an unfinished restore."""

        return self.deployment_root / "restore-target.json"

    @property
    def lifecycle_lock_path(self) -> PurePosixPath:
        """The one exclusive lifecycle lock derived from ``install_root``."""

        return self.install_root / "lifecycle.lock"

    def release_manifest(self, release_id: str) -> PurePosixPath:
        """Return the manifest path below a caller-validated release ID."""

        from taskman_ops.releases.identifiers import validate_release_id

        validate_release_id(release_id)
        return self.release_root / release_id / ".taskman-release.json"

    def backup_manifest(self, backup_id: str) -> PurePosixPath:
        """Return the sidecar manifest path below a caller-validated backup ID."""

        if type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None:
            raise PathAuthorityError("invalid backup identifier")
        return self.backup_root / f"{backup_id}.json"

    def backup_protection(self, backup_id: str) -> PurePosixPath:
        """Return the derived protection record path for one backup ID."""

        if type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None:
            raise PathAuthorityError("invalid backup identifier")
        return self.backup_protection_root / f"{backup_id}.json"

    def selection_record(self, filename: str) -> PurePosixPath:
        """Return a selection path below the derived selection directory."""

        if type(filename) is not str or _SELECTION_FILE_RE.fullmatch(filename) is None:
            raise PathAuthorityError("invalid selection record filename")
        return self.selection_root / filename

    def local(self, path: PurePosixPath) -> Path:
        """Materialize an already-derived POSIX path without joining input."""

        return Path(path.as_posix())

    def validate_existing(self, *, owner_uid: int) -> None:
        """Reject any existing authoritative directory that is unsafe.

        Lexical authority is not filesystem authority: each root used to
        derive lifecycle state must be inspected with ``lstat`` so a link is
        never followed while deciding whether it is trustworthy.  Absence is
        allowed because an empty host has no lifecycle storage yet.
        """

        if type(owner_uid) is not int or owner_uid < 0:
            raise PathAuthorityError("invalid managed root owner")
        for label, authority in (
            ("install root", self.install_root),
            ("backup root", self.backup_root),
            ("release root", self.release_root),
            ("deployment root", self.deployment_root),
            ("selection root", self.selection_root),
            ("backup protection root", self.backup_protection_root),
        ):
            path = self.local(authority)
            try:
                details = path.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise PathAuthorityError(f"unable to inspect {label}") from error
            if (
                stat.S_ISLNK(details.st_mode)
                or not stat.S_ISDIR(details.st_mode)
                or details.st_uid != owner_uid
                # Read and search permission is harmless for these roots;
                # authority is lost only to special bits or group/other
                # writers (the established lifecycle-lock contract).
                or details.st_mode & 0o7022
            ):
                raise PathAuthorityError(f"{label} is unsafe")


def _upload_metadata(path: Path) -> os.stat_result:
    return path.lstat()


def _upload_directory(path: Path, *, private: bool = False) -> None:
    details = _upload_metadata(path)
    if (not stat.S_ISDIR(details.st_mode) or details.st_uid != 0
            or details.st_mode & 0o7022
            or (private and (details.st_gid != 0 or stat.S_IMODE(details.st_mode) != 0o700))):
        raise PathAuthorityError("upload staging path is unsafe")


def _upload_install_root(install_root: PurePosixPath) -> Path:
    if not isinstance(install_root, PurePosixPath):
        raise PathAuthorityError("invalid install root")
    root = _absolute(str(install_root), "install root")
    if root == PurePosixPath('/'):
        raise PathAuthorityError("invalid install root")
    return Path(root)


def validate_upload_root(install_root: PurePosixPath, *, allow_missing: bool = False) -> None:
    """Check ordinary pathname authority; only the two derived leaves may be absent."""

    root = _upload_install_root(install_root)
    for path in (*reversed(root.parents), root, root / 'uploads'):
        try:
            _upload_directory(path, private=path == root / 'uploads')
        except FileNotFoundError:
            if not allow_missing or path not in (root, root / 'uploads'):
                raise PathAuthorityError("upload staging directory is missing") from None


def validate_upload_path(install_root: PurePosixPath, upload_path: PurePosixPath) -> None:
    """Validate a contained private input without retaining directory descriptors."""

    root = _upload_install_root(install_root) / 'uploads'
    if not isinstance(upload_path, PurePosixPath):
        raise PathAuthorityError("invalid upload path")
    upload = _absolute(str(upload_path), "upload path")
    if not upload.is_relative_to(root) or upload == PurePosixPath(root):
        raise PathAuthorityError("upload path is outside upload staging")
    validate_upload_root(install_root)
    relative = upload.relative_to(root)
    for parent in reversed(relative.parents):
        if parent != PurePosixPath('.'):
            _upload_directory(root / parent)
    try:
        details = _upload_metadata(Path(upload))
    except FileNotFoundError:
        raise PathAuthorityError("upload input is missing") from None
    if (not stat.S_ISREG(details.st_mode) or details.st_uid != 0
            or stat.S_IMODE(details.st_mode) != 0o600):
        raise PathAuthorityError("upload input is unsafe")


def prepare_upload_root(install_root: PurePosixPath, destinations: tuple[str, ...] = ()) -> bool:
    """Refuse existing destinations, then create only permitted missing leaves.

    Authority errors prove no mutation was attempted. After a creation attempt,
    failures are ordinary OS errors so callers retain possible mutation evidence.
    """

    root = _upload_install_root(install_root)
    validate_upload_root(install_root, allow_missing=True)
    if not isinstance(destinations, tuple):
        raise PathAuthorityError("invalid upload destinations")
    for name in destinations:
        if (type(name) is not str or not name or name in {'.', '..'}
                or any(character in name for character in '/\\\x00')):
            raise PathAuthorityError("invalid upload destination")
        try:
            _upload_metadata(root / 'uploads' / name)
        except FileNotFoundError:
            continue
        raise PathAuthorityError("upload destination already exists")
    changed = False
    attempted = False
    try:
        for path, mode in ((root, 0o750), (root / 'uploads', 0o700)):
            try:
                _upload_metadata(path)
            except FileNotFoundError:
                attempted = True
                try:
                    path.mkdir(mode=mode)
                except FileExistsError:
                    # Check the colliding directory without repairing it.
                    pass
                else:
                    changed = True
                    os.chown(path, 0, 0)
                    path.chmod(mode)
            _upload_directory(path, private=path == root / 'uploads')
        validate_upload_root(install_root)
    except PathAuthorityError as error:
        if attempted:
            raise OSError("unable to prepare upload staging") from error
        raise
    return changed


__all__ = ["ManagedPaths", "PathAuthorityError", "validate_upload_root", "validate_upload_path", "prepare_upload_root"]
