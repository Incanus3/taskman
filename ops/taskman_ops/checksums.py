"""Streaming file checksums shared by controller and server-side code."""

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Return a file's SHA-256 digest without loading it all into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
