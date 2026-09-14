"""Exact backup-protection records and durable publication."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from tests.host_helper.support import managed_paths

import taskman_ops.host_helper.backup_protection as protection_module
from taskman_ops.host_helper.backup_protection import (
    BackupProtection,
    replace_backup_protection,
    write_backup_protection,
)
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import (
    BackupRecord,
    RecordError,
    SelectionRecord,
    append_selection,
    selection_filename,
)
from taskman_ops.host_helper.state import HostState


BACKUP = "backup-" + "a" * 32
OTHER_BACKUP = "backup-" + "b" * 32
TARGET = "0.2.0-" + "c" * 12 + "-ubuntu26.04-amd64-otp29.0.6-" + "d" * 64
OTHER_TARGET = "0.2.1-" + "f" * 12 + "-ubuntu26.04-amd64-otp29.0.6-" + "1" * 64
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


def _backup(backup_id: str) -> BackupRecord:
    return BackupRecord(backup_id, AT, "2" * 64, TARGET, (), 1024)


def _state(
    *,
    selections: tuple[SelectionRecord, ...] = (),
    protections: tuple[BackupProtection, ...] = (),
    backups: tuple[BackupRecord, ...] = (),
) -> HostState:
    return HostState(
        selected_release_id=TARGET,
        releases=(),
        backups=backups,
        selections=selections,
        applied_migrations=(),
        service_state="unknown",
        database_state="ready",
        temporary_paths=(),
        warnings=(),
        backup_protections=protections,
    )


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


@pytest.mark.parametrize("mode", (0o644, 0o700))
def test_backup_protection_replacement_refuses_nonprivate_existing_authority(
    tmp_path: Path, mode: int
) -> None:
    paths = managed_paths(tmp_path)
    first = _protection()
    replacement = _protection(
        base_selection_id=None,
        attempt_number=1,
        created_at=datetime(2026, 9, 7, 13, 0, tzinfo=UTC),
    )
    write_backup_protection(paths, first)
    Path(paths.local(paths.backup_protection(BACKUP))).chmod(mode)

    with pytest.raises(ValueError, match="unsafe"):
        replace_backup_protection(paths, replacement)


def test_backup_protection_serializer_enforces_the_other_record_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import taskman_ops.host_helper.backup_protection as protection_module

    monkeypatch.setattr(protection_module, "MAX_RECORD_BYTES", 1)
    with pytest.raises(ValueError, match="oversized|size|protection"):
        BackupProtection.from_mapping(_protection().to_mapping())


def test_first_success_transfers_all_null_baseline_references_before_removal(
    tmp_path: Path,
) -> None:
    """Dropping a protection before durable history would expose its exact backup."""

    paths = managed_paths(tmp_path)
    original = _protection(base_selection_id=None)
    newest = _protection(
        backup_id=OTHER_BACKUP,
        base_selection_id=None,
        attempt_number=1,
        created_at=AT.replace(minute=1),
    )
    write_backup_protection(paths, original)
    write_backup_protection(paths, newest)

    record, created = protection_module.complete_successful_selection(
        paths,
        _state(
            protections=(original, newest),
            backups=(_backup(BACKUP), _backup(OTHER_BACKUP)),
        ),
        release_id=TARGET,
        observed_previous_release_id=None,
        selected_at=AT.replace(minute=2),
    )

    assert created is True
    assert record.previous_release_id is None
    assert record.backup_id == OTHER_BACKUP
    assert record.recovery_backup_ids == (BACKUP, OTHER_BACKUP)
    assert (Path(paths.local(paths.selection_root)) / selection_filename(record)).is_file()
    assert not Path(paths.local(paths.backup_protection(BACKUP))).exists()
    assert not Path(paths.local(paths.backup_protection(OTHER_BACKUP))).exists()


def test_first_success_preserves_an_existing_physical_selection(tmp_path: Path) -> None:
    """Empty successful history does not imply that physical current was absent."""

    paths = managed_paths(tmp_path)

    record, created = protection_module.complete_successful_selection(
        paths,
        _state(),
        release_id=TARGET,
        observed_previous_release_id=TARGET,
        selected_at=AT,
    )

    assert created is True
    assert record.previous_release_id is None
    assert record.observed_previous_release_id == TARGET


def test_success_publication_survives_protection_removal_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reversing publication/removal order would lose the only durable backup reference."""

    paths = managed_paths(tmp_path)
    protection = _protection(base_selection_id=None)
    write_backup_protection(paths, protection)

    def fail_removal(_paths: object, _protections: object) -> None:
        selection_files = list(Path(paths.local(paths.selection_root)).glob("selection-*.json"))
        assert len(selection_files) == 1
        persisted = SelectionRecord.from_mapping(
            json.loads(selection_files[0].read_text(encoding="utf-8"))
        )
        assert persisted.recovery_backup_ids == (BACKUP,)
        raise RecordError("interrupted protection removal")

    monkeypatch.setattr(protection_module, "_remove_resolved_protections", fail_removal)

    with pytest.raises(RecordError, match="remove|protection"):
        protection_module.complete_successful_selection(
            paths,
            _state(protections=(protection,), backups=(_backup(BACKUP),)),
            release_id=TARGET,
            observed_previous_release_id=None,
            selected_at=AT.replace(minute=1),
        )

    assert Path(paths.local(paths.backup_protection(BACKUP))).is_file()
    assert len(list(Path(paths.local(paths.selection_root)).glob("selection-*.json"))) == 1


def test_matching_complete_success_is_a_history_noop(tmp_path: Path) -> None:
    """A healthy replay without unresolved references must not duplicate history."""

    paths = managed_paths(tmp_path)
    latest = SelectionRecord(TARGET, None, None, AT, 2, None, ())
    append_selection(paths, latest)

    record, created = protection_module.complete_successful_selection(
        paths,
        _state(selections=(latest,)),
        release_id=TARGET,
        observed_previous_release_id=TARGET,
        selected_at=AT.replace(minute=1),
    )

    assert created is False
    assert record == latest
    assert list(Path(paths.local(paths.selection_root)).glob("selection-*.json")) == [
        Path(paths.local(paths.selection_root)) / selection_filename(latest)
    ]


def test_explicit_completed_retry_removes_stale_protection_without_duplicate_history(
    tmp_path: Path,
) -> None:
    """A retry after durable publication must finish cleanup, not append the same success."""

    paths = managed_paths(tmp_path)
    latest = SelectionRecord(
        TARGET,
        None,
        BACKUP,
        AT,
        2,
        None,
        (BACKUP,),
    )
    append_selection(paths, latest)
    protection = _protection(base_selection_id=None)
    write_backup_protection(paths, protection)

    record, created = protection_module.complete_successful_selection(
        paths,
        _state(
            selections=(latest,),
            protections=(protection,),
            backups=(_backup(BACKUP),),
        ),
        release_id=TARGET,
        observed_previous_release_id=None,
        backup_id=BACKUP,
        recovery_backup_ids=(BACKUP,),
        selected_at=AT.replace(minute=1),
    )

    assert created is False
    assert record == latest
    assert not Path(paths.local(paths.backup_protection(BACKUP))).exists()
    assert len(list(Path(paths.local(paths.selection_root)).glob("selection-*.json"))) == 1


def test_same_release_physical_transition_appends_meaningful_success(tmp_path: Path) -> None:
    """Release equality alone must not hide a freshly verified physical transition."""

    paths = managed_paths(tmp_path)
    latest = SelectionRecord(TARGET, None, None, AT, 2, None, ())
    append_selection(paths, latest)

    record, created = protection_module.complete_successful_selection(
        paths,
        _state(selections=(latest,)),
        release_id=TARGET,
        observed_previous_release_id=OTHER_TARGET,
        selected_at=AT.replace(minute=1),
    )

    assert created is True
    assert record.release_id == TARGET
    assert record.previous_release_id == TARGET
    assert record.observed_previous_release_id == OTHER_TARGET
    assert len(list(Path(paths.local(paths.selection_root)).glob("selection-*.json"))) == 2


def test_null_baseline_protection_cannot_resolve_into_a_later_success(
    tmp_path: Path,
) -> None:
    """A later selection cannot retroactively claim a pre-first-success protection."""

    paths = managed_paths(tmp_path)
    latest = SelectionRecord(TARGET, None, None, AT, 2, None, ())
    append_selection(paths, latest)
    protection = _protection(base_selection_id=None)
    write_backup_protection(paths, protection)

    with pytest.raises(RecordError, match="null-baseline|first success|baseline"):
        protection_module.complete_successful_selection(
            paths,
            _state(
                selections=(latest,),
                protections=(protection,),
                backups=(_backup(BACKUP),),
            ),
            release_id=TARGET,
            observed_previous_release_id=TARGET,
            selected_at=AT.replace(minute=1),
        )
