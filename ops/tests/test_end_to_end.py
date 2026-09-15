"""State-changing helper results remain redacted at the CLI boundary."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from io import StringIO
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import hashlib
import json
import os
import select
import shutil
import subprocess
import sys

import pytest

from taskman_ops.cli import Invocation, main
from taskman_ops.errors import ExitStatus, HelperTransportError, OpsError
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
from taskman_ops.workflows.helper import mutable
from taskman_ops.workflows.provision import ProvisionCapabilities, provision
from tests.host_helper import test_cleanup as host_cleanup_tests
from tests.host_helper import test_deploy as host_deploy_tests
from tests.host_helper import test_restore as host_restore_tests
from tests.support.environments import valid_environment
from tests.workflows.test_operational_preflight import _managed_host_facts


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
        self._root_staging = Path("/tmp") / f"taskman-e2e-run-{id(self)}"

    def _local_path(self, value: object) -> Path:
        path = Path(str(value))
        try:
            relative = path.relative_to("/run/taskman-ops")
        except ValueError:
            return path
        return self._root_staging / relative

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
        if args == ("id", "-u"):
            return CommandResult(0, f"{os.geteuid()}\n")
        if args == ("id", "-g"):
            return CommandResult(0, f"{os.getegid()}\n")
        if isinstance(args, tuple) and args[:4] == ("mkdir", "-m", "700", "--"):
            try:
                self._local_path(args[4]).mkdir(mode=0o700)
            except FileExistsError:
                return CommandResult(1)
            return CommandResult(0)
        if isinstance(args, tuple) and args[:3] == ("stat", "-c", "%u:%g:%a:%F"):
            path = self._local_path(args[-1])
            details = path.stat()
            kind = "directory" if path.is_dir() else "regular file"
            root_staging = Path(str(args[-1])).is_relative_to("/run/taskman-ops")
            uid = 0 if root_staging else details.st_uid
            gid = 0 if root_staging else details.st_gid
            return CommandResult(
                0,
                f"{uid}:{gid}:{details.st_mode & 0o777:o}:{kind}\n",
            )
        if isinstance(args, tuple) and args[:2] == ("sha256sum", "--"):
            path = self._local_path(args[-1])
            return CommandResult(
                0,
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {args[-1]}\n",
            )
        if isinstance(args, tuple) and args[:2] == ("install", "-o"):
            source = self._local_path(args[-2])
            destination = self._local_path(args[-1])
            shutil.copyfile(source, destination)
            destination.chmod(0o500)
            return CommandResult(0)
        if isinstance(args, tuple) and args[:2] == ("rm", "--"):
            self._local_path(args[-1]).unlink()
            return CommandResult(0)
        if isinstance(args, tuple) and args[:2] == ("rmdir", "--"):
            self._local_path(args[-1]).rmdir()
            return CommandResult(0)
        if (
            isinstance(args, tuple)
            and len(args) == 10
            and args[:4] == ("sudo", "--preserve-env=SSH_CONNECTION", "--", "python3")
            and args[5] == "provision-pgpass-authority"
            and isinstance(stdin, bytes)
        ):
            self.credential_checks.append(("packaged-pgpass-authority", stdin))
            if self.credential_mismatch or (
                self.credentials and self.credentials.get("pgpass") != stdin
            ):
                return CommandResult(10)
            harness = r'''
import runpy
import subprocess
import sys

archive, *arguments = sys.argv[1:]
sys.path.insert(0, archive)
from taskman_ops.host_helper.operations import preflight
preflight.run_command = lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, b"", b"")
sys.argv = [archive, *arguments]
runpy.run_path(archive, run_name="__main__")
'''
            completed = subprocess.run(
                [
                    sys.executable, "-I", "-S", "-c", harness,
                    self._local_path(args[4]).as_posix(), *map(str, args[5:]),
                ],
                input=stdin,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return CommandResult(completed.returncode)
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


def test_public_cleanup_executes_controller_plan_through_packaged_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The public cleanup path keeps paging, confirmation, and host deletion on the v3 wire."""

    from taskman_ops.workflows import cleanup as cleanup_workflow
    from taskman_ops.workflows import helper

    paths = host_cleanup_tests._seed(tmp_path / "public-cleanup")
    config = _controller_config(
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        }
    )
    package = build_helper_package(tmp_path / "taskman-cleanup-host.pyz")
    runtime_path = tmp_path / "isolated-cleanup-state.json"
    runtime_path.write_text("{}")
    remote = _ControllerRemote()
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

    monkeypatch.setattr(cleanup_workflow, "validate_cleanup_preflight", lambda *_args: object())
    monkeypatch.setattr(cleanup_workflow, "run_request", dispatch)

    result = cleanup_workflow.cleanup(remote, config, confirm=lambda _plan: True)

    assert result.stage == "cleaned"
    assert result.facts["starting_state"] is not None
    assert result.facts["completed_targets"]
    assert not Path(paths.local(paths.release_root / host_cleanup_tests.STALE_RELEASE)).exists()


@pytest.mark.parametrize(
    "scenario",
    ("low_capacity", "unavailable_database", "mismatched_selection", "unfinished_genesis"),
)
def test_public_cleanup_recovery_matrix_uses_real_filesystem_only_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> None:
    """Recovery-state cleanup must reach the packaged helper without a DB/capacity probe."""

    from taskman_ops.workflows import cleanup as cleanup_workflow
    from taskman_ops.workflows import helper, operational_preflight

    if scenario == "unfinished_genesis":
        paths = host_cleanup_tests.managed_paths(tmp_path / scenario)
        host_cleanup_tests._release(
            paths,
            host_cleanup_tests.RELEASE,
            host_cleanup_tests.CURRENT_REVISION,
            host_cleanup_tests.CURRENT_DIGEST,
        )
        root = Path(paths.local(paths.backup_root))
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / ("backup-" + "f" * 32 + ".dump")
        temporary.write_bytes(b"partial")
        temporary.chmod(0o600)
    else:
        paths = host_cleanup_tests._seed(tmp_path / scenario)
        if scenario == "mismatched_selection":
            Path(paths.local(paths.current_link)).unlink()
            temporary = Path(paths.local(paths.backup_root)) / (
                "backup-" + "e" * 32 + ".dump"
            )
            temporary.write_bytes(b"partial")
            temporary.chmod(0o600)

    config = _controller_config(
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        }
    )
    facts = _managed_host_facts()
    if scenario == "low_capacity":
        facts = replace(
            facts,
            available_disk_bytes=0,
            backup_available_disk_bytes=0,
            failed_checks=("install-root disk", "backup-root disk"),
        )
    elif scenario == "unavailable_database":
        facts = replace(facts, existing_databases=())

    class CleanupRemote(_ControllerRemote):
        def facts(self):
            return facts

        def run(self, *_args: object, **_kwargs: object) -> CommandResult:
            pytest.fail("cleanup preflight must not run database or capacity commands")

    def forbidden(*_args: object, **_kwargs: object):
        pytest.fail("cleanup must not use operational database or capacity preflight")

    monkeypatch.setattr(operational_preflight, "collect_operational_preflight", forbidden)
    monkeypatch.setattr(operational_preflight, "validate_restore_preflight", forbidden)
    monkeypatch.setattr(operational_preflight, "validate_restore_inspection_preflight", forbidden)
    package = build_helper_package(tmp_path / f"taskman-cleanup-{scenario}.pyz")
    runtime_path = tmp_path / f"isolated-cleanup-{scenario}.json"
    runtime_path.write_text("{}")
    remote = CleanupRemote()
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

    monkeypatch.setattr(cleanup_workflow, "run_request", dispatch)
    result = cleanup_workflow.cleanup(remote, config, confirm=lambda _plan: True)

    assert result.stage == "cleaned"
    assert result.facts["completed_targets"]


def test_public_cleanup_partial_batch_keeps_proved_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A packaged same-batch failure must retain the earlier native deletion as changed."""

    from taskman_ops.workflows import cleanup as cleanup_workflow
    from taskman_ops.workflows import helper

    paths = host_cleanup_tests._seed(tmp_path / "partial-cleanup")
    for index in range(3):
        revision = f"{index + 32:040x}"
        digest = f"{index + 32:064x}"
        release_id = host_cleanup_tests.build_release_id(
            f"3.0.{index}", revision, artifact_sha256=digest, source_dirty=False
        )
        host_cleanup_tests._release(paths, release_id, revision, digest)
    config = _controller_config(
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        }
    )

    class CleanupRemote(_ControllerRemote):
        def facts(self):
            return _managed_host_facts()

        def run(self, *_args: object, **_kwargs: object) -> CommandResult:
            pytest.fail("cleanup preflight must not run database or capacity commands")

    package = build_helper_package(tmp_path / "taskman-cleanup-partial.pyz")
    runtime_path = tmp_path / "isolated-cleanup-partial.json"
    runtime_path.write_text(json.dumps({"cleanup_fail_delete_at": 2}))
    remote = CleanupRemote()
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

    monkeypatch.setattr(cleanup_workflow, "run_request", dispatch)
    result = cleanup_workflow.cleanup(remote, config, confirm=lambda _plan: True)

    assert result.exit_status is ExitStatus.SAFETY
    assert result.changed is True
    assert result.facts["mutation_state"] == "changed"
    assert len(result.facts["completed_targets"]) == 1
    assert set(result.facts["unavailable_fields"]) == {
        "selected_release_id",
        "last_successful_selection_id",
        "backup_protection_sha256",
        "restore_target_sha256",
    }


_ISOLATED_HELPER_HARNESS = r'''
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from dataclasses import replace

archive, runtime_path = map(Path, sys.argv[1:3])
ready_fd = int(sys.argv[3])
sys.path.insert(0, archive.as_posix())

from taskman_ops.host_helper import __main__ as entrypoint
from taskman_ops.host_helper import backup_helper, services, state as state_module
from taskman_ops.host_helper import restore_database as restore_database_module
from taskman_ops.host_helper.operations import deploy as deploy_module
from taskman_ops.host_helper.operations import cleanup as cleanup_module
from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper.operations import restore as restore_module
from taskman_ops.host_helper.records import BackupRecord, write_backup_manifest
from taskman_ops.host_helper.restore_target import replace_restore_target
from taskman_ops.host_helper.commands import CommandError
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
    if (
        argv[0] == "pg_restore"
        and value.get("invalid_backup_list_id") == Path(argv[-1]).stem
    ):
        raise CommandError("injected pg_restore list failure")
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
    value["events"].append("safety-backup")
    if value.get("safety_failure"):
        write_state(value)
        raise OSError("injected safety backup failure")
    value["backup_count"] += 1
    dump = Path(paths.local(paths.backup_root / f"backup-{value['backup_count']:032x}.dump"))
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_bytes(b"archive-backup")
    dump.chmod(0o600)
    record = BackupRecord(
        f"backup-{value['backup_count']:032x}",
        __import__("datetime").datetime(2026, 9, 7, 12, tzinfo=__import__("datetime").UTC)
        + __import__("datetime").timedelta(
            minutes=(
                -value["backup_count"]
                if value.get("backup_clock_rollback")
                else value["backup_count"]
            )
        ),
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
discover_module._validate_managed_resources = lambda *_args, **_kwargs: None
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
restore_module._scheduler_facts = scheduler_facts
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
    if "scheduler_running" in value:
        value["events"].append("old-scheduler-finished")
        value["scheduler_running"] = False
    write_state(value)
    if value.get("scheduler_failure"):
        raise CommandError("scheduled backup wait interrupted")
def replace_helper(_upload, digest):
    value = read_state()
    assert not value.get("scheduler_running", False)
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

def restore_databases(*_args, **_kwargs):
    value = read_state()
    return value["restore_databases"]

def create_temporary(*_args, **_kwargs):
    value = read_state()
    value["events"].append("temporary-created")
    value["next_restore_oid"] += 1
    value["restore_databases"]["temporary"] = {
        "oid": value["next_restore_oid"],
        "owner": "taskman",
        "migration_table_present": False,
        "applied_migrations": None,
    }
    write_state(value)

_register_restored_database = restore_database_module.register_restored_database
def prove_temporary_empty(_database, _credentials):
    value = read_state()
    temporary = value["restore_databases"]["temporary"]
    assert temporary is not None and temporary["migration_table_present"] is False
    if value.get("temporary_active_writers"):
        raise restore_database_module.RestoreDatabaseError(
            "restore temporary database has active writers"
        )
    if value.get("temporary_empty_proof_failure"):
        raise restore_database_module.RestoreDatabaseError(
            "restore temporary database is not empty"
        )
    return temporary["oid"]
restore_database_module.prove_temporary_database_empty = prove_temporary_empty

def register_temporary(paths, target, database, credentials):
    updated = _register_restored_database(paths, target, database, credentials)
    value = read_state()
    value["events"].append("temporary-registered")
    write_state(value)
    return updated

def load_temporary(target, *_args, **_kwargs):
    value = read_state()
    assert not target.temporary_creation_pending
    assert value["restore_databases"]["temporary"]["oid"] == target.restored_database_oid
    value["events"].append("dump-loaded")
    value["events"].append(f"dump-loaded:{target.backup_id}")
    value["restore_databases"]["temporary"].update(
        migration_table_present=True,
        applied_migrations=value["migrations"],
    )
    write_state(value)

def rename_database(_database, _credentials, source, destination, oid):
    value = read_state()
    record = value["restore_databases"][source]
    assert record is not None and record["oid"] == oid
    assert value["restore_databases"][destination] is None
    value["events"].append(f"rename:{source}:{destination}")
    value["restore_databases"][destination] = record
    value["restore_databases"][source] = None
    write_state(value)

def drop_retired(_database, _credentials, oid):
    value = read_state()
    retired = value["restore_databases"]["retired"]
    if retired is not None:
        assert retired["oid"] == oid
        value["events"].append("retired-dropped")
        value["restore_databases"]["retired"] = None
        write_state(value)

def begin_rebuild(paths, target):
    value = read_state()
    value["events"].append("rebuild-pending")
    write_state(value)
    updated = replace(target, temporary_creation_pending=True)
    replace_restore_target(paths, updated)
    return updated

def drop_temporary(_database, _credentials, oid):
    value = read_state()
    temporary = value["restore_databases"]["temporary"]
    if temporary is not None:
        assert temporary["oid"] == oid
        value["events"].append("temporary-dropped")
        value["events"].append(f"temporary-dropped:{oid}")
        value["restore_databases"]["temporary"] = None
        write_state(value)

def drop_restored(_database, _credentials, role, oid):
    value = read_state()
    assert role in {"canonical", "temporary"}
    restored = value["restore_databases"][role]
    if restored is not None:
        assert restored["oid"] == oid
        if role == "canonical":
            assert value["restore_databases"]["retired"] is not None
        value["events"].append(f"restored-dropped:{role}:{oid}")
        value["restore_databases"][role] = None
        write_state(value)

_write_restore_target = restore_module.write_restore_target
def write_binding(paths, target):
    value = read_state()
    value["events"].append("binding-published")
    write_state(value)
    return _write_restore_target(paths, target)

_remove_restore_target = restore_module.remove_restore_target
def remove_binding(paths):
    value = read_state()
    value["events"].append("binding-removed")
    write_state(value)
    return _remove_restore_target(paths)

discover_module.observe_restore_databases = restore_databases
restore_database_module.observe_restore_databases = restore_databases
restore_database_module._oid_present = lambda _database, oid: any(
    value is not None and value["oid"] == oid
    for value in read_state()["restore_databases"].values()
)
discover_module.run_command = command
restore_module.validate_credentials = lambda *_args: None
restore_module.observe_restore_databases = restore_databases
restore_module.observe_database_available_bytes = lambda *_args: read_state()["database_capacity"]
restore_module.create_validated_backup = backup
restore_module.create_temporary_database = create_temporary
restore_module.register_restored_database = register_temporary
restore_module.begin_temporary_rebuild = begin_rebuild
restore_module.drop_registered_temporary = drop_temporary
restore_module.drop_registered_restored = drop_restored
restore_module.load_registered_temporary = load_temporary
restore_module.rename_registered_database = rename_database
restore_module.drop_registered_retired = drop_retired
restore_module.write_restore_target = write_binding
restore_module.remove_restore_target = remove_binding
restore_module.run_command = command
restore_module._terminate_connections = lambda *_args: None
restore_module.verify = verify

_replace_restore_target = restore_module.replace_restore_target
def replace_target(paths, target):
    value = read_state()
    _replace_restore_target(paths, target)
    if value.get("lose_registration_reply"):
        value["lose_registration_reply"] = False
        value["events"].append("registration-reply-lost")
        write_state(value)
        raise OSError("injected registration reply loss")

_retire_safety_attempts = restore_module.retire_safety_attempts
def retire_attempts(paths, target, confirmed, **kwargs):
    updated = _retire_safety_attempts(paths, target, confirmed, **kwargs)
    value = read_state()
    value["events"].append("safety-references-retired")
    if value.get("lose_retirement_reply"):
        value["lose_retirement_reply"] = False
        write_state(value)
        raise OSError("injected retirement reply loss")
    write_state(value)
    return updated

_delete_completed_backup = restore_module.delete_completed_backup
def delete_backup(paths, record):
    value = read_state()
    if value.get("interrupt_backup_deletion"):
        value["interrupt_backup_deletion"] = False
        value["events"].append("backup-deletion-interrupted")
        write_state(value)
        raise OSError("injected backup deletion interruption")
    return _delete_completed_backup(paths, record)

restore_module.replace_restore_target = replace_target
restore_module.retire_safety_attempts = retire_attempts
restore_module.delete_completed_backup = delete_backup

_cleanup_delete = cleanup_module._delete_target
def cleanup_delete(target, state, paths):
    value = read_state()
    value["cleanup_delete_count"] = value.get("cleanup_delete_count", 0) + 1
    write_state(value)
    if value.get("cleanup_fail_delete_at") == value["cleanup_delete_count"]:
        raise OSError("injected cleanup deletion failure")
    return _cleanup_delete(target, state, paths)
cleanup_module._delete_target = cleanup_delete

os.write(ready_fd, b"R")
os.close(ready_fd)
sys.argv = [archive.as_posix()]
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


def _install_public_restore_controller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    scheduler_failure: bool,
    state_family: str = "initial",
    first_success: bool = False,
    current_present: bool = True,
) -> tuple[EnvironmentConfig, Path, object, _ControllerRemote, Path]:
    """Keep public restore and the packaged helper real; replace only native effects."""

    from taskman_ops.workflows import helper, inventory
    from taskman_ops.workflows import restore as restore_workflow
    from taskman_ops.workflows.operational_preflight import RestorePreflightFacts

    from taskman_ops.host_helper.backup_protection import (
        BackupProtection,
        write_backup_protection,
    )
    from taskman_ops.host_helper.records import SelectionRecord, append_selection
    from taskman_ops.host_helper.restore_target import replace_restore_target

    paths, source = host_restore_tests._seed(tmp_path / "public-restore")
    if first_success:
        selection_root = Path(paths.local(paths.selection_root))
        for selection in selection_root.glob("*.json"):
            selection.unlink()
        current = Path(paths.local(paths.current_link))
        if not current_present:
            current.unlink()
        protection = host_restore_tests._backup(
            paths,
            host_restore_tests.PROTECTION_BACKUP,
            host_restore_tests.CURRENT,
            b"unresolved null-baseline protection",
        )
        write_backup_protection(
            paths,
            BackupProtection(
                1,
                protection.backup_id,
                None,
                host_restore_tests.TARGET,
                0,
                datetime(2026, 9, 14, 11, tzinfo=UTC),
            ),
        )
    restore_databases = host_restore_tests.Runtime().observe_databases()
    target = None
    if state_family != "initial":
        arrangement = {
            "binding": "canonical",
            "created": "canonical+temporary",
            "registered": "canonical+temporary",
            "partial": "canonical+temporary",
            "pending-old": "canonical+temporary",
            "replacement-pending": "canonical+temporary",
            "temporary-retired": "temporary+retired",
            "retired": "retired",
            "swapped": "canonical+retired",
            "durable-retired": "canonical+retired",
            "durable-canonical": "canonical+retired",
        }[state_family]
        model = host_restore_tests.Runtime(arrangement)
        restore_databases = model.observe_databases()
        host_restore_tests._backup(
            paths, "backup-" + "c" * 32, host_restore_tests.CURRENT, b"stale safety"
        )
        target = host_restore_tests._binding(paths, source, model)
        if state_family == "created":
            target = replace(
                target, restored_database_oid=None, temporary_creation_pending=True
            )
            replace_restore_target(paths, target)
        if state_family == "partial":
            restore_databases["temporary"].update(
                migration_table_present=True, applied_migrations=[]
            )
        if state_family == "pending-old":
            target = replace(target, temporary_creation_pending=True)
            replace_restore_target(paths, target)
        if state_family == "replacement-pending":
            pending = host_restore_tests._backup(
                paths,
                host_restore_tests.REPLACEMENT_BACKUP,
                host_restore_tests.TARGET,
                b"pending restore source",
            )
            target = replace(
                target,
                replacement={
                    "backup_id": pending.backup_id,
                    "dump_sha256": pending.dump_sha256,
                    "source_release_id": pending.source_release_id,
                    "discard_database_oid": target.restored_database_oid,
                },
            )
            replace_restore_target(paths, target)
        if state_family == "retired":
            target = replace(
                target, restored_database_oid=202, temporary_creation_pending=True
            )
            replace_restore_target(paths, target)
        if state_family.startswith("durable-"):
            append_selection(
                paths,
                SelectionRecord(
                    host_restore_tests.TARGET,
                    host_restore_tests.CURRENT,
                    target.safety_backup_id,
                    datetime(2026, 9, 14, 12, tzinfo=UTC),
                    2,
                    host_restore_tests.CURRENT,
                    tuple(sorted({source.backup_id, target.safety_backup_id})),
                ),
            )
            current = Path(paths.local(paths.current_link))
            current.unlink()
            current.symlink_to(
                Path(paths.local(paths.release_root / host_restore_tests.TARGET))
            )
            if state_family == "durable-canonical":
                restore_databases["retired"] = None
    config = EnvironmentConfig.model_validate(valid_environment(ssh_port=22)).model_copy(
        update={"install_root": paths.install_root, "backup_root": paths.backup_root}
    )
    package = build_helper_package(tmp_path / "taskman-restore-host.pyz")
    remote = _ControllerRemote()
    runtime_path = tmp_path / "isolated-restore-state.json"
    runtime_path.write_text(
        json.dumps(
            {
                "backup_count": 16,
                "migrations": [host_restore_tests.VERSION],
                "database_state": "ready",
                "restore_databases": restore_databases,
                "next_restore_oid": 202 if target is not None and target.restored_database_oid is not None else 201,
                "database_capacity": 1_000_000,
                "scheduler_sha256": "e" * 64,
                "scheduler_resources": {
                    "helper": True,
                    "service": True,
                    "timer": True,
                    "environment": True,
                },
                "scheduler_running": True,
                "scheduler_failure": scheduler_failure,
                "backup_timer_enabled": True,
                "backup_timer_state": "active",
                "service_running": True,
                "verification": "passing",
                "events": [],
            },
            sort_keys=True,
        )
    )
    real_run_request = helper.run_request

    def dispatch(_remote: object, request: object, **_kwargs: object) -> object:
        def invoke(_transport, selected, wire_request, **_options):
            result = _wire_helper(wire_request, selected, runtime_path)
            runtime = json.loads(runtime_path.read_text())
            if wire_request.operation == "restore" and runtime.get("lose_restore_reply"):
                runtime["lose_restore_reply"] = False
                runtime_path.write_text(json.dumps(runtime, sort_keys=True))
                raise HelperTransportError(
                    OpsError(
                        ExitStatus.SAFETY,
                        "helper",
                        "restore helper reply was lost",
                        True,
                    ),
                    helper_entry_dispatched=True,
                )
            return result

        return real_run_request(
            _remote,
            request,
            package=package,
            invoker=invoke,
        )

    monkeypatch.setattr(
        restore_workflow,
        "validate_restore_inspection_preflight",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        restore_workflow,
        "validate_restore_preflight",
        lambda *_args: RestorePreflightFacts(
            1_000_000,
            1_000_000,
            1_000_000,
            {
                role: None if observed is None else 1024
                for role, observed in restore_databases.items()
            },
        ),
    )
    monkeypatch.setattr(restore_workflow, "run_request", dispatch)
    monkeypatch.setattr(inventory, "run_request", dispatch)
    monkeypatch.setattr(helper, "run_request", dispatch)
    return config, runtime_path, paths, remote, source


@pytest.mark.parametrize("scheduler_failure", (False, True))
def test_public_restore_runs_old_scheduler_to_quiescence_before_packaged_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheduler_failure: bool,
) -> None:
    """Skipping controller upload or packaged convergence would publish too early."""

    from taskman_ops.workflows.restore import restore

    config, runtime_path, paths, remote, source = _install_public_restore_controller(
        monkeypatch,
        tmp_path,
        scheduler_failure=scheduler_failure,
    )

    plans = []
    result = restore(
        remote,
        config,
        source.backup_id,
        confirm=lambda plan: plans.append(plan) or True,
    )

    runtime = json.loads(runtime_path.read_text())
    binding = Path(paths.local(paths.restore_target_path))
    if not scheduler_failure:
        assert result.exit_status is ExitStatus.OK, result.facts
    assert "scheduler-stop" in runtime["events"], (result, runtime)
    assert runtime["events"].index("scheduler-stop") < runtime["events"].index(
        "old-scheduler-finished"
    )
    if scheduler_failure:
        assert result.exit_status is ExitStatus.RELEASE
        assert result.facts["failed_boundary"] == "backup_helper"
        assert result.facts["mutation_state"] == "changed"
        assert "scheduler-replace" not in runtime["events"]
        assert not binding.exists()
    else:
        assert runtime["events"].index("old-scheduler-finished") < runtime[
            "events"
        ].index("scheduler-replace")
        assert runtime["events"].index("scheduler-replace") < runtime[
            "events"
        ].index("binding-published")
        assert not binding.exists()


@pytest.mark.parametrize("current_present", (True, False), ids=("current-present", "current-absent"))
def test_public_packaged_restore_creates_first_success_from_null_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current_present: bool,
) -> None:
    """Dropping null-baseline reference transfer would lose pre-first-success recovery."""

    from taskman_ops.host_helper.state import observe_host_state
    from taskman_ops.workflows.restore import restore

    config, runtime_path, paths, remote, source = _install_public_restore_controller(
        monkeypatch,
        tmp_path,
        scheduler_failure=False,
        first_success=True,
        current_present=current_present,
    )
    safety_backup_id = "backup-" + f"{17:032x}"

    result = restore(remote, config, source.backup_id, confirm=lambda _plan: True)

    runtime = json.loads(runtime_path.read_text())
    state = observe_host_state(paths)
    assert result.exit_status is ExitStatus.OK, result.facts
    assert result.facts["starting_state"]["last_successful_selection_id"] is None
    assert result.facts["starting_state"]["selected_release_id"] == (
        host_restore_tests.CURRENT if current_present else None
    )
    assert runtime["events"].index("safety-backup") < runtime["events"].index(
        "binding-published"
    )
    assert state.selected_release_id == host_restore_tests.TARGET
    assert len(state.selections) == 1
    selection = state.selections[0]
    assert selection.release_id == host_restore_tests.TARGET
    assert selection.previous_release_id is None
    assert selection.observed_previous_release_id == (
        host_restore_tests.CURRENT if current_present else None
    )
    assert selection.backup_id == safety_backup_id
    assert set(selection.recovery_backup_ids) == {
        source.backup_id,
        host_restore_tests.PROTECTION_BACKUP,
        safety_backup_id,
    }
    assert state.restore_target is None
    assert state.backup_protections == ()
    for backup_id in selection.recovery_backup_ids:
        assert Path(paths.local(paths.backup_root / f"{backup_id}.dump")).is_file()
        assert Path(paths.local(paths.backup_root / f"{backup_id}.json")).is_file()


def test_public_packaged_null_baseline_restore_retries_after_lost_completion_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost post-completion reply must retain original recovery and avoid duplicate success."""

    from taskman_ops.host_helper.state import observe_host_state
    from taskman_ops.workflows.restore import restore

    config, runtime_path, paths, remote, source = _install_public_restore_controller(
        monkeypatch,
        tmp_path,
        scheduler_failure=False,
        first_success=True,
        current_present=True,
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["lose_restore_reply"] = True
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    interrupted = restore(remote, config, source.backup_id, confirm=lambda _plan: True)

    runtime = json.loads(runtime_path.read_text())
    pending = observe_host_state(paths)
    first_safety_backup_id = "backup-" + f"{17:032x}"
    assert interrupted.exit_status is ExitStatus.SAFETY
    assert interrupted.changed is True
    assert interrupted.facts["mutation_state"] == "unknown"
    assert len(pending.selections) == 1
    assert pending.selections[0].previous_release_id is None
    assert pending.selections[0].backup_id == first_safety_backup_id
    assert runtime["restore_databases"]["canonical"]["oid"] != 101
    assert runtime["restore_databases"]["temporary"] is None
    for backup_id in (source.backup_id, host_restore_tests.PROTECTION_BACKUP, first_safety_backup_id):
        assert Path(paths.local(paths.backup_root / f"{backup_id}.dump")).is_file()
        assert Path(paths.local(paths.backup_root / f"{backup_id}.json")).is_file()

    retried = restore(remote, config, source.backup_id, confirm=lambda _plan: True)

    runtime = json.loads(runtime_path.read_text())
    state = observe_host_state(paths)
    assert retried.exit_status is ExitStatus.OK, retried.facts
    assert len(state.selections) == 1
    selection = state.selections[0]
    assert selection.previous_release_id is None
    assert selection.observed_previous_release_id == host_restore_tests.CURRENT
    assert {source.backup_id, host_restore_tests.PROTECTION_BACKUP, first_safety_backup_id}.issubset(
        selection.recovery_backup_ids
    )
    assert state.restore_target is None
    assert state.backup_protections == ()
    assert runtime["events"].count("safety-backup") == 1
    assert runtime["events"].count("dump-loaded") == 1
    assert runtime["restore_databases"]["retired"] is None


@pytest.mark.parametrize(
    ("family", "required_events", "forbidden_events"),
    (
        (
            "binding",
            {"safety-backup", "temporary-created", "temporary-registered", "dump-loaded"},
            set(),
        ),
        (
            "created",
            {"safety-backup", "temporary-registered", "dump-loaded"},
            {"temporary-created"},
        ),
        (
            "registered",
            {"safety-backup", "rebuild-pending", "temporary-dropped", "dump-loaded"},
            set(),
        ),
        (
            "partial",
            {"safety-backup", "rebuild-pending", "temporary-dropped", "dump-loaded"},
            set(),
        ),
        (
            "pending-old",
            {"temporary-dropped:202", "temporary-registered", "dump-loaded"},
            set(),
        ),
        (
            "temporary-retired",
            {"rebuild-pending", "temporary-dropped", "dump-loaded"},
            {"safety-backup"},
        ),
        ("retired", {"temporary-created", "dump-loaded"}, {"safety-backup"}),
        ("swapped", {"retired-dropped", "binding-removed"}, {"dump-loaded"}),
        (
            "durable-retired",
            {"retired-dropped", "binding-removed"},
            {"dump-loaded", "scheduler-replace"},
        ),
        (
            "durable-canonical",
            {"binding-removed"},
            {"dump-loaded", "retired-dropped", "scheduler-replace"},
        ),
    ),
)
def test_public_packaged_restore_converges_each_durable_database_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    required_events: set[str],
    forbidden_events: set[str],
) -> None:
    """Real controller/package admission resumes each distinct durable family."""

    from taskman_ops.workflows.restore import restore

    config, runtime_path, paths, remote, source = _install_public_restore_controller(
        monkeypatch, tmp_path, scheduler_failure=False, state_family=family
    )
    if family == "binding":
        runtime = json.loads(runtime_path.read_text())
        runtime["service_running"] = False
        runtime["writes_after_binding"] = True
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    plans = []
    result = restore(
        remote,
        config,
        source.backup_id,
        confirm=lambda plan: plans.append(plan) or True,
    )

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, (result.facts, runtime)
    assert required_events.issubset(runtime["events"])
    assert forbidden_events.isdisjoint(runtime["events"])
    assert not Path(paths.local(paths.restore_target_path)).exists()
    if family == "binding":
        assert plans[0]["planned_pre_restore_backup"] is True
        assert runtime["events"].index("safety-backup") < runtime["events"].index(
            "service-stop"
        )


def test_public_packaged_reapply_cleans_then_creates_one_fresh_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taskman_ops.workflows.restore import restore

    config, runtime_path, paths, remote, source = _install_public_restore_controller(
        monkeypatch, tmp_path, scheduler_failure=False, state_family="durable-retired"
    )
    from taskman_ops.workflows import restore as restore_workflow

    dispatched_expected_states = []
    run_restore_request = restore_workflow.run_restore_request

    def capture_request(*args, **kwargs):
        dispatched_expected_states.append(dict(kwargs["request"].expected_state))
        return run_restore_request(*args, **kwargs)

    monkeypatch.setattr(restore_workflow, "run_restore_request", capture_request)

    result = restore(
        remote, config, source.backup_id, reapply=True, confirm=lambda _plan: True
    )

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, result.facts
    assert runtime["events"].count("binding-removed") == 2
    assert runtime["events"].count("dump-loaded") == 1
    assert runtime["events"].index("retired-dropped") < runtime["events"].index(
        "safety-backup"
    )
    assert not Path(paths.local(paths.restore_target_path)).exists()
    assert result.facts["starting_state"] == mutable(dispatched_expected_states[-1])
    assert result.facts["starting_state"] != mutable(dispatched_expected_states[0])


def test_public_ordinary_retry_preserves_writes_after_durable_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taskman_ops.workflows.restore import restore

    config, runtime_path, _paths, remote, source = _install_public_restore_controller(
        monkeypatch, tmp_path, scheduler_failure=False
    )
    first = restore(remote, config, source.backup_id, confirm=lambda _plan: True)
    assert first.exit_status is ExitStatus.OK
    runtime = json.loads(runtime_path.read_text())
    runtime["later_write"] = "must survive"
    runtime["events"] = []
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    retry = restore(remote, config, source.backup_id, confirm=lambda _plan: True)

    runtime = json.loads(runtime_path.read_text())
    assert retry.exit_status is ExitStatus.OK, retry.facts
    assert runtime["later_write"] == "must survive"
    assert "dump-loaded" not in runtime["events"]
    assert "safety-backup" not in runtime["events"]


@pytest.mark.parametrize(
    "drift",
    ("original-oid", "restored-oid", "owner", "unregistered-not-empty"),
)
def test_public_packaged_restore_refuses_database_identity_or_empty_proof_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    from taskman_ops.workflows.restore import restore

    family = "registered" if drift == "restored-oid" else "binding"
    config, runtime_path, _paths, remote, source = _install_public_restore_controller(
        monkeypatch, tmp_path, scheduler_failure=False, state_family=family
    )
    runtime = json.loads(runtime_path.read_text())
    if drift == "original-oid":
        runtime["restore_databases"]["canonical"]["oid"] = 999
    elif drift == "restored-oid":
        runtime["restore_databases"]["temporary"]["oid"] = 999
    elif drift == "owner":
        runtime["restore_databases"]["canonical"]["owner"] = "foreign"
    else:
        runtime["restore_databases"]["temporary"] = {
            "oid": 202,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [host_restore_tests.VERSION],
        }
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    result = restore(remote, config, source.backup_id, dry_run=True)

    assert result.exit_status is ExitStatus.SAFETY
    assert json.loads(runtime_path.read_text())["events"] == []


@pytest.mark.parametrize("failure", ("active-writers", "empty-proof-failed"))
def test_public_packaged_restore_refuses_unregistered_temporary_without_empty_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from taskman_ops.workflows.restore import restore

    config, runtime_path, paths, remote, source = _install_public_restore_controller(
        monkeypatch, tmp_path, scheduler_failure=False, state_family="created"
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["temporary_active_writers"] = failure == "active-writers"
    runtime["temporary_empty_proof_failure"] = failure == "empty-proof-failed"
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    result = restore(remote, config, source.backup_id, confirm=lambda _plan: True)

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.RESTORE
    assert result.changed is True
    assert "temporary-registered" not in runtime["events"]
    assert "dump-loaded" not in runtime["events"]
    assert Path(paths.local(paths.restore_target_path)).exists()


def test_public_packaged_restore_lost_reply_reports_unknown_after_real_consequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taskman_ops.workflows.restore import restore

    config, runtime_path, _paths, remote, source = _install_public_restore_controller(
        monkeypatch, tmp_path, scheduler_failure=False
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["lose_restore_reply"] = True
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    result = restore(remote, config, source.backup_id, confirm=lambda _plan: True)

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.SAFETY
    assert result.changed is True
    assert result.facts["mutation_state"] == "unknown"
    assert runtime["restore_databases"]["canonical"]["oid"] == 202
    assert "dump-loaded" in runtime["events"]


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
        if "/etc/systemd/system/taskman-backup.timer" in scheduler_create:
            runtime["backup_timer_enabled"] = True
            runtime["backup_timer_state"] = "inactive"
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
    monkeypatch.setattr(
        provision_module,
        "render_pgpass",
        lambda *_args: b"127.0.0.1:5432:taskman_prod:taskman:test-password\n",
    )
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
    assert runtime["events"].index("scheduler-replace") < runtime["events"].index("scheduler-start")
    assert runtime["backup_timer_enabled"] is True
    assert runtime["backup_timer_state"] == "active"


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
    preserved = {
        "runtime": b"RUNTIME=value\n",
        "pgpass": b"127.0.0.1:5432:taskman_prod:taskman:test-password\n",
    }
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

    preserved = {
        "runtime": b"RUNTIME=value\n",
        "pgpass": b"127.0.0.1:5432:taskman_prod:taskman:test-password\n",
    }
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
        render_pgpass=lambda _config, _secrets: (
            b"127.0.0.1:5432:taskman_prod:taskman:test-password\n"
        ),
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
    monkeypatch.setattr(
        provision_module,
        "render_pgpass",
        lambda *_args: b"127.0.0.1:5432:taskman_prod:taskman:test-password\n",
    )
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
    monkeypatch.setattr(
        provision_module,
        "render_pgpass",
        lambda *_args: b"127.0.0.1:5432:taskman_prod:taskman:test-password\n",
    )
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
