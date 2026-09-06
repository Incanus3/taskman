"""Controller contracts for helper-owned exact cleanup."""

from __future__ import annotations

from typing import Any

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.cleanup import cleanup


BACKUP = "backup-" + "a" * 32
TARGET = {
    "authority": None, "identifier": BACKUP, "kind": "backup",
    "path": f"/var/backups/taskman/{BACKUP}.dump", "recoverable": False,
}


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate({
        "name": "production", "ssh_host": "203.0.113.10", "ssh_port": 22,
        "ssh_user": "deployer", "host_key_fingerprint": "SHA256:" + "A" * 43,
        "public_hostname": "taskman.acme.tld", "public_ipv4": "203.0.113.10",
        "target_os": "ubuntu26.04", "architecture": "amd64",
        "application_port": 4000, "distribution_port": 6789,
        "database_name": "taskman_prod", "database_role": "taskman",
        "mail_from": "no-reply@acme.tld",
    })


def _discovery() -> dict[str, object]:
    return {
        "records": {
            "releases": ({"release_id": "release"},),
            "activations": ({"activation_id": "activation-" + "a" * 32},),
            "backups": ({"backup_id": BACKUP},), "adoptions": (),
        }
    }


def _inspection(
    request: Any, warnings: tuple[str, ...] = ()
) -> HostResult:
    return HostResult(
        1, "cleanup", request.operation_id, "succeeded", "cleanup-inspected", (),
        {
            "lifecycle": request.expected_state["lifecycle"],
            "targets": (TARGET,),
        },
        {}, {}, (), (), warnings,
    )


def test_cleanup_confirms_exact_plan_then_uses_a_fresh_execute_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.discover_lifecycle",
        lambda *_args: (_discovery(), ("discovery cleanup warning",)),
    )

    def run(_remote: object, request: Any) -> HostResult:
        requests.append(request)
        if request.parameters["action"] == "inspect":
            return _inspection(request, ("inspection cleanup warning",))
        return HostResult(
            1, "cleanup", request.operation_id, "succeeded", "cleanup",
            ("cleanup",),
            {"targets": (TARGET,), "removed": (TARGET,), "recoverability": (False,)},
            {}, {}, (), (), ("execution cleanup warning",),
        )

    monkeypatch.setattr("taskman_ops.workflows.cleanup.run_request", run)
    result = cleanup(object(), _config(), confirm=lambda plan: (
        plan["typed_confirmation"] == f"cleanup production {BACKUP}"
    ))

    assert result.stage == "cleaned"
    assert [item.parameters["action"] for item in requests] == ["inspect", "execute"]
    assert requests[0].operation_id != requests[1].operation_id
    assert requests[1].parameters["targets"] == (TARGET,)
    assert result.warnings == (
        "discovery cleanup warning",
        "inspection cleanup warning",
        "execution cleanup warning",
    )


def test_cleanup_dry_run_inspects_but_does_not_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.run_request",
        lambda _remote, request: requests.append(request) or _inspection(request),
    )

    result = cleanup(object(), _config(), dry_run=True)

    assert result.stage == "planned"
    assert len(requests) == 1
    assert requests[0].parameters["action"] == "inspect"


def test_cleanup_surfaces_stale_confirmation_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        if request.parameters["action"] == "inspect":
            return _inspection(request)
        return HostResult(
            1, "cleanup", request.operation_id, "refused", "cleanup-preflight",
            (), {}, {}, {}, (),
            ("recompute an exact cleanup plan before retrying",), (),
        )

    monkeypatch.setattr("taskman_ops.workflows.cleanup.run_request", run)
    result = cleanup(object(), _config(), confirm=lambda _plan: True)

    assert result.stage == "safety-refused"
    assert result.changed is False


def test_cleanup_preserves_uncertain_database_failure_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The controller must not discard the helper's post-drop uncertainty."""

    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        if request.parameters["action"] == "inspect":
            return _inspection(request)
        return HostResult(
            1,
            "cleanup",
            request.operation_id,
            "failed",
            "cleanup",
            ("cleanup",),
            {
                "targets": (TARGET,),
                "removed": (),
                "recoverability": (),
                "database_state": "unknown",
            },
            {},
            {},
            ("/database/recovery", "/safe/restore-record"),
            ("prove whether the exact recovery database exists",),
            ("unable to remove operation residue",),
        )

    monkeypatch.setattr("taskman_ops.workflows.cleanup.run_request", run)

    result = cleanup(object(), _config(), confirm=lambda _plan: True)

    assert result.stage == "safety-refused"
    assert result.changed is True
    assert result.facts["database_state"] == "unknown"
