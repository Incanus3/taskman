"""Exact backup-protection records and durable publication."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from tests.host_helper.support import managed_paths

from taskman_ops.host_helper.backup_protection import (
    BackupProtection,
    replace_backup_protection,
    write_backup_protection,
)
from taskman_ops.host_helper.paths import ManagedPaths


BACKUP = "backup-" + "a" * 32
OTHER_BACKUP = "backup-" + "b" * 32
TARGET = "0.2.0-" + "c" * 12 + "-ubuntu26.04-amd64-otp29.0.6-" + "d" * 64
SELECTION = "selection-" + "e" * 64 + ".json"
AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _protection(**changes: object) -> BackupProtection:
    values: dict[str, object] = {
        "schema_version": 1,
        "backup_id": BACKUP,
        "base_selection_id": SELECTION,
        "target_release_id": TARGET,
        "attempt_number": 0,
        "created_at": AT,
    }
    values.update(changes)
    return BackupProtection(**values)  # type: ignore[arg-type]


def test_backup_protection_has_exact_persisted_fields_and_round_trips() -> None:
    record = _protection()

    assert record.to_mapping() == {
        "schema_version": 1,
        "backup_id": BACKUP,
        "base_selection_id": SELECTION,
        "target_release_id": TARGET,
        "attempt_number": 0,
        "created_at": "2026-09-07T12:00:00Z",
    }
    assert BackupProtection.from_mapping(record.to_mapping()) == record


@pytest.mark.parametrize(
    "changes",
    (
        {"schema_version": 2},
        {"backup_id": OTHER_BACKUP[:-1] + "Z"},
        {"base_selection_id": "selection-invalid.json"},
        {"target_release_id": "source-only-release"},
        {"attempt_number": -1},
        {"attempt_number": True},
        {"created_at": "2026-09-07T12:00:00.000Z"},
        {"created_at": datetime(2026, 9, 7, 12, 0, 1)},
        {"extra": "not persisted"},
    ),
)
def test_backup_protection_rejects_invalid_or_extra_fields(changes: dict[str, object]) -> None:
    mapping = _protection().to_mapping()
    mapping.update(changes)

    with pytest.raises((TypeError, ValueError)):
        BackupProtection.from_mapping(mapping)


def test_null_baseline_is_a_valid_pre_first_selection_protection() -> None:
    record = _protection(base_selection_id=None)

    assert record.base_selection_id is None
    assert BackupProtection.from_mapping(record.to_mapping()) == record


def test_backup_protection_is_created_once_with_private_atomic_publication(tmp_path: Path) -> None:
    paths = managed_paths(tmp_path)
    record = _protection()

    write_backup_protection(paths, record)

    target = Path(paths.local(paths.backup_protection(record.backup_id)))
    assert target.is_file()
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_text(encoding="utf-8")) == record.to_mapping()
    assert not list(target.parent.glob(".*.tmp"))
    with pytest.raises(ValueError, match="exists|published"):
        write_backup_protection(paths, record)


def test_backup_protection_can_be_replaced_atomically_after_initial_publication(tmp_path: Path) -> None:
    paths: ManagedPaths = managed_paths(tmp_path)
    first = _protection()
    replacement = _protection(
        base_selection_id=None,
        attempt_number=1,
        created_at=datetime(2026, 9, 7, 13, 0, tzinfo=UTC),
    )
    write_backup_protection(paths, first)

    replace_backup_protection(paths, replacement)

    target = Path(paths.local(paths.backup_protection(BACKUP)))
    assert json.loads(target.read_text(encoding="utf-8")) == replacement.to_mapping()
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(target.parent.glob(".*.tmp"))


def test_backup_protection_serializer_enforces_the_other_record_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import taskman_ops.host_helper.backup_protection as protection_module

    monkeypatch.setattr(protection_module, "MAX_RECORD_BYTES", 1)
    with pytest.raises(ValueError, match="oversized|size|protection"):
        BackupProtection.from_mapping(_protection().to_mapping())
