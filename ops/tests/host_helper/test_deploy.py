"""Convergence outcomes for the final deploy/genesis helper procedure."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
from collections.abc import Mapping
from dataclasses import replace

import pytest

from taskman_ops.host_helper.operations import deploy as deploy_module
from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper import services as service_capability
from taskman_ops.host_helper.operations.deploy import converge_deployment, deploy, genesis
from taskman_ops.host_helper.backup_protection import BackupProtection
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    selection_filename,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.helper_client.package import build_helper_package
from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION, decode_result, encode_request
from taskman_ops.host_protocol.mutation_results import validate_mutation_state
from taskman_ops.releases.manifests import (
    ARCHITECTURE,
    APPLICATION,
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    ELIXIR_VERSION,
    HEX_VERSION,
    NODE_VERSION,
    OTP_VERSION,
    REBAR3_VERSION,
    SCHEMA_VERSION,
    TARGET_OS,
    ArtifactManifest,
    MigrationFingerprint,
)
from taskman_ops.releases.identifiers import build_release_id


CORRELATION = "op-0123456789abcdef0123456789abcdef"
CURRENT_REVISION = "a" * 40
CANDIDATE_REVISION = "b" * 40
CURRENT = build_release_id("0.2.0", CURRENT_REVISION, artifact_sha256="c" * 64, source_dirty=False)
CANDIDATE = build_release_id("0.2.0", CANDIDATE_REVISION, artifact_sha256="b" * 64, source_dirty=False)
MIGRATION = MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64)
SECOND_MIGRATION = MigrationFingerprint("20260906120000_add_projects.exs", "e" * 64)


def _manifest(
    *,
    migrations: tuple[MigrationFingerprint, ...] = (MIGRATION,),
    application_version: str = "0.2.0",
    release_id: str = CANDIDATE,
    artifact_sha256: str = "b" * 64,
) -> ArtifactManifest:
    return ArtifactManifest(
        SCHEMA_VERSION, APPLICATION, application_version, CANDIDATE_REVISION, release_id,
        datetime(2026, 9, 7, 12, tzinfo=UTC), TARGET_OS, ARCHITECTURE, OTP_VERSION,
        ELIXIR_VERSION, NODE_VERSION, BUILDER_BASE_TAG, BUILDER_BASE_DIGEST, migrations,
        "taskman", HEX_VERSION, REBAR3_VERSION, artifact_sha256, False,
    )


def _candidate_id(request: HostRequest) -> str:
    target = request.parameters["target"]
    assert isinstance(target, Mapping)
    manifest = target["manifest"]
    assert isinstance(manifest, Mapping)
    release_id = manifest["release_id"]
    assert isinstance(release_id, str)
    return release_id


def _release_tree(path: Path) -> None:
    (path / "bin").mkdir(parents=True, exist_ok=True)
    (path / "lib").mkdir(exist_ok=True)
    (path / "releases").mkdir(exist_ok=True)
    for relative in ("bin/server", "bin/migrate", "lib/runtime", "releases/start_erl.data"):
        target = path / relative
        target.write_text("release\n")
        target.chmod(0o750 if relative.startswith("bin/") else 0o640)


def _archive(path: Path) -> str:
    source = path.parent / "archive-source" / "taskman"
    _release_tree(source)
    with tarfile.open(path, "w:gz") as archive:
        archive.add(source, arcname="taskman")
    path.chmod(0o600)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _roots(tmp_path: Path) -> dict[str, str]:
    return {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}


def _install_current(
    roots: dict[str, str],
    *,
    migrations: tuple[MigrationFingerprint, ...] = (),
) -> None:
    install = Path(roots["install_root"])
    backup = Path(roots["backup_root"])
    release = install / "releases" / CURRENT
    _release_tree(release)
    backup.mkdir(parents=True)
    paths = deploy_module.ManagedPaths.from_mapping(roots)
    current_manifest = ArtifactManifest(
        SCHEMA_VERSION, APPLICATION, "0.2.0", CURRENT_REVISION, CURRENT,
        datetime(2026, 9, 7, 12, tzinfo=UTC), TARGET_OS, ARCHITECTURE, OTP_VERSION,
        ELIXIR_VERSION, NODE_VERSION, BUILDER_BASE_TAG, BUILDER_BASE_DIGEST, migrations,
        "taskman", HEX_VERSION, REBAR3_VERSION, "c" * 64, False,
    )
    write_release_manifest(
        paths,
        ReleaseRecord(CURRENT, CURRENT_REVISION, "c" * 64, tuple(item.to_mapping() for item in migrations), 2, current_manifest),
    )
    install.mkdir(parents=True, exist_ok=True)
    (install / "current").symlink_to(release)
    append_selection(paths, SelectionRecord(CURRENT, None, None, datetime(2026, 9, 7, 11, tzinfo=UTC), 2, None, ()))


def _install_selected_candidate(request: HostRequest) -> None:
    """Install the request target as both physical and completed authority."""

    target = request.parameters["target"]
    assert isinstance(target, Mapping)
    manifest_mapping = target["manifest"]
    assert isinstance(manifest_mapping, Mapping)
    manifest = ArtifactManifest.from_mapping(deploy_module._mutable(manifest_mapping))
    artifact_sha256 = target["artifact_sha256"]
    assert isinstance(artifact_sha256, str)
    record = ReleaseRecord(
        manifest.release_id,
        manifest.source_revision,
        artifact_sha256,
        tuple(item.to_mapping() for item in manifest.migrations),
        2,
        manifest,
    )
    paths = deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    release = Path(paths.local(paths.release_root / record.release_id))
    _release_tree(release)
    Path(paths.local(paths.backup_root)).mkdir(parents=True)
    write_release_manifest(paths, record)
    install = Path(paths.local(paths.install_root))
    install.mkdir(parents=True, exist_ok=True)
    (install / "current").symlink_to(release)
    append_selection(
        paths,
        SelectionRecord(record.release_id, None, None, datetime(2026, 9, 7, 11, tzinfo=UTC), 2, None, ()),
    )


def _request(
    tmp_path: Path,
    *,
    operation: str = "deploy",
    previous: str | None = CURRENT,
    applied_migrations: tuple[int, ...] = (),
    migrations: tuple[MigrationFingerprint, ...] = (MIGRATION,),
    policy: str = "backward-compatible",
    candidate_release_id: str = CANDIDATE,
    application_version: str = "0.2.0",
) -> HostRequest:
    roots = _roots(tmp_path)
    upload = Path(roots["install_root"]) / "deployments" / "uploads" / "artifact.tar.gz"
    upload.parent.mkdir(parents=True, exist_ok=True)
    checksum = _archive(upload)
    candidate_release_id = build_release_id(
        application_version,
        CANDIDATE_REVISION,
        artifact_sha256=checksum,
        source_dirty=False,
    ) if candidate_release_id == CANDIDATE else candidate_release_id
    credentials = tmp_path / "pgpass"
    credentials.write_text("localhost:5432:*:taskman:secret\n")
    credentials.chmod(0o600)
    return HostRequest(
        PROTOCOL_VERSION, operation, CORRELATION,
        {
            "selected_release_id": previous,
            "last_successful_selection_id": selection_filename(
                SelectionRecord(CURRENT, None, None, datetime(2026, 9, 7, 11, tzinfo=UTC), 2, None, ())
            ) if previous is not None else None,
            "applied_migrations": applied_migrations,
            "backup_protection_sha256": hashlib.sha256(b"[]").hexdigest(),
            "scheduled_backup_sha256": "a" * 64,
            "backup_timer_enabled": True,
            "downgrade_baseline_sha256": hashlib.sha256(
                json.dumps([] if previous is None else [previous], separators=(",", ":")).encode("ascii")
            ).hexdigest(),
        }, roots,
        {
            "target": {"kind": "upload", "artifact_sha256": checksum, "artifact_path": str(upload), "manifest": _manifest(
                migrations=migrations,
                application_version=application_version,
                release_id=candidate_release_id,
                artifact_sha256=checksum,
            ).to_mapping()},
            "migration_policy": policy, "credentials_path": str(credentials),
            "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
            "verification": {
                "application_port": 4000, "distribution_port": 6789, "database_port": 5432,
                "public_hostname": "taskman.example.test", "public_ipv4": "203.0.113.10",
                "public_ipv6": None, "ssh_port": 22, "ssh_user": "deployer",
                "readiness_timeout": 1, "connection_timeout": 1,
            },
            "backup_helper": {"sha256": "a" * 64, "upload_path": None},
            "prune_backup_ids": [],
        },
    )


class _Runtime:
    def __init__(
        self,
        migrations: tuple[int, ...] = (),
        *,
        migration_result: tuple[int, ...] = (20260905120000,),
    ) -> None:
        self.migrations = migrations
        self.migration_result = migration_result
        self.events: list[str] = []
        self.backup_calls = 0
        self.service_running = False

    def observe_database(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"state": "ready", "applied_migrations": self.migrations}

    def backup(self, state, paths, *_args: object, **_kwargs: object) -> BackupRecord:
        self.events.append("backup")
        self.backup_calls += 1
        record = BackupRecord(
            f"backup-{self.backup_calls:032x}", datetime(2026, 9, 7, 12, 0, tzinfo=UTC), "e" * 64, state.selected_release_id,
            state.applied_migrations, 1024,
        )
        dump = Path(paths.local(paths.backup_root / f"{record.backup_id}.dump"))
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_bytes(b"backup")
        dump.chmod(0o600)
        record = BackupRecord(
            record.backup_id, record.created_at, hashlib.sha256(b"backup").hexdigest(), record.source_release_id,
            record.migration_versions, record.source_database_size_bytes,
        )
        write_backup_manifest(paths, record)
        return record

    def command(self, argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[:2] == ("systemctl", "stop"):
            self.events.append("stop")
            self.service_running = False
        elif argv[0] == "systemd-run":
            self.events.append("migration")
            self.migrations = self.migration_result
        elif argv[:2] == ("systemctl", "start"):
            self.events.append("start")
            self.service_running = True
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def verify(self, request: HostRequest, **_kwargs: object) -> HostResult:
        self.events.append("verify")
        return HostResult(PROTOCOL_VERSION, "verify", request.correlation_id, "succeeded", "verified", {"report": {"ok": True}}, ())


def _install_runtime(monkeypatch: pytest.MonkeyPatch, runtime: _Runtime) -> None:
    from taskman_ops.host_helper.backup_helper import BackupHelperConvergence, BackupHelperMutation

    monkeypatch.setattr(deploy_module, "observe_database_state_or_empty", runtime.observe_database)
    monkeypatch.setattr(deploy_module, "create_validated_backup", runtime.backup, raising=False)
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)
    monkeypatch.setattr(service_capability, "run_command", runtime.command)
    monkeypatch.setattr(deploy_module, "verify", runtime.verify, raising=False)
    monkeypatch.setattr(deploy_module, "host_preflight", lambda *_args: None, raising=False)
    monkeypatch.setattr(deploy_module, "_taskman_gid", os.getegid, raising=False)
    monkeypatch.setattr(
        "taskman_ops.host_helper.state._service_state",
        lambda include_runtime: "running" if include_runtime and runtime.service_running else "stopped",
    )
    monkeypatch.setattr(
        deploy_module,
        "converge_backup_helper",
        lambda _paths, _payload, **kwargs: BackupHelperConvergence(kwargs["lock"], BackupHelperMutation()),
    )
    monkeypatch.setattr(
        deploy_module,
        "_scheduler_facts",
        lambda _paths: {
            "scheduled_backup_sha256": "a" * 64,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
        },
    )


def _replanned_request(request: HostRequest, runtime: _Runtime) -> HostRequest:
    """Model the controller's fresh v3 expected-state observation for a retry."""

    paths = deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    state = deploy_module.observe_host_state(
        paths,
        database=runtime.observe_database(),
        allow_selection_transition=True,
    )
    projection = discover_module._deployment_projection(
        state,
        deploy_module._scheduler_facts(paths),
        mode="deploy",
    )
    return replace(
        request,
        expected_state={
            "selected_release_id": state.selected_release_id,
            "last_successful_selection_id": state.latest_successful_selection_filename,
            "applied_migrations": state.applied_migrations,
            "backup_protection_sha256": projection["backup_protection_sha256"],
            "scheduled_backup_sha256": projection["scheduled_backup_sha256"],
            "backup_timer_enabled": projection["backup_timer_enabled"],
            "downgrade_baseline_sha256": projection["downgrade_baseline_sha256"],
        },
    )


def test_deploy_refusal_keeps_the_complete_v3_mutation_evidence(tmp_path: Path) -> None:
    """A pre-mutation refusal cannot omit the result fields the controller validates."""

    request = HostRequest(
        PROTOCOL_VERSION,
        "deploy",
        CORRELATION,
        {
            "selected_release_id": CURRENT,
            "last_successful_selection_id": None,
            "applied_migrations": (),
            "backup_protection_sha256": hashlib.sha256(b"[]").hexdigest(),
            "scheduled_backup_sha256": "a" * 64,
            "backup_timer_enabled": True,
            "downgrade_baseline_sha256": hashlib.sha256(b"[]").hexdigest(),
        },
        _roots(tmp_path),
        {
            "target": {"kind": "installed", "release_record": _manifest().to_mapping()},
            "migration_policy": "no-change",
            "credentials_path": str(tmp_path / "pgpass"),
            "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
            "verification": {},
            "backup_helper": {"sha256": "a" * 64, "upload_path": None},
            "prune_backup_ids": [],
        },
    )

    result = deploy_module._result(request, "refused", "unsafe", None)

    assert validate_mutation_state("deploy", result.outcome, result.state)["mutation_state"] == "unchanged"


def test_converge_deployment_stages_backs_up_migrates_selects_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    original = deploy_module._append_selection_with_previous

    def record_selection(*args: object, **kwargs: object) -> None:
        runtime.events.append("selection")
        original(*args, **kwargs)

    monkeypatch.setattr(deploy_module, "_append_selection_with_previous", record_selection, raising=False)

    result = converge_deployment(request)

    assert result.outcome == "succeeded", (result.message, result.state)
    candidate_id = _candidate_id(request)
    assert result.state["observations"]["selected_release_id"] == candidate_id
    assert result.state["backup_id"] == "backup-00000000000000000000000000000001"
    assert runtime.events == ["backup", "stop", "migration", "start", "verify", "selection"]
    assert (Path(request.paths["install_root"]) / "current").resolve().name == candidate_id
    candidate = Path(request.paths["install_root"]) / "releases" / candidate_id
    for item in (candidate, *candidate.rglob("*")):
        details = item.lstat()
        assert details.st_uid == os.geteuid()
        assert details.st_gid == os.getegid()
        if stat.S_ISLNK(details.st_mode):
            continue
        mode = stat.S_IMODE(details.st_mode)
        assert (mode & stat.S_IWGRP) == 0
        assert (mode & 0o007) == 0
    assert stat.S_IMODE((candidate / "bin" / "server").stat().st_mode) == 0o750
    assert stat.S_IMODE((candidate / "bin" / "migrate").stat().st_mode) == 0o750
    assert stat.S_IMODE((candidate / "lib" / "runtime").stat().st_mode) == 0o640
    assert stat.S_IMODE((candidate / "releases" / "start_erl.data").stat().st_mode) == 0o640
    assert stat.S_IMODE((candidate / ".taskman-release.json").stat().st_mode) == 0o600


def test_real_discovery_supplies_migrated_predecessor_authority_to_real_deploy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty discovery projection would make an ordinary forward migration manual."""

    request = _request(
        tmp_path,
        applied_migrations=(20260905120000,),
        migrations=(MIGRATION, SECOND_MIGRATION),
    )
    _install_current(dict(request.paths), migrations=(MIGRATION,))
    runtime = _Runtime(
        (20260905120000,),
        migration_result=(20260905120000, 20260906120000),
    )
    _install_runtime(monkeypatch, runtime)
    monkeypatch.setattr(discover_module, "observe_database_state", runtime.observe_database, raising=False)
    monkeypatch.setattr(
        discover_module,
        "_scheduler_facts",
        lambda _paths: {
            "scheduled_backup_sha256": "a" * 64,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
        },
    )
    from taskman_ops.workflows import deploy as deploy_workflow
    from tests.workflows.test_deploy import config

    discovery_request = HostRequest(
        PROTOCOL_VERSION,
        "discover",
        CORRELATION,
        {},
        request.paths,
        {
            "credentials_path": request.parameters["credentials_path"],
            "database": request.parameters["database"],
            "mode": "deploy",
        },
    )
    monkeypatch.setattr(deploy_workflow, "discovery_request", lambda _config, **_kwargs: discovery_request)
    monkeypatch.setattr(
        deploy_workflow,
        "run_request", lambda _remote, live_request: discover_module.discover(live_request),
    )
    from taskman_ops.workflows import inventory

    current_record = deploy_module.observe_host_state(
        deploy_module.ManagedPaths.from_mapping(dict(request.paths)),
        database=runtime.observe_database(),
    ).releases[0]
    monkeypatch.setattr(inventory, "collect_inventory", lambda *_args, **_kwargs: (current_record.to_mapping(),))

    previous, fingerprints, actual = deploy_workflow._planning_authority(object(), config())
    forwarded = _request(
        tmp_path,
        applied_migrations=actual,
        migrations=(MIGRATION, SECOND_MIGRATION),
    )
    result = deploy(forwarded)

    assert previous == CURRENT
    assert fingerprints == (MIGRATION,)
    assert actual == (20260905120000,)
    assert result.outcome == "succeeded"
    assert runtime.events == ["backup", "stop", "migration", "start", "verify"]


def test_deploy_does_not_migrate_when_its_pre_deploy_backup_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migrating after an unrecorded backup failure would leave no recovery point."""

    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    def interrupt_backup(*_args: object, **_kwargs: object) -> BackupRecord:
        runtime.events.append("backup")
        raise deploy_module.CommandError("pre-deploy backup interrupted")

    monkeypatch.setattr(deploy_module, "create_validated_backup", interrupt_backup)

    result = deploy(_replanned_request(request, runtime))

    assert result.outcome == "retryable"
    assert result.state["failed_boundary"] == "backup"
    assert runtime.events == ["backup"]
    assert runtime.migrations == ()


def test_deploy_stops_the_selected_candidate_before_its_missing_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping this stop would migrate a database beneath the running app."""

    request = _request(tmp_path)
    _install_selected_candidate(request)
    runtime = _Runtime()
    runtime.service_running = True
    _install_runtime(monkeypatch, runtime)

    result = deploy(_replanned_request(request, runtime))

    assert result.outcome == "succeeded"
    assert runtime.events == ["backup", "stop", "migration", "start", "verify"]


def test_deploy_rerun_keeps_a_healthy_complete_candidate_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing the no-op guard would restart a verified completed release."""
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    assert deploy(request).outcome == "succeeded"

    result = deploy(_replanned_request(request, runtime))

    assert result.outcome == "succeeded"
    assert result.state["mutation_state"] == "unchanged"
    assert runtime.events == ["backup", "stop", "migration", "start", "verify", "verify"]


def test_genesis_scheduler_refresh_revalidates_the_first_release_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real helper refresh must revalidate the genesis admission under its lock."""

    from taskman_ops.host_helper import backup_helper

    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    request = replace(
        request,
        parameters={
            **request.parameters,
            "backup_helper": {
                "sha256": "b" * 64,
                "upload_path": str(Path(request.paths["install_root"]) / "deployments" / "uploads" / "backup.pyz"),
            },
        },
    )
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    events: list[str] = []
    monkeypatch.setattr(deploy_module, "converge_backup_helper", backup_helper.converge_backup_helper)
    monkeypatch.setattr(deploy_module, "_validate_starting_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        deploy_module,
        "_record_successful_selection",
        lambda _inputs, state, backup: (state, False, backup),
    )
    monkeypatch.setattr(backup_helper, "observe_backup_timer", lambda **_kwargs: (True, "inactive"))
    monkeypatch.setattr(backup_helper, "stop_backup_timer", lambda **_kwargs: events.append("pause"))
    monkeypatch.setattr(backup_helper, "start_backup_timer", lambda **_kwargs: events.append("restart"))
    monkeypatch.setattr(backup_helper, "_wait_for_backup_service", lambda _timeout: events.append("wait"))
    monkeypatch.setattr(backup_helper, "_verified_executable_checksum", lambda: "a" * 64)
    monkeypatch.setattr(backup_helper, "_validate_upload", lambda *_args: events.append("validate"))
    monkeypatch.setattr(backup_helper, "_replace_executable", lambda *_args: events.append("replace"))

    result = genesis(request)

    assert result.outcome == "succeeded", (result.message, result.state, events, runtime.events)
    assert events == ["validate", "pause", "wait", "replace", "restart"]


def test_partial_migration_history_uses_the_target_protected_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing a recovery backup's source must not discard its protection authority."""

    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime(migrations=(20260905120000,))
    _install_runtime(monkeypatch, runtime)
    inputs = deploy_module._inputs(request, first_release=False)
    state = deploy_module._observe(inputs)
    backup = BackupRecord(
        "backup-00000000000000000000000000000009",
        datetime(2026, 9, 7, 12, tzinfo=UTC),
        "e" * 64,
        _candidate_id(request),
        (20260905120000,),
        1024,
    )
    protection = BackupProtection(
        1,
        backup.backup_id,
        state.latest_successful_selection_filename,
        _candidate_id(request),
        1,
        datetime(2026, 9, 7, 12, tzinfo=UTC),
    )
    state = replace(state, backups=(backup,), backup_protections=(protection,))

    selected = deploy_module._selection_backup(inputs, state, None, None)

    assert selected == backup


def test_verified_start_is_retained_when_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later verification failure must not erase evidence that the service started."""

    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    monkeypatch.setattr(
        deploy_module,
        "verify",
        lambda verify_request, **_kwargs: HostResult(
            PROTOCOL_VERSION, "verify", verify_request.correlation_id, "retryable", "failed", {"report": {"ok": False}}, ()
        ),
    )

    result = deploy(request)

    assert result.outcome == "retryable"
    assert result.state["mutation_state"] == "changed"
    assert result.state["report"] == {"ok": False}


def test_confirmed_protection_pruning_finishes_before_another_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed prior retirement must stop before it creates another recovery point."""

    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    monkeypatch.setattr(
        deploy_module,
        "_finish_confirmed_pruning",
        lambda *_args: (_ for _ in ()).throw(deploy_module.RecordError("retirement interrupted")),
    )

    result = deploy(request)

    assert result.outcome == "retryable"
    assert result.state["failed_boundary"] == "protection"
    assert runtime.events == []


def test_successful_history_failure_keeps_the_report_and_history_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publication/reference-transfer is a history boundary after passing verification."""

    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    monkeypatch.setattr(
        deploy_module,
        "_append_selection_with_previous",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("history publication interrupted")),
    )

    result = deploy(request)

    assert result.outcome == "retryable"
    assert result.state["failed_boundary"] == "history"
    assert result.state["report"] == {"ok": True}


def test_deploy_reuses_an_installed_target_after_its_uploaded_archive_is_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installed immutable record, not a vanished upload, is retry authority."""

    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    assert deploy(request).outcome == "succeeded"

    paths = deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    candidate = next(
        release
        for release in deploy_module.observe_host_state(paths).releases
        if release.release_id == _candidate_id(request)
    )
    upload = Path(request.parameters["target"]["artifact_path"])
    upload.unlink()
    replanned = _replanned_request(request, runtime)
    parameters = dict(replanned.parameters)
    parameters["target"] = {"kind": "installed", "release_record": candidate.to_mapping()}

    result = deploy(replace(replanned, parameters=parameters))

    assert result.outcome == "succeeded"
    assert result.state["mutation_state"] == "unchanged"
    assert runtime.events == ["backup", "stop", "migration", "start", "verify", "verify"]


@pytest.mark.parametrize("boundary", ("selection", "service", "verification"))
def test_interrupted_late_deploy_replans_from_its_fresh_final_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    """A stale pre-selection snapshot would strand a retry after late work."""

    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    candidate_id = _candidate_id(request)

    with monkeypatch.context() as interrupted:
        if boundary == "selection":
            original_selection = deploy_module.select_current

            def lose_selection(*args: object, **kwargs: object) -> None:
                original_selection(*args, **kwargs)
                raise OSError("selection reply lost after atomic switch")

            interrupted.setattr(deploy_module, "select_current", lose_selection)
        elif boundary == "service":
            original_command = runtime.command

            def lose_start(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
                result = original_command(argv, **kwargs)
                if argv[:2] == ("systemctl", "start"):
                    raise deploy_module.CommandError("service reply lost after start")
                return result

            interrupted.setattr(service_capability, "run_command", lose_start)
        else:
            def fail_verification(
                verify_request: HostRequest, **_kwargs: object,
            ) -> HostResult:
                return HostResult(
                    PROTOCOL_VERSION,
                    "verify",
                    verify_request.correlation_id,
                    "retryable",
                    "not ready",
                    {"report": {"ok": False}},
                    (),
                )

            interrupted.setattr(deploy_module, "verify", fail_verification)

        first = deploy(request)

    assert first.outcome == "retryable"
    assert first.state["mutation_state"] == "changed"
    assert first.state["observations"]["selected_release_id"] == candidate_id
    assert first.state["observations"]["applied_migrations"] == (20260905120000,)

    retry = deploy(_replanned_request(request, runtime))

    assert retry.outcome == "succeeded", retry.message
    assert retry.state["observations"]["selected_release_id"] == candidate_id
    state = deploy_module.observe_host_state(deploy_module.ManagedPaths.from_mapping(dict(request.paths)))
    assert [selection.release_id for selection in state.selections] == [CURRENT, candidate_id]


def test_genesis_is_the_same_procedure_with_an_empty_host_precondition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, migrations=(), policy="no-change")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = genesis(request)

    assert result.outcome == "succeeded"
    assert result.state["observations"]["selected_release_id"] == _candidate_id(request)
    assert runtime.backup_calls == 0
    assert runtime.events == ["start", "verify"]


def test_genesis_applies_initial_migrations_under_restore_required_without_a_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = genesis(request)

    assert result.outcome == "succeeded"
    assert result.state["observations"]["selected_release_id"] == _candidate_id(request)
    assert result.state["observations"]["database_state"] == "ready"
    assert result.state["backup_id"] is None
    assert runtime.backup_calls == 0
    assert runtime.events == ["migration", "start", "verify"]


def test_genesis_replays_an_exact_staged_candidate_before_its_first_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    migration_attempts = 0

    def fail_before_database_change(
        argv: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal migration_attempts
        if argv[0] == "systemd-run":
            migration_attempts += 1
            raise deploy_module.CommandError("migration failed before database change")
        return runtime.command(argv, **kwargs)

    monkeypatch.setattr(deploy_module, "run_command", fail_before_database_change, raising=False)
    first = genesis(request)

    paths = deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    state = deploy_module.observe_host_state(paths, database=runtime.observe_database())
    assert first.outcome == "retryable"
    assert state.selected_release_id is None
    assert state.selections == ()
    assert state.applied_migrations == ()
    assert state.temporary_paths == ()
    assert len(state.releases) == 1
    assert state.releases[0].release_id == _candidate_id(request)
    assert state.releases[0].source_revision == CANDIDATE_REVISION
    assert state.releases[0].artifact_sha256 == request.parameters["target"]["artifact_sha256"]

    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)
    result = genesis(request)

    assert result.outcome == "succeeded"
    assert migration_attempts == 1
    assert runtime.events == ["migration", "start", "verify"]


def test_genesis_rejects_an_exact_staged_candidate_with_partial_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        tmp_path,
        operation="genesis",
        previous=None,
        migrations=(MIGRATION, SECOND_MIGRATION),
        policy="restore-required",
    )
    runtime = _Runtime(migration_result=(20260905120000,))
    _install_runtime(monkeypatch, runtime)

    def fail_after_partial_database_change(
        argv: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        result = runtime.command(argv, **kwargs)
        if argv[0] == "systemd-run":
            raise deploy_module.CommandError("migration result lost after partial change")
        return result

    monkeypatch.setattr(deploy_module, "run_command", fail_after_partial_database_change, raising=False)
    first = genesis(request)
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)

    result = genesis(request)

    assert first.outcome == "retryable"
    assert result.outcome == "manual"
    assert result.state["observations"]["selected_release_id"] is None
    assert runtime.events == ["migration"]


def test_genesis_rejects_an_exact_staged_candidate_with_a_pre_migration_current_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    def fail_before_database_change(
        argv: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if argv[0] == "systemd-run":
            raise deploy_module.CommandError("migration failed before database change")
        return runtime.command(argv, **kwargs)

    monkeypatch.setattr(deploy_module, "run_command", fail_before_database_change, raising=False)
    first = genesis(request)
    install = Path(request.paths["install_root"])
    (install / "current").symlink_to(install / "releases" / _candidate_id(request))
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)

    result = genesis(request)

    assert first.outcome == "retryable"
    assert result.outcome == "manual"
    assert result.state["observations"]["selected_release_id"] == _candidate_id(request)
    assert runtime.events == []


def test_genesis_rejects_an_exact_staged_candidate_with_a_pre_migration_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    def fail_before_database_change(
        argv: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if argv[0] == "systemd-run":
            raise deploy_module.CommandError("migration failed before database change")
        return runtime.command(argv, **kwargs)

    monkeypatch.setattr(deploy_module, "run_command", fail_before_database_change, raising=False)
    first = genesis(request)
    append_selection(
        deploy_module.ManagedPaths.from_mapping(dict(request.paths)),
        SelectionRecord(_candidate_id(request), None, None, datetime(2026, 9, 7, 12, tzinfo=UTC), 2, None, ()),
    )
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)

    result = genesis(request)

    assert first.outcome == "retryable"
    assert result.outcome == "manual"
    assert result.state["observations"]["selected_release_id"] is None
    assert runtime.events == []


@pytest.mark.parametrize("applied_migrations", ((999,), (20260905120000,)))
def test_genesis_rejects_unowned_database_migrations_before_any_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    applied_migrations: tuple[int, ...],
) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    runtime = _Runtime(applied_migrations)
    _install_runtime(monkeypatch, runtime)

    result = genesis(request)

    paths = deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    state = deploy_module.observe_host_state(paths, database=runtime.observe_database())
    assert result.outcome == "manual"
    assert runtime.events == []
    assert runtime.backup_calls == 0
    assert state.backups == ()
    assert not Path(paths.local(paths.release_root / _candidate_id(request))).exists()
    assert not Path(paths.local(paths.release_root / f".release-{_candidate_id(request)}.tmp")).exists()


def test_genesis_recognizes_interrupted_staging_only_with_the_clean_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            deploy_module,
            "_extract_release",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(tarfile.TarError("interrupted")),
            raising=False,
        )
        first = genesis(request)

    state = deploy_module.observe_host_state(
        deploy_module.ManagedPaths.from_mapping(dict(request.paths)),
        database=runtime.observe_database(),
    )
    result = genesis(request)

    assert first.outcome == "retryable"
    assert state.applied_migrations == ()
    assert Path(state.temporary_paths[0].as_posix()).name == f".release-{_candidate_id(request)}.tmp"
    assert result.outcome == "succeeded"
    assert runtime.backup_calls == 0
    assert runtime.events == ["migration", "start", "verify"]


def test_genesis_recovers_lost_migration_result_only_from_the_exact_candidate_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    original = runtime.command

    def lose_result(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = original(argv, **kwargs)
        if argv[0] == "systemd-run":
            raise deploy_module.CommandError("transport result lost after migration")
        return result

    monkeypatch.setattr(deploy_module, "run_command", lose_result, raising=False)
    first = genesis(request)
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)

    paths = deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    state = deploy_module.observe_host_state(paths, database=runtime.observe_database())
    result = genesis(request)

    assert first.outcome == "retryable"
    assert state.selected_release_id is None
    assert state.applied_migrations == (20260905120000,)
    assert len(state.releases) == 1
    assert state.releases[0].release_id == _candidate_id(request)
    assert state.releases[0].source_revision == CANDIDATE_REVISION
    assert state.releases[0].artifact_sha256 == request.parameters["target"]["artifact_sha256"]
    assert result.outcome == "succeeded"
    assert runtime.events.count("migration") == 1
    assert runtime.backup_calls == 0


def test_genesis_recovers_lost_selection_record_only_from_the_exact_candidate_current_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, policy="restore-required")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            deploy_module,
            "_append_selection_with_previous",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("lost selection record")),
            raising=False,
        )
        first = genesis(request)

    state = deploy_module.observe_host_state(
        deploy_module.ManagedPaths.from_mapping(dict(request.paths)),
        database=runtime.observe_database(),
        allow_selection_transition=True,
    )
    result = genesis(request)

    assert first.outcome == "retryable"
    assert state.selected_release_id == _candidate_id(request)
    assert state.applied_migrations == (20260905120000,)
    assert state.selections == ()
    assert result.outcome == "succeeded"
    assert runtime.backup_calls == 0
    assert result.state["backup_id"] is None


@pytest.mark.parametrize("boundary", ("staging", "backup", "migration"))
def test_recognizable_interruption_converges_when_the_same_deploy_is_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    with monkeypatch.context() as interrupted:
        if boundary == "staging":
            def fail_staging(*_args: object, **_kwargs: object) -> None:
                raise tarfile.TarError("interrupted")

            interrupted.setattr(deploy_module, "_extract_release", fail_staging, raising=False)
        elif boundary == "backup":
            def fail_backup(*_args: object, **_kwargs: object) -> BackupRecord:
                raise deploy_module.CommandError("interrupted")

            interrupted.setattr(deploy_module, "create_validated_backup", fail_backup, raising=False)
        elif boundary == "migration":
            original = runtime.command

            def fail_migration(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
                result = original(argv, **kwargs)
                if argv[0] == "systemd-run":
                    raise deploy_module.CommandError("lost migration result")
                return result

            interrupted.setattr(deploy_module, "run_command", fail_migration, raising=False)
        elif boundary == "selection":
            def fail_selection(*_args: object, **_kwargs: object) -> None:
                raise OSError("interrupted selection")

            interrupted.setattr(deploy_module, "select_current", fail_selection)
        elif boundary == "start":
            original = runtime.command

            def fail_start(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
                if argv[:2] == ("systemctl", "start"):
                    raise deploy_module.CommandError("interrupted start")
                return original(argv, **kwargs)

            interrupted.setattr(service_capability, "run_command", fail_start)
        else:
            def fail_readiness(request: HostRequest, **_kwargs: object) -> HostResult:
                return HostResult(PROTOCOL_VERSION, "verify", request.correlation_id, "retryable", "not ready", {}, ())

            interrupted.setattr(deploy_module, "verify", fail_readiness, raising=False)

        first = deploy(request)

    result = deploy(_replanned_request(request, runtime))

    assert first.outcome == "retryable"
    assert result.outcome == "succeeded"
    assert result.state["observations"]["selected_release_id"] == _candidate_id(request)


def test_lost_migration_result_observes_applied_versions_and_only_starts_the_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    original = runtime.command

    def lose_result(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = original(argv, **kwargs)
        if argv[0] == "systemd-run":
            raise deploy_module.CommandError("transport result lost after migration")
        return result

    monkeypatch.setattr(deploy_module, "run_command", lose_result, raising=False)
    assert deploy(request).outcome == "retryable"
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)

    result = deploy(_replanned_request(request, runtime))

    assert result.outcome == "succeeded"
    assert runtime.migrations == (20260905120000,)
    assert runtime.events.count("migration") == 1
    assert runtime.backup_calls == 1
    assert result.state["backup_id"] is None
    state = deploy_module.observe_host_state(deploy_module.ManagedPaths.from_mapping(dict(request.paths)))
    assert state.selections[-1].backup_id == "backup-00000000000000000000000000000001"
    assert "start" in runtime.events


def test_selection_record_lost_after_atomic_switch_converges_on_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    def lose_selection_record(*args: object, **kwargs: object) -> None:
        raise OSError("interrupted after the atomic selection")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(deploy_module, "_append_selection_with_previous", lose_selection_record, raising=False)
        first = deploy(request)

    result = deploy(_replanned_request(request, runtime))

    assert first.outcome == "retryable"
    assert result.outcome == "succeeded"
    state = deploy_module.observe_host_state(deploy_module.ManagedPaths.from_mapping(dict(request.paths)))
    assert [selection.release_id for selection in state.selections] == [CURRENT, _candidate_id(request)]


def test_lost_migration_result_with_multiple_matching_backups_requires_manual_intervention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    original = runtime.command

    def lose_result(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = original(argv, **kwargs)
        if argv[0] == "systemd-run":
            raise deploy_module.CommandError("transport result lost after migration")
        return result

    monkeypatch.setattr(deploy_module, "run_command", lose_result, raising=False)
    assert deploy(request).outcome == "retryable"
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)
    paths = deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    observed = deploy_module.observe_host_state(paths)
    runtime.backup(observed, paths)

    result = deploy(_replanned_request(request, runtime))

    assert result.outcome == "succeeded"


def test_recorded_candidate_with_current_reverted_to_predecessor_requires_manual_intervention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    assert deploy(request).outcome == "succeeded"
    current = Path(request.paths["install_root"]) / "current"
    current.unlink()
    current.symlink_to(Path(request.paths["install_root"]) / "releases" / CURRENT)
    runtime.events.clear()

    result = deploy(request)

    assert result.outcome == "manual"
    assert current.resolve().name == CURRENT
    assert runtime.events == []


def test_prerelease_staging_is_observed_then_converges_on_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = "0.2.0-rc.1"
    request = _request(
        tmp_path,
        application_version=version,
    )
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            deploy_module,
            "_extract_release",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(tarfile.TarError("interrupted")),
            raising=False,
        )
        first = deploy(request)

    state = deploy_module.observe_host_state(deploy_module.ManagedPaths.from_mapping(dict(request.paths)))
    result = deploy(request)

    assert first.outcome == "retryable"
    assert Path(state.temporary_paths[0].as_posix()).name == f".release-{_candidate_id(request)}.tmp"
    assert result.outcome == "succeeded"


def test_contradictory_release_schema_identity_requires_manual_intervention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime((999,))
    _install_runtime(monkeypatch, runtime)

    result = deploy(request)

    assert result.outcome == "manual"
    assert result.state["observations"]["selected_release_id"] == CURRENT
    assert runtime.events == []


def test_normalize_release_tree_assigns_runtime_group_without_following_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "bin").mkdir()
    executable = release / "bin" / "server"
    executable.write_text("server\n")
    executable.chmod(0o750)
    data = release / "runtime.config"
    data.write_text("runtime\n")
    outside = tmp_path / "outside"
    outside.write_text("outside\n")
    link = release / "outside-link"
    link.symlink_to(outside)
    chown_calls: list[tuple[Path, int, int]] = []
    lchown_calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(
        deploy_module.os,
        "chown",
        lambda path, uid, gid: chown_calls.append((Path(path), uid, gid)),
    )
    monkeypatch.setattr(
        deploy_module.os,
        "lchown",
        lambda path, uid, gid: lchown_calls.append((Path(path), uid, gid)),
    )

    deploy_module._normalize_release_tree(release, owner_uid=101, owner_gid=202)

    assert set(chown_calls) == {
        (release, 101, 202),
        (release / "bin", 101, 202),
        (executable, 101, 202),
        (data, 101, 202),
    }
    assert lchown_calls == [(link, 101, 202)]
    assert outside not in {path for path, _uid, _gid in chown_calls + lchown_calls}
    assert stat.S_IMODE(release.stat().st_mode) == 0o750
    assert stat.S_IMODE(executable.stat().st_mode) == 0o750
    assert stat.S_IMODE(data.stat().st_mode) == 0o640


def test_write_release_manifest_assigns_runtime_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    target = release / ".taskman-release.json"
    chown_calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(
        deploy_module.os,
        "chown",
        lambda path, uid, gid: chown_calls.append((Path(path), uid, gid)),
    )
    record = ReleaseRecord(CANDIDATE, CANDIDATE_REVISION, "b" * 64, (MIGRATION.to_mapping(),), 2, _manifest())

    deploy_module._write_release_manifest(release, record, owner_uid=101, owner_gid=202)

    assert chown_calls == [(target, 101, 202)]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_taskman_gid_resolves_the_named_runtime_group(monkeypatch: pytest.MonkeyPatch) -> None:
    names: list[str] = []

    class _Group:
        gr_gid = 202

    def getgrnam(name: str) -> _Group:
        names.append(name)
        return _Group()

    monkeypatch.setattr(deploy_module.grp, "getgrnam", getgrnam)

    assert deploy_module._taskman_gid() == 202
    assert names == ["taskman"]


def test_missing_taskman_group_requires_manual_intervention(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> object:
        raise KeyError("taskman")

    monkeypatch.setattr(deploy_module.grp, "getgrnam", missing)

    with pytest.raises(deploy_module.DeploymentManualError, match="taskman service group is unavailable"):
        deploy_module._taskman_gid()


def test_packaged_deploy_uses_the_final_protocol_directly(tmp_path: Path) -> None:
    """Malformed input reaches the final deploy refusal, never a legacy bridge."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        PROTOCOL_VERSION,
        "deploy",
        CORRELATION,
        {},
        _roots(tmp_path),
        {},
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(package.path)],
        input=encode_request(request),
        capture_output=True,
        check=False,
    )
    result = decode_result(completed.stdout)

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "refused"
    assert result.state["failed_boundary"] == "input"
    assert validate_mutation_state("deploy", result.outcome, result.state)["mutation_state"] == "unchanged"
