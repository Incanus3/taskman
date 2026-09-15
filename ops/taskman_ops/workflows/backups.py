"""Controller translation for completed backup manifests."""

from __future__ import annotations

import time

from ..config import EnvironmentConfig
from ..remote import Remote
from . import DiscoveryResult
from .inventory import collect_inventory


_LISTING_TIMEOUT_SECONDS = 660.0


def list_backups(
    remote: Remote,
    config: EnvironmentConfig,
) -> DiscoveryResult:
    """Return the complete validated backup inventory after every page succeeds."""

    return DiscoveryResult(
        collect_inventory(
            remote,
            config,
            "list_backups",
            deadline=time.monotonic() + _LISTING_TIMEOUT_SECONDS,
        )
    )


__all__ = ["list_backups"]
