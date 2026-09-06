"""Packaged backup-operation contracts."""

from __future__ import annotations

import fcntl
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import os
import signal
import subprocess

import pytest

from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_helper.lifecycle import BackupRecord, LifecycleStore
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request


def test_packaged_backup_refuses_an_incomplete_request_without_unavailable_dispatch(tmp_path: Path) -> None:
    """Removing backup dispatch or accepting a partial backup request is unsafe."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="backup",
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
    assert result.stage == "backup-preflight"
    assert result.warnings == ()


def test_packaged_backup_validates_a_custom_dump_before_publishing_its_record(tmp_path: Path) -> None:
    """Publishing before `pg_restore --list` would create unusable recovery authority."""

    commands = tmp_path / "commands"
    commands.mkdir()
    _command(
        commands / "psql",
        "#!/bin/sh\nprintf '1024\\n'\n",
    )
    _command(
        commands / "pg_dump",
        "#!/bin/sh\nfor arg in \"$@\"; do case \"$arg\" in --file=*) printf dump > \"${arg#--file=}\";; esac; done\n",
    )
    _command(commands / "pg_restore", "#!/bin/sh\nexit 0\n")
    credentials = tmp_path / "postgres.pgpass"
    secret = "taskman:5432:taskman_prod:taskman:do-not-return-me"
    credentials.write_text(secret, encoding="utf-8")
    credentials.chmod(0o600)
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="backup",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"backup_ids": []},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={
            "credentials_path": credentials.as_posix(),
            "database": {"host": "127.0.0.1", "name": "taskman_prod", "port": 5432, "role": "taskman"},
            "reason": "scheduled",
            "retention": 2,
        },
    )
    environment = {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"}

    completed = subprocess.run(
        ["python3", "-I", str(package.path)], input=encode_request(request), capture_output=True, check=False, env=environment
    )

    result = decode_result(completed.stdout)
    dump = tmp_path / "backups" / "backup-0123456789abcdef0123456789abcdef.dump"
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "succeeded"
    assert result.stage == "records"
    assert result.changed_stages == ("backup", "records")
    assert result.lifecycle["backup_id"] == "backup-0123456789abcdef0123456789abcdef"
    assert dump.read_text(encoding="utf-8") == "dump"
    assert secret.encode("utf-8") not in completed.stdout


def test_packaged_backup_fsyncs_validated_temp_before_durable_pending_and_rename(
    tmp_path: Path,
) -> None:
    """A power loss may not make pending authority outlive its dump contents."""

    commands, request, package = _valid_backup(tmp_path)
    trace = tmp_path / "fsync-trace"

    result = _invoke_with_fsync_trace(package, request, commands, trace)

    backup_root = tmp_path / "backups"
    temporary = backup_root / f".backup-{'a' * 32}.tmp"
    pending = backup_root / f".backup-{'a' * 32}.pending.json"
    events = trace.read_text(encoding="utf-8").splitlines()
    temporary_index = events.index(temporary.as_posix())
    pending_index = events.index(pending.as_posix())
    root_indices = [
        index for index, event in enumerate(events)
        if event == backup_root.as_posix()
    ]

    assert result.outcome == "succeeded"
    assert temporary_index < pending_index < root_indices[0] < root_indices[1]


def test_packaged_backup_discards_pending_authority_when_temp_file_fsync_fails(
    tmp_path: Path,
) -> None:
    """A failed temp-file fsync must not publish recoverable-looking authority."""

    commands, request, package = _valid_backup(tmp_path)

    result = _invoke_with_temp_file_fsync_failure(package, request, commands)

    backup_root = tmp_path / "backups"
    assert result.outcome == "failed"
    assert result.stage == "backup"
    assert result.changed_stages == ()
    assert result.residue_paths == ()
    assert not (backup_root / f"backup-{'a' * 32}.dump").exists()
    assert not (backup_root / f".backup-{'a' * 32}.pending.json").exists()
    assert not (backup_root / f".backup-{'a' * 32}.tmp").exists()


def test_packaged_backup_retains_validated_dump_when_record_publication_fails(tmp_path: Path) -> None:
    """A published dump without its record must remain explicit recovery residue."""

    commands = tmp_path / "commands"
    commands.mkdir()
    _command(commands / "psql", "#!/bin/sh\nprintf '1024\\n'\n")
    _command(commands / "pg_dump", "#!/bin/sh\nfor arg in \"$@\"; do case \"$arg\" in --file=*) printf dump > \"${arg#--file=}\";; esac; done\n")
    _command(commands / "pg_restore", "#!/bin/sh\nexit 0\n")
    credentials = tmp_path / "postgres.pgpass"
    credentials.write_text("credential-not-in-json", encoding="utf-8")
    credentials.chmod(0o600)
    install = tmp_path / "install"
    install.mkdir()
    install.chmod(0o500)
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="backup",
        operation_id="op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        expected_state={"backup_ids": []},
        paths={"install_root": install.as_posix(), "backup_root": str(tmp_path / "backups")},
        parameters={"credentials_path": credentials.as_posix(), "database": {"host": "127.0.0.1", "name": "taskman_prod", "port": 5432, "role": "taskman"}, "reason": "scheduled", "retention": 2},
    )
    environment = {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"}
    try:
        completed = subprocess.run(["python3", "-I", str(package.path)], input=encode_request(request), capture_output=True, check=False, env=environment)
    finally:
        install.chmod(0o750)

    result = decode_result(completed.stdout)
    dump = tmp_path / "backups" / "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.dump"
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "failed"
    assert result.stage == "records"
    assert result.changed_stages == ("backup",)
    assert result.residue_paths == (
        dump.as_posix(),
        (tmp_path / "backups" / f".backup-{'a' * 32}.pending.json").as_posix(),
    )
    assert result.warnings == ("unable to publish validated backup",)


def test_packaged_backup_finalizes_an_exact_published_dump_on_same_id_retry(
    tmp_path: Path,
) -> None:
    """Retry must publish the immutable origin metadata, not a new observation."""

    commands, request, package = _valid_backup(tmp_path)

    failed = _invoke_record_failure(package, request, commands)
    pending = (
        tmp_path
        / "backups"
        / f".backup-{'a' * 32}.pending.json"
    )
    pending_value = json.loads(pending.read_text(encoding="utf-8"))
    _command(commands / "psql", "#!/bin/sh\nprintf '4096\\n'\n")
    retried = _invoke(package, request, commands)
    record = json.loads(
        (
            tmp_path
            / "install"
            / "deployments"
            / "backups"
            / f"backup-{'a' * 32}.json"
        ).read_text(encoding="utf-8")
    )

    assert failed.outcome == "failed"
    assert failed.stage == "records"
    assert failed.changed_stages == ("backup",)
    assert pending_value["record"]["created_at"] == "2026-09-01T01:02:03Z"
    assert pending_value["record"]["source_database_size_bytes"] == 1024
    assert retried.outcome == "succeeded"
    assert retried.changed_stages == ("backup", "records")
    assert retried.lifecycle["backup_id"] == "backup-" + "a" * 32
    assert record["created_at"] == pending_value["record"]["created_at"]
    assert record["source_database_size_bytes"] == 1024
    assert not pending.exists()
    assert retried.verification == {"format": "custom", "validated": True}


def test_packaged_backup_finishes_pending_before_rename_after_process_crash(
    tmp_path: Path,
) -> None:
    """An operation-bound pending temp is rerunnable after an abrupt process exit."""

    commands, request, package = _valid_backup(tmp_path)

    crashed = _invoke_crash_before_dump_rename(package, request, commands)
    backup_root = tmp_path / "backups"
    temporary = backup_root / f".backup-{'a' * 32}.tmp"
    pending = backup_root / f".backup-{'a' * 32}.pending.json"

    assert crashed.returncode == 71
    assert crashed.stdout == b""
    assert crashed.stderr == b""
    assert temporary.is_file()
    assert pending.is_file()

    retried = _invoke(package, request, commands)

    assert retried.outcome == "succeeded"
    assert retried.changed_stages == ("backup", "records")
    assert not temporary.exists()
    assert not pending.exists()
    assert (backup_root / f"backup-{'a' * 32}.dump").is_file()


def test_packaged_backup_reports_validated_foreign_pending_recovery_authority(
    tmp_path: Path,
) -> None:
    """A new operation must identify only the safe, owning recovery state."""

    commands, request, package = _valid_backup(tmp_path)
    crashed = _invoke_crash_before_dump_rename(package, request, commands)
    fresh = HostRequest(
        request.protocol_version,
        request.operation,
        "op-" + "b" * 32,
        request.expected_state,
        request.paths,
        request.parameters,
    )
    backup_root = tmp_path / "backups"
    pending = backup_root / f".backup-{'a' * 32}.pending.json"
    temporary = backup_root / f".backup-{'a' * 32}.tmp"

    result = _invoke(package, fresh, commands)

    assert crashed.returncode == 71
    assert result.outcome == "refused"
    assert result.stage == "backup-preflight"
    assert result.changed_stages == ()
    assert result.residue_paths == tuple(
        sorted((pending.as_posix(), temporary.as_posix()))
    )
    assert result.recovery_actions == (
        "retry backup operation op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa before starting another backup",
    )
    assert result.warnings == ()
    assert "secret-canary" not in repr(result)


@pytest.mark.parametrize("corruption", ("mode", "digest", "custom-format"))
def test_packaged_backup_refuses_unsafe_pre_rename_recovery_residue(
    tmp_path: Path,
    corruption: str,
) -> None:
    """A retried pending temp must be revalidated before it can be renamed."""

    commands, request, package = _valid_backup(tmp_path)
    crashed = _invoke_crash_before_dump_rename(package, request, commands)
    backup_root = tmp_path / "backups"
    temporary = backup_root / f".backup-{'a' * 32}.tmp"
    pending = backup_root / f".backup-{'a' * 32}.pending.json"
    if corruption == "mode":
        temporary.chmod(0o644)
    elif corruption == "digest":
        temporary.write_bytes(b"evil")
    else:
        _command(commands / "pg_restore", "#!/bin/sh\nexit 1\n")

    result = _invoke(package, request, commands)

    assert crashed.returncode == 71
    assert result.outcome == "failed"
    assert result.stage == "backup"
    assert result.changed_stages == ()
    assert result.residue_paths == (pending.as_posix(), temporary.as_posix())
    assert pending.is_file()
    assert temporary.is_file()


def test_packaged_backup_refuses_ambiguous_temp_after_dump_publication(
    tmp_path: Path,
) -> None:
    """A published dump and an operation temp cannot describe one recovery state."""

    commands, request, package = _valid_backup(tmp_path)
    failed = _invoke_record_failure(package, request, commands)
    backup_root = tmp_path / "backups"
    dump = backup_root / f"backup-{'a' * 32}.dump"
    pending = backup_root / f".backup-{'a' * 32}.pending.json"
    temporary = backup_root / f".backup-{'a' * 32}.tmp"
    temporary.write_bytes(b"ambiguous")
    temporary.chmod(0o600)

    result = _invoke(package, request, commands)

    assert failed.outcome == "failed"
    assert result.outcome == "failed"
    assert result.stage == "backup"
    assert result.changed_stages == ("backup",)
    assert result.residue_paths == (
        dump.as_posix(), pending.as_posix(), temporary.as_posix()
    )
    assert dump.is_file()
    assert pending.is_file()
    assert temporary.is_file()


def test_packaged_backup_refuses_correlated_record_with_noncanonical_dump_path(
    tmp_path: Path,
) -> None:
    """A correlated record may never redirect backup validation to another path."""

    commands, request, package = _valid_backup(tmp_path)
    paths = ManagedPaths.from_mapping(request.paths)
    store = LifecycleStore(paths)
    store.backup_root.mkdir()
    redirected = store.backup_root / "other.dump"
    redirected.write_bytes(b"validated")
    redirected.chmod(0o600)
    identifier = f"backup-{'a' * 32}"
    store.write_backup(
        BackupRecord(
            1,
            identifier,
            datetime(2026, 9, 1, 1, 2, 3, tzinfo=UTC),
            redirected.stat().st_size,
            1024,
            "taskman_prod",
            None,
            None,
            "scheduled",
            True,
            paths.backup_root / redirected.name,
        )
    )
    correlated = HostRequest(
        request.protocol_version,
        request.operation,
        request.operation_id,
        {"backup_ids": (identifier,)},
        request.paths,
        request.parameters,
    )

    result = _invoke(package, correlated, commands)

    assert result.outcome == "refused"
    assert result.stage == "backup-preflight"
    assert result.changed_stages == ()


@pytest.mark.parametrize(
    "corruption",
    ("metadata", "operation", "dump", "mode"),
)
def test_packaged_backup_refuses_corrupt_pending_authority(
    tmp_path: Path,
    corruption: str,
) -> None:
    """Changing either origin authority or same-size dump identity must refuse."""

    commands, request, package = _valid_backup(tmp_path)
    failed = _invoke_record_failure(package, request, commands)
    pending = (
        tmp_path
        / "backups"
        / f".backup-{'a' * 32}.pending.json"
    )
    dump = tmp_path / "backups" / f"backup-{'a' * 32}.dump"
    if corruption == "metadata":
        value = json.loads(pending.read_text(encoding="utf-8"))
        value["record"]["reason"] = "pre-deploy"
        pending.write_text(json.dumps(value), encoding="utf-8")
    elif corruption == "operation":
        value = json.loads(pending.read_text(encoding="utf-8"))
        value["operation_id"] = "op-" + "b" * 32
        pending.write_text(json.dumps(value), encoding="utf-8")
    elif corruption == "dump":
        dump.write_bytes(b"evil")
    else:
        dump.chmod(0o644)

    retried = _invoke(package, request, commands)

    assert failed.outcome == "failed"
    assert retried.outcome == "refused"
    assert retried.changed_stages == ()


def test_packaged_backup_fresh_id_does_not_adopt_another_pending_dump(
    tmp_path: Path,
) -> None:
    """Only the operation that created pending authority may resolve it."""

    commands, request, package = _valid_backup(tmp_path)
    failed = _invoke_record_failure(package, request, commands)
    fresh = HostRequest(
        request.protocol_version,
        request.operation,
        "op-" + "b" * 32,
        request.expected_state,
        request.paths,
        request.parameters,
    )

    result = _invoke(package, fresh, commands)

    assert failed.outcome == "failed"
    assert result.outcome == "refused"
    assert not (
        tmp_path / "backups" / f"backup-{'b' * 32}.dump"
    ).exists()


def test_packaged_backup_requires_durable_origin_authority_before_dump_publication(
    tmp_path: Path,
) -> None:
    """A pending-authority fsync failure must leave no published dump."""

    commands, request, package = _valid_backup(tmp_path)

    result = _invoke_pending_fsync_failure(package, request, commands)

    backup_root = tmp_path / "backups"
    assert result.outcome == "failed"
    assert result.stage == "backup"
    assert result.changed_stages == ()
    assert not (backup_root / f"backup-{'a' * 32}.dump").exists()
    assert not (backup_root / f".backup-{'a' * 32}.pending.json").exists()


def test_packaged_backup_exact_rerun_is_a_validated_noop(
    tmp_path: Path,
) -> None:
    commands, request, package = _valid_backup(tmp_path)

    first = _invoke(package, request, commands)
    second = _invoke(package, request, commands)

    assert first.outcome == "succeeded"
    assert second.outcome == "no_change"
    assert second.changed_stages == ()
    assert second.lifecycle["backup_id"] == first.lifecycle["backup_id"]
    assert second.verification == {"format": "custom", "validated": True}


@pytest.mark.parametrize("drift", ("mode", "digest"))
def test_packaged_backup_refuses_finalized_dump_drift_without_mutation(
    tmp_path: Path,
    drift: str,
) -> None:
    """No-change verification must not normalize or trust a changed dump."""

    commands, request, package = _valid_backup(tmp_path)
    first = _invoke(package, request, commands)
    dump = tmp_path / "backups" / f"backup-{'a' * 32}.dump"
    if drift == "mode":
        dump.chmod(0o644)
    else:
        dump.write_bytes(b"evil")

    second = _invoke(package, request, commands)

    assert first.outcome == "succeeded"
    assert second.outcome == "refused"
    assert second.stage == "backup-preflight"
    assert second.changed_stages == ()
    assert dump.stat().st_mode & 0o777 == (0o644 if drift == "mode" else 0o600)


def test_packaged_backup_refuses_stale_confirmation_without_mutation(
    tmp_path: Path,
) -> None:
    commands, request, package = _valid_backup(tmp_path)
    _invoke(package, request, commands)
    stale = HostRequest(
        request.protocol_version, request.operation, "op-" + "b" * 32,
        {"backup_ids": ()}, request.paths, request.parameters,
    )

    result = _invoke(package, stale, commands)

    assert result.outcome == "refused"
    assert result.stage == "backup-preflight"
    assert result.changed_stages == ()


@pytest.mark.parametrize("failed_command", ("psql", "pg_dump", "pg_restore"))
def test_packaged_backup_reports_command_failures_without_secret_leakage(
    tmp_path: Path,
    failed_command: str,
) -> None:
    commands, request, package = _valid_backup(tmp_path)
    _command(commands / failed_command, "#!/bin/sh\nexit 1\n")

    result = _invoke(package, request, commands)

    assert result.outcome == "failed"
    assert result.stage == "backup"
    assert result.changed_stages == ()
    assert "secret-canary" not in repr(result)
    assert len(result.recovery_actions) == 1


def test_packaged_backup_reports_lifecycle_lock_contention(
    tmp_path: Path,
) -> None:
    commands, request, package = _valid_backup(tmp_path)
    lock_root = tmp_path / ".taskman-lock"
    lock_root.mkdir(mode=0o750)
    lock = (lock_root / "lifecycle.lock").open("a+")
    (lock_root / "lifecycle.lock").chmod(0o600)
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        result = _invoke(package, request, commands)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

    assert result.outcome == "refused"
    assert result.stage == "lifecycle-lock"
    assert len(result.recovery_actions) == 1


def test_packaged_backup_reports_private_partial_cleanup_failure(
    tmp_path: Path,
) -> None:
    commands, request, package = _valid_backup(tmp_path)
    _command(
        commands / "pg_dump",
        "#!/bin/sh\nfor arg in \"$@\"; do case \"$arg\" in --file=*) printf partial > \"${arg#--file=}\";; esac; done\nexit 1\n",
    )
    harness = r'''
import io, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
original = Path.unlink
def unlink(path, *args, **kwargs):
    if path.name.endswith(".tmp"): raise OSError("injected cleanup failure")
    return original(path, *args, **kwargs)
Path.unlink = unlink
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package)],
        input=encode_request(request), capture_output=True, check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )
    result = decode_result(completed.stdout)

    assert result.outcome == "failed"
    assert result.stage == "backup"
    assert len(result.residue_paths) == 1
    assert result.residue_paths[0].endswith(".tmp")
    assert "secret-canary" not in completed.stdout.decode()


def test_packaged_backup_terminates_a_child_at_the_capture_bound(
    tmp_path: Path,
) -> None:
    """Captured database output must be bounded while the child is still running."""

    commands, request, package = _valid_backup(tmp_path)
    pid_path = tmp_path / "oversized.pid"
    _command(
        commands / "psql",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$$\" > {pid_path}\n"
        "head -c 5000 /dev/zero\n"
        "while :; do :; done\n",
    )

    try:
        result = _invoke(package, request, commands, timeout=2)
    finally:
        if pid_path.exists():
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    assert result.outcome == "failed"
    assert result.stage == "backup"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize(
    ("failure", "first_outcome", "rerun_outcome"),
    (
        ("dump-fsync", "failed", "failed"),
        ("record-unlink", "failed", "failed"),
        ("record-fsync", "failed", "no_change"),
    ),
)
def test_packaged_scheduled_pruning_finishes_every_partial_pair(
    tmp_path: Path,
    failure: str,
    first_outcome: str,
    rerun_outcome: str,
) -> None:
    """A partial scheduled deletion either finishes durably or stays fail-closed."""

    commands, request, package, old_dump, old_record = _pruning_backup(tmp_path)
    first = _invoke_prune_failure(package, request, commands, failure)
    rerun = _invoke(package, request, commands)

    assert first.outcome == first_outcome
    if failure in {"dump-fsync", "record-unlink", "record-fsync"}:
        assert "cleanup" in first.changed_stages
    if failure in {"dump-fsync", "record-unlink"}:
        assert first.residue_paths == (old_record.as_posix(),)
    if failure == "record-fsync":
        assert first.residue_paths == ()
    assert rerun.outcome == rerun_outcome
    if failure in {"dump-fsync", "record-unlink"}:
        assert not old_dump.exists()
        assert old_record.exists()
    else:
        assert not old_dump.exists()
        assert not old_record.exists()


def test_packaged_backup_refuses_unsafe_scheduled_dump_without_normalizing_mode(
    tmp_path: Path,
) -> None:
    """Only a newly created private temp may receive mode normalization."""

    commands, request, package, old_dump, old_record = _pruning_backup(tmp_path)
    old_dump.chmod(0o644)

    result = _invoke(package, request, commands)

    assert result.outcome == "failed"
    assert result.stage == "cleanup"
    assert old_dump.is_file()
    assert old_record.is_file()
    assert old_dump.stat().st_mode & 0o777 == 0o644


def test_packaged_scheduled_pruning_retains_a_dump_referenced_by_a_retained_record(
    tmp_path: Path,
) -> None:
    """A retained record may not be made stale by pruning another record's dump."""

    commands, request, package, old_dump, old_record = _pruning_backup(tmp_path)
    retained_record = old_record.with_name(f"backup-{'2' * 32}.json")
    retained_payload = json.loads(retained_record.read_text(encoding="utf-8"))
    retained_payload["dump_path"] = old_dump.as_posix()
    retained_record.write_text(json.dumps(retained_payload), encoding="utf-8")
    retained_record.chmod(0o600)

    result = _invoke(package, request, commands)

    assert result.outcome == "failed"
    assert result.stage == "cleanup"
    assert old_dump.is_file()
    assert old_record.is_file()
    assert retained_record.is_file()


def test_packaged_scheduled_pruning_retains_a_dump_referenced_by_a_predeploy_record(
    tmp_path: Path,
) -> None:
    """A non-scheduled lifecycle record must also block scheduled dump deletion."""

    commands, request, package, old_dump, old_record = _pruning_backup(tmp_path)
    paths = ManagedPaths.from_mapping(request.paths)
    store = LifecycleStore(paths)
    predeploy_id = "backup-" + "4" * 32
    store.write_backup(
        BackupRecord(
            1,
            predeploy_id,
            datetime(2026, 9, 5, 12, 2, tzinfo=UTC),
            len(b"validated"),
            1024,
            "taskman_prod",
            None,
            None,
            "pre-deploy",
            True,
            paths.backup_root / old_dump.name,
            hashlib.sha256(b"validated").hexdigest(),
        )
    )
    predeploy_record = old_record.with_name(f"{predeploy_id}.json")
    request = HostRequest(
        request.protocol_version,
        request.operation,
        request.operation_id,
        {"backup_ids": (*request.expected_state["backup_ids"], predeploy_id)},
        request.paths,
        request.parameters,
    )

    result = _invoke(package, request, commands)

    assert result.outcome == "failed"
    assert result.stage == "cleanup"
    assert old_dump.is_file()
    assert old_record.is_file()
    assert predeploy_record.is_file()


def test_packaged_scheduled_pruning_refuses_before_any_unlink_when_inventory_is_ambiguous(
    tmp_path: Path,
) -> None:
    """Ambiguity in a later candidate must not permit an earlier deletion."""

    commands, request, package, old_dump, old_record = _pruning_backup(tmp_path)
    paths = ManagedPaths.from_mapping(request.paths)
    store = LifecycleStore(paths)
    retained_id = "backup-" + "4" * 32
    retained_dump = store.backup_root / f"{retained_id}.dump"
    retained_dump.write_bytes(b"validated")
    retained_dump.chmod(0o600)
    store.write_backup(
        BackupRecord(
            1,
            retained_id,
            datetime(2026, 9, 5, 12, 2, tzinfo=UTC),
            len(b"validated"),
            1024,
            "taskman_prod",
            None,
            None,
            "scheduled",
            True,
            paths.backup_root / retained_dump.name,
            hashlib.sha256(b"validated").hexdigest(),
        )
    )
    retained_record = old_record.with_name(f"{retained_id}.json")
    retained_payload = json.loads(retained_record.read_text(encoding="utf-8"))
    retained_payload["dump_path"] = old_dump.as_posix()
    retained_record.write_text(json.dumps(retained_payload), encoding="utf-8")
    retained_record.chmod(0o600)
    request = HostRequest(
        request.protocol_version,
        request.operation,
        request.operation_id,
        {"backup_ids": (*request.expected_state["backup_ids"], retained_id)},
        request.paths,
        request.parameters,
    )
    intermediate_dump = old_dump.with_name("backup-" + "2" * 32 + ".dump")
    intermediate_record = old_record.with_name("backup-" + "2" * 32 + ".json")

    result = _invoke(package, request, commands)

    assert result.outcome == "failed", result
    assert result.stage == "cleanup"
    assert old_dump.is_file()
    assert old_record.is_file()
    assert intermediate_dump.is_file()
    assert intermediate_record.is_file()


def test_packaged_scheduled_pruning_prevalidates_every_candidate_before_unlink(
    tmp_path: Path,
) -> None:
    """A later invalid candidate must not follow an earlier successful deletion."""

    commands, request, package, old_dump, old_record = _pruning_backup(tmp_path)
    intermediate_dump = old_dump.with_name("backup-" + "2" * 32 + ".dump")
    intermediate_record = old_record.with_name("backup-" + "2" * 32 + ".json")
    payload = json.loads(old_record.read_text(encoding="utf-8"))
    payload["dump_sha256"] = "f" * 64
    old_record.write_text(json.dumps(payload), encoding="utf-8")
    old_record.chmod(0o600)
    request = HostRequest(
        request.protocol_version,
        request.operation,
        request.operation_id,
        request.expected_state,
        request.paths,
        {**request.parameters, "retention": 1},
    )

    result = _invoke(package, request, commands)

    assert result.outcome == "failed"
    assert result.stage == "cleanup"
    assert result.changed_stages == ("backup", "records")
    assert old_dump.is_file()
    assert old_record.is_file()
    assert intermediate_dump.is_file()
    assert intermediate_record.is_file()


@pytest.mark.parametrize(
    ("failure", "dump_survives"),
    (
        ("later-dump-unlink", True),
        ("later-record-unlink", False),
    ),
)
def test_packaged_scheduled_pruning_reports_cumulative_effects_after_later_unlink_failure(
    tmp_path: Path,
    failure: str,
    dump_survives: bool,
) -> None:
    """A later unlink failure must report every earlier completed deletion."""

    commands, request, package, old_dump, old_record = _pruning_backup(tmp_path)
    intermediate_dump = old_dump.with_name("backup-" + "2" * 32 + ".dump")
    intermediate_record = old_record.with_name("backup-" + "2" * 32 + ".json")
    request = HostRequest(
        request.protocol_version,
        request.operation,
        request.operation_id,
        request.expected_state,
        {**request.paths},
        {**request.parameters, "retention": 1},
    )

    result = _invoke_prune_failure(package, request, commands, failure)

    assert result.outcome == "failed"
    assert result.stage == "cleanup"
    assert result.changed_stages == ("backup", "records", "cleanup")
    assert result.lifecycle == {
        "pruned_backup_ids": ("backup-" + "2" * 32,),
    }
    assert result.residue_paths == tuple(
        path.as_posix()
        for path in ((old_dump, old_record) if dump_survives else (old_record,))
    )
    assert not intermediate_dump.exists()
    assert not intermediate_record.exists()
    assert old_dump.exists() is dump_survives
    assert old_record.is_file()


@pytest.mark.parametrize(
    "mutation",
    ("legacy", "missing-dump", "wrong-size", "wrong-digest", "noncanonical-path", "invalid-format"),
)
def test_packaged_scheduled_pruning_retains_unproved_backup_authority(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Removing any published-dump proof must retain both deletion targets."""

    commands, request, package, old_dump, old_record = _pruning_backup(tmp_path)
    payload = json.loads(old_record.read_text(encoding="utf-8"))

    if mutation == "legacy":
        payload.pop("dump_sha256")
    elif mutation == "missing-dump":
        old_dump.unlink()
    elif mutation == "wrong-size":
        payload["size_bytes"] += 1
    elif mutation == "wrong-digest":
        payload["dump_sha256"] = "f" * 64
    elif mutation == "noncanonical-path":
        payload["dump_path"] = str(old_dump.with_name("backup-" + "f" * 32 + ".dump"))
    else:
        old_dump.write_bytes(b"corrupt")
        (commands / "pg_dump").write_text(
            "#!/bin/sh\nfor arg in \"$@\"; do case \"$arg\" in --file=*) printf validated > \"${arg#--file=}\";; esac; done\n",
            encoding="utf-8",
        )
        (commands / "pg_dump").chmod(0o755)
        (commands / "pg_restore").write_text(
            "#!/bin/sh\ngrep -qx validated \"$2\"\n", encoding="utf-8"
        )
        (commands / "pg_restore").chmod(0o755)

    old_record.write_text(json.dumps(payload), encoding="utf-8")
    old_record.chmod(0o600)

    result = _invoke(package, request, commands)

    assert result.outcome == "failed"
    assert result.stage == "cleanup"
    if mutation != "missing-dump":
        assert old_dump.is_file()
    assert old_record.is_file()


def _pruning_backup(
    tmp_path: Path,
) -> tuple[Path, HostRequest, Path, Path, Path]:
    commands, base, package = _valid_backup(tmp_path)
    paths = ManagedPaths.from_mapping(base.paths)
    store = LifecycleStore(paths)
    store.backup_root.mkdir(exist_ok=True)
    created = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    identifiers = ("backup-" + "1" * 32, "backup-" + "2" * 32)
    for index, identifier in enumerate(identifiers):
        dump = store.backup_root / f"{identifier}.dump"
        dump.write_bytes(b"validated")
        dump.chmod(0o600)
        store.write_backup(
            BackupRecord(
                1,
                identifier,
                created + timedelta(minutes=index),
                len(b"validated"),
                1024,
                "taskman_prod",
                None,
                None,
                "scheduled",
                True,
                paths.backup_root / dump.name,
                hashlib.sha256(b"validated").hexdigest(),
            )
        )
    request = HostRequest(
        base.protocol_version,
        base.operation,
        "op-" + "3" * 32,
        {"backup_ids": identifiers},
        base.paths,
        base.parameters,
    )
    old_dump = store.backup_root / f"{identifiers[0]}.dump"
    old_record = (
        store.deployment_root / "backups" / f"{identifiers[0]}.json"
    )
    return commands, request, package, old_dump, old_record


def _invoke_prune_failure(
    package: Path,
    request: HostRequest,
    commands: Path,
    failure: str,
):
    harness = r'''
import io, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.backup as operation
failure = sys.argv[2]
original_fsync = operation._fsync_directory
def fsync(path):
    old_dump = path / ("backup-" + "1" * 32 + ".dump")
    old_record = path / ("backup-" + "1" * 32 + ".json")
    if (
        failure == "dump-fsync"
        and path.name == "backups"
        and path.parent.name != "deployments"
        and not old_dump.exists()
    ):
        raise OSError("injected dump fsync failure")
    if (
        failure == "record-fsync"
        and path.name == "backups"
        and path.parent.name == "deployments"
        and not old_record.exists()
    ):
        raise OSError("injected record fsync failure")
    return original_fsync(path)
operation._fsync_directory = fsync
original_unlink = Path.unlink
def unlink(path, *args, **kwargs):
    if (
        failure in {"record-unlink", "later-record-unlink"}
        and path.name == "backup-" + "1" * 32 + ".json"
    ):
        raise OSError("injected record unlink failure")
    if (
        failure == "later-dump-unlink"
        and path.name == "backup-" + "1" * 32 + ".dump"
    ):
        raise OSError("injected dump unlink failure")
    return original_unlink(path, *args, **kwargs)
Path.unlink = unlink
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package), failure],
        input=encode_request(request),
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _invoke_record_failure(
    package: Path,
    request: HostRequest,
    commands: Path,
):
    harness = r'''
import io, sys
from datetime import datetime
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.backup as backup
from taskman_ops.host_helper.lifecycle import LifecycleError, LifecycleStore
class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 1, 1, 2, 3, tzinfo=tz)
backup.datetime = FixedDateTime
backup._database_size = lambda *_args: 1024
LifecycleStore.write_backup = lambda *_args: (
    (_ for _ in ()).throw(LifecycleError("injected record failure"))
)
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package)],
        input=encode_request(request),
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _invoke_pending_fsync_failure(
    package: Path,
    request: HostRequest,
    commands: Path,
):
    harness = r'''
import io, sys
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.backup as operation
failed = False
original_fsync = operation._fsync_directory
def fsync(path):
    global failed
    pending = path / (".backup-" + "a" * 32 + ".pending.json")
    dump = path / ("backup-" + "a" * 32 + ".dump")
    if not failed and pending.exists() and not dump.exists():
        failed = True
        raise OSError("injected pending authority fsync failure")
    return original_fsync(path)
operation._fsync_directory = fsync
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package)],
        input=encode_request(request),
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _invoke_with_fsync_trace(
    package: Path,
    request: HostRequest,
    commands: Path,
    trace: Path,
):
    harness = r'''
import io, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.backup as backup
trace = Path(sys.argv[2])
original_fsync = os.fsync
def fsync(descriptor):
    try:
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError:
        target = "unresolved"
    with trace.open("a", encoding="utf-8") as stream:
        stream.write(target + "\n")
    return original_fsync(descriptor)
backup.os.fsync = fsync
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package), str(trace)],
        input=encode_request(request),
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _invoke_with_temp_file_fsync_failure(
    package: Path,
    request: HostRequest,
    commands: Path,
):
    harness = r'''
import io, os, sys
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.backup as backup
backup_root = sys.argv[2]
original_fsync = os.fsync
def fsync(descriptor):
    try:
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError:
        target = ""
    if target.startswith(backup_root + "/") and target.endswith(".tmp"):
        raise OSError("injected temporary dump fsync failure")
    return original_fsync(descriptor)
backup.os.fsync = fsync
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    completed = subprocess.run(
        ["python3", "-I", "-c", harness, str(package), request.paths["backup_root"]],
        input=encode_request(request),
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _invoke_crash_before_dump_rename(
    package: Path,
    request: HostRequest,
    commands: Path,
) -> subprocess.CompletedProcess[bytes]:
    harness = r'''
import io, os, sys
sys.path.insert(0, sys.argv[1])
import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.backup as backup
def crash(_source, _target):
    os._exit(71)
backup.os.replace = crash
payload = sys.stdin.buffer.read()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
entry.main()
'''
    return subprocess.run(
        ["python3", "-I", "-c", harness, str(package)],
        input=encode_request(request),
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
    )


def _valid_backup(
    tmp_path: Path,
) -> tuple[Path, HostRequest, Path]:
    commands = tmp_path / "commands"
    commands.mkdir()
    _command(commands / "psql", "#!/bin/sh\nprintf '1024\\n'\n")
    _command(
        commands / "pg_dump",
        "#!/bin/sh\nfor arg in \"$@\"; do case \"$arg\" in --file=*) printf dump > \"${arg#--file=}\";; esac; done\n",
    )
    _command(commands / "pg_restore", "#!/bin/sh\nexit 0\n")
    credentials = tmp_path / "postgres.pgpass"
    credentials.write_text("secret-canary", encoding="utf-8")
    credentials.chmod(0o600)
    request = HostRequest(
        1, "backup", "op-" + "a" * 32, {"backup_ids": ()},
        {
            "install_root": str(tmp_path / "install"),
            "backup_root": str(tmp_path / "backups"),
        },
        {
            "credentials_path": credentials.as_posix(),
            "database": {
                "host": "127.0.0.1", "name": "taskman_prod",
                "port": 5432, "role": "taskman",
            },
            "reason": "scheduled", "retention": 2,
        },
    )
    return commands, request, build_helper_package(
        tmp_path / "taskman-host.pyz"
    ).path


def _invoke(
    package: Path,
    request: HostRequest,
    commands: Path,
    *,
    timeout: float | None = None,
):
    completed = subprocess.run(
        ["python3", "-I", str(package)],
        input=encode_request(request), capture_output=True, check=False,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
        timeout=timeout,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _command(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
