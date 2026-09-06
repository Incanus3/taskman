"""Real-entrypoint deploy and genesis contracts for the host helper."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile

import pytest
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.helper_package import build_helper_package
from taskman_ops.helper_runner import HelperInvocation
from taskman_ops.host_protocol import HostRequest, HostResult, decode_result, encode_request
from taskman_ops.host_helper.operations import deploy as deploy_operation
from taskman_ops.host_helper.lifecycle import BackupRecord, LifecycleError, LifecycleStore, LifecycleWriteFailure, StagedRelease
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.runtime import RuntimeFailure
from taskman_ops.manifests import ArtifactManifest, VerifiedArtifact
from taskman_ops.remote import CommandResult
from taskman_ops.workflows.helper_deploy import run_helper_deployment
from taskman_ops.workflows.helper_deploy_results import (
    translate_helper_deployment_result,
)


RELEASE_ID = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


def _prepared_genesis_request(
    tmp_path: Path, *, migrations: list[dict[str, str]] | None = None
) -> tuple[HostRequest, Path, Path]:
    """Build one valid local artifact/upload pair for helper-operation probes."""

    archive = tmp_path / "candidate.tar.gz"
    source = tmp_path / "source" / "taskman"
    for path in (source / "bin", source / "lib", source / "releases"):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("server", "migrate"):
        launcher = source / "bin" / name
        launcher.write_text("#!/bin/sh\n", encoding="utf-8")
        launcher.chmod(0o750)
    with tarfile.open(archive, "w:gz") as packed:
        packed.add(source, arcname="taskman")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    upload = tmp_path / "install" / "deployments" / "uploads" / ".upload.tar.gz"
    upload.parent.mkdir(parents=True)
    upload.parent.chmod(0o750)
    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    migration_fingerprints = [] if migrations is None else migrations
    policy = "no-change" if not migration_fingerprints else "restore-required"
    manifest = {
        "schema_version": 2, "application": "taskman", "application_version": "0.2.0",
        "source_revision": "b" * 40, "release_id": RELEASE_ID,
        "built_at": "2026-09-05T12:00:00Z", "target_os": "ubuntu26.04",
        "architecture": "amd64", "otp_version": "27.3.4.6", "elixir_version": "1.18.3",
        "node_version": "22.22.1", "hex_version": "2.5.1", "rebar3_version": "3.24.0",
        "builder_base_tag": "ubuntu:resolute-20260811.1",
        "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
        "migrations": migration_fingerprints, "top_level": "taskman",
    }
    request = HostRequest(
        protocol_version=1,
        operation="genesis",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"previous_release_id": None, "current_migrations": []},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={
            "candidate_release_id": RELEASE_ID, "artifact_sha256": checksum,
            "artifact_path": upload.as_posix(), "manifest": manifest, "migration_policy": policy,
            "manual_adoption": None,
            "verification": {
                "application_port": 4000, "distribution_port": 6789, "database_port": 5432,
                "public_hostname": "taskman.example.test", "public_ipv4": "203.0.113.10",
                "public_ipv6": None, "ssh_port": 22, "ssh_user": "deployer",
                "readiness_timeout": 1, "connection_timeout": 1, "database_host": "127.0.0.1",
                "database_role": "taskman", "database_name": "taskman_prod",
            },
        },
    )
    return request, archive, upload


def _accept_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_operation, "host_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy_operation, "available_bytes", lambda _path: 20 * 1024 * 1024)
    monkeypatch.setattr(deploy_operation, "_safe_secret_file", lambda *_args: None)
    monkeypatch.setattr(deploy_operation, "_database_size", lambda _settings: 1_048_576)


def _successful_verification(release_id: str) -> dict[str, object]:
    """Return the independently specified verified-release report fixture."""

    checks = (
        ("taskman-service", "taskman.service is active with a positive MainPID"),
        ("release-identity", "systemd MainPID executable is under the selected release"),
        ("caddy-service", "caddy.service is active"),
        ("listener-topology", "Taskman, distribution, and PostgreSQL listeners have the required topology"),
        ("startup-journal", "recent startup journal evidence is clean"),
        ("local-readiness", "loopback health endpoint returned exact ready response"),
        ("public-readiness", "public HTTPS health endpoint returned exact ready response"),
        ("public-hsts", "public HTTPS response includes HSTS"),
    )
    return {
        "schema_version": 1,
        "status": "ok",
        "exit_status": 0,
        "release_id": release_id,
        "expected_release_id": release_id,
        "checks": tuple(
            {"schema_version": 1, "name": name, "status": "passed", "summary": summary}
            for name, summary in checks
        ),
        "next_action": None,
    }


def _run_packaged_helper(
    package: Path,
    request: HostRequest,
    *,
    failure_stage: str | None = None,
    captured_stderr: list[str] | None = None,
) -> tuple[HostResult, str]:
    """Execute the packaged entrypoint while patching its own imported modules.

    ``python -I`` prevents the checkout from being imported accidentally. The
    subprocess adds only the built archive to ``sys.path``, asserts its module
    provenance, then calls the packaged ``main`` with real protocol bytes.
    """

    harness = r'''
import base64, io, json, sys, tarfile
from pathlib import Path

archive = sys.argv[1]
payload = base64.b64decode(sys.argv[2])
failure_stage = sys.argv[3]
canaries = {
    "staging": "staging-secret-canary",
    "backup-publication": "backup-publication-secret-canary",
    "selection": "selection-secret-canary",
    "records": "records-secret-canary",
}
canary = canaries.get(failure_stage, "")
sys.path.insert(0, archive)

import taskman_ops.host_helper.__main__ as entry
import taskman_ops.host_helper.operations.deploy as deployment
from taskman_ops.host_protocol import HostResult
from taskman_ops.host_helper.lifecycle import LifecycleError, LifecycleStore, LifecycleWriteFailure
from taskman_ops.host_helper.runtime import RuntimeFailure

assert deployment.__file__.startswith(archive), deployment.__file__
deployment.host_preflight = lambda *_args: None
deployment.available_bytes = lambda _path: 20 * 1024 * 1024
deployment._safe_secret_file = lambda *_args: None
deployment._database_size = lambda _settings: 1_048_576
deployment._observed_service_state = lambda: "active"

def command(_stage, argv, **_kwargs):
    if argv[0] == "pg_dump":
        dump = Path(argv[argv.index("--file") + 1])
        dump.write_bytes(b"validated backup")
        dump.chmod(0o600)
    if failure_stage == "backup" and argv[0] == "pg_restore":
        raise RuntimeFailure("backup", "injected packaged backup validation failure")
    if failure_stage == "migration" and argv[0] == "systemd-run":
        raise RuntimeFailure("migration", "injected packaged migration failure")
    if failure_stage == "start" and argv[:3] == ("systemctl", "start", "taskman.service"):
        raise RuntimeFailure("start", "injected packaged startup failure")
    return ""

def verified(value, **_kwargs):
    release_id = value.expected_state["expected_release_id"]
    checks = (
        ("taskman-service", "taskman.service is active with a positive MainPID"),
        ("release-identity", "systemd MainPID executable is under the selected release"),
        ("caddy-service", "caddy.service is active"),
        ("listener-topology", "Taskman, distribution, and PostgreSQL listeners have the required topology"),
        ("startup-journal", "recent startup journal evidence is clean"),
        ("local-readiness", "loopback health endpoint returned exact ready response"),
        ("public-readiness", "public HTTPS health endpoint returned exact ready response"),
        ("public-hsts", "public HTTPS response includes HSTS"),
    )
    if failure_stage == "verification":
        return HostResult(
            protocol_version=1, operation=value.operation, operation_id=value.operation_id,
            outcome="failed", stage="verification", changed_stages=(), lifecycle={}, runtime_state={},
            verification={}, residue_paths=(),
            recovery_actions=("inspect selected release verification evidence",), warnings=(),
        )
    return HostResult(
        protocol_version=1, operation=value.operation, operation_id=value.operation_id,
        outcome="succeeded", stage="verified", changed_stages=(), lifecycle={}, runtime_state={},
        verification={
            "schema_version": 1, "status": "ok", "exit_status": 0,
            "release_id": release_id, "expected_release_id": release_id,
            "checks": tuple(
                {"schema_version": 1, "name": name, "status": "passed", "summary": summary}
                for name, summary in checks
            ),
            "next_action": None,
        }, residue_paths=(),
        recovery_actions=(), warnings=(),
    )

deployment._run_command = command
deployment.verify = verified
if failure_stage == "staging":
    deployment._extract_release = lambda *_args: (_ for _ in ()).throw(tarfile.TarError(f"injected packaged staging failure {canary}"))
if failure_stage == "cleanup":
    deployment._extract_release = lambda *_args: (_ for _ in ()).throw(tarfile.TarError("injected packaged staging failure"))
    deployment.shutil.rmtree = lambda *_args: (_ for _ in ()).throw(OSError("injected packaged cleanup failure"))
if failure_stage == "records":
    original_write = LifecycleStore._write
    def partial_records(self, path, payload):
        effect = original_write(self, path, payload)
        if path.parent.name == "releases":
            raise LifecycleWriteFailure(f"injected records failure {canary}", effect)
        return effect
    LifecycleStore._write = partial_records
if failure_stage == "backup-publication":
    original_write = LifecycleStore._write
    def fail_backup_publication(self, path, payload):
        effect = original_write(self, path, payload)
        if path.parent.name == "backups":
            raise LifecycleWriteFailure(f"injected backup publication failure {canary}", effect)
        return effect
    LifecycleStore._write = fail_backup_publication
if failure_stage == "provisional-unlink":
    original_unlink = Path.unlink
    def retain_provisional(path, *args, **kwargs):
        if path.parent.name == "provisionals" and path.name.startswith("activation-"):
            raise OSError("injected packaged provisional unlink failure")
        return original_unlink(path, *args, **kwargs)
    Path.unlink = retain_provisional
if failure_stage == "selection":
    original_fsync = deployment._fsync_directory
    install = Path(json.loads(payload.decode("utf-8"))["paths"]["install_root"])
    def fsync(path):
        if path == install:
            raise OSError(f"injected packaged current fsync failure {canary}")
        original_fsync(path)
    deployment._fsync_directory = fsync

stdin = sys.stdin
stdout = sys.stdout
result = io.BytesIO()
sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
captured_stdout = io.TextIOWrapper(result, encoding="utf-8")
sys.stdout = captured_stdout
status = entry.main()
captured_stdout.flush()
sys.stdin = stdin
sys.stdout = stdout
stdout.write(json.dumps({
    "status": status,
    "module_file": deployment.__file__,
    "result": base64.b64encode(result.getvalue()).decode("ascii"),
}))
'''
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            harness,
            package.as_posix(),
            base64.b64encode(encode_request(request)).decode("ascii"),
            "" if failure_stage is None else failure_stage,
        ],
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    if captured_stderr is not None:
        captured_stderr.append(completed.stderr.decode("utf-8", "replace"))
    response = json.loads(completed.stdout)
    assert response["status"] == 0
    return decode_result(base64.b64decode(response["result"])), response["module_file"]


def _request(tmp_path: Path, operation: str, *, expected_state: dict[str, object]) -> HostRequest:
    return HostRequest(
        protocol_version=1,
        operation=operation,
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state=expected_state,
        paths={
            "install_root": str(tmp_path / "install"),
            "backup_root": str(tmp_path / "backups"),
        },
        parameters={
            "candidate_release_id": RELEASE_ID,
            "artifact_sha256": "a" * 64,
            "artifact_path": str(tmp_path / "install" / "deployments" / "uploads" / ".upload.tar.gz"),
            "manifest": {},
            "migration_policy": "no-change",
            "manual_adoption": None,
        },
    )


def test_built_zipapp_refuses_deploy_before_any_mutation_when_request_is_incomplete(
    tmp_path: Path,
) -> None:
    """An incomplete deployment request cannot create lifecycle state.

    Removing helper-owned request validation or dispatching deploy through the
    old controller program would make the archive either mutate roots or emit
    the unrelated unavailable result.
    """

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = _request(
        tmp_path,
        "deploy",
        expected_state={"previous_release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"},
    )

    completed = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=encode_request(request),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    result = decode_result(completed.stdout)
    assert result.outcome == "refused"
    assert result.stage == "deploy-preflight"
    assert result.changed_stages == ()
    assert result.lifecycle == {}
    assert result.runtime_state == {}
    assert not (tmp_path / "install").exists()
    assert not (tmp_path / "backups").exists()


def test_built_zipapp_module_harness_runs_genesis_success_noop_and_selection_failure(
    tmp_path: Path,
) -> None:
    """The built archive—not checkout imports—executes mutation success and evidence paths."""

    request, archive, upload = _prepared_genesis_request(tmp_path)
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    success, module_file = _run_packaged_helper(package.path, request)

    assert module_file.startswith(package.path.as_posix())
    assert success.outcome == "succeeded"
    assert success.stage == "records"
    assert success.changed_stages == ("staging", "backup", "migration", "selection", "start", "records")
    assert success.lifecycle["activation_recorded"] is True
    records = LifecycleStore(
        ManagedPaths.from_mapping(request.paths)
    ).read()
    created_backup = records.backups[0]
    assert created_backup.dump_sha256 == hashlib.sha256(
        Path(created_backup.dump_path.as_posix()).read_bytes()
    ).hexdigest()

    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    no_op, _module_file = _run_packaged_helper(package.path, request)

    assert no_op.outcome == "no_change"
    assert no_op.stage == "already-current"
    assert no_op.changed_stages == ()

    failed_root = tmp_path / "selection-failure"
    failure_request, _failure_archive, _failure_upload = _prepared_genesis_request(failed_root)
    failure, _module_file = _run_packaged_helper(
        package.path, failure_request, failure_stage="selection"
    )

    assert failure.outcome == "failed"
    assert failure.stage == "selection"
    assert failure.changed_stages == ("staging", "backup", "migration", "selection")
    assert failure.lifecycle["selected_release_id"] == RELEASE_ID

    # A normal CLI retry uses a fresh operation ID. The packaged helper must
    # rediscover the one matching older provisional rather than refuse it.
    _failure_upload.write_bytes(_failure_archive.read_bytes())
    _failure_upload.chmod(0o600)
    resumed_mapping = failure_request.to_mapping()
    resumed_mapping["operation_id"] = "op-fedcba9876543210fedcba9876543210"
    resumed_request = HostRequest.from_mapping(resumed_mapping)
    resumed, _module_file = _run_packaged_helper(package.path, resumed_request)

    assert resumed.outcome == "succeeded"
    assert resumed.changed_stages == ("start", "records")
    assert resumed.runtime_state == {
        "resumed_activation_id": "activation-0123456789abcdef0123456789abcdef"
    }


@pytest.mark.parametrize(
    ("failure_stage", "expected_stage", "expected_changed", "expects_residue"),
    [
        ("staging", "staging", ("staging",), False),
        ("backup", "backup", ("staging", "backup"), True),
        ("backup-publication", "backup", ("staging", "backup"), True),
        ("migration", "migration", ("staging", "backup", "migration"), False),
        ("start", "start", ("staging", "backup", "migration", "selection", "start"), False),
        ("verification", "verification", ("staging", "backup", "migration", "selection", "start"), False),
        ("records", "records", ("staging", "backup", "migration", "selection", "start", "records"), True),
        ("cleanup", "staging", ("staging",), True),
    ],
)
def test_built_zipapp_module_harness_reports_each_mutation_boundary_failure(
    tmp_path: Path,
    failure_stage: str,
    expected_stage: str,
    expected_changed: tuple[str, ...],
    expects_residue: bool,
) -> None:
    """The packaged entrypoint preserves stage effects and cleanup evidence.

    This executes modules imported from the built archive with real protocol
    bytes.  It prevents checkout-only mocked operation tests from concealing
    dispatch, encoding, or post-mutation evidence regressions.
    """

    request, _archive, _upload = _prepared_genesis_request(
        tmp_path,
        migrations=(
            [{"filename": "20260906000000_add_widgets.exs", "sha256": "d" * 64}]
            if failure_stage == "migration"
            else None
        ),
    )
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    result, module_file = _run_packaged_helper(
        package.path, request, failure_stage=failure_stage
    )

    assert module_file.startswith(package.path.as_posix())
    assert result.outcome == "failed"
    assert result.stage == expected_stage
    assert result.changed_stages == expected_changed
    assert bool(result.residue_paths) is expects_residue
    if failure_stage == "start":
        assert result.lifecycle["service_state"] == "active"
    if failure_stage == "records":
        assert result.lifecycle["activation_recorded"] is False
        install = tmp_path / "install"
        assert result.residue_paths == (
            (install / "deployments" / "manifests" / f"release-{RELEASE_ID}.json").as_posix(),
            (install / "deployments" / "releases" / f"release-{RELEASE_ID}.json").as_posix(),
            (install / "deployments" / "provisionals" / "activation-0123456789abcdef0123456789abcdef.json").as_posix(),
        )
        assert result.recovery_actions == (
            "inspect published lifecycle records and the provisional activation before retrying",
        )
        assert "records-canary" not in repr(result)
    if failure_stage in {"staging", "backup-publication", "selection"}:
        expected_actions = {
            "staging": ("inspect the immutable release staging evidence before retrying",),
            "backup-publication": (
                "preserve the validated deployment backup and inspect its lifecycle record before retrying",
            ),
            "selection": ("inspect the current release selection before retrying",),
        }
        assert result.recovery_actions == expected_actions[failure_stage]
    if failure_stage == "cleanup":
        assert result.warnings == ("unable to remove operation residue",)


def test_built_zipapp_resumes_matching_partial_finalization_with_a_fresh_operation_id(
    tmp_path: Path,
) -> None:
    """A packaged retry completes an exact final-record prefix without replacement.

    Removing partial-record recognition would make the second helper reject
    the existing manifest/release no-replace records instead of safely
    publishing the activation and retiring its retained provisional.
    """

    request, archive, upload = _prepared_genesis_request(tmp_path)
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    failed, module_file = _run_packaged_helper(package.path, request, failure_stage="records")

    assert module_file.startswith(package.path.as_posix())
    assert failed.outcome == "failed"
    assert failed.stage == "records"
    assert failed.lifecycle["activation_recorded"] is False

    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    mapping = request.to_mapping()
    mapping["operation_id"] = "op-fedcba9876543210fedcba9876543210"
    resumed, _module_file = _run_packaged_helper(package.path, HostRequest.from_mapping(mapping))

    assert resumed.outcome == "succeeded"
    assert resumed.changed_stages == ("start", "records")
    assert resumed.runtime_state == {
        "resumed_activation_id": "activation-0123456789abcdef0123456789abcdef"
    }
    assert resumed.residue_paths == ()
    assert not (
        tmp_path / "install" / "deployments" / "provisionals" / "activation-0123456789abcdef0123456789abcdef.json"
    ).exists()


@pytest.mark.parametrize(
    "retry_operation_id",
    (
        "op-0123456789abcdef0123456789abcdef",
        "op-fedcba9876543210fedcba9876543210",
    ),
    ids=("same-operation", "fresh-operation"),
)
def test_built_zipapp_and_controller_refuse_same_size_resume_backup_replacement(
    tmp_path: Path,
    retry_operation_id: str,
) -> None:
    """A content swap cannot cross either the helper or controller result boundary."""

    request, archive, upload = _prepared_genesis_request(tmp_path)
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    failed, _module_file = _run_packaged_helper(
        package.path,
        request,
        failure_stage="selection",
    )
    assert failed.stage == "selection"
    dump = (
        tmp_path
        / "backups"
        / "backup-0123456789abcdef0123456789abcdef.dump"
    )
    assert len(dump.read_bytes()) == len(b"corrupted backup")
    dump.write_bytes(b"corrupted backup")
    dump.chmod(0o600)
    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    mapping = request.to_mapping()
    mapping["operation_id"] = retry_operation_id
    resumed_request = HostRequest.from_mapping(mapping)

    refused, _module_file = _run_packaged_helper(
        package.path,
        resumed_request,
    )

    assert refused.outcome == "refused"
    assert refused.stage == "deploy-preflight"
    assert refused.changed_stages == ()
    with pytest.raises(OpsError) as raised:
        translate_helper_deployment_result(
            HelperInvocation(refused),
            resumed_request,
        )
    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.changed is False
    assert raised.value.stage == "deploy-preflight"


def test_built_zipapp_retires_a_retained_provisional_after_all_final_records_exist(
    tmp_path: Path,
) -> None:
    """A fresh-ID retry finishes provisional retirement instead of reporting no-op.

    If completed final records short-circuit the resumed operation, the exact
    provisional left by a failed unlink becomes permanent lifecycle residue.
    """

    request, archive, upload = _prepared_genesis_request(tmp_path)
    package = build_helper_package(tmp_path / "taskman-host.pyz")

    failed, _module_file = _run_packaged_helper(
        package.path, request, failure_stage="provisional-unlink"
    )

    provisional = (
        tmp_path
        / "install"
        / "deployments"
        / "provisionals"
        / "activation-0123456789abcdef0123456789abcdef.json"
    )
    assert failed.outcome == "failed"
    assert failed.stage == "records"
    assert failed.lifecycle["activation_recorded"] is True
    assert provisional.is_file()

    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    mapping = request.to_mapping()
    mapping["operation_id"] = "op-fedcba9876543210fedcba9876543210"
    resumed, _module_file = _run_packaged_helper(package.path, HostRequest.from_mapping(mapping))

    assert resumed.outcome == "succeeded"
    assert resumed.stage == "records"
    assert resumed.changed_stages == ("start", "records")
    assert not provisional.exists()


def test_genesis_operation_retires_a_provisional_after_unlink_failure_with_a_fresh_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operation itself finalizes an all-record prefix left by unlink failure."""

    request, archive, upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)
    original_unlink = Path.unlink

    def command(_stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
        return ""

    def verified(value: HostRequest, **_kwargs: object) -> HostResult:
        return HostResult(
            protocol_version=1,
            operation=value.operation,
            operation_id=value.operation_id,
            outcome="succeeded",
            stage="verified",
            changed_stages=(),
            lifecycle={},
            runtime_state={},
            verification={"schema_version": 1, "status": "ok"},
            residue_paths=(),
            recovery_actions=(),
            warnings=(),
        )

    provisional = (
        tmp_path
        / "install"
        / "deployments"
        / "provisionals"
        / "activation-0123456789abcdef0123456789abcdef.json"
    )

    def retain_provisional(path: Path, *args: object, **kwargs: object) -> None:
        if path == provisional:
            raise OSError("injected provisional unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(deploy_operation, "_run_command", command)
    monkeypatch.setattr(deploy_operation, "_observed_service_state", lambda: "active")
    monkeypatch.setattr(deploy_operation, "verify", verified)
    monkeypatch.setattr(Path, "unlink", retain_provisional)

    failed = deploy_operation.genesis(request)

    assert failed.outcome == "failed"
    assert failed.stage == "records"
    assert failed.lifecycle["activation_recorded"] is True
    assert provisional.is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    mapping = request.to_mapping()
    mapping["operation_id"] = "op-fedcba9876543210fedcba9876543210"
    resumed = deploy_operation.genesis(HostRequest.from_mapping(mapping))

    assert resumed.outcome == "succeeded"
    assert resumed.stage == "records"
    assert resumed.changed_stages == ("start", "records")
    assert not provisional.exists()


def test_packaged_completed_genesis_noop_translates_through_the_real_controller(
    tmp_path: Path,
) -> None:
    """A completed clean-host activation has no predecessor and no new database work.

    Reporting the newly current candidate as the genesis predecessor makes the
    controller reject its otherwise valid no-op evidence. This invokes the
    built helper archive and the public controller result translator rather
    than manufacturing a helper result in the test.
    """

    request, archive, _upload = _prepared_genesis_request(tmp_path)
    manifest_value = dict(request.parameters["manifest"])
    manifest_value["migrations"] = []
    manifest = ArtifactManifest.from_mapping(manifest_value)
    artifact = VerifiedArtifact(
        archive,
        tmp_path / "manifest.json",
        tmp_path / "checksum.txt",
        hashlib.sha256(archive.read_bytes()).hexdigest(),
        manifest,
    )
    config = EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43,
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@acme.tld",
        }
    ).model_copy(
        update={
            "install_root": PurePosixPath((tmp_path / "install").as_posix()),
            "backup_root": PurePosixPath((tmp_path / "backups").as_posix()),
        }
    )

    class LocalRemote:
        def run(self, _argv: object, **_kwargs: object) -> CommandResult:
            return CommandResult(0)

        def put(self, source: Path, destination: PurePosixPath, **_kwargs: object) -> None:
            target = Path(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o600)

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    def invoke(_remote: object, active_package: object, helper_request: HostRequest) -> HelperInvocation:
        result, module_file = _run_packaged_helper(active_package.path, helper_request)
        assert module_file.startswith(active_package.path.as_posix())
        return HelperInvocation(result)

    remote = LocalRemote()
    first = run_helper_deployment(
        remote,
        config,
        artifact,
        previous_release_id=None,
        current_migrations=(),
        migration_policy="no-change",
        genesis=True,
        package=package,
        invoker=invoke,
    )
    second = run_helper_deployment(
        remote,
        config,
        artifact,
        previous_release_id=None,
        current_migrations=(),
        migration_policy="no-change",
        genesis=True,
        package=package,
        invoker=invoke,
    )

    assert first["stage"] == "deployed"
    assert second["stage"] == "already-current"
    assert second["previous_release_id"] is None
    assert second["database_state"] == "unchanged"
    assert second["backup_id"] is None


@pytest.mark.parametrize(
    ("failure_stage", "recovery_action", "canary"),
    (
        (
            "staging",
            "inspect the immutable release staging evidence before retrying",
            "staging-secret-canary",
        ),
        (
            "backup-publication",
            "preserve the validated deployment backup and inspect its lifecycle record before retrying",
            "backup-publication-secret-canary",
        ),
        (
            "selection",
            "inspect the current release selection before retrying",
            "selection-secret-canary",
        ),
        (
            "records",
            "inspect published lifecycle records and the provisional activation before retrying",
            "records-secret-canary",
        ),
    ),
)
def test_packaged_failure_evidence_reaches_the_real_controller_with_exact_recovery_and_redaction(
    tmp_path: Path, failure_stage: str, recovery_action: str, canary: str
) -> None:
    """The controller must preserve recovery evidence emitted by the built helper.

    Returning a manufactured ``HostResult`` here would not prove the archive,
    protocol decoder, and controller translator keep each failed stage's
    recovery action intact.
    """

    root = tmp_path / failure_stage
    request, archive, _upload = _prepared_genesis_request(root)
    manifest_value = dict(request.parameters["manifest"])
    manifest_value["migrations"] = list(manifest_value["migrations"])
    manifest = ArtifactManifest.from_mapping(manifest_value)
    artifact = VerifiedArtifact(
        archive,
        root / "manifest.json",
        root / "checksum.txt",
        hashlib.sha256(archive.read_bytes()).hexdigest(),
        manifest,
    )
    config = EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43,
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@acme.tld",
        }
    ).model_copy(
        update={
            "install_root": PurePosixPath((root / "install").as_posix()),
            "backup_root": PurePosixPath((root / "backups").as_posix()),
        }
    )

    class LocalRemote:
        def run(self, _argv: object, **_kwargs: object) -> CommandResult:
            return CommandResult(0)

        def put(self, source: Path, destination: PurePosixPath, **_kwargs: object) -> None:
            target = Path(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o600)

    package = build_helper_package(root / "taskman-host.pyz")
    helper_results: list[HostResult] = []
    captured_stderr: list[str] = []

    def invoke(_remote: object, active_package: object, helper_request: HostRequest) -> HelperInvocation:
        result, module_file = _run_packaged_helper(
            active_package.path,
            helper_request,
            failure_stage=failure_stage,
            captured_stderr=captured_stderr,
        )
        assert module_file.startswith(active_package.path.as_posix())
        helper_results.append(result)
        return HelperInvocation(result)

    with pytest.raises(OpsError) as raised:
        run_helper_deployment(
            LocalRemote(),
            config,
            artifact,
            previous_release_id=None,
            current_migrations=(),
            migration_policy="no-change",
            genesis=True,
            package=package,
            invoker=invoke,
        )

    error = raised.value
    assert error.stage == ("backup" if failure_stage == "backup-publication" else failure_stage)
    assert error.recovery_commands == (recovery_action,)
    assert error.warnings == ()
    assert len(helper_results) == 1
    assert helper_results[0].warnings == ()
    assert captured_stderr == [""]
    for observable in (
        str(error),
        repr(error),
        repr(error.warnings),
        repr(helper_results[0]),
        repr(helper_results[0].warnings),
        captured_stderr[0],
    ):
        assert canary not in observable


def test_deploy_input_refuses_changed_migrations_declared_as_no_change(tmp_path: Path) -> None:
    """The confirmed policy must match the compared migration fingerprints.

    Accepting a changed candidate under ``no-change`` would let later staging
    run after a plan that falsely promises no database transition.
    """

    manifest = {
        "schema_version": 2,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": "b" * 40,
        "release_id": RELEASE_ID,
        "built_at": "2026-09-05T12:00:00Z",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "elixir_version": "1.18.3",
        "node_version": "22.22.1",
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "builder_base_tag": "ubuntu:resolute-20260811.1",
        "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
        "migrations": [{"filename": "20260906000000_add_widgets.exs", "sha256": "f" * 64}],
        "top_level": "taskman",
    }
    request = HostRequest(
        protocol_version=1,
        operation="genesis",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"previous_release_id": None, "current_migrations": []},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={
            "candidate_release_id": RELEASE_ID,
            "artifact_sha256": "a" * 64,
            "artifact_path": str(tmp_path / "install" / "deployments" / "uploads" / ".upload.tar.gz"),
            "manifest": manifest,
            "migration_policy": "no-change",
            "manual_adoption": None,
            "verification": {
                "application_port": 4000,
                "distribution_port": 6789,
                "database_port": 5432,
                "public_hostname": "taskman.example.test",
                "public_ipv4": "203.0.113.10",
                "public_ipv6": None,
                "ssh_port": 22,
                "ssh_user": "deployer",
                "readiness_timeout": 1,
                "connection_timeout": 1,
                "database_host": "127.0.0.1",
                "database_role": "taskman",
                "database_name": "taskman_prod",
            },
        },
    )

    with pytest.raises(ValueError, match="migration"):
        deploy_operation._inputs(request, genesis=True)

    assert not (tmp_path / "install").exists()


def test_genesis_reports_staging_when_rename_precedes_a_failed_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A renamed immutable tree remains changed even when durability confirmation fails."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)
    monkeypatch.setattr(deploy_operation, "_run_command", lambda *_args, **_kwargs: "")
    real_fsync = deploy_operation._fsync_directory
    release_root = tmp_path / "install" / "releases"

    def fail_after_rename(path: Path) -> None:
        if path == release_root:
            raise OSError("injected fsync failure")
        real_fsync(path)

    monkeypatch.setattr(deploy_operation, "_fsync_directory", fail_after_rename)

    result = deploy_operation.genesis(request)

    assert result.outcome == "failed"
    assert result.stage == "staging"
    assert result.changed_stages == ("staging",)
    assert result.lifecycle["selected_release_id"] is None
    assert result.recovery_actions == (
        "inspect the immutable release staging evidence before retrying",
    )
    assert (release_root / RELEASE_ID).is_dir()


def test_genesis_marks_staging_when_root_creation_precedes_a_staging_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating managed roots is an active staging mutation, not rediscovery."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)
    monkeypatch.setattr(
        deploy_operation,
        "_extract_release",
        lambda *_args: (_ for _ in ()).throw(tarfile.TarError("injected extraction failure")),
    )

    result = deploy_operation.genesis(request)

    assert result.outcome == "failed"
    assert result.stage == "staging"
    assert result.changed_stages == ("staging",)
    assert (tmp_path / "install" / "releases").is_dir()
    assert (tmp_path / "backups").is_dir()


def test_deploy_represents_manual_adoption_as_staging_before_later_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual lifecycle publication cannot be hidden in under-lock rediscovery."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    selected = tmp_path / "install" / "releases" / "historical"
    server = selected / "bin" / "server"
    app = selected / "lib" / "taskman-0.2.0" / "ebin" / "taskman.app"
    migrations = selected / "lib" / "taskman-0.2.0" / "priv" / "repo" / "migrations"
    for directory in (server.parent, app.parent, migrations):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    server.write_text("#!/bin/sh\n", encoding="utf-8")
    server.chmod(0o750)
    app.write_text('{application,taskman,[{vsn,"0.2.0"}]}.\n', encoding="utf-8")
    app.chmod(0o640)
    selected.chmod(0o750)
    (tmp_path / "install" / "current").symlink_to(selected)
    store = LifecycleStore(
        ManagedPaths.from_mapping(
            {"install_root": (tmp_path / "install").as_posix(), "backup_root": (tmp_path / "backups").as_posix()}
        ),
        owner_uid=os.geteuid(),
    )
    manual = store.inspect_manual_current()
    mapping = request.to_mapping()
    mapping["operation"] = "deploy"
    mapping["expected_state"] = {
        "previous_release_id": manual.release_id,
        "current_migrations": [dict(item) for item in manual.migrations],
    }
    parameters = dict(mapping["parameters"])
    parameters["manual_adoption"] = manual.to_mapping()
    mapping["parameters"] = parameters
    deploy_request = HostRequest.from_mapping(mapping)
    _accept_preflight(monkeypatch)
    monkeypatch.setattr(
        deploy_operation,
        "_extract_release",
        lambda *_args: (_ for _ in ()).throw(tarfile.TarError("injected later staging failure")),
    )

    result = deploy_operation.deploy(deploy_request)

    assert result.outcome == "failed"
    assert result.stage == "staging"
    assert result.changed_stages == ("staging",)
    assert result.lifecycle["previous_release_id"] == manual.release_id
    assert store.read().adoptions[0].release_id == manual.release_id


def test_genesis_preserves_dump_residue_when_validation_fails_after_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed dump validation retains both changed-stage and recovery artifact evidence."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)
    backup_path = tmp_path / "backups" / "backup-0123456789abcdef0123456789abcdef.dump"

    def command(stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            backup_path.write_bytes(b"incomplete dump")
            backup_path.chmod(0o600)
            return ""
        if argv[0] == "pg_restore":
            raise RuntimeFailure(stage, "injected backup validation failure")
        return ""

    monkeypatch.setattr(deploy_operation, "_run_command", command)

    result = deploy_operation.genesis(request)

    assert result.outcome == "failed"
    assert result.stage == "backup"
    assert result.changed_stages == ("staging", "backup")
    assert result.lifecycle["backup_id"] == "backup-0123456789abcdef0123456789abcdef"
    assert result.residue_paths == (backup_path.as_posix(),)
    assert backup_path.is_file()


def test_genesis_preserves_validated_dump_residue_when_backup_record_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A usable dump is recovery residue if its immutable record did not publish."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)

    def command(_stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
        return ""

    monkeypatch.setattr(deploy_operation, "_run_command", command)
    monkeypatch.setattr(
        LifecycleStore,
        "write_backup",
        lambda *_args: (_ for _ in ()).throw(LifecycleError("injected backup record failure")),
    )

    result = deploy_operation.genesis(request)

    dump = tmp_path / "backups" / "backup-0123456789abcdef0123456789abcdef.dump"
    assert result.outcome == "failed"
    assert result.stage == "backup"
    assert result.changed_stages == ("staging", "backup")
    assert result.residue_paths == (dump.as_posix(),)
    assert result.recovery_actions == (
        "preserve the validated deployment backup and inspect its lifecycle record before retrying",
    )
    assert dump.is_file()


def test_genesis_reports_selected_state_when_current_replace_precedes_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-replace selection failure exposes the provisional and selected candidate."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)

    def command(_stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
        return ""

    monkeypatch.setattr(deploy_operation, "_run_command", command)
    real_fsync = deploy_operation._fsync_directory
    install = tmp_path / "install"

    def fail_after_current_replace(path: Path) -> None:
        if path == install:
            raise OSError("injected current fsync failure")
        real_fsync(path)

    monkeypatch.setattr(deploy_operation, "_fsync_directory", fail_after_current_replace)

    result = deploy_operation.genesis(request)

    assert result.outcome == "failed"
    assert result.stage == "selection"
    assert result.changed_stages == ("staging", "backup", "migration", "selection")
    assert result.lifecycle["selected_release_id"] == RELEASE_ID
    assert result.lifecycle["activation_recorded"] is False
    assert result.recovery_actions == (
        "inspect the current release selection before retrying",
    )
    assert (install / "current").resolve() == install / "releases" / RELEASE_ID
    assert (
        install / "deployments" / "provisionals" / "activation-0123456789abcdef0123456789abcdef.json"
    ).is_file()


def test_genesis_removes_operation_current_temporary_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact temporary symlink is cleanup-owned when ``current`` is unchanged."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)

    def command(_stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
        return ""

    monkeypatch.setattr(deploy_operation, "_run_command", command)
    monkeypatch.setattr(
        deploy_operation.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("injected current replacement failure")),
    )

    result = deploy_operation.genesis(request)

    temporary = tmp_path / "install" / ".current-op-0123456789abcdef0123456789abcdef"
    assert result.outcome == "failed"
    assert result.stage == "selection"
    assert result.changed_stages == ("staging", "backup", "migration", "selection")
    assert not temporary.exists() and not temporary.is_symlink()
    assert result.residue_paths == ()


def test_genesis_marks_database_transition_unknown_when_migration_runner_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration process can commit before its error, so failure is not no-change."""

    request, _archive, _upload = _prepared_genesis_request(
        tmp_path,
        migrations=[{"filename": "20260906000000_add_widgets.exs", "sha256": "d" * 64}],
    )
    _accept_preflight(monkeypatch)

    def command(stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
            return ""
        if argv[0] == "systemd-run":
            raise RuntimeFailure(stage, "injected partial migration failure")
        return ""

    monkeypatch.setattr(deploy_operation, "_run_command", command)

    result = deploy_operation.genesis(request)

    assert result.outcome == "failed"
    assert result.stage == "migration"
    assert result.changed_stages == ("staging", "backup", "migration")
    assert result.lifecycle["database_state"] == "unknown"
    assert result.lifecycle["backup_id"] == "backup-0123456789abcdef0123456789abcdef"


def test_genesis_reports_published_activation_when_record_finalization_fails_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late record-publication failure retains the activation that already reached disk."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)

    def command(_stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
        return ""

    def verified(value: HostRequest, **_kwargs: object) -> HostResult:
        return HostResult(
            protocol_version=1, operation=value.operation, operation_id=value.operation_id,
            outcome="succeeded", stage="verified", changed_stages=(), lifecycle={}, runtime_state={},
            verification={"schema_version": 1, "status": "ok"}, residue_paths=(),
            recovery_actions=(), warnings=(),
        )

    def partial_finalize(
        store: LifecycleStore, staged: StagedRelease, release: object, activation: object
    ) -> None:
        store._write(store.manifest_path(staged.candidate_release_id), staged.manifest)
        store.write_release(release)
        store.write_activation(activation)
        raise LifecycleError("injected failure after activation record publication")

    monkeypatch.setattr(deploy_operation, "_run_command", command)
    monkeypatch.setattr(deploy_operation, "verify", verified)
    monkeypatch.setattr(LifecycleStore, "finalize_staged_locked", partial_finalize)

    result = deploy_operation.genesis(request)

    assert result.outcome == "failed"
    assert result.stage == "records"
    assert result.changed_stages == ("staging", "backup", "migration", "selection", "start", "records")
    assert result.lifecycle["activation_recorded"] is True
    assert (
        tmp_path / "install" / "deployments" / "activations" / "activation-0123456789abcdef0123456789abcdef.json"
    ).is_file()


def test_genesis_marks_started_service_when_start_command_errors_after_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service observation wins over an ambiguous post-start command failure."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)

    def command(stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
        if argv[:3] == ("systemctl", "start", "taskman.service"):
            raise RuntimeFailure(stage, "injected post-start transport failure")
        return ""

    monkeypatch.setattr(deploy_operation, "_run_command", command)
    monkeypatch.setattr(deploy_operation, "_observed_service_state", lambda: "active")

    result = deploy_operation.genesis(request)

    assert result.outcome == "failed"
    assert result.stage == "start"
    assert result.changed_stages == ("staging", "backup", "migration", "selection", "start")
    assert result.lifecycle["selected_release_id"] == RELEASE_ID
    assert result.lifecycle["service_state"] == "active"


def test_genesis_cleans_uploaded_artifact_when_post_upload_archive_preflight_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-upload archive inspection runs with cleanup already registered."""

    request, _archive, upload = _prepared_genesis_request(tmp_path)
    malformed = b"not a gzip archive"
    upload.write_bytes(malformed)
    upload.chmod(0o600)
    mapping = request.to_mapping()
    parameters = dict(mapping["parameters"])
    parameters["artifact_sha256"] = hashlib.sha256(malformed).hexdigest()
    mapping["parameters"] = parameters
    malformed_request = HostRequest.from_mapping(mapping)
    _accept_preflight(monkeypatch)

    result = deploy_operation.genesis(malformed_request)

    assert result.outcome == "refused"
    assert result.stage == "deploy-preflight"
    assert result.changed_stages == ()
    assert not upload.exists()
    assert result.residue_paths == ()


def test_genesis_refuses_insufficient_release_capacity_before_creating_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive expansion capacity is a preflight fact, not a staging side effect."""

    request, _archive, _upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)
    monkeypatch.setattr(deploy_operation, "available_bytes", lambda _path: 0)

    result = deploy_operation.genesis(request)

    assert result.outcome == "refused"
    assert result.stage == "deploy-preflight"
    assert result.changed_stages == ()
    assert not (tmp_path / "install" / "releases").exists()
    assert not (tmp_path / "backups").exists()


def test_genesis_runs_explicit_helper_stages_and_publishes_records_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful genesis has no predecessor and durable records follow verification.

    Reordering backup after migration, selecting a release non-atomically, or
    publishing lifecycle records before verification must make this contract
    fail through the real helper operation.
    """

    archive = tmp_path / "candidate.tar.gz"
    source = tmp_path / "source" / "taskman"
    for path in (source / "bin", source / "lib", source / "releases"):
        path.mkdir(parents=True, exist_ok=True)
    (source / "bin" / "server").write_text("#!/bin/sh\n", encoding="utf-8")
    (source / "bin" / "migrate").write_text("#!/bin/sh\n", encoding="utf-8")
    (source / "bin" / "server").chmod(0o750)
    (source / "bin" / "migrate").chmod(0o750)
    with tarfile.open(archive, "w:gz") as packed:
        packed.add(source, arcname="taskman")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()

    upload = tmp_path / "install" / "deployments" / "uploads" / ".upload.tar.gz"
    upload.parent.mkdir(parents=True)
    upload.parent.chmod(0o750)
    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    manifest = {
        "schema_version": 2,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": "b" * 40,
        "release_id": RELEASE_ID,
        "built_at": "2026-09-05T12:00:00Z",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "elixir_version": "1.18.3",
        "node_version": "22.22.1",
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "builder_base_tag": "ubuntu:resolute-20260811.1",
        "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
        "migrations": [],
        "top_level": "taskman",
    }
    request = HostRequest(
        protocol_version=1,
        operation="genesis",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"previous_release_id": None, "current_migrations": []},
        paths={"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        parameters={
            "candidate_release_id": RELEASE_ID,
            "artifact_sha256": checksum,
            "artifact_path": str(upload),
            "manifest": manifest,
            "migration_policy": "no-change",
            "manual_adoption": None,
            "verification": {
                "application_port": 4000,
                "distribution_port": 6789,
                "database_port": 5432,
                "public_hostname": "taskman.example.test",
                "public_ipv4": "203.0.113.10",
                "public_ipv6": None,
                "ssh_port": 22,
                "ssh_user": "deployer",
                "readiness_timeout": 1,
                "connection_timeout": 1,
                "database_host": "127.0.0.1",
                "database_role": "taskman",
                "database_name": "taskman_prod",
            },
        },
    )
    events: list[str] = []

    def command(stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        events.append(stage)
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
        if argv[0] == "psql":
            return "1048576\n"
        return ""

    def verified(value: HostRequest, **_kwargs: object) -> HostResult:
        events.append("verification")
        return HostResult(
            protocol_version=1,
            operation=value.operation,
            operation_id=value.operation_id,
            outcome="succeeded",
            stage="verified",
            changed_stages=(),
            lifecycle={},
            runtime_state={},
            verification={"schema_version": 1, "status": "ok"},
            residue_paths=(),
            recovery_actions=(),
            warnings=(),
        )

    monkeypatch.setattr(deploy_operation, "_safe_secret_file", lambda *_args: None)
    monkeypatch.setattr(deploy_operation, "_run_command", command)
    monkeypatch.setattr(deploy_operation, "verify", verified)

    def preflight(*_args: object) -> None:
        # Upload preparation is controller-owned, but no release or backup
        # root may be created until non-mutating host/database/capacity facts
        # have accepted this transaction.
        if not events:
            assert not (tmp_path / "install" / "releases").exists()
            assert not (tmp_path / "backups").exists()
        events.append("host-preflight")
        return None

    monkeypatch.setattr(deploy_operation, "host_preflight", preflight)
    monkeypatch.setattr(deploy_operation, "available_bytes", lambda _path: 20 * 1024 * 1024)

    result = deploy_operation.genesis(request)

    assert result.outcome == "succeeded", result
    assert result.stage == "records"
    assert result.changed_stages == (
        "staging", "backup", "migration", "selection", "start", "records"
    )
    assert events == ["host-preflight", "backup", "backup", "backup", "migration", "start", "verification"]
    assert result.lifecycle["previous_release_id"] is None
    assert result.lifecycle["selected_release_id"] == RELEASE_ID
    assert result.lifecycle["activation_recorded"] is True
    assert (tmp_path / "install" / "current").resolve() == tmp_path / "install" / "releases" / RELEASE_ID
    assert (tmp_path / "install" / "deployments" / "releases" / f"release-{RELEASE_ID}.json").is_file()
    assert (tmp_path / "install" / "deployments" / "activations" / "activation-0123456789abcdef0123456789abcdef.json").is_file()
    assert not upload.exists()

    # A completed genesis is a safe exact-current no-op on the same immutable
    # candidate, not a permanent clean-host refusal or a second backup.
    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    rerun = deploy_operation.genesis(request)

    assert rerun.outcome == "no_change", rerun
    assert rerun.stage == "already-current"
    assert rerun.changed_stages == ()
    assert rerun.lifecycle["selected_release_id"] == RELEASE_ID
    assert rerun.lifecycle["activation_recorded"] is True


def test_genesis_resumes_matching_post_rename_immutable_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matching post-rename provisional resumes rather than becoming a refusal.

    The prepared state is the exact durable shape left when selection renamed
    ``current`` and then failed before records. The operation itself, not a
    controller recovery program, performs the resumed start/verify/publication.
    """

    install = tmp_path / "install"
    backup_root = tmp_path / "backups"
    paths = ManagedPaths.from_mapping(
        {"install_root": install.as_posix(), "backup_root": backup_root.as_posix()}
    )
    store = LifecycleStore(paths, owner_uid=os.geteuid())
    release = install / "releases" / RELEASE_ID
    release.mkdir(parents=True)
    release.parent.chmod(0o750)
    release.chmod(0o750)
    archive = tmp_path / "candidate.tar.gz"
    source = tmp_path / "archive-source" / "taskman"
    source.mkdir(parents=True)
    with tarfile.open(archive, "w:gz") as packed:
        packed.add(source, arcname="taskman")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release / ".taskman-release.json").write_text(
        f'{{"schema_version":1,"release_id":"{RELEASE_ID}","artifact_sha256":"{checksum}"}}\n',
        encoding="utf-8",
    )
    (release / ".taskman-release.json").chmod(0o640)
    upload = install / "deployments" / "uploads" / ".upload.tar.gz"
    upload.parent.mkdir(parents=True)
    upload.parent.chmod(0o750)
    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    manifest = {
        "schema_version": 2, "application": "taskman", "application_version": "0.2.0",
        "source_revision": "b" * 40, "release_id": RELEASE_ID,
        "built_at": "2026-09-05T12:00:00Z", "target_os": "ubuntu26.04",
        "architecture": "amd64", "otp_version": "27.3.4.6", "elixir_version": "1.18.3",
        "node_version": "22.22.1", "hex_version": "2.5.1", "rebar3_version": "3.24.0",
        "builder_base_tag": "ubuntu:resolute-20260811.1",
        "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
        "migrations": [], "top_level": "taskman",
    }
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    backup_id = "backup-0123456789abcdef0123456789abcdef"
    dump = backup_root / f"{backup_id}.dump"
    backup_root.mkdir()
    backup_root.chmod(0o750)
    dump.write_bytes(b"validated backup")
    dump.chmod(0o600)
    backup = BackupRecord(
        1, backup_id, now, dump.stat().st_size, 1_048_576, "taskman_prod", None,
        RELEASE_ID, "pre-deploy", True, PurePosixPath(dump.as_posix()),
        hashlib.sha256(dump.read_bytes()).hexdigest(),
    )
    store.write_backup(backup)
    staged = StagedRelease(
        1, "activation-0123456789abcdef0123456789abcdef", None, RELEASE_ID, backup_id,
        "no-change", checksum, now, now, manifest,
    )
    store.stage_immutable(staged)
    (install / "current").symlink_to(release)
    request = HostRequest(
        protocol_version=1, operation="genesis", operation_id="op-fedcba9876543210fedcba9876543210",
        expected_state={"previous_release_id": None, "current_migrations": []},
        paths={"install_root": install.as_posix(), "backup_root": backup_root.as_posix()},
        parameters={
            "candidate_release_id": RELEASE_ID, "artifact_sha256": checksum,
            "artifact_path": upload.as_posix(), "manifest": manifest, "migration_policy": "no-change",
            "manual_adoption": None,
            "verification": {
                "application_port": 4000, "distribution_port": 6789, "database_port": 5432,
                "public_hostname": "taskman.example.test", "public_ipv4": "203.0.113.10",
                "public_ipv6": None, "ssh_port": 22, "ssh_user": "deployer",
                "readiness_timeout": 1, "connection_timeout": 1, "database_host": "127.0.0.1",
                "database_role": "taskman", "database_name": "taskman_prod",
            },
        },
    )

    monkeypatch.setattr(deploy_operation, "host_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy_operation, "available_bytes", lambda _path: 20 * 1024 * 1024)
    monkeypatch.setattr(deploy_operation, "_safe_secret_file", lambda *_args: None)
    monkeypatch.setattr(deploy_operation, "_database_size", lambda _settings: 1_048_576)
    monkeypatch.setattr(deploy_operation, "_run_command", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        deploy_operation,
        "verify",
        lambda value, **_kwargs: HostResult(
            protocol_version=1, operation=value.operation, operation_id=value.operation_id,
            outcome="succeeded", stage="verified", changed_stages=(), lifecycle={}, runtime_state={},
            verification={"schema_version": 1, "status": "ok"}, residue_paths=(),
            recovery_actions=(), warnings=(),
        ),
    )

    result = deploy_operation.genesis(request)

    assert result.outcome == "succeeded", (result.stage, dict(result.runtime_state), dict(result.lifecycle))
    assert result.changed_stages == ("start", "records")
    assert result.lifecycle["selected_release_id"] == RELEASE_ID
    assert result.lifecycle["activation_recorded"] is True
    assert result.lifecycle["activation_id"] == staged.activation_id
    assert result.runtime_state["resumed_activation_id"] == staged.activation_id
    assert store.read().current_release_id == RELEASE_ID
    assert not (install / "deployments" / "provisionals" / f"{staged.activation_id}.json").exists()


@pytest.mark.parametrize(
    "damage",
    ("missing", "replaced", "same-size-replaced", "invalid"),
)
def test_fresh_id_resume_revalidates_the_recorded_backup_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    """A fresh retry cannot trust a historical dump merely because it has a record.

    The first helper creates a real provisional after current replacement. A
    distinct operation ID then rediscovers it, but must refuse before start
    when the dump vanished, changed size, or no longer passes ``pg_restore``.
    """

    request, archive, upload = _prepared_genesis_request(tmp_path)
    _accept_preflight(monkeypatch)
    state = {"fail_current_fsync": True, "invalid_restore": False, "starts": 0}
    install = tmp_path / "install"
    original_fsync = deploy_operation._fsync_directory

    def command(stage: str, argv: tuple[str, ...], **_kwargs: object) -> str:
        if argv[0] == "pg_dump":
            dump = Path(argv[argv.index("--file") + 1])
            dump.write_bytes(b"validated backup")
            dump.chmod(0o600)
        if argv[0] == "pg_restore" and state["invalid_restore"]:
            raise RuntimeFailure(stage, "injected invalid historical dump")
        if argv[:3] == ("systemctl", "start", "taskman.service"):
            state["starts"] += 1
        return ""

    def fail_after_selection(path: Path) -> None:
        if state["fail_current_fsync"] and path == install:
            raise OSError("injected post-replace failure")
        original_fsync(path)

    monkeypatch.setattr(deploy_operation, "_run_command", command)
    monkeypatch.setattr(deploy_operation, "_fsync_directory", fail_after_selection)

    first = deploy_operation.genesis(request)
    assert first.stage == "selection"
    state["fail_current_fsync"] = False

    backup = tmp_path / "backups" / "backup-0123456789abcdef0123456789abcdef.dump"
    if damage == "missing":
        backup.unlink()
    elif damage == "replaced":
        backup.write_bytes(b"replacement has a different size")
        backup.chmod(0o600)
    elif damage == "same-size-replaced":
        backup.write_bytes(b"corrupted backup")
        backup.chmod(0o600)
    else:
        state["invalid_restore"] = True
    upload.write_bytes(archive.read_bytes())
    upload.chmod(0o600)
    rerun_mapping = request.to_mapping()
    rerun_mapping["operation_id"] = "op-fedcba9876543210fedcba9876543210"

    result = deploy_operation.genesis(HostRequest.from_mapping(rerun_mapping))

    assert result.outcome == "refused"
    assert result.stage == "deploy-preflight"
    assert result.changed_stages == ()
    assert state["starts"] == 0


def test_built_zipapp_genesis_refuses_a_declared_predecessor_without_creating_state(
    tmp_path: Path,
) -> None:
    """Genesis has no predecessor and cannot fabricate one during a refusal.

    Accepting a predecessor in the clean-host path would let a failure report
    an invented prior running release, which is a recovery-evidence bug.
    """

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = _request(
        tmp_path,
        "genesis",
        expected_state={"previous_release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"},
    )

    completed = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=encode_request(request),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    result = decode_result(completed.stdout)
    assert result.outcome == "refused"
    assert result.stage == "deploy-preflight"
    assert result.changed_stages == ()
    assert result.runtime_state == {}
    assert not (tmp_path / "install").exists()
