"""State-changing helper results remain redacted at the CLI boundary."""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
from dataclasses import replace
from pathlib import Path, PurePosixPath
import shutil

import pytest

from taskman_ops.cli import main
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import WorkflowResult, register_secret, clear_secrets
from taskman_ops.config import EnvironmentConfig
from taskman_ops.helper_client.package import build_helper_package
from taskman_ops.host_helper import __main__ as helper_entrypoint
from taskman_ops.host_helper.operations import deploy as host_deploy
from taskman_ops.host_helper.operations import discover as host_discover
from taskman_ops.host_protocol import decode_request, decode_result, encode_request, encode_result
from taskman_ops.releases.artifacts import DeploymentTarget
from taskman_ops.releases.manifests import ArtifactManifest, VerifiedArtifact
from taskman_ops.remote import CommandResult, UploadReceipt
from tests.host_helper import test_deploy as host_deploy_tests
from tests.support.environments import valid_environment


def test_helper_deployment_failure_preserves_primary_stage_and_redacts_residue() -> None:
    secret = "controller-failure-canary-5f5fb156"
    register_secret(secret)
    try:
        error = OpsError(ExitStatus.MIGRATION, "migration", f"host failed: {secret}", changed=True)
        result = WorkflowResult(
            command="deploy", environment="production", changed=True, stage="migration-failed",
            facts={"failure_stage": "migration", "changed_stages": ("staging", "backup", "migration")},
            warnings=("unable to remove operation residue",), next_action=error.next_action,
            exit_status=error.status,
        )
        stdout, stderr = StringIO(), StringIO()
        status = main(["deploy", "production", "--json"], dispatch_fn=lambda _invocation: result, stdout=stdout, stderr=stderr)

        assert status == int(ExitStatus.MIGRATION)
        assert secret not in stdout.getvalue() + stderr.getvalue()
        assert "migration-failed" in stdout.getvalue()
        assert "unable to remove operation residue" in stdout.getvalue()
    finally:
        clear_secrets()


def test_helper_deployment_noop_is_successful_without_mutation_claim() -> None:
    result = WorkflowResult(command="deploy", environment="production", changed=False, stage="already-current", facts={"selected_release_id": "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"})
    stdout, stderr = StringIO(), StringIO()

    status = main(["deploy", "production", "--json"], dispatch_fn=lambda _invocation: result, stdout=stdout, stderr=stderr)

    assert status == 0
    assert '"changed": false' in stdout.getvalue()
    assert stderr.getvalue() == ""


class _ControllerRemote:
    """Local transport seam preserving the controller's real request flow."""

    def run(self, *_args: object, **_kwargs: object) -> CommandResult:
        return CommandResult(0)

    def put(self, source: Path, destination: Path | PurePosixPath, **_kwargs: object) -> UploadReceipt:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
        return UploadReceipt()


def _artifact_target(request: object) -> DeploymentTarget:
    from taskman_ops.host_protocol import HostRequest
    from taskman_ops.workflows.helper import mutable

    assert isinstance(request, HostRequest)
    target = request.parameters["target"]
    assert isinstance(target, Mapping)
    manifest_mapping = target["manifest"]
    assert isinstance(manifest_mapping, Mapping)
    manifest = ArtifactManifest.from_mapping(mutable(manifest_mapping))
    archive = Path(target["artifact_path"])
    checksum = target["artifact_sha256"]
    assert isinstance(checksum, str)
    artifact = VerifiedArtifact(archive, archive, archive, checksum, manifest)
    return DeploymentTarget(artifact=artifact, release_record=None, source="explicit")


def _controller_config(paths: dict[str, str]) -> EnvironmentConfig:
    config = EnvironmentConfig.model_validate(valid_environment(ssh_port=22))
    return config.model_copy(
        update={
            "install_root": PurePosixPath(paths["install_root"]),
            "backup_root": PurePosixPath(paths["backup_root"]),
        }
    )


def _wire_helper(request: object, package: object) -> object:
    """Cross the final v3 wire codec before invoking the real helper dispatch."""

    from taskman_ops.helper_client.package import HelperPackage
    from taskman_ops.host_protocol import HostRequest

    assert isinstance(package, HelperPackage)
    assert package.path.is_file()
    assert isinstance(request, HostRequest)
    decoded = decode_request(encode_request(request))
    result = helper_entrypoint._dispatch(decoded)
    return decode_result(encode_result(result))


def _install_public_controller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime: object,
) -> _ControllerRemote:
    """Keep discovery/admission real while replacing only native and transport edges."""

    from taskman_ops.workflows import deploy as deploy_workflow
    from taskman_ops.workflows import helper, inventory

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    remote = _ControllerRemote()
    host_deploy_tests._install_runtime(monkeypatch, runtime)
    monkeypatch.setattr(host_deploy, "validate_credentials", lambda _path: None)
    monkeypatch.setattr(host_discover, "validate_credentials", lambda _path: None)
    monkeypatch.setattr(helper_entrypoint, "validate_credentials", lambda _path: None)
    monkeypatch.setattr(host_discover, "observe_database_state", runtime.observe_database)
    monkeypatch.setattr(helper_entrypoint, "observe_database_state", runtime.observe_database)
    monkeypatch.setattr(
        host_discover,
        "_scheduler_facts",
        lambda _paths: {
            "scheduled_backup_sha256": "a" * 64,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
        },
    )
    monkeypatch.setattr(
        helper_entrypoint,
        "_scheduler_facts",
        lambda _paths: {
            "scheduled_backup_sha256": "a" * 64,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
        },
    )

    real_run_request = helper.run_request

    def dispatch(_remote: object, request: object, **_kwargs: object) -> object:
        return real_run_request(
            _remote,
            request,
            package=package,
            invoker=lambda _transport, selected, wire_request, **_options: _wire_helper(
                wire_request, selected
            ),
        )

    monkeypatch.setattr(deploy_workflow, "validate_operational_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy_workflow, "run_request", dispatch)
    monkeypatch.setattr(inventory, "run_request", dispatch)
    monkeypatch.setattr(helper, "run_request", dispatch)
    return remote


def _state(paths: dict[str, str], runtime: object):
    return host_deploy.observe_host_state(
        host_deploy.ManagedPaths.from_mapping(paths),
        database=runtime.observe_database(),
        include_runtime=True,
        allow_selection_transition=True,
    )


def _failed_verification_report(release_id: str) -> dict[str, object]:
    names = (
        "taskman-service",
        "release-identity",
        "caddy-service",
        "listener-topology",
        "startup-journal",
        "local-readiness",
    )
    return {
        "schema_version": 1,
        "status": "failed",
        "exit_status": 9,
        "release_id": release_id,
        "expected_release_id": release_id,
        "checks": [
            {
                "schema_version": 1,
                "name": name,
                "status": "failed" if name == "local-readiness" else "passed",
                "summary": "checked",
            }
            for name in names
        ],
        "next_action": "inspect the fixed verification summaries and correct the reported host state before retrying",
    }


def _passing_verification_report(release_id: str) -> dict[str, object]:
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
    return {
        "schema_version": 1,
        "status": "ok",
        "exit_status": 0,
        "release_id": release_id,
        "expected_release_id": release_id,
        "checks": [
            {"schema_version": 1, "name": name, "status": "passed", "summary": "checked"}
            for name in names
        ],
        "next_action": None,
    }


def _pass_verification(release_id: str):
    def verify(wire_request, **_kwargs):
        from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION

        return HostResult(
            PROTOCOL_VERSION,
            "verify",
            wire_request.correlation_id,
            "succeeded",
            "verified",
            {"report": _passing_verification_report(release_id)},
            (),
        )

    return verify


def test_public_controller_retries_a_selected_unverified_release_without_synthetic_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing B before its verification succeeds must break this recovery path."""
    from taskman_ops.workflows.deploy import deploy

    request = host_deploy_tests._request(tmp_path / "b")
    host_deploy_tests._install_current(dict(request.paths))
    runtime = host_deploy_tests._Runtime()
    remote = _install_public_controller(monkeypatch, tmp_path, runtime)
    target = _artifact_target(request)

    def fail_verification(wire_request, **_kwargs):
        runtime.events.append("verify")
        from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION

        return HostResult(
            PROTOCOL_VERSION,
            "verify",
            wire_request.correlation_id,
            "retryable",
            "not ready",
            {"report": _failed_verification_report(target.release_id)},
            (),
        )

    monkeypatch.setattr(host_deploy, "verify", fail_verification)
    failed = deploy(
        remote, _controller_config(dict(request.paths)), target,
        migration_policy="backward-compatible", yes=True, allow_downgrade=True,
    )

    failed_state = _state(dict(request.paths), runtime)
    assert failed.exit_status is ExitStatus.READINESS
    assert failed_state.selected_release_id == target.release_id
    assert [selection.release_id for selection in failed_state.selections] == [host_deploy_tests.CURRENT]
    assert failed_state.backup_protections[0].target_release_id == target.release_id

    monkeypatch.setattr(host_deploy, "verify", _pass_verification(target.release_id))
    recovered = deploy(remote, _controller_config(dict(request.paths)), target, yes=True, allow_downgrade=True)

    state = _state(dict(request.paths), runtime)
    assert recovered.exit_status is ExitStatus.OK
    assert [selection.release_id for selection in state.selections] == [host_deploy_tests.CURRENT, target.release_id]
    assert state.selections[-1].previous_release_id == host_deploy_tests.CURRENT
    assert state.backup_protections == ()


def test_public_controller_replaces_an_unhealthy_selected_release_without_publishing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating an unhealthy B as successful would make C inherit the wrong predecessor."""
    from taskman_ops.workflows.deploy import deploy

    b_request = host_deploy_tests._request(tmp_path / "b")
    c_request = replace(host_deploy_tests._request(tmp_path / "c", application_version="0.3.0"), paths=b_request.paths)
    host_deploy_tests._install_current(dict(b_request.paths))
    runtime = host_deploy_tests._Runtime()
    remote = _install_public_controller(monkeypatch, tmp_path, runtime)
    b_target = _artifact_target(b_request)
    c_target = _artifact_target(c_request)

    def fail_verification(wire_request, **_kwargs):
        runtime.events.append("verify")
        from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION

        return HostResult(
            PROTOCOL_VERSION,
            "verify",
            wire_request.correlation_id,
            "retryable",
            "not ready",
            {"report": _failed_verification_report(b_target.release_id)},
            (),
        )

    monkeypatch.setattr(host_deploy, "verify", fail_verification)
    failed = deploy(
        remote, _controller_config(dict(b_request.paths)), b_target,
        migration_policy="backward-compatible", yes=True, allow_downgrade=True,
    )
    assert failed.exit_status is ExitStatus.READINESS
    assert [selection.release_id for selection in _state(dict(b_request.paths), runtime).selections] == [host_deploy_tests.CURRENT]

    monkeypatch.setattr(host_deploy, "verify", _pass_verification(c_target.release_id))
    replaced = deploy(remote, _controller_config(dict(b_request.paths)), c_target, yes=True, allow_downgrade=True)

    state = _state(dict(b_request.paths), runtime)
    assert replaced.exit_status is ExitStatus.OK
    assert state.selected_release_id == c_target.release_id
    assert [selection.release_id for selection in state.selections] == [host_deploy_tests.CURRENT, c_target.release_id]
    assert state.selections[-1].previous_release_id == host_deploy_tests.CURRENT
    assert all(protection.target_release_id != b_target.release_id for protection in state.backup_protections)
