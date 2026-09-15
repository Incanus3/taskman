"""Controller translation for completed release manifests."""

from __future__ import annotations

import time

from ..config import EnvironmentConfig
from ..remote import Remote
from . import DiscoveryResult
from .inventory import collect_inventory


_LISTING_TIMEOUT_SECONDS = 660.0


def list_releases(
    remote: Remote,
    config: EnvironmentConfig,
) -> DiscoveryResult:
    """Return the complete validated release inventory after every page succeeds."""

    return DiscoveryResult(
        collect_inventory(
            remote,
            config,
            "list_releases",
            deadline=time.monotonic() + _LISTING_TIMEOUT_SECONDS,
        )
    )


__all__ = ["list_releases"]
