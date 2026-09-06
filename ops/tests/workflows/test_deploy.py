"""Controller contracts for helper-owned deployment and genesis."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.helper_runner import HelperInvocation
from taskman_ops.host_protocol import HostResult
from taskman_ops.manifests import ArtifactManifest, BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, VerifiedArtifact
from taskman_ops.workflows.deploy import _planning_authority, deploy, deploy_first_release


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CANDIDATE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate({
        "name": "production", "ssh_host": "203.0.113.10", "ssh_port": 22, "ssh_user": "deployer",
        "host_key_fingerprint": "SHA256:" + "A" * 43, "public_hostname": "taskman.acme.tld",
        "public_ipv4": "203.0.113.10", "target_os": "ubuntu26.04", "architecture": "amd64",
        "application_port": 4000, "distribution_port": 6789, "database_name": "taskman_prod",
        "database_role": "taskman", "mail_from": "no-reply@acme.tld",
    })


def artifact(tmp_path: Path, release_id: str = CANDIDATE) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"release")
    manifest = ArtifactManifest(
        2, "taskman", "0.2.0", "b" * 40, release_id, datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "ubuntu26.04", "amd64", "27.3.4.6", "1.18.3", "22.22.1", BUILDER_BASE_TAG,
        BUILDER_BASE_DIGEST, (), "taskman",
    )
    return VerifiedArtifact(archive, tmp_path / "manifest.json", tmp_path / "checksum", "c" * 64, manifest)


def payload(*, candidate: str, previous: str | None, changed: bool) -> dict[str, object]:
    return {
        "stage": "deployed" if changed else "already-current", "previous_release_id": previous,
        "candidate_release_id": candidate, "selected_release_id": candidate,
        "backup_id": "backup-" + "a" * 32 if changed else None, "activation_id": "activation-" + "d" * 32,
        "service_state": "active", "database_state": "unchanged", "activation_recorded": True,
        "changed": changed, "changed_stages": ("staging", "backup", "migration", "selection", "start", "records") if changed else (),
        "warnings": (), "recovery_commands": (), "residue_paths": (), "verification": {},
    }


def _plan(monkeypatch: pytest.MonkeyPatch, current: str = CURRENT) -> None:
    monkeypatch.setattr("taskman_ops.workflows.deploy._planning_authority", lambda *_args, **_kwargs: (current, None, ()))


def test_planning_authority_accepts_protocol_frozen_managed_activation_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A decoded JSON activation array is a tuple, not a controller-local list.

    Changing the planner back to accept only lists would reject a normal
    helper discover result before a user can see or confirm a deploy plan.
    """

    result = HostResult(
        protocol_version=1,
        operation="discover",
        operation_id="op-0123456789abcdef0123456789abcdef",
        outcome="succeeded",
        stage="discovered",
        changed_stages=(),
        lifecycle={
            "state": "managed",
            "current_migrations": (),
            "records": {
                "releases": (),
                "activations": ({"candidate_release_id": CURRENT},),
                "backups": (),
                "adoptions": (),
            },
            "warnings": (),
        },
        runtime_state={},
        verification={},
        residue_paths=(),
        recovery_actions=(),
        warnings=(),
    )
    monkeypatch.setattr("taskman_ops.workflows.deploy.temporary_helper_package", lambda: nullcontext(object()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.invoke_helper",
        lambda *_args: HelperInvocation(result=replace(result, operation_id=_args[-1].operation_id)),
    )

    previous, manual, migrations = _planning_authority(object(), config(), False)

    assert previous == CURRENT
    assert manual is None
    assert migrations == ()


def test_planning_authority_preserves_protocol_frozen_manual_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmed manual authority survives tuple freezing without weakening it.

    Rejecting tuple migrations would make the read-only helper's correctly
    decoded manual-adoption evidence unusable by the subsequent mutation.
    """

    checksum = "d" * 64
    manual_id = f"0.2.0-{checksum[:12]}-ubuntu26.04-amd64-otp27.3.4.6"
    result = HostResult(
        protocol_version=1,
        operation="discover",
        operation_id="op-0123456789abcdef0123456789abcdef",
        outcome="succeeded",
        stage="discovered",
        changed_stages=(),
        lifecycle={
            "state": "manual",
            "current_migrations": (),
            "records": {"releases": (), "activations": (), "backups": (), "adoptions": ()},
            "warnings": (),
            "manual_adoption": {
                "schema_version": 1,
                "release_id": manual_id,
                "release_path": "/opt/taskman/releases/manual",
                "content_sha256": checksum,
                "application_version": "0.2.0",
                "migrations": ({"filename": "20260906000000_add_widgets.exs", "sha256": "e" * 64},),
            },
        },
        runtime_state={},
        verification={},
        residue_paths=(),
        recovery_actions=(),
        warnings=(),
    )
    monkeypatch.setattr("taskman_ops.workflows.deploy.temporary_helper_package", lambda: nullcontext(object()))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.invoke_helper",
        lambda *_args: HelperInvocation(result=replace(result, operation_id=_args[-1].operation_id)),
    )

    previous, manual, migrations = _planning_authority(object(), config(), True)

    assert previous == manual_id
    assert manual is not None
    assert migrations[0].filename == "20260906000000_add_widgets.exs"
    assert manual.to_mapping()["migrations"] == [
        {"filename": "20260906000000_add_widgets.exs", "sha256": "e" * 64}
    ]


def test_deploy_confirms_then_translates_one_helper_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _plan(monkeypatch)
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_helper_deployment",
        lambda *_args, **kwargs: seen.append(kwargs) or payload(candidate=CANDIDATE, previous=CURRENT, changed=True),
    )

    result = deploy(object(), config(), artifact(tmp_path), present_plan=lambda _: None, confirm=lambda _: True)

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "deployed"
    assert result.facts["selected_release_id"] == CANDIDATE
    assert len(seen) == 1


def test_deploy_cancelled_before_helper_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _plan(monkeypatch)
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_helper_deployment", lambda *_args, **_kwargs: pytest.fail("invoked"))

    result = deploy(object(), config(), artifact(tmp_path), present_plan=lambda _: None, confirm=lambda _: False)

    assert result.stage == "confirmation-cancelled"
    assert result.changed is False


def test_deploy_translates_helper_failure_without_overriding_primary_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _plan(monkeypatch)
    error = OpsError(ExitStatus.MIGRATION, "migration", "helper failed", changed=True)
    error.backup_id = "backup-" + "a" * 32
    error.database_state = "changed"
    error.changed_stages = ("staging", "backup", "migration")
    error.warnings = ("unable to remove operation residue",)
    monkeypatch.setattr("taskman_ops.workflows.deploy.run_helper_deployment", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    result = deploy(object(), config(), artifact(tmp_path), present_plan=lambda _: None, confirm=lambda _: True)

    assert result.exit_status is ExitStatus.MIGRATION
    assert result.stage == "migration-failed"
    assert result.changed is True
    assert result.warnings == ("unable to remove operation residue",)
    assert result.facts["changed_stages"] == ("staging", "backup", "migration")


def test_genesis_translates_helper_noop_with_absent_predecessor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_helper_deployment",
        lambda *_args, **kwargs: seen.append(kwargs) or payload(candidate=CANDIDATE, previous=None, changed=False),
    )

    result = deploy_first_release(object(), config(), artifact(tmp_path))

    assert result.stage == "already-current"
    assert result.facts["previous_release_id"] is None
    assert seen == [{"migration_policy": "no-change", "previous_release_id": None, "current_migrations": (), "genesis": True}]
