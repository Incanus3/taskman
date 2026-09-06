"""Exact host-local path authority derived from the two protocol roots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import stat


class PathAuthorityError(ValueError):
    """Raised when a request tries to supply derived or unsafe host paths."""


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
    def deployment_root(self) -> PurePosixPath:
        return self.install_root / "deployments"

    @property
    def current_link(self) -> PurePosixPath:
        return self.install_root / "current"

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


__all__ = ["ManagedPaths", "PathAuthorityError"]
