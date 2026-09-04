"""The explicit backup workflow's one-transaction remote contract."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest
import json

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.remote import CommandResult
from taskman_ops.releases.records import RemoteLifecycleStore
from taskman_ops.services.backups import BackupContext, create_backup
from taskman_ops.workflows.backup import run_backup


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.validate_operational_preflight",
        lambda *_args: object(),
    )


class RecordingRemote:
    def __init__(self, response: CommandResult) -> None:
        self.response = response
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((tuple(argv), kwargs))
        return self.response


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@example.test",
        }
    )


def test_create_backup_uses_the_root_asset_as_the_one_exclusive_remote_lifecycle_transaction() -> None:
    """Adding a controller lock wrapper or omitting the asset invocation must fail this."""

    payload = (
        '{"backup_id":"backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","candidate_release_id":null,'
        '"created_at":"2026-09-05T12:00:00Z","current_release_id":null,"database":"taskman_prod",'
        '"dump_path":"/var/backups/taskman/backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.dump",'
        '"reason":"scheduled","schema_version":1,"size_bytes":17,"source_database_size_bytes":1048576,"validated":true}\n'
    )
    remote = RecordingRemote(CommandResult(0, payload))
    config = _config()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    record = create_backup(remote, BackupContext(config, store), "scheduled")

    assert record.backup_id == "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    argv, kwargs = remote.calls[0]
    assert argv[0] == "/usr/local/lib/taskman/taskman-backup"
    assert "--lock-held" not in argv
    assert kwargs == {"sudo": True, "stdin": None, "sensitive": False}
    assert "--database-name" in argv
    assert "taskman_prod" in argv


def test_explicit_backup_workflow_reports_the_authoritative_record_without_changing_taskman_state() -> None:
    """Dropping the exact backup facts or adding service lifecycle calls must fail this."""

    payload = (
        '{"backup_id":"backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","candidate_release_id":null,'
        '"created_at":"2026-09-05T12:00:00Z","current_release_id":null,"database":"taskman_prod",'
        '"dump_path":"/var/backups/taskman/backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.dump",'
        '"reason":"scheduled","schema_version":1,"size_bytes":17,"source_database_size_bytes":1048576,"validated":true}\n'
    )
    remote = RecordingRemote(CommandResult(0, payload))
    config = _config()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    result = run_backup(remote, BackupContext(config, store))

    assert result.command == "backup"
    assert result.environment == "production"
    assert result.changed is True
    assert result.stage == "backed-up"
    assert result.facts == {
        "backup_id": "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "dump_path": "/var/backups/taskman/backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.dump",
        "size_bytes": 17,
        "source_database_size_bytes": 1_048_576,
        "reason": "scheduled",
    }
    assert len(remote.calls) == 1


def test_explicit_backup_dry_run_discovers_lifecycle_state_without_creating_a_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling the backup asset in dry-run would create a database dump."""

    from taskman_ops.workflows.operational_preflight import (
        validate_operational_preflight as validate_real_preflight,
    )

    snapshot = {
        "schema_version": 1,
        "records": {
            "releases": [],
            "activations": [],
            "backups": [],
            "adoptions": [],
        },
        "current_target": None,
        "manifests": {},
        "dump_states": {},
        "warnings": [],
    }
    remote = RecordingRemote(CommandResult(0, json.dumps(snapshot)))
    config = _config()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.validate_operational_preflight",
        lambda actual_remote, actual_config: validate_real_preflight(
            actual_remote,
            actual_config,
            host_validator=lambda *_args: object(),
        ),
    )

    result = run_backup(remote, BackupContext(config, store), dry_run=True)

    assert result.changed is False
    assert result.stage == "planned"
    assert result.facts == {
        "current_release_id": None,
        "planned_backup": True,
        "reason": "scheduled",
    }
    assert len(remote.calls) == 3
    assert "/etc/taskman/taskman.env" in remote.calls[0][0]
    assert "pg_database_size" in remote.calls[1][0][2]
    assert remote.calls[2][0][0:2] == ("sh", "-ceu")
    assert all(
        "/usr/local/lib/taskman/taskman-backup" not in call[0]
        for call in remote.calls
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected_status"),
    [
        (
            ExitStatus.LOCKED,
            '{"schema_version":1,"holder":{"operation":"snapshot","pid":42,'
            '"started_at":"2026-09-05T12:00:00Z","mode":"shared"}}',
            ExitStatus.LOCKED,
        ),
        (
            ExitStatus.LOCKED,
            '{"schema_version":1,"holder":{"operation":"Snapshot!","pid":42,'
            '"started_at":"2026-09-05T12:00:00Z","mode":"exclusive"}}',
            ExitStatus.SAFETY,
        ),
        (
            ExitStatus.LOCKED,
            '{"schema_version":1,"holder":{"operation":"snapshot","pid":42,'
            '"started_at":"2026-02-30T12:00:00Z","mode":"exclusive"}}',
            ExitStatus.SAFETY,
        ),
        (ExitStatus.SAFETY, "remote state canary", ExitStatus.SAFETY),
        (ExitStatus.BACKUP, "database canary", ExitStatus.BACKUP),
    ],
)
def test_create_backup_preserves_declared_asset_statuses_and_uses_task_six_holder_validation(
    returncode: ExitStatus, stdout: str, expected_status: ExitStatus
) -> None:
    """A shared holder is valid contention; malformed holders and safety exits remain safety failures."""

    remote = RecordingRemote(CommandResult(returncode, stdout))
    config = _config()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    with pytest.raises(OpsError) as raised:
        create_backup(remote, BackupContext(config, store), "scheduled")

    assert raised.value.status == expected_status
