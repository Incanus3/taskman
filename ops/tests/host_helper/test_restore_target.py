"""Restore-target binding codecs and durable intent publication."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from tests.host_helper.support import managed_paths

from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.restore_target import (
    RestoreTarget,
    replace_restore_target,
    restore_target_sha256,
    write_restore_target,
)


BACKUP = "backup-" + "a" * 32
SECOND_BACKUP = "backup-" + "b" * 32
TARGET = "0.2.0-" + "c" * 12 + "-ubuntu26.04-amd64-otp29.0.6-" + "d" * 64
SELECTION = "selection-" + "e" * 64 + ".json"
AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _target(**changes: object) -> RestoreTarget:
    values: dict[str, object] = {
        "schema_version": 1,
        "backup_id": BACKUP,
        "dump_sha256": "f" * 64,
        "source_release_id": TARGET,
        "base_selection_id": SELECTION,
        "observed_previous_release_id": TARGET,
        "original_database_oid": 101,
        "restored_database_oid": None,
        "temporary_creation_pending": True,
        "safety_backup_id": SECOND_BACKUP,
        "replacement": None,
        "safety_backup_attempts": (
            {"backup_id": SECOND_BACKUP, "attempt_number": 0},
        ),
    }
    values.update(changes)
    return RestoreTarget(**values)  # type: ignore[arg-type]


def test_restore_target_has_exact_persisted_fields_and_round_trips() -> None:
    record = _target()

    assert record.to_mapping() == {
        "schema_version": 1,
        "backup_id": BACKUP,
        "dump_sha256": "f" * 64,
        "source_release_id": TARGET,
        "base_selection_id": SELECTION,
        "observed_previous_release_id": TARGET,
        "original_database_oid": 101,
        "restored_database_oid": None,
        "temporary_creation_pending": True,
        "safety_backup_id": SECOND_BACKUP,
        "replacement": None,
        "safety_backup_attempts": [
            {"backup_id": SECOND_BACKUP, "attempt_number": 0},
        ],
    }
    assert RestoreTarget.from_mapping(record.to_mapping()) == record
    assert restore_target_sha256(record) == restore_target_sha256(record.to_mapping())


@pytest.mark.parametrize(
    "changes",
    (
        {"schema_version": 2},
        {"backup_id": "backup-invalid"},
        {"dump_sha256": "bad"},
        {"source_release_id": "source-only-release"},
        {"base_selection_id": "selection-invalid.json"},
        {"original_database_oid": 0},
        {"original_database_oid": True},
        {"restored_database_oid": -1},
        {"temporary_creation_pending": 1},
        {"replacement": {"backup_id": BACKUP}},
        {"safety_backup_attempts": [{"backup_id": SECOND_BACKUP, "attempt_number": 1}]},
        {"safety_backup_attempts": [
            {"backup_id": SECOND_BACKUP, "attempt_number": 0},
            {"backup_id": SECOND_BACKUP, "attempt_number": 1},
        ]},
        {"safety_backup_attempts": [
            {"backup_id": SECOND_BACKUP, "attempt_number": 1},
            {"backup_id": BACKUP, "attempt_number": 0},
        ]},
        {"unexpected": "not persisted"},
    ),
)
def test_restore_target_rejects_invalid_or_extra_fields(changes: dict[str, object]) -> None:
    mapping = _target().to_mapping()
    mapping.update(changes)

    with pytest.raises((TypeError, ValueError)):
        RestoreTarget.from_mapping(mapping)


def test_restore_target_requires_distinct_registered_database_oids() -> None:
    with pytest.raises(ValueError, match="OID|database"):
        _target(restored_database_oid=101, temporary_creation_pending=False)


def test_restore_target_accepts_64_safety_attempts_and_rejects_65() -> None:
    attempts = tuple(
        {"backup_id": f"backup-{index:032x}", "attempt_number": index}
        for index in range(64)
    )
    record = _target(
        safety_backup_id=attempts[0]["backup_id"],
        safety_backup_attempts=attempts,
    )

    assert len(record.safety_backup_attempts) == 64
    with pytest.raises(ValueError, match="attempt|64|bounded"):
        _target(
            safety_backup_id=attempts[0]["backup_id"],
            safety_backup_attempts=(*attempts, {"backup_id": "backup-" + "f" * 32, "attempt_number": 64}),
        )


def test_restore_target_replacement_has_an_exact_discard_intent() -> None:
    replacement = {
        "backup_id": BACKUP,
        "dump_sha256": "1" * 64,
        "source_release_id": TARGET,
        "discard_database_oid": 202,
    }

    record = _target(restored_database_oid=202, replacement=replacement)

    assert record.replacement == replacement
    assert RestoreTarget.from_mapping(record.to_mapping()) == record


def test_restore_target_is_created_once_and_replaced_atomically(tmp_path: Path) -> None:
    paths: ManagedPaths = managed_paths(tmp_path)
    first = _target()
    replacement = _target(restored_database_oid=202, temporary_creation_pending=False)

    write_restore_target(paths, first)
    replace_restore_target(paths, replacement)

    target = Path(paths.local(paths.restore_target_path))
    assert target.is_file()
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_text(encoding="utf-8")) == replacement.to_mapping()
    assert not list(target.parent.glob(".*.tmp"))


def test_restore_target_serializer_enforces_the_other_record_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import taskman_ops.host_helper.restore_target as target_module

    monkeypatch.setattr(target_module, "MAX_RECORD_BYTES", 1)
    with pytest.raises(ValueError, match="oversized|size|restore"):
        RestoreTarget.from_mapping(_target().to_mapping())
