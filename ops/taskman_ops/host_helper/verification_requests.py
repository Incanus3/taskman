"""Construction of the final host verification request."""

from __future__ import annotations

from collections.abc import Mapping

from taskman_ops.host_protocol import HostRequest, PROTOCOL_VERSION


def verification_request(
    source: HostRequest,
    expected_release_id: str,
    verification: Mapping[str, object],
) -> HostRequest:
    """Preserve request identity while making one selected release verifiable."""

    return HostRequest(
        PROTOCOL_VERSION,
        "verify",
        source.correlation_id,
        {"expected_release_id": expected_release_id},
        source.paths,
        verification,
    )


__all__ = ["verification_request"]
