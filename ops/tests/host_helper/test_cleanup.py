"""Packaged cleanup-operation contracts."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request
from taskman_ops.host_helper.lifecycle import (
    ActivationRecord,
    BackupRecord,
    LifecycleStore,
    ReleaseRecord,
)
from taskman_ops.host_helper.paths import ManagedPaths


def test_packaged_cleanup_refuses_an_incomplete_request_without_unavailable_dispatch(tmp_path: Path) -> None:
    """Cleanup must reject unspecified deletion authority before touching paths."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="cleanup",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={},
    )

    completed = subprocess.run(
        ["python3", "-I", str(package.path)], input=encode_request(request), capture_output=True, check=False
    )

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "refused"
    assert result.stage == "cleanup-preflight"
    assert result.warnings == ()


def test_packaged_cleanup_reports_a_confirmed_empty_plan_as_a_noop(tmp_path: Path) -> None:
    """An exact empty deletion plan is safe and must not be reported as a mutation."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="cleanup",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"lifecycle": {"activations": [], "backups": [], "releases": []}},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={"action": "execute", "targets": [], "release_retention": 3, "backup_retention": 14, "database_port": 5432},
    )

    completed = subprocess.run(
        ["python3", "-I", str(package.path)], input=encode_request(request), capture_output=True, check=False
    )

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "no_change"
    assert result.stage == "cleanup"
    assert result.changed_stages == ()
    assert result.residue_paths == ()


def test_packaged_cleanup_removes_only_the_confirmed_lifecycle_backed_backup(tmp_path: Path) -> None:
    """Replacing exact record authority with a path prefix would permit data loss."""

    paths = ManagedPaths.from_mapping({"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")})
    store = LifecycleStore(paths)
    store.backup_root.mkdir()
    backup_id = "backup-0123456789abcdef0123456789abcdef"
    dump = store.backup_root / f"{backup_id}.dump"
    dump.write_bytes(b"validated")
    dump.chmod(0o600)
    store.write_backup(
        BackupRecord(1, backup_id, datetime.now(UTC).replace(microsecond=0), 9, 9, "taskman_prod", None, None, "scheduled", True, paths.backup_root / dump.name)
    )
    retained_id = "backup-ffffffffffffffffffffffffffffffff"
    retained = store.backup_root / f"{retained_id}.dump"
    retained.write_bytes(b"validated")
    retained.chmod(0o600)
    store.write_backup(
        BackupRecord(1, retained_id, datetime.now(UTC).replace(microsecond=0), 9, 9, "taskman_prod", None, None, "scheduled", True, paths.backup_root / retained.name)
    )
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="cleanup",
        operation_id="op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        expected_state={"lifecycle": {"activations": [], "backups": [backup_id, retained_id], "releases": []}},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={"action": "execute", "targets": [{"authority": None, "identifier": backup_id, "kind": "backup", "path": dump.as_posix(), "recoverable": False}], "release_retention": 3, "backup_retention": 1, "database_port": 5432},
    )

    completed = subprocess.run(
        ["python3", "-I", str(package.path)], input=encode_request(request), capture_output=True, check=False
    )

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "succeeded"
    assert result.changed_stages == ("cleanup",)
    assert result.lifecycle["removed"][0]["identifier"] == backup_id
    assert not dump.exists()

    rerun = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=encode_request(request), capture_output=True, check=False,
    )
    rerun_result = decode_result(rerun.stdout)
    assert rerun_result.outcome == "no_change"
    assert rerun_result.changed_stages == ()


def test_packaged_cleanup_retains_exact_record_residue_after_partial_deletion(tmp_path: Path) -> None:
    """A record-unlink failure after dump removal must retain recoverable evidence."""

    paths = ManagedPaths.from_mapping({"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")})
    store = LifecycleStore(paths)
    store.backup_root.mkdir()
    backup_id = "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    dump = store.backup_root / f"{backup_id}.dump"
    dump.write_bytes(b"validated")
    dump.chmod(0o600)
    store.write_backup(
        BackupRecord(1, backup_id, datetime.now(UTC).replace(microsecond=0), 9, 9, "taskman_prod", None, None, "scheduled", True, paths.backup_root / dump.name)
    )
    retained_id = "backup-ffffffffffffffffffffffffffffffff"
    retained = store.backup_root / f"{retained_id}.dump"
    retained.write_bytes(b"validated")
    retained.chmod(0o600)
    store.write_backup(
        BackupRecord(1, retained_id, datetime.now(UTC).replace(microsecond=0), 9, 9, "taskman_prod", None, None, "scheduled", True, paths.backup_root / retained.name)
    )
    records = store.deployment_root / "backups"
    records.chmod(0o500)
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="cleanup",
        operation_id="op-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        expected_state={"lifecycle": {"activations": [], "backups": [backup_id, retained_id], "releases": []}},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={"action": "execute", "targets": [{"authority": None, "identifier": backup_id, "kind": "backup", "path": dump.as_posix(), "recoverable": False}], "release_retention": 3, "backup_retention": 1, "database_port": 5432},
    )
    try:
        completed = subprocess.run(
            ["python3", "-I", str(package.path)], input=encode_request(request), capture_output=True, check=False
        )
    finally:
        records.chmod(0o750)

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "failed"
    assert result.stage == "cleanup"
    assert result.changed_stages == ("cleanup",)
    assert result.residue_paths == ((records / f"{backup_id}.json").as_posix(),)
    assert result.warnings == ("unable to remove operation residue",)

    finished = _invoke(package.path, request)
    rerun = _invoke(package.path, request)

    assert finished.outcome == "succeeded"
    assert finished.changed_stages == ("cleanup",)
    assert not (records / f"{backup_id}.json").exists()
    assert rerun.outcome == "no_change"
    assert rerun.changed_stages == ()


@pytest.mark.parametrize(
    ("fsync_boundary", "expected_residue", "finished_outcome"),
    [
        ("dump", "record", "succeeded"),
        ("record", None, "no_change"),
    ],
)
def test_packaged_cleanup_derives_unlink_effect_after_fsync_failure(
    tmp_path: Path,
    fsync_boundary: str,
    expected_residue: str | None,
    finished_outcome: str,
) -> None:
    """Each missing path after fsync failure is reported and safely finishable."""

    paths = ManagedPaths.from_mapping({
        "install_root": str(tmp_path / "install"),
        "backup_root": str(tmp_path / "backups"),
    })
    store = LifecycleStore(paths)
    store.backup_root.mkdir()
    identifier = "backup-" + "b" * 32
    retained_id = "backup-" + "f" * 32
    for backup_id in (identifier, retained_id):
        dump = store.backup_root / f"{backup_id}.dump"
        dump.write_bytes(b"validated")
        dump.chmod(0o600)
        store.write_backup(
            BackupRecord(
                1, backup_id, datetime.now(UTC).replace(microsecond=0),
                9, 9, "taskman_prod", None, None, "scheduled", True,
                paths.backup_root / dump.name,
            )
        )
    target_path = store.backup_root / f"{identifier}.dump"
    target = {
        "authority": None,
        "identifier": identifier,
        "kind": "backup",
        "path": target_path.as_posix(),
        "recoverable": False,
    }
    request = HostRequest(
        1,
        "cleanup",
        "op-" + "b" * 32,
        {"lifecycle": {
            "activations": (),
            "backups": (identifier, retained_id),
            "releases": (),
        }},
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        },
        {
            "action": "execute",
            "targets": (target,),
            "release_retention": 3,
            "backup_retention": 1,
            "database_port": 5432,
        },
    )
    package = build_helper_package(tmp_path / "taskman-host.pyz").path

    record = store.deployment_root / "backups" / f"{identifier}.json"
    directory = target_path.parent if fsync_boundary == "dump" else record.parent
    failed = _run_fsync_failure(package, request, directory)
    finished = _invoke(package, request)
    rerun = _invoke(package, request)

    assert failed.outcome == "failed"
    assert failed.changed_stages == ("cleanup",)
    assert failed.residue_paths == (
        (record.as_posix(),) if expected_residue == "record" else ()
    )
    assert finished.outcome == finished_outcome
    assert not record.exists()
    assert rerun.outcome == "no_change"


def test_packaged_cleanup_refuses_stale_exact_targets_without_deletion(
    tmp_path: Path,
) -> None:
    paths = ManagedPaths.from_mapping({
        "install_root": str(tmp_path / "install"),
        "backup_root": str(tmp_path / "backups"),
    })
    store = LifecycleStore(paths)
    store.backup_root.mkdir()
    identifier = "backup-" + "a" * 32
    dump = store.backup_root / f"{identifier}.dump"
    dump.write_bytes(b"validated")
    dump.chmod(0o600)
    store.write_backup(
        BackupRecord(
            1, identifier, datetime.now(UTC).replace(microsecond=0),
            9, 9, "taskman_prod", None, None, "scheduled", True,
            paths.backup_root / dump.name,
        )
    )
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        1, "cleanup", "op-" + "a" * 32,
        {"lifecycle": {
            "activations": (), "backups": (identifier,), "releases": (),
        }},
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        },
        {
            "action": "execute", "targets": ({
                "authority": None, "identifier": identifier, "kind": "backup",
                "path": dump.as_posix(), "recoverable": False,
            },),
            "release_retention": 3, "backup_retention": 1,
            "database_port": 5432,
        },
    )

    result = _invoke(package.path, request)

    assert result.outcome == "refused"
    assert result.changed_stages == ()
    assert dump.is_file()


def test_packaged_cleanup_reports_lifecycle_lock_contention(
    tmp_path: Path,
) -> None:
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        1, "cleanup", "op-" + "a" * 32,
        {"lifecycle": {
            "activations": (), "backups": (), "releases": (),
        }},
        {
            "install_root": str(tmp_path / "install"),
            "backup_root": str(tmp_path / "backups"),
        },
        {
            "action": "execute", "targets": (),
            "release_retention": 3, "backup_retention": 1,
            "database_port": 5432,
        },
    )
    lock_root = tmp_path / ".taskman-lock"
    lock_root.mkdir(mode=0o750)
    lock_path = lock_root / "lifecycle.lock"
    lock = lock_path.open("a+")
    lock_path.chmod(0o600)
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        result = _invoke(package.path, request)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

    assert result.outcome == "refused"
    assert result.stage == "lifecycle-lock"
    assert len(result.recovery_actions) == 1


def test_packaged_cleanup_removes_exact_completed_staging_receipt(
    tmp_path: Path,
) -> None:
    store, paths = _empty_store(tmp_path)
    uploads = store.deployment_root / "uploads"
    uploads.mkdir(mode=0o750)
    identifier = "stage-" + "a" * 32
    record = uploads / f"{identifier}.json"
    record.write_text(json.dumps({
        "schema_version": 1, "staging_id": identifier,
        "state": "completed", "completed_at": "2026-09-05T12:00:00Z",
    }), encoding="utf-8")
    record.chmod(0o600)
    target = {
        "kind": "staging", "identifier": identifier,
        "path": record.as_posix(), "recoverable": False,
        "authority": {"record_path": record.as_posix(), "state": "completed"},
    }
    request = _cleanup_request(paths, (target,))
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    result = _invoke(package.path, request)

    assert result.outcome == "succeeded"
    assert not record.exists()


@pytest.mark.parametrize("partial_failure", (False, True))
def test_packaged_cleanup_handles_exact_eligible_release_tree(
    tmp_path: Path,
    partial_failure: bool,
) -> None:
    paths = ManagedPaths.from_mapping({
        "install_root": str(tmp_path / "install"),
        "backup_root": str(tmp_path / "backups"),
    })
    store = LifecycleStore(paths)
    releases = (
        "0.1.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
        "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
        "0.3.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6",
    )
    moments = (
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 5, 12, 1, tzinfo=UTC),
        datetime(2026, 9, 5, 12, 2, tzinfo=UTC),
    )
    for index, identifier in enumerate(releases):
        directory = store.release_root / identifier
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "owned").write_text("release", encoding="utf-8")
        store.write_release(
            ReleaseRecord(
                1, identifier, chr(97 + index) * 64,
                moments[index], moments[index],
                releases[index - 1] if index else None, None, "no-change",
            )
        )
        store.write_activation(
            ActivationRecord(
                1, f"activation-{chr(97 + index) * 32}",
                releases[index - 1] if index else None,
                identifier, moments[index], None, "no-change",
            )
        )
    store.current_link.parent.mkdir(parents=True, exist_ok=True)
    store.current_link.symlink_to(store.release_root / releases[-1])
    target_path = store.release_root / releases[0]
    target = {
        "kind": "release", "identifier": releases[0],
        "path": target_path.as_posix(), "recoverable": False,
        "authority": None,
    }
    request = HostRequest(
        1, "cleanup", "op-" + "d" * 32,
        {"lifecycle": {
            "releases": releases,
            "activations": tuple(
                f"activation-{char * 32}" for char in ("a", "b", "c")
            ),
            "backups": (),
        }},
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        },
        {
            "action": "execute", "targets": (target,),
            "release_retention": 1, "backup_retention": 1,
            "database_port": 5432,
        },
    )
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    if partial_failure:
        result = _run_release_failure(package.path, request)
        assert result.outcome == "failed"
        assert result.changed_stages == ("cleanup",)
        assert result.residue_paths == (target_path.as_posix(),)
    else:
        result = _invoke(package.path, request)
        assert result.outcome == "succeeded"
        assert not target_path.exists()
        rerun = _invoke(package.path, request)
        assert rerun.outcome == "no_change"


def test_packaged_cleanup_recognizes_release_removed_before_fsync_failed(
    tmp_path: Path,
) -> None:
    """A deleted exact release tree is a completed rerun even if durability reporting failed."""

    paths = ManagedPaths.from_mapping({
        "install_root": str(tmp_path / "install"),
        "backup_root": str(tmp_path / "backups"),
    })
    store = LifecycleStore(paths)
    identifiers = tuple(
        f"0.{index}.0-{char * 12}-ubuntu26.04-amd64-otp27.3.4.6"
        for index, char in enumerate(("a", "b", "c"), start=1)
    )
    moment = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    for index, identifier in enumerate(identifiers):
        activated = moment + timedelta(minutes=index)
        directory = store.release_root / identifier
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "owned").write_text("release", encoding="utf-8")
        previous = identifiers[index - 1] if index else None
        store.write_release(
            ReleaseRecord(
                1, identifier, chr(97 + index) * 64, activated, activated,
                previous, None, "no-change",
            )
        )
        store.write_activation(
            ActivationRecord(
                1, f"activation-{chr(97 + index) * 32}", previous,
                identifier, activated, None, "no-change",
            )
        )
    store.current_link.parent.mkdir(parents=True, exist_ok=True)
    store.current_link.symlink_to(store.release_root / identifiers[-1])
    target_path = store.release_root / identifiers[0]
    target = {
        "kind": "release", "identifier": identifiers[0],
        "path": target_path.as_posix(), "recoverable": False,
        "authority": None,
    }
    request = HostRequest(
        1, "cleanup", "op-" + "f" * 32,
        {"lifecycle": {
            "releases": identifiers,
            "activations": tuple(
                f"activation-{char * 32}" for char in ("a", "b", "c")
            ),
            "backups": (),
        }},
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        },
        {
            "action": "execute", "targets": (target,),
            "release_retention": 1, "backup_retention": 1,
            "database_port": 5432,
        },
    )
    package = build_helper_package(tmp_path / "taskman-host.pyz").path

    failed = _run_fsync_failure(package, request, target_path.parent)
    rerun = _invoke(package, request)

    assert failed.outcome == "failed"
    assert failed.changed_stages == ("cleanup",)
    assert failed.residue_paths == ()
    assert rerun.outcome == "no_change"


@pytest.mark.parametrize(
    ("failure", "changed", "record_retained", "finished_outcome"),
    [
        ("database-terminate", False, True, None),
        ("database-drop", False, True, None),
        ("database-drop-after", True, True, "succeeded"),
        ("database-record", True, True, "succeeded"),
        ("database-record-fsync", True, False, "no_change"),
    ],
)
def test_packaged_cleanup_database_failures_retain_exact_recovery_evidence(
    tmp_path: Path,
    failure: str,
    changed: bool,
    record_retained: bool,
    finished_outcome: str | None,
) -> None:
    store, paths = _empty_store(tmp_path)
    restores = store.deployment_root / "restores"
    restores.mkdir(mode=0o750)
    identifier = "recovery-" + "b" * 32
    database = "taskman_recovery_" + "b" * 32
    record = restores / f"{identifier}.json"
    authority = {
        "record_path": record.as_posix(),
        "source_backup_id": "backup-" + "a" * 32,
        "pre_restore_backup_id": "backup-" + "b" * 32,
        "intended_release_id":
            "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
        "recovery_database": database, "state": "retained",
    }
    record.write_text(json.dumps({
        "schema_version": 1, "recovery_id": identifier,
        "database": "taskman_prod", "recovery_database": database,
        "source_backup_id": authority["source_backup_id"],
        "pre_restore_backup_id": authority["pre_restore_backup_id"],
        "intended_release_id": authority["intended_release_id"],
        "state": "retained", "created_at": "2026-09-05T12:00:00Z",
    }), encoding="utf-8")
    record.chmod(0o600)
    target = {
        "kind": "database", "identifier": identifier,
        "path": f"/database/{identifier}", "recoverable": True,
        "authority": authority,
    }
    request = _cleanup_request(paths, (target,))
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    result = _run_packaged_failure(package.path, request, failure)

    assert result.outcome == "failed"
    assert result.stage == "cleanup"
    assert bool(result.changed_stages) is changed
    assert (record.as_posix() in result.residue_paths) is record_retained
    assert len(result.recovery_actions) == 1
    if finished_outcome is not None:
        finished = _run_packaged_failure(
            package.path,
            request,
            "database-already-absent",
        )
        rerun = _run_packaged_failure(
            package.path,
            request,
            "database-already-absent",
        )
        assert finished.outcome == finished_outcome
        assert not record.exists()
        assert rerun.outcome == "no_change"


def test_packaged_cleanup_recovers_an_uncertain_drop_after_probe_failure(
    tmp_path: Path,
) -> None:
    """A failed post-drop probe must preserve uncertainty until a retry proves it."""

    store, paths = _empty_store(tmp_path)
    restores = store.deployment_root / "restores"
    restores.mkdir(mode=0o750)
    identifier = "recovery-" + "9" * 32
    database = "taskman_recovery_" + "9" * 32
    record = restores / f"{identifier}.json"
    authority = {
        "record_path": record.as_posix(),
        "source_backup_id": "backup-" + "a" * 32,
        "pre_restore_backup_id": "backup-" + "b" * 32,
        "intended_release_id":
            "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
        "recovery_database": database,
        "state": "retained",
    }
    record.write_text(json.dumps({
        "schema_version": 1,
        "recovery_id": identifier,
        "database": "taskman_prod",
        "recovery_database": database,
        "source_backup_id": authority["source_backup_id"],
        "pre_restore_backup_id": authority["pre_restore_backup_id"],
        "intended_release_id": authority["intended_release_id"],
        "state": "retained",
        "created_at": "2026-09-05T12:00:00Z",
    }), encoding="utf-8")
    record.chmod(0o600)
    target = {
        "kind": "database",
        "identifier": identifier,
        "path": f"/database/{identifier}",
        "recoverable": True,
        "authority": authority,
    }
    request = _cleanup_request(paths, (target,))
    package = build_helper_package(tmp_path / "taskman-host.pyz").path
    state = tmp_path / "database-state"
    state.write_text("present", encoding="utf-8")

    failed = _run_uncertain_database_cleanup(
        package, request, state, fail_probe=True
    )
    retried = _run_uncertain_database_cleanup(
        package, request, state, fail_probe=False
    )

    assert failed.outcome == "failed"
    assert failed.stage == "cleanup"
    assert failed.changed_stages == ("cleanup",)
    assert failed.lifecycle["database_state"] == "unknown"
    assert failed.residue_paths == (target["path"], record.as_posix())
    assert failed.recovery_actions == (
        f"prove whether {database} exists before retrying exact cleanup",
    )
    assert retried.outcome == "succeeded"
    assert retried.changed_stages == ("cleanup",)
    assert not record.exists()


def test_packaged_cleanup_removes_and_recognizes_rerun_of_retained_database(
    tmp_path: Path,
) -> None:
    store, paths = _empty_store(tmp_path)
    restores = store.deployment_root / "restores"
    restores.mkdir(mode=0o750)
    identifier = "recovery-" + "e" * 32
    database = "taskman_recovery_" + "e" * 32
    record = restores / f"{identifier}.json"
    authority = {
        "record_path": record.as_posix(),
        "source_backup_id": "backup-" + "a" * 32,
        "pre_restore_backup_id": "backup-" + "b" * 32,
        "intended_release_id":
            "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
        "recovery_database": database, "state": "retained",
    }
    record.write_text(json.dumps({
        "schema_version": 1, "recovery_id": identifier,
        "database": "taskman_prod", "recovery_database": database,
        "source_backup_id": authority["source_backup_id"],
        "pre_restore_backup_id": authority["pre_restore_backup_id"],
        "intended_release_id": authority["intended_release_id"],
        "state": "retained", "created_at": "2026-09-05T12:00:00Z",
    }), encoding="utf-8")
    record.chmod(0o600)
    target = {
        "kind": "database", "identifier": identifier,
        "path": f"/database/{identifier}", "recoverable": True,
        "authority": authority,
    }
    request = _cleanup_request(paths, (target,))
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    first = _run_packaged_failure(package.path, request, "")
    second = _run_packaged_failure(
        package.path,
        request,
        "database-already-absent",
    )

    assert first.outcome == "succeeded"
    assert first.lifecycle["recoverability"] == (True,)
    assert second.outcome == "no_change"
    assert second.changed_stages == ()


def _empty_store(tmp_path: Path) -> tuple[LifecycleStore, ManagedPaths]:
    paths = ManagedPaths.from_mapping({
        "install_root": str(tmp_path / "install"),
        "backup_root": str(tmp_path / "backups"),
    })
    store = LifecycleStore(paths)
    store.deployment_root.mkdir(parents=True, mode=0o750)
    return store, paths


def _cleanup_request(
    paths: ManagedPaths,
    targets: tuple[dict[str, object], ...],
) -> HostRequest:
    return HostRequest(
        1, "cleanup", "op-" + "c" * 32,
        {"lifecycle": {
            "activations": (), "backups": (), "releases": (),
        }},
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        },
        {
            "action": "execute", "targets": targets,
            "release_retention": 3, "backup_retention": 1,
            "database_port": 5432,
        },
    )


def _run_packaged_failure(
    package: Path,
    request: HostRequest,
    failure: str,
):
    harness = r'''
import io, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.cleanup as operation
failure = sys.argv[2]
calls = 0
database_exists = failure != "database-already-absent"
def postgres(_port, _sql):
    global calls, database_exists
    calls += 1
    if failure == "database-terminate" and calls == 1:
        raise subprocess.CalledProcessError(1, "terminate")
    if failure == "database-drop" and calls == 2:
        raise subprocess.CalledProcessError(1, "drop")
    if failure == "database-drop-after" and calls == 2:
        database_exists = False
        raise subprocess.CalledProcessError(1, "drop")
    if failure == "database-already-absent":
        raise AssertionError("cleanup retried an already absent database")
    if _sql.startswith("DROP DATABASE"):
        database_exists = False
operation._postgres = postgres
operation._database_absent = lambda *_args: not database_exists
if failure == "database-record":
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path.parent.name == "restores":
            raise OSError("injected")
        return original(path, *args, **kwargs)
    Path.unlink = unlink
if failure == "database-record-fsync":
    original_fsync = operation._fsync_directory
    def fsync(path):
        if path.name == "restores":
            raise OSError("injected")
        return original_fsync(path)
    operation._fsync_directory = fsync
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package), failure],
        input=encode_request(request), capture_output=True, check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _run_fsync_failure(
    package: Path,
    request: HostRequest,
    directory: Path,
):
    harness = r'''
import io, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.cleanup as operation
directory = Path(sys.argv[2])
original = operation._fsync_directory
failed = False
def fsync(path):
    global failed
    if path == directory and not failed:
        failed = True
        raise OSError("injected fsync failure")
    return original(path)
operation._fsync_directory = fsync
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package), str(directory)],
        input=encode_request(request),
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _run_uncertain_database_cleanup(
    package: Path,
    request: HostRequest,
    state: Path,
    *,
    fail_probe: bool,
):
    harness = r'''
import io, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.cleanup as operation
state = Path(sys.argv[2])
fail_probe = sys.argv[3] == "fail"
probes = 0
def absent(_port, _database):
    global probes
    probes += 1
    if fail_probe and probes == 2:
        raise subprocess.CalledProcessError(1, "absence probe")
    return state.read_text(encoding="utf-8") == "absent"
def postgres(_port, sql):
    if sql.startswith("DROP DATABASE"):
        state.write_text("absent", encoding="utf-8")
        if fail_probe:
            raise subprocess.CalledProcessError(1, "drop")
operation._database_absent = absent
operation._postgres = postgres
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        [
            "python3",
            "-I",
            "-c",
            harness,
            str(package),
            str(state),
            "fail" if fail_probe else "resume",
        ],
        input=encode_request(request),
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _run_release_failure(package: Path, request: HostRequest):
    harness = r'''
import io, shutil, sys
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.cleanup as operation
original = shutil.rmtree
def partial(path):
    child = next(path.iterdir())
    child.unlink()
    raise OSError("injected partial release deletion")
operation.shutil.rmtree = partial
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package)],
        input=encode_request(request), capture_output=True, check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _invoke(package: Path, request: HostRequest):
    completed = subprocess.run(
        ["python3", "-I", str(package)],
        input=encode_request(request), capture_output=True, check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)
