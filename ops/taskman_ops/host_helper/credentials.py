"""Private credential-file authority for host database operations."""

from __future__ import annotations

import os
from pathlib import Path
import stat


class CredentialError(ValueError):
    """A credential file is absent or not private to the invoking administrator."""


def validate_credentials(path: Path) -> None:
    """Require one regular, non-symlinked, owner-only PostgreSQL password file."""

    try:
        details = path.lstat()
    except OSError as error:
        raise CredentialError("database credentials are unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise CredentialError("database credentials are unsafe")


__all__ = ["CredentialError", "validate_credentials"]
