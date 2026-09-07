from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.backup import _success


def test_backup_success_translates_only_final_backup_facts() -> None:
    """Restoring lifecycle evidence here would make the controller a second host-state model."""

    result = HostResult(
        2,
        "backup",
        "op-0123456789abcdef0123456789abcdef",
        "succeeded",
        "completed",
        {
            "backup_id": "backup-0123456789abcdef0123456789abcdef",
            "dump_path": "/var/backups/taskman/backup-0123456789abcdef0123456789abcdef.dump",
            "size_bytes": 1,
            "source_database_size_bytes": 1,
            "selected_release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
            "service_state": "running",
            "database_state": "ready",
        },
        (),
    )

    facts = _success(result)

    assert facts["backup_id"] == "backup-0123456789abcdef0123456789abcdef"
    assert "pruned_backup_ids" not in facts
