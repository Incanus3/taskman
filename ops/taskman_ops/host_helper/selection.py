"""Atomic current-release selection for explicit host procedures."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from .filesystem import fsync_directory
from .paths import ManagedPaths


class SelectionAmbiguityError(ValueError):
    """A stale current-link temporary cannot be attributed to this procedure."""


def select_current(paths: ManagedPaths, release_id: str) -> None:
    """Replace ``current`` with a same-filesystem link to one confirmed release."""

    root = Path(paths.local(paths.install_root))
    target = Path(paths.local(paths.release_root / release_id))
    temporary = root / f".current-{release_id}.tmp"
    if temporary.exists() or temporary.is_symlink():
        details = temporary.lstat()
        if not stat.S_ISLNK(details.st_mode) or temporary.resolve(strict=False) != target:
            raise SelectionAmbiguityError("selection temporary is unsafe")
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, Path(paths.local(paths.current_link)))
    fsync_directory(root)


__all__ = ["SelectionAmbiguityError", "select_current"]
