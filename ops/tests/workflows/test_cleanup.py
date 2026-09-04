"""Plan-first workflow tests for guarded cleanup."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.releases.remote_snapshot import REMOTE_SNAPSHOT_TRANSACTION
from taskman_ops.remote import CommandResult
from taskman_ops.workflows.cleanup import CLEANUP_TRANSACTION, _CLEANUP_EXTRA_DISCOVERY, cleanup, run_locked_cleanup


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.validate_operational_preflight",
        lambda *_args: object(),
    )


def test_cleanup_requires_a_typed_exact_plan_before_the_exclusive_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare affirmative cannot authorize deletion of an operator-visible target set."""

    class _Remote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(0, '{"schema_version":1,"targets":[],"warnings":[]}')

    remote = _Remote()
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.run_locked_cleanup",
        lambda *_args, **_kwargs: pytest.fail("cancelled cleanup acquired an exclusive transaction"),
    )

    result = cleanup(remote, "production", confirm=lambda plan: False)

    assert result.stage == "confirmation-cancelled"
    assert result.changed is False


def test_locked_cleanup_rejects_a_changed_target_set_before_any_deletion() -> None:
    """The host result must prove that every reviewed path survived revalidation."""

    class _Remote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(
                ExitStatus.SAFETY,
                '{"stage":"revalidation","removed":[],"recoverability":[],"changed":false,"warnings":[]}',
            )

    with pytest.raises(OpsError) as raised:
        run_locked_cleanup(
            _Remote(),
            "production",
            targets=(
                {
                    "kind": "staging",
                    "identifier": "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "path": PurePosixPath("/opt/taskman/deployments/uploads/stage-a"),
                    "recoverable": False,
                    "authority": {
                        "record_path": "/opt/taskman/deployments/uploads/stage-a",
                        "state": "completed",
                    },
                },
            ),
            confirmation="production cleanup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )

    assert raised.value.status is ExitStatus.SAFETY


def test_cleanup_transaction_revalidates_every_exact_target_before_removal(tmp_path: Path) -> None:
    """Cleanup must delete only reviewed exact targets after a complete pre-delete revalidation pass."""

    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o750)
    managed = tmp_path / "managed"
    releases = managed / "releases"
    backups = tmp_path / "backups"
    for directory in (managed, releases, backups):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    staging = tmp_path / "deployments" / "uploads"
    staging.mkdir(parents=True, mode=0o750)
    staging.parent.chmod(0o750)
    first = staging / "stage-a"
    second = staging / "stage-b"
    first.write_text("complete", encoding="utf-8")
    second.write_text("complete", encoding="utf-8")
    targets = [
        {"kind": "staging", "identifier": "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "path": str(first), "recoverable": False, "authority": {"record_path": str(first), "state": "completed"}},
        {"kind": "staging", "identifier": "stage-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "path": str(second), "recoverable": False, "authority": {"record_path": str(second), "state": "completed"}},
    ]
    snapshot_transaction = REMOTE_SNAPSHOT_TRANSACTION.replace("owner_uid=$8", f"owner_uid={os.getuid()}", 1)
    initial = subprocess.run(
        ("sh", "-ceu", snapshot_transaction, "taskman-lifecycle-snapshot", str(staging.parent), str(managed), str(releases), str(backups), str(lock_root), "cleanup", "5000", str(os.getuid())),
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    fingerprint = hashlib.sha256(json.dumps(json.loads(initial.stdout), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    transaction = CLEANUP_TRANSACTION.replace(
        "managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root=/var/lock/taskman; operation=cleanup; owner_uid=0;",
        f"managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root={lock_root}; operation=cleanup; owner_uid={os.getuid()};",
        1,
    )
    completed = subprocess.run(
        ("sh", "-ceu", transaction, "taskman-cleanup-transaction", str(managed), str(releases), str(staging.parent), str(backups), json.dumps(targets), "production cleanup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", fingerprint, "5000", "d" * 32),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not first.exists()
    assert not second.exists()
    assert json.loads(completed.stdout)["removed"] == targets


def test_cleanup_transaction_aborts_the_whole_plan_when_any_target_changes_before_deletion(tmp_path: Path) -> None:
    """A later invalid target must not permit deletion of an earlier valid target."""

    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o750)
    staging = tmp_path / "deployments" / "uploads"
    staging.mkdir(parents=True, mode=0o750)
    staging.parent.chmod(0o750)
    first = staging / "stage-a"
    changed = staging / "stage-b"
    first.write_text("complete", encoding="utf-8")
    changed.symlink_to(first)
    targets = [
        {"kind": "staging", "identifier": "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "path": str(first), "recoverable": False, "authority": {"record_path": str(first), "state": "completed"}},
        {"kind": "staging", "identifier": "stage-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "path": str(changed), "recoverable": False, "authority": {"record_path": str(changed), "state": "completed"}},
    ]
    transaction = CLEANUP_TRANSACTION.replace(
        "managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root=/var/lock/taskman; operation=cleanup; owner_uid=0;",
        f"managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root={lock_root}; operation=cleanup; owner_uid={os.getuid()};",
        1,
    )
    completed = subprocess.run(
        ("sh", "-ceu", transaction, "taskman-cleanup-transaction", str(tmp_path / "managed"), str(tmp_path / "managed" / "releases"), str(staging.parent), str(tmp_path / "backups"), json.dumps(targets), "production cleanup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "-", "5000", "d" * 32),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == ExitStatus.SAFETY
    assert first.is_file()
    assert changed.is_symlink()
    assert json.loads(completed.stdout)["removed"] == []


def test_cleanup_discovery_refuses_an_unrecognized_staging_object(tmp_path: Path) -> None:
    """A file is not a cleanup target merely because it resides under the uploads root."""

    deployment = tmp_path / "deployments"
    deployment.mkdir(mode=0o750)
    uploads = deployment / "uploads"
    uploads.mkdir(mode=0o750)
    (uploads / "unrecognized-upload").write_text("not a completion record", encoding="utf-8")
    discovery = _CLEANUP_EXTRA_DISCOVERY.replace("value.st_uid == 0", f"value.st_uid == {os.getuid()}")

    completed = subprocess.run(
        ("sh", "-ceu", discovery, "taskman-cleanup-extra-discovery", str(deployment)),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == ExitStatus.SAFETY


def test_cleanup_transaction_refuses_an_intervening_lifecycle_snapshot_change_before_deletion(
    tmp_path: Path,
) -> None:
    """The reviewed target list expires if lifecycle state changes before the exclusive lock."""

    lock_root = tmp_path / "locks"
    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backups = tmp_path / "backups"
    uploads = deployment / "uploads"
    for directory in (lock_root, managed, releases, deployment, backups, uploads):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    target_path = uploads / "stage-a"
    target_path.write_text("complete", encoding="utf-8")
    targets = [{"kind": "staging", "identifier": "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "path": str(target_path), "recoverable": False, "authority": {"record_path": str(target_path), "state": "completed"}}]

    snapshot_transaction = REMOTE_SNAPSHOT_TRANSACTION.replace("owner_uid=$8", f"owner_uid={os.getuid()}", 1)
    initial = subprocess.run(
        ("sh", "-ceu", snapshot_transaction, "taskman-lifecycle-snapshot", str(deployment), str(managed), str(releases), str(backups), str(lock_root), "cleanup", "5000", str(os.getuid())),
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    fingerprint = hashlib.sha256(json.dumps(json.loads(initial.stdout), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    (managed / "current").symlink_to(releases)
    transaction = CLEANUP_TRANSACTION.replace(
        "managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root=/var/lock/taskman; operation=cleanup; owner_uid=0;",
        f"managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root={lock_root}; operation=cleanup; owner_uid={os.getuid()};",
        1,
    )
    completed = subprocess.run(
        ("sh", "-ceu", transaction, "taskman-cleanup-transaction", str(managed), str(releases), str(deployment), str(backups), json.dumps(targets), "production cleanup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", fingerprint, "5000", "d" * 32),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == ExitStatus.SAFETY
    assert target_path.is_file()
    assert json.loads(completed.stdout)["stage"] == "revalidation"


def test_cleanup_transaction_revalidates_the_exact_restore_record_authority_under_lock(tmp_path: Path) -> None:
    """A reviewed recovery record cannot be altered between confirmation and database cleanup."""

    lock_root = tmp_path / "locks"
    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backups = tmp_path / "backups"
    restores = deployment / "restores"
    for directory in (lock_root, managed, releases, deployment, backups, restores):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)

    recovery_id = "recovery-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    release_id = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    record = restores / f"{recovery_id}.json"
    authority = {
        "record_path": str(record),
        "source_backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "pre_restore_backup_id": "backup-cccccccccccccccccccccccccccccccc",
        "intended_release_id": release_id,
        "recovery_database": "taskman_recovery_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "state": "retained",
    }
    target = {
        "kind": "database",
        "identifier": recovery_id,
        "path": f"/database/{recovery_id}",
        "recoverable": True,
        "authority": authority,
    }
    record.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recovery_id": recovery_id,
                "database": "taskman_prod",
                **{key: value for key, value in authority.items() if key != "record_path"},
                "created_at": "2026-09-05T12:00:00Z",
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    record.chmod(0o600)
    reviewed_extras = {"schema_version": 1, "retained_databases": [target], "completed_staging": []}
    extra_fingerprint = hashlib.sha256(
        json.dumps(reviewed_extras, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    # The record remains a regular, root-like controlled file but its exact
    # state no longer grants deletion authority.
    invalid = json.loads(record.read_text(encoding="utf-8"))
    invalid["state"] = "discarded"
    record.write_text(json.dumps(invalid, separators=(",", ":")), encoding="utf-8")
    record.chmod(0o600)
    transaction = (
        CLEANUP_TRANSACTION.replace(
            "managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root=/var/lock/taskman; operation=cleanup; owner_uid=0;",
            f"managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root={lock_root}; operation=cleanup; owner_uid={os.getuid()};",
            1,
        ).replace("value.st_uid == 0", f"value.st_uid == {os.getuid()}")
    )
    completed = subprocess.run(
        (
            "sh",
            "-ceu",
            transaction,
            "taskman-cleanup-transaction",
            str(managed),
            str(releases),
            str(deployment),
            str(backups),
            json.dumps([target]),
            "production cleanup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "-",
            "5000",
            "d" * 32,
            extra_fingerprint,
            "5432",
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == ExitStatus.SAFETY
    assert record.is_file()
    assert json.loads(completed.stdout) == {
        "stage": "revalidation",
        "removed": [],
        "recoverability": [],
        "changed": False,
        "warnings": [],
    }


def test_cleanup_transaction_reports_prior_exact_removals_when_a_later_database_drop_fails(tmp_path: Path) -> None:
    """A later failure must preserve the ordered truth of earlier completed deletions."""

    lock_root = tmp_path / "locks"
    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backups = tmp_path / "backups"
    uploads = deployment / "uploads"
    records_releases = deployment / "releases"
    records_backups = deployment / "backups"
    manifests = deployment / "manifests"
    for directory in (lock_root, managed, releases, deployment, backups, uploads, records_releases, records_backups, manifests):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    release_id = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    (releases / release_id).mkdir(mode=0o750)
    release_record = records_releases / f"release-{release_id}.json"
    release_record.write_text(json.dumps({"release_id": release_id}, separators=(",", ":")), encoding="utf-8")
    release_record.chmod(0o600)
    manifest = manifests / f"release-{release_id}.json"
    manifest.write_text(json.dumps({"release_id": release_id}, separators=(",", ":")), encoding="utf-8")
    manifest.chmod(0o600)
    for backup_id in ("backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "backup-cccccccccccccccccccccccccccccccc"):
        dump = backups / f"{backup_id}.dump"
        dump.write_bytes(b"backup")
        record = records_backups / f"{backup_id}.json"
        record.write_text(json.dumps({"backup_id": backup_id, "validated": True, "dump_path": str(dump)}, separators=(",", ":")), encoding="utf-8")
        record.chmod(0o600)
    staging = uploads / "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    staging.write_text("complete", encoding="utf-8")
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "commands.log"
    psql = commands / "psql"
    psql.write_text(
        "#!/bin/sh\nprintf 'psql %s\\n' \"$*\" >> \"$COMMAND_LOG\"\n"
        "case \"$*\" in *'FROM pg_database'*) printf '1\\n';; *'DROP DATABASE'*) exit 1;; esac\n",
        encoding="utf-8",
    )
    psql.chmod(0o700)
    sudo = commands / "sudo"
    sudo.write_text("#!/bin/sh\nprintf 'sudo %s\\n' \"$*\" >> \"$COMMAND_LOG\"\ntest \"$1\" = -u && test \"$2\" = postgres && test \"$3\" = -- || exit 1\nshift 3\nexec \"$@\"\n", encoding="utf-8")
    sudo.chmod(0o700)
    targets = [
        {"kind": "staging", "identifier": "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "path": str(staging), "recoverable": False, "authority": {"record_path": str(staging), "state": "completed"}},
        {"kind": "database", "identifier": "recovery-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "path": "/database/recovery-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "recoverable": True, "authority": {"record_path": str(deployment / "restores" / "recovery-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.json"), "source_backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "pre_restore_backup_id": "backup-cccccccccccccccccccccccccccccccc", "intended_release_id": release_id, "recovery_database": "taskman_recovery_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "state": "retained"}},
    ]
    transaction = CLEANUP_TRANSACTION.replace(
        "managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root=/var/lock/taskman; operation=cleanup; owner_uid=0;",
        f"managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root={lock_root}; operation=cleanup; owner_uid={os.getuid()};",
        1,
    )
    completed = subprocess.run(
        ("sh", "-ceu", transaction, "taskman-cleanup-transaction", str(managed), str(releases), str(deployment), str(backups), json.dumps(targets), "production cleanup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "-", "5000", "d" * 32),
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}", "COMMAND_LOG": str(log)},
        check=False,
    )

    assert completed.returncode == ExitStatus.SAFETY
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "cleanup", completed.stderr
    assert not staging.exists()
    assert evidence["stage"] == "cleanup"
    assert evidence["removed"] == [targets[0]]
    assert evidence["recoverability"] == [False]
    assert evidence["changed"] is True
    assert "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres" in log.read_text(encoding="utf-8")


def test_locked_cleanup_propagates_partial_removal_evidence_from_the_host() -> None:
    """Controller failure results must not erase durable host-side deletion facts."""

    target = {
        "kind": "staging",
        "identifier": "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "path": "/opt/taskman/deployments/uploads/stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "recoverable": False,
        "authority": {"record_path": "/opt/taskman/deployments/uploads/stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "state": "completed"},
    }

    class _Remote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(
                ExitStatus.SAFETY,
                json.dumps({"stage": "cleanup", "removed": [target], "recoverability": [False], "changed": True, "warnings": ["database drop failed"]}),
            )

    with pytest.raises(OpsError) as raised:
        run_locked_cleanup(
            _Remote(),
            "production",
            targets=(target,),
            confirmation="production cleanup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )

    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.stage == "cleanup"
    assert raised.value.changed is True
    assert getattr(raised.value, "removed") == (target,)


def test_cleanup_discovery_accepts_only_a_root_owned_completed_staging_receipt(tmp_path: Path) -> None:
    """Known completed staging is a strict durable receipt, not a filename convention."""

    deployment = tmp_path / "deployments"
    deployment.mkdir(mode=0o750)
    uploads = deployment / "uploads"
    uploads.mkdir(mode=0o750)
    identifier = "stage-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    receipt = uploads / f"{identifier}.json"
    receipt.write_text(
        json.dumps({"schema_version": 1, "staging_id": identifier, "state": "completed", "completed_at": "2026-09-05T12:00:00Z"}),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    discovery = _CLEANUP_EXTRA_DISCOVERY.replace("value.st_uid == 0", f"value.st_uid == {os.getuid()}")

    completed = subprocess.run(
        ("sh", "-ceu", discovery, "taskman-cleanup-extra-discovery", str(deployment)),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["completed_staging"] == [
        {
            "kind": "staging",
            "identifier": identifier,
            "path": str(receipt),
            "recoverable": False,
            "authority": {"record_path": str(receipt), "state": "completed"},
        }
    ]
