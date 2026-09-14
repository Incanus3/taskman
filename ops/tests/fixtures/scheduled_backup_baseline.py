"""Frozen supported-baseline scheduled-backup fixture.

This intentionally narrow, standard-library-only zipapp source represents an
already-running earlier scheduled backup.  It validates the supported release,
selection, backup, and protection record shapes needed by retention; it is not
a second copy of the live backup implementation.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
import re
import sys


_RELEASE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}-ubuntu26\.04-amd64-otp[0-9.]+-[0-9a-f]{64}\Z")
_BACKUP = re.compile(r"backup-[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE_FIELDS = frozenset(
    {"release_id", "source_revision", "artifact_sha256", "migrations", "schema_version", "artifact_manifest"}
)
_SELECTION_FIELDS = frozenset(
    {
        "release_id",
        "previous_release_id",
        "backup_id",
        "selected_at",
        "schema_version",
        "observed_previous_release_id",
        "recovery_backup_ids",
    }
)
_BACKUP_FIELDS = frozenset(
    {"backup_id", "created_at", "dump_sha256", "source_release_id", "migration_versions", "source_database_size_bytes"}
)
_PROTECTION_FIELDS = frozenset(
    {"schema_version", "backup_id", "base_selection_id", "target_release_id", "attempt_number", "created_at"}
)


def _mapping(value: object, fields: frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields or not all(type(key) is str for key in value):
        raise ValueError(f"invalid {label}")
    return value


def _release(value: object) -> dict[str, object]:
    record = _mapping(value, _RELEASE_FIELDS, "release record")
    if (
        record["schema_version"] != 2
        or type(record["release_id"]) is not str
        or _RELEASE.fullmatch(record["release_id"]) is None
        or type(record["source_revision"]) is not str
        or len(record["source_revision"]) not in {40, 64}
        or type(record["artifact_sha256"]) is not str
        or _SHA256.fullmatch(record["artifact_sha256"]) is None
        or not isinstance(record["migrations"], list)
        or not isinstance(record["artifact_manifest"], dict)
    ):
        raise ValueError("invalid release record")
    return record


def _backup_id(value: object, label: str) -> str:
    if type(value) is not str or _BACKUP.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _selection(value: object, release_id: str) -> dict[str, object]:
    record = _mapping(value, _SELECTION_FIELDS, "selection record")
    if record["schema_version"] != 2 or record["release_id"] != release_id or type(record["selected_at"]) is not str:
        raise ValueError("invalid selection record")
    if record["backup_id"] is not None:
        _backup_id(record["backup_id"], "selection backup")
    recovery = record["recovery_backup_ids"]
    if not isinstance(recovery, list) or recovery != sorted(set(recovery)):
        raise ValueError("invalid selection recovery backups")
    for backup_id in recovery:
        _backup_id(backup_id, "selection recovery backup")
    return record


def _backups(value: object, release_id: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("invalid backup records")
    result = []
    for item in value:
        record = _mapping(item, _BACKUP_FIELDS, "backup record")
        _backup_id(record["backup_id"], "backup identifier")
        if (
            type(record["created_at"]) is not str
            or type(record["dump_sha256"]) is not str
            or _SHA256.fullmatch(record["dump_sha256"]) is None
            or record["source_release_id"] != release_id
        ):
            raise ValueError("invalid backup record")
        result.append(record)
    return result


def _protections(value: object, release_id: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("invalid backup protections")
    result = []
    for item in value:
        record = _mapping(item, _PROTECTION_FIELDS, "backup protection")
        if record["schema_version"] != 1 or record["target_release_id"] != release_id:
            raise ValueError("invalid backup protection")
        _backup_id(record["backup_id"], "protected backup")
        result.append(record)
    return result


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    release = _release(payload["release"])
    release_id = str(release["release_id"])
    selection = _selection(payload["selection"], release_id)
    backups = _backups(payload["backups"], release_id)
    protections = _protections(payload["protections"], release_id)
    retention = payload["retention"]
    if type(retention) is not int or not 1 <= retention <= 64:
        raise ValueError("invalid backup retention")

    install_root = Path(payload["install_root"])
    install_root.mkdir(mode=0o750, parents=True, exist_ok=True)
    print("waiting-for-lock", flush=True)
    with (install_root / "lifecycle.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        retained = set(selection["recovery_backup_ids"])
        if selection["backup_id"] is not None:
            retained.add(selection["backup_id"])
        retained.update(item["backup_id"] for item in protections)
        unprotected = [item for item in backups if item["backup_id"] not in retained]
        newest = sorted(unprotected, key=lambda item: (item["created_at"], item["backup_id"]), reverse=True)
        retained.update(item["backup_id"] for item in newest[:retention])
    print(
        json.dumps(
            {
                "release_id": release_id,
                "selected_release_id": selection["release_id"],
                "retained_backup_ids": sorted(retained),
            },
            sort_keys=True,
        )
    )
    return 0


raise SystemExit(main())
