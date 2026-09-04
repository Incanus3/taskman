"""The admission boundary is separate from read-only host collection."""

from __future__ import annotations


def test_acceptance_boundary_exports_supported_host_validation() -> None:
    from taskman_ops.host.acceptance import validate_supported_host

    assert callable(validate_supported_host)
