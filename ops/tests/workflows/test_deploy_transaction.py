"""Executable local harness for the one-lock deployment transaction."""

from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import select
import shutil
import subprocess
import tarfile

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.releases.activation import _ACTIVATION_BODY
from taskman_ops.releases.remote_locking import REMOTE_LOCK_FRAMING
from taskman_ops.releases.staging import _STAGE_BODY
from taskman_ops.verification import CheckStatus, VerificationCheck, VerificationReport
from taskman_ops.workflows.deploy_transaction import (
    DEPLOY_TRANSACTION,
    _failure_payload,
    _success_payload,
    _transaction_error,
)


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CANDIDATE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


def _verification_mapping(*, successful: bool = True) -> dict[str, object]:
    names = (
        "taskman-service",
        "release-identity",
        "caddy-service",
        "listener-topology",
        "startup-journal",
        "local-readiness",
        "public-readiness",
        "public-hsts",
    )
    if not successful:
        names = names[:-1]
    return VerificationReport(
        ExitStatus.OK if successful else ExitStatus.READINESS,
        CANDIDATE,
        CANDIDATE,
        tuple(
            VerificationCheck(
                name,
                CheckStatus.PASSED if successful or name != "public-readiness" else CheckStatus.FAILED,
                f"{name} passed" if successful or name != "public-readiness" else f"{name} failed",
            )
            for name in names
        ),
        (
            None
            if successful
            else "inspect the fixed verification summaries and correct the reported host state before retrying"
        ),
    ).to_mapping()


def _deployed_payload(changed_stages: list[str]) -> dict[str, object]:
    return {
        "stage": "deployed",
        "previous_release_id": CURRENT,
        "candidate_release_id": CANDIDATE,
        "selected_release_id": CANDIDATE,
        "backup_id": "backup-" + "a" * 32,
        "activation_id": "activation-" + "d" * 32,
        "service_state": "active",
        "database_state": "unchanged",
        "activation_recorded": True,
        "changed": True,
        "changed_stages": changed_stages,
        "warnings": [],
        "recovery_commands": [
            "systemctl status taskman.service",
            "readlink -f /srv/taskman/current",
            "journalctl --no-pager --unit taskman.service --lines=100",
        ],
        "residue_paths": [],
        "verification": _verification_mapping(),
    }


def test_record_publication_failure_retains_its_successful_verification_evidence() -> None:
    verification = _verification_mapping()
    payload = {
        **_deployed_payload(
            [
                "staging",
                "backup",
                "stop",
                "migration",
                "selection",
                "start",
                "verification",
                "records",
            ]
        ),
        "stage": "records",
        "activation_recorded": False,
        "verification": verification,
    }

    assert _failure_payload(payload) == payload


@pytest.mark.parametrize(
    "changed_stages",
    [
        ["staging"],
        ["backup", "verification", "records"],
        ["staging", "backup", "records"],
    ],
)
def test_deployed_success_rejects_ordered_but_impossible_stage_subsets(
    changed_stages: list[str],
) -> None:
    """An ordered subset is not evidence that the deployment reached every required boundary."""

    with pytest.raises(OpsError):
        _success_payload(
            _deployed_payload(changed_stages),
            CANDIDATE,
            PurePosixPath("/srv/taskman"),
            PurePosixPath("/srv/taskman/deployments/uploads/.upload-candidate"),
        )


def test_deployed_success_accepts_the_exact_resume_finalization_sequence() -> None:
    payload = _deployed_payload(["verification", "records"])

    assert _success_payload(
        payload,
        CANDIDATE,
        PurePosixPath("/srv/taskman"),
        PurePosixPath("/srv/taskman/deployments/uploads/.upload-candidate"),
    ) == payload


def test_failure_evidence_requires_changed_to_match_stage_sequence_presence() -> None:
    payload = {
        **_deployed_payload([]),
        "stage": "backup",
        "activation_id": None,
        "activation_recorded": False,
        "service_state": "active",
        "selected_release_id": CURRENT,
        "verification": None,
    }

    assert payload["changed"] is True
    assert _failure_payload(payload) is None


def test_failure_evidence_rejects_an_impossible_prefix_for_its_boundary() -> None:
    payload = {
        **_deployed_payload(["staging", "migration"]),
        "stage": "migration",
        "activation_recorded": False,
        "service_state": "stopped",
        "selected_release_id": CURRENT,
        "database_state": "unknown",
        "verification": None,
    }

    assert _failure_payload(payload) is None


def test_failed_resume_verification_accepts_its_exact_terminal_sequence() -> None:
    payload = {
        **_deployed_payload(["verification"]),
        "stage": "verification",
        "activation_recorded": False,
        "verification": _verification_mapping(successful=False),
    }

    assert _failure_payload(payload) == payload


def test_lock_contention_retains_exact_unknown_state_and_safe_inspection_commands() -> None:
    managed_root = Path("/srv/taskman")
    output = json.dumps(
        {
            "schema_version": 1,
            "holder": {
                "operation": "backup",
                "pid": 123,
                "started_at": "2026-09-05T12:00:00Z",
                "mode": "exclusive",
            },
        }
    )

    error = _transaction_error(
        ExitStatus.LOCKED,
        "deploy",
        "locked deployment transaction failed",
        output,
        managed_root=managed_root,
        candidate_release_id=CANDIDATE,
    )

    assert error.status is ExitStatus.LOCKED
    assert error.changed is False
    assert error.selected_release_id == "unknown"
    assert error.service_state == "unknown"
    assert error.database_state == "unchanged"
    assert error.activation_recorded is False
    assert error.changed_stages == ()
    assert error.recovery_commands == (
        "systemctl status taskman.service",
        "readlink -f /srv/taskman/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
    )


def _manifest_mapping(release_id: str, migrations: list[dict[str, str]] | None = None) -> dict[str, object]:
    revision = release_id.removesuffix("-ubuntu26.04-amd64-otp27.3.4.6").rsplit("-", 1)[1]
    return {
        "schema_version": 1,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": revision[0] * 40,
        "release_id": release_id,
        "built_at": "2026-09-05T12:00:00Z",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "elixir_version": "1.18.3",
        "node_version": "22.22.1",
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "migrations": migrations or [],
        "top_level": "taskman",
    }


def _manifest_json(release_id: str, migrations: list[dict[str, str]] | None = None) -> str:
    return json.dumps(_manifest_mapping(release_id, migrations), sort_keys=True, separators=(",", ":")) + "\n"


def _manifest_b64(release_id: str, migrations: list[dict[str, str]] | None = None) -> str:
    return base64.b64encode(_manifest_json(release_id, migrations).encode()).decode()


@pytest.mark.parametrize(
    ("failure", "expected_returncode"),
    [
        (None, 0),
        ("capacity", 10),
        ("migration", 7),
        ("start", 8),
        ("foreign-activation", 10),
        ("readiness", 9),
        ("pid", 8),
        ("listeners", 8),
        ("malformed-listeners", 8),
        ("journal", 8),
        ("hsts", 9),
        ("duplicate-cache-control", 9),
        ("duplicate-hsts", 9),
        ("wrong-body", 9),
        ("deadline", 9),
        ("residue", 10),
        ("residue-success", 0),
        ("manifest-publication", 8),
        ("release-publication", 8),
        ("activation-publication", 8),
    ],
)
def test_one_locked_transaction_stages_backs_up_activates_verifies_then_publishes_the_final_edge(
    tmp_path: Path, failure: str | None, expected_returncode: int,
) -> None:
    """Releasing the lock or publishing the activation before readiness must fail this harness."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    upload_root = deployment / "uploads"
    for directory in (managed, releases, deployment, upload_root, backup_root):
        directory.mkdir(parents=True)
        directory.chmod(0o750)
    (deployment / "manifests").mkdir()
    (deployment / "manifests").chmod(0o750)
    (deployment / "manifests" / f"release-{CURRENT}.json").write_text(_manifest_json(CURRENT), encoding="utf-8")
    (deployment / "manifests" / f"release-{CURRENT}.json").chmod(0o600)
    _record_current_lifecycle(deployment, backup_root)
    _release(releases / CURRENT, release_id=CURRENT, checksum="b" * 64)
    (managed / "current").symlink_to(releases / CURRENT)
    token = "d" * 32
    upload = upload_root / f".upload-{CANDIDATE}-{token}.tar.gz"
    _archive(upload)
    upload.chmod(0o600)
    archive_bytes = upload.read_bytes()
    residue_paths: list[Path] = []
    if failure in {"residue", "residue-success"}:
        foreign_upload = upload_root / ".upload-foreign.tar.gz"
        foreign_upload.write_bytes(b"foreign")
        foreign_stage = releases / ".stage-foreign"
        foreign_stage.mkdir()
        residue_paths = [foreign_upload, foreign_stage]
        if failure == "residue":
            provisionals = deployment / "provisionals"
            provisionals.mkdir()
            provisionals.chmod(0o750)
            foreign_provisional = (
                provisionals / "activation-ffffffffffffffffffffffffffffffff.json"
            )
            foreign_provisional.write_text("{}\n", encoding="utf-8")
            foreign_provisional.chmod(0o600)
            residue_paths.append(foreign_provisional)
    commands = tmp_path / "commands"
    commands.mkdir()
    events = tmp_path / "events"
    _stubs(commands)
    manifest = _manifest_b64(CANDIDATE)
    transaction = DEPLOY_TRANSACTION.replace(
        "lock_root=/var/lock/taskman;", f"lock_root={tmp_path / 'canonical-lock'};", 1
    ).replace("record_owner_uid=0", "record_owner_uid=$(id -u)", 1)

    completed = subprocess.run(
        (
            "sh",
                "-ceu",
            transaction,
            "taskman-deploy-test",
            str(managed),
            str(releases),
            str(deployment),
            str(backup_root),
            "4000",
            "taskman.example.test",
            CURRENT,
            CANDIDATE,
            sha256(archive_bytes).hexdigest(),
            token,
            "no-change",
            manifest,
            str(upload),
            str(commands / "taskman-backup"),
            base64.b64encode(_STAGE_BODY.encode()).decode(),
            base64.b64encode(
                ("printf '{\\\"activation_id\\\":\\\"activation-" + "e" * 32 + "\\\"}\\n'" if failure == "foreign-activation" else _ACTIVATION_BODY).encode()
            ).decode(),
            "5000",
            "127.0.0.1",
            "5432",
            "taskman",
            "taskman_prod",
            "14",
            "6789",
            "0",
            "/etc/caddy/Caddyfile",
            "0.05",
            "0.05",
        ),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TASKMAN_EVENTS": str(events),
            "TASKMAN_STATE": str(tmp_path / "service-state"),
            "TASKMAN_ACTIVATION_RECORD": str(deployment / "activations" / f"activation-{token}.json"),
            "TASKMAN_FAILURE": failure or "",
            "TASKMAN_CANDIDATE": str(releases / CANDIDATE),
            "TASKMAN_DEPLOYMENT": str(deployment),
            "TASKMAN_PUBLICATION_MARKER": str(tmp_path / "publication-failed"),
            "TASKMAN_BACKUP_ROOT": str(backup_root),
            "TASKMAN_CURRENT_RELEASE": CURRENT,
            "TASKMAN_CANDIDATE_RELEASE": CANDIDATE,
        },
        check=False,
    )

    assert completed.returncode == expected_returncode, completed.stderr
    activation_record = deployment / "activations" / f"activation-{token}.json"
    provisional = deployment / "provisionals" / f"activation-{token}.json"
    if failure == "migration":
        assert (managed / "current").resolve() == releases / CURRENT
        assert not activation_record.exists()
        assert not provisional.exists()
        payload = json.loads(completed.stdout)
        assert payload["service_state"] == "stopped"
        assert payload["database_state"] == "unknown"
        return
    if failure == "capacity":
        assert (managed / "current").resolve() == releases / CURRENT
        assert not upload.exists()
        assert not activation_record.exists()
        assert not provisional.exists()
        assert not events.exists()
        return
    if failure == "residue":
        payload = json.loads(completed.stdout)
        assert payload["residue_paths"] == sorted(str(path) for path in residue_paths)
        assert payload["recovery_commands"][3:] == [
            f"stat -Lc '%U:%G %a %F %n' -- {path}" for path in sorted(residue_paths)
        ]
        assert all(path.exists() for path in residue_paths)
        assert set(payload["warnings"]) >= {
            "unexpected deployment-root entry: uploads",
            "unexpected deployment-root entry: provisionals",
            "unrecognized release directory: .stage-foreign",
        }
        return
    if failure == "residue-success":
        payload = json.loads(completed.stdout)
        assert payload["stage"] == "deployed"
        assert payload["residue_paths"] == sorted(str(path) for path in residue_paths)
        assert payload["recovery_commands"][3:] == [
            f"stat -Lc '%U:%G %a %F %n' -- {path}"
            for path in sorted(residue_paths)
        ]
        assert all(path.exists() for path in residue_paths)
    if failure in {"manifest-publication", "release-publication", "activation-publication"}:
        payload = json.loads(completed.stdout)
        failed_directory = deployment / {
            "manifest-publication": "manifests",
            "release-publication": "releases",
            "activation-publication": "activations",
        }[failure]
        foreign_temporary = failed_directory / ".foreign-publication.tmp"
        assert payload["stage"] == "records"
        assert payload["activation_id"] == f"activation-{token}"
        assert payload["database_state"] == "unchanged"
        assert payload["changed_stages"] == [
            "staging", "backup", "stop", "migration", "selection", "start", "verification", "records"
        ]
        assert foreign_temporary.is_file()
        assert str(foreign_temporary) in payload["residue_paths"]
        assert (
            f"stat -Lc '%U:%G %a %F %n' -- {foreign_temporary}"
            in payload["recovery_commands"]
        )
        assert payload["recovery_commands"][3:] == [
            f"stat -Lc '%U:%G %a %F %n' -- {path}"
            for path in payload["residue_paths"]
        ]
        operation_temporaries = [
            path
            for directory in (
                deployment / "manifests",
                deployment / "releases",
                deployment / "activations",
            )
            for path in directory.glob(".*")
            if path != foreign_temporary
        ]
        assert operation_temporaries == []
        assert provisional.is_file()
        retry_token = "e" * 32
        retry_upload = upload_root / f".upload-{CANDIDATE}-{retry_token}.tar.gz"
        retry_upload.write_bytes(archive_bytes)
        retry_upload.chmod(0o600)
        retry_args = list(completed.args)
        retry_args[retry_args.index(token)] = retry_token
        retry_args[retry_args.index(str(upload))] = str(retry_upload)
        retry_environment = {
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TASKMAN_EVENTS": str(events),
            "TASKMAN_STATE": str(tmp_path / "service-state"),
            "TASKMAN_ACTIVATION_RECORD": str(
                deployment / "activations" / f"activation-{retry_token}.json"
            ),
            "TASKMAN_FAILURE": "",
            "TASKMAN_CANDIDATE": str(releases / CANDIDATE),
            "TASKMAN_DEPLOYMENT": str(deployment),
            "TASKMAN_PUBLICATION_MARKER": str(tmp_path / "publication-failed"),
            "TASKMAN_BACKUP_ROOT": str(backup_root),
            "TASKMAN_CURRENT_RELEASE": CURRENT,
            "TASKMAN_CANDIDATE_RELEASE": CANDIDATE,
        }
        retried = subprocess.run(
            retry_args, text=True, capture_output=True, env=retry_environment, check=False
        )
        assert retried.returncode == 0, (retried.stderr, retried.stdout)
        retried_payload = json.loads(retried.stdout)
        assert retried_payload["activation_id"] == f"activation-{token}"
        assert str(foreign_temporary) in retried_payload["residue_paths"]
        assert foreign_temporary.is_file()
        assert not provisional.exists()
        for directory in (
            deployment / "manifests",
            deployment / "releases",
            deployment / "activations",
        ):
            assert directory.stat().st_mode & 0o777 == 0o750
            assert directory.stat().st_uid == os.getuid()
        for record in (
            deployment / "manifests" / f"release-{CANDIDATE}.json",
            deployment / "releases" / f"release-{CANDIDATE}.json",
            deployment / "activations" / f"activation-{token}.json",
        ):
            assert record.stat().st_mode & 0o777 == 0o600
            assert record.stat().st_uid == os.getuid()
        return
    if failure == "start":
        assert (managed / "current").resolve() == releases / CANDIDATE
        payload = json.loads(completed.stdout)
        assert payload["service_state"] == "unknown"
        assert payload["selected_release_id"] == CANDIDATE
        assert payload["changed_stages"] == [
            "staging", "backup", "stop", "migration", "selection", "start"
        ]
        return
    if failure == "foreign-activation":
        assert (managed / "current").resolve() == releases / CURRENT
        payload = json.loads(completed.stdout)
        assert payload["service_state"] == "unknown"
        assert payload["selected_release_id"] == "unknown"
        assert payload["changed_stages"] == ["staging", "backup"]
        return
    if failure in {
        "readiness",
        "pid",
        "listeners",
        "malformed-listeners",
        "journal",
        "hsts",
        "duplicate-cache-control",
        "duplicate-hsts",
        "wrong-body",
        "deadline",
    }:
        assert (managed / "current").resolve() == releases / CANDIDATE
        assert not activation_record.exists()
        assert provisional.is_file()
        payload = json.loads(completed.stdout)
        assert payload["service_state"] == "active"
        assert payload["database_state"] == "unchanged"
        assert payload["changed"] is True
        assert payload["changed_stages"] == [
            "staging",
            "backup",
            "stop",
            "migration",
            "selection",
            "start",
            "verification",
        ]
        assert payload["verification"]["status"] == "failed"
        expected_status = 8 if failure in {"pid", "listeners", "malformed-listeners", "journal"} else 9
        assert payload["verification"]["exit_status"] == expected_status
        return
    assert events.read_text(encoding="utf-8").splitlines() == [
        "backup",
        "stop",
        "inactive",
        "migrate",
        "start",
        "verify-pid",
        "verify-caddy",
        "verify-local",
        "verify-public",
    ]
    assert activation_record.is_file()
    assert not provisional.exists()
    payload = json.loads(completed.stdout)
    assert payload["verification"]["status"] == "ok"
    assert payload["verification"]["exit_status"] == 0
    assert payload["changed_stages"] == [
        "staging",
        "backup",
        "stop",
        "migration",
        "selection",
        "start",
        "verification",
        "records",
    ]


@pytest.mark.parametrize(("failure", "expected_returncode"), [("", 0), ("readiness", 9)])
def test_an_exact_current_release_reports_verification_without_backup_or_restart(
    tmp_path: Path,
    failure: str,
    expected_returncode: int,
) -> None:
    """A new token runs verification truthfully without inventing a backup or restart."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    upload_root = deployment / "uploads"
    for directory in (managed, releases, deployment, upload_root, backup_root, deployment / "activations", deployment / "releases", deployment / "manifests"):
        directory.mkdir(parents=True)
        directory.chmod(0o750)
    _release(releases / CURRENT, release_id=CURRENT, checksum="b" * 64)
    _release(releases / CANDIDATE, release_id=CANDIDATE, checksum="a" * 64)
    _record_current_lifecycle(deployment, backup_root, include_backup=True)
    (managed / "current").symlink_to(releases / CANDIDATE)
    timestamp = "2026-09-05T12:00:00Z"
    release_record = deployment / "releases" / f"release-{CANDIDATE}.json"
    release_record.write_text(
        f'{{"schema_version":1,"release_id":"{CANDIDATE}","artifact_sha256":"{"a" * 64}","installed_at":"{timestamp}","activated_at":"{timestamp}","previous_release_id":"{CURRENT}","backup_id":"backup-{"a" * 32}","migration_policy":"no-change"}}\n',
        encoding="utf-8",
    )
    activation_record = deployment / "activations" / ("activation-" + "a" * 32 + ".json")
    activation_record.write_text(
        f'{{"schema_version":1,"activation_id":"activation-{"a" * 32}","previous_release_id":"{CURRENT}","candidate_release_id":"{CANDIDATE}","activated_at":"{timestamp}","backup_id":"backup-{"a" * 32}","migration_policy":"no-change"}}\n',
        encoding="utf-8",
    )
    release_record.chmod(0o600)
    activation_record.chmod(0o600)
    candidate_manifest = deployment / "manifests" / f"release-{CANDIDATE}.json"
    candidate_manifest.write_text(_manifest_json(CANDIDATE), encoding="utf-8")
    candidate_manifest.chmod(0o600)
    current_manifest = deployment / "manifests" / f"release-{CURRENT}.json"
    current_manifest.write_text(_manifest_json(CURRENT), encoding="utf-8")
    current_manifest.chmod(0o600)
    token = "d" * 32
    upload = upload_root / f".upload-{CANDIDATE}-{token}.tar.gz"
    upload.write_bytes(b"unused on no-op")
    upload.chmod(0o600)
    commands = tmp_path / "commands"
    commands.mkdir()
    _stubs(commands)
    manifest = _manifest_b64(CANDIDATE)
    transaction = DEPLOY_TRANSACTION.replace(
        "lock_root=/var/lock/taskman;", f"lock_root={tmp_path / 'canonical-lock'};", 1
    ).replace("record_owner_uid=0", "record_owner_uid=$(id -u)", 1)

    completed = subprocess.run(
        (
            "sh", "-ceu", transaction, "taskman-deploy-noop-test", str(managed), str(releases),
            str(deployment), str(backup_root), "4000", "taskman.example.test", CANDIDATE, CANDIDATE,
            "a" * 64, token, "no-change", manifest, str(upload), str(commands / "taskman-backup"),
            base64.b64encode(_STAGE_BODY.encode()).decode(), base64.b64encode(_ACTIVATION_BODY.encode()).decode(),
            "5000", "127.0.0.1", "5432", "taskman", "taskman_prod", "14", "6789", "0", "/etc/caddy/Caddyfile",
            "0.05", "0.05",
        ),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TASKMAN_EVENTS": str(tmp_path / "events"),
            "TASKMAN_STATE": str(tmp_path / "service-state"),
            "TASKMAN_ACTIVATION_RECORD": str(deployment / "activations" / f"activation-{token}.json"),
            "TASKMAN_FAILURE": failure,
            "TASKMAN_CANDIDATE": str(releases / CANDIDATE),
        },
        check=False,
    )

    assert completed.returncode == expected_returncode, completed.stderr
    payload = json.loads(completed.stdout)
    if failure == "readiness":
        assert payload["stage"] == "verification"
        assert payload["changed"] is True
        assert payload["changed_stages"] == ["verification"]
    else:
        assert payload["stage"] == "already-current"
        assert payload["changed"] is False
        assert payload["changed_stages"] == []
    assert not upload.exists()
    expected_events = ["verify-pid", "verify-caddy"]
    if not failure:
        expected_events.extend(["verify-local", "verify-public"])
    assert (tmp_path / "events").read_text(encoding="utf-8").splitlines() == expected_events


def test_genesis_mode_backs_up_migrates_selects_verifies_and_is_rerunnable(
    tmp_path: Path,
) -> None:
    """A clean lifecycle must use the same lock-held transaction without inventing a predecessor."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    uploads = deployment / "uploads"
    for directory in (managed, releases, deployment, uploads, backup_root):
        directory.mkdir(parents=True)
        directory.chmod(0o750)

    commands = tmp_path / "commands"
    commands.mkdir()
    events = tmp_path / "events"
    _stubs(commands)
    manifest = _manifest_b64(CANDIDATE)
    transaction = DEPLOY_TRANSACTION.replace(
        "lock_root=/var/lock/taskman;", f"lock_root={tmp_path / 'canonical-lock'};", 1
    ).replace("record_owner_uid=0", "record_owner_uid=$(id -u)", 1)
    archive_fixture = tmp_path / "candidate.tar.gz"
    _archive(archive_fixture)
    archive_bytes = archive_fixture.read_bytes()
    archive_checksum = sha256(archive_bytes).hexdigest()

    def execute(token: str, failure: str = "") -> subprocess.CompletedProcess[str]:
        upload = uploads / f".upload-{CANDIDATE}-{token}.tar.gz"
        upload.write_bytes(archive_bytes)
        upload.chmod(0o600)
        return subprocess.run(
            (
                "sh",
                "-ceu",
                transaction,
                "taskman-genesis-deploy",
                str(managed),
                str(releases),
                str(deployment),
                str(backup_root),
                "4000",
                "taskman.example.test",
                "-",
                CANDIDATE,
                archive_checksum,
                token,
                "no-change",
                manifest,
                str(upload),
                str(commands / "taskman-backup"),
                base64.b64encode(_STAGE_BODY.encode()).decode(),
                base64.b64encode(_ACTIVATION_BODY.encode()).decode(),
                "5000",
                "127.0.0.1",
                "5432",
                "taskman",
                "taskman_prod",
                "14",
                "6789",
                "0",
                "/etc/caddy/Caddyfile",
                "0.05",
                "0.05",
                "-",
                "-",
                "-",
                "1",
            ),
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "PATH": f"{commands}:{os.environ['PATH']}",
                "TASKMAN_EVENTS": str(events),
                "TASKMAN_STATE": str(tmp_path / "service-state"),
                "TASKMAN_ACTIVATION_RECORD": str(
                    deployment / "activations" / f"activation-{token}.json"
                ),
                "TASKMAN_FAILURE": failure,
                "TASKMAN_CANDIDATE": str(releases / CANDIDATE),
                "TASKMAN_DEPLOYMENT": str(deployment),
                "TASKMAN_PUBLICATION_MARKER": str(tmp_path / "publication-failed"),
                "TASKMAN_BACKUP_ROOT": str(backup_root),
                "TASKMAN_CANDIDATE_RELEASE": CANDIDATE,
            },
            check=False,
        )

    rejected_backup = execute("b" * 32, "backup")
    assert rejected_backup.returncode == ExitStatus.BACKUP, (
        rejected_backup.stderr,
        rejected_backup.stdout,
    )
    backup_payload = json.loads(rejected_backup.stdout)
    assert backup_payload["previous_release_id"] is None
    assert backup_payload["selected_release_id"] == "unknown"
    assert backup_payload["service_state"] == "stopped"
    assert backup_payload["changed_stages"] == ["staging"]
    assert _failure_payload(backup_payload, genesis=True) == backup_payload
    shutil.rmtree(releases / CANDIDATE)

    rejected_capacity = execute("c" * 32, "capacity")
    assert rejected_capacity.returncode == ExitStatus.SAFETY, (
        rejected_capacity.stderr,
        rejected_capacity.stdout,
    )
    capacity_payload = json.loads(rejected_capacity.stdout)
    assert capacity_payload["previous_release_id"] is None
    assert capacity_payload["selected_release_id"] == "unknown"
    assert capacity_payload["service_state"] == "stopped"
    assert capacity_payload["changed"] is False
    assert _failure_payload(capacity_payload, genesis=True) == capacity_payload

    interrupted = execute("d" * 32, "readiness")
    assert interrupted.returncode == ExitStatus.READINESS, (
        interrupted.stderr,
        interrupted.stdout,
    )
    assert json.loads(interrupted.stdout)["changed_stages"] == [
        "staging",
        "backup",
        "migration",
        "selection",
        "start",
        "verification",
    ]
    first = execute("e" * 32)
    assert first.returncode == 0, (first.stderr, first.stdout)
    first_payload = json.loads(first.stdout)
    assert first_payload["previous_release_id"] is None
    assert first_payload["changed_stages"] == ["verification", "records"]
    assert json.loads(
        (deployment / "activations" / ("activation-" + "d" * 32 + ".json")).read_text(
            encoding="utf-8"
        )
    )["previous_release_id"] is None

    events_after_first = events.read_text(encoding="utf-8").splitlines()
    second = execute("f" * 32)
    assert second.returncode == 0, (second.stderr, second.stdout)
    second_payload = json.loads(second.stdout)
    assert second_payload["stage"] == "already-current"
    assert second_payload["changed"] is False
    assert events.read_text(encoding="utf-8").splitlines() == [
        *events_after_first,
        "verify-pid",
        "verify-caddy",
        "verify-local",
        "verify-public",
    ]


@pytest.mark.parametrize("partial_state", ["provisional", "release-record-only", "activation-record-with-provisional"])
def test_verified_partial_finalization_resumes_only_the_missing_final_records_and_publishes_activation_last(
    tmp_path: Path, partial_state: str,
) -> None:
    """A retry after post-selection verification cannot invent a second backup or edge."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    uploads = deployment / "uploads"
    for directory in (managed, releases, deployment, uploads, backup_root):
        directory.mkdir(parents=True)
        directory.chmod(0o750)
    manifests = deployment / "manifests"
    manifests.mkdir()
    manifests.chmod(0o750)
    current_manifest = manifests / f"release-{CURRENT}.json"
    current_manifest.write_text(_manifest_json(CURRENT), encoding="utf-8")
    current_manifest.chmod(0o600)
    _record_current_lifecycle(deployment, backup_root)
    _release(releases / CURRENT, release_id=CURRENT, checksum="b" * 64)
    (managed / "current").symlink_to(releases / CURRENT)
    commands = tmp_path / "commands"
    commands.mkdir()
    events = tmp_path / "events"
    _stubs(commands)
    manifest = _manifest_b64(CANDIDATE)
    transaction = DEPLOY_TRANSACTION.replace(
        "lock_root=/var/lock/taskman;", f"lock_root={tmp_path / 'canonical-lock'};", 1
    ).replace("record_owner_uid=0", "record_owner_uid=$(id -u)", 1)
    archive_checksum: str | None = None

    def execute(token: str, failure: str) -> subprocess.CompletedProcess[str]:
        nonlocal archive_checksum
        upload = uploads / f".upload-{CANDIDATE}-{token}.tar.gz"
        _archive(upload)
        upload.chmod(0o600)
        archive_checksum = archive_checksum or sha256(upload.read_bytes()).hexdigest()
        return subprocess.run(
            (
                "sh", "-ceu", transaction, "taskman-provisional-retry", str(managed), str(releases),
                str(deployment), str(backup_root), "4000", "taskman.example.test", "-", CANDIDATE,
                archive_checksum, token, "no-change", manifest, str(upload),
                str(commands / "taskman-backup"), base64.b64encode(_STAGE_BODY.encode()).decode(),
                base64.b64encode(_ACTIVATION_BODY.encode()).decode(), "5000", "127.0.0.1", "5432",
                "taskman", "taskman_prod", "14", "6789", "0", "/etc/caddy/Caddyfile",
                "0.05", "0.05",
            ),
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "PATH": f"{commands}:{os.environ['PATH']}",
                "TASKMAN_EVENTS": str(events),
                "TASKMAN_STATE": str(tmp_path / "service-state"),
                    "TASKMAN_ACTIVATION_RECORD": str(deployment / "activations" / f"activation-{token}.json"),
                    "TASKMAN_FAILURE": failure,
                    "TASKMAN_CANDIDATE": str(releases / CANDIDATE),
                    "TASKMAN_DEPLOYMENT": str(deployment),
                    "TASKMAN_PUBLICATION_MARKER": str(tmp_path / "publication-failed"),
                    "TASKMAN_BACKUP_ROOT": str(backup_root),
                    "TASKMAN_CURRENT_RELEASE": CURRENT,
                    "TASKMAN_CANDIDATE_RELEASE": CANDIDATE,
            },
            check=False,
        )

    first = execute("d" * 32, "readiness")
    assert first.returncode == 9, first.stderr
    provisional = deployment / "provisionals" / ("activation-" + "d" * 32 + ".json")
    assert provisional.is_file()
    assert not (deployment / "activations" / ("activation-" + "d" * 32 + ".json")).exists()
    (backup_root / "backup.dump").write_bytes(b"validated backup")
    (backup_root / "backup.dump").chmod(0o600)

    if partial_state in {"release-record-only", "activation-record-with-provisional"}:
        provisional_data = json.loads(provisional.read_text(encoding="utf-8"))
        release_records = deployment / "releases"
        release_records.mkdir(exist_ok=True)
        release_records.chmod(0o750)
        candidate_manifest = deployment / "manifests" / f"release-{CANDIDATE}.json"
        candidate_manifest.write_text(base64.b64decode(manifest).decode(), encoding="utf-8")
        candidate_manifest.chmod(0o600)
        release_record = {
            "schema_version": 1,
            "release_id": CANDIDATE,
            "artifact_sha256": archive_checksum,
            "installed_at": provisional_data["installed_at"],
            "activated_at": provisional_data["activated_at"],
            "previous_release_id": CURRENT,
            "backup_id": provisional_data["backup_id"],
            "migration_policy": "no-change",
        }
        (release_records / f"release-{CANDIDATE}.json").write_text(
            json.dumps(release_record, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        (release_records / f"release-{CANDIDATE}.json").chmod(0o600)
        if partial_state == "activation-record-with-provisional":
            activations = deployment / "activations"
            activations.mkdir(exist_ok=True)
            activations.chmod(0o750)
            activation_record = {
                "schema_version": 1,
                "activation_id": provisional_data["activation_id"],
                "previous_release_id": CURRENT,
                "candidate_release_id": CANDIDATE,
                "activated_at": provisional_data["activated_at"],
                "backup_id": provisional_data["backup_id"],
                "migration_policy": "no-change",
            }
            activation_path = activations / f"{provisional_data['activation_id']}.json"
            activation_path.write_text(json.dumps(activation_record, separators=(",", ":")) + "\n", encoding="utf-8")
            activation_path.chmod(0o600)
        else:
            provisional.unlink()

    failed_resume = execute("e" * 32, "readiness")

    assert failed_resume.returncode == 9, (failed_resume.stderr[-8_000:], failed_resume.stdout)
    failed_payload = json.loads(failed_resume.stdout)
    assert failed_payload["changed"] is True
    assert failed_payload["changed_stages"] == ["verification"]

    success_token = "f" * 32
    if partial_state == "activation-record-with-provisional":
        failed_cleanup = execute(success_token, "provisional-cleanup")
        assert failed_cleanup.returncode == 8, (
            failed_cleanup.stderr[-8_000:],
            failed_cleanup.stdout,
        )
        cleanup_payload = json.loads(failed_cleanup.stdout)
        assert cleanup_payload["stage"] == "records"
        assert cleanup_payload["changed"] is True
        assert cleanup_payload["changed_stages"] == ["verification", "records"]
        success_token = "0" * 32

    resumed = execute(success_token, "")

    assert resumed.returncode == 0, (resumed.stderr[-8_000:], resumed.stdout)
    payload = json.loads(resumed.stdout)
    expected_activation = (
        f"activation-{success_token}"
        if partial_state == "release-record-only"
        else "activation-" + "d" * 32
    )
    assert payload["activation_id"] == expected_activation
    assert payload["changed_stages"] == ["verification", "records"]
    assert not provisional.exists()
    assert (deployment / "activations" / f"{expected_activation}.json").is_file()
    assert events.read_text(encoding="utf-8").splitlines().count("backup") == 1


def test_the_canonical_deploy_lock_excludes_a_competing_transaction_for_its_full_lifetime(
    tmp_path: Path,
) -> None:
    """Every stage of the real transaction retains the canonical flock, including its child backup."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    uploads = deployment / "uploads"
    for directory in (managed, releases, deployment, uploads, backup_root, deployment / "manifests"):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    (deployment / "manifests" / f"release-{CURRENT}.json").write_text(
        _manifest_json(CURRENT), encoding="utf-8"
    )
    (deployment / "manifests" / f"release-{CURRENT}.json").chmod(0o600)
    _record_current_lifecycle(deployment, backup_root)
    _release(releases / CURRENT, release_id=CURRENT, checksum="b" * 64)
    (managed / "current").symlink_to(releases / CURRENT)
    token = "d" * 32
    upload = uploads / f".upload-{CANDIDATE}-{token}.tar.gz"
    _archive(upload)
    upload.chmod(0o600)
    commands = tmp_path / "commands"
    commands.mkdir()
    _stubs(commands)
    backup_real = commands / "taskman-backup-real"
    (commands / "taskman-backup").rename(backup_real)
    ready_fifo = tmp_path / "backup-ready"
    gate_fifo = tmp_path / "backup-gate"
    os.mkfifo(ready_fifo)
    os.mkfifo(gate_fifo)
    _stub(
        commands / "taskman-backup",
        """#!/bin/sh
printf 'ready\\n' > "$TASKMAN_READY_FIFO"
IFS= read -r _ < "$TASKMAN_GATE_FIFO"
exec "$TASKMAN_BACKUP_REAL" "$@"
""",
    )
    lock_root = tmp_path / "canonical-lock"
    transaction = DEPLOY_TRANSACTION.replace(
        "lock_root=/var/lock/taskman;", f"lock_root={lock_root};", 1
    ).replace("record_owner_uid=0", "record_owner_uid=$(id -u)", 1)
    argv = (
        "sh", "-ceu", transaction, "taskman-full-lock-holder", str(managed), str(releases),
        str(deployment), str(backup_root), "4000", "taskman.example.test", CURRENT, CANDIDATE,
        sha256(upload.read_bytes()).hexdigest(), token, "no-change", _manifest_b64(CANDIDATE),
        str(upload), str(commands / "taskman-backup"), base64.b64encode(_STAGE_BODY.encode()).decode(),
        base64.b64encode(_ACTIVATION_BODY.encode()).decode(), "5000", "127.0.0.1", "5432",
        "taskman", "taskman_prod", "14", "6789", "0", "/etc/caddy/Caddyfile", "0.05", "0.05",
    )
    environment = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "TASKMAN_EVENTS": str(tmp_path / "events"),
        "TASKMAN_STATE": str(tmp_path / "service-state"),
        "TASKMAN_ACTIVATION_RECORD": str(deployment / "activations" / f"activation-{token}.json"),
        "TASKMAN_FAILURE": "",
        "TASKMAN_CANDIDATE": str(releases / CANDIDATE),
        "TASKMAN_DEPLOYMENT": str(deployment),
        "TASKMAN_BACKUP_ROOT": str(backup_root),
        "TASKMAN_CURRENT_RELEASE": CURRENT,
        "TASKMAN_CANDIDATE_RELEASE": CANDIDATE,
        "TASKMAN_READY_FIFO": str(ready_fifo),
        "TASKMAN_GATE_FIFO": str(gate_fifo),
        "TASKMAN_BACKUP_REAL": str(backup_real),
    }
    ready_descriptor = os.open(ready_fifo, os.O_RDONLY | os.O_NONBLOCK)
    holder = subprocess.Popen(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    try:
        readable, _, _ = select.select((ready_descriptor,), (), (), 5)
        assert readable and os.read(ready_descriptor, 32) == b"ready\n"
        contender_script = (
            "lock_root=$1; operation=releases; timeout_ms=0; owner_uid=0; lock_mode=shared\n"
            + REMOTE_LOCK_FRAMING
            + "printf unexpected\\n"
        )
        contender = subprocess.run(
            ("sh", "-ceu", contender_script, "taskman-lock-contender", str(lock_root)),
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        assert contender.returncode == 12
        assert json.loads(contender.stdout)["holder"]["operation"] == "deploy"
        with gate_fifo.open("w", encoding="utf-8") as gate:
            gate.write("release\n")
    finally:
        os.close(ready_descriptor)
    stdout, stderr = holder.communicate(timeout=10)
    assert holder.returncode == 0, (stderr, stdout)


@pytest.mark.parametrize(
    ("loose_lifecycle_record", "previous_hint"),
    [
        (False, "matching"),
        (True, "matching"),
        (False, CURRENT),
    ],
)
def test_explicit_manual_adoption_uses_the_strict_snapshot_identity_and_validated_selected_path(
    tmp_path: Path,
    loose_lifecycle_record: bool,
    previous_hint: str,
) -> None:
    """A manual release uses a derived identity and rejects loose lifecycle evidence."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    uploads = deployment / "uploads"
    for directory in (managed, releases, deployment, uploads, backup_root):
        directory.mkdir(parents=True)
        directory.chmod(0o750)
    manual = releases / "operator-baseline"
    _adoptable_release(manual)
    (managed / "current").symlink_to(manual)
    if loose_lifecycle_record:
        records = deployment / "releases"
        records.mkdir()
        records.chmod(0o750)
        loose = records / f"release-{CURRENT}.json"
        loose.write_text("{}\n", encoding="utf-8")
        loose.chmod(0o600)
    upload = uploads / f".upload-{CANDIDATE}-{'d' * 32}.tar.gz"
    _archive(upload)
    upload.chmod(0o600)
    caddy_config = tmp_path / "Caddyfile"
    caddy_config.write_text("reverse_proxy 127.0.0.1:4000\n", encoding="utf-8")
    commands = tmp_path / "commands"
    commands.mkdir()
    _stubs(commands)
    _stub(
        commands / "find",
        "#!/bin/sh\ncase \"$*\" in *taskman-*/ebin/taskman.app*) exec /usr/bin/find \"$@\";; *) exit 0;; esac\n",
    )
    _stub(
        commands / "readlink",
        "#!/bin/sh\ncase \"$*\" in */proc/123/exe*) selected=$(/usr/bin/readlink -f -- \"$TASKMAN_MANAGED/current\") || exit; printf '%s/bin/server\\n' \"$selected\";; *) exec /usr/bin/readlink \"$@\";; esac\n",
    )
    state = tmp_path / "service-state"
    state.write_text("active\n", encoding="utf-8")
    events = tmp_path / "events"
    baseline_sha = sha256(b"# baseline\n").hexdigest()
    manual_content_sha = sha256(b"entries\0contents\0").hexdigest()
    manual_release_id = (
        f"0.2.0-{manual_content_sha[:12]}-ubuntu26.04-amd64-otp27.3.4.6"
    )
    transaction_previous_hint = (
        manual_release_id if previous_hint == "matching" else previous_hint
    )
    manual_migrations_b64 = base64.b64encode(
        json.dumps(
            [
                {
                    "filename": "20260905120000_initial.exs",
                    "sha256": baseline_sha,
                }
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")
    manifest = _manifest_b64(
        CANDIDATE,
        [{"filename": "20260905120000_initial.exs", "sha256": baseline_sha}],
    )
    transaction = DEPLOY_TRANSACTION.replace(
        "lock_root=/var/lock/taskman;", f"lock_root={tmp_path / 'canonical-lock'};", 1
    )

    completed = subprocess.run(
        (
            "sh", "-ceu", transaction, "taskman-manual-adoption-test", str(managed), str(releases),
            str(deployment), str(backup_root), "4000", "taskman.example.test",
            transaction_previous_hint, CANDIDATE,
            sha256(upload.read_bytes()).hexdigest(), "d" * 32, "no-change", manifest, str(upload),
            str(commands / "taskman-backup"), base64.b64encode(_STAGE_BODY.encode()).decode(),
            base64.b64encode(_ACTIVATION_BODY.encode()).decode(), "5000", "127.0.0.1", "5432",
            "taskman", "taskman_prod", "14", "6789", "1", str(caddy_config), "0.05", "0.05",
            manual_release_id, manual_content_sha, manual_migrations_b64,
        ),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TASKMAN_EVENTS": str(events),
            "TASKMAN_STATE": str(state),
            "TASKMAN_ACTIVATION_RECORD": str(deployment / "activations" / ("activation-" + "d" * 32 + ".json")),
                "TASKMAN_FAILURE": "",
                "TASKMAN_CANDIDATE": str(releases / CANDIDATE),
                "TASKMAN_MANAGED": str(managed),
                "TASKMAN_DEPLOYMENT": str(deployment),
                "TASKMAN_BACKUP_ROOT": str(backup_root),
                "TASKMAN_CURRENT_RELEASE": CURRENT,
                "TASKMAN_CANDIDATE_RELEASE": CANDIDATE,
        },
        check=False,
    )

    if loose_lifecycle_record or previous_hint != "matching":
        assert completed.returncode == 10
        assert not (deployment / "adoptions").exists()
        return
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert "adoption" in payload["changed_stages"]
    marker = next((deployment / "adoptions").glob("adoption-*.json"))
    adopted = json.loads(marker.read_text(encoding="utf-8"))
    assert adopted["release_path"] == str(manual)
    final_edge = json.loads(
        (deployment / "activations" / ("activation-" + "d" * 32 + ".json")).read_text(encoding="utf-8")
    )
    assert final_edge["previous_release_id"] == adopted["release_id"]


def _release(path: Path, *, release_id: str | None = None, checksum: str | None = None) -> None:
    (path / "bin").mkdir(parents=True)
    for name in ("server", "migrate"):
        binary = path / "bin" / name
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o750)
    (path / "lib").mkdir()
    (path / "releases").mkdir()
    marker = {} if release_id is None or checksum is None else {
        "schema_version": 1,
        "release_id": release_id,
        "artifact_sha256": checksum,
    }
    (path / ".taskman-release.json").write_text(
        json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def _record_current_lifecycle(deployment: Path, backup_root: Path, *, include_backup: bool = False) -> None:
    """Write the minimum complete lifecycle chain for the test's direct current release."""

    releases = deployment / "releases"
    activations = deployment / "activations"
    for directory in (releases, activations):
        directory.mkdir(exist_ok=True)
        directory.chmod(0o750)
    timestamp = "2026-09-05T11:59:59Z"
    (releases / f"release-{CURRENT}.json").write_text(
        f'{{"schema_version":1,"release_id":"{CURRENT}","artifact_sha256":"{"b" * 64}","installed_at":"{timestamp}","activated_at":"{timestamp}","previous_release_id":null,"backup_id":null,"migration_policy":"no-change"}}\n',
        encoding="utf-8",
    )
    (activations / ("activation-" + "b" * 32 + ".json")).write_text(
        f'{{"schema_version":1,"activation_id":"activation-{"b" * 32}","previous_release_id":null,"candidate_release_id":"{CURRENT}","activated_at":"{timestamp}","backup_id":null,"migration_policy":"no-change"}}\n',
        encoding="utf-8",
    )
    for record in releases.iterdir():
        record.chmod(0o600)
    for record in activations.iterdir():
        record.chmod(0o600)
    if include_backup:
        backups = deployment / "backups"
        backups.mkdir(exist_ok=True)
        backups.chmod(0o750)
        (backups / ("backup-" + "a" * 32 + ".json")).write_text(
            f'{{"schema_version":1,"backup_id":"backup-{"a" * 32}","created_at":"{timestamp}","size_bytes":1,"source_database_size_bytes":1048576,"database":"taskman_prod","current_release_id":"{CURRENT}","candidate_release_id":"{CANDIDATE}","reason":"pre-deploy","validated":true,"dump_path":"{backup_root}/backup.dump"}}\n',
            encoding="utf-8",
        )
        (backups / ("backup-" + "a" * 32 + ".json")).chmod(0o600)


def _adoptable_release(path: Path) -> None:
    _release(path)
    app = path / "lib" / "taskman-0.2.0" / "ebin" / "taskman.app"
    app.parent.mkdir(parents=True)
    app.write_text('{application,taskman,[{vsn,"0.2.0"}]}.\n', encoding="utf-8")
    migrations = path / "lib" / "taskman-0.2.0" / "priv" / "repo" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "20260905120000_initial.exs").write_text("# baseline\n", encoding="utf-8")


def _archive(path: Path) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, mode in (("taskman/bin/server", 0o750), ("taskman/bin/migrate", 0o750), ("taskman/lib/release", 0o640), ("taskman/releases/start", 0o640)):
            entry = tarfile.TarInfo(name)
            entry.mode = mode
            entry.size = 0
            archive.addfile(entry)


def _stubs(root: Path) -> None:
    _stub(root / "stat", """#!/bin/sh
case "$*" in
  *%s*) /usr/bin/stat "$@" ;;
  *%u:%a*) printf '0:600\\n' ;;
  *%u*) printf '0\\n' ;;
  *%a*) printf '750\\n' ;;
  *) exit 64 ;;
esac
""")
    _stub(root / "find", "#!/bin/sh\nexit 0\n")
    _stub(root / "ln", """#!/bin/sh
last=
for value in "$@"; do last=$value; done
case "${TASKMAN_FAILURE:-}:$last" in
  manifest-publication:*/manifests/*|release-publication:*/releases/*|activation-publication:*/activations/*)
    marker="$TASKMAN_PUBLICATION_MARKER-${TASKMAN_FAILURE}"
    if test ! -e "$marker"; then
      : > "$marker"
      directory=${last%/*}
      printf 'foreign\\n' > "$directory/.foreign-publication.tmp"
      exit 1
    fi
    ;;
esac
exec /usr/bin/ln "$@"
""")
    _stub(root / "df", """#!/bin/sh
if test "$TASKMAN_FAILURE" = capacity; then
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/test 1 1 0 100%% /tmp\\n'
else
  /usr/bin/df "$@"
fi
""")
    _stub(root / "chown", "#!/bin/sh\nexit 0\n")
    _stub(root / "rm", """#!/bin/sh
last=
for value in "$@"; do last=$value; done
if test "${TASKMAN_FAILURE:-}" = provisional-cleanup; then
  case "$last" in
    */provisionals/activation-*.json)
      marker="$TASKMAN_PUBLICATION_MARKER-provisional-cleanup"
      if test ! -e "$marker"; then : > "$marker"; exit 1; fi
      ;;
  esac
fi
exec /usr/bin/rm "$@"
""")
    _stub(root / "getent", "#!/bin/sh\nexit 0\n")
    _stub(root / "systemctl", """#!/bin/sh
case "$1" in
  stop) printf 'stopped\\n' > "$TASKMAN_STATE"; printf 'stop\\n' >> "$TASKMAN_EVENTS" ;;
  is-active) case "$3" in taskman.service) if test "$(cat "$TASKMAN_STATE")" = stopped; then printf 'inactive\\n' >> "$TASKMAN_EVENTS"; exit 3; else printf 'verify-taskman\\n' >> "$TASKMAN_EVENTS"; fi ;; caddy.service) printf 'verify-caddy\\n' >> "$TASKMAN_EVENTS" ;; esac ;;
  show) test "$TASKMAN_FAILURE" != pid || { printf 'active\\n0\\n'; exit 0; }; printf 'verify-pid\\n' >> "$TASKMAN_EVENTS"; case "$*" in *ActiveState*) printf 'active\\n123\\n' ;; *) printf '123\\n' ;; esac ;;
  start) test "$TASKMAN_FAILURE" != start || exit 1; printf 'active\\n' > "$TASKMAN_STATE"; printf 'start\\n' >> "$TASKMAN_EVENTS" ;;
  *) exit 64 ;;
esac
""")
    _stub(root / "readlink", """#!/bin/sh
case "$*" in
  */proc/123/exe*) printf '%s\\n' "$TASKMAN_CANDIDATE/bin/server" ;;
  *) /usr/bin/readlink "$@" ;;
esac
""")
    _stub(root / "ss", """#!/bin/sh
    if test "$TASKMAN_FAILURE" = listeners; then
      printf '%s\\n' 'LISTEN 0 1 0.0.0.0:4000 0.0.0.0:*'
    elif test "$TASKMAN_FAILURE" = malformed-listeners; then
      printf '%s\\n' 'LISTEN malformed'
else
  case "$*" in
    *:4000*) printf '%s\\n' 'LISTEN 0 1 127.0.0.1:4000 0.0.0.0:*' ;;
    *:6789*) printf '%s\\n' 'LISTEN 0 1 127.0.0.1:6789 0.0.0.0:*' ;;
    *:5432*) printf '%s\\n' 'LISTEN 0 1 127.0.0.1:5432 0.0.0.0:*' ;;
    *:4369*) : ;;
    *) printf '%s\\n' 'LISTEN 0 1 127.0.0.1:4000 0.0.0.0:*' 'LISTEN 0 1 127.0.0.1:6789 0.0.0.0:*' 'LISTEN 0 1 127.0.0.1:5432 0.0.0.0:*' ;;
  esac
fi
""")
    _stub(root / "journalctl", """#!/bin/sh
test "$TASKMAN_FAILURE" != journal || { printf '%s\\n' 'failed to start'; exit 0; }
printf '%s\\n' 'startup complete'
""")
    _stub(root / "psql", "#!/bin/sh\nprintf '%s\\n' 1048576\n")
    _stub(root / "systemd-run", """#!/bin/sh
test "$TASKMAN_FAILURE" != migration || exit 1
printf 'migrate\\n' >> "$TASKMAN_EVENTS"
""")
    _stub(root / "curl", """#!/bin/sh
test ! -e "$TASKMAN_ACTIVATION_RECORD" || exit 64
test "$TASKMAN_FAILURE" != readiness || exit 1
if test "$TASKMAN_FAILURE" = deadline; then
  printf 'HTTP/1.1 503 Service Unavailable\\r\\nCache-Control: no-store\\r\\n\\r\\nunavailable'
  exit 0
fi
case "$*" in
  *127.0.0.1*) printf 'verify-local\\n' >> "$TASKMAN_EVENTS" ;;
  *) printf 'verify-public\\n' >> "$TASKMAN_EVENTS" ;;
esac
case "$*" in *--dump-header*) headers=1 ;; *) headers=0 ;; esac
test "$headers" = 1 || { printf 'ready'; exit 0; }
case "$*" in
  *127.0.0.1*)
    if test "$TASKMAN_FAILURE" = duplicate-cache-control; then
      printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\nCache-Control: no-store\\r\\n\\r\\nready'
    elif test "$TASKMAN_FAILURE" = wrong-body; then
      printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\n\\r\\nready-extra'
    else
      printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\n\\r\\nready'
    fi
    ;;
  *)
    if test "$TASKMAN_FAILURE" = hsts; then
      printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\n\\r\\nready'
    elif test "$TASKMAN_FAILURE" = duplicate-hsts; then
      printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\nStrict-Transport-Security: max-age=1\\r\\nStrict-Transport-Security: max-age=2\\r\\n\\r\\nready'
    else
      printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\nStrict-Transport-Security: max-age=31536000\\r\\n\\r\\nready'
    fi
    ;;
esac
""")
    _stub(root / "taskman-backup", """#!/bin/sh
test "${TASKMAN_FAILURE:-}" != backup || exit 6
current=
candidate=
while test "$#" -gt 0; do
  case "$1" in
    --current-release) current=$2; shift 2 ;;
    --candidate-release) candidate=$2; shift 2 ;;
    *) shift ;;
  esac
done
printf 'backup\\n' >> "$TASKMAN_EVENTS"
current_json=null
test -z "$current" || current_json="\\"$current\\""
if test -n "${TASKMAN_DEPLOYMENT:-}"; then
  mkdir -p "$TASKMAN_DEPLOYMENT/backups"
  printf 'validated backup\\n' > "$TASKMAN_BACKUP_ROOT/backup.dump"
  chmod 600 "$TASKMAN_BACKUP_ROOT/backup.dump"
  printf '%s\\n' "{\\"schema_version\\":1,\\"backup_id\\":\\"backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\",\\"created_at\\":\\"2026-09-05T12:00:00Z\\",\\"size_bytes\\":1,\\"source_database_size_bytes\\":1048576,\\"database\\":\\"taskman_prod\\",\\"current_release_id\\":$current_json,\\"candidate_release_id\\":\\"$candidate\\",\\"reason\\":\\"pre-deploy\\",\\"validated\\":true,\\"dump_path\\":\\"$TASKMAN_BACKUP_ROOT/backup.dump\\"}" > "$TASKMAN_DEPLOYMENT/backups/backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
  chmod 600 "$TASKMAN_DEPLOYMENT/backups/backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
fi
printf '%s\\n' "{\\"schema_version\\":1,\\"backup_id\\":\\"backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\",\\"created_at\\":\\"2026-09-05T12:00:00Z\\",\\"size_bytes\\":1,\\"source_database_size_bytes\\":1048576,\\"database\\":\\"taskman_prod\\",\\"current_release_id\\":$current_json,\\"candidate_release_id\\":\\"$candidate\\",\\"reason\\":\\"pre-deploy\\",\\"validated\\":true,\\"dump_path\\":\\"$TASKMAN_BACKUP_ROOT/backup.dump\\"}"
""")
    _stub(root / "install", """#!/bin/sh
last=
for value in "$@"; do last=$value; done
mkdir -p -- "$last"
chmod 750 -- "$last"
""")


def _stub(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
