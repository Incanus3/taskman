"""Packaged rollback-operation contracts."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
from datetime import UTC, datetime
import os
import subprocess

import pytest

from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, HostResult, decode_result, encode_request
from taskman_ops.host_helper.lifecycle import ActivationRecord, LifecycleStore, ReleaseRecord
from taskman_ops.host_helper.paths import ManagedPaths


def test_packaged_rollback_refuses_an_incomplete_request_without_unavailable_dispatch(tmp_path: Path) -> None:
    """A rollback may not fall through to the generic unavailable response."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="rollback",
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
    assert result.stage == "rollback-preflight"
    assert result.warnings == ()


def test_packaged_rollback_reports_lock_held_target_ineligibility(tmp_path: Path) -> None:
    """A controller-side eligibility guess could roll back across unknown history."""

    target = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="rollback",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"current_release_id": None},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={"target_release_id": target},
    )

    completed = subprocess.run(
        ["python3", "-I", str(package.path)], input=encode_request(request), capture_output=True, check=False
    )

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "refused"
    assert result.stage == "rollback-preflight"
    assert result.lifecycle == {"rollback_eligible": False, "target_release_id": target}


def test_packaged_rollback_creates_backup_and_appends_activation_for_a_compatible_edge(tmp_path: Path) -> None:
    """Selecting an old release without a fresh validated backup is unsafe."""

    first = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    current = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
    paths = ManagedPaths.from_mapping({"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")})
    store = LifecycleStore(paths)
    for release in (first, current):
        directory = store.release_root / release
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    first_time = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    current_time = datetime(2026, 9, 5, 12, 1, tzinfo=UTC)
    store.write_release(ReleaseRecord(1, first, "a" * 64, first_time, first_time, None, None, "no-change"))
    store.write_release(ReleaseRecord(1, current, "b" * 64, current_time, current_time, first, None, "no-change"))
    store.write_activation(ActivationRecord(1, "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, first, first_time, None, "no-change"))
    store.write_activation(ActivationRecord(1, "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", first, current, current_time, None, "no-change"))
    store.current_link.parent.mkdir(parents=True, exist_ok=True)
    store.current_link.symlink_to(store.release_root / current)
    credentials = tmp_path / "pgpass"
    credentials.write_text("not-in-result", encoding="utf-8")
    credentials.chmod(0o600)
    commands = tmp_path / "commands"
    commands.mkdir()
    _command(commands / "psql", "#!/bin/sh\nprintf '1024\\n'\n")
    _command(commands / "pg_dump", "#!/bin/sh\nfor arg in \"$@\"; do case \"$arg\" in --file=*) printf dump > \"${arg#--file=}\";; esac; done\n")
    _command(commands / "pg_restore", "#!/bin/sh\nexit 0\n")
    _command(commands / "systemctl", "#!/bin/sh\nexit 0\n")
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1, operation="rollback", operation_id="op-cccccccccccccccccccccccccccccccc",
        expected_state={"current_release_id": current},
        paths={"install_root": paths.install_root.as_posix(), "backup_root": paths.backup_root.as_posix()},
        parameters={
            "target_release_id": first,
            "credentials_path": credentials.as_posix(),
            "database": {"host": "127.0.0.1", "name": "taskman_prod", "port": 5432, "role": "taskman"},
            "verification": _verification(),
        },
    )
    result = _run_packaged(package.path, request, commands)
    assert result.outcome == "succeeded"
    assert result.stage == "records"
    assert result.changed_stages == ("backup", "stop", "selection", "start", "verification", "records")
    assert result.lifecycle["selected_release_id"] == first
    assert result.lifecycle["backup_id"] == "backup-cccccccccccccccccccccccccccccccc"


def test_packaged_rollback_exact_rerun_is_a_noop(tmp_path: Path) -> None:
    package, request, commands = _managed_rollback(tmp_path)

    first = _run_packaged(package, request, commands)
    second = _run_packaged(package, request, commands)

    assert first.outcome == "succeeded"
    assert second.outcome == "no_change"
    assert second.stage == "already-current"
    assert second.changed_stages == ()
    assert second.lifecycle["activation_recorded"] is True
    assert second.verification["release_id"] == FIRST


@pytest.mark.parametrize(
    ("failure", "stage"),
    [
        ("backup", "backup"),
        ("stop", "stop"),
        ("selection", "selection"),
        ("selection-cleanup", "selection"),
        ("start", "start"),
        ("verification", "verification"),
        ("records", "records"),
    ],
)
def test_packaged_rollback_reports_every_mutation_failure(
    tmp_path: Path,
    failure: str,
    stage: str,
) -> None:
    package, request, commands = _managed_rollback(tmp_path)

    result = _run_packaged(package, request, commands, failure=failure)

    assert result.outcome == "failed"
    assert result.stage == stage
    assert result.lifecycle["target_release_id"] == FIRST
    assert len(result.recovery_actions) <= 3
    assert "secret-canary" not in repr(result)
    if failure == "selection":
        assert "selection" in result.changed_stages
        assert result.lifecycle["selected_release_id"] == FIRST


def test_packaged_rollback_preserves_nested_backup_failure_evidence(
    tmp_path: Path,
) -> None:
    """The rollback transaction must not flatten a published backup failure."""

    package, request, commands = _managed_rollback(tmp_path)

    result = _run_packaged(
        package,
        request,
        commands,
        failure="backup-nested",
    )

    assert result.outcome == "failed"
    assert result.stage == "records"
    assert result.changed_stages == ("backup",)
    assert result.residue_paths == ("/safe/backup-residue",)


def test_packaged_rollback_resumes_a_published_pre_rollback_dump(
    tmp_path: Path,
) -> None:
    """The same rollback ID may finalize its exact dump before selecting."""

    package, request, commands = _managed_rollback(tmp_path)

    failed = _run_packaged(
        package,
        request,
        commands,
        failure="backup-record",
    )
    _command(commands / "psql", "#!/bin/sh\nprintf '4096\\n'\n")
    retried = _run_packaged(package, request, commands)
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
    assert retried.lifecycle["backup_id"] == "backup-" + "d" * 32
    assert retried.lifecycle["selected_release_id"] == FIRST
    assert record["source_database_size_bytes"] == 1024


def test_packaged_rollback_refuses_stale_and_incompatible_authority(
    tmp_path: Path,
) -> None:
    package, request, commands = _managed_rollback(
        tmp_path, policy="restore-required"
    )

    incompatible = _run_packaged(package, request, commands)
    stale = _run_packaged(
        package,
        HostRequest(
            1, "rollback", "op-" + "e" * 32,
            {"current_release_id": FIRST}, request.paths, request.parameters,
        ),
        commands,
    )

    assert incompatible.outcome == "refused"
    assert incompatible.stage == "rollback-preflight"
    assert stale.outcome == "refused"
    assert stale.changed_stages == ()


def test_packaged_rollback_reports_lifecycle_lock_contention(
    tmp_path: Path,
) -> None:
    package, request, commands = _managed_rollback(tmp_path)
    lock_root = tmp_path / ".taskman-lock"
    lock_root.mkdir(mode=0o750)
    lock = (lock_root / "lifecycle.lock").open("a+")
    (lock_root / "lifecycle.lock").chmod(0o600)
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        result = _run_packaged(package, request, commands)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

    assert result.outcome == "refused"
    assert result.stage == "lifecycle-lock"


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
def test_packaged_rollback_refuses_unsafe_target_release_before_mutation(
    tmp_path: Path,
    damage: str,
) -> None:
    """Rollback may not create a backup or stop service for an unsafe target."""

    package, request, commands = _managed_rollback(tmp_path)
    target = Path(request.paths["install_root"]) / "releases" / FIRST
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
        commands,
        authority_damage=damage,
    )

    backup_id = f"backup-{request.operation_id.removeprefix('op-')}"
    assert result.outcome == "refused"
    assert result.stage == "rollback-preflight"
    assert result.changed_stages == ()
    assert result.recovery_actions
    assert not (
        Path(request.paths["backup_root"]) / f"{backup_id}.dump"
    ).exists()
    assert (
        Path(request.paths["install_root"]) / "current"
    ).readlink() == Path(request.paths["install_root"]) / "releases" / CURRENT


FIRST = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


def _managed_rollback(
    tmp_path: Path,
    *,
    policy: str = "backward-compatible",
) -> tuple[Path, HostRequest, Path]:
    paths = ManagedPaths.from_mapping({
        "install_root": str(tmp_path / "install"),
        "backup_root": str(tmp_path / "backups"),
    })
    store = LifecycleStore(paths)
    first_time = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    current_time = datetime(2026, 9, 5, 12, 1, tzinfo=UTC)
    for identifier in (FIRST, CURRENT):
        directory = store.release_root / identifier
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    store.write_release(
        ReleaseRecord(
            1, FIRST, "a" * 64, first_time, first_time, None, None,
            "no-change",
        )
    )
    store.write_release(
        ReleaseRecord(
            1, CURRENT, "b" * 64, current_time, current_time, FIRST, None,
            policy,
        )
    )
    store.write_activation(
        ActivationRecord(
            1, "activation-" + "a" * 32, None, FIRST, first_time, None,
            "no-change",
        )
    )
    store.write_activation(
        ActivationRecord(
            1, "activation-" + "b" * 32, FIRST, CURRENT, current_time, None,
            policy,
        )
    )
    store.current_link.parent.mkdir(parents=True, exist_ok=True)
    store.current_link.symlink_to(store.release_root / CURRENT)
    credentials = tmp_path / "pgpass"
    credentials.write_text("secret-canary", encoding="utf-8")
    credentials.chmod(0o600)
    commands = tmp_path / "commands"
    commands.mkdir()
    _command(commands / "psql", "#!/bin/sh\nprintf '1024\\n'\n")
    _command(
        commands / "pg_dump",
        "#!/bin/sh\nfor arg in \"$@\"; do case \"$arg\" in --file=*) printf dump > \"${arg#--file=}\";; esac; done\n",
    )
    _command(commands / "pg_restore", "#!/bin/sh\nexit 0\n")
    _command(commands / "systemctl", "#!/bin/sh\nexit 0\n")
    request = HostRequest(
        1, "rollback", "op-" + "d" * 32,
        {"current_release_id": CURRENT},
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        },
        {
            "target_release_id": FIRST,
            "credentials_path": credentials.as_posix(),
            "database": {
                "host": "127.0.0.1", "name": "taskman_prod",
                "port": 5432, "role": "taskman",
            },
            "verification": _verification(),
        },
    )
    return build_helper_package(
        tmp_path / "taskman-host.pyz"
    ).path, request, commands


def _command(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _verification() -> dict[str, object]:
    return {
        "application_port": 4000, "distribution_port": 6789,
        "database_port": 5432, "public_hostname": "taskman.acme.tld",
        "public_ipv4": "203.0.113.10", "public_ipv6": None,
        "ssh_port": 22, "ssh_user": "deployer",
        "readiness_timeout": 1, "connection_timeout": 1,
    }


def _run_packaged(
    package: Path,
    request: HostRequest,
    commands: Path,
    *,
    failure: str = "",
    authority_damage: str = "",
) -> HostResult:
    harness = r'''
import io, os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.backup as backup
import taskman_ops.host_helper.operations.rollback as operation
from taskman_ops.host_helper.lifecycle import LifecycleError, LifecycleStore
from taskman_ops.host_helper.operations.backup import BackupOperationFailure
from taskman_ops.host_protocol import HostResult
failure = sys.argv[3]
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
if failure == "selection-cleanup":
    original_backup = operation.create_validated_backup
    def backup_then_fail_selection(*args, **kwargs):
        result = original_backup(*args, **kwargs)
        operation.os.replace = lambda *_args: (
            (_ for _ in ()).throw(OSError("injected selection failure"))
        )
        return result
    operation.create_validated_backup = backup_then_fail_selection
    original_unlink = operation.Path.unlink
    def retain_selection(path, *args, **kwargs):
        if path.name.startswith(".current-"):
            raise OSError("injected selection cleanup failure")
        return original_unlink(path, *args, **kwargs)
    operation.Path.unlink = retain_selection
original_dump = backup._dump
def dump(*args):
    if failure == "backup": raise LifecycleError("injected")
    original_dump(*args)
backup._dump = dump
original_service = operation._service
def service(action):
    if failure == action: raise OSError("injected")
    original_service(action)
operation._service = service
if failure == "selection":
    operation._fsync_directory = lambda *_args: (
        (_ for _ in ()).throw(OSError("injected"))
    )
if failure == "records":
    LifecycleStore.write_activation = lambda *_args: (
        (_ for _ in ()).throw(LifecycleError("injected"))
    )
def verified(request, **_kwargs):
    outcome = "failed" if failure == "verification" else "succeeded"
    stage = "verification" if outcome == "failed" else "verified"
    return HostResult(1, request.operation, request.operation_id, outcome, stage, (), {}, {}, {
        "schema_version": 1, "status": "failed" if outcome == "failed" else "ok",
        "release_id": request.expected_state["expected_release_id"],
        "expected_release_id": request.expected_state["expected_release_id"],
        "checks": (), "exit_status": 9 if outcome == "failed" else 0,
        "next_action": "inspect" if outcome == "failed" else None,
    }, (), (), ())
operation.verify = verified
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
            str(commands),
            failure,
            authority_damage,
            (
                Path(request.paths["install_root"])
                / "releases"
                / str(request.parameters["target_release_id"])
            ).as_posix(),
        ],
        input=encode_request(request),
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)
