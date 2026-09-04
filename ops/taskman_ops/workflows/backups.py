"""Read-only backup discovery without revalidating dump contents."""

from __future__ import annotations

from collections.abc import Mapping

from ..errors import ExitStatus, OpsError
from ..releases.records import RemoteLifecycleStore
from . import DiscoveryResult


def list_backups(store: RemoteLifecycleStore, *, lock_timeout_seconds: float = 5) -> DiscoveryResult:
    """Return retained backup metadata and whether each dump is still regular.

    The function intentionally does not call ``pg_restore --list``: validation
    recorded when the dump was created remains evidence for listing, while the
    destructive restore workflow will always perform a fresh validation.
    """

    if not isinstance(store, RemoteLifecycleStore):
        raise TypeError("backup discovery requires a remote lifecycle store")
    records, snapshot = store.read(operation="backups", lock_timeout_seconds=lock_timeout_seconds)
    dump_states = snapshot["dump_states"]
    if not isinstance(dump_states, Mapping):
        raise _safety("remote lifecycle dump states are invalid")
    warnings = list(records.warnings)
    rows: list[dict[str, object]] = []
    for backup in sorted(records.backups, key=lambda item: (item.created_at, item.backup_id), reverse=True):
        dump_state = dump_states.get(backup.dump_path.as_posix())
        if dump_state not in {"present", "stale"}:
            raise _safety("remote lifecycle dump state is invalid")
        if dump_state != "present":
            warnings.append(f"backup metadata is stale: {backup.backup_id}")
        rows.append(
            {
                "backup_id": backup.backup_id,
                "created_at": backup.created_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "size_bytes": backup.size_bytes,
                "source_database_size_bytes": backup.source_database_size_bytes,
                "database": backup.database,
                "current_release_id": backup.current_release_id,
                "candidate_release_id": backup.candidate_release_id,
                "reason": backup.reason,
                "validated": backup.validated,
                "dump_path": backup.dump_path.as_posix(),
                "dump_state": dump_state,
            }
        )
    return DiscoveryResult(tuple(rows), tuple(sorted(warnings)))


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "backup-discovery",
        message,
        changed=False,
        next_action="resolve the managed lifecycle metadata before selecting a backup",
    )


__all__ = ["list_backups"]
