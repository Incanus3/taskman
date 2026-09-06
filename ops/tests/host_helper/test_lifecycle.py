"""Durable lifecycle-record storage tests for the standard-library helper."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from taskman_ops.host_helper.facts import collect_lifecycle_facts, observe_lifecycle
from taskman_ops.host_helper.lifecycle import (
    ActivationRecord,
    LifecycleError,
    LifecycleLockContention,
    LifecycleWriteEffect,
    LifecycleWriteFailure,
    LifecycleRecords,
    LifecycleStore,
    ManualAdoptionCandidate,
    ReleaseRecord,
    StagedRelease,
    rollback_eligibility,
)
from taskman_ops.host_helper.paths import ManagedPaths


def test_record_schema_module_round_trips_a_checksumming_backup() -> None:
    """Storage-independent records retain the checksum publication contract."""

    from taskman_ops.host_helper.lifecycle_records import BackupRecord

    record = BackupRecord.from_mapping(
        {
            "schema_version": 1,
            "backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "created_at": "2026-09-05T12:00:00Z",
            "size_bytes": 10,
            "source_database_size_bytes": 1,
            "database": "taskman",
            "current_release_id": None,
            "candidate_release_id": None,
            "reason": "scheduled",
            "validated": True,
            "dump_path": "/var/backups/taskman/backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.dump",
            "dump_sha256": "a" * 64,
        }
    )

    assert record.to_mapping()["dump_sha256"] == "a" * 64


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths.from_mapping(
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}
    )


def _release() -> ReleaseRecord:
    return ReleaseRecord.from_mapping(
        {
            "schema_version": 1,
            "release_id": RELEASE_ID,
            "artifact_sha256": "a" * 64,
            "installed_at": datetime(2026, 9, 5, 12, 0, tzinfo=UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "activated_at": None,
            "previous_release_id": None,
            "backup_id": None,
            "migration_policy": "no-change",
        }
    )


def test_lifecycle_keeps_first_release_activation_immutable_across_a_later_rollback_event(tmp_path: Path) -> None:
    """Overwriting a release record on rollback would erase its original provenance."""

    release_a = RELEASE_ID
    release_b = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
    first = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    second = datetime(2026, 9, 5, 12, 1, tzinfo=UTC)
    rollback_time = datetime(2026, 9, 5, 12, 2, tzinfo=UTC)
    records = LifecycleRecords(
        (
            ReleaseRecord(1, release_a, "a" * 64, first, first, None, None, "no-change"),
            ReleaseRecord(1, release_b, "b" * 64, second, second, release_a, None, "no-change"),
        ),
        (
            ActivationRecord(1, "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, release_a, first, None, "no-change"),
            ActivationRecord(1, "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", release_a, release_b, second, None, "no-change"),
            ActivationRecord(1, "activation-cccccccccccccccccccccccccccccccc", release_b, release_a, rollback_time, None, "no-change"),
        ),
        (), (), (),
    )

    from taskman_ops.host_helper.lifecycle import validate_lifecycle_records

    validate_lifecycle_records(records, _paths(tmp_path))
    assert records.current_release_id == release_a
    assert records.releases[0].activated_at == first


def _staged_manifest() -> dict[str, object]:
    return {
        "schema_version": 2,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": "a" * 40,
        "release_id": RELEASE_ID,
        "built_at": "2026-09-05T12:00:00Z",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "elixir_version": "1.18.3",
        "node_version": "22.22.1",
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "builder_base_tag": "ubuntu:resolute-20260811.1",
        "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
        "migrations": [],
        "top_level": "taskman",
    }


def _staged_candidate(tmp_path: Path, *, checksum: str = "a" * 64) -> None:
    candidate = tmp_path / "install" / "releases" / RELEASE_ID
    candidate.mkdir(parents=True)
    candidate.parent.chmod(0o750)
    candidate.chmod(0o750)
    marker = candidate / ".taskman-release.json"
    marker.write_text(
        '{"schema_version":1,"release_id":"' + RELEASE_ID + '","artifact_sha256":"' + checksum + '"}\n',
        encoding="utf-8",
    )
    marker.chmod(0o640)


def test_store_writes_private_root_owned_records_without_replacement(tmp_path: Path) -> None:
    """A completed record may be published once but never overwritten by a retry."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    store.write_release(_release())

    record_path = tmp_path / "install" / "deployments" / "releases" / f"release-{RELEASE_ID}.json"
    assert record_path.exists()
    assert record_path.stat().st_mode & 0o777 == 0o600
    assert store.read().releases[0].release_id == RELEASE_ID

    with pytest.raises(LifecycleError, match="already exists"):
        store.write_release(_release())


def test_store_reports_published_record_when_final_directory_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link that reached its final name remains durable-effect evidence.

    Treating the post-link directory fsync error as an ordinary unchanged
    lifecycle failure loses the record a recovery must inspect.  The write
    contract therefore carries the exact publication effect with the error.
    """

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    target = tmp_path / "install" / "deployments" / "releases" / "effect.json"
    original = store._fsync_directory

    def fail_after_link(directory: Path) -> None:
        if directory == target.parent:
            raise OSError("injected final-link fsync failure")
        original(directory)

    monkeypatch.setattr(store, "_fsync_directory", fail_after_link)

    with pytest.raises(LifecycleWriteFailure) as raised:
        store._write(target, {"schema_version": 1})

    assert raised.value.effect == LifecycleWriteEffect(published=True)
    assert target.is_file()


@pytest.mark.parametrize(
    ("boundary", "published_count"),
    (("manifest", 1), ("release", 2), ("activation", 3)),
)
def test_finalization_keeps_exact_partial_effects_and_resumes_matching_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, published_count: int
) -> None:
    """A post-write interruption must not poison the matching finalization retry.

    Replacing finalization with blind no-replace writes would fail the retry
    after the first durable manifest, release, or activation record instead of
    validating that exact prefix and publishing only its missing suffix.
    """

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    staged = StagedRelease(
        1,
        "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        None,
        RELEASE_ID,
        None,
        "no-change",
        "a" * 64,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        _staged_manifest(),
    )
    release = _release()
    activation = ActivationRecord(
        1,
        staged.activation_id,
        None,
        RELEASE_ID,
        staged.activated_at,
        None,
        "no-change",
    )
    _staged_candidate(tmp_path)
    store.stage_immutable(staged)

    targets = (
        store.manifest_path(RELEASE_ID),
        store._record_path("releases", RELEASE_ID),
        store._record_path("activations", staged.activation_id),
    )
    original_write = store._write

    def fail_after_selected_write(path: Path, payload: object) -> LifecycleWriteEffect:
        effect = original_write(path, payload)  # type: ignore[arg-type]
        if path == targets[published_count - 1]:
            raise LifecycleWriteFailure("records-canary-after-final-link", effect)
        return effect

    monkeypatch.setattr(store, "_write", fail_after_selected_write)
    with store.exclusive_lifecycle_lock(operation="deploy"):
        with pytest.raises(LifecycleError) as raised:
            store.finalize_staged_locked(staged, release, activation)

    # The finalization contract must expose every record known durable at the
    # interrupted boundary, not merely the final record whose fsync failed.
    assert raised.value.effect.published_paths == tuple(targets[:published_count])  # type: ignore[attr-defined]
    assert raised.value.effect.provisional_path == (
        tmp_path / "install" / "deployments" / "provisionals" / f"{staged.activation_id}.json"
    )  # type: ignore[attr-defined]

    monkeypatch.setattr(store, "_write", original_write)
    with store.exclusive_lifecycle_lock(operation="deploy"):
        effect = store.finalize_staged_locked(staged, release, activation)

    assert effect.published_paths == targets  # type: ignore[attr-defined]
    assert effect.provisional_path is None  # type: ignore[attr-defined]
    assert not (
        tmp_path / "install" / "deployments" / "provisionals" / f"{staged.activation_id}.json"
    ).exists()


def test_finalization_reports_published_records_when_provisional_unlink_fails_after_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final unlink/fsync failure retains record and provisional truth for retry.

    Treating provisional retirement as an all-or-nothing cleanup would hide
    the three final records already linked before its failure.
    """

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    staged = StagedRelease(
        1,
        "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        None,
        RELEASE_ID,
        None,
        "no-change",
        "a" * 64,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        _staged_manifest(),
    )
    release = _release()
    activation = ActivationRecord(1, staged.activation_id, None, RELEASE_ID, staged.activated_at, None, "no-change")
    _staged_candidate(tmp_path)
    store.stage_immutable(staged)
    provisional = tmp_path / "install" / "deployments" / "provisionals" / f"{staged.activation_id}.json"
    original_fsync = store._fsync_directory

    def fail_after_provisional_unlink(directory: Path) -> None:
        if directory == provisional.parent and not provisional.exists():
            raise OSError("records-canary-after-provisional-unlink")
        original_fsync(directory)

    monkeypatch.setattr(store, "_fsync_directory", fail_after_provisional_unlink)
    with store.exclusive_lifecycle_lock(operation="deploy"):
        with pytest.raises(LifecycleError) as raised:
            store.finalize_staged_locked(staged, release, activation)

    assert raised.value.effect.published_paths == (  # type: ignore[attr-defined]
        store.manifest_path(RELEASE_ID),
        store._record_path("releases", RELEASE_ID),
        store._record_path("activations", staged.activation_id),
    )
    assert raised.value.effect.provisional_path is None  # type: ignore[attr-defined]
    assert not provisional.exists()


def test_record_write_preserves_a_published_link_and_primary_failure_when_temp_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retained temporary must not hide a final record that already linked.

    Replacing the write failure with the cleanup ``OSError`` would erase both
    the durable publication fact and the exact path an operator must inspect.
    """

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    target = tmp_path / "install" / "deployments" / "releases" / f"release-{RELEASE_ID}.json"
    original_fsync = store._fsync_directory
    original_unlink = Path.unlink

    def fail_after_link(directory: Path) -> None:
        if directory == target.parent:
            raise OSError("records-canary-after-final-link")
        original_fsync(directory)

    def retain_temporary(path: Path, *args: object, **kwargs: object) -> None:
        if path.parent == target.parent and path.name.startswith(f".{target.name}."):
            raise OSError("records-canary-temporary-residue")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(store, "_fsync_directory", fail_after_link)
    monkeypatch.setattr(Path, "unlink", retain_temporary)

    with pytest.raises(LifecycleWriteFailure) as raised:
        store._write(target, {"schema_version": 1})

    assert raised.value.effect == LifecycleWriteEffect(published=True)
    assert len(raised.value.residue_paths) == 1
    assert raised.value.residue_paths[0].parent == target.parent
    assert raised.value.residue_paths[0].name.startswith(f".{target.name}.")
    assert target.is_file()


@pytest.mark.parametrize("mode", (0o777, 0o7000))
def test_store_refuses_unsafe_record_storage_before_reading(tmp_path: Path, mode: int) -> None:
    """Group writable and special-bit roots cannot become lifecycle authority."""

    root = tmp_path / "install" / "deployments" / "releases"
    root.mkdir(parents=True)
    root.chmod(mode)

    try:
        with pytest.raises(LifecycleError, match="directory"):
            LifecycleStore(_paths(tmp_path), owner_uid=os.getuid()).read()
    finally:
        root.chmod(0o700)


def test_store_rejects_a_current_link_without_an_activation_record(tmp_path: Path) -> None:
    """A selected path is never valid lifecycle authority on its own."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    release = tmp_path / "install" / "releases" / RELEASE_ID
    release.mkdir(parents=True)
    release.parent.chmod(0o750)
    release.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(release)

    with pytest.raises(LifecycleError, match="without an activation"):
        store.read()


def test_store_refuses_to_publish_a_record_larger_than_its_read_bound(tmp_path: Path) -> None:
    """A writer must never create a record its bounded reader will reject."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    oversized = {"payload": "x" * (64 * 1024)}

    with pytest.raises(LifecycleError, match="oversized"):
        store._write(tmp_path / "install" / "deployments" / "releases" / "large.json", oversized)

    assert not (tmp_path / "install" / "deployments" / "releases" / "large.json").exists()


def test_manual_adoption_revalidates_the_exact_confirmed_candidate_under_lock(tmp_path: Path) -> None:
    """A manual release is adopted only when the inspected authority is unchanged."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    selected = tmp_path / "install" / "releases" / "historical"
    server = selected / "bin" / "server"
    app = selected / "lib" / "taskman-0.2.0" / "ebin" / "taskman.app"
    migration = selected / "lib" / "taskman-0.2.0" / "priv" / "repo" / "migrations" / "20260905120000_bootstrap.exs"
    for directory in (server.parent, app.parent, migration.parent):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    server.write_text("#!/bin/sh\n", encoding="utf-8")
    server.chmod(0o750)
    app.write_text('{application,taskman,[{vsn,"0.2.0"}]}.\n', encoding="utf-8")
    app.chmod(0o640)
    migration.write_text("# migration\n", encoding="utf-8")
    migration.chmod(0o640)
    selected.chmod(0o750)
    selected.parent.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(selected)

    candidate = store.inspect_manual_current()
    adopted = store.adopt_manual_current(candidate)

    assert adopted.release_id == candidate.release_id
    assert store.read().adoptions == (adopted,)


def test_manual_adoption_refuses_when_confirmation_no_longer_matches(tmp_path: Path) -> None:
    """The controller-confirmed digest is re-observed before any adoption record is written."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    selected = tmp_path / "install" / "releases" / "historical"
    server = selected / "bin" / "server"
    app = selected / "lib" / "taskman-0.2.0" / "ebin" / "taskman.app"
    migrations = selected / "lib" / "taskman-0.2.0" / "priv" / "repo" / "migrations"
    for directory in (server.parent, app.parent, migrations):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    server.write_text("#!/bin/sh\n", encoding="utf-8")
    server.chmod(0o750)
    app.write_text('{application,taskman,[{vsn,"0.2.0"}]}.\n', encoding="utf-8")
    app.chmod(0o640)
    selected.chmod(0o750)
    selected.parent.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(selected)
    candidate = store.inspect_manual_current()
    changed = ManualAdoptionCandidate(
        candidate.schema_version,
        "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
        candidate.release_path,
        "b" * 64,
        candidate.application_version,
        candidate.migrations,
    )

    with pytest.raises(LifecycleError, match="confirmation"):
        store.adopt_manual_current(changed)


def test_immutable_staging_resumes_only_the_exact_published_candidate(tmp_path: Path) -> None:
    """A retry may resume its own provisional edge but cannot replace its authority."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    staged = StagedRelease.from_mapping(
        {
            "schema_version": 1,
            "activation_id": "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "previous_release_id": None,
            "candidate_release_id": RELEASE_ID,
            "backup_id": None,
            "migration_policy": "no-change",
            "artifact_sha256": "a" * 64,
            "installed_at": "2026-09-05T12:00:00Z",
            "activated_at": "2026-09-05T12:00:00Z",
            "manifest": _staged_manifest(),
        }
    )

    _staged_candidate(tmp_path)
    store.stage_immutable(staged)

    assert store.resume_immutable(staged.activation_id, RELEASE_ID, "a" * 64) == staged
    with store.exclusive_lifecycle_lock(operation="deploy"):
        assert store.resume_immutable_locked(staged.activation_id, RELEASE_ID, "a" * 64) == staged
    with pytest.raises(LifecycleError, match="confirmation"):
        store.resume_immutable(staged.activation_id, RELEASE_ID, "b" * 64)
    with pytest.raises(LifecycleError, match="already exists"):
        store.stage_immutable(staged)


def test_locked_matching_resume_refuses_ambiguous_prior_provisionals(tmp_path: Path) -> None:
    """A fresh operation cannot guess between two matching interrupted stages."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    staged = StagedRelease.from_mapping(
        {
            "schema_version": 1,
            "activation_id": "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "previous_release_id": None,
            "candidate_release_id": RELEASE_ID,
            "backup_id": None,
            "migration_policy": "no-change",
            "artifact_sha256": "a" * 64,
            "installed_at": "2026-09-05T12:00:00Z",
            "activated_at": "2026-09-05T12:00:00Z",
            "manifest": _staged_manifest(),
        }
    )
    _staged_candidate(tmp_path)
    store.stage_immutable(staged)
    store.stage_immutable(replace(staged, activation_id="activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))

    with store.exclusive_lifecycle_lock(operation="deploy"):
        with pytest.raises(LifecycleError, match="ambiguous"):
            store.resume_matching_immutable_locked(RELEASE_ID, "a" * 64)


def test_immutable_staging_refuses_an_absent_or_changed_candidate_marker(tmp_path: Path) -> None:
    """A provisional edge is authority only while its immutable tree proves its checksum."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    staged = StagedRelease.from_mapping(
        {
            "schema_version": 1,
            "activation_id": "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "previous_release_id": None,
            "candidate_release_id": RELEASE_ID,
            "backup_id": None,
            "migration_policy": "no-change",
            "artifact_sha256": "a" * 64,
            "installed_at": "2026-09-05T12:00:00Z",
            "activated_at": "2026-09-05T12:00:00Z",
            "manifest": _staged_manifest(),
        }
    )

    with pytest.raises(LifecycleError, match="candidate"):
        store.stage_immutable(staged)

    _staged_candidate(tmp_path, checksum="b" * 64)

    with pytest.raises(LifecycleError, match="candidate"):
        store.stage_immutable(staged)


def test_immutable_staging_refuses_to_resume_after_partial_candidate_publication(tmp_path: Path) -> None:
    """A provisional cannot outlive the marker that bound it to a published tree."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    staged = StagedRelease.from_mapping(
        {
            "schema_version": 1,
            "activation_id": "activation-cccccccccccccccccccccccccccccccc",
            "previous_release_id": None,
            "candidate_release_id": RELEASE_ID,
            "backup_id": None,
            "migration_policy": "no-change",
            "artifact_sha256": "a" * 64,
            "installed_at": "2026-09-05T12:00:00Z",
            "activated_at": "2026-09-05T12:00:00Z",
            "manifest": _staged_manifest(),
        }
    )
    _staged_candidate(tmp_path)
    store.stage_immutable(staged)
    (tmp_path / "install" / "releases" / RELEASE_ID / ".taskman-release.json").unlink()

    with pytest.raises(LifecycleError, match="candidate marker is absent"):
        store.resume_immutable(staged.activation_id, RELEASE_ID, "a" * 64)


def test_manual_adoption_resumes_a_complete_markerless_publication(tmp_path: Path) -> None:
    """A crash after the durable bundle but before its marker is recoverable evidence."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    selected = tmp_path / "install" / "releases" / "historical"
    server = selected / "bin" / "server"
    app = selected / "lib" / "taskman-0.2.0" / "ebin" / "taskman.app"
    migrations = selected / "lib" / "taskman-0.2.0" / "priv" / "repo" / "migrations"
    for directory in (server.parent, app.parent, migrations):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    server.write_text("#!/bin/sh\n", encoding="utf-8")
    server.chmod(0o750)
    app.write_text('{application,taskman,[{vsn,"0.2.0"}]}.\n', encoding="utf-8")
    app.chmod(0o640)
    selected.chmod(0o750)
    selected.parent.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(selected)

    candidate = store.inspect_manual_current()
    adopted = store.adopt_manual_current(candidate)
    (tmp_path / "install" / "deployments" / "adoptions" / f"adoption-{adopted.release_id}.json").unlink()

    assert store.adopt_manual_current(candidate) == adopted
    assert store.read().adoptions == (adopted,)


def test_markerless_manual_adoption_reobserves_the_confirmed_tree_before_resume(tmp_path: Path) -> None:
    """A missing marker cannot turn changed manual files into stale adopted authority."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    selected = tmp_path / "install" / "releases" / "historical"
    server = selected / "bin" / "server"
    app = selected / "lib" / "taskman-0.2.0" / "ebin" / "taskman.app"
    migrations = selected / "lib" / "taskman-0.2.0" / "priv" / "repo" / "migrations"
    for directory in (server.parent, app.parent, migrations):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    server.write_text("#!/bin/sh\n", encoding="utf-8")
    server.chmod(0o750)
    app.write_text('{application,taskman,[{vsn,"0.2.0"}]}.\n', encoding="utf-8")
    app.chmod(0o640)
    selected.chmod(0o750)
    selected.parent.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(selected)

    candidate = store.inspect_manual_current()
    adopted = store.adopt_manual_current(candidate)
    (tmp_path / "install" / "deployments" / "adoptions" / f"adoption-{adopted.release_id}.json").unlink()
    server.write_text("#!/bin/sh\n# changed after markerless publication\n", encoding="utf-8")

    with pytest.raises(LifecycleError, match="confirmation"):
        store.adopt_manual_current(candidate)


def test_manual_adoption_refuses_incomplete_markerless_publication(tmp_path: Path) -> None:
    """A partial bundle remains explicit residue rather than implicit adoption authority."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    selected = tmp_path / "install" / "releases" / "historical"
    server = selected / "bin" / "server"
    app = selected / "lib" / "taskman-0.2.0" / "ebin" / "taskman.app"
    migrations = selected / "lib" / "taskman-0.2.0" / "priv" / "repo" / "migrations"
    for directory in (server.parent, app.parent, migrations):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    server.write_text("#!/bin/sh\n", encoding="utf-8")
    server.chmod(0o750)
    app.write_text('{application,taskman,[{vsn,"0.2.0"}]}.\n', encoding="utf-8")
    app.chmod(0o640)
    selected.chmod(0o750)
    selected.parent.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(selected)

    candidate = store.inspect_manual_current()
    adopted = store.adopt_manual_current(candidate)
    (tmp_path / "install" / "deployments" / "adoptions" / f"adoption-{adopted.release_id}.json").unlink()
    transaction = tmp_path / "install" / "deployments" / "adoption-transactions" / f"adoption-{adopted.release_id}"
    (transaction / "activation.json").unlink()

    with pytest.raises(LifecycleError, match="incomplete manual adoption transaction"):
        store.adopt_manual_current(candidate)


def test_snapshot_lock_fails_by_deadline_with_the_canonical_holder_evidence(tmp_path: Path) -> None:
    """Read-only helper calls cannot consume the runner budget behind a lifecycle writer."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    lock_root = tmp_path / ".taskman-lock"
    lock_root.mkdir()
    lock_root.chmod(0o750)
    descriptor = os.open(lock_root / "lifecycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(LifecycleLockContention):
            collect_lifecycle_facts(_paths(tmp_path), owner_uid=os.getuid(), deadline=started + 0.02)
        assert time.monotonic() - started < 0.5
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_snapshot_lock_cleanup_does_not_wait_past_its_deadline_for_metadata(tmp_path: Path) -> None:
    """A metadata contender cannot keep a completed snapshot inside the runner."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    lock_root = tmp_path / ".taskman-lock"
    blocker: subprocess.Popen[str] | None = None
    started = time.monotonic()
    try:
        with store.shared_snapshot_lock(deadline=started + 0.12):
            blocker = subprocess.Popen(
                (
                    sys.executable,
                    "-c",
                    "import fcntl, os, sys, time; descriptor = os.open(sys.argv[1], os.O_RDWR); fcntl.flock(descriptor, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(0.5)",
                    str(lock_root / "lifecycle.lock.meta"),
                ),
                stdout=subprocess.PIPE,
                text=True,
            )
            assert blocker.stdout is not None
            assert blocker.stdout.readline() == "locked\n"
        assert time.monotonic() - started < 0.35
    finally:
        if blocker is not None:
            blocker.wait(timeout=1)


def test_raw_observation_preserves_partial_state_before_discovery_acceptance(tmp_path: Path) -> None:
    """A partial lifecycle is evidence first; discovery decides it is refused."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    store.write_release(_release())
    selected = tmp_path / "install" / "releases" / RELEASE_ID
    selected.mkdir(parents=True)
    selected.chmod(0o750)
    selected.parent.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(selected)

    observation = observe_lifecycle(_paths(tmp_path), owner_uid=os.getuid())

    assert observation.records.releases == (_release(),)
    assert observation.current_target == selected.as_posix()
    with pytest.raises(LifecycleError, match="without an activation"):
        collect_lifecycle_facts(_paths(tmp_path), owner_uid=os.getuid())


def test_store_refuses_record_inventory_above_its_bounded_stream_limit(tmp_path: Path) -> None:
    """Record enumeration stops at a deterministic bound before materializing a directory."""

    store = LifecycleStore(_paths(tmp_path), owner_uid=os.getuid())
    records = tmp_path / "install" / "deployments" / "releases"
    records.mkdir(parents=True)
    records.chmod(0o750)
    for index in range(65):
        entry = records / f"unexpected-{index:02d}"
        entry.write_text("x", encoding="utf-8")
        entry.chmod(0o600)

    with pytest.raises(LifecycleError, match="at least 65 entries; limit 64"):
        store.read()


def _activation(
    activation_id: str,
    previous_release_id: str | None,
    candidate_release_id: str,
    *,
    migration_policy: str = "no-change",
    hour: int,
) -> ActivationRecord:
    return ActivationRecord.from_mapping(
        {
            "schema_version": 1,
            "activation_id": activation_id,
            "previous_release_id": previous_release_id,
            "candidate_release_id": candidate_release_id,
            "activated_at": f"2026-09-05T{hour:02d}:00:00Z",
            "backup_id": None,
            "migration_policy": migration_policy,
        }
    )


@pytest.mark.parametrize(
    ("activations", "current", "target", "eligible", "reason"),
    (
        (
            (_activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, hour=12), _activation("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_ID, "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6", hour=13)),
            "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
            RELEASE_ID,
            True,
            None,
        ),
        (
            (_activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, hour=12),),
            RELEASE_ID,
            "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6",
            False,
            "target release is not connected to the current activation chain",
        ),
        (
            (_activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, hour=12), _activation("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_ID, "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6", migration_policy="restore-required", hour=13)),
            "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
            RELEASE_ID,
            False,
            "activation activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb requires database restore",
        ),
        (
            (_activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, hour=12), _activation("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6", "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6", hour=13)),
            "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
            RELEASE_ID,
            False,
            "activation chain is incomplete",
        ),
    ),
)
def test_rollback_eligibility_reports_connected_and_refusal_chain_states(
    activations: tuple[ActivationRecord, ...],
    current: str,
    target: str,
    eligible: bool,
    reason: str | None,
) -> None:
    """Rollback facts distinguish a direct edge from disconnected or restore-bound history."""

    records = LifecycleRecords((), activations, (), (), ())

    assert rollback_eligibility(records, current, target) == (eligible, reason)
