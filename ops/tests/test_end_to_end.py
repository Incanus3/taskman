"""State-changing helper results remain redacted at the CLI boundary."""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import json
import os
import select
import shutil
import subprocess
import sys

import pytest

from taskman_ops.cli import Invocation, main
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import WorkflowResult, register_secret, clear_secrets
from taskman_ops.config import EnvironmentConfig
from taskman_ops.helper_client.package import build_helper_package, build_scheduled_backup_package
from taskman_ops.host_helper.operations import deploy as host_deploy
from taskman_ops.host_protocol import decode_result, encode_request
from taskman_ops.releases.artifacts import DeploymentTarget
from taskman_ops.releases.manifests import ArtifactManifest, VerifiedArtifact
from taskman_ops.remote import CommandResult, UploadReceipt
from taskman_ops.remote import ChangeSet
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository
from taskman_ops.workflows.provision import ProvisionCapabilities, provision
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

    def __init__(self, *, credentials: dict[str, bytes] | None = None, credential_mismatch: bool = False) -> None:
        self.credentials = {} if credentials is None else dict(credentials)
        self.credential_mismatch = credential_mismatch
        self.credential_checks: list[tuple[str, bytes]] = []

    def run(self, args: object, **kwargs: object) -> CommandResult:
        stdin = kwargs.get("stdin")
        if isinstance(args, tuple) and len(args) >= 4 and args[:3] == ("sh", "-ceu", args[2]):
            label = args[3]
            if label == "taskman-credential-authority" and isinstance(stdin, bytes):
                self.credential_checks.append((str(args[2]), stdin))
                path = args[4] if len(args) > 4 else None
                credential = {
                    "/etc/taskman/taskman.env": "runtime",
                    "/etc/taskman/pgpass": "pgpass",
                }.get(path)
                if self.credential_mismatch or (
                    credential is not None
                    and self.credentials
                    and self.credentials.get(credential) != stdin
                ):
                    return CommandResult(1)
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
    value = read_state()
    migrations = tuple(value["migrations"])
    return {
        "state": value["database_state"],
        "applied_migrations": migrations,
        "initial_empty": value["database_state"] == "ready" and not migrations,
    }

def command(argv, **_kwargs):
    value = read_state()
    if argv[:2] == ("systemctl", "stop"):
        value["service_running"] = False
        value["events"].append("service-stop")
    elif argv[:2] == ("systemctl", "start"):
        value["service_running"] = True
        value["events"].append("service-start")
    elif argv[0] == "systemd-run":
        value["migrations"] = value["migration_result"]
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
        current.selected_release_id or current.releases[0].release_id,
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
discover_module.observe_database_state_or_empty = database
discover_module.observe_database_state_or_empty_as_admin = database
discover_module._observe_postgresql_authority = lambda *_args: read_state()["database_state"]
deploy_module.create_validated_backup = backup
deploy_module.run_command = command
services.run_command = command
deploy_module.host_preflight = lambda *_args: None
deploy_module.validate_credentials = lambda *_args: None
discover_module.validate_credentials = lambda *_args: None
deploy_module._taskman_gid = os.getegid
state_module._service_state = lambda include_runtime: "running" if include_runtime and read_state()["service_running"] else "stopped"
deploy_module.verify = verify
def scheduler_facts(_paths):
    value = read_state()
    helper_present = value["scheduler_resources"]["helper"]
    timer_present = value["scheduler_resources"]["timer"]
    return {
        "scheduled_backup_sha256": value["scheduler_sha256"] if helper_present else None,
        "backup_timer_enabled": value["backup_timer_enabled"] if timer_present else False,
        "backup_timer_state": value["backup_timer_state"] if timer_present else "inactive",
    }

deploy_module._scheduler_facts = scheduler_facts
discover_module._scheduler_facts = scheduler_facts
discover_module._scheduler_resources = lambda _paths: read_state()["scheduler_resources"]
backup_helper.observe_backup_timer = lambda **_kwargs: (
    read_state()["backup_timer_enabled"], read_state()["backup_timer_state"]
)
backup_helper._verified_executable_checksum = lambda: read_state()["scheduler_sha256"]
def stop_timer(**_kwargs):
    value = read_state()
    value["backup_timer_state"] = "inactive"
    value["events"].append("scheduler-stop")
    write_state(value)
def start_timer(**_kwargs):
    value = read_state()
    value["backup_timer_state"] = "active"
    value["events"].append("scheduler-start")
    write_state(value)
def wait_backup(*_args, **_kwargs):
    value = read_state()
    value["events"].append("scheduler-wait")
    write_state(value)
def replace_helper(_upload, digest):
    value = read_state()
    value["scheduler_sha256"] = digest
    value["events"].append("scheduler-replace")
    write_state(value)
backup_helper.stop_backup_timer = stop_timer
backup_helper.start_backup_timer = start_timer
backup_helper._wait_for_backup_service = wait_backup
backup_helper._validate_upload = lambda *_args, **_kwargs: None
backup_helper._replace_executable = replace_helper
_select_current = deploy_module.select_current
def select_current(paths, release_id):
    value = read_state()
    value["events"].append("current-swap")
    write_state(value)
    return _select_current(paths, release_id)
deploy_module.select_current = select_current

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
    database_state: str = "ready",
    migrations: tuple[int, ...] = (),
    migration_result: tuple[int, ...] | None = None,
    scheduler_resources: dict[str, bool] | None = None,
    remote: _ControllerRemote | None = None,
) -> tuple[_ControllerRemote, Path]:
    """Keep discovery/admission real while replacing only native and transport edges."""

    from taskman_ops.workflows import deploy as deploy_workflow
    from taskman_ops.workflows import helper, inventory

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    remote = _ControllerRemote() if remote is None else remote
    runtime_path = tmp_path / "isolated-helper-state.json"
    scheduler = build_scheduled_backup_package(tmp_path / "taskman-backup.pyz")
    runtime_path.write_text(json.dumps({
        "backup_count": 0,
        "migrations": list(migrations),
        "migration_result": list(
            (20260905120000,) if migration_result is None else migration_result
        ),
        "database_state": database_state,
        "scheduler_sha256": scheduler.sha256,
        "scheduler_resources": scheduler_resources or {
            "helper": True,
            "service": True,
            "timer": True,
            "environment": True,
        },
        "backup_timer_enabled": True,
        "backup_timer_state": "active",
        "service_running": False,
        "verification": verification,
        "events": [],
        "database_contents": "durable-database-contents",
        "authority_calls": 0,
    }))

    real_run_request = helper.run_request

    def dispatch(_remote: object, request: object, **_kwargs: object) -> object:
        from taskman_ops.errors import ExitStatus, HelperTransportError, OpsError

        result = real_run_request(
            _remote,
            request,
            package=package,
            invoker=lambda _transport, selected, wire_request, **_options: _wire_helper(
                wire_request, selected, runtime_path
            ),
        )
        operation = getattr(request, "operation", None)
        runtime = json.loads(runtime_path.read_text())
        if operation == "provision_authority":
            runtime["authority_calls"] += 1
            drift_after = runtime.get("drift_after_authority_calls")
            if runtime["authority_calls"] == drift_after:
                runtime["scheduler_sha256"] = "f" * 64
            runtime_path.write_text(json.dumps(runtime, sort_keys=True))
        if operation == "genesis" and runtime.get("lose_genesis_result_once"):
            runtime["lose_genesis_result_once"] = False
            runtime_path.write_text(json.dumps(runtime, sort_keys=True))
            raise HelperTransportError(
                OpsError(ExitStatus.REMOTE_PREFLIGHT, "helper", "isolated result lost"),
                helper_entry_dispatched=True,
            )
        return result

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


def _converge_native_provision_writers(runtime_path: Path):
    """Model the native/pyinfra write boundary without replacing provision.

    The controller, authority package, inventory, planning, and genesis remain
    real.  This boundary only supplies the local effects a disposable host
    would normally receive from pyinfra/PostgreSQL: creating confirmed missing
    scheduler resources and making the configured database available.
    """

    def converge(_remote: object, inputs: object) -> ChangeSet:
        scheduler_create = getattr(inputs, "scheduler_create")
        assert isinstance(scheduler_create, frozenset)
        runtime = json.loads(runtime_path.read_text())
        paths = {
            "/usr/local/lib/taskman/taskman-backup.pyz": "helper",
            "/etc/systemd/system/taskman-backup.service": "service",
            "/etc/systemd/system/taskman-backup.timer": "timer",
            "/etc/taskman/taskman-backup.env": "environment",
        }
        for path in scheduler_create:
            runtime["scheduler_resources"][paths[path]] = True
        runtime["database_state"] = "ready"
        if "post_pyinfra_migrations" in runtime:
            runtime["migrations"] = runtime["post_pyinfra_migrations"]
        runtime["native_provision_writes"] = runtime.get("native_provision_writes", 0) + 1
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))
        return ChangeSet(changed=True, operations=("pyinfra", "postgresql", "scheduler"))

    return converge


def _configure_default_public_provision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: object,
    remote: _ControllerRemote,
    runtime_path: Path,
    *,
    archive_name: str,
) -> tuple[EnvironmentConfig, Path, DeploymentTarget]:
    """Run default provision through real authority, planning, and genesis.

    The caller supplies only host-side state.  This fixture deliberately leaves
    the public controller and package entrypoint intact, replacing the native
    host convergence boundary with its isolated stateful equivalent.
    """

    from taskman_ops.host import acceptance as host_acceptance
    from taskman_ops.host.facts import CaddyState, HostFacts
    from taskman_ops.workflows import provision as provision_module

    from taskman_ops.host_protocol import HostRequest

    assert isinstance(request, HostRequest)
    target = _artifact_target(request)
    config = _controller_config(dict(request.paths))
    archive = tmp_path / archive_name
    shutil.copyfile(target.artifact.archive, archive)
    stem = archive.name[: -len(".tar.gz")]
    archive.with_name(f"{stem}.manifest.json").write_text(
        json.dumps(target.manifest.to_mapping(), sort_keys=True)
    )
    archive.with_name(f"{archive.name}.sha256").write_text(
        f"{target.artifact_sha256}  {archive.name}\n"
    )
    facts = HostFacts(
        "ubuntu", "26.04", "amd64", "systemd", True, False, None, config.ssh_port,
        8 * 1024**3, 40 * 1024**3, 40 * 1024**3, (config.public_ipv4,), (), (), (),
        CaddyState.ABSENT, (), (), False, (), (),
    )
    monkeypatch.setattr(host_acceptance, "collect_host_facts", lambda *_args, **_kwargs: facts)
    monkeypatch.setattr(provision_module, "load_environment", lambda _name: config)
    monkeypatch.setattr(
        provision_module,
        "decrypt_secrets",
        lambda _name: SimpleNamespace(database_password="test-password"),
    )
    monkeypatch.setattr(provision_module, "connect", lambda _config: remote)
    monkeypatch.setattr(provision_module, "render_runtime_environment", lambda *_args: b"RUNTIME=value\n")
    monkeypatch.setattr(provision_module, "render_pgpass", lambda *_args: b"pgpass\n")
    monkeypatch.setattr(provision_module, "render_role_password_input", lambda *_args: b"role-password\n")
    monkeypatch.setattr(
        provision_module,
        "build_caddy_plan",
        lambda _config: CaddyPlan(
            CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
            (), ("caddy",), "taskman.example.test {\n}\n",
        ),
    )
    monkeypatch.setattr(
        provision_module,
        "converge_provisioning",
        _converge_native_provision_writers(runtime_path),
    )
    return config, archive, target


def _default_provision(archive: Path, *, migration_policy: str = "backward-compatible") -> Invocation:
    return Invocation(
        command="provision",
        environment="production",
        yes=True,
        artifact=archive,
        migration_policy=migration_policy,
    )


def test_default_public_provision_refreshes_an_existing_scheduler_under_genesis_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing pause/wait/replace/resume would overwrite a live scheduler unsafely."""

    request = host_deploy_tests._request(tmp_path / "scheduler-refresh", operation="genesis", previous=None)
    remote, runtime_path = _install_public_controller(
        monkeypatch,
        tmp_path,
        verification="passing",
    )
    _config, archive, target = _configure_default_public_provision(
        monkeypatch, tmp_path, request, remote, runtime_path, archive_name="scheduler-refresh.tar.gz"
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["scheduler_sha256"] = "e" * 64
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    result = provision(_default_provision(archive))

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, result.facts
    assert _state(dict(request.paths), runtime_path).selected_release_id == target.release_id
    assert runtime["native_provision_writes"] == 1
    assert runtime["scheduler_sha256"] != "e" * 64
    assert runtime["events"].index("scheduler-stop") < runtime["events"].index("scheduler-wait")
    assert runtime["events"].index("scheduler-wait") < runtime["events"].index("scheduler-replace")
    assert runtime["events"].index("scheduler-replace") < runtime["events"].index("scheduler-start")


def test_default_public_partial_scheduler_never_starts_an_old_helper_before_locked_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent timer must not launch the existing helper during generic convergence."""

    request = host_deploy_tests._request(tmp_path / "partial-scheduler", operation="genesis", previous=None)
    remote, runtime_path = _install_public_controller(
        monkeypatch,
        tmp_path,
        verification="passing",
        scheduler_resources={"helper": True, "service": True, "timer": False, "environment": True},
    )
    _config, archive, target = _configure_default_public_provision(
        monkeypatch, tmp_path, request, remote, runtime_path, archive_name="partial-scheduler.tar.gz"
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["scheduler_sha256"] = "e" * 64
    runtime["backup_timer_enabled"] = False
    runtime["backup_timer_state"] = "inactive"
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    result = provision(_default_provision(archive))

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, (result.facts, runtime["events"])
    assert _state(dict(request.paths), runtime_path).selected_release_id == target.release_id
    assert runtime["scheduler_resources"] == {
        "helper": True, "service": True, "timer": True, "environment": True,
    }
    assert runtime["events"][:3] == ["scheduler-stop", "scheduler-wait", "scheduler-replace"]
    assert runtime["events"].index("scheduler-replace") < runtime["events"].index("current-swap")
    assert "scheduler-start" not in runtime["events"]


def test_default_public_provision_refuses_unattended_post_confirmation_authority_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the second authority comparison would let --yes mutate a changed host."""

    request = host_deploy_tests._request(tmp_path / "authority-drift", operation="genesis", previous=None)
    remote, runtime_path = _install_public_controller(monkeypatch, tmp_path, verification="passing")
    _config, archive, _target = _configure_default_public_provision(
        monkeypatch, tmp_path, request, remote, runtime_path, archive_name="authority-drift.tar.gz"
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["drift_after_authority_calls"] = 1
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    result = provision(_default_provision(archive))

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.SAFETY
    assert runtime.get("native_provision_writes", 0) == 0
    assert runtime["events"] == []


def test_default_public_provision_records_physical_failed_predecessor_before_equal_schema_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting the stop or physical predecessor would make same-schema recovery unsafe."""

    b_request = host_deploy_tests._request(
        tmp_path / "physical-b", operation="genesis", previous=None, migrations=(host_deploy_tests.MIGRATION,)
    )
    c_request = replace(
        host_deploy_tests._request(
            tmp_path / "physical-c", operation="genesis", previous=None,
            migrations=(host_deploy_tests.MIGRATION,), application_version="0.3.0",
        ),
        paths=b_request.paths,
    )
    host_deploy_tests._install_unselected_candidate(b_request)
    b_target = _artifact_target(b_request)
    paths = host_deploy.ManagedPaths.from_mapping(dict(b_request.paths))
    install = Path(paths.local(paths.install_root))
    (install / "current").symlink_to(install / "releases" / b_target.release_id)
    remote, runtime_path = _install_public_controller(
        monkeypatch,
        tmp_path,
        verification="passing",
        migrations=(20260905120000,),
        migration_result=(20260905120000,),
    )
    _config, archive, c_target = _configure_default_public_provision(
        monkeypatch, tmp_path, c_request, remote, runtime_path, archive_name="physical-c.tar.gz"
    )

    result = provision(_default_provision(archive, migration_policy="no-change"))

    state = _state(dict(b_request.paths), runtime_path)
    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, result.facts
    assert state.selected_release_id == c_target.release_id
    assert len(state.selections) == 1
    assert state.selections[0].previous_release_id is None
    assert state.selections[0].observed_previous_release_id == b_target.release_id
    assert runtime["events"].index("service-stop") < runtime["events"].index("current-swap")


def test_default_public_provision_replays_only_the_exact_completed_lost_genesis_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treating a lost result as a fresh install would duplicate or invent history."""

    request = host_deploy_tests._request(tmp_path / "lost-genesis", operation="genesis", previous=None)
    remote, runtime_path = _install_public_controller(monkeypatch, tmp_path, verification="passing")
    _config, archive, target = _configure_default_public_provision(
        monkeypatch, tmp_path, request, remote, runtime_path, archive_name="lost-genesis.tar.gz"
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["lose_genesis_result_once"] = True
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    lost = provision(_default_provision(archive))
    recovered = provision(_default_provision(archive))

    state = _state(dict(request.paths), runtime_path)
    assert lost.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert lost.facts["mutation_state"] == "changed"
    assert recovered.exit_status is ExitStatus.OK, recovered.facts
    assert state.selected_release_id == target.release_id
    assert [selection.release_id for selection in state.selections] == [target.release_id]
    assert state.selections[0].previous_release_id is None
    assert state.backup_protections == ()

    changed_request = replace(
        host_deploy_tests._request(
            tmp_path / "lost-genesis-changed", operation="genesis", previous=None,
            application_version="0.3.0",
        ),
        paths=request.paths,
    )
    _changed_config, changed_archive, _changed_target = _configure_default_public_provision(
        monkeypatch, tmp_path, changed_request, remote, runtime_path, archive_name="lost-genesis-changed.tar.gz"
    )
    changed = provision(_default_provision(changed_archive))

    assert changed.exit_status is ExitStatus.SAFETY
    assert [selection.release_id for selection in _state(dict(request.paths), runtime_path).selections] == [target.release_id]


def test_default_public_provision_preserves_existing_credentials_and_database_before_partial_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing admitted credentials or data would destroy partial-recovery authority."""

    request = host_deploy_tests._request(
        tmp_path / "credential-recovery", operation="genesis", previous=None,
        applied_migrations=(20260905120000,),
        migrations=(host_deploy_tests.MIGRATION, host_deploy_tests.SECOND_MIGRATION),
    )
    host_deploy_tests._install_unselected_candidate(request)
    preserved = {"runtime": b"RUNTIME=value\n", "pgpass": b"pgpass\n"}
    remote = _ControllerRemote(credentials=preserved)
    remote, runtime_path = _install_public_controller(
        monkeypatch,
        tmp_path,
        remote=remote,
        verification="passing",
        migrations=(20260905120000,),
        migration_result=(20260905120000, 20260906120000),
    )
    _config, archive, _target = _configure_default_public_provision(
        monkeypatch, tmp_path, request, remote, runtime_path, archive_name="credential-recovery.tar.gz"
    )
    before = json.loads(runtime_path.read_text())

    result = provision(_default_provision(archive))

    after = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, result.facts
    assert remote.credentials == preserved
    assert {content for _script, content in remote.credential_checks} == set(preserved.values())
    assert after["database_contents"] == before["database_contents"]


def test_default_public_provision_refuses_credential_mismatch_before_partial_recovery_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping credential admission would permit replacement before recovery is proved safe."""

    preserved = {"runtime": b"RUNTIME=value\n", "pgpass": b"pgpass\n"}
    mismatch_remote = _ControllerRemote(credentials=preserved, credential_mismatch=True)
    mismatch_remote, mismatch_state = _install_public_controller(
        monkeypatch,
        tmp_path,
        remote=mismatch_remote,
        verification="passing",
        migrations=(20260905120000,),
        migration_result=(20260905120000, 20260906120000),
    )
    mismatch_request = host_deploy_tests._request(
        tmp_path / "mismatch-request", operation="genesis", previous=None,
        applied_migrations=(20260905120000,),
        migrations=(host_deploy_tests.MIGRATION, host_deploy_tests.SECOND_MIGRATION),
    )
    host_deploy_tests._install_unselected_candidate(mismatch_request)
    _mismatch_config, mismatch_archive, _mismatch_target = _configure_default_public_provision(
        monkeypatch, tmp_path, mismatch_request, mismatch_remote, mismatch_state,
        archive_name="credential-mismatch.tar.gz",
    )
    mismatch_before = json.loads(mismatch_state.read_text())

    refused = provision(_default_provision(mismatch_archive))

    mismatch_after = json.loads(mismatch_state.read_text())
    assert refused.exit_status is ExitStatus.SAFETY
    assert mismatch_after.get("native_provision_writes", 0) == 0
    assert mismatch_after["events"] == []
    assert mismatch_after["database_contents"] == mismatch_before["database_contents"]


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


def test_public_provision_executes_genesis_through_the_packaged_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provision reaches real genesis after its bounded local convergence seam."""

    from taskman_ops.workflows.deploy import deploy_first_release

    request = host_deploy_tests._request(tmp_path / "genesis", operation="genesis", previous=None)
    remote, runtime_path = _install_public_controller(monkeypatch, tmp_path, verification="passing")
    target = _artifact_target(request)
    config = _controller_config(dict(request.paths))
    capabilities = ProvisionCapabilities(
        load_environment=lambda _name: config,
        decrypt_secrets=lambda _name: SimpleNamespace(database_password="test-password"),
        resolve_artifact=lambda _invocation: target.artifact,
        render_runtime_environment=lambda _config, _secrets: b"RUNTIME=value\n",
        render_pgpass=lambda _config, _secrets: b"pgpass\n",
        render_plan=lambda _config, artifact: {"candidate_release_id": artifact.manifest.release_id},
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
        connect=lambda _config: remote,
        discover=lambda _remote, _config, **_kwargs: {"admission": "isolated"},
        render_role_password_input=lambda _role, _password: b"role-password-input\n",
        provisioning=lambda _remote, _inputs: ChangeSet(changed=False),
        caddy_plan=lambda _config: CaddyPlan(
            CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
            (),
            ("caddy",),
            "taskman.example.test {\n}\n",
        ),
        genesis=deploy_first_release,
    )

    result = provision(
        Invocation(command="provision", environment="production", yes=True),
        capabilities=capabilities,
    )

    state = _state(dict(request.paths), runtime_path)
    assert result.exit_status is ExitStatus.OK, result.facts
    assert result.command == "provision"
    assert state.selected_release_id == target.release_id
    assert len(state.selections) == 1
    assert state.selections[0].previous_release_id is None
    assert state.selections[0].observed_previous_release_id is None


def test_default_public_provision_creates_confirmed_absent_scheduler_through_packaged_genesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh timer must cross default authority, writers, and genesis intact."""

    from taskman_ops.host import acceptance as host_acceptance
    from taskman_ops.host.facts import CaddyState, HostFacts
    from taskman_ops.workflows import provision as provision_module

    request = host_deploy_tests._request(tmp_path / "default-genesis", operation="genesis", previous=None)
    remote, runtime_path = _install_public_controller(
        monkeypatch,
        tmp_path,
        verification="passing",
        database_state="absent",
        migration_result=(20260905120000,),
        scheduler_resources={
            "helper": False,
            "service": False,
            "timer": False,
            "environment": False,
        },
    )
    target = _artifact_target(request)
    config = _controller_config(dict(request.paths))
    archive = tmp_path / "local-artifact.tar.gz"
    shutil.copyfile(target.artifact.archive, archive)
    archive.with_name("local-artifact.manifest.json").write_text(
        json.dumps(target.manifest.to_mapping(), sort_keys=True)
    )
    archive.with_name("local-artifact.tar.gz.sha256").write_text(
        f"{target.artifact_sha256}  {archive.name}\n"
    )
    facts = HostFacts(
        "ubuntu", "26.04", "amd64", "systemd", True, False, None, config.ssh_port,
        8 * 1024**3, 40 * 1024**3, 40 * 1024**3, (config.public_ipv4,), (), (), (),
        CaddyState.ABSENT, (), (), False, (), (),
    )
    monkeypatch.setattr(host_acceptance, "collect_host_facts", lambda *_args, **_kwargs: facts)
    monkeypatch.setattr(provision_module, "load_environment", lambda _name: config)
    monkeypatch.setattr(
        provision_module,
        "decrypt_secrets",
        lambda _name: SimpleNamespace(database_password="test-password"),
    )
    monkeypatch.setattr(provision_module, "connect", lambda _config: remote)
    monkeypatch.setattr(provision_module, "render_runtime_environment", lambda *_args: b"RUNTIME=value\n")
    monkeypatch.setattr(provision_module, "render_pgpass", lambda *_args: b"pgpass\n")
    monkeypatch.setattr(provision_module, "render_role_password_input", lambda *_args: b"role-password\n")
    monkeypatch.setattr(
        provision_module,
        "build_caddy_plan",
        lambda _config: CaddyPlan(
            CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
            (), ("caddy",), "taskman.example.test {\n}\n",
        ),
    )
    monkeypatch.setattr(
        provision_module,
        "converge_provisioning",
        _converge_native_provision_writers(runtime_path),
    )

    result = provision(Invocation(
        command="provision", environment="production", yes=True, artifact=archive
    ))

    state = _state(dict(request.paths), runtime_path)
    assert result.exit_status is ExitStatus.OK, result.facts
    assert state.selected_release_id == target.release_id
    assert len(state.selections) == 1
    assert state.selections[0].previous_release_id is None
    runtime = json.loads(runtime_path.read_text())
    assert runtime["native_provision_writes"] == 1
    assert runtime["scheduler_resources"] == {
        "helper": True,
        "service": True,
        "timer": True,
        "environment": True,
    }


def test_default_public_fresh_database_refuses_post_pyinfra_schema_drift_before_genesis_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating PostgreSQL cannot reauthorize a changed schema after confirmation."""

    request = host_deploy_tests._request(tmp_path / "fresh-post-pyinfra-drift", operation="genesis", previous=None)
    remote, runtime_path = _install_public_controller(
        monkeypatch,
        tmp_path,
        verification="passing",
        database_state="absent",
        migration_result=(20260905120000,),
        scheduler_resources={"helper": False, "service": False, "timer": False, "environment": False},
    )
    _config, archive, _target = _configure_default_public_provision(
        monkeypatch, tmp_path, request, remote, runtime_path, archive_name="fresh-post-pyinfra-drift.tar.gz"
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["post_pyinfra_migrations"] = [20260905120000]
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    refused = provision(_default_provision(archive))

    runtime = json.loads(runtime_path.read_text())
    assert refused.exit_status is ExitStatus.SAFETY
    assert runtime["native_provision_writes"] == 1
    assert runtime["events"] == []


def test_default_public_provision_recovers_a_partial_schema_with_null_baseline_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing genesis's null-baseline backup or prune authority breaks recovery."""

    from taskman_ops.host import acceptance as host_acceptance
    from taskman_ops.host.facts import CaddyState, HostFacts
    from taskman_ops.workflows import provision as provision_module

    request = host_deploy_tests._request(
        tmp_path / "partial-genesis",
        operation="genesis",
        previous=None,
        applied_migrations=(20260905120000,),
        migrations=(host_deploy_tests.MIGRATION, host_deploy_tests.SECOND_MIGRATION),
    )
    host_deploy_tests._install_unselected_candidate(request)
    remote, runtime_path = _install_public_controller(
        monkeypatch,
        tmp_path,
        verification="passing",
        migrations=(20260905120000,),
        migration_result=(20260905120000, 20260906120000),
    )
    target = _artifact_target(request)
    config = _controller_config(dict(request.paths))
    archive = tmp_path / "partial-artifact.tar.gz"
    shutil.copyfile(target.artifact.archive, archive)
    archive.with_name("partial-artifact.manifest.json").write_text(
        json.dumps(target.manifest.to_mapping(), sort_keys=True)
    )
    archive.with_name("partial-artifact.tar.gz.sha256").write_text(
        f"{target.artifact_sha256}  {archive.name}\n"
    )
    facts = HostFacts(
        "ubuntu", "26.04", "amd64", "systemd", True, False, None, config.ssh_port,
        8 * 1024**3, 40 * 1024**3, 40 * 1024**3, (config.public_ipv4,), (), (), (),
        CaddyState.ABSENT, (), (), False, (), (),
    )
    monkeypatch.setattr(host_acceptance, "collect_host_facts", lambda *_args, **_kwargs: facts)
    monkeypatch.setattr(provision_module, "load_environment", lambda _name: config)
    monkeypatch.setattr(provision_module, "decrypt_secrets", lambda _name: SimpleNamespace(database_password="test-password"))
    monkeypatch.setattr(provision_module, "connect", lambda _config: remote)
    monkeypatch.setattr(provision_module, "render_runtime_environment", lambda *_args: b"RUNTIME=value\n")
    monkeypatch.setattr(provision_module, "render_pgpass", lambda *_args: b"pgpass\n")
    monkeypatch.setattr(provision_module, "render_role_password_input", lambda *_args: b"role-password\n")
    monkeypatch.setattr(provision_module, "build_caddy_plan", lambda _config: CaddyPlan(
        CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
        (), ("caddy",), "taskman.example.test {\n}\n",
    ))
    monkeypatch.setattr(provision_module, "converge_provisioning", _converge_native_provision_writers(runtime_path))

    result = provision(Invocation(
        command="provision",
        environment="production",
        yes=True,
        artifact=archive,
        migration_policy="backward-compatible",
    ))

    state = _state(dict(request.paths), runtime_path)
    assert result.exit_status is ExitStatus.OK, result.facts
    assert state.applied_migrations == (20260905120000, 20260906120000)
    assert len(state.selections) == 1
    assert state.selections[0].previous_release_id is None
    assert state.selections[0].observed_previous_release_id is None
    assert state.backups
