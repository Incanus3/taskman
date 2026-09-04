"""Compatibility-gated, history-preserving release rollback."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.remote import CommandResult
from taskman_ops.releases.records import AdoptionRecord, ActivationRecord, LifecycleRecords, ReleaseRecord, RemoteLifecycleStore
from taskman_ops.verification import CheckStatus, VerificationCheck, VerificationReport
from taskman_ops.workflows.rollback import ROLLBACK_TRANSACTION, assess_rollback, rollback, run_locked_rollback


RELEASE_A = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_B = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_C = "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_D = "0.2.0-dddddddddddd-ubuntu26.04-amd64-otp27.3.4.6"
FIRST = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
SECOND = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
THIRD = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.validate_operational_preflight",
        lambda *_args: object(),
    )


def _release(
    release_id: str,
    installed_at: datetime,
    *,
    activated_at: datetime | None = None,
    previous: str | None = None,
    policy: str = "no-change",
) -> ReleaseRecord:
    return ReleaseRecord(1, release_id, "a" * 64, installed_at, activated_at, previous, None, policy)  # type: ignore[arg-type]


def _activation(
    activation_id: str,
    previous: str | None,
    candidate: str,
    at: datetime,
    policy: str = "no-change",
) -> ActivationRecord:
    return ActivationRecord(1, activation_id, previous, candidate, at, None, policy)  # type: ignore[arg-type]


def _records(*, policies: tuple[str, str] = ("no-change", "backward-compatible")) -> LifecycleRecords:
    return LifecycleRecords(
        releases=(
            _release(RELEASE_A, FIRST, activated_at=FIRST),
            _release(RELEASE_B, SECOND, activated_at=SECOND, previous=RELEASE_A, policy=policies[0]),
            _release(RELEASE_C, THIRD, activated_at=THIRD, previous=RELEASE_B, policy=policies[1]),
        ),
        activations=(
            _activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_A, FIRST),
            _activation("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_A, RELEASE_B, SECOND, policies[0]),
            _activation("activation-cccccccccccccccccccccccccccccccc", RELEASE_B, RELEASE_C, THIRD, policies[1]),
        ),
        backups=(),
        adoptions=(),
        warnings=(),
    )


def test_assess_rollback_accepts_the_complete_compatible_current_to_target_chain() -> None:
    """Removing a compatible intermediate edge must make this rollback ineligible."""

    plan = assess_rollback(_records(), RELEASE_C, RELEASE_A)

    assert plan.current_release_id == RELEASE_C
    assert plan.target_release_id == RELEASE_A
    assert plan.activation_ids == (
        "activation-cccccccccccccccccccccccccccccccc",
        "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    assert plan.confirmation_fingerprint == (
        "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
        "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
    )


def test_assess_rollback_accepts_a_direct_compatible_edge() -> None:
    """A valid direct predecessor remains eligible without manufacturing an intermediate edge."""

    plan = assess_rollback(_records(), RELEASE_C, RELEASE_B)

    assert plan.activation_ids == ("activation-cccccccccccccccccccccccccccccccc",)


def test_assess_rollback_refuses_a_current_identifier_or_history_that_is_not_authoritative() -> None:
    """Treating a stale current or a skipped edge as a compatible path would select arbitrary code."""

    records = _records()
    incomplete = LifecycleRecords(
        releases=records.releases,
        activations=(records.activations[0], records.activations[2]),
        backups=(),
        adoptions=(),
        warnings=(),
    )

    for candidate in (RELEASE_B, RELEASE_C):
        with pytest.raises(OpsError) as raised:
            assess_rollback(incomplete if candidate == RELEASE_C else records, candidate, RELEASE_A)

        assert raised.value.status is ExitStatus.SAFETY


@pytest.mark.parametrize(
    ("target", "records", "reason"),
    [
        (RELEASE_D, _records(), "target release is not installed"),
        (RELEASE_C, _records(), "target release is already current"),
        (RELEASE_A, _records(policies=("restore-required", "backward-compatible")), "requires database restore"),
    ],
)
def test_assess_rollback_refuses_an_unknown_current_or_restore_required_target_path(
    target: str, records: LifecycleRecords, reason: str
) -> None:
    """Dropping the safety gate would allow a target with no code-only recovery path."""

    with pytest.raises(OpsError) as raised:
        assess_rollback(records, RELEASE_C, target)

    assert raised.value.status is ExitStatus.SAFETY
    assert reason in raised.value.message


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@example.test",
        }
    )


def _manifest(release_id: str, source: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": source * 40,
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


class _SnapshotRemote:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((argv, kwargs))
        return CommandResult(0, json.dumps(self.snapshot))


def _remote_store() -> tuple[_SnapshotRemote, RemoteLifecycleStore]:
    records = _records()
    remote = _SnapshotRemote(
        {
            "schema_version": 1,
            "records": {
                "releases": [record.to_mapping() for record in records.releases],
                "activations": [record.to_mapping() for record in records.activations],
                "backups": [],
                "adoptions": [],
            },
            "current_target": f"/opt/taskman/releases/{RELEASE_C}",
            "manifests": {
                RELEASE_A: _manifest(RELEASE_A, "a"),
                RELEASE_B: _manifest(RELEASE_B, "b"),
                RELEASE_C: _manifest(RELEASE_C, "c"),
            },
            "dump_states": {},
            "warnings": [],
        }
    )
    return remote, RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )


def _adopted_target_store() -> tuple[_SnapshotRemote, RemoteLifecycleStore, PurePosixPath]:
    """Build one compatible direct-to-adopted rollback baseline."""

    adopted_path = PurePosixPath("/opt/taskman/releases/operator-chosen-baseline")
    adopted = AdoptionRecord(
        1,
        RELEASE_A,
        FIRST,
        adopted_path,
        "a" * 64,
        "0.2.0",
        "unknown",
        "unknown",
        (),
    )
    records = LifecycleRecords(
        releases=(
            ReleaseRecord(1, RELEASE_A, None, FIRST, FIRST, None, None, "adopted"),
            _release(RELEASE_B, SECOND, activated_at=SECOND, previous=RELEASE_A),
        ),
        activations=(
            _activation("activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_A, FIRST, "adopted"),
            _activation("activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_A, RELEASE_B, SECOND),
        ),
        backups=(),
        adoptions=(adopted,),
        warnings=(),
    )
    remote = _SnapshotRemote(
        {
            "schema_version": 1,
            "records": {
                "releases": [record.to_mapping() for record in records.releases],
                "activations": [record.to_mapping() for record in records.activations],
                "backups": [],
                "adoptions": [adopted.to_mapping()],
            },
            "current_target": f"/opt/taskman/releases/{RELEASE_B}",
            "manifests": {RELEASE_B: _manifest(RELEASE_B, "b")},
            "dump_states": {},
            "warnings": [],
        }
    )
    return (
        remote,
        RemoteLifecycleStore(
            remote,
            PurePosixPath("/opt/taskman/deployments"),
            PurePosixPath("/opt/taskman"),
            PurePosixPath("/opt/taskman/releases"),
            PurePosixPath("/var/backups/taskman"),
        ),
        adopted_path,
    )


def test_rollback_requires_exact_confirmation_before_acquiring_the_exclusive_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting a rollback before rejection of the exact plan would change a host without consent."""

    remote, store = _remote_store()
    confirmed: list[dict[str, object]] = []

    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.run_locked_rollback",
        lambda *_args, **_kwargs: pytest.fail("cancelled rollback acquired the exclusive transaction"),
    )

    result = rollback(
        remote,
        _config(),
        RELEASE_A,
        lifecycle_store=store,
        confirm=lambda plan: confirmed.append(dict(plan)) or False,
    )

    assert result.changed is False
    assert result.stage == "confirmation-cancelled"
    assert result.facts["previous_release_id"] == RELEASE_C
    assert result.facts["target_release_id"] == RELEASE_A
    assert confirmed == [
        {
            "current_release_id": RELEASE_C,
            "target_release_id": RELEASE_A,
            "activation_ids": (
                "activation-cccccccccccccccccccccccccccccccc",
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ),
            "confirmation_fingerprint": (
                "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
                ),
                "target_release_path": f"/opt/taskman/releases/{RELEASE_A}",
                "target_authority": "direct",
                "planned_backup": True,
            "services_affected": ("taskman.service",),
        }
    ]
    assert len(remote.calls) == 1


def test_rollback_dry_run_reports_the_exact_plan_without_prompting_or_locking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The common CLI dry-run contract must not turn a rollback request into a host mutation."""

    from taskman_ops.workflows.operational_preflight import (
        validate_operational_preflight as validate_real_preflight,
    )

    remote, store = _remote_store()
    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.validate_operational_preflight",
        lambda actual_remote, actual_config: validate_real_preflight(
            actual_remote,
            actual_config,
            host_validator=lambda *_args: object(),
        ),
    )
    result = rollback(
        remote,
        _config(),
        RELEASE_A,
        lifecycle_store=store,
        dry_run=True,
        confirm=lambda _plan: pytest.fail("dry-run requested confirmation"),
    )

    assert result.stage == "planned"
    assert result.changed is False
    assert result.facts["previous_release_id"] == RELEASE_C
    assert result.facts["target_release_id"] == RELEASE_A
    assert len(remote.calls) == 3
    assert "/etc/taskman/taskman.env" in remote.calls[0][0]
    assert "pg_database_size" in remote.calls[1][0][2]
    assert remote.calls[2][0][3] == "taskman-lifecycle-snapshot"


def test_rollback_refuses_a_current_selection_outside_the_managed_release_root() -> None:
    """An external symlink must never be treated as an installed rollback baseline."""

    remote, store = _remote_store()
    remote.snapshot["current_target"] = f"/tmp/{RELEASE_C}"

    result = rollback(
        remote,
        _config(),
        RELEASE_A,
        lifecycle_store=store,
        confirm=lambda _plan: pytest.fail("unsafe selection reached confirmation"),
    )

    assert result.stage == "safety-refused"
    assert result.exit_status is ExitStatus.SAFETY
    assert result.changed is False


def test_rollback_refuses_an_incomplete_target_before_confirmation() -> None:
    """A historical edge cannot revive a target whose authoritative manifest is missing."""

    remote, store = _remote_store()
    manifests = remote.snapshot["manifests"]
    assert isinstance(manifests, dict)
    del manifests[RELEASE_A]

    result = rollback(
        remote,
        _config(),
        RELEASE_A,
        lifecycle_store=store,
        confirm=lambda _plan: pytest.fail("incomplete target reached confirmation"),
    )

    assert result.stage == "safety-refused"
    assert result.exit_status is ExitStatus.SAFETY
    assert result.facts["recovery_commands"] == (
        "systemctl status taskman.service",
        "readlink -f /opt/taskman/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
    )


def test_rollback_accepts_a_strict_adopted_target_using_its_recorded_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adopted baseline is selected by its strict marker authority, never by its release ID path."""

    remote, store, adopted_path = _adopted_target_store()
    seen: list[dict[str, object]] = []

    def locked(*_args: object, **kwargs: object) -> dict[str, object]:
        seen.append(kwargs)
        return {
            "stage": "rolled-back",
            "previous_release_id": RELEASE_B,
            "target_release_id": RELEASE_A,
            "selected_release_id": RELEASE_A,
            "backup_id": "backup-dddddddddddddddddddddddddddddddd",
            "activation_id": "activation-dddddddddddddddddddddddddddddddd",
            "service_state": "active",
            "database_state": "unchanged",
            "history_state": "activation-appended",
            "activation_recorded": True,
            "changed": True,
            "changed_stages": ("backup", "stop", "selection", "start", "verification", "records"),
            "warnings": (),
            "recovery_commands": (),
            "residue_paths": (),
            "verification": None,
        }

    monkeypatch.setattr("taskman_ops.workflows.rollback.run_locked_rollback", locked)

    result = rollback(remote, _config(), RELEASE_A, lifecycle_store=store, confirm=lambda _plan: True)

    assert result.stage == "rolled-back"
    assert seen[0]["target_release_path"] == adopted_path
    assert seen[0]["target_authority"] == "adopted"
    assert seen[0]["target_checksum"] is None
    assert seen[0]["confirmation_fingerprint"] == "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"


def test_rollback_runs_the_confirmed_current_and_target_in_one_locked_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing the target or issuing separate backup/selection calls must fail this workflow."""

    remote, store = _remote_store()
    seen: list[dict[str, object]] = []

    def locked(
        actual_remote: object,
        _config: EnvironmentConfig,
        actual_store: RemoteLifecycleStore,
        **kwargs: object,
    ) -> dict[str, object]:
        seen.append({"remote": actual_remote, "store": actual_store, **kwargs})
        return {
            "stage": "rolled-back",
            "previous_release_id": RELEASE_C,
            "target_release_id": RELEASE_A,
            "selected_release_id": RELEASE_A,
            "backup_id": "backup-dddddddddddddddddddddddddddddddd",
            "activation_id": "activation-dddddddddddddddddddddddddddddddd",
            "service_state": "unknown",
            "database_state": "unchanged",
            "history_state": "activation-appended",
            "activation_recorded": True,
            "changed": True,
            "changed_stages": ("backup", "stop", "selection", "start", "verification", "records"),
            "warnings": (),
            "recovery_commands": (),
            "residue_paths": (),
            "verification": None,
        }

    monkeypatch.setattr("taskman_ops.workflows.rollback.run_locked_rollback", locked)

    result = rollback(
        remote,
        _config(),
        RELEASE_A,
        lifecycle_store=store,
        confirm=lambda _plan: True,
    )

    assert result.command == "rollback"
    assert result.stage == "rolled-back"
    assert result.changed is True
    assert result.facts["backup_id"] == "backup-dddddddddddddddddddddddddddddddd"
    assert result.facts["history_state"] == "activation-appended"
    assert seen == [
        {
            "remote": remote,
            "store": store,
            "current_release_id": RELEASE_C,
            "target_release_id": RELEASE_A,
            "target_release_path": PurePosixPath(f"/opt/taskman/releases/{RELEASE_A}"),
            "target_authority": "direct",
            "target_checksum": "a" * 64,
            "confirmation_fingerprint": (
                "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
            ),
            "lock_timeout_seconds": 5,
        }
    ]


def _successful_verification(release_id: str) -> dict[str, object]:
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
    return VerificationReport(
        ExitStatus.OK,
        release_id,
        release_id,
        tuple(VerificationCheck(name, CheckStatus.PASSED, f"{name} passed") for name in names),
        None,
    ).to_mapping()


def test_locked_rollback_invokes_one_inherited_lock_transaction_without_migrations() -> None:
    """Separating backup from selection or invoking the target migrator would violate code-only rollback."""

    payload = {
        "stage": "rolled-back",
        "previous_release_id": RELEASE_C,
        "target_release_id": RELEASE_A,
        "selected_release_id": RELEASE_A,
        "backup_id": "backup-dddddddddddddddddddddddddddddddd",
        "activation_id": "activation-dddddddddddddddddddddddddddddddd",
        "service_state": "active",
        "database_state": "unchanged",
        "history_state": "activation-appended",
        "activation_recorded": True,
        "changed": True,
        "changed_stages": ["backup", "stop", "selection", "start", "verification", "records"],
        "warnings": [],
        "recovery_commands": [
            "systemctl status taskman.service",
            "readlink -f /opt/taskman/current",
            "journalctl --no-pager --unit taskman.service --lines=100",
        ],
        "residue_paths": [],
        "verification": _successful_verification(RELEASE_A),
    }

    class _TransactionRemote:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
            self.calls.append((argv, kwargs))
            return CommandResult(0, json.dumps(payload))

    remote = _TransactionRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    evidence = run_locked_rollback(
        remote,
        _config(),
        store,
        current_release_id=RELEASE_C,
        target_release_id=RELEASE_A,
        target_release_path=PurePosixPath(f"/opt/taskman/releases/{RELEASE_A}"),
        target_authority="direct",
        target_checksum="a" * 64,
        confirmation_fingerprint=(
            "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
            "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
        ),
        lock_timeout_seconds=5,
        operation_token="d" * 32,
    )

    assert evidence == payload
    assert len(remote.calls) == 1
    argv, kwargs = remote.calls[0]
    assert argv[:3] == ("sh", "-ceu", argv[2])
    assert "--already-locked" in argv[2]
    assert "systemd-run" not in argv[2]
    assert kwargs == {"sudo": True, "stdin": None, "sensitive": False}


def _failure_payload(
    *,
    stage: str,
    backup_id: str | None,
    selected_release_id: str,
    service_state: str,
    changed_stages: list[str],
    verification: object = None,
) -> dict[str, object]:
    return {
        "stage": stage,
        "previous_release_id": RELEASE_C,
        "target_release_id": RELEASE_A,
        "selected_release_id": selected_release_id,
        "backup_id": backup_id,
        "activation_id": None,
        "service_state": service_state,
        "database_state": "unchanged",
        "history_state": "unchanged",
        "activation_recorded": False,
        "changed": bool(changed_stages),
        "changed_stages": changed_stages,
        "warnings": [],
        "recovery_commands": [
            "systemctl status taskman.service",
            "readlink -f /opt/taskman/current",
            "journalctl --no-pager --unit taskman.service --lines=100",
        ],
        "residue_paths": [],
        "verification": verification,
    }


def _failed_verification(release_id: str) -> dict[str, object]:
    names = (
        "taskman-service",
        "release-identity",
        "caddy-service",
        "listener-topology",
        "startup-journal",
        "local-readiness",
    )
    return VerificationReport(
        ExitStatus.READINESS,
        release_id,
        release_id,
        tuple(
            VerificationCheck(
                name,
                CheckStatus.FAILED if name == "local-readiness" else CheckStatus.PASSED,
                f"{name} failed" if name == "local-readiness" else f"{name} passed",
            )
            for name in names
        ),
        "inspect the fixed verification summaries and correct the reported host state before retrying",
    ).to_mapping()


@pytest.mark.parametrize(
    ("returncode", "payload", "expected_stage"),
    [
        (
            ExitStatus.BACKUP,
            _failure_payload(
                stage="backup",
                backup_id=None,
                selected_release_id="unknown",
                service_state="unknown",
                changed_stages=[],
            ),
            "backup",
        ),
        (
            ExitStatus.RELEASE,
            _failure_payload(
                stage="start",
                backup_id="backup-dddddddddddddddddddddddddddddddd",
                selected_release_id=RELEASE_A,
                service_state="unknown",
                changed_stages=["backup", "stop", "selection", "start"],
            ),
            "start",
        ),
        (
            ExitStatus.READINESS,
            _failure_payload(
                stage="verification",
                backup_id="backup-dddddddddddddddddddddddddddddddd",
                selected_release_id=RELEASE_A,
                service_state="unknown",
                changed_stages=["backup", "stop", "selection", "start", "verification"],
                verification=_failed_verification(RELEASE_A),
            ),
            "verification",
        ),
    ],
)
def test_locked_rollback_preserves_transaction_failure_evidence(
    returncode: ExitStatus,
    payload: dict[str, object],
    expected_stage: str,
) -> None:
    """A failed transaction exposes its observed selection, service, backup, and history state."""

    class _FailureRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(returncode, json.dumps(payload))

    remote = _FailureRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    with pytest.raises(OpsError) as raised:
        run_locked_rollback(
            remote,
            _config(),
            store,
            current_release_id=RELEASE_C,
            target_release_id=RELEASE_A,
            target_release_path=PurePosixPath(f"/opt/taskman/releases/{RELEASE_A}"),
            target_authority="direct",
            target_checksum="a" * 64,
            confirmation_fingerprint=(
                "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
            ),
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )

    error = raised.value
    assert error.status is returncode
    assert error.stage == expected_stage
    assert error.backup_id == payload["backup_id"]
    assert error.selected_release_id == payload["selected_release_id"]
    assert error.service_state == payload["service_state"]
    assert error.database_state == "unchanged"
    assert error.history_state == "unchanged"
    assert error.activation_recorded is False
    assert error.changed_stages == payload["changed_stages"]
    assert error.recovery_commands == payload["recovery_commands"]
    if returncode is ExitStatus.RELEASE:
        assert "do not start another release" in (error.next_action or "")


def test_locked_rollback_preserves_unknown_state_when_the_inherited_backup_refuses_safety() -> None:
    """A backup-side lifecycle contradiction is evidence of unknown state, not malformed output."""

    payload = _failure_payload(
        stage="backup",
        backup_id=None,
        selected_release_id="unknown",
        service_state="unknown",
        changed_stages=[],
    )
    payload["database_state"] = "unknown"
    payload["history_state"] = "unknown"

    class _FailureRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(ExitStatus.SAFETY, json.dumps(payload))

    remote = _FailureRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    with pytest.raises(OpsError) as raised:
        run_locked_rollback(
            remote,
            _config(),
            store,
            current_release_id=RELEASE_C,
            target_release_id=RELEASE_A,
            target_release_path=PurePosixPath(f"/opt/taskman/releases/{RELEASE_A}"),
            target_authority="direct",
            target_checksum="a" * 64,
            confirmation_fingerprint=(
                "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
            ),
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )

    error = raised.value
    assert error.status is ExitStatus.SAFETY
    assert error.stage == "backup"
    assert error.selected_release_id == "unknown"
    assert error.service_state == "unknown"
    assert error.database_state == "unknown"
    assert error.history_state == "unknown"


def test_locked_rollback_preserves_unobserved_preflight_state_after_a_stale_confirmation_refusal() -> None:
    """The controller must retain a stale in-lock refusal rather than invent an active service or healthy database."""

    payload = _failure_payload(
        stage="preflight",
        backup_id=None,
        selected_release_id="unknown",
        service_state="unknown",
        changed_stages=[],
    )
    payload["database_state"] = "unknown"
    payload["history_state"] = "unknown"

    class _FailureRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(ExitStatus.SAFETY, json.dumps(payload))

    remote = _FailureRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    with pytest.raises(OpsError) as raised:
        run_locked_rollback(
            remote,
            _config(),
            store,
            current_release_id=RELEASE_C,
            target_release_id=RELEASE_A,
            target_release_path=PurePosixPath(f"/opt/taskman/releases/{RELEASE_A}"),
            target_authority="direct",
            target_checksum="a" * 64,
            confirmation_fingerprint=(
                "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
            ),
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )

    error = raised.value
    assert error.status is ExitStatus.SAFETY
    assert error.stage == "preflight"
    assert error.service_state == error.database_state == error.history_state == "unknown"


def test_locked_rollback_rejects_failure_evidence_with_an_impossible_completed_prefix() -> None:
    """A made-up prefix cannot obscure which irreversible rollback stages actually ran."""

    payload = _failure_payload(
        stage="start",
        backup_id="backup-dddddddddddddddddddddddddddddddd",
        selected_release_id=RELEASE_A,
        service_state="unknown",
        changed_stages=["backup"],
    )

    class _FailureRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(ExitStatus.RELEASE, json.dumps(payload))

    remote = _FailureRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    with pytest.raises(OpsError) as raised:
        run_locked_rollback(
            remote,
            _config(),
            store,
            current_release_id=RELEASE_C,
            target_release_id=RELEASE_A,
            target_release_path=PurePosixPath(f"/opt/taskman/releases/{RELEASE_A}"),
            target_authority="direct",
            target_checksum="a" * 64,
            confirmation_fingerprint=(
                "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
            ),
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )

    assert raised.value.status is ExitStatus.SAFETY


def test_locked_rollback_refuses_a_readiness_exit_without_verification_evidence() -> None:
    """A readiness status alone must not replace the verifier's complete failure report."""

    payload = _failure_payload(
        stage="verification",
        backup_id="backup-dddddddddddddddddddddddddddddddd",
        selected_release_id=RELEASE_A,
        service_state="unknown",
        changed_stages=["backup", "stop", "selection", "start", "verification"],
    )

    class _FailureRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(ExitStatus.READINESS, json.dumps(payload))

    remote = _FailureRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    with pytest.raises(OpsError) as raised:
        run_locked_rollback(
            remote,
            _config(),
            store,
            current_release_id=RELEASE_C,
            target_release_id=RELEASE_A,
            target_release_path=PurePosixPath(f"/opt/taskman/releases/{RELEASE_A}"),
            target_authority="direct",
            target_checksum="a" * 64,
            confirmation_fingerprint=(
                "activation-cccccccccccccccccccccccccccccccc:backward-compatible,"
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change"
            ),
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )

    assert raised.value.status is ExitStatus.SAFETY


def test_rollback_failure_result_keeps_all_observed_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Controller rendering must not discard the recovery evidence returned under the exclusive lock."""

    remote, store = _remote_store()
    failure = OpsError(
        ExitStatus.RELEASE,
        "start",
        "rollback transaction did not complete",
        changed=True,
        next_action="do not start another release; inspect the selected release",
    )
    failure.backup_id = "backup-dddddddddddddddddddddddddddddddd"
    failure.selected_release_id = RELEASE_A
    failure.service_state = "unknown"
    failure.database_state = "unchanged"
    failure.history_state = "unchanged"
    failure.activation_recorded = False
    failure.changed_stages = ("backup", "stop", "selection", "start")
    failure.recovery_commands = (
        "systemctl status taskman.service",
        "readlink -f /opt/taskman/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
    )
    failure.residue_paths = ()
    failure.warnings = ()
    failure.verification = None
    monkeypatch.setattr(
        "taskman_ops.workflows.rollback.run_locked_rollback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    result = rollback(
        remote,
        _config(),
        RELEASE_A,
        lifecycle_store=store,
        confirm=lambda _plan: True,
    )

    assert result.stage == "start-failed"
    assert result.changed is True
    assert result.facts == {
        "previous_release_id": RELEASE_C,
        "target_release_id": RELEASE_A,
        "backup_id": "backup-dddddddddddddddddddddddddddddddd",
        "activation_id": None,
        "selected_release_id": RELEASE_A,
        "service_state": "unknown",
        "database_state": "unchanged",
        "history_state": "unchanged",
        "activation_recorded": False,
        "changed_stages": ("backup", "stop", "selection", "start"),
        "recovery_commands": (
            "systemctl status taskman.service",
            "readlink -f /opt/taskman/current",
            "journalctl --no-pager --unit taskman.service --lines=100",
        ),
        "warnings": (),
        "residue_paths": (),
        "verification": None,
    }


def _executable(path: Path, lines: tuple[str, ...]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def _release_directory(path: Path) -> None:
    (path / "bin").mkdir(parents=True)
    (path / "lib").mkdir()
    (path / "releases").mkdir()
    (path / ".taskman-release.json").write_text(
        '{"schema_version":1,"release_id":"' + path.name + '","artifact_sha256":"' + "a" * 64 + '"}\n',
        encoding="utf-8",
    )
    for executable in ("server", "migrate"):
        binary = path / "bin" / executable
        body = "#!/bin/sh\n"
        if executable == "migrate":
            body += "printf 'migrate\\n' >> \"$TASKMAN_EVENTS\"\nexit 97\n"
        binary.write_text(body, encoding="utf-8")
        binary.chmod(0o750)
    (path / ".taskman-release.json").chmod(0o640)
    for directory in (path / "bin", path / "lib", path / "releases", path):
        directory.chmod(0o750)


def _adopted_release_directory(path: Path) -> None:
    """Create only the accepted adopted-release topology."""

    (path / "bin").mkdir(parents=True)
    (path / "lib").mkdir()
    (path / "releases").mkdir()
    _executable(path / "bin" / "server", ("#!/bin/sh", "exit 0"))
    for entry in (path, path / "bin", path / "lib", path / "releases"):
        entry.chmod(0o750)


def _write_private_record(path: Path, record: ReleaseRecord | ActivationRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _adopted_baseline(tmp_path: Path) -> Path:
    """Install strict adoption authority for ``RELEASE_A``."""

    deployment = tmp_path / "deployments"
    target = tmp_path / "managed" / "releases" / "operator-baseline"
    _adopted_release_directory(target)
    bundle = deployment / "adoption-transactions" / f"adoption-{RELEASE_A}"
    bundle.mkdir(parents=True)
    bundle.chmod(0o750)
    timestamp = "2026-09-05T10:00:00Z"
    (bundle / "release.json").write_text(
        '{"schema_version":1,"release_id":"' + RELEASE_A + '","artifact_sha256":null,'
        '"installed_at":"' + timestamp + '","activated_at":"' + timestamp + '",'
        '"previous_release_id":null,"backup_id":null,"migration_policy":"adopted"}\n',
        encoding="utf-8",
    )
    (bundle / "activation.json").write_text(
        '{"schema_version":1,"activation_id":"activation-' + "a" * 32 + '","previous_release_id":null,'
        '"candidate_release_id":"' + RELEASE_A + '","activated_at":"' + timestamp + '",'
        '"backup_id":null,"migration_policy":"adopted"}\n',
        encoding="utf-8",
    )
    for record in (bundle / "release.json", bundle / "activation.json"):
        record.chmod(0o600)
    marker = deployment / "adoptions" / f"adoption-{RELEASE_A}.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        '{"schema_version":1,"release_id":"' + RELEASE_A + '","adopted_at":"' + timestamp + '",'
        '"release_path":"' + str(target) + '","content_sha256":"' + "a" * 64 + '",'
        '"application_version":"0.2.0","source_revision":"unknown","artifact_sha256":"unknown","migrations":[]}\n',
        encoding="utf-8",
    )
    marker.chmod(0o600)
    return target


def test_rollback_transaction_selects_a_valid_adopted_target_without_direct_release_assumptions(tmp_path: Path) -> None:
    """Requiring a direct marker or migration executable would reject a valid adopted rollback target."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    commands = tmp_path / "commands"
    events = tmp_path / "events"
    service_state = tmp_path / "service-state"
    for directory in (managed, releases, deployment, backup_root, commands):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    target = _adopted_baseline(tmp_path)
    _release_directory(releases / RELEASE_C)
    (managed / "current").symlink_to(releases / RELEASE_C)
    _write_private_record(
        deployment / "releases" / f"release-{RELEASE_C}.json",
        ReleaseRecord(1, RELEASE_C, "c" * 64, THIRD, THIRD, RELEASE_A, None, "no-change"),
    )
    _write_private_record(
        deployment / "activations" / ("activation-" + "c" * 32 + ".json"),
        ActivationRecord(1, "activation-" + "c" * 32, RELEASE_A, RELEASE_C, THIRD, None, "no-change"),
    )
    manifest = deployment / "manifests" / f"release-{RELEASE_C}.json"
    manifest.parent.mkdir()
    manifest.write_text("{}\n", encoding="utf-8")
    manifest.chmod(0o600)

    _executable(
        commands / "psql",
        (
            "#!/bin/sh",
            "printf 'psql %s\\n' \"$*\" >> \"$TASKMAN_EVENTS\"",
            "case \"$*\" in *pg_database_size*) printf '1048576\\n' ;; *) printf '1\\n' ;; esac",
        ),
    )
    _executable(commands / "pg_dump", ("#!/bin/sh", "printf 'dump\\n' > \"$3\""))
    _executable(commands / "pg_restore", ("#!/bin/sh", "exit 0"))
    _executable(commands / "df", ("#!/bin/sh", "printf 'Avail\\n1073741824\\n'"))
    _executable(
        commands / "systemctl",
        (
            "#!/bin/sh",
            "case \"$1\" in",
            "stop) printf stopped > \"$TASKMAN_SERVICE_STATE\"; printf 'stop\\n' >> \"$TASKMAN_EVENTS\" ;;",
            "start) printf active > \"$TASKMAN_SERVICE_STATE\"; printf 'start\\n' >> \"$TASKMAN_EVENTS\" ;;",
            "is-active) test \"${3:-}\" != taskman.service || test \"$(cat \"$TASKMAN_SERVICE_STATE\")\" = active || exit 3 ;;",
            "show) printf 'active\\n123\\n' ;;",
            "*) exit 64 ;;",
            "esac",
        ),
    )
    _executable(commands / "readlink", ("#!/bin/sh", "case \"$*\" in */proc/123/exe*) printf '%s\\n' \"$TASKMAN_TARGET/bin/server\" ;; *) exec /usr/bin/readlink \"$@\" ;; esac"))
    _executable(commands / "ss", ("#!/bin/sh", "printf '%s\\n' 'LISTEN 0 1 127.0.0.1:4000 0.0.0.0:*' 'LISTEN 0 1 127.0.0.1:6789 0.0.0.0:*' 'LISTEN 0 1 127.0.0.1:5432 0.0.0.0:*'"))
    _executable(commands / "journalctl", ("#!/bin/sh", "printf 'startup complete\\n'"))
    _executable(
        commands / "curl",
        (
            "#!/bin/sh",
            "case \"$*\" in *127.0.0.1*) printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\n\\r\\nready' ;; *) printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\nStrict-Transport-Security: max-age=31536000\\r\\n\\r\\nready' ;; esac",
        ),
    )

    transaction = (
        ROLLBACK_TRANSACTION
        .replace("lock_root=/var/lock/taskman;", f"lock_root={tmp_path / 'canonical-lock'};", 1)
        .replace("owner_uid=0", "owner_uid=$(id -u)", 1)
        .replace("! -user root", '! -user "$(id -un)"')
        .replace("chown root:root", 'chown "$(id -u):$(id -g)"')
    )
    token = "d" * 32
    completed = subprocess.run(
        (
            "sh", "-ceu", transaction, "taskman-rollback-test", str(managed), str(releases), str(deployment),
            str(backup_root), "4000", "taskman.example.test", RELEASE_C, RELEASE_A, token,
            str(Path(__file__).resolve().parents[2] / "backup" / "taskman-backup"), "127.0.0.1", "5432", "taskman", "taskman_prod", "2",
            "6789", "/etc/caddy/Caddyfile", "5000", "0.5", "0.5", str(target),
            "activation-" + "c" * 32 + ":no-change", "adopted", "-",
        ),
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TASKMAN_EVENTS": str(events),
            "TASKMAN_SERVICE_STATE": str(service_state),
            "TASKMAN_TARGET": str(target),
        },
    )

    assert completed.returncode == 0, (completed.stderr, completed.stdout)
    payload = json.loads(completed.stdout)
    assert (managed / "current").resolve() == target
    assert events.read_text(encoding="utf-8").splitlines()[:3] == ["psql --no-psqlrc --host 127.0.0.1 --port 5432 --username taskman --dbname taskman_prod --tuples-only --no-align --command SELECT 1", "psql --no-psqlrc --host 127.0.0.1 --port 5432 --username taskman --dbname taskman_prod --tuples-only --no-align --command SELECT pg_database_size(current_database())", "stop"]
    assert payload["changed_stages"] == ["backup", "stop", "selection", "start", "verification", "records"]
    activation = deployment / "activations" / f"activation-{token}.json"
    assert activation.is_file()
    assert json.loads(activation.read_text(encoding="utf-8"))["candidate_release_id"] == RELEASE_A
    assert not (target / ".taskman-release.json").exists()
    assert not (target / "bin" / "migrate").exists()


@pytest.mark.parametrize(
    ("failure", "returncode", "stage", "selected", "service", "changed_stages", "expected_events"),
    [
        ("", ExitStatus.OK, "rolled-back", RELEASE_A, "active", ["backup", "stop", "start"], ["backup", "stop", "start"]),
        ("backup", ExitStatus.BACKUP, "backup", "unknown", "unknown", [], ["backup"]),
        ("stale", ExitStatus.SAFETY, "preflight", "unknown", "unknown", [], ["backup"]),
        ("outside", ExitStatus.SAFETY, "preflight", "unknown", "unknown", [], []),
        ("marker", ExitStatus.SAFETY, "preflight", "unknown", "unknown", [], []),
        ("selection", ExitStatus.RELEASE, "selection", "unknown", "stopped", ["backup", "stop", "selection"], ["backup", "stop"]),
        ("start", ExitStatus.RELEASE, "start", RELEASE_A, "unknown", ["backup", "stop", "selection", "start"], ["backup", "stop", "start"]),
        ("readiness", ExitStatus.READINESS, "verification", RELEASE_A, "unknown", ["backup", "stop", "selection", "start", "verification"], ["backup", "stop", "start"]),
        ("records", ExitStatus.RELEASE, "records", RELEASE_A, "active", ["backup", "stop", "selection", "start", "verification", "records"], ["backup", "stop", "start"]),
    ],
)
def test_rollback_transaction_keeps_its_lock_during_inherited_backup_and_never_runs_migrations(
    tmp_path: Path,
    failure: str,
    returncode: ExitStatus,
    stage: str,
    selected: str,
    service: str,
    changed_stages: list[str],
    expected_events: list[str],
) -> None:
    """The remote path changes only selection and activation history after a fresh inherited-lock backup."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    commands = tmp_path / "commands"
    events = tmp_path / "events"
    service_state = tmp_path / "service-state"
    backup_arguments = tmp_path / "backup-arguments"
    for directory in (managed, releases, deployment, backup_root, commands):
        directory.mkdir(parents=True)
        directory.chmod(0o750)
    for directory in (deployment / "releases", deployment / "manifests", deployment / "activations"):
        directory.mkdir()
        directory.chmod(0o750)
    _release_directory(releases / RELEASE_C)
    _release_directory(releases / RELEASE_A)
    if failure == "marker":
        marker = releases / RELEASE_A / ".taskman-release.json"
        marker.write_text(marker.read_text(encoding="utf-8") + "unexpected-data\n", encoding="utf-8")
    (managed / "current").symlink_to(releases / RELEASE_C)
    for directory in (deployment / "releases", deployment / "manifests"):
        record = directory / f"release-{RELEASE_A}.json"
        record.write_text("{}\n", encoding="utf-8")
        record.chmod(0o600)
    before_release_records = {
        entry: entry.read_bytes()
        for directory in (deployment / "releases", deployment / "manifests")
        for entry in directory.iterdir()
    }

    _executable(
        commands / "taskman-backup",
        (
                "#!/bin/sh",
                "printf '%s\\n' \"$*\" > \"$TASKMAN_BACKUP_ARGUMENTS\"",
                "printf 'backup\\n' >> \"$TASKMAN_EVENTS\"",
                "case \"$TASKMAN_ROLLBACK_FAILURE\" in backup) exit 6;; stale) exit 10;; esac",
            "printf '%s\\n' '{\"schema_version\":1,\"backup_id\":\"backup-dddddddddddddddddddddddddddddddd\",\"created_at\":\"2026-09-05T12:00:00Z\",\"size_bytes\":1,\"source_database_size_bytes\":1048576,\"database\":\"taskman_prod\",\"current_release_id\":\"0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6\",\"candidate_release_id\":\"0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6\",\"reason\":\"pre-rollback\",\"validated\":true,\"dump_path\":\"'\"$TASKMAN_BACKUP_ROOT\"'/backup.dump\"}'",
        ),
    )
    _executable(
        commands / "systemctl",
        (
            "#!/bin/sh",
            "case \"$1\" in",
            "stop) printf stopped > \"$TASKMAN_SERVICE_STATE\"; printf 'stop\\n' >> \"$TASKMAN_EVENTS\" ;;",
            "start) printf 'start\\n' >> \"$TASKMAN_EVENTS\"; test \"$TASKMAN_ROLLBACK_FAILURE\" != start || exit 1; printf active > \"$TASKMAN_SERVICE_STATE\" ;;",
            "is-active) test \"${3:-}\" != taskman.service || test \"$(cat \"$TASKMAN_SERVICE_STATE\")\" = active || exit 3 ;;",
            "show) printf 'active\\n123\\n' ;;",
            "*) exit 64 ;;",
            "esac",
        ),
    )
    _executable(
        commands / "readlink",
        (
            "#!/bin/sh",
            "case \"$*\" in",
            "*/proc/123/exe*) printf '%s\\n' \"$TASKMAN_TARGET/bin/server\" ;;",
            "*) exec /usr/bin/readlink \"$@\" ;;",
            "esac",
        ),
    )
    _executable(
        commands / "mv",
        (
            "#!/bin/sh",
            "test \"$TASKMAN_ROLLBACK_FAILURE\" != selection || exit 1",
            "exec /usr/bin/mv \"$@\"",
        ),
    )
    _executable(
        commands / "sync",
        (
            "#!/bin/sh",
            "case \"$TASKMAN_ROLLBACK_FAILURE:$*\" in records:*'/activations/'*) exit 1;; esac",
            "exec /usr/bin/sync \"$@\"",
        ),
    )
    _executable(
        commands / "ss",
        ("#!/bin/sh", "printf '%s\\n' 'LISTEN 0 1 127.0.0.1:4000 0.0.0.0:*' 'LISTEN 0 1 127.0.0.1:6789 0.0.0.0:*' 'LISTEN 0 1 127.0.0.1:5432 0.0.0.0:*'"),
    )
    _executable(commands / "journalctl", ("#!/bin/sh", "printf 'startup complete\\n'"))
    _executable(
        commands / "curl",
        (
            "#!/bin/sh",
            "case \"$*\" in",
            "*127.0.0.1*) test \"$TASKMAN_ROLLBACK_FAILURE\" != readiness || exit 22; printf 'local-readiness\\n' >> \"$TASKMAN_EVENTS\"; printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\n\\r\\nready' ;;",
            "*) printf 'public-readiness\\n' >> \"$TASKMAN_EVENTS\"; printf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\nStrict-Transport-Security: max-age=31536000\\r\\n\\r\\nready' ;;",
            "esac",
        ),
    )

    transaction = (
        ROLLBACK_TRANSACTION
        .replace("lock_root=/var/lock/taskman;", f"lock_root={tmp_path / 'canonical-lock'};", 1)
        .replace("owner_uid=0", "owner_uid=$(id -u)", 1)
        .replace("! -user root", '! -user "$(id -un)"')
        .replace("! -group taskman", '! -group "$(id -gn)"')
        .replace("chown root:root", 'chown "$(id -u):$(id -g)"')
    )
    token = "d" * 32
    completed = subprocess.run(
        (
            "sh", "-ceu", transaction, "taskman-rollback-test", str(managed), str(releases), str(deployment),
            str(backup_root), "4000", "taskman.example.test", RELEASE_C, RELEASE_A, token,
            str(commands / "taskman-backup"), "127.0.0.1", "5432", "taskman", "taskman_prod", "14",
            "6789", "/etc/caddy/Caddyfile", "5000", "0.5", "0.5", str(tmp_path / "outside" if failure == "outside" else releases / RELEASE_A),
            "activation-cccccccccccccccccccccccccccccccc:backward-compatible,activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:no-change", "direct", "a" * 64,
        ),
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TASKMAN_EVENTS": str(events),
            "TASKMAN_BACKUP_ARGUMENTS": str(backup_arguments),
            "TASKMAN_BACKUP_ROOT": str(backup_root),
            "TASKMAN_SERVICE_STATE": str(service_state),
            "TASKMAN_TARGET": str(releases / RELEASE_A),
            "TASKMAN_ROLLBACK_FAILURE": failure,
        },
    )

    assert completed.returncode == returncode, (completed.stderr, completed.stdout)
    payload = json.loads(completed.stdout)
    assert (managed / "current").resolve() == releases / (RELEASE_C if failure in {"backup", "stale", "outside", "marker", "selection"} else RELEASE_A)
    if expected_events:
        arguments = backup_arguments.read_text(encoding="utf-8")
        assert "--already-locked" in arguments
        assert "--reason pre-rollback" in arguments
    else:
        assert not backup_arguments.exists()
    observed_events = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
    assert observed_events[: len(expected_events)] == expected_events
    assert "migrate" not in observed_events
    assert {
        entry: entry.read_bytes()
        for directory in (deployment / "releases", deployment / "manifests")
        for entry in directory.iterdir()
    } == before_release_records

    activation = deployment / "activations" / f"activation-{token}.json"
    if failure:
        assert payload["stage"] == stage
        assert payload["backup_id"] == (None if failure in {"backup", "stale", "outside", "marker"} else "backup-" + "d" * 32)
        assert payload["selected_release_id"] == selected
        assert payload["service_state"] == service
        expected_database = "unknown" if failure in {"stale", "outside", "marker"} else "unchanged"
        expected_history = "unknown" if failure in {"stale", "outside", "marker"} else "unchanged"
        assert payload["database_state"] == expected_database
        assert payload["history_state"] == expected_history
        assert payload["activation_recorded"] is False
        assert payload["changed_stages"] == changed_stages
        assert not activation.exists()
    else:
        assert payload["stage"] == "rolled-back"
        assert payload["history_state"] == "activation-appended"
        assert payload["activation_recorded"] is True
        assert payload["changed_stages"] == ["backup", "stop", "selection", "start", "verification", "records"]
        activation_record = json.loads(activation.read_text(encoding="utf-8"))
        assert activation_record["activation_id"] == f"activation-{token}"
        assert activation_record["previous_release_id"] == RELEASE_C
        assert activation_record["candidate_release_id"] == RELEASE_A
        assert activation_record["backup_id"] == "backup-" + "d" * 32
        assert activation_record["migration_policy"] == "no-change"
