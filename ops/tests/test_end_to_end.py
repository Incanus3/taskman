"""State-changing helper results remain redacted at the CLI boundary."""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
from dataclasses import replace
from pathlib import Path, PurePosixPath
import json
import os
import select
import shutil
import subprocess
import sys

import pytest

from taskman_ops.cli import main
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import WorkflowResult, register_secret, clear_secrets
from taskman_ops.config import EnvironmentConfig
from taskman_ops.helper_client.package import build_helper_package, build_scheduled_backup_package
from taskman_ops.host_helper.operations import deploy as host_deploy
from taskman_ops.host_protocol import decode_result, encode_request
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
        status = main(["deploy", "production", "--json", "--yes"], dispatch_fn=lambda _invocation: result, stdout=stdout, stderr=stderr)

        assert status == int(ExitStatus.MIGRATION)
        assert secret not in stdout.getvalue() + stderr.getvalue()
        assert "migration-failed" in stdout.getvalue()
        assert "unable to remove operation residue" in stdout.getvalue()
    finally:
        clear_secrets()


def test_helper_deployment_noop_is_successful_without_mutation_claim() -> None:
    result = WorkflowResult(command="deploy", environment="production", changed=False, stage="already-current", facts={"selected_release_id": "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"})
    stdout, stderr = StringIO(), StringIO()

    status = main(["deploy", "production", "--json", "--yes"], dispatch_fn=lambda _invocation: result, stdout=stdout, stderr=stderr)

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


def _wire_helper(request: object, package: object, runtime_path: Path) -> object:
    """Execute the archive entrypoint after crossing the final v3 wire codec."""

    from taskman_ops.helper_client.package import HelperPackage
    from taskman_ops.host_protocol import HostRequest

    assert isinstance(package, HelperPackage)
    assert package.path.is_file()
    assert isinstance(request, HostRequest)
    return _run_isolated_helper(package, request, runtime_path)


_ISOLATED_HELPER_HARNESS = r'''
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

archive, runtime_path = map(Path, sys.argv[1:3])
ready_fd = int(sys.argv[3])
sys.path.insert(0, archive.as_posix())

from taskman_ops.host_helper import __main__ as entrypoint
from taskman_ops.host_helper import backup_helper, services, state as state_module
from taskman_ops.host_helper.operations import deploy as deploy_module
from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper.records import BackupRecord, write_backup_manifest
from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION

assert archive.as_posix() in entrypoint.__file__

def read_state():
    return json.loads(runtime_path.read_text())

def write_state(value):
    runtime_path.write_text(json.dumps(value, sort_keys=True))

def database(*_args, **_kwargs):
    return {"state": "ready", "applied_migrations": tuple(read_state()["migrations"])}

def command(argv, **_kwargs):
    value = read_state()
    if argv[:2] == ("systemctl", "stop"):
        value["service_running"] = False
    elif argv[:2] == ("systemctl", "start"):
        value["service_running"] = True
    elif argv[0] == "systemd-run":
        value["migrations"] = [20260905120000]
    write_state(value)
    return subprocess.CompletedProcess(argv, 0, b"", b"")

def backup(current, paths, *_args, **_kwargs):
    value = read_state()
    value["backup_count"] += 1
    dump = Path(paths.local(paths.backup_root / f"backup-{value['backup_count']:032x}.dump"))
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_bytes(b"archive-backup")
    dump.chmod(0o600)
    record = BackupRecord(
        f"backup-{value['backup_count']:032x}",
        __import__("datetime").datetime(2026, 9, 7, 12, value["backup_count"], tzinfo=__import__("datetime").UTC),
        hashlib.sha256(dump.read_bytes()).hexdigest(),
        current.selected_release_id,
        tuple(value["migrations"]),
        1024,
    )
    write_backup_manifest(paths, record)
    write_state(value)
    return record

def report(release_id, ok):
    names = (
        "taskman-service", "release-identity", "caddy-service", "listener-topology",
        "startup-journal", "local-readiness", "public-readiness", "public-hsts",
    )
    return {
        "schema_version": 1,
        "status": "ok" if ok else "failed",
        "exit_status": 0 if ok else 9,
        "release_id": release_id,
        "expected_release_id": release_id,
        "checks": [
            {"schema_version": 1, "name": name, "status": "passed" if ok or name != "local-readiness" else "failed", "summary": "checked"}
            for name in names
        ],
        "next_action": None if ok else "inspect the fixed verification summaries and correct the reported host state before retrying",
    }

def verify(request, **_kwargs):
    expected = request.expected_state["expected_release_id"]
    ok = read_state()["verification"] == "passing"
    return HostResult(PROTOCOL_VERSION, "verify", request.correlation_id, "succeeded" if ok else "retryable", "verified" if ok else "not ready", {"report": report(expected, ok)}, ())

deploy_module.observe_database_state_or_empty = database
discover_module.observe_database_state = database
deploy_module.create_validated_backup = backup
deploy_module.run_command = command
services.run_command = command
deploy_module.host_preflight = lambda *_args: None
deploy_module.validate_credentials = lambda *_args: None
discover_module.validate_credentials = lambda *_args: None
deploy_module._taskman_gid = os.getegid
state_module._service_state = lambda include_runtime: "running" if include_runtime and read_state()["service_running"] else "stopped"
deploy_module.verify = verify
facts = {"scheduled_backup_sha256": read_state()["scheduler_sha256"], "backup_timer_enabled": True, "backup_timer_state": "active"}
deploy_module._scheduler_facts = lambda _paths: facts
discover_module._scheduler_facts = lambda _paths: facts
backup_helper.observe_backup_timer = lambda **_kwargs: (True, "active")
backup_helper._verified_executable_checksum = lambda: read_state()["scheduler_sha256"]

os.write(ready_fd, b"R")
os.close(ready_fd)
runpy.run_path(archive.as_posix(), run_name="__main__")
'''


_ISOLATED_HELPER_TIMEOUT_SECONDS = 10.0


def _terminate_isolated_helper(
    process: subprocess.Popen[bytes], timeout_seconds: float
) -> tuple[bytes, bytes]:
    process.kill()
    try:
        return process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        raise AssertionError("isolated helper did not exit after termination") from error


def _run_isolated_helper(
    package: object,
    request: object,
    runtime_path: Path,
    *,
    timeout_seconds: float = _ISOLATED_HELPER_TIMEOUT_SECONDS,
    harness: str = _ISOLATED_HELPER_HARNESS,
) -> object:
    """Run the archive's own entrypoint with only native-effect seams substituted."""

    from taskman_ops.helper_client.package import HelperPackage
    from taskman_ops.host_protocol import HostRequest

    assert isinstance(package, HelperPackage)
    assert isinstance(request, HostRequest)
    assert timeout_seconds > 0
    ready_read, ready_write = os.pipe()
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            harness,
            package.path.as_posix(),
            runtime_path.as_posix(),
            str(ready_write),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(ready_write,),
    )
    os.close(ready_write)
    try:
        ready, _, _ = select.select([ready_read], [], [], timeout_seconds)
        if not ready:
            _terminate_isolated_helper(process, timeout_seconds)
            raise AssertionError("isolated helper did not signal readiness before its deadline")
        marker = os.read(ready_read, 1)
        if marker != b"R":
            _stdout, stderr = _terminate_isolated_helper(process, timeout_seconds)
            raise AssertionError(
                "isolated helper exited before declaring readiness: "
                + stderr.decode("utf-8", "replace")
            )
        try:
            stdout, stderr = process.communicate(
                input=encode_request(request), timeout=timeout_seconds
            )
        except subprocess.TimeoutExpired as error:
            _terminate_isolated_helper(process, timeout_seconds)
            raise AssertionError("isolated helper did not finish before its deadline") from error
    finally:
        os.close(ready_read)
    assert process.returncode == 0, stderr.decode("utf-8", "replace")
    assert stderr == b""
    return decode_result(stdout)


def test_isolated_helper_runner_requires_a_bounded_readiness_handshake(tmp_path: Path) -> None:
    """A blocked helper must fail within the caller's explicit deadline."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = host_deploy_tests._request(tmp_path / "blocked")

    with pytest.raises(AssertionError, match="did not signal readiness"):
        _run_isolated_helper(
            package,
            request,
            tmp_path / "unused-runtime.json",
            timeout_seconds=0.25,
            harness="import sys; sys.stdin.buffer.read()",
        )


def _install_public_controller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    verification: str = "failing",
) -> tuple[_ControllerRemote, Path]:
    """Keep discovery/admission real while replacing only native and transport edges."""

    from taskman_ops.workflows import deploy as deploy_workflow
    from taskman_ops.workflows import helper, inventory

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    remote = _ControllerRemote()
    runtime_path = tmp_path / "isolated-helper-state.json"
    scheduler = build_scheduled_backup_package(tmp_path / "taskman-backup.pyz")
    runtime_path.write_text(json.dumps({
        "backup_count": 0,
        "migrations": [],
        "scheduler_sha256": scheduler.sha256,
        "service_running": False,
        "verification": verification,
    }))

    real_run_request = helper.run_request

    def dispatch(_remote: object, request: object, **_kwargs: object) -> object:
        return real_run_request(
            _remote,
            request,
            package=package,
            invoker=lambda _transport, selected, wire_request, **_options: _wire_helper(
                wire_request, selected, runtime_path
            ),
        )

    monkeypatch.setattr(deploy_workflow, "validate_operational_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy_workflow, "run_request", dispatch)
    monkeypatch.setattr(inventory, "run_request", dispatch)
    monkeypatch.setattr(helper, "run_request", dispatch)
    return remote, runtime_path


def _state(paths: dict[str, str], runtime_path: Path):
    runtime = json.loads(runtime_path.read_text())
    return host_deploy.observe_host_state(
        host_deploy.ManagedPaths.from_mapping(paths),
        database={"state": "ready", "applied_migrations": tuple(runtime["migrations"])},
        include_runtime=True,
        allow_selection_transition=True,
    )


def _set_verification(runtime_path: Path, value: str) -> None:
    runtime = json.loads(runtime_path.read_text())
    runtime["verification"] = value
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))


def test_public_controller_retries_a_selected_unverified_release_without_synthetic_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing B before its verification succeeds must break this recovery path."""
    from taskman_ops.workflows.deploy import deploy

    request = host_deploy_tests._request(tmp_path / "b")
    host_deploy_tests._install_current(dict(request.paths))
    remote, runtime_path = _install_public_controller(monkeypatch, tmp_path)
    target = _artifact_target(request)
    failed = deploy(
        remote, _controller_config(dict(request.paths)), target,
        migration_policy="backward-compatible", yes=True, allow_downgrade=True,
    )

    failed_state = _state(dict(request.paths), runtime_path)
    assert failed.exit_status is ExitStatus.READINESS
    assert failed_state.selected_release_id == target.release_id
    assert [selection.release_id for selection in failed_state.selections] == [host_deploy_tests.CURRENT]
    assert failed_state.backup_protections[0].target_release_id == target.release_id

    _set_verification(runtime_path, "passing")
    recovered = deploy(remote, _controller_config(dict(request.paths)), target, yes=True, allow_downgrade=True)

    state = _state(dict(request.paths), runtime_path)
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
    remote, runtime_path = _install_public_controller(monkeypatch, tmp_path)
    b_target = _artifact_target(b_request)
    c_target = _artifact_target(c_request)

    failed = deploy(
        remote, _controller_config(dict(b_request.paths)), b_target,
        migration_policy="backward-compatible", yes=True, allow_downgrade=True,
    )
    assert failed.exit_status is ExitStatus.READINESS
    assert [selection.release_id for selection in _state(dict(b_request.paths), runtime_path).selections] == [host_deploy_tests.CURRENT]

    _set_verification(runtime_path, "passing")
    replaced = deploy(remote, _controller_config(dict(b_request.paths)), c_target, yes=True, allow_downgrade=True)

    state = _state(dict(b_request.paths), runtime_path)
    assert replaced.exit_status is ExitStatus.OK
    assert state.selected_release_id == c_target.release_id
    assert [selection.release_id for selection in state.selections] == [host_deploy_tests.CURRENT, c_target.release_id]
    assert state.selections[-1].previous_release_id == host_deploy_tests.CURRENT
    assert all(protection.target_release_id != b_target.release_id for protection in state.backup_protections)
