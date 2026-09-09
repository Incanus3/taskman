"""Convergence outcomes for the final deploy/genesis helper procedure."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import hashlib
import os
import stat
import subprocess
import sys
import tarfile

import pytest

from taskman_ops.host_helper.operations import deploy as deploy_module
from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper import services as service_capability
from taskman_ops.host_helper.operations.deploy import converge_deployment, deploy, genesis
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.helper_client.package import build_helper_package
from taskman_ops.host_protocol import HostRequest, HostResult, decode_result, encode_request
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
CURRENT = build_release_id("0.2.0", CURRENT_REVISION)
CANDIDATE = build_release_id("0.2.0", CANDIDATE_REVISION)
MIGRATION = MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64)
SECOND_MIGRATION = MigrationFingerprint("20260906120000_add_projects.exs", "e" * 64)


def _manifest(
    *,
    migrations: tuple[MigrationFingerprint, ...] = (MIGRATION,),
    application_version: str = "0.2.0",
    release_id: str = CANDIDATE,
) -> ArtifactManifest:
    return ArtifactManifest(
        SCHEMA_VERSION, APPLICATION, application_version, CANDIDATE_REVISION, release_id,
        datetime(2026, 9, 7, 12, tzinfo=UTC), TARGET_OS, ARCHITECTURE, OTP_VERSION,
        ELIXIR_VERSION, NODE_VERSION, BUILDER_BASE_TAG, BUILDER_BASE_DIGEST, migrations,
        "taskman", HEX_VERSION, REBAR3_VERSION,
    )


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
    write_release_manifest(
        paths,
        ReleaseRecord(CURRENT, CURRENT_REVISION, "c" * 64, tuple(item.to_mapping() for item in migrations)),
    )
    install.mkdir(parents=True, exist_ok=True)
    (install / "current").symlink_to(release)
    append_selection(paths, SelectionRecord(CURRENT, None, None, datetime(2026, 9, 7, 11, tzinfo=UTC)))


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
    credentials = tmp_path / "pgpass"
    credentials.write_text("localhost:5432:*:taskman:secret\n")
    credentials.chmod(0o600)
    return HostRequest(
        2, operation, CORRELATION,
        {"selected_release_id": previous, "applied_migrations": applied_migrations}, roots,
        {
            "candidate_release_id": candidate_release_id, "artifact_sha256": checksum,
            "artifact_path": str(upload),
            "manifest": _manifest(
                migrations=migrations,
                application_version=application_version,
                release_id=candidate_release_id,
            ).to_mapping(),
            "migration_policy": policy, "credentials_path": str(credentials),
            "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
            "verification": {
                "application_port": 4000, "distribution_port": 6789, "database_port": 5432,
                "public_hostname": "taskman.example.test", "public_ipv4": "203.0.113.10",
                "public_ipv6": None, "ssh_port": 22, "ssh_user": "deployer",
                "readiness_timeout": 1, "connection_timeout": 1,
            },
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
        elif argv[0] == "systemd-run":
            self.events.append("migration")
            self.migrations = self.migration_result
        elif argv[:2] == ("systemctl", "start"):
            self.events.append("start")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def verify(self, request: HostRequest, **_kwargs: object) -> HostResult:
        self.events.append("verify")
        return HostResult(2, "verify", request.correlation_id, "succeeded", "verified", {"report": {"ok": True}}, ())


def _install_runtime(monkeypatch: pytest.MonkeyPatch, runtime: _Runtime) -> None:
    monkeypatch.setattr(deploy_module, "observe_database_state_or_empty", runtime.observe_database)
    monkeypatch.setattr(deploy_module, "create_validated_backup", runtime.backup, raising=False)
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)
    monkeypatch.setattr(service_capability, "run_command", runtime.command)
    monkeypatch.setattr(deploy_module, "verify", runtime.verify, raising=False)
    monkeypatch.setattr(deploy_module, "host_preflight", lambda *_args: None, raising=False)
    monkeypatch.setattr(deploy_module, "_taskman_gid", os.getegid, raising=False)


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

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == CANDIDATE
    assert result.state["backup_id"] == "backup-00000000000000000000000000000001"
    assert runtime.events == ["backup", "stop", "migration", "start", "verify", "selection"]
    assert (Path(request.paths["install_root"]) / "current").resolve().name == CANDIDATE
    candidate = Path(request.paths["install_root"]) / "releases" / CANDIDATE
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
    assert stat.S_IMODE((candidate / ".taskman-release.json").stat().st_mode) == 0o640


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
    from taskman_ops.workflows import deploy as deploy_workflow
    from tests.workflows.test_deploy import config

    discovery_request = HostRequest(
        2,
        "discover",
        CORRELATION,
        {},
        request.paths,
        {
            "credentials_path": request.parameters["credentials_path"],
            "database": request.parameters["database"],
        },
    )
    monkeypatch.setattr(deploy_workflow, "discovery_request", lambda _config: discovery_request)
    monkeypatch.setattr(
        deploy_workflow,
        "run_request", lambda _remote, live_request: discover_module.discover(live_request),
    )

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

    result = deploy(request)

    assert result.outcome == "retryable"
    assert result.state["failed_boundary"] == "backup"
    assert runtime.events == ["backup"]
    assert runtime.migrations == ()


def test_deploy_rerun_is_a_noop_when_the_candidate_is_already_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    _install_current(dict(request.paths))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)
    assert deploy(request).outcome == "succeeded"

    result = deploy(request)

    assert result.outcome == "succeeded"
    assert result.state["changed"] is False
    assert runtime.events == ["backup", "stop", "migration", "start", "verify", "start", "verify"]


def test_genesis_is_the_same_procedure_with_an_empty_host_precondition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path, operation="genesis", previous=None, migrations=(), policy="no-change")
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = genesis(request)

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == CANDIDATE
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
    assert result.state["selected_release_id"] == CANDIDATE
    assert result.state["database_state"] == "changed"
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
    assert state.releases[0].release_id == CANDIDATE
    assert state.releases[0].source_revision == CANDIDATE_REVISION
    assert state.releases[0].artifact_sha256 == request.parameters["artifact_sha256"]

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
    assert result.state["selected_release_id"] is None
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
    (install / "current").symlink_to(install / "releases" / CANDIDATE)
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)

    result = genesis(request)

    assert first.outcome == "retryable"
    assert result.outcome == "manual"
    assert result.state["selected_release_id"] == CANDIDATE
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
        SelectionRecord(CANDIDATE, None, None, datetime(2026, 9, 7, 12, tzinfo=UTC)),
    )
    monkeypatch.setattr(deploy_module, "run_command", runtime.command, raising=False)

    result = genesis(request)

    assert first.outcome == "retryable"
    assert result.outcome == "manual"
    assert result.state["selected_release_id"] is None
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
    assert not Path(paths.local(paths.release_root / CANDIDATE)).exists()
    assert not Path(paths.local(paths.release_root / f".release-{CANDIDATE}.tmp")).exists()


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
    assert Path(state.temporary_paths[0].as_posix()).name == f".release-{CANDIDATE}.tmp"
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
    assert state.releases[0].release_id == CANDIDATE
    assert state.releases[0].source_revision == CANDIDATE_REVISION
    assert state.releases[0].artifact_sha256 == request.parameters["artifact_sha256"]
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
    assert state.selected_release_id == CANDIDATE
    assert state.applied_migrations == (20260905120000,)
    assert state.selections == ()
    assert result.outcome == "succeeded"
    assert runtime.backup_calls == 0
    assert result.state["backup_id"] is None


@pytest.mark.parametrize("boundary", ("staging", "backup", "migration", "selection", "start", "readiness"))
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
                return HostResult(2, "verify", request.correlation_id, "retryable", "not ready", {}, ())

            interrupted.setattr(deploy_module, "verify", fail_readiness, raising=False)

        first = deploy(request)

    result = deploy(request)

    assert first.outcome == "retryable"
    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == CANDIDATE


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

    result = deploy(request)

    assert result.outcome == "succeeded"
    assert runtime.migrations == (20260905120000,)
    assert runtime.events.count("migration") == 1
    assert runtime.backup_calls == 1
    assert result.state["backup_id"] == "backup-00000000000000000000000000000001"
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

    result = deploy(request)

    assert first.outcome == "retryable"
    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == CANDIDATE
    assert result.state["backup_id"] == "backup-00000000000000000000000000000001"
    state = deploy_module.observe_host_state(deploy_module.ManagedPaths.from_mapping(dict(request.paths)))
    assert state.selections[-1].backup_id == "backup-00000000000000000000000000000001"


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

    result = deploy(request)

    assert result.outcome == "manual"
    assert result.state["selected_release_id"] == CANDIDATE


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
    candidate = build_release_id(version, CANDIDATE_REVISION)
    request = _request(
        tmp_path,
        candidate_release_id=candidate,
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
    assert Path(state.temporary_paths[0].as_posix()).name == f".release-{candidate}.tmp"
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
    assert result.state["selected_release_id"] == CURRENT
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
    record = ReleaseRecord(CANDIDATE, CANDIDATE_REVISION, "c" * 64, ())

    deploy_module._write_release_manifest(release, record, owner_uid=101, owner_gid=202)

    assert chown_calls == [(target, 101, 202)]
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


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
        2,
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
    assert "failed_boundary" not in result.state
