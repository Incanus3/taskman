"""Packaged restore-operation contracts."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess

import pytest

from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_helper.lifecycle import (
    ActivationRecord,
    BackupRecord,
    LifecycleStore,
    ReleaseRecord,
)
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_protocol import HostRequest, HostResult, decode_result, encode_request


def test_packaged_restore_refuses_an_incomplete_request_without_unavailable_dispatch(tmp_path: Path) -> None:
    """Restore must reject missing authority before any host mutation."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="restore",
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
    assert result.stage == "restore-preflight"
    assert result.warnings == ()


def test_packaged_restore_rejects_missing_confirmed_dump_before_database_work(tmp_path: Path) -> None:
    """Treating an absent dump as a restore failure could stop a healthy database."""

    backup_id = "backup-0123456789abcdef0123456789abcdef"
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="restore",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"backup_id": backup_id},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={"backup_id": backup_id},
    )

    completed = subprocess.run(
        ["python3", "-I", str(package.path)], input=encode_request(request), capture_output=True, check=False
    )

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "refused"
    assert result.stage == "restore-preflight"
    assert result.lifecycle == {"backup_id": backup_id, "dump_validated": False}


def test_packaged_restore_refuses_a_mismatched_digest_before_confirmation(tmp_path: Path) -> None:
    """A current backup record must bind the inspected dump bytes before confirmation."""

    package, request = _managed_restore(tmp_path, dump_sha256="a" * 64)
    inspection = HostRequest(
        request.protocol_version,
        request.operation,
        request.operation_id,
        request.expected_state,
        request.paths,
        {**request.parameters, "action": "inspect"},
    )

    result = _run_packaged(package, inspection, "")

    assert result.outcome == "refused"
    assert result.stage == "restore-preflight"
    assert result.changed_stages == ()
    assert result.lifecycle == {
        "backup_id": request.parameters["backup_id"],
        "dump_validated": False,
    }


def test_packaged_restore_rechecks_a_current_digest_under_the_lifecycle_lock(tmp_path: Path) -> None:
    """A same-size dump replacement after confirmation must not reach database mutation."""

    digest = hashlib.sha256(b"validated").hexdigest()
    package, request = _managed_restore(tmp_path, dump_sha256=digest)
    inspection = HostRequest(
        request.protocol_version,
        request.operation,
        request.operation_id,
        request.expected_state,
        request.paths,
        {**request.parameters, "action": "inspect"},
    )
    assert _run_packaged(package, inspection, "").stage == "restore-inspected"
    dump = Path(request.paths["backup_root"]) / f"{request.parameters['backup_id']}.dump"
    dump.write_bytes(b"replaced!")
    dump.chmod(0o600)

    result = _run_packaged(package, request, "")

    assert result.outcome == "refused"
    assert result.stage == "restore-preflight"
    assert result.changed_stages == ()
    assert result.lifecycle == {
        "backup_id": request.parameters["backup_id"],
        "dump_validated": False,
    }


def test_packaged_restore_accepts_a_legacy_record_without_a_digest(tmp_path: Path) -> None:
    """Legacy backup authority keeps its existing path, size, and format validation."""

    package, request = _managed_restore(tmp_path)
    inspection = HostRequest(
        request.protocol_version,
        request.operation,
        request.operation_id,
        request.expected_state,
        request.paths,
        {**request.parameters, "action": "inspect"},
    )

    result = _run_packaged(package, inspection, "")

    assert result.outcome == "succeeded"
    assert result.stage == "restore-inspected"
    assert result.lifecycle["dump_validated"] is True


@pytest.mark.parametrize(
    "damage",
    (
        "missing",
        "redirected",
        "non-directory",
        "wrong-owner",
        "mode-drifted",
        "locked-redirected",
    ),
)
def test_packaged_restore_refuses_unsafe_intended_release_before_mutation(
    tmp_path: Path,
    damage: str,
) -> None:
    """Restore may not back up, stop, or swap for an unsafe intended release."""

    package, request = _managed_restore(tmp_path)
    target = Path(request.paths["install_root"]) / "releases" / INTENDED
    if damage == "missing":
        target.rmdir()
    elif damage == "redirected":
        target.rmdir()
        target.symlink_to(
            Path(request.paths["install_root"]) / "releases" / CURRENT
        )
    elif damage == "non-directory":
        target.rmdir()
        target.write_text("not a release", encoding="utf-8")
    elif damage == "mode-drifted":
        target.chmod(0o777)

    result = _run_packaged(
        package,
        request,
        "",
        authority_damage=damage,
    )

    token = request.operation_id.removeprefix("op-")
    assert result.outcome == "refused"
    assert result.stage == "restore-preflight"
    assert result.changed_stages == ()
    assert result.recovery_actions
    assert not (
        Path(request.paths["backup_root"]) / f"backup-{token}.dump"
    ).exists()
    assert (
        Path(request.paths["install_root"]) / "current"
    ).readlink() == Path(request.paths["install_root"]) / "releases" / CURRENT


@pytest.mark.parametrize(
    ("failure", "stage"),
    [
        ("backup", "backup"),
        ("stop", "stop"),
        ("restore", "restore"),
        ("validation", "validation"),
        ("validation-cleanup", "validation"),
        ("swap", "swap"),
        ("selection", "selection"),
        ("selection-fsync", "selection"),
        ("start", "start"),
        ("verification", "verification"),
        ("records", "records"),
    ],
)
def test_packaged_restore_reports_each_mutation_failure_boundary(
    tmp_path: Path,
    failure: str,
    stage: str,
) -> None:
    """Each failure retains bounded state instead of collapsing to refusal."""

    package, request = _managed_restore(tmp_path)

    result = _run_packaged(package, request, failure)

    assert result.outcome == "failed"
    assert result.stage == stage
    assert result.lifecycle["backup_id"] == request.parameters["backup_id"]
    assert result.lifecycle["intended_release_id"] == INTENDED
    assert result.lifecycle["restore_recorded"] is False
    assert len(result.recovery_actions) <= 4
    assert all("secret-canary" not in value for value in result.recovery_actions)
    if failure == "validation-cleanup":
        assert result.residue_paths == (
            "/database/taskman_restore_" + "d" * 32,
        )
    if failure == "selection-fsync":
        assert "selection" in result.changed_stages
        assert result.lifecycle["selected_release_id"] == INTENDED


def test_packaged_restore_preserves_nested_backup_failure_evidence(
    tmp_path: Path,
) -> None:
    """The restore transaction must retain a nested backup's durable effects."""

    package, request = _managed_restore(tmp_path)

    result = _run_packaged(package, request, "backup-nested")

    assert result.outcome == "failed"
    assert result.stage == "records"
    assert result.changed_stages == ("backup",)
    assert result.residue_paths == ("/safe/backup-residue",)


def test_packaged_restore_resumes_a_published_pre_restore_dump(
    tmp_path: Path,
) -> None:
    """The same restore ID may finish its pre-restore backup before mutation."""

    package, request = _managed_restore(tmp_path)

    failed = _run_packaged(package, request, "backup-record")
    retried = _run_packaged(package, request, "")
    record = json.loads(
        (
            Path(request.paths["install_root"])
            / "deployments"
            / "backups"
            / f"backup-{'d' * 32}.json"
        ).read_text(encoding="utf-8")
    )

    assert failed.outcome == "failed"
    assert failed.stage == "records"
    assert failed.changed_stages == ("backup",)
    assert retried.outcome == "succeeded"
    assert retried.lifecycle["pre_restore_backup_id"] == "backup-" + "d" * 32
    assert retried.lifecycle["restore_recorded"] is True
    assert record["source_database_size_bytes"] == 1024


def test_packaged_restore_exact_rerun_is_a_noop(
    tmp_path: Path,
) -> None:
    package, request = _managed_restore(tmp_path)

    first = _run_packaged(package, request, "")
    second = _run_packaged(package, request, "")

    assert first.outcome == "succeeded"
    assert second.outcome == "no_change"
    assert second.stage == "already-restored"
    assert second.changed_stages == ()
    assert second.lifecycle["restore_recorded"] is True
    assert second.verification["release_id"] == INTENDED


def test_packaged_restore_finalizes_an_activation_whose_restore_record_failed(
    tmp_path: Path,
) -> None:
    """The correlated retry must publish retained-database authority without swapping again."""

    package, request = _managed_restore(tmp_path)

    failed = _run_packaged(package, request, "records")
    finalized = _run_packaged(package, request, "")
    fresh = HostRequest(
        request.protocol_version,
        request.operation,
        "op-" + "e" * 32,
        request.expected_state,
        request.paths,
        request.parameters,
    )
    refused = _run_packaged(package, fresh, "")

    recovery = "/database/taskman_recovery_" + "d" * 32
    assert failed.outcome == "failed"
    assert failed.stage == "records"
    assert failed.lifecycle["restore_recorded"] is False
    assert recovery in failed.residue_paths
    assert finalized.outcome == "succeeded"
    assert finalized.stage == "records-finalized"
    assert finalized.changed_stages == ("records",)
    assert finalized.lifecycle["restore_recorded"] is True
    assert finalized.lifecycle["recovery_database"] == "taskman_recovery_" + "d" * 32
    assert finalized.verification["release_id"] == INTENDED
    assert refused.outcome == "refused"
    assert refused.changed_stages == ()


@pytest.mark.parametrize(
    ("failure", "published"),
    (
        ("restore-record-link", False),
        ("restore-record-fsync", True),
    ),
)
def test_packaged_restore_resumes_each_restore_record_publication_boundary(
    tmp_path: Path,
    failure: str,
    published: bool,
) -> None:
    """A typed pending record effect must be finalized by the correlated retry."""

    package, request = _managed_restore(tmp_path)

    failed = _run_packaged(package, request, failure)

    token = "d" * 32
    directory = (
        Path(request.paths["install_root"])
        / "deployments"
        / "restores"
    )
    pending = directory / f".recovery-{token}.pending"
    record = directory / f"recovery-{token}.json"
    recovery = f"/database/taskman_recovery_{token}"
    assert failed.outcome == "failed"
    assert failed.stage == "records"
    assert failed.lifecycle["restore_recorded"] is published
    assert failed.lifecycle["recovery_database"] == f"taskman_recovery_{token}"
    assert failed.changed_stages[-1] == "records"
    assert failed.residue_paths == (recovery, pending.as_posix())
    assert pending.is_file()
    assert record.exists() is published

    retried = _run_packaged(package, request, "")

    assert retried.outcome == "succeeded"
    assert retried.stage == "records-finalized"
    assert retried.changed_stages == ("records",)
    assert retried.lifecycle["restore_recorded"] is True
    assert not pending.exists()


def test_packaged_restore_durably_publishes_the_restore_record_directory(
    tmp_path: Path,
) -> None:
    """Parent fsync must precede child record durability and be retryable."""

    package, request = _managed_restore(tmp_path)
    fsync_log = tmp_path / "restore-fsync.log"
    deployment = Path(request.paths["install_root"]) / "deployments"
    restores = deployment / "restores"

    failed = _run_packaged(
        package,
        request,
        "restore-directory-parent-fsync",
        fsync_log=fsync_log,
    )
    failed_paths = tuple(fsync_log.read_text(encoding="utf-8").splitlines())

    assert failed.outcome == "failed"
    assert failed.stage == "records"
    assert failed.lifecycle["restore_recorded"] is False
    assert failed.residue_paths == (
        "/database/taskman_recovery_" + "d" * 32,
        restores.as_posix(),
    )
    assert tuple(
        path for path in failed_paths if path in {deployment.as_posix(), restores.as_posix()}
    ) == (deployment.as_posix(),)

    fsync_log.unlink()
    retried = _run_packaged(package, request, "", fsync_log=fsync_log)
    retried_paths = tuple(fsync_log.read_text(encoding="utf-8").splitlines())

    assert retried.outcome == "succeeded"
    assert retried.stage == "records-finalized"
    assert tuple(
        path for path in retried_paths if path in {deployment.as_posix(), restores.as_posix()}
    ) == (
        deployment.as_posix(),
        restores.as_posix(),
        restores.as_posix(),
        restores.as_posix(),
    )


def test_packaged_completed_restore_reports_live_verification_failure(
    tmp_path: Path,
) -> None:
    """A completed rerun verification failure is not a preflight refusal."""

    package, request = _managed_restore(tmp_path)
    completed = _run_packaged(package, request, "")

    failed = _run_packaged(package, request, "verification")

    recovery = "/database/taskman_recovery_" + "d" * 32
    assert completed.outcome == "succeeded"
    assert failed.outcome == "failed"
    assert failed.stage == "verification"
    assert failed.lifecycle["selected_release_id"] == INTENDED
    assert failed.lifecycle["database_state"] == "restored-promoted"
    assert failed.lifecycle["recovery_id"] == "recovery-" + "d" * 32
    assert failed.lifecycle["recovery_database"] == "taskman_recovery_" + "d" * 32
    assert failed.lifecycle["restore_recorded"] is True
    assert failed.lifecycle["service_state"] == "unknown"
    assert failed.verification["status"] == "failed"
    assert failed.verification["checks"][0]["name"] == "taskman-service"
    assert failed.verification["checks"][0]["status"] == "failed"
    assert failed.residue_paths == (recovery,)
    assert any("retain taskman_recovery_" in item for item in failed.recovery_actions)


def test_packaged_restore_refuses_stale_confirmation(
    tmp_path: Path,
) -> None:
    package, request = _managed_restore(tmp_path)
    stale = HostRequest(
        1, "restore", "op-" + "e" * 32,
        {
            "backup_id": request.parameters["backup_id"],
            "current_release_id": INTENDED,
        },
        request.paths,
        request.parameters,
    )

    result = _run_packaged(package, stale, "")

    assert result.outcome == "refused"
    assert result.changed_stages == ()


def test_packaged_restore_reports_lifecycle_lock_contention(
    tmp_path: Path,
) -> None:
    import fcntl

    package, request = _managed_restore(tmp_path)
    lock_path = tmp_path / ".taskman-lock" / "lifecycle.lock"
    lock_path.parent.mkdir(mode=0o750)
    lock = lock_path.open("a+")
    lock_path.chmod(0o600)
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        result = _run_packaged(package, request, "")
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

    assert result.outcome == "refused"
    assert result.stage == "lifecycle-lock"


def test_packaged_restore_completes_the_guarded_swap_and_records_recovery(
    tmp_path: Path,
) -> None:
    package, request = _managed_restore(tmp_path)

    result = _run_packaged(package, request, "")

    assert result.outcome == "succeeded"
    assert result.stage == "records"
    assert result.changed_stages == (
        "backup", "stop", "restore", "validation", "swap",
        "selection", "start", "verification", "records",
    )
    assert result.lifecycle["selected_release_id"] == INTENDED
    assert result.lifecycle["database_state"] == "restored-promoted"
    assert result.lifecycle["restore_recorded"] is True
    record = (
        Path(request.paths["install_root"])
        / "deployments" / "restores"
        / f"{result.lifecycle['recovery_id']}.json"
    )
    assert record.is_file()


def test_packaged_restore_terminates_a_child_at_the_capture_bound(
    tmp_path: Path,
) -> None:
    """Restore database probes must stop oversized children before buffering grows."""

    package = build_helper_package(tmp_path / "taskman-host.pyz").path
    credentials = tmp_path / "pgpass"
    credentials.write_text("secret", encoding="utf-8")
    credentials.chmod(0o600)
    command = tmp_path / "oversized"
    pid_path = tmp_path / "oversized.pid"
    command.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$$\" > {pid_path}\n"
        "head -c 5000 /dev/zero\n"
        "while :; do :; done\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    harness = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from taskman_ops.host_helper.lifecycle import LifecycleError
from taskman_ops.host_helper.operations.restore import _run
try:
    _run((sys.argv[2],), Path(sys.argv[3]), capture=True)
except LifecycleError:
    print("bounded")
'''
    pid = -1
    try:
        completed = subprocess.run(
            [
                "python3",
                "-I",
                "-c",
                harness,
                str(package),
                str(command),
                str(credentials),
            ],
            capture_output=True,
            check=False,
            timeout=2,
        )
    finally:
        if pid_path.exists():
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    assert completed.returncode == 0
    assert completed.stdout == b"bounded\n"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


INTENDED = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


def _managed_restore(
    tmp_path: Path,
    *,
    dump_sha256: str | None = None,
) -> tuple[Path, HostRequest]:
    paths = ManagedPaths.from_mapping({
        "install_root": str(tmp_path / "install"),
        "backup_root": str(tmp_path / "backups"),
    })
    store = LifecycleStore(paths)
    when = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    later = datetime(2026, 9, 5, 12, 1, tzinfo=UTC)
    for identifier, previous, activated, policy in (
        (INTENDED, None, when, "no-change"),
        (CURRENT, INTENDED, later, "backward-compatible"),
    ):
        directory = store.release_root / identifier
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
        store.write_release(
            ReleaseRecord(
                1, identifier, "a" * 64, activated, activated, previous, None,
                policy,
            )
        )
    store.write_activation(
        ActivationRecord(
            1, "activation-" + "a" * 32, None, INTENDED, when, None,
            "no-change",
        )
    )
    store.write_activation(
        ActivationRecord(
            1, "activation-" + "b" * 32, INTENDED, CURRENT, later, None,
            "backward-compatible",
        )
    )
    store.current_link.parent.mkdir(parents=True, exist_ok=True)
    store.current_link.symlink_to(store.release_root / CURRENT)
    store.backup_root.mkdir()
    source_id = "backup-" + "c" * 32
    dump = store.backup_root / f"{source_id}.dump"
    dump.write_bytes(b"validated")
    dump.chmod(0o600)
    store.write_backup(
        BackupRecord(
            1, source_id, when, len(b"validated"), 1024, "taskman_prod",
            INTENDED, CURRENT, "pre-deploy", True, paths.backup_root / dump.name,
            dump_sha256,
        )
    )
    secret = tmp_path / "pgpass"
    secret.write_text("secret-canary", encoding="utf-8")
    secret.chmod(0o600)
    request = HostRequest(
        1, "restore", "op-" + "d" * 32,
        {"backup_id": source_id, "current_release_id": CURRENT},
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        },
        {
            "action": "execute", "backup_id": source_id,
            "credentials_path": secret.as_posix(),
            "database": {
                "host": "127.0.0.1", "name": "taskman_prod",
                "port": 5432, "role": "taskman",
            },
            "expected_migration_versions": ("20260905120000",),
            "verification": {
                "application_port": 4000, "distribution_port": 6789,
                "database_port": 5432, "public_hostname": "taskman.acme.tld",
                "public_ipv4": "203.0.113.10", "public_ipv6": None,
                "ssh_port": 22, "ssh_user": "deployer",
                "readiness_timeout": 1, "connection_timeout": 1,
            },
        },
    )
    return build_helper_package(tmp_path / "taskman-host.pyz").path, request


def _run_packaged(
    package: Path,
    request: HostRequest,
    failure: str,
    *,
    fsync_log: Path | None = None,
    authority_damage: str = "",
) -> HostResult:
    harness = r'''
import io, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.backup as backup
import taskman_ops.host_helper.operations.restore as operation
from taskman_ops.host_helper.lifecycle import LifecycleError, LifecycleStore
from taskman_ops.host_helper.operations.backup import BackupOperationFailure
from taskman_ops.host_protocol import HostResult
failure = sys.argv[2]
fsync_log = Path(sys.argv[3]) if sys.argv[3] else None
authority_damage = sys.argv[4]
if authority_damage == "wrong-owner":
    original_lstat = Path.lstat
    target_release = Path(sys.argv[5])
    def wrong_owner(path):
        details = original_lstat(path)
        if path == target_release:
            return SimpleNamespace(
                st_mode=details.st_mode,
                st_uid=details.st_uid + 1,
            )
        return details
    Path.lstat = wrong_owner
if authority_damage == "locked-redirected":
    original_require = LifecycleStore.require_release_directory
    require_calls = 0
    def redirect_before_locked_check(self, path):
        global require_calls
        require_calls += 1
        if require_calls == 2:
            path.rmdir()
            replacement = next(
                item for item in path.parent.iterdir()
                if item.name != path.name and item.is_dir()
            )
            path.symlink_to(replacement)
        return original_require(self, path)
    LifecycleStore.require_release_directory = redirect_before_locked_check
if failure == "backup-nested":
    operation.create_validated_backup = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(
            BackupOperationFailure(
                "records",
                changed=True,
                changed_stages=("backup",),
                residue_paths=(Path("/safe/backup-residue"),),
            )
        )
    )
if failure == "backup-record":
    LifecycleStore.write_backup = lambda *_args: (
        (_ for _ in ()).throw(LifecycleError("injected backup record failure"))
    )
backup._database_size = lambda *_args: 1024 if failure == "backup-record" else 4096
def dump(_database, _credentials, destination):
    if failure == "backup": raise LifecycleError("injected")
    destination.write_bytes(b"pre-restore")
    destination.chmod(0o600)
backup._dump = dump
backup._validate_dump = lambda *_args: None
operation._validate_dump = lambda *_args: None
operation._validate_capacity = lambda *_args: None
def service(action):
    if failure == action: raise subprocess.CalledProcessError(1, action)
operation._service = service
def admin(_inputs, sql):
    if failure == "restore" and sql.startswith("CREATE DATABASE"):
        raise subprocess.CalledProcessError(1, "create")
    if failure == "swap" and "RENAME TO" in sql:
        raise subprocess.CalledProcessError(1, "swap")
    if sql.startswith("SELECT datname"):
        return "taskman_prod\ntaskman_recovery_" + "d" * 32 + "\n"
    return ""
operation._admin = admin
operation._restore_dump = lambda *_args: None
def validate(*_args):
    if failure in {"validation", "validation-cleanup"}:
        raise LifecycleError("injected")
operation._validate_restored_database = validate
if failure == "validation-cleanup":
    original_admin = operation._admin
    def fail_cleanup(inputs, sql):
        if sql.startswith("DROP DATABASE"):
            raise subprocess.CalledProcessError(1, "cleanup")
        return original_admin(inputs, sql)
    operation._admin = fail_cleanup
original_select = operation._select_release
def select(*args):
    if failure == "selection": raise LifecycleError("injected")
    original_select(*args)
operation._select_release = select
if failure == "selection-fsync":
    operation._fsync_directory = lambda *_args: (
        (_ for _ in ()).throw(OSError("injected selection fsync failure"))
    )
def verified(request, **_kwargs):
    if failure == "verification":
        release = request.expected_state["expected_release_id"]
        return HostResult(1, request.operation, request.operation_id, "failed",
            "verification", (), {}, {}, {
                "schema_version": 1, "status": "failed", "exit_status": 8,
                "release_id": release, "expected_release_id": release,
                "checks": ({
                    "schema_version": 1, "name": "taskman-service",
                    "status": "failed",
                    "summary": "taskman.service is not active with a usable MainPID",
                },),
                "next_action": "inspect",
            }, (), (), ())
    release = request.expected_state["expected_release_id"]
    return HostResult(1, request.operation, request.operation_id, "succeeded",
        "verified", (), {}, {}, {
            "schema_version": 1, "status": "ok", "exit_status": 0,
            "release_id": release, "expected_release_id": release,
            "checks": (), "next_action": None,
        }, (), (), ())
operation.verify = verified
if failure == "records":
    operation._write_restore_record = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(OSError("injected"))
    )
if failure == "restore-record-link":
    original_link = operation.os.link
    def fail_restore_record_link(source, destination, **kwargs):
        if (
            Path(destination).parent.name == "restores"
            and Path(destination).name.startswith("recovery-")
        ):
            raise OSError("injected restore record link failure")
        return original_link(source, destination, **kwargs)
    operation.os.link = fail_restore_record_link
if failure == "restore-record-fsync":
    original_fsync = operation._fsync_directory
    def fail_published_record_fsync(path):
        final = path / ("recovery-" + "d" * 32 + ".json")
        if path.name == "restores" and final.exists():
            raise OSError("injected restore record fsync failure")
        return original_fsync(path)
    operation._fsync_directory = fail_published_record_fsync
if fsync_log is not None or failure == "restore-directory-parent-fsync":
    original_fsync = operation._fsync_directory
    def trace_restore_fsync(path):
        if fsync_log is not None:
            with fsync_log.open("a", encoding="utf-8") as stream:
                stream.write(path.as_posix() + "\n")
        if (
            failure == "restore-directory-parent-fsync"
            and path.name == "deployments"
        ):
            raise OSError("injected restore directory parent fsync failure")
        return original_fsync(path)
    operation._fsync_directory = trace_restore_fsync
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
            failure,
            "" if fsync_log is None else str(fsync_log),
            authority_damage,
            (
                Path(request.paths["install_root"])
                / "releases"
                / INTENDED
            ).as_posix(),
        ],
        input=encode_request(request), capture_output=True, check=False,
        env=os.environ,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)
