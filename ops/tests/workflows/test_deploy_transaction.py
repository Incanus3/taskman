"""Exact controller-to-helper deployment request contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION
from taskman_ops.remote import CommandResult, UploadReceipt
from taskman_ops.releases.artifacts import DeploymentTarget
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import OTP_VERSION
from tests.support.environments import valid_environment
from tests.workflows.support import deployment_artifact


CURRENT = build_release_id(
    "0.2.0", "a" * 40, artifact_sha256="a" * 64, source_dirty=False, otp_version=OTP_VERSION
)
EXPECTED = {
    "selected_release_id": CURRENT,
    "last_successful_selection_id": "selection-" + "a" * 64 + ".json",
    "applied_migrations": (20260905120000,),
    "backup_protection_sha256": "b" * 64,
    "scheduled_backup_sha256": "c" * 64,
    "backup_timer_enabled": True,
    "downgrade_baseline_sha256": "d" * 64,
}


class Remote:
    def __init__(self) -> None:
        self.uploads: list[object] = []
        self.commands: list[tuple[object, object]] = []
        self.cleanup_fails = False

    def run(self, args, **kwargs):
        self.commands.append((args, kwargs))
        if self.cleanup_fails and args[:2] == ("rm", "-f"):
            return CommandResult(1)
        return CommandResult(0)

    def put(self, source, *_args, **_kwargs):
        self.uploads.append(source)
        return UploadReceipt()


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment())


def test_deployment_request_carries_exact_v3_authority_without_consent_fields(tmp_path, monkeypatch) -> None:
    """A host request cannot gain authority from public confirmation flags."""
    from taskman_ops.workflows import helper

    remote = Remote()
    captured = []

    def invoke(_remote, request, **_kwargs):
        captured.append(request)
        return HostResult(PROTOCOL_VERSION, request.operation, request.correlation_id, "succeeded", "completed", {}, ())

    monkeypatch.setattr(helper, "run_request", invoke)
    artifact = deployment_artifact(tmp_path)
    helper.run_deployment_request(
        remote,
        _config(),
        DeploymentTarget(artifact=artifact, release_record=None, source="built"),
        expected_state=EXPECTED,
        migration_policy="backward-compatible",
        backup_helper={"sha256": "e" * 64, "upload_path": None},
        prune_backup_ids=("backup-00000000000000000000000000000001",),
    )

    request = captured[0]
    assert request.protocol_version == PROTOCOL_VERSION
    assert request.expected_state == EXPECTED
    assert set(request.parameters) == {
        "target", "migration_policy", "credentials_path", "database", "verification", "backup_helper", "prune_backup_ids",
    }
    assert helper.mutable(request.parameters["target"]) == {
        "kind": "upload",
        "manifest": artifact.manifest.to_mapping(),
        "artifact_sha256": artifact.sha256,
        "artifact_path": request.parameters["target"]["artifact_path"],
    }
    assert helper.mutable(request.parameters["prune_backup_ids"]) == ["backup-00000000000000000000000000000001"]
    assert "yes" not in request.parameters
    assert "allow_downgrade" not in request.parameters
    assert "force" not in request.parameters


def test_deployment_request_rejects_unsorted_or_nonexact_confirmed_authority(tmp_path) -> None:
    """The controller cannot silently widen a prune set or omit an apply-time fact."""
    from taskman_ops.workflows import helper

    target = DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built")
    with pytest.raises(ValueError):
        helper.run_deployment_request(
            Remote(), _config(), target,
            expected_state={key: value for key, value in EXPECTED.items() if key != "backup_timer_enabled"},
            migration_policy="no-change",
            backup_helper={"sha256": "e" * 64, "upload_path": None},
            prune_backup_ids=(),
        )
    with pytest.raises(ValueError):
        helper.run_deployment_request(
            Remote(), _config(), target,
            expected_state=EXPECTED,
            migration_policy="no-change",
            backup_helper={"sha256": "e" * 64, "upload_path": None},
            prune_backup_ids=("backup-00000000000000000000000000000002", "backup-00000000000000000000000000000001"),
        )


def test_deployment_request_rejects_invalid_confirmed_state_values_before_upload(tmp_path) -> None:
    """A malformed final observation cannot be sent as host expected state."""
    from taskman_ops.workflows import helper

    target = DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built")
    with pytest.raises(ValueError):
        helper.run_deployment_request(
            Remote(), _config(), target,
            expected_state={**EXPECTED, "scheduled_backup_sha256": "not-a-checksum"},
            migration_policy="no-change",
            backup_helper={"sha256": "e" * 64, "upload_path": None},
            prune_backup_ids=(),
        )


@pytest.mark.parametrize("outcome", ("succeeded", "retryable"))
def test_deployment_uploads_are_removed_after_each_valid_helper_result(
    tmp_path, monkeypatch, outcome
) -> None:
    """A response, including a valid failed one, cannot retain private release bytes."""
    from taskman_ops.workflows import helper

    remote = Remote()
    captured = []

    def invoke(_remote, request, **_kwargs):
        captured.append(request)
        return HostResult(PROTOCOL_VERSION, request.operation, request.correlation_id, outcome, "final", {}, ())

    monkeypatch.setattr(helper, "run_request", invoke)
    artifact = deployment_artifact(tmp_path)
    result = helper.run_deployment_request(
        remote,
        _config(),
        DeploymentTarget(artifact=artifact, release_record=None, source="built"),
        expected_state=EXPECTED,
        migration_policy="no-change",
        backup_helper={"sha256": "e" * 64, "upload_path": None},
        prune_backup_ids=(),
    )

    uploaded = captured[0].parameters["target"]["artifact_path"]
    cleanup = [argv for argv, _kwargs in remote.commands if argv[:2] == ("rm", "-f")]
    assert cleanup == [("rm", "-f", "--", uploaded)]
    assert result.outcome == outcome


def test_deployment_upload_cleanup_warning_preserves_the_helper_result(tmp_path, monkeypatch) -> None:
    """Cleanup trouble is a generic warning, never a replacement result or archive path leak."""
    from taskman_ops.workflows import helper

    remote = Remote()
    remote.cleanup_fails = True

    monkeypatch.setattr(
        helper,
        "run_request",
        lambda _remote, request, **_kwargs: HostResult(
            PROTOCOL_VERSION, request.operation, request.correlation_id, "retryable", "final", {}, ()
        ),
    )
    result = helper.run_deployment_request(
        remote,
        _config(),
        DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built"),
        expected_state=EXPECTED,
        migration_policy="no-change",
        backup_helper={"sha256": "e" * 64, "upload_path": None},
        prune_backup_ids=(),
    )

    assert result.outcome == "retryable"
    assert result.warnings == ("transient upload cleanup was incomplete",)


def test_restore_scheduler_upload_is_removed_after_a_valid_helper_response(tmp_path, monkeypatch) -> None:
    """Restore refreshes scheduler code without retaining its cookie-bearing package."""
    from taskman_ops.workflows import helper

    remote = Remote()
    scheduler = SimpleNamespace(path=tmp_path / "taskman-backup.pyz", sha256="e" * 64)
    request = helper.request(
        "restore",
        _config(),
        expected_state={},
        parameters={
            "backup_id": "backup-" + "a" * 32,
            "credentials_path": "/etc/taskman/pgpass",
            "database": helper.database_settings(_config()),
            "verification": helper.verification_settings(_config()),
            "backup_helper": {"sha256": scheduler.sha256, "upload_path": "pending-controller-upload"},
            "prune_backup_ids": [],
            "replace_unfinished": False,
            "reapply": False,
        },
    )
    captured = []

    def invoke(_remote, dispatched, **_kwargs):
        captured.append(dispatched)
        return HostResult(PROTOCOL_VERSION, dispatched.operation, dispatched.correlation_id, "retryable", "final", {}, ())

    monkeypatch.setattr(helper, "run_request", invoke)

    result = helper.run_restore_request(
        remote, _config(), request=request, backup_helper_package=scheduler
    )

    uploaded = captured[0].parameters["backup_helper"]["upload_path"]
    cleanup = [argv for argv, _kwargs in remote.commands if argv[:2] == ("rm", "-f")]
    assert cleanup == [("rm", "-f", "--", uploaded)]
    assert result.outcome == "retryable"
