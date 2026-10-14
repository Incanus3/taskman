"""Streaming file checksums shared by controller and server-side code."""

import hashlib
from pathlib import Path
import time


def sha256_file(path: Path, *, deadline: float | None = None) -> str:
    """Hash bounded chunks, optionally refusing an expired monotonic deadline."""

    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("file checksum timed out")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("file checksum timed out")
            chunk = source.read(1024 * 1024)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("file checksum timed out")
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
