"""Public restore recovery admission, confirmation, and result evidence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_helper.commands import CommandError
from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    selection_filename,
)
from taskman_ops.host_helper.restore_target import RestoreTarget, restore_target_sha256
from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import ArtifactManifest, MigrationFingerprint, OTP_VERSION
from taskman_ops.workflows.restore import restore
from taskman_ops.workflows.helper import mutable
from tests.workflows.support import deployment_artifact, successful_verification_report
from tests.workflows.test_deploy import config
from tests.host_helper import test_restore as host_restore_tests


BACKUP = "backup-" + "a" * 32
SAFETY = "backup-" + "b" * 32
MIGRATION = 20260905120000
FAILED_CURRENT = build_release_id(
    "0.3.0",
    "d" * 40,
    artifact_sha256="d" * 64,
    source_dirty=False,
    otp_version=OTP_VERSION,
)
PROTECTION_SHA = hashlib.sha256(b"[]").hexdigest()
SCHEDULER_SHA = "c" * 64
DESIRED_SCHEDULER_SHA = "e" * 64


class _Package:
    sha256 = DESIRED_SCHEDULER_SHA
    path = None


@contextmanager
def _scheduler_package():
    yield _Package()


def _authority(tmp_path, *, current=FAILED_CURRENT, with_success=True, databases=None, target=None):
    fingerprint = MigrationFingerprint("20260905120000_create_tasks.exs", "f" * 64)
    artifact = deployment_artifact(tmp_path, migrations=(fingerprint,))
    release = ReleaseRecord(
        artifact.manifest.release_id,
        artifact.manifest.source_revision,
        artifact.sha256,
        tuple(item.to_mapping() for item in artifact.manifest.migrations),
        2,
        artifact.manifest,
    )
    backup = BackupRecord(
        BACKUP,
        datetime(2026, 9, 14, 10, 30, tzinfo=UTC),
        "a" * 64,
        release.release_id,
        (MIGRATION,),
        1024,
    )
    selection = (
        SelectionRecord(
            release.release_id,
            None,
            SAFETY,
            datetime(2026, 9, 14, 11, 0, tzinfo=UTC),
            2,
            None,
            (BACKUP, SAFETY),
        )
        if with_success
        else None
    )
    if databases is None:
        databases = {
            "canonical": {
                "oid": 41,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": [MIGRATION],
            },
            "temporary": None,
            "retired": None,
        }
    state = {
        "selected_release_id": current,
        "last_successful_selection_id": None if selection is None else selection_filename(selection),
        "last_successful_selection": None if selection is None else selection.to_mapping(),
        "previous_successful_selection": None,
        "applied_migrations": (
            None
            if databases["canonical"] is None
            or not databases["canonical"]["migration_table_present"]
            else list(databases["canonical"]["applied_migrations"])
        ),
        "service_state": "failed",
        "database_state": "absent" if databases["canonical"] is None else "ready",
        "backup_protections": [],
        "independently_held_backup_ids": [],
        "backup_protection_sha256": PROTECTION_SHA,
        "scheduled_backup_sha256": SCHEDULER_SHA,
        "backup_timer_enabled": True,
        "backup_timer_state": "active",
        "restore_target": (
            None
            if target is None
            else {**target.to_mapping(), "sha256": restore_target_sha256(target)}
        ),
        "restore_database_state": databases,
    }
    return release, backup, selection, state


def _install_controller_fakes(monkeypatch, release, backup, discoveries, mutations):
    database_sizes = {
        role: None if value is None else 4096
        for role, value in discoveries[0]["restore_database_state"].items()
    }
    monkeypatch.setattr("taskman_ops.workflows.restore.validate_restore_preflight", lambda *_: type("Facts", (), {"available_disk_bytes": 10_000, "backup_available_disk_bytes": 20_000, "database_available_disk_bytes": 10_000, "database_size_bytes": database_sizes})())
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_inspection_preflight",
        lambda *_args: object(),
    )
    monkeypatch.setattr("taskman_ops.workflows.restore.temporary_scheduled_backup_helper_package", _scheduler_package)
    release_records = [release.to_mapping()]
    selected = discoveries[0]["selected_release_id"]
    if selected == FAILED_CURRENT:
        failed_manifest = release.artifact_manifest.to_mapping()
        failed_manifest.update(
            application_version="0.3.0",
            source_revision="d" * 40,
            release_id=FAILED_CURRENT,
            artifact_sha256="d" * 64,
        )
        release_records.append(
            ReleaseRecord(
                FAILED_CURRENT,
                "d" * 40,
                "d" * 64,
                tuple(item.to_mapping() for item in release.artifact_manifest.migrations),
                2,
                ArtifactManifest.from_mapping(failed_manifest),
            ).to_mapping()
        )
    backup_records = [backup.to_mapping()]
    target = discoveries[0]["restore_target"]
    if target is not None:
        if target["backup_id"] != backup.backup_id:
            backup_records.append(
                BackupRecord(
                    target["backup_id"],
                    datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
                    target["dump_sha256"],
                    target["source_release_id"],
                    (MIGRATION,),
                    1024,
                ).to_mapping()
            )
        known = {item["backup_id"] for item in backup_records}
        for attempt in target["safety_backup_attempts"]:
            if attempt["backup_id"] not in known:
                backup_records.append(
                    BackupRecord(
                        attempt["backup_id"],
                        datetime(2026, 9, 14, 9, 30, tzinfo=UTC),
                        "b" * 64,
                        release.release_id,
                        (MIGRATION,),
                        1024,
                    ).to_mapping()
                )
                known.add(attempt["backup_id"])
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.collect_inventory",
        lambda _remote, _config, operation, **_kwargs: (
            tuple(release_records) if operation == "list_releases" else tuple(backup_records)
        ),
    )

    def discover(_remote, request):
        assert request.operation == "discover"
        assert request.parameters["mode"] == "restore"
        assert request.parameters["backup_id"] == BACKUP
        state = discoveries.pop(0)
        return HostResult(PROTOCOL_VERSION, "discover", request.correlation_id, "succeeded", "observed", state, ())

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", discover)

    def mutate(_remote, _config, **kwargs):
        mutations.append(kwargs)
        result = kwargs.pop("result") if "result" in kwargs else None
        if result is not None:
            return result
        source = release.release_id
        observations = {
            "selected_release_id": source,
            "last_successful_selection_id": "selection-" + "9" * 64 + ".json",
            "applied_migrations": [MIGRATION],
            "protected_backup_ids": [],
            "backup_protection_sha256": PROTECTION_SHA,
            "restore_target_sha256": None,
            "database_state": "ready",
            "service_state": "running",
            "scheduled_backup_sha256": DESIRED_SCHEDULER_SHA,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
            "restore_database_state": {
                "canonical": {
                    "oid": 42,
                    "owner": "taskman",
                    "migration_table_present": True,
                    "applied_migrations": [MIGRATION],
                },
                "temporary": None,
                "retired": None,
            },
        }
        request = kwargs["request"]
        return HostResult(
            PROTOCOL_VERSION,
            "restore",
            request.correlation_id,
            "succeeded",
            "converged",
            {
                "mutation_state": "changed",
                "exit_code": 0,
                "failed_boundary": None,
                "observations": observations,
                "unavailable_fields": [],
                "inspection_error": None,
                "report": successful_verification_report(source),
                "desired_release_id": source,
                "backup_id": BACKUP,
                "pre_restore_backup_id": SAFETY,
            },
            (),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_restore_request", mutate)


@pytest.mark.parametrize(
    ("current", "with_success"),
    ((FAILED_CURRENT, True), (None, False), (FAILED_CURRENT, False)),
)
def test_restore_plans_from_restore_specific_authority_after_failed_or_first_install(
    tmp_path, monkeypatch, current, with_success
):
    release, backup, selection, state = _authority(
        tmp_path, current=current, with_success=with_success
    )
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    outcome = restore(object(), config(), BACKUP, dry_run=True)

    assert outcome.exit_status is ExitStatus.OK
    assert outcome.stage == "planned"
    assert outcome.facts["physical_current_release_id"] == current
    assert outcome.facts["last_successful_selection"] == (
        None if selection is None else selection.to_mapping()
    )
    assert outcome.facts["canonical_applied_migrations"] == [MIGRATION]
    assert outcome.facts["requested_backup"] == backup.to_mapping()
    assert outcome.facts["typed_confirmation"] == f"restore production {BACKUP}"
    assert outcome.facts["scheduler_refresh_required"] is True
    assert outcome.facts["planned_pre_restore_backup"] is True
    assert mutations == []


def test_restore_refuses_when_native_database_volume_cannot_hold_remaining_work(
    tmp_path, monkeypatch
):
    release, backup, _selection, state = _authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight",
        lambda *_args: type(
            "Facts",
            (),
            {
                "available_disk_bytes": 10_000,
                "backup_available_disk_bytes": 20_000,
                "database_available_disk_bytes": 2_047,
                "database_size_bytes": {
                    "canonical": 4096,
                    "temporary": None,
                    "retired": None,
                },
            },
        )(),
    )

    outcome = restore(object(), config(), BACKUP, dry_run=True)

    assert outcome.exit_status is ExitStatus.SAFETY
    assert outcome.facts["plan"]["available_database_bytes"] == 2_047
    assert mutations == []


@pytest.mark.parametrize("failure", ("corrupt", "missing", "unreadable", "invalid-list"))
def test_restore_dry_run_validates_selected_dump_content_before_plan_or_prompt(
    failure, tmp_path, monkeypatch
):
    paths, source = host_restore_tests._seed(tmp_path)
    dump = Path(paths.local(paths.backup_root / f"{source.backup_id}.dump"))
    if failure == "corrupt":
        dump.write_bytes(b"changed restore source")
    elif failure == "missing":
        dump.unlink()
    elif failure == "unreadable":
        dump.chmod(0)

    actual_config = config().model_copy(
        update={"install_root": paths.install_root, "backup_root": paths.backup_root}
    )
    runtime = host_restore_tests.Runtime()
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight",
        lambda *_args: type(
            "Facts",
            (),
            {
                "available_disk_bytes": 10_000,
                "backup_available_disk_bytes": 20_000,
                "database_available_disk_bytes": 10_000,
                "database_size_bytes": {
                    "canonical": 4096,
                    "temporary": None,
                    "retired": None,
                },
            },
        )(),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_inspection_preflight",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.temporary_scheduled_backup_helper_package",
        _scheduler_package,
    )
    monkeypatch.setattr(discover_module, "validate_credentials", lambda *_args: None)
    monkeypatch.setattr(discover_module, "observe_restore_databases", runtime.observe_databases)
    monkeypatch.setattr(
        discover_module,
        "_scheduler_facts",
        lambda *_args: {
            "scheduled_backup_sha256": host_restore_tests.OLD_HELPER,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
        },
    )

    def native(argv, **_kwargs):
        if failure == "invalid-list" and argv[:2] == ("pg_restore", "--list"):
            raise CommandError("invalid dump directory")
        return type("Completed", (), {"stdout": b""})()

    monkeypatch.setattr(discover_module, "run_command", native)
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.run_request",
        lambda _remote, request: discover_module.discover(request),
    )
    state = host_restore_tests.observe_host_state(
        paths,
        database={"state": "ready", "applied_migrations": (host_restore_tests.VERSION,)},
        allow_selection_transition=True,
    ) if failure not in {"missing", "unreadable"} else None
    if state is not None:
        monkeypatch.setattr(
            "taskman_ops.workflows.restore.collect_inventory",
            lambda _remote, _config, operation, **_kwargs: (
                tuple(item.to_mapping() for item in state.releases)
                if operation == "list_releases"
                else tuple(item.to_mapping() for item in state.backups)
            ),
        )
    prompted = []

    outcome = restore(
        object(),
        actual_config,
        source.backup_id,
        dry_run=True,
        confirm=lambda plan: prompted.append(plan) or True,
    )

    assert outcome.exit_status is ExitStatus.BACKUP
    assert outcome.stage == "backup-failed"
    assert outcome.changed is False
    assert outcome.facts["plan"] is None
    assert prompted == []


def test_restore_dispatches_exact_v3_state_and_parameters_and_preserves_result_evidence(
    tmp_path, monkeypatch
):
    release, backup, _selection, state = _authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    outcome = restore(
        object(), config(), BACKUP, confirm=lambda plan: plan["typed_confirmation"] == f"restore production {BACKUP}"
    )

    sent = mutations[0]
    request = sent["request"]
    assert set(request.expected_state) == {
        "selected_release_id", "last_successful_selection_id", "applied_migrations",
        "backup_protection_sha256", "scheduled_backup_sha256", "backup_timer_enabled",
        "backup_id", "restore_target_sha256", "restore_database_state",
    }
    assert set(request.parameters) == {
        "backup_id", "credentials_path", "database", "verification", "backup_helper",
        "prune_backup_ids", "replace_unfinished", "reapply",
    }
    assert request.parameters["backup_helper"] == {
        "sha256": DESIRED_SCHEDULER_SHA,
        "upload_path": "pending-controller-upload",
    }
    assert outcome.stage == "restored"
    assert outcome.facts["mutation_state"] == "changed"
    assert outcome.facts["starting_state"] == mutable(request.expected_state)
    assert outcome.facts["observations"]["selected_release_id"] == release.release_id
    assert outcome.facts["report"]["status"] == "ok"


def test_different_unfinished_backup_preview_names_required_flag_but_execution_refuses(
    tmp_path, monkeypatch
):
    release, backup, selection, state = _authority(tmp_path)
    target = RestoreTarget(
        1, "backup-" + "7" * 32, "7" * 64, release.release_id,
        selection_filename(selection), FAILED_CURRENT, 41, None, True,
        SAFETY, None, ({"backup_id": SAFETY, "attempt_number": 0},),
    )
    state["restore_target"] = {**target.to_mapping(), "sha256": restore_target_sha256(target)}
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state, state], mutations)

    preview = restore(object(), config(), BACKUP, dry_run=True)
    execution = restore(object(), config(), BACKUP, confirm=lambda _plan: True)

    assert preview.stage == "planned"
    assert preview.facts["replace_unfinished_required"] is True
    assert "--replace-unfinished" in preview.next_action
    assert execution.exit_status is ExitStatus.SAFETY
    assert "--replace-unfinished" in execution.next_action
    assert mutations == []


def test_reapply_refuses_an_unfinished_binding_before_mutation(tmp_path, monkeypatch):
    release, backup, selection, state = _authority(tmp_path)
    target = RestoreTarget(
        1, BACKUP, backup.dump_sha256, release.release_id,
        selection_filename(selection), FAILED_CURRENT, 41, 42, False,
        SAFETY, None, ({"backup_id": SAFETY, "attempt_number": 0},),
    )
    state["restore_target"] = {**target.to_mapping(), "sha256": restore_target_sha256(target)}
    state["restore_database_state"]["temporary"] = {
        "oid": 42, "owner": "taskman", "migration_table_present": False, "applied_migrations": None,
    }
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    outcome = restore(object(), config(), BACKUP, reapply=True, confirm=lambda _plan: True)

    assert outcome.exit_status is ExitStatus.SAFETY
    assert "ordinary retry" in outcome.next_action
    assert mutations == []


def test_unfinished_restore_refuses_when_its_base_selection_no_longer_matches_authority(
    tmp_path, monkeypatch
):
    release, backup, _selection, state = _authority(tmp_path)
    target = RestoreTarget(
        1, BACKUP, backup.dump_sha256, release.release_id,
        "selection-" + "8" * 64 + ".json", FAILED_CURRENT, 41, 42, False,
        SAFETY, None, ({"backup_id": SAFETY, "attempt_number": 0},),
    )
    state["restore_target"] = {**target.to_mapping(), "sha256": restore_target_sha256(target)}
    state["restore_database_state"]["temporary"] = {
        "oid": 42, "owner": "taskman", "migration_table_present": False, "applied_migrations": None,
    }
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    outcome = restore(object(), config(), BACKUP, confirm=lambda _plan: True)

    assert outcome.exit_status is ExitStatus.SAFETY
    assert outcome.stage == "safety-refused"
    assert outcome.facts["backup_id"] == BACKUP
    assert mutations == []


def test_retired_only_same_backup_retry_is_planned_as_remaining_load_and_swap(
    tmp_path, monkeypatch
):
    retired = {
        "canonical": None,
        "temporary": None,
        "retired": {
            "oid": 41,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [MIGRATION],
        },
    }
    release, backup, selection, state = _authority(tmp_path, databases=retired)
    target = RestoreTarget(
        1, BACKUP, backup.dump_sha256, release.release_id,
        selection_filename(selection), FAILED_CURRENT, 41, 42, True,
        SAFETY, None, ({"backup_id": SAFETY, "attempt_number": 0},),
    )
    state["restore_target"] = {**target.to_mapping(), "sha256": restore_target_sha256(target)}
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    outcome = restore(object(), config(), BACKUP, dry_run=True)

    assert outcome.stage == "planned"
    assert outcome.facts["physical_database_arrangement"] == ["retired"]
    assert outcome.facts["canonical_applied_migrations"] is None
    assert outcome.facts["remaining_restore_bytes"] == 2048
    assert "load-temporary" in outcome.facts["remaining_consequences"]


def test_durable_success_after_retired_cleanup_is_valid_cleanup_only_authority(
    tmp_path, monkeypatch
):
    restored = {
        "canonical": {
            "oid": 42,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [MIGRATION],
        },
        "temporary": None,
        "retired": None,
    }
    release, backup, selection, state = _authority(tmp_path, databases=restored)
    state["selected_release_id"] = release.release_id
    target = RestoreTarget(
        1, BACKUP, backup.dump_sha256, release.release_id,
        selection_filename(selection), None, 41, 42, False,
        SAFETY, None, ({"backup_id": SAFETY, "attempt_number": 0},),
    )
    state["restore_target"] = {**target.to_mapping(), "sha256": restore_target_sha256(target)}
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    outcome = restore(object(), config(), BACKUP, dry_run=True)

    assert outcome.stage == "planned"
    assert outcome.facts["remaining_restore_bytes"] == 0
    assert outcome.facts["required_safety_backup_bytes"] == 0
    assert outcome.facts["remaining_consequences"] == ["cleanup-retired", "cleanup-binding"]


def test_post_swap_before_success_does_not_reserve_or_plan_another_dump_load(
    tmp_path, monkeypatch
):
    swapped = {
        "canonical": {
            "oid": 42,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [MIGRATION],
        },
        "temporary": None,
        "retired": {
            "oid": 41,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [MIGRATION],
        },
    }
    release, backup, selection, state = _authority(tmp_path, databases=swapped)
    target = RestoreTarget(
        1, BACKUP, backup.dump_sha256, release.release_id,
        selection_filename(selection), FAILED_CURRENT, 41, 42, False,
        SAFETY, None, ({"backup_id": SAFETY, "attempt_number": 0},),
    )
    state["restore_target"] = {**target.to_mapping(), "sha256": restore_target_sha256(target)}
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    outcome = restore(object(), config(), BACKUP, dry_run=True)

    assert outcome.stage == "planned"
    assert outcome.facts["remaining_restore_bytes"] == 0
    assert outcome.facts["required_safety_backup_bytes"] == 0
    assert "load-temporary" not in outcome.facts["remaining_consequences"]


@pytest.mark.parametrize("arrangement", ("wrong-restored", "wrong-temporary"))
def test_restore_refuses_role_identity_drift_before_consequence(
    arrangement, tmp_path, monkeypatch
):
    databases = {
        "canonical": {
            "oid": 42 if arrangement == "wrong-temporary" else 43,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [MIGRATION],
        },
        "temporary": (
            {
                "oid": 43,
                "owner": "taskman",
                "migration_table_present": False,
                "applied_migrations": None,
            }
            if arrangement == "wrong-temporary"
            else None
        ),
        "retired": (
            {
                "oid": 41,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": [MIGRATION],
            }
            if arrangement == "wrong-restored"
            else None
        ),
    }
    if arrangement == "wrong-temporary":
        databases["canonical"]["oid"] = 41
    release, backup, selection, state = _authority(tmp_path, databases=databases)
    target = RestoreTarget(
        1, BACKUP, backup.dump_sha256, release.release_id,
        selection_filename(selection), FAILED_CURRENT, 41, 42, False,
        SAFETY, None, ({"backup_id": SAFETY, "attempt_number": 0},),
    )
    state["restore_target"] = {**target.to_mapping(), "sha256": restore_target_sha256(target)}
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    outcome = restore(object(), config(), BACKUP, dry_run=True)

    assert outcome.exit_status is ExitStatus.SAFETY
    assert mutations == []


@pytest.mark.parametrize("observed_size", (4096, None))
def test_restore_uses_actual_original_database_size_for_safety_backup_capacity(
    observed_size, tmp_path, monkeypatch
):
    release, backup, _selection, state = _authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight",
        lambda *_args: type(
            "Facts",
            (),
            {
                "available_disk_bytes": 10_000,
                "backup_available_disk_bytes": 4095,
                "database_available_disk_bytes": 10_000,
                "database_size_bytes": {
                    "canonical": observed_size,
                    "temporary": None,
                    "retired": None,
                },
            },
        )(),
    )

    outcome = restore(object(), config(), BACKUP, dry_run=True)

    assert outcome.exit_status is ExitStatus.SAFETY
    assert outcome.facts["plan"]["required_safety_backup_bytes"] == observed_size
    assert mutations == []


def test_unknown_dispatched_restore_reply_preserves_starting_state_and_unknown_observations(
    tmp_path, monkeypatch
):
    release, backup, _selection, state = _authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    def lost(_remote, _config, **kwargs):
        request = kwargs["request"]
        unavailable = {
            "starting_state": dict(request.expected_state),
            "mutation_state": "unknown",
            "failed_boundary": "helper",
            "inspection_error": None,
            "report": None,
            "observations": {
                "selected_release_id": None, "last_successful_selection_id": None,
                "applied_migrations": None, "protected_backup_ids": None,
                "backup_protection_sha256": None, "restore_target_sha256": None,
                "database_state": "unknown", "service_state": "unknown",
                "scheduled_backup_sha256": None, "backup_timer_enabled": None,
                "backup_timer_state": "unknown", "restore_database_state": None,
            },
            "unavailable_fields": [
                "applied_migrations", "backup_protection_sha256", "backup_timer_enabled",
                "database_state", "last_successful_selection_id", "protected_backup_ids",
                "restore_database_state", "restore_target_sha256", "scheduled_backup_sha256",
                "selected_release_id", "service_state", "backup_timer_state",
            ],
            "desired_release_id": None,
            "backup_id": BACKUP,
            "pre_restore_backup_id": None,
        }
        raise OpsError(
            ExitStatus.SAFETY, "helper", "restore reply was unavailable", True,
            state=unavailable, next_action="inspect the observed host state before retrying",
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_restore_request", lost)

    outcome = restore(object(), config(), BACKUP, confirm=lambda _plan: True)

    assert outcome.changed is True
    assert outcome.facts["mutation_state"] == "unknown"
    assert outcome.facts["starting_state"]["backup_id"] == BACKUP
    assert outcome.facts["observations"]["restore_database_state"] is None


def _completed_restore_authority(tmp_path):
    completed_databases = {
        "canonical": {
            "oid": 42,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [MIGRATION],
        },
        "temporary": None,
        "retired": {
            "oid": 41,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [MIGRATION],
        },
    }
    release, backup, selection, completed = _authority(
        tmp_path,
        current=None,
        databases=completed_databases,
    )
    completed["selected_release_id"] = release.release_id
    target = RestoreTarget(
        1,
        BACKUP,
        backup.dump_sha256,
        release.release_id,
        selection_filename(selection),
        None,
        41,
        42,
        False,
        SAFETY,
        None,
        ({"backup_id": SAFETY, "attempt_number": 0},),
    )
    completed["restore_target"] = {
        **target.to_mapping(),
        "sha256": restore_target_sha256(target),
    }
    fresh = dict(completed)
    fresh["restore_target"] = None
    fresh["restore_database_state"] = {
        "canonical": completed_databases["canonical"],
        "temporary": None,
        "retired": None,
    }
    return release, backup, completed, fresh


@pytest.mark.parametrize(
    "database_result",
    (
        CommandError("native restore capacity command failed"),
        OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "preflight",
            "PostgreSQL data-volume capacity is unobservable",
            False,
        ),
    ),
)
def test_completed_cleanup_does_not_run_failing_or_unobservable_capacity_preflight(
    tmp_path, monkeypatch, database_result
):
    release, backup, completed, _fresh = _completed_restore_authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [completed], mutations)
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_inspection_preflight",
        lambda *_args: object(),
        raising=False,
    )
    capacity_calls = []

    def capacity(*_args):
        capacity_calls.append(True)
        if isinstance(database_result, OpsError):
            raise database_result
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "preflight",
            "PostgreSQL maintenance access, database role, or restore capacity preflight failed",
            False,
        ) from database_result

    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight", capacity
    )

    outcome = restore(
        object(), config(), BACKUP, confirm=lambda _plan: True
    )

    assert outcome.exit_status is ExitStatus.OK, outcome.facts
    assert outcome.stage == "restored"
    assert len(mutations) == 1
    assert capacity_calls == []


def test_reapply_finishes_completed_binding_then_rediscovers_and_confirms_fresh_plan(
    tmp_path, monkeypatch
):
    release, backup, completed, fresh = _completed_restore_authority(tmp_path)
    mutations = []
    _install_controller_fakes(
        monkeypatch,
        release,
        backup,
        [completed, fresh],
        mutations,
    )
    preflight_calls = []

    def preflight(*_args):
        preflight_calls.append(True)
        return type(
            "Facts",
            (),
            {
                "available_disk_bytes": 10_000,
                "backup_available_disk_bytes": 10_000,
                "database_available_disk_bytes": 10_000,
                "database_size_bytes": {
                    "canonical": 4096,
                    "temporary": None,
                    "retired": None,
                },
            },
        )()

    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight", preflight
    )
    inspection_calls = []
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_inspection_preflight",
        lambda *_args: inspection_calls.append(True) or object(),
        raising=False,
    )
    confirmations = []

    outcome = restore(
        object(),
        config(),
        BACKUP,
        reapply=True,
        confirm=lambda plan: confirmations.append(plan) or True,
    )

    assert outcome.stage == "reapplied"
    assert len(mutations) == 2
    assert mutations[0]["request"].parameters["reapply"] is True
    assert mutations[0]["request"].parameters["backup_helper"]["upload_path"] is None
    assert mutations[1]["request"].parameters["reapply"] is True
    assert len(confirmations) == 1
    assert confirmations[0]["planned_pre_restore_backup"] is True
    assert len(preflight_calls) == 1
    assert len(inspection_calls) == 2
    assert confirmations[0]["available_database_bytes"] == 10_000


def test_reapply_cleanup_mutation_survives_fresh_capacity_preflight_failure(
    tmp_path, monkeypatch
):
    release, backup, completed, fresh = _completed_restore_authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [completed, fresh], mutations)
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_inspection_preflight",
        lambda *_args: object(),
        raising=False,
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight",
        lambda *_args: (_ for _ in ()).throw(
            OpsError(
                ExitStatus.REMOTE_PREFLIGHT,
                "preflight",
                "PostgreSQL data-volume capacity is unobservable",
                False,
            )
        ),
    )

    outcome = restore(object(), config(), BACKUP, reapply=True, confirm=lambda _: True)

    assert len(mutations) == 1
    assert outcome.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert outcome.changed is True
    assert outcome.facts["mutation_state"] == "changed"


def test_reapply_cleanup_mutation_survives_lost_rediscovery_result(
    tmp_path, monkeypatch
):
    release, backup, completed, _fresh = _completed_restore_authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [completed], mutations)
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_inspection_preflight",
        lambda *_args: object(),
        raising=False,
    )
    discoveries = 0
    original = __import__("taskman_ops.workflows.restore", fromlist=["run_request"]).run_request

    def lost_after_cleanup(remote, request):
        nonlocal discoveries
        discoveries += 1
        if discoveries > 1:
            raise OpsError(
                ExitStatus.SAFETY,
                "helper",
                "restore discovery reply was lost",
                False,
                state={"mutation_state": "unknown"},
            )
        return original(remote, request)

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", lost_after_cleanup)

    outcome = restore(object(), config(), BACKUP, reapply=True, confirm=lambda _: True)

    assert len(mutations) == 1
    assert outcome.exit_status is ExitStatus.SAFETY
    assert outcome.changed is True
    assert outcome.facts["mutation_state"] == "changed"
