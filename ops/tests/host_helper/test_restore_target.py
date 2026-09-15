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
    allocate_safety_attempt,
    append_safety_attempt,
    retire_safety_attempts,
    safety_attempt_prune_ids,
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


def test_restore_target_accepts_more_than_64_safety_attempts_for_bounded_recovery() -> None:
    attempts = tuple(
        {"backup_id": f"backup-{index:032x}", "attempt_number": index}
        for index in range(64)
    )
    record = _target(
        safety_backup_id=attempts[-1]["backup_id"],
        safety_backup_attempts=attempts,
    )

    extended = _target(
        safety_backup_id="backup-" + "f" * 32,
        safety_backup_attempts=(*attempts, {"backup_id": "backup-" + "f" * 32, "attempt_number": 64}),
    )

    assert len(record.safety_backup_attempts) == 64
    assert len(extended.safety_backup_attempts) == 65


def test_safety_attempt_retention_uses_attempt_order_and_retires_independent_entries() -> None:
    """Restore safety attempts retain five references plus one transient fresh copy."""

    attempts = tuple(
        {"backup_id": f"backup-{index:032x}", "attempt_number": index}
        for index in range(7)
    )
    record = _target(
        safety_backup_id=attempts[-1]["backup_id"],
        safety_backup_attempts=attempts,
    )

    assert allocate_safety_attempt(record) == 7
    assert safety_attempt_prune_ids(
        record,
        independently_held_backup_ids={"backup-00000000000000000000000000000002"},
    ) == (
        "backup-00000000000000000000000000000001",
        "backup-00000000000000000000000000000002",
    )


def test_current_original_safety_reference_is_never_returned_for_retirement() -> None:
    """A newer failed-restored copy must not make the required original copy removable."""

    attempts = tuple(
        {"backup_id": f"backup-{index:032x}", "attempt_number": index}
        for index in range(7)
    )
    record = _target(
        safety_backup_id=attempts[2]["backup_id"],
        safety_backup_attempts=attempts,
    )

    assert record.safety_backup_id not in safety_attempt_prune_ids(record)


def test_safety_attempt_reference_is_published_before_exact_retirement(tmp_path: Path) -> None:
    """A failed fresh copy cannot remove an older safety attempt."""

    paths = managed_paths(tmp_path)
    first = _target()
    write_restore_target(paths, first)
    fresh = append_safety_attempt(
        first,
        "backup-00000000000000000000000000000002",
        promote_original=True,
    )

    updated = retire_safety_attempts(paths, fresh, ())

    persisted = RestoreTarget.from_mapping(
        json.loads(Path(paths.local(paths.restore_target_path)).read_text(encoding="utf-8"))
    )
    assert updated == persisted
    assert updated.safety_backup_id == "backup-00000000000000000000000000000002"
    assert updated.safety_backup_attempts[0]["backup_id"] == SECOND_BACKUP
    assert tuple(item["attempt_number"] for item in updated.safety_backup_attempts) == (0, 1)


def test_sixty_five_safety_attempts_converge_to_original_newest_and_three_recent(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    record = _target()
    write_restore_target(paths, record)

    for attempt_number in range(1, 66):
        record = append_safety_attempt(
            record,
            f"backup-{attempt_number + 1:032x}",
            promote_original=True,
        )
        replace_restore_target(paths, record)
        record = retire_safety_attempts(paths, record, safety_attempt_prune_ids(record))

    assert tuple(item["attempt_number"] for item in record.safety_backup_attempts) == (
        0,
        62,
        63,
        64,
        65,
    )
    persisted = RestoreTarget.from_mapping(
        json.loads(Path(paths.local(paths.restore_target_path)).read_text(encoding="utf-8"))
    )
    assert persisted == record


def test_independently_held_intermediates_do_not_make_attempts_unbounded(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    attempts = tuple(
        {"backup_id": f"backup-{index:032x}", "attempt_number": index}
        for index in range(67)
    )
    record = _target(
        safety_backup_id=attempts[0]["backup_id"],
        safety_backup_attempts=attempts,
    )
    independently_held = {
        str(item["backup_id"])
        for item in attempts[1:-1]
    }
    write_restore_target(paths, record)

    prune_ids = safety_attempt_prune_ids(
        record,
        independently_held_backup_ids=independently_held,
    )
    updated = retire_safety_attempts(
        paths,
        record,
        prune_ids,
        independently_held_backup_ids=independently_held,
    )

    assert len(prune_ids) == 65
    assert tuple(
        int(item["attempt_number"])
        for item in updated.safety_backup_attempts
    ) == (0, 66)


def test_newer_failed_restore_backup_does_not_replace_original_safety_reference() -> None:
    """A later safety attempt may append another kind without changing the original copy."""

    original = _target()
    failed_restore = append_safety_attempt(
        original,
        "backup-00000000000000000000000000000002",
        promote_original=False,
    )

    assert failed_restore.safety_backup_id == original.safety_backup_id
    assert failed_restore.safety_backup_attempts[-1]["backup_id"] != original.safety_backup_id


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
