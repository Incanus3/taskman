"""Controller contracts for helper-owned explicit backup."""

from __future__ import annotations

from typing import Any

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostResult
from taskman_ops.output import clear_secrets, register_secret, render_json
from taskman_ops.workflows.backup import run_backup


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
            "releases": (), "activations": (),
            "backups": ({"backup_id": "backup-" + "a" * 32},),
            "adoptions": (),
        }
    }


def test_backup_sends_one_policy_request_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.discover_lifecycle",
        lambda *_args: (_discovery(), ("discovery warning",)),
    )

    def run(_remote: object, request: Any) -> HostResult:
        requests.append(request)
        token = request.operation_id.removeprefix("op-")
        identifier = f"backup-{token}"
        return HostResult(
            1, "backup", request.operation_id, "succeeded", "records",
            ("backup", "records"),
            {
                "backup_id": identifier,
                "dump_path": f"/var/backups/taskman/{identifier}.dump",
                "size_bytes": 17, "source_database_size_bytes": 1024,
                "reason": "scheduled", "pruned_backup_ids": (),
            },
            {}, {"format": "custom", "validated": True}, (), (), (),
        )

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", run)
    result = run_backup(object(), _config())

    assert result.stage == "backed-up"
    assert result.changed is True
    assert len(requests) == 1
    assert requests[0].parameters["credentials_path"] == "/etc/taskman/pgpass"
    assert "password" not in repr(requests[0]).lower()


def test_backup_dry_run_discovers_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.run_request",
        lambda *_args: pytest.fail("dry-run sent a mutating request"),
    )

    result = run_backup(object(), _config(), dry_run=True)

    assert result.stage == "planned"
    assert result.changed is False


def test_backup_rejects_uncorrelated_success_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.run_request",
        lambda _remote, request: HostResult(
            1, "backup", request.operation_id, "succeeded", "records",
            ("backup", "records"),
            {
                "backup_id": "backup-" + "f" * 32,
                "dump_path": "/var/backups/taskman/backup-" + "f" * 32 + ".dump",
                "size_bytes": 17, "source_database_size_bytes": 1024,
                "reason": "scheduled", "pruned_backup_ids": (),
            },
            {}, {"format": "custom", "validated": True}, (), (), (),
        ),
    )

    result = run_backup(object(), _config())

    assert result.stage == "safety-refused"
    assert result.changed is False


@pytest.mark.parametrize(
    ("outcome", "stages", "pruned", "residue"),
    (
        ("succeeded", (), (), ()),
        ("succeeded", ("cleanup",), (), ()),
        ("succeeded", ("backup", "records"), ("backup-" + "b" * 32,), ()),
        ("no_change", (), ("backup-" + "b" * 32,), ()),
        ("no_change", (), (), ("/var/backups/taskman/residue",)),
    ),
)
def test_backup_rejects_incoherent_success_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    stages: tuple[str, ...],
    pruned: tuple[str, ...],
    residue: tuple[str, ...],
) -> None:
    """Outcome, stages, pruning evidence, and residue must describe one exact state."""

    monkeypatch.setattr(
        "taskman_ops.workflows.backup.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        identifier = f"backup-{request.operation_id.removeprefix('op-')}"
        return HostResult(
            1,
            "backup",
            request.operation_id,
            outcome,
            "records",
            stages,
            {
                "backup_id": identifier,
                "dump_path": f"/var/backups/taskman/{identifier}.dump",
                "size_bytes": 17,
                "source_database_size_bytes": 1024,
                "reason": "scheduled",
                "pruned_backup_ids": pruned,
            },
            {},
            {"format": "custom", "validated": True},
            residue,
            (),
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", run)

    result = run_backup(object(), _config())

    assert result.stage == "safety-refused"
    assert result.changed is False


@pytest.mark.parametrize(
    "changed_stages",
    (
        ("cleanup",),
        ("backup", "records", "cleanup"),
    ),
    ids=("cleanup-only", "new-backup-and-cleanup"),
)
def test_backup_failure_reports_partial_pruning_and_redacts_its_warning(
    monkeypatch: pytest.MonkeyPatch,
    changed_stages: tuple[str, ...],
) -> None:
    """Workflow failure facts must retain helper-proved completed pruning."""

    canary = "partial-prune-warning-canary"
    pruned = "backup-" + "a" * 32
    surviving = "backup-" + "b" * 32
    residue = (
        "/opt/taskman/deployments/backups/"
        + surviving
        + ".json"
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.discover_lifecycle",
        lambda *_args: (
            {
                "records": {
                    "releases": (),
                    "activations": (),
                    "backups": (
                        {"backup_id": pruned},
                        {"backup_id": surviving},
                    ),
                    "adoptions": (),
                }
            },
            (),
        ),
    )

    def run(_remote: object, request: Any) -> HostResult:
        return HostResult(
            1,
            "backup",
            request.operation_id,
            "failed",
            "cleanup",
            changed_stages,
            {"pruned_backup_ids": (pruned,)},
            {},
            {},
            (residue,),
            ("inspect exact backup residue before retrying",),
            (f"cleanup warning: {canary}",),
        )

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", run)
    register_secret(canary)
    try:
        result = run_backup(object(), _config())
        rendered = render_json(result)
    finally:
        clear_secrets()

    assert result.changed is True
    assert result.exit_status is ExitStatus.BACKUP
    assert result.stage == "cleanup-failed"
    assert result.facts["pruned_backup_ids"] == (pruned,)
    assert result.facts["changed_stages"] == changed_stages
    assert result.facts["residue_paths"] == (residue,)
    assert canary not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.parametrize(
    "lifecycle",
    (
        {"pruned_backup_ids": ()},
        {
            "pruned_backup_ids": (
                "backup-" + "a" * 32,
                "backup-" + "a" * 32,
            )
        },
        {"pruned_backup_ids": ("invalid-prune-lifecycle-canary",)},
        {"pruned_backup_ids": ("backup-" + "c" * 32,)},
        {
            "pruned_backup_ids": (
                {"nested_canary": "invalid-prune-lifecycle-canary"},
            )
        },
        {
            "pruned_backup_ids": (
                ["invalid-prune-lifecycle-canary"],
            )
        },
        {
            "pruned_backup_ids": ("backup-" + "b" * 32,),
            "unexpected": "invalid-prune-lifecycle-canary",
        },
    ),
    ids=(
        "empty",
        "duplicate",
        "invalid-id",
        "unknown-id",
        "nested-dict",
        "nested-list",
        "unexpected-field",
    ),
)
def test_backup_failure_rejects_malformed_pruning_lifecycle_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
    lifecycle: dict[str, object],
) -> None:
    """Untrusted failure lifecycle must not become operator-facing facts."""

    canary = "invalid-prune-lifecycle-canary"
    residue = "/var/backups/taskman/backup-" + "a" * 32 + ".dump"
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.run_request",
        lambda _remote, request: HostResult(
            1,
            "backup",
            request.operation_id,
            "failed",
            "cleanup",
            ("backup", "records", "cleanup"),
            lifecycle,
            {},
            {},
            (residue,),
            (f"unsafe recovery {canary}",),
            (f"unsafe warning {canary}",),
        ),
    )
    register_secret(canary)
    try:
        result = run_backup(object(), _config())
        rendered = render_json(result)
    finally:
        clear_secrets()

    assert result.changed is True
    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"
    assert result.facts["pruned_backup_ids"] == ()
    assert result.facts["changed_stages"] == (
        "backup",
        "records",
        "cleanup",
    )
    assert result.facts["residue_paths"] == (residue,)
    assert result.next_action == "inspect managed backup lifecycle before retrying"
    assert canary not in rendered


@pytest.mark.parametrize(
    "changed_stages",
    (
        ("cleanup", "backup"),
        ("records", "cleanup"),
        ("backup", "cleanup"),
        ("backup", "records"),
        ("backup", "records", "cleanup", "verify"),
        ("backup", "backup", "records", "cleanup"),
    ),
    ids=(
        "reordered",
        "missing-backup",
        "missing-records",
        "missing-cleanup",
        "extra-stage",
        "duplicate-stage",
    ),
)
def test_backup_failure_rejects_noncanonical_partial_prune_stage_plane(
    monkeypatch: pytest.MonkeyPatch,
    changed_stages: tuple[str, ...],
) -> None:
    """Completed-prune evidence is authoritative only on exact legal stage planes."""

    canary = "invalid-prune-stage-canary"
    pruned = "backup-" + "a" * 32
    residue = "/var/backups/taskman/backup-" + "b" * 32 + ".dump"
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.discover_lifecycle",
        lambda *_args: (_discovery(), ()),
    )

    def run(_remote: object, request: Any) -> HostResult:
        if len(set(changed_stages)) != len(changed_stages):
            error = OpsError(
                ExitStatus.BACKUP,
                "cleanup",
                "backup helper did not complete",
                True,
                f"unsafe recovery {canary}",
            )
            error.lifecycle = {"pruned_backup_ids": (pruned,)}
            error.changed_stages = changed_stages
            error.residue_paths = (residue,)
            error.recovery_commands = (f"unsafe recovery {canary}",)
            error.warnings = (f"unsafe warning {canary}",)
            raise error
        return HostResult(
            1,
            "backup",
            request.operation_id,
            "failed",
            "cleanup",
            changed_stages,
            {"pruned_backup_ids": (pruned,)},
            {},
            {},
            (residue,),
            (f"unsafe recovery {canary}",),
            (f"unsafe warning {canary}",),
        )

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", run)
    register_secret(canary)
    try:
        result = run_backup(object(), _config())
        rendered = render_json(result)
    finally:
        clear_secrets()

    assert result.changed is True
    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"
    assert result.facts["pruned_backup_ids"] == ()
    assert result.facts["changed_stages"] == changed_stages
    assert result.facts["residue_paths"] == (residue,)
    assert result.next_action == "inspect managed backup lifecycle before retrying"
    assert canary not in rendered
