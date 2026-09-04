"""Explicit, application-state-neutral local database backup workflow."""

from __future__ import annotations

from ..output import WorkflowResult
from ..remote import Remote
from ..services.backups import BackupContext, create_backup
from .operational_preflight import validate_operational_preflight


def run_backup(remote: Remote, context: BackupContext, *, dry_run: bool = False) -> WorkflowResult:
    """Create one scheduled backup through the host's exclusive transaction.

    The root asset performs the database health/capacity preflight and takes
    the lifecycle lock itself.  This workflow deliberately performs no
    Taskman stop, start, migration, release selection, or separate lock call.
    """

    if not isinstance(dry_run, bool):
        raise TypeError("backup dry-run flag must be boolean")
    validate_operational_preflight(remote, context.config)
    if dry_run:
        records, _snapshot = context.store.read(
            operation="backup",
            lock_timeout_seconds=context.lock_timeout_seconds,
        )
        return WorkflowResult(
            command="backup",
            environment=context.config.environment or "",
            changed=False,
            stage="planned",
            facts={
                "current_release_id": records.current_release_id,
                "planned_backup": True,
                "reason": "scheduled",
            },
            next_action="review the backup plan and rerun without --dry-run",
        )

    record = create_backup(remote, context, "scheduled")
    return WorkflowResult(
        command="backup",
        environment=context.config.environment or "",
        changed=True,
        stage="backed-up",
        facts={
            "backup_id": record.backup_id,
            "dump_path": record.dump_path.as_posix(),
            "size_bytes": record.size_bytes,
            "source_database_size_bytes": record.source_database_size_bytes,
            "reason": record.reason,
        },
        next_action="copy the validated local backup off-host according to the recovery policy",
    )


__all__ = ["run_backup"]
