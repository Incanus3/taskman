from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path, PurePosixPath
from queue import Queue
import select
import shutil
import subprocess
from threading import Thread

import pytest

from taskman_ops.errors import ExitStatus, OpsError
import taskman_ops.releases.records as lifecycle_records
from taskman_ops.remote import CommandResult
from taskman_ops.releases.records import (
    ActivationRecord,
    AdoptionRecord,
    BackupRecord,
    LifecycleRecords,
    LifecycleStore,
    RemoteLifecycleStore,
    ReleaseRecord,
    load_lifecycle_records,
    rollback_eligibility,
)


RELEASE_A = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_B = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP_A = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ACTIVATION_A = "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ACTIVATION_B = "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
FIRST = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
SECOND = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)


def release_record(release_id: str, **overrides: object) -> ReleaseRecord:
    values: dict[str, object] = {
        "schema_version": 1,
        "release_id": release_id,
        "artifact_sha256": "c" * 64,
        "installed_at": FIRST,
        "activated_at": None,
        "previous_release_id": None,
        "backup_id": None,
        "migration_policy": "no-change",
    }
    values.update(overrides)
    return ReleaseRecord(**values)  # type: ignore[arg-type]


def activation_record(activation_id: str, previous: str | None, candidate: str, **overrides: object) -> ActivationRecord:
    values: dict[str, object] = {
        "schema_version": 1,
        "activation_id": activation_id,
        "previous_release_id": previous,
        "candidate_release_id": candidate,
        "activated_at": FIRST if previous is None else SECOND,
        "backup_id": None,
        "migration_policy": "no-change",
    }
    values.update(overrides)
    return ActivationRecord(**values)  # type: ignore[arg-type]


def backup_record(**overrides: object) -> BackupRecord:
    values: dict[str, object] = {
        "schema_version": 1,
        "backup_id": BACKUP_A,
        "created_at": FIRST,
        "size_bytes": 123,
        "source_database_size_bytes": 456,
        "database": "taskman_prod",
        "current_release_id": RELEASE_A,
        "candidate_release_id": RELEASE_B,
        "reason": "pre-deploy",
        "validated": True,
        "dump_path": PurePosixPath("/var/backups/taskman/backup.dump"),
    }
    values.update(overrides)
    return BackupRecord(**values)  # type: ignore[arg-type]


def test_backup_record_refuses_legacy_metadata_without_source_allocation_authority() -> None:
    """Records created before source-allocation evidence cannot authorize a restore."""

    legacy = backup_record().to_mapping()
    del legacy["source_database_size_bytes"]

    with pytest.raises(ValueError, match="backup record"):
        BackupRecord.from_mapping(legacy)


def lifecycle_store(tmp_path: Path) -> LifecycleStore:
    return LifecycleStore(
        deployment_root=tmp_path / "deployments",
        managed_root=tmp_path / "taskman",
        release_root=tmp_path / "taskman" / "releases",
        backup_root=tmp_path / "backups",
        owner_uid=os.getuid(),
    )


class RecordingRemote:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.responses: list[CommandResult] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((argv, kwargs))
        return self.responses.pop(0) if self.responses else CommandResult(0)


class SnapshotSubprocessRemote:
    """Runs the fixed snapshot source in a disposable root user namespace."""

    def __init__(self, lock_root: Path, environment: dict[str, str] | None = None) -> None:
        self.lock_root = lock_root
        self.environment = environment
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((argv, kwargs))
        translated = tuple(str(self.lock_root) if value == "/var/lock/taskman" else value for value in argv)
        completed = subprocess.run(
            ("unshare", "-Ur", "--", *translated),
            check=False,
            capture_output=True,
            input=kwargs["stdin"],
            env=self.environment,
        )
        return CommandResult(completed.returncode, completed.stdout.decode("utf-8"))


def _write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    path.chmod(0o600)


def snapshot_subprocess_store(
    tmp_path: Path,
    *,
    contradiction: str | None = None,
) -> tuple[RemoteLifecycleStore, dict[str, Path]]:
    """Build a real deployment tree for the immutable snapshot command."""

    host = tmp_path / "host"
    deployment_root = host / "deployments"
    managed_root = host / "taskman"
    release_root = managed_root / "releases"
    backup_root = host / "backups"
    record_roots = {
        "releases": deployment_root / "releases",
        "activations": deployment_root / "activations",
        "backups": deployment_root / "backups",
        "adoptions": deployment_root / "adoptions",
        "adoption-transactions": deployment_root / "adoption-transactions",
        "manifests": deployment_root / "manifests",
    }
    for directory in (deployment_root, managed_root, release_root, backup_root, *record_roots.values()):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)

    (release_root / RELEASE_A).mkdir()
    adopted_path = release_root / "manual-current"
    adopted_path.mkdir()
    if contradiction != "missing_release_path":
        adopted_path.chmod(0o750)
    else:
        adopted_path.rmdir()
    (managed_root / "current").symlink_to(adopted_path)

    direct_release = release_record(RELEASE_A, activated_at=FIRST)
    direct_activation = activation_record(ACTIVATION_A, None, RELEASE_A)
    adopted_release = release_record(
        RELEASE_B,
        artifact_sha256=None,
        installed_at=SECOND,
        activated_at=SECOND,
        previous_release_id=RELEASE_A,
        migration_policy="adopted",
    )
    adopted_activation = activation_record(
        ACTIVATION_B,
        RELEASE_A,
        RELEASE_B,
        migration_policy="adopted",
    )
    adoption = AdoptionRecord(
        1,
        RELEASE_B,
        FIRST if contradiction == "adoption_timestamp" else SECOND,
        PurePosixPath(str(adopted_path)),
        "b" * 64,
        "0.2.0",
        "unknown",
        "unknown",
        (),
    )
    _write_private_json(record_roots["releases"] / f"release-{RELEASE_A}.json", direct_release.to_mapping())
    _write_private_json(record_roots["activations"] / f"{ACTIVATION_A}.json", direct_activation.to_mapping())
    bundle = record_roots["adoption-transactions"] / f"adoption-{RELEASE_B}"
    bundle.mkdir()
    bundle.chmod(0o750)
    _write_private_json(bundle / "release.json", adopted_release.to_mapping())
    _write_private_json(bundle / "activation.json", adopted_activation.to_mapping())
    _write_private_json(record_roots["adoptions"] / f"adoption-{RELEASE_B}.json", adoption.to_mapping())

    present_dump = backup_root / "present.dump"
    present_dump.write_bytes(b"postgres dump")
    present_dump.chmod(0o600)
    present = backup_record(
        current_release_id=RELEASE_A,
        candidate_release_id=RELEASE_B,
        dump_path=PurePosixPath(str(present_dump)),
    )
    stale = BackupRecord(
        1,
        "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        SECOND,
        17,
        17,
        "taskman_prod",
        RELEASE_A,
        RELEASE_B,
        "scheduled",
        True,
        PurePosixPath(str(backup_root / "stale.dump")),
    )
    _write_private_json(record_roots["backups"] / f"{present.backup_id}.json", present.to_mapping())
    _write_private_json(record_roots["backups"] / f"{stale.backup_id}.json", stale.to_mapping())
    _write_private_json(
        record_roots["manifests"] / f"release-{RELEASE_A}.json",
        {
            "schema_version": 1,
            "application": "taskman",
            "application_version": "0.2.0",
            "source_revision": "a" * 40,
            "release_id": RELEASE_A,
            "built_at": "2026-09-05T09:00:00Z",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "27.3.4.6",
            "elixir_version": "1.18.3",
            "node_version": "22.22.1",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "migrations": [],
            "top_level": "taskman",
        },
    )

    remote = SnapshotSubprocessRemote(host / "lock")
    return (
        RemoteLifecycleStore(
            remote,  # type: ignore[arg-type]
            PurePosixPath(str(deployment_root)),
            PurePosixPath(str(managed_root)),
            PurePosixPath(str(release_root)),
            PurePosixPath(str(backup_root)),
        ),
        {
            "deployment": deployment_root,
            "managed": managed_root,
            "releases": release_root,
            "backups": backup_root,
        },
    )


def test_records_have_exact_versioned_schemas_and_utc_dates() -> None:
    release = release_record(RELEASE_A)
    backup = backup_record()
    activation = activation_record(ACTIVATION_A, None, RELEASE_A)

    assert release.to_mapping() == {
        "schema_version": 1,
        "release_id": RELEASE_A,
        "artifact_sha256": "c" * 64,
        "installed_at": "2026-09-05T10:00:00Z",
        "activated_at": None,
        "previous_release_id": None,
        "backup_id": None,
        "migration_policy": "no-change",
    }
    assert backup.to_mapping()["dump_path"] == "/var/backups/taskman/backup.dump"
    assert activation.to_mapping()["candidate_release_id"] == RELEASE_A

    with pytest.raises(ValueError):
        ReleaseRecord.from_mapping({**release.to_mapping(), "unknown": True})
    with pytest.raises(ValueError):
        BackupRecord.from_mapping({**backup.to_mapping(), "created_at": "2026-09-05T10:00:00+00:00"})
    with pytest.raises(ValueError):
        ActivationRecord.from_mapping({**activation.to_mapping(), "schema_version": 2})
    with pytest.raises(ValueError):
        BackupRecord(
            1,
            BACKUP_A,
            FIRST,
            1,
            1,
            "taskman_prod",
            None,
            None,
            "scheduled",
            True,
            "/var/backups/taskman/backup.dump",  # type: ignore[arg-type]
        )


def test_store_atomically_writes_root_owned_private_records_and_loads_them(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    installed = release_record(RELEASE_A, activated_at=FIRST)
    store.write_release(installed)
    store.write_activation(activation_record(ACTIVATION_A, None, RELEASE_A))
    store.write_backup(
        backup_record(
            candidate_release_id=None,
            dump_path=PurePosixPath(str(store.backup_root / "backup.dump")),
        )
    )

    records = load_lifecycle_records(store)
    record_path = store.release_path(RELEASE_A)
    mode = record_path.stat().st_mode & 0o777

    assert records.releases == (installed,)
    assert records.activations == (activation_record(ACTIVATION_A, None, RELEASE_A),)
    assert records.backups[0].backup_id == BACKUP_A
    assert mode == 0o600
    assert record_path.stat().st_uid == os.getuid()
    assert not list(record_path.parent.glob(".*.tmp"))


def test_atomic_write_completes_short_kernel_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = lifecycle_store(tmp_path)
    original_write = lifecycle_records.os.write

    def short_write(descriptor: int, data: bytes) -> int:
        return original_write(descriptor, data[: max(1, len(data) // 2)])

    monkeypatch.setattr(lifecycle_records.os, "write", short_write)
    store.write_release(release_record(RELEASE_A))

    assert load_lifecycle_records(store).releases == (release_record(RELEASE_A),)


def test_atomic_record_publication_never_overwrites_a_competing_final_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = lifecycle_store(tmp_path)
    final = store.release_path(RELEASE_A)
    original_link = lifecycle_records.os.link

    def competing_link(source: Path, destination: Path, **kwargs: object) -> None:
        final.write_text("attacker record", encoding="utf-8")
        original_link(source, destination, **kwargs)

    monkeypatch.setattr(lifecycle_records.os, "link", competing_link)

    with pytest.raises(OpsError) as raised:
        store.write_release(release_record(RELEASE_A))

    assert raised.value.status is ExitStatus.SAFETY
    assert final.read_text(encoding="utf-8") == "attacker record"


def test_load_refuses_unsafe_record_files_and_unknown_schema(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    store.write_release(release_record(RELEASE_A))
    record_path = store.release_path(RELEASE_A)
    record_path.chmod(0o644)

    with pytest.raises(OpsError) as unsafe_mode:
        load_lifecycle_records(store)
    assert unsafe_mode.value.status is ExitStatus.SAFETY

    record_path.chmod(0o600)
    record_path.write_text(json.dumps({**release_record(RELEASE_A).to_mapping(), "schema_version": 2}), encoding="utf-8")
    with pytest.raises(OpsError) as unknown_schema:
        load_lifecycle_records(store)
    assert unknown_schema.value.status is ExitStatus.SAFETY


def test_write_refuses_an_existing_unsafe_record_directory_instead_of_repairing_it(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    store.releases_directory.mkdir(parents=True)
    store.releases_directory.chmod(0o777)

    with pytest.raises(OpsError) as raised:
        store.write_release(release_record(RELEASE_A))

    assert raised.value.status is ExitStatus.SAFETY


def test_load_refuses_a_broken_symbolic_link_in_record_storage(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    store.deployment_root.mkdir()
    store.releases_directory.symlink_to(tmp_path / "missing-record-storage")

    with pytest.raises(OpsError) as raised:
        load_lifecycle_records(store)

    assert raised.value.status is ExitStatus.SAFETY


def test_load_rejects_duplicate_or_broken_activation_edges_and_inconsistent_current_selection(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    store.write_release(release_record(RELEASE_A, activated_at=FIRST))
    store.write_release(release_record(RELEASE_B, activated_at=SECOND, previous_release_id=RELEASE_A, migration_policy="restore-required"))
    store.write_activation(activation_record(ACTIVATION_A, None, RELEASE_A))
    store.write_activation(activation_record(ACTIVATION_B, RELEASE_A, RELEASE_B, migration_policy="restore-required"))
    store.release_root.mkdir(parents=True)
    (store.release_root / RELEASE_A).mkdir()
    (store.release_root / RELEASE_B).mkdir()
    store.managed_root.mkdir(parents=True, exist_ok=True)
    (store.managed_root / "current").symlink_to(Path("releases") / RELEASE_A)

    with pytest.raises(OpsError) as inconsistent:
        load_lifecycle_records(store, current_link=store.managed_root / "current")
    assert inconsistent.value.status is ExitStatus.SAFETY

    (store.managed_root / "current").unlink()
    (store.managed_root / "current").symlink_to(Path("releases") / RELEASE_B)
    records = load_lifecycle_records(store, current_link=store.managed_root / "current")
    assert rollback_eligibility(records, current_release_id=RELEASE_B, target_release_id=RELEASE_A) == (
        False,
        "activation activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb requires database restore",
    )

    store.write_activation(activation_record("activation-cccccccccccccccccccccccccccccccc", RELEASE_A, RELEASE_B))
    with pytest.raises(OpsError) as broken_chain:
        load_lifecycle_records(store, current_link=store.managed_root / "current")
    assert broken_chain.value.status is ExitStatus.SAFETY


def test_load_refuses_release_fields_that_contradict_its_first_activation_edge(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    store.write_release(release_record(RELEASE_A, activated_at=FIRST, previous_release_id=None, backup_id=None, migration_policy="no-change"))
    store.write_activation(activation_record(ACTIVATION_A, None, RELEASE_A, migration_policy="backward-compatible"))

    with pytest.raises(OpsError) as raised:
        load_lifecycle_records(store)

    assert raised.value.status is ExitStatus.SAFETY


def test_load_requires_the_configured_current_symlink_not_an_attacker_selected_link(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    store.write_release(release_record(RELEASE_A))
    store.write_activation(activation_record(ACTIVATION_A, None, RELEASE_A))
    (store.release_root / RELEASE_A).mkdir(parents=True)
    external_link = tmp_path / "other-current"
    external_link.symlink_to(store.release_root / RELEASE_A)

    with pytest.raises(OpsError) as raised:
        load_lifecycle_records(store, current_link=external_link)

    assert raised.value.status is ExitStatus.SAFETY


def test_local_parser_accepts_an_adopted_current_path_with_an_independent_directory_name(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    adopted_path = store.release_root / "manual-current"
    adopted_path.mkdir(parents=True)
    adopted_release = release_record(
        RELEASE_B,
        artifact_sha256=None,
        activated_at=FIRST,
        migration_policy="adopted",
    )
    adopted_activation = activation_record(
        ACTIVATION_A,
        None,
        RELEASE_B,
        migration_policy="adopted",
    )
    adoption = AdoptionRecord(
        1,
        RELEASE_B,
        FIRST,
        PurePosixPath(str(adopted_path)),
        "b" * 64,
        "0.2.0",
        "unknown",
        "unknown",
        (),
    )
    store.write_release(adopted_release)
    store.write_activation(adopted_activation)
    store.write_adoption(adoption)
    store.managed_root.mkdir(parents=True, exist_ok=True)
    (store.managed_root / "current").symlink_to(adopted_path)

    records = load_lifecycle_records(store, current_link=store.managed_root / "current")

    assert records.current_release_id == RELEASE_B


def test_unknown_storage_entries_are_warnings_not_authoritative_records(tmp_path: Path) -> None:
    store = lifecycle_store(tmp_path)
    store.write_release(release_record(RELEASE_A))
    (store.releases_directory / "investigate-me").mkdir()

    records = load_lifecycle_records(store)

    assert records.releases[0].release_id == RELEASE_A
    assert records.warnings == ("unrecognized deployment storage entry: releases/investigate-me",)


def test_remote_store_uses_a_root_owned_fsynced_sibling_rename_without_shell_interpolation() -> None:
    remote = RecordingRemote()
    store = RemoteLifecycleStore(
        remote,
        deployment_root=PurePosixPath("/opt/taskman/deployments"),
        managed_root=PurePosixPath("/opt/taskman"),
        release_root=PurePosixPath("/opt/taskman/releases"),
        backup_root=PurePosixPath("/var/backups/taskman"),
    )

    store.write_release(release_record(RELEASE_A))

    argv, kwargs = remote.calls[-1]
    assert argv[:3] == ("sh", "-ceu", argv[2])
    assert "mktemp" in argv[2]
    assert "sync -f" in argv[2]
    assert "ln --" in argv[2]
    assert "mv -fT" not in argv[2]
    assert "chown root:root" in argv[2]
    assert "chmod 600" in argv[2]
    assert argv[-2:] == ("/opt/taskman/deployments/releases", f"release-{RELEASE_A}.json")
    assert kwargs["sudo"] is True
    assert kwargs["stdin"] == (json.dumps(release_record(RELEASE_A).to_mapping(), sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_remote_store_runs_one_constant_posix_snapshot_transaction_without_python() -> None:
    remote = RecordingRemote()
    remote.responses.append(
        CommandResult(
            0,
            json.dumps(
                {
                    "schema_version": 1,
                    "records": {"releases": [], "activations": [], "backups": [], "adoptions": []},
                    "current_target": None,
                    "manifests": {},
                    "dump_states": {},
                    "warnings": [],
                }
            ),
        )
    )
    store = RemoteLifecycleStore(
        remote,
        deployment_root=PurePosixPath("/opt/taskman/deployments"),
        managed_root=PurePosixPath("/opt/taskman"),
        release_root=PurePosixPath("/opt/taskman/releases"),
        backup_root=PurePosixPath("/var/backups/taskman"),
    )

    records, snapshot = store.read(operation="releases", lock_timeout_seconds=0)

    assert records == LifecycleRecords((), (), (), (), ())
    assert snapshot["current_target"] is None
    argv, kwargs = remote.calls[-1]
    assert argv[:2] == ("sh", "-ceu")
    assert "python3" not in argv
    assert kwargs["stdin"] is None
    assert kwargs["sudo"] is True


def test_snapshot_transaction_executes_on_a_pristine_host_without_a_preinstalled_helper(tmp_path: Path) -> None:
    """Removing flock/root validation makes this command fail or emit no envelope."""
    import taskman_ops.releases.records as records_module

    root = tmp_path / "host"
    lock_root = root / "lock"
    completed = subprocess.run(
        (
            "sh", "-ceu", records_module._REMOTE_SNAPSHOT_TRANSACTION, "taskman-lifecycle-snapshot",
            str(root / "deployments"), str(root / "taskman"), str(root / "taskman" / "releases"),
            str(root / "backups"), str(lock_root), "releases", "0", str(os.getuid()),
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "schema_version": 1,
        "records": {"releases": [], "activations": [], "backups": [], "adoptions": []},
        "current_target": None,
        "manifests": {},
        "dump_states": {},
        "warnings": [],
    }


def test_real_snapshot_collects_direct_and_marker_gated_records_manifests_and_dump_states(tmp_path: Path) -> None:
    store, paths = snapshot_subprocess_store(tmp_path)

    records, snapshot = store.read(operation="releases", lock_timeout_seconds=0)

    assert [record.release_id for record in records.releases] == [RELEASE_A, RELEASE_B]
    assert [record.activation_id for record in records.activations] == [ACTIVATION_A, ACTIVATION_B]
    assert [record.backup_id for record in records.backups] == [BACKUP_A, "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
    assert [record.release_id for record in records.adoptions] == [RELEASE_B]
    assert snapshot["current_target"] == str(paths["releases"] / "manual-current")
    assert snapshot["manifests"] == {
        RELEASE_A: {
            "schema_version": 1,
            "application": "taskman",
            "application_version": "0.2.0",
            "source_revision": "a" * 40,
            "release_id": RELEASE_A,
            "built_at": "2026-09-05T09:00:00Z",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "27.3.4.6",
            "elixir_version": "1.18.3",
            "node_version": "22.22.1",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "migrations": [],
            "top_level": "taskman",
        }
    }
    assert snapshot["dump_states"] == {
        str(paths["backups"] / "present.dump"): "present",
        str(paths["backups"] / "stale.dump"): "stale",
    }
    assert records.warnings == ()
    assert isinstance(store.remote, SnapshotSubprocessRemote)
    assert store.remote.calls[0][1]["stdin"] is None


@pytest.mark.parametrize("contradiction", ("adoption_timestamp", "missing_release_path"))
def test_real_snapshot_refuses_adoption_or_release_path_contradictions(
    tmp_path: Path,
    contradiction: str,
) -> None:
    store, _paths = snapshot_subprocess_store(tmp_path, contradiction=contradiction)

    with pytest.raises(OpsError) as raised:
        store.read(operation="releases", lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY


def test_real_snapshot_reports_unknown_entries_without_treating_them_as_records(tmp_path: Path) -> None:
    store, paths = snapshot_subprocess_store(tmp_path)
    deployment = paths["deployment"]
    (deployment / "unexpected").mkdir()
    (deployment / "releases" / "investigate-me").mkdir()
    (paths["releases"] / "manual-copy").mkdir()
    (deployment / "manifests" / "left-behind").mkdir()
    (paths["backups"] / "orphan.dump").write_bytes(b"orphan")

    records, _snapshot = store.read(operation="releases", lock_timeout_seconds=0)

    assert records.warnings == (
        "orphan backup dump: orphan.dump",
        "unexpected deployment-root entry: unexpected",
        "unrecognized deployment storage entry: releases/investigate-me",
        "unrecognized manifest entry: left-behind",
        "unrecognized release directory: manual-copy",
    )


def test_real_snapshot_stray_adoption_does_not_mask_the_required_direct_manifest(tmp_path: Path) -> None:
    store, paths = snapshot_subprocess_store(tmp_path)
    stray = paths["deployment"] / "adoptions" / f"adoption-{RELEASE_A}.json.partial"
    _write_private_json(stray, {"not": "an adoption"})
    (paths["deployment"] / "manifests" / f"release-{RELEASE_A}.json").unlink()

    with pytest.raises(OpsError) as raised:
        store.read(operation="releases", lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY


def test_real_snapshot_prunes_stale_crash_holder_metadata_before_returning(tmp_path: Path) -> None:
    store, _paths = snapshot_subprocess_store(tmp_path)
    assert isinstance(store.remote, SnapshotSubprocessRemote)
    store.remote.lock_root.mkdir(parents=True)
    metadata = store.remote.lock_root / "lifecycle.lock.meta"
    metadata.write_text(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|deploy|999999999|2026-09-05T12:00:00Z|exclusive\n",
        encoding="utf-8",
    )
    metadata.chmod(0o600)

    records, _snapshot = store.read(operation="releases", lock_timeout_seconds=0)

    assert records.current_release_id == RELEASE_B
    assert metadata.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("unsafe_lock_entry", ("root", "lock", "metadata"))
def test_real_snapshot_refuses_unsafe_existing_lifecycle_lock_state(
    tmp_path: Path,
    unsafe_lock_entry: str,
) -> None:
    store, _paths = snapshot_subprocess_store(tmp_path)
    assert isinstance(store.remote, SnapshotSubprocessRemote)
    lock_root = store.remote.lock_root
    lock_root.mkdir(parents=True)
    lock_root.chmod(0o750)
    target = {
        "root": lock_root,
        "lock": lock_root / "lifecycle.lock",
        "metadata": lock_root / "lifecycle.lock.meta",
    }[unsafe_lock_entry]
    if unsafe_lock_entry != "root":
        target.touch()
    target.chmod(0o777 if unsafe_lock_entry == "root" else 0o644)

    with pytest.raises(OpsError) as raised:
        store.read(operation="releases", lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY


def test_real_shared_snapshot_publishes_and_removes_its_live_holder_metadata(tmp_path: Path) -> None:
    store, _paths = snapshot_subprocess_store(tmp_path)
    assert isinstance(store.remote, SnapshotSubprocessRemote)
    commands = tmp_path / "commands"
    commands.mkdir()
    ready = tmp_path / "snapshot-ready"
    release = tmp_path / "snapshot-release"
    os.mkfifo(ready)
    os.mkfifo(release)
    ready_descriptor = os.open(ready, os.O_RDWR | os.O_NONBLOCK)
    release_descriptor = os.open(release, os.O_RDWR | os.O_NONBLOCK)
    (commands / "readlink").write_text(
        "#!/bin/sh\n"
        'printf "ready\\n" > "' + str(ready) + '"\n'
        'read _ < "' + str(release) + '"\n'
        "exec " + str(shutil.which("readlink")) + ' "$@"\n',
        encoding="utf-8",
    )
    (commands / "readlink").chmod(0o700)
    store.remote.environment = {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"}
    outcome: Queue[object] = Queue()

    def run_snapshot() -> None:
        try:
            outcome.put(store.read(operation="releases", lock_timeout_seconds=0))
        except BaseException as error:  # pragma: no cover - surfaced below
            outcome.put(error)

    thread = Thread(target=run_snapshot)
    thread.start()
    try:
        readable, _writable, _exceptional = select.select([ready_descriptor], [], [], 5)
        assert readable
        assert os.read(ready_descriptor, 64) == b"ready\n"
        holder = (store.remote.lock_root / "lifecycle.lock.meta").read_text(encoding="utf-8").strip().split("|")
        assert holder[1] == "releases"
        assert int(holder[2]) > 0
        assert holder[3].endswith("Z")
        assert holder[4] == "shared"

        second_remote = SnapshotSubprocessRemote(store.remote.lock_root, store.remote.environment)
        second_store = RemoteLifecycleStore(
            second_remote,  # type: ignore[arg-type]
            store.deployment_root,
            store.managed_root,
            store.release_root,
            store.backup_root,
        )
        second_outcome: Queue[object] = Queue()

        def run_second_snapshot() -> None:
            try:
                second_outcome.put(second_store.read(operation="backups", lock_timeout_seconds=0))
            except BaseException as error:  # pragma: no cover - surfaced below
                second_outcome.put(error)

        second_thread = Thread(target=run_second_snapshot)
        second_thread.start()
        readable, _writable, _exceptional = select.select([ready_descriptor], [], [], 5)
        assert readable
        assert os.read(ready_descriptor, 64) == b"ready\n"
        holders = (store.remote.lock_root / "lifecycle.lock.meta").read_text(encoding="utf-8").strip().splitlines()
        assert len(holders) == 2
        assert {entry.split("|")[1] for entry in holders} == {"releases", "backups"}
    finally:
        os.write(release_descriptor, b"continue\ncontinue\n")
        os.close(ready_descriptor)
        os.close(release_descriptor)

    result = outcome.get(timeout=5)
    second_result = second_outcome.get(timeout=5)
    thread.join(timeout=5)
    second_thread.join(timeout=5)
    if isinstance(result, BaseException):
        raise result
    if isinstance(second_result, BaseException):
        raise second_result
    assert (store.remote.lock_root / "lifecycle.lock.meta").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "unsafe_root",
    (
        "deployment",
        "managed",
        "release-root",
        "backup-root",
        "releases",
        "activations",
        "backups",
        "adoptions",
        "adoption-transactions",
        "manifests",
    ),
)
def test_real_snapshot_refuses_unsafe_existing_roots_before_traversal(tmp_path: Path, unsafe_root: str) -> None:
    store, paths = snapshot_subprocess_store(tmp_path)
    root = {
        "deployment": paths["deployment"],
        "managed": paths["managed"],
        "release-root": paths["releases"],
        "backup-root": paths["backups"],
    }.get(unsafe_root, paths["deployment"] / unsafe_root)
    root.chmod(0o777)

    with pytest.raises(OpsError) as raised:
        store.read(operation="releases", lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY


def test_real_snapshot_refuses_a_special_bit_root_mode(tmp_path: Path) -> None:
    store, paths = snapshot_subprocess_store(tmp_path)
    paths["deployment"].chmod(0o1557)

    with pytest.raises(OpsError) as raised:
        store.read(operation="releases", lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY


def test_remote_store_rejects_a_managed_root_with_path_traversal_segments() -> None:
    with pytest.raises(ValueError):
        RemoteLifecycleStore(
            RecordingRemote(),
            deployment_root=PurePosixPath("/opt/taskman/../outside"),
            managed_root=PurePosixPath("/opt/taskman"),
            release_root=PurePosixPath("/opt/taskman/releases"),
            backup_root=PurePosixPath("/var/backups/taskman"),
        )
