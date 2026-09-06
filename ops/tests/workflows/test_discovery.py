"""Read-only discovery is translated from one transient helper result."""

from __future__ import annotations

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.helper_runner import HelperInvocation
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.backups import list_backups
from taskman_ops.workflows.releases import list_releases

from .test_helper_read_only import _config


def _refused(request, operation: str) -> HelperInvocation:
    return HelperInvocation(
        HostResult(
            protocol_version=1, operation=operation, operation_id=request.operation_id,
            outcome="refused", stage="lifecycle-records", changed_stages=(), lifecycle={}, runtime_state={},
            verification={}, residue_paths=(),
            recovery_actions=("inspect the managed lifecycle state before retrying",), warnings=(),
        )
    )


def test_release_discovery_keeps_the_helper_refusal_as_a_safety_outcome() -> None:
    """The controller does not attempt an old snapshot after a helper refusal."""

    with pytest.raises(OpsError) as raised:
        list_releases(
            object(), _config(),
            invoker=lambda _remote, _package, request: _refused(request, "list_releases"),
        )

    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.stage == "lifecycle-records"


def test_backup_discovery_keeps_the_helper_refusal_as_a_safety_outcome() -> None:
    """A refusal is terminal evidence rather than a cue for controller rereads."""

    with pytest.raises(OpsError) as raised:
        list_backups(
            object(), _config(),
            invoker=lambda _remote, _package, request: _refused(request, "list_backups"),
        )

    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.stage == "lifecycle-records"
