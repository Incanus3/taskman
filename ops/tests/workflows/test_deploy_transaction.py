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


@pytest.mark.parametrize("operation", ("deploy", "genesis", "prepare_genesis"))
def test_deployment_request_carries_exact_v3_authority_without_consent_fields(tmp_path, monkeypatch, operation) -> None:
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
        operation=operation,
        migration_policy="backward-compatible",
        backup_helper={"sha256": "e" * 64, "upload_path": None},
        prune_backup_ids=("backup-00000000000000000000000000000001",),
    )

    request = captured[0]
    assert request.operation == operation
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
    assert result.result.outcome == outcome


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

    assert result.result.outcome == "retryable"
    assert result.result.warnings == ("transient upload cleanup was incomplete",)


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
    assert result.result.outcome == "retryable"


def _assert_upload_request(tmp_path, monkeypatch, operation, artifact_input, helper_input):
    """Every upload producer prepares once, including helper-only reuse; no-input reuse stays read-only."""
    from taskman_ops.workflows import helper
    from taskman_ops.host_helper.records import ReleaseRecord

    config = _config()
    artifact = deployment_artifact(tmp_path)
    record = ReleaseRecord.from_mapping({
        "schema_version": 2, "release_id": artifact.manifest.release_id,
        "source_revision": artifact.manifest.source_revision,
        "artifact_sha256": artifact.sha256,
        "migrations": [item.to_mapping() for item in artifact.manifest.migrations],
        "artifact_manifest": artifact.manifest.to_mapping(),
    }) if not artifact_input else None
    target = DeploymentTarget(artifact=artifact if artifact_input else None, release_record=record, source="built" if artifact_input else "installed")
    remote = Remote()
    events = []
    scheduler = SimpleNamespace(path=tmp_path / "helper.pyz", sha256="e" * 64)
    def prepare(_remote, root, *, operation, destinations):
        assert root == config.install_root
        events.append(("prepare", destinations))
        return "changed"
    def put(source, destination, **kwargs):
        events.append("put")
        assert destination.parent == config.upload_root
        assert destination.name in events[0][1]
        assert kwargs == {"mode": 0o600, "sensitive": True}
        return UploadReceipt()
    raw = HostResult(PROTOCOL_VERSION, operation, "op-" + "a" * 32, "retryable", "final", {"mutation_state": "unchanged"}, ())
    def dispatch(_remote, request, **kwargs):
        events.append("dispatch")
        assert kwargs["prior_mutation_state"] == ("changed" if artifact_input or helper_input else "unchanged")
        return raw
    monkeypatch.setattr(helper, "prepare_upload_root", prepare, raising=False)
    monkeypatch.setattr(remote, "put", put)
    monkeypatch.setattr(helper, "run_request", dispatch)
    result = helper.run_deployment_request(remote, config, target,
        expected_state=EXPECTED, migration_policy="no-change", operation=operation,
        backup_helper={"sha256": scheduler.sha256, "upload_path": "pending-controller-upload" if helper_input else None},
        backup_helper_package=scheduler, prune_backup_ids=())
    if artifact_input or helper_input:
        assert events[0][0] == "prepare"
        assert len(events[0][1]) == artifact_input + helper_input
        assert all('/' not in name for name in events[0][1])
        assert events[1:] == ["put"] * (artifact_input + helper_input) + ["dispatch"]
    else:
        assert events == ["dispatch"]
    assert result.result is raw
    assert result.prior_mutation_state == ("changed" if artifact_input or helper_input else "unchanged")


@pytest.mark.parametrize("code,state,status,stage,message", (
    (10, "unchanged", 10, "deploy", "upload staging path is unsafe"),
    (1, "unknown", 8, "release", "unable to prepare private upload staging"),
))
def test_upload_preparation_failure_preserves_evidence(tmp_path, monkeypatch, code, state, status, stage, message):
    from taskman_ops.errors import OpsError
    from taskman_ops.workflows import helper
    remote = Remote()
    monkeypatch.setattr(remote, "run", lambda *_a, **_kw: CommandResult(code))
    monkeypatch.setattr(helper, "run_request", lambda *_a, **_kw: pytest.fail("unsafe preparation dispatched"))
    with pytest.raises(OpsError) as raised:
        helper.run_deployment_request(remote, _config(),
            DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built"),
            expected_state=EXPECTED, migration_policy="no-change",
            backup_helper={"sha256": "e" * 64, "upload_path": None}, prune_backup_ids=())
    assert (raised.value.status, raised.value.stage, raised.value.message) == (status, stage, message)
    assert raised.value.state["mutation_state"] == state
    assert not remote.uploads


def test_restore_helper_upload_prepares_root(tmp_path, monkeypatch):
    from taskman_ops.workflows import helper
    config = _config()
    remote = Remote()
    scheduler = SimpleNamespace(path=tmp_path / "backup.pyz", sha256="e" * 64)
    request = helper.request("restore", config, parameters={
        "backup_id": "backup-" + "a" * 32, "credentials_path": "/etc/taskman/pgpass",
        "database": helper.database_settings(config), "verification": helper.verification_settings(config),
        "backup_helper": {"sha256": scheduler.sha256, "upload_path": "pending-controller-upload"},
        "prune_backup_ids": [], "replace_unfinished": False, "reapply": False})
    events = []
    prepared = []
    def prepare(_remote, root, *, operation, destinations):
        assert root == config.install_root and operation == "restore"
        prepared.extend(destinations)
        events.append("prepare")
        return "changed"
    monkeypatch.setattr(helper, "prepare_upload_root", prepare)
    def put(_source, destination, **kwargs):
        events.append("put")
        assert destination.parent == config.upload_root
        assert prepared == [destination.name]
        assert kwargs == {"mode": 0o600, "sensitive": True}
        return UploadReceipt()
    raw = HostResult.for_request(request, "retryable", "final", {})
    def dispatch(*_a, **kwargs):
        events.append("dispatch")
        assert kwargs["prior_mutation_state"] == "changed"
        return raw
    monkeypatch.setattr(remote, "put", put)
    monkeypatch.setattr(helper, "run_request", dispatch)
    result = helper.run_restore_request(remote, config, request=request, backup_helper_package=scheduler,
        prior_mutation_state="unknown")
    assert events == ["prepare", "put", "dispatch"]
    assert result.result is raw
    assert result.prior_mutation_state == "changed"


@pytest.mark.parametrize("failure", ("transfer", "predispatch", "dispatched", "protocol"))
def test_upload_dispatch_failure_retains_preparation_mutation(tmp_path, monkeypatch, failure):
    from taskman_ops.errors import OpsError, HelperTransportError
    from taskman_ops.helper_client.package import HelperPackage
    from taskman_ops.workflows import helper
    remote = Remote()
    def run(args, **kwargs):
        remote.commands.append((args, kwargs))
        return CommandResult(3 if args[0] == "/usr/bin/python3" else 0)
    monkeypatch.setattr(remote, "run", run)
    def failed_put(*_a, **_kw):
        raise OpsError(5, "remote", "upload failed")
    if failure == "transfer":
        monkeypatch.setattr(remote, "put", failed_put)
    def invoke(_remote, _package, request):
        if failure == "protocol":
            return HostResult.for_request(request, "retryable", "invalid", {})
        raise HelperTransportError(OpsError(5, "helper", "connection lost"),
            helper_entry_dispatched=failure == "dispatched")
    with pytest.raises(OpsError) as raised:
        helper.run_deployment_request(remote, _config(),
            DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built"),
            expected_state=EXPECTED, migration_policy="no-change",
            backup_helper={"sha256": "e" * 64, "upload_path": None}, prune_backup_ids=(),
            package=HelperPackage(tmp_path / "unused.pyz", "e" * 64, PROTOCOL_VERSION), invoker=invoke)
    assert raised.value.changed
    assert raised.value.state["mutation_state"] == "changed"
    cleanup = [args for args, _ in remote.commands if args[:2] == ("rm", "-f")]
    assert bool(cleanup) == (failure == "predispatch")


@pytest.mark.parametrize("outcome", ("succeeded", "retryable"))
def test_upload_dispatch_preserves_raw_helper_evidence(tmp_path, monkeypatch, outcome):
    """Directory creation must not overwrite the helper's own unchanged evidence."""
    from taskman_ops.helper_client.package import HelperPackage
    from taskman_ops.workflows import helper
    from tests.workflows.test_deploy import _success
    remote = Remote()
    real_run = remote.run
    monkeypatch.setattr(remote, "run", lambda args, **kw: CommandResult(3) if args[0] == "/usr/bin/python3" else real_run(args, **kw))
    raw = _success(mutation_state="unchanged")
    state = dict(raw.state)
    if outcome == "retryable":
        state.update(exit_code=8, failed_boundary="staging", report=None)
    def invoke(_remote, _package, request):
        return HostResult.for_request(request, outcome, "final", state)
    result = helper.run_deployment_request(remote, _config(),
        DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source="built"),
        expected_state=EXPECTED, migration_policy="no-change",
        backup_helper={"sha256": "e" * 64, "upload_path": None}, prune_backup_ids=(),
        package=HelperPackage(tmp_path / "unused.pyz", "e" * 64, PROTOCOL_VERSION), invoker=invoke)
    assert result.prior_mutation_state == "changed"
    assert result.result.state["mutation_state"] == "unchanged"
    assert result.result.outcome == outcome
    assert helper.mutation_result_facts(result.result, prior_mutation_state=result.prior_mutation_state)["mutation_state"] == "changed"
    assert len([args for args, _ in remote.commands if args[:2] == ("rm", "-f")]) == 1


@pytest.mark.parametrize("operation", ("deploy", "genesis", "prepare_genesis"))
def test_upload_request_uses_derived_root(tmp_path, monkeypatch, operation):
    _assert_upload_request(tmp_path, monkeypatch, operation, True, True)


@pytest.mark.parametrize("operation", ("deploy", "genesis", "prepare_genesis"))
def test_installed_release_helper_upload_prepares_root(tmp_path, monkeypatch, operation):
    _assert_upload_request(tmp_path, monkeypatch, operation, False, True)


@pytest.mark.parametrize("operation", ("deploy", "genesis", "prepare_genesis"))
def test_installed_release_reuse_without_inputs_does_not_prepare_uploads(tmp_path, monkeypatch, operation):
    _assert_upload_request(tmp_path, monkeypatch, operation, False, False)


@pytest.mark.parametrize('preparation, proof, expected', [('unchanged', False, 'unknown'), ('changed', False, 'changed'), ('unchanged', True, 'unchanged')])
def test_failed_native_upload_preserves_possible_mutation(tmp_path, monkeypatch, preparation, proof, expected):
    from taskman_ops.errors import OpsError
    from taskman_ops.workflows import helper
    remote = Remote()
    destination = tmp_path / 'touched-upload'
    monkeypatch.setattr(helper, 'prepare_upload_root', lambda *_a, **_kw: preparation)
    def interrupted(*_a, **_kw):
        if not proof:
            destination.write_bytes(b'partial native install')
        raise OpsError(5, 'remote', 'private remote upload failed', state={'mutation_state': 'unchanged'} if proof else {})
    monkeypatch.setattr(remote, 'put', interrupted)
    monkeypatch.setattr(helper, 'run_request', lambda *_a, **_kw: pytest.fail('failed upload dispatched helper'))
    with pytest.raises(OpsError) as raised:
        helper.run_deployment_request(remote, _config(), DeploymentTarget(artifact=deployment_artifact(tmp_path), release_record=None, source='built'),
            expected_state=EXPECTED, migration_policy='no-change', backup_helper={'sha256': 'e' * 64, 'upload_path': None}, prune_backup_ids=())
    assert raised.value.state == {'mutation_state': expected}
    assert raised.value.changed is (expected != 'unchanged')
    assert destination.exists() is (not proof)
