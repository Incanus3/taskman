"""Protocol-v3 host restore consequence and interruption contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskman_ops.host_helper import backup_protection as protection_module
from taskman_ops.host_helper.backup_helper import BackupHelperError, BackupHelperMutation
from taskman_ops.host_helper.backup_protection import BackupProtection, write_backup_protection
from taskman_ops.host_helper.operations import restore as restore_module
from taskman_ops.host_helper.records import BackupRecord, ReleaseRecord, SelectionRecord, append_selection, write_backup_manifest, write_release_manifest
from taskman_ops.host_helper.restore_target import RestoreTarget, replace_restore_target, restore_target_sha256, write_restore_target
from taskman_ops.host_helper.state import observe_host_state
from taskman_ops.host_protocol import HostRequest, HostResult, validate_mutation_state
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import APPLICATION, ARCHITECTURE, BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, ELIXIR_VERSION, HEX_VERSION, NODE_VERSION, OTP_VERSION, REBAR3_VERSION, SCHEMA_VERSION, TARGET_OS, ArtifactManifest, MigrationFingerprint
from tests.host_helper.support import database_mapping, managed_paths, verification_settings
from tests.workflows.support import successful_verification_report


CORRELATION = "op-0123456789abcdef0123456789abcdef"
CURRENT_REVISION = "a" * 40
TARGET_REVISION = "b" * 40
CURRENT = build_release_id("0.2.0", CURRENT_REVISION, artifact_sha256="c" * 64, source_dirty=False)
TARGET = build_release_id("0.2.0", TARGET_REVISION, artifact_sha256="d" * 64, source_dirty=False)
INPUT_BACKUP = "backup-" + "a" * 32
PROTECTION_BACKUP = "backup-" + "b" * 32
OLD_HELPER = "1" * 64
NEW_HELPER = "2" * 64
MIGRATION = MigrationFingerprint("20260905120000_create_tasks.exs", "e" * 64)
VERSION = 20260905120000


def _manifest(release_id: str, revision: str, digest: str) -> ArtifactManifest:
    return ArtifactManifest(
        SCHEMA_VERSION, APPLICATION, "0.2.0", revision, release_id,
        datetime(2026, 9, 14, 10, tzinfo=UTC), TARGET_OS, ARCHITECTURE, OTP_VERSION,
        ELIXIR_VERSION, NODE_VERSION, BUILDER_BASE_TAG, BUILDER_BASE_DIGEST, (MIGRATION,),
        "taskman", HEX_VERSION, REBAR3_VERSION, digest, False,
    )


def _release(paths, release_id: str, revision: str, digest: str) -> None:
    root = Path(paths.local(paths.release_root / release_id))
    root.mkdir(parents=True)
    root.chmod(0o750)
    manifest = _manifest(release_id, revision, digest)
    write_release_manifest(paths, ReleaseRecord(release_id, revision, digest, (MIGRATION.to_mapping(),), 2, manifest))


def _backup(paths, backup_id: str, release_id: str, contents: bytes) -> BackupRecord:
    dump = Path(paths.local(paths.backup_root / f"{backup_id}.dump"))
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_bytes(contents)
    dump.chmod(0o600)
    record = BackupRecord(backup_id, datetime(2026, 9, 14, 11, tzinfo=UTC), hashlib.sha256(contents).hexdigest(), release_id, (VERSION,), 1024)
    write_backup_manifest(paths, record)
    return record


def _seed(tmp_path: Path, *, protection: bool = False):
    paths = managed_paths(tmp_path)
    _release(paths, CURRENT, CURRENT_REVISION, "c" * 64)
    _release(paths, TARGET, TARGET_REVISION, "d" * 64)
    append_selection(paths, SelectionRecord(CURRENT, None, None, datetime(2026, 9, 14, 10, tzinfo=UTC), 2, None, ()))
    Path(paths.local(paths.current_link)).symlink_to(Path(paths.local(paths.release_root / CURRENT)))
    source = _backup(paths, INPUT_BACKUP, TARGET, b"restore source")
    if protection:
        _backup(paths, PROTECTION_BACKUP, CURRENT, b"protected source")
        write_backup_protection(
            paths,
            BackupProtection(1, PROTECTION_BACKUP, observe_host_state(paths).latest_successful_selection_filename, TARGET, 0, datetime(2026, 9, 14, 11, tzinfo=UTC)),
        )
    return paths, source


def _protection_digest(state) -> str:
    records = [item.to_mapping() for item in sorted((*state.backup_protections, *state.retiring_backup_protections), key=lambda item: item.backup_id)]
    return hashlib.sha256(json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


class Runtime:
    def __init__(self, arrangement: str = "canonical") -> None:
        self.events: list[str] = []
        self.next_oid = 300
        self.scheduler_sha256 = OLD_HELPER
        self.scheduler_failure = False
        self.verification_failure = False
        self.verification_lost = False
        self.cleanup_failure: str | None = None
        self.safety_count = 0
        self.later_write: str | None = None
        original = {"oid": 101, "owner": "taskman", "migration_table_present": True, "applied_migrations": (VERSION,)}
        restored = {"oid": 202, "owner": "taskman", "migration_table_present": True, "applied_migrations": (VERSION,)}
        empty = {"oid": 202, "owner": "taskman", "migration_table_present": False, "applied_migrations": None}
        self.databases: dict[str, dict[str, object] | None] = {"canonical": original, "temporary": None, "retired": None}
        if arrangement == "canonical+temporary":
            self.databases["temporary"] = empty
        elif arrangement == "temporary+retired":
            self.databases = {"canonical": None, "temporary": restored, "retired": original}
        elif arrangement == "retired":
            self.databases = {"canonical": None, "temporary": None, "retired": original}
        elif arrangement == "canonical+retired":
            self.databases = {"canonical": restored, "temporary": None, "retired": original}

    def observe_databases(self, *_args, **_kwargs):
        return {key: None if value is None else dict(value) for key, value in self.databases.items()}

    def converge_helper(self, paths, helper, *, revalidate, lock, **_kwargs):
        self.events.append("scheduler")
        assert helper == {"sha256": NEW_HELPER, "upload_path": "/opt/taskman/deployments/uploads/backup.pyz"}
        revalidate()
        if self.scheduler_failure:
            raise BackupHelperError("interrupted", BackupHelperMutation(paused=True))
        self.scheduler_sha256 = NEW_HELPER
        return SimpleNamespace(lock=lock, mutation=BackupHelperMutation(paused=True, replaced=True, restarted=True))

    def safety_backup(self, state, paths, database, *_args, **_kwargs):
        self.events.append(f"backup:{database['name']}")
        backup_id = "backup-" + chr(ord("c") + self.safety_count) * 32
        self.safety_count += 1
        return _backup(paths, backup_id, state.selected_release_id or CURRENT, f"safety-{self.safety_count}".encode())

    def create_temporary(self, *_args):
        self.events.append("create")
        self.next_oid += 1
        self.databases["temporary"] = {"oid": self.next_oid, "owner": "taskman", "migration_table_present": False, "applied_migrations": None}

    def register(self, paths, target, *_args):
        self.events.append("register")
        temporary = self.databases["temporary"]
        assert temporary is not None and target.temporary_creation_pending
        updated = replace(target, restored_database_oid=temporary["oid"], temporary_creation_pending=False)
        replace_restore_target(paths, updated)
        return updated

    def begin_rebuild(self, paths, target):
        self.events.append("pending")
        updated = replace(target, temporary_creation_pending=True)
        replace_restore_target(paths, updated)
        return updated

    def drop_temporary(self, _database, _credentials, oid):
        self.events.append("drop-temporary")
        temporary = self.databases["temporary"]
        if temporary is not None:
            assert temporary["oid"] == oid
            self.databases["temporary"] = None

    def load(self, target, *_args):
        self.events.append("load")
        self.later_write = None
        temporary = self.databases["temporary"]
        assert temporary is not None and temporary["oid"] == target.restored_database_oid and not target.temporary_creation_pending
        temporary.update(migration_table_present=True, applied_migrations=(VERSION,))

    def rename(self, _database, _credentials, source, destination, oid):
        self.events.append(f"rename:{source}:{destination}")
        value = self.databases[source]
        assert value is not None and value["oid"] == oid and self.databases[destination] is None
        self.databases[destination], self.databases[source] = value, None

    def drop_retired(self, _database, _credentials, oid):
        self.events.append("drop-retired")
        if self.cleanup_failure == "retired":
            self.cleanup_failure = None
            raise restore_module.RestoreDatabaseError("interrupted")
        retired = self.databases["retired"]
        if retired is not None:
            assert retired["oid"] == oid
            self.databases["retired"] = None

    def remove_target(self, paths):
        self.events.append("remove-binding")
        if self.cleanup_failure == "binding":
            self.cleanup_failure = None
            raise restore_module.RecordError("interrupted")
        restore_module._remove_restore_target_file(paths)

    def service(self, action: str):
        self.events.append(action)

    def verify(self, request, **_kwargs):
        self.events.append("verify")
        if self.verification_lost:
            raise restore_module.CommandError("verification result lost")
        report = successful_verification_report(TARGET)
        if self.verification_failure:
            report = dict(report)
            checks = [dict(item) for item in report["checks"]]
            checks[-1]["status"] = "failed"
            report.update(status="failed", exit_status=9, checks=checks, next_action="inspect the fixed verification summaries and correct the reported host state before retrying")
            return HostResult.for_request(request, "retryable", "failed", {"report": report})
        return HostResult.for_request(request, "succeeded", "verified", {"report": report})


def _binding(paths, source: BackupRecord, runtime: Runtime) -> RestoreTarget:
    state = observe_host_state(paths, allow_selection_transition=True)
    target = RestoreTarget(
        1, source.backup_id, source.dump_sha256, source.source_release_id,
        state.latest_successful_selection_filename, CURRENT, 101, None, True,
        "backup-" + "c" * 32, None,
        ({"backup_id": "backup-" + "c" * 32, "attempt_number": 0},),
    )
    canonical = runtime.databases["canonical"]
    if runtime.databases["temporary"] is not None or canonical is not None and canonical["oid"] == 202:
        target = replace(target, restored_database_oid=202, temporary_creation_pending=False)
    write_restore_target(paths, target)
    return target


def _request(paths, runtime: Runtime) -> HostRequest:
    state = observe_host_state(paths, allow_selection_transition=True)
    databases = runtime.observe_databases()
    canonical = databases["canonical"]
    expected = {
        "selected_release_id": state.selected_release_id,
        "last_successful_selection_id": state.latest_successful_selection_filename,
        "applied_migrations": None if canonical is None or not canonical["migration_table_present"] else canonical["applied_migrations"],
        "backup_protection_sha256": _protection_digest(state),
        "scheduled_backup_sha256": runtime.scheduler_sha256,
        "backup_timer_enabled": True,
        "backup_id": INPUT_BACKUP,
        "restore_target_sha256": None if state.restore_target is None else restore_target_sha256(state.restore_target),
        "restore_database_state": databases,
    }
    return HostRequest(
        3, "restore", CORRELATION, expected,
        {"install_root": paths.install_root.as_posix(), "backup_root": paths.backup_root.as_posix()},
        {
            "backup_id": INPUT_BACKUP,
            "credentials_path": "/etc/taskman/pgpass",
            "database": database_mapping(),
            "verification": verification_settings(),
            "backup_helper": {"sha256": NEW_HELPER, "upload_path": "/opt/taskman/deployments/uploads/backup.pyz"},
            "prune_backup_ids": [],
            "replace_unfinished": False,
            "reapply": False,
        },
    )


def _install(monkeypatch: pytest.MonkeyPatch, runtime: Runtime) -> None:
    monkeypatch.setattr(restore_module, "validate_credentials", lambda *_args: None)
    monkeypatch.setattr(restore_module, "observe_restore_databases", runtime.observe_databases)
    monkeypatch.setattr(restore_module, "_scheduler_facts", lambda *_args: {"scheduled_backup_sha256": runtime.scheduler_sha256, "backup_timer_enabled": True, "backup_timer_state": "active"})
    monkeypatch.setattr(restore_module, "converge_backup_helper", runtime.converge_helper)
    monkeypatch.setattr(restore_module, "create_validated_backup", runtime.safety_backup)
    monkeypatch.setattr(restore_module, "create_temporary_database", runtime.create_temporary)
    monkeypatch.setattr(restore_module, "register_restored_database", runtime.register)
    monkeypatch.setattr(restore_module, "begin_temporary_rebuild", runtime.begin_rebuild)
    monkeypatch.setattr(restore_module, "drop_registered_temporary", runtime.drop_temporary)
    monkeypatch.setattr(restore_module, "load_registered_temporary", runtime.load)
    monkeypatch.setattr(restore_module, "rename_registered_database", runtime.rename)
    monkeypatch.setattr(restore_module, "drop_registered_retired", runtime.drop_retired)
    monkeypatch.setattr(restore_module, "remove_restore_target", runtime.remove_target)
    monkeypatch.setattr(restore_module, "change_service", runtime.service)
    monkeypatch.setattr(restore_module, "verify", runtime.verify)
    monkeypatch.setattr(restore_module, "_validate_source_dump", lambda *_args: None)
    monkeypatch.setattr(restore_module, "run_command", lambda *_args, **_kwargs: SimpleNamespace(stdout=b""))
    monkeypatch.setattr(restore_module, "_terminate_connections", lambda *_args: None)
    monkeypatch.setattr(restore_module, "available_bytes", lambda *_args: 1_000_000)


def test_scheduler_converges_before_any_supported_write_and_failure_publishes_no_binding(tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path)
    runtime = Runtime()
    runtime.scheduler_failure = True
    _install(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, runtime))

    assert result.outcome == "retryable"
    assert result.state["failed_boundary"] == "backup_helper"
    validate_mutation_state("restore", "retryable", result.state)
    assert runtime.events == ["scheduler"]
    assert not Path(paths.local(paths.restore_target_path)).exists()


def test_new_restore_publishes_binding_before_create_and_registers_oid_before_load(tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path, protection=True)
    runtime = Runtime()
    _install(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, runtime))

    assert result.outcome == "succeeded"
    validate_mutation_state("restore", "succeeded", result.state)
    assert result.state["pre_restore_backup_id"] == "backup-" + "c" * 32
    assert runtime.events.index("scheduler") < runtime.events.index("backup:taskman") < runtime.events.index("create")
    assert runtime.events.index("register") < runtime.events.index("load")
    assert runtime.databases["canonical"]["oid"] == 301
    assert runtime.databases["temporary"] is None and runtime.databases["retired"] is None
    state = observe_host_state(paths)
    latest = state.latest_successful_selection
    assert latest is not None and latest.release_id == TARGET
    assert latest.backup_id == "backup-" + "c" * 32
    assert latest.recovery_backup_ids == tuple(sorted({INPUT_BACKUP, PROTECTION_BACKUP, "backup-" + "c" * 32}))
    assert state.restore_target is None and not state.backup_protections


@pytest.mark.parametrize("arrangement", ["canonical+temporary", "temporary+retired", "retired", "canonical+retired"])
def test_same_backup_recovers_each_bound_arrangement(arrangement, tmp_path, monkeypatch):
    paths, source = _seed(tmp_path)
    runtime = Runtime(arrangement)
    _backup(paths, "backup-" + "c" * 32, CURRENT, b"safety")
    _binding(paths, source, runtime)
    _install(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, runtime))

    assert result.outcome == "succeeded"
    assert runtime.databases["canonical"] is not None and runtime.databases["retired"] is None
    if arrangement == "canonical+retired":
        assert "load" not in runtime.events and "verify" in runtime.events
    else:
        assert "load" in runtime.events


def test_partial_temporary_is_rebuilt_with_pending_intent_and_new_oid(tmp_path, monkeypatch):
    paths, source = _seed(tmp_path)
    runtime = Runtime("canonical+temporary")
    runtime.databases["temporary"].update(migration_table_present=True, applied_migrations=())
    _backup(paths, "backup-" + "c" * 32, CURRENT, b"safety")
    _binding(paths, source, runtime)
    _install(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, runtime))

    assert result.outcome == "succeeded"
    assert runtime.events.index("pending") < runtime.events.index("drop-temporary") < runtime.events.index("create")
    assert runtime.databases["canonical"]["oid"] == 301


@pytest.mark.parametrize(("field", "value"), [("oid", 999), ("owner", "intruder")])
def test_bound_restore_refuses_wrong_original_identity(field, value, tmp_path, monkeypatch):
    paths, source = _seed(tmp_path)
    runtime = Runtime("canonical+temporary")
    _backup(paths, "backup-" + "c" * 32, CURRENT, b"safety")
    _binding(paths, source, runtime)
    runtime.databases["canonical"][field] = value
    _install(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, runtime))

    assert result.outcome in {"manual", "refused"}
    assert "load" not in runtime.events and runtime.databases["canonical"] is not None


@pytest.mark.parametrize("lost", [False, True])
def test_failed_or_lost_verification_preserves_original_binding_and_protection(lost, tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path, protection=True)
    runtime = Runtime()
    runtime.verification_failure = not lost
    runtime.verification_lost = lost
    _install(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, runtime))

    assert result.outcome == "retryable"
    assert result.state["failed_boundary"] == ("restore" if lost else "verification")
    validate_mutation_state("restore", "retryable", result.state)
    state = observe_host_state(paths, allow_selection_transition=True)
    assert state.restore_target is not None and state.backup_protections
    assert state.latest_successful_selection.release_id == CURRENT
    assert runtime.databases["retired"]["oid"] == 101


def test_success_publication_interruption_retries_only_protection_and_database_cleanup(tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path, protection=True)
    runtime = Runtime()
    _install(monkeypatch, runtime)
    original_remove = protection_module._remove_resolved_protections
    interrupted = False

    def interrupt_once(*args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise restore_module.RecordError("protection cleanup interrupted")
        return original_remove(*args, **kwargs)

    monkeypatch.setattr(protection_module, "_remove_resolved_protections", interrupt_once)

    first = restore_module.restore(_request(paths, runtime))

    assert first.outcome == "retryable"
    assert observe_host_state(paths, allow_selection_transition=True).latest_successful_selection.release_id == TARGET
    history_count = len(observe_host_state(paths, allow_selection_transition=True).selections)
    runtime.events.clear()
    second = restore_module.restore(_request(paths, runtime))

    assert second.outcome == "succeeded"
    assert "load" not in runtime.events and "verify" not in runtime.events and "scheduler" not in runtime.events
    assert len(observe_host_state(paths).selections) == history_count
    assert not observe_host_state(paths).backup_protections


@pytest.mark.parametrize("failure", ["retired", "binding"])
def test_retry_after_durable_success_only_finishes_cleanup(failure, tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path)
    runtime = Runtime()
    runtime.cleanup_failure = failure
    _install(monkeypatch, runtime)
    interrupted = restore_module.restore(_request(paths, runtime))
    assert interrupted.outcome == "retryable"
    history_count = len(observe_host_state(paths, allow_selection_transition=True).selections)
    assert observe_host_state(paths, allow_selection_transition=True).latest_successful_selection.release_id == TARGET
    runtime.events.clear()

    completed = restore_module.restore(_request(paths, runtime))

    assert completed.outcome == "succeeded"
    assert "load" not in runtime.events and "verify" not in runtime.events and "scheduler" not in runtime.events
    assert len(observe_host_state(paths).selections) == history_count
    assert observe_host_state(paths).restore_target is None


def test_deferred_replacement_mode_is_refused_without_mutation(tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path)
    runtime = Runtime()
    _install(monkeypatch, runtime)
    request = _request(paths, runtime)

    result = restore_module.restore(replace(request, parameters={**request.parameters, "replace_unfinished": True}))

    assert result.outcome == "refused" and result.state["failed_boundary"] == "input"
    assert runtime.events == []


def test_reapply_after_completed_restore_takes_fresh_safety_backup_and_appends_success(tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path)
    runtime = Runtime()
    _install(monkeypatch, runtime)
    first = restore_module.restore(_request(paths, runtime))
    assert first.outcome == "succeeded"
    first_selection = observe_host_state(paths).latest_successful_selection_filename
    runtime.events.clear()

    request = _request(paths, runtime)
    reapplied = restore_module.restore(
        replace(request, parameters={**request.parameters, "reapply": True})
    )

    assert reapplied.outcome == "succeeded"
    assert "backup:taskman" in runtime.events and "load" in runtime.events
    assert reapplied.state["pre_restore_backup_id"] == "backup-" + "d" * 32
    reapply_state = observe_host_state(paths)
    assert reapply_state.latest_successful_selection_filename != first_selection
    assert reapply_state.latest_successful_selection.backup_id == "backup-" + "d" * 32


def test_failed_reapply_binding_resumes_as_an_ordinary_restore(tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path)
    runtime = Runtime()
    _install(monkeypatch, runtime)
    assert restore_module.restore(_request(paths, runtime)).outcome == "succeeded"
    runtime.verification_failure = True
    request = _request(paths, runtime)
    failed = restore_module.restore(
        replace(request, parameters={**request.parameters, "reapply": True})
    )
    assert failed.outcome == "retryable"
    runtime.verification_failure = False
    runtime.events.clear()

    resumed = restore_module.restore(_request(paths, runtime))

    assert resumed.outcome == "succeeded"
    assert "backup:taskman" not in runtime.events and "load" not in runtime.events
    assert observe_host_state(paths).restore_target is None


def test_reapply_refuses_an_unfinished_restore_binding(tmp_path, monkeypatch):
    paths, source = _seed(tmp_path)
    runtime = Runtime("canonical+temporary")
    _backup(paths, "backup-" + "c" * 32, CURRENT, b"safety")
    _binding(paths, source, runtime)
    _install(monkeypatch, runtime)
    request = _request(paths, runtime)

    result = restore_module.restore(
        replace(request, parameters={**request.parameters, "reapply": True})
    )

    assert result.outcome == "refused"
    assert result.state["failed_boundary"] == "input"
    assert runtime.events == []


def test_ordinary_completed_retry_preserves_later_application_writes(tmp_path, monkeypatch):
    paths, _source = _seed(tmp_path)
    runtime = Runtime()
    _install(monkeypatch, runtime)
    assert restore_module.restore(_request(paths, runtime)).outcome == "succeeded"
    runtime.later_write = "preserved"
    history = observe_host_state(paths).latest_successful_selection_filename
    runtime.events.clear()

    retried = restore_module.restore(_request(paths, runtime))

    assert retried.outcome == "succeeded"
    assert retried.state["mutation_state"] == "unchanged"
    assert "load" not in runtime.events and "backup:taskman" not in runtime.events
    assert runtime.later_write == "preserved"
    assert observe_host_state(paths).latest_successful_selection_filename == history
