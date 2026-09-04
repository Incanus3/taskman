from __future__ import annotations

from datetime import UTC, datetime
from multiprocessing import Event, Process
import json
import os
from pathlib import Path, PurePosixPath

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.manifests import ArtifactManifest
from taskman_ops.remote import CommandResult
from taskman_ops.releases.records import ActivationRecord, BackupRecord, LifecycleStore, ReleaseRecord, RemoteLifecycleStore
from taskman_ops.releases.locking import lifecycle_lock
from taskman_ops.workflows.backups import list_backups
from taskman_ops.workflows.releases import list_releases


RELEASE_A = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_B = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
FIRST = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
SECOND = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)


def store_for(tmp_path: Path) -> LifecycleStore:
    return LifecycleStore(
        deployment_root=tmp_path / "deployments",
        managed_root=tmp_path / "taskman",
        release_root=tmp_path / "taskman" / "releases",
        backup_root=tmp_path / "backups",
        owner_uid=os.getuid(),
    )


def _hold_exclusive_lock(path: str, ready: Event, release: Event) -> None:
    with lifecycle_lock(Path(path), operation="deploy", exclusive=True, timeout_seconds=1, owner_uid=os.getuid()):
        ready.set()
        assert release.wait(timeout=5)


def release(release_id: str, installed_at: datetime) -> ReleaseRecord:
    return ReleaseRecord(1, release_id, "c" * 64, installed_at, None, None, None, "no-change")


def activate(activation_id: str, previous: str | None, candidate: str, at: datetime, policy: str = "no-change") -> ActivationRecord:
    return ActivationRecord(1, activation_id, previous, candidate, at, None, policy)  # type: ignore[arg-type]


def manifest(release_id: str, revision: str) -> ArtifactManifest:
    return ArtifactManifest.from_mapping(
        {
            "schema_version": 1,
            "application": "taskman",
            "application_version": "0.2.0",
            "source_revision": revision,
            "release_id": release_id,
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
    )


def test_release_discovery_sorts_locked_remote_records_and_remote_manifests() -> None:
    first = ReleaseRecord(1, RELEASE_A, "c" * 64, FIRST, FIRST, None, None, "no-change")
    second = ReleaseRecord(1, RELEASE_B, "c" * 64, SECOND, SECOND, RELEASE_A, None, "no-change")
    remote = SnapshotRemote(
        {
            "schema_version": 1,
            "records": {
                "releases": [first.to_mapping(), second.to_mapping()],
                "activations": [
                    activate("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_A, FIRST).to_mapping(),
                    activate("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_A, RELEASE_B, SECOND).to_mapping(),
                ],
                "backups": [],
                "adoptions": [],
            },
            "current_target": f"/opt/taskman/releases/{RELEASE_B}",
            "manifests": {RELEASE_A: manifest(RELEASE_A, "a" * 40).to_mapping(), RELEASE_B: manifest(RELEASE_B, "b" * 40).to_mapping()},
            "dump_states": {},
            "warnings": [],
        }
    )
    store = RemoteLifecycleStore(remote, PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman"))

    result = list_releases(store)

    assert [row["release_id"] for row in result.records] == [RELEASE_B, RELEASE_A]
    assert result.records[0]["status"] == "current"
    assert result.records[1]["status"] == "previous"
    assert (
        result.records[0]["hex_version"],
        result.records[0]["rebar3_version"],
    ) == ("2.5.1", "3.24.0")
    assert result.to_mapping()["schema_version"] == 1
    assert "release_id" in result.human()


def test_backup_discovery_sorts_remote_metadata_and_marks_stale_dump_without_pg_restore() -> None:
    newest = BackupRecord(1, "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SECOND, 4, 4, "taskman_prod", None, None, "scheduled", True, PurePosixPath("/var/backups/taskman/present.dump"))
    older = BackupRecord(1, "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", FIRST, 10, 10, "taskman_prod", None, None, "scheduled", True, PurePosixPath("/var/backups/taskman/missing.dump"))
    remote = SnapshotRemote(
        {
            "schema_version": 1,
            "records": {"releases": [], "activations": [], "backups": [newest.to_mapping(), older.to_mapping()], "adoptions": []},
            "current_target": None,
            "manifests": {},
            "dump_states": {"/var/backups/taskman/present.dump": "present", "/var/backups/taskman/missing.dump": "stale"},
            "warnings": [],
        }
    )
    store = RemoteLifecycleStore(remote, PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman"))

    result = list_backups(store)

    assert [row["backup_id"] for row in result.records] == [
        "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    assert result.records[0]["dump_state"] == "present"
    assert result.records[1]["dump_state"] == "stale"
    assert result.to_mapping()["records"][0]["validated"] is True


def test_discovery_returns_empty_success_and_refuses_contradictory_remote_metadata() -> None:
    remote = SnapshotRemote(
        {"schema_version": 1, "records": {"releases": [], "activations": [], "backups": [], "adoptions": []}, "current_target": None, "manifests": {}, "dump_states": {}, "warnings": []}
    )
    remote_store = RemoteLifecycleStore(remote, PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman"))
    assert list_releases(remote_store).records == ()
    assert list_backups(remote_store).records == ()

    contradictory = SnapshotRemote(
        {
            "schema_version": 1,
            "records": {
                "releases": [release(RELEASE_A, FIRST).to_mapping()],
                "activations": [activate("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", RELEASE_B, RELEASE_A, FIRST).to_mapping()],
                "backups": [],
                "adoptions": [],
            },
            "current_target": f"/opt/taskman/releases/{RELEASE_A}",
            "manifests": {RELEASE_A: manifest(RELEASE_A, "a" * 40).to_mapping()},
            "dump_states": {},
            "warnings": [],
        }
    )
    with pytest.raises(OpsError) as raised:
        list_releases(RemoteLifecycleStore(contradictory, PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman")))
    assert raised.value.status is ExitStatus.SAFETY


def test_backup_discovery_surfaces_remote_shared_or_exclusive_lock_holder_metadata() -> None:
    class ContendedRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(12, json.dumps({"schema_version": 1, "holder": {"operation": "deploy", "pid": 123, "started_at": "2026-09-05T12:00:00Z", "mode": "exclusive"}}))

    store = RemoteLifecycleStore(ContendedRemote(), PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman"))
    with pytest.raises(OpsError) as raised:
        list_backups(store, lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.LOCKED
    assert "deploy" in raised.value.message
    assert "pid 123" in raised.value.message


def test_discovery_refuses_malformed_remote_lock_holder_metadata() -> None:
    class ContendedRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(12, json.dumps({"schema_version": 1, "holder": {"operation": "deploy", "pid": 0, "started_at": "not-a-time", "mode": "shared"}}))

    store = RemoteLifecycleStore(ContendedRemote(), PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman"))

    with pytest.raises(OpsError) as raised:
        list_releases(store)

    assert raised.value.status is ExitStatus.SAFETY


class SnapshotRemote:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((argv, kwargs))
        return CommandResult(0, json.dumps(self.snapshot))


def test_backup_discovery_reads_dump_state_from_the_locked_remote_snapshot() -> None:
    backup = BackupRecord(
        1,
        "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        SECOND,
        4,
        4,
        "taskman_prod",
        None,
        None,
        "scheduled",
        True,
        PurePosixPath("/var/backups/taskman/present.dump"),
    )
    remote = SnapshotRemote(
        {
            "schema_version": 1,
            "records": {"releases": [], "activations": [], "backups": [backup.to_mapping()], "adoptions": []},
            "current_target": None,
            "manifests": {},
            "dump_states": {"/var/backups/taskman/present.dump": "present"},
            "warnings": [],
        }
    )
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    result = list_backups(store, lock_timeout_seconds=0)

    assert result.records[0]["dump_state"] == "present"
    assert remote.calls[0][0][:2] == ("sh", "-ceu")


def test_backup_discovery_refuses_an_unknown_remote_dump_state_as_contradictory() -> None:
    backup = BackupRecord(1, "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SECOND, 4, 4, "taskman_prod", None, None, "scheduled", True, PurePosixPath("/var/backups/taskman/present.dump"))
    remote = SnapshotRemote(
        {
            "schema_version": 1,
            "records": {"releases": [], "activations": [], "backups": [backup.to_mapping()], "adoptions": []},
            "current_target": None,
            "manifests": {},
            "dump_states": {"/var/backups/taskman/present.dump": "untrusted"},
            "warnings": [],
        }
    )
    store = RemoteLifecycleStore(remote, PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman"))

    with pytest.raises(OpsError) as raised:
        list_backups(store)

    assert raised.value.status is ExitStatus.SAFETY


def test_discovery_rejects_controller_local_lifecycle_stores() -> None:
    local = LifecycleStore(
        Path("/controller/deployments"),
        Path("/controller/taskman"),
        Path("/controller/taskman/releases"),
        Path("/controller/backups"),
        owner_uid=os.getuid(),
    )

    with pytest.raises(TypeError, match="remote lifecycle store"):
        list_releases(local)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="remote lifecycle store"):
        list_backups(local)  # type: ignore[arg-type]


def test_adopted_release_discovery_keeps_known_target_platform_fields() -> None:
    from taskman_ops.releases.records import AdoptionRecord

    adoption = AdoptionRecord(
        1,
        RELEASE_A,
        FIRST,
        PurePosixPath("/opt/taskman/releases/manual-current"),
        "a" * 64,
        "0.2.0",
        "unknown",
        "unknown",
        (),
    )
    release_record = ReleaseRecord(1, RELEASE_A, None, FIRST, FIRST, None, None, "adopted")
    activation = ActivationRecord(1, "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_A, FIRST, None, "adopted")
    remote = SnapshotRemote(
        {
            "schema_version": 1,
            "records": {"releases": [release_record.to_mapping()], "activations": [activation.to_mapping()], "backups": [], "adoptions": [adoption.to_mapping()]},
            "current_target": "/opt/taskman/releases/manual-current",
            "manifests": {},
            "dump_states": {},
            "warnings": [],
        }
    )
    store = RemoteLifecycleStore(remote, PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman"))

    row = list_releases(store).records[0]

    assert row["source_revision"] == "unknown"
    assert (row["target_os"], row["architecture"], row["otp_version"]) == ("ubuntu26.04", "amd64", "27.3.4.6")
    assert (row["hex_version"], row["rebar3_version"]) == ("unknown", "unknown")


def test_host_inventory_warnings_have_json_and_human_parity() -> None:
    warnings = [
        "unexpected deployment-root entry: investigate",
        "unrecognized release directory: manual-copy",
        "orphan backup dump: left-behind.dump",
    ]
    remote = SnapshotRemote(
        {
            "schema_version": 1,
            "records": {"releases": [], "activations": [], "backups": [], "adoptions": []},
            "current_target": None,
            "manifests": {},
            "dump_states": {},
            "warnings": warnings,
        }
    )
    store = RemoteLifecycleStore(remote, PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/var/backups/taskman"))

    result = list_backups(store)

    assert result.to_mapping()["warnings"] == sorted(warnings)
    assert all(warning in result.human() for warning in warnings)
