from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.backup import _success


def test_backup_success_consumes_only_final_state() -> None:
    result = HostResult(
        2, "backup", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {
            "changed": True,
            "backup_id": "backup-0123456789abcdef0123456789abcdef",
            "dump_path": "/var/backups/taskman/backup-0123456789abcdef0123456789abcdef.dump",
            "size_bytes": 1,
            "source_database_size_bytes": 1,
            "reason": "scheduled",
            "pruned_backup_ids": (),
        },
        (),
    )

    assert _success(result)["backup_id"] == "backup-0123456789abcdef0123456789abcdef"
