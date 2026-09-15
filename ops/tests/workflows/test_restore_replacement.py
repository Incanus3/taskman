"""Public controller contracts for replacing an unfinished restore."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import pytest

from taskman_ops.errors import ExitStatus
from taskman_ops.host_helper.records import SelectionRecord, selection_filename
from taskman_ops.host_helper.restore_target import (
    REPLACEMENT_RECONFIRM_MESSAGE,
    RestoreTarget,
    restore_target_sha256,
    replace_restore_target,
)
from taskman_ops.host_protocol import HostResult, PROTOCOL_VERSION
from taskman_ops.workflows.helper import mutable
from taskman_ops.workflows.restore import restore
from tests.host_helper import test_restore as host_restore_tests
from tests.test_end_to_end import _install_public_restore_controller
from tests.workflows.test_deploy import config
from tests.workflows.test_helper import _exact_failure_state
from tests.workflows.test_restore_recovery import (
    BACKUP,
    FAILED_CURRENT,
    MIGRATION,
    _authority,
    _install_controller_fakes,
)


def _replacement_authority(tmp_path):
    databases = {
        "canonical": {
            "oid": 41,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [MIGRATION],
        },
        "temporary": {
            "oid": 42,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": [],
        },
        "retired": None,
    }
    release, backup, selection, state = _authority(
        tmp_path, databases=databases
    )
    attempt_ids = tuple(f"backup-{number:032x}" for number in range(7))
    target = RestoreTarget(
        1,
        "backup-" + "8" * 32,
        "8" * 64,
        release.release_id,
        state["last_successful_selection_id"],
        FAILED_CURRENT,
        41,
        42,
        False,
        attempt_ids[0],
        None,
        tuple(
            {"backup_id": backup_id, "attempt_number": number}
            for number, backup_id in enumerate(attempt_ids)
        ),
    )
    state["restore_target"] = {
        **target.to_mapping(),
        "sha256": restore_target_sha256(target),
    }
    state["independently_held_backup_ids"] = [attempt_ids[4]]
    return release, backup, selection, state, attempt_ids


def test_replacement_preview_lists_exact_conditional_pruning_and_new_target(
    tmp_path, monkeypatch
):
    """Omitting the post-copy retirements would ask for incomplete authority."""

    release, backup, _selection, state, attempt_ids = _replacement_authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    result = restore(
        object(),
        config(),
        BACKUP,
        dry_run=True,
        replace_unfinished=True,
    )

    assert result.exit_status is ExitStatus.OK
    assert result.facts["replace_unfinished_required"] is True
    assert result.facts["previous_backup_id"] == "backup-" + "8" * 32
    assert result.facts["backup_id"] == BACKUP
    assert result.facts["planned_pre_restore_backup"] is True
    assert result.facts["prune_backup_ids"] == [attempt_ids[1], attempt_ids[2], attempt_ids[4]]
    assert result.facts["typed_confirmation"] == f"restore production {BACKUP}"
    assert mutations == []


def test_replacement_execution_sends_the_confirmed_sorted_prune_ids(
    tmp_path, monkeypatch
):
    """An empty helper prune list would strand the sixth transient attempt."""

    release, backup, _selection, state, attempt_ids = _replacement_authority(tmp_path)
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    result = restore(
        object(),
        config(),
        BACKUP,
        replace_unfinished=True,
        confirm=lambda plan: plan["prune_backup_ids"]
        == [attempt_ids[1], attempt_ids[2], attempt_ids[4]],
    )

    assert result.exit_status is ExitStatus.OK
    assert mutations[0]["request"].parameters["prune_backup_ids"] == (
        attempt_ids[1],
        attempt_ids[2],
        attempt_ids[4],
    )
    assert mutations[0]["request"].parameters["replace_unfinished"] is True


def test_restore_rejects_malformed_independent_safety_projection(
    tmp_path, monkeypatch
):
    """Controller pruning must never trust duplicate, unordered, or foreign IDs."""

    release, backup, _selection, state, attempt_ids = _replacement_authority(tmp_path)
    state["independently_held_backup_ids"] = [attempt_ids[4], attempt_ids[4]]
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    result = restore(
        object(), config(), BACKUP, dry_run=True, replace_unfinished=True
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert mutations == []


def test_pending_third_target_normalization_requires_a_second_confirmation(
    tmp_path, monkeypatch
):
    """Treating normalization as success would load a target never freshly confirmed."""

    release, backup, _selection, first, _attempt_ids = _replacement_authority(
        tmp_path
    )
    old = RestoreTarget.from_mapping(
        {key: value for key, value in first["restore_target"].items() if key != "sha256"}
    )
    first_target = replace(
        old,
        replacement={
            "backup_id": BACKUP,
            "dump_sha256": backup.dump_sha256,
            "source_release_id": backup.source_release_id,
            "discard_database_oid": old.restored_database_oid,
        },
    )
    first["restore_target"] = {
        **first_target.to_mapping(),
        "sha256": restore_target_sha256(first_target),
    }
    normalized = replace(
        first_target,
        backup_id=BACKUP,
        dump_sha256=backup.dump_sha256,
        source_release_id=backup.source_release_id,
        restored_database_oid=None,
        temporary_creation_pending=True,
        replacement=None,
    )
    second = {
        **first,
        "applied_migrations": [MIGRATION],
        "restore_target": {
            **normalized.to_mapping(),
            "sha256": restore_target_sha256(normalized),
        },
        "restore_database_state": {
            "canonical": first["restore_database_state"]["canonical"],
            "temporary": None,
            "retired": None,
        },
    }
    mutations = []
    _install_controller_fakes(
        monkeypatch, release, backup, [first, second], mutations
    )
    from taskman_ops.workflows import restore as restore_workflow

    default_mutate = restore_workflow.run_restore_request
    calls = 0

    def normalize_then_restore(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            request = kwargs["request"]
            return HostResult(
                PROTOCOL_VERSION,
                "restore",
                request.correlation_id,
                "retryable",
                REPLACEMENT_RECONFIRM_MESSAGE,
                _exact_failure_state("restore", "restore", 11),
                ("abandoned input dump is unavailable",),
            )
        return default_mutate(*args, **kwargs)

    monkeypatch.setattr(
        restore_workflow, "run_restore_request", normalize_then_restore
    )
    plans = []

    result = restore(
        object(),
        config(),
        BACKUP,
        replace_unfinished=True,
        confirm=lambda plan: plans.append(plan) or True,
    )

    assert result.exit_status is ExitStatus.OK
    assert len(plans) == 2
    assert plans[0]["restore_target"]["replacement"]["backup_id"] == BACKUP
    assert plans[1]["restore_target"]["replacement"] is None
    assert result.changed is True
    assert result.facts["starting_state"]["restore_target_sha256"] == first[
        "restore_target"
    ]["sha256"]
    assert "abandoned input dump is unavailable" in result.warnings


def test_cancellation_after_normalization_retains_prior_mutation_evidence(
    tmp_path, monkeypatch
):
    """A cancelled fresh plan must still report the completed normalization write."""

    release, backup, _selection, first, _attempt_ids = _replacement_authority(
        tmp_path
    )
    old = RestoreTarget.from_mapping(
        {key: value for key, value in first["restore_target"].items() if key != "sha256"}
    )
    first_target = replace(
        old,
        replacement={
            "backup_id": BACKUP,
            "dump_sha256": backup.dump_sha256,
            "source_release_id": backup.source_release_id,
            "discard_database_oid": old.restored_database_oid,
        },
    )
    first["restore_target"] = {
        **first_target.to_mapping(),
        "sha256": restore_target_sha256(first_target),
    }
    normalized = replace(
        first_target,
        backup_id=BACKUP,
        dump_sha256=backup.dump_sha256,
        source_release_id=backup.source_release_id,
        restored_database_oid=None,
        temporary_creation_pending=True,
        replacement=None,
    )
    second = {
        **first,
        "restore_target": {
            **normalized.to_mapping(),
            "sha256": restore_target_sha256(normalized),
        },
        "restore_database_state": {
            "canonical": first["restore_database_state"]["canonical"],
            "temporary": None,
            "retired": None,
        },
    }
    mutations = []
    _install_controller_fakes(
        monkeypatch, release, backup, [first, second], mutations
    )
    from taskman_ops.workflows import restore as restore_workflow

    def normalized_result(_remote, _config, **kwargs):
        request = kwargs["request"]
        return HostResult(
            PROTOCOL_VERSION,
            "restore",
            request.correlation_id,
            "retryable",
            REPLACEMENT_RECONFIRM_MESSAGE,
            _exact_failure_state("restore", "restore", 11),
            (),
        )

    monkeypatch.setattr(restore_workflow, "run_restore_request", normalized_result)
    confirmations = 0

    def confirm(_plan):
        nonlocal confirmations
        confirmations += 1
        return confirmations == 1

    result = restore(
        object(), config(), BACKUP, replace_unfinished=True, confirm=confirm
    )

    assert result.stage == "confirmation-cancelled"
    assert result.changed is True
    assert result.facts["mutation_state"] == "changed"
    assert confirmations == 2


def test_completed_different_target_cleans_before_the_only_new_restore_confirmation(
    tmp_path, monkeypatch
):
    """Durable success cleanup is not authority to confirm a different restore."""

    release, backup, _selection, first, attempt_ids = _replacement_authority(
        tmp_path
    )
    old = RestoreTarget.from_mapping(
        {key: value for key, value in first["restore_target"].items() if key != "sha256"}
    )
    completed_selection = SelectionRecord(
        old.source_release_id,
        release.release_id,
        old.safety_backup_id,
        datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
        2,
        old.observed_previous_release_id,
        tuple(sorted({old.backup_id, *attempt_ids})),
    )
    first.update(
        selected_release_id=old.source_release_id,
        last_successful_selection_id=selection_filename(completed_selection),
        last_successful_selection=completed_selection.to_mapping(),
        restore_database_state={
            "canonical": {
                "oid": old.restored_database_oid,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": [MIGRATION],
            },
            "temporary": None,
            "retired": {
                "oid": old.original_database_oid,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": [MIGRATION],
            },
        },
    )
    first["applied_migrations"] = [MIGRATION]
    second = {
        **first,
        "restore_target": None,
        "independently_held_backup_ids": [],
        "restore_database_state": {
            "canonical": first["restore_database_state"]["canonical"],
            "temporary": None,
            "retired": None,
        },
    }
    mutations = []
    _install_controller_fakes(
        monkeypatch, release, backup, [first, second], mutations
    )
    plans = []

    result = restore(
        object(), config(), BACKUP, confirm=lambda plan: plans.append(plan) or True
    )

    assert result.exit_status is ExitStatus.OK
    assert len(plans) == 1
    assert plans[0]["restore_target"] is None
    assert len(mutations) == 2
    assert mutations[0]["request"].parameters["backup_id"] == old.backup_id
    assert mutations[1]["request"].parameters["backup_id"] == BACKUP
    assert result.facts["starting_state"] == mutable(
        mutations[1]["request"].expected_state
    )


def test_completed_cleanup_then_cancel_has_no_confirmed_starting_state(
    tmp_path, monkeypatch
):
    release, backup, _selection, first, attempt_ids = _replacement_authority(
        tmp_path
    )
    old = RestoreTarget.from_mapping(
        {key: value for key, value in first["restore_target"].items() if key != "sha256"}
    )
    completed_selection = SelectionRecord(
        old.source_release_id,
        release.release_id,
        old.safety_backup_id,
        datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
        2,
        old.observed_previous_release_id,
        tuple(sorted({old.backup_id, *attempt_ids})),
    )
    first.update(
        selected_release_id=old.source_release_id,
        last_successful_selection_id=selection_filename(completed_selection),
        last_successful_selection=completed_selection.to_mapping(),
        applied_migrations=[MIGRATION],
        restore_database_state={
            "canonical": {
                "oid": old.restored_database_oid,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": [MIGRATION],
            },
            "temporary": None,
            "retired": {
                "oid": old.original_database_oid,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": [MIGRATION],
            },
        },
    )
    second = {
        **first,
        "restore_target": None,
        "independently_held_backup_ids": [],
        "restore_database_state": {
            "canonical": first["restore_database_state"]["canonical"],
            "temporary": None,
            "retired": None,
        },
    }
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [first, second], mutations)

    result = restore(object(), config(), BACKUP, confirm=lambda _plan: False)

    assert result.stage == "confirmation-cancelled"
    assert result.changed is True
    assert result.facts["starting_state"] is None
    assert len(mutations) == 1


@pytest.mark.parametrize(
    ("family", "safety_copy_required"),
    (
        ("binding", True),
        ("created", True),
        ("registered", True),
        ("temporary-retired", False),
        ("retired", False),
        ("swapped", True),
    ),
)
def test_public_packaged_replacement_converges_each_unfinished_arrangement(
    family, safety_copy_required, tmp_path, monkeypatch
):
    """The real controller and packaged helper must reach every admitted arrangement."""

    config_value, runtime_path, paths, remote, _old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family=family,
        )
    )
    replacement = host_restore_tests._backup(
        paths,
        host_restore_tests.REPLACEMENT_BACKUP,
        host_restore_tests.TARGET,
        b"replacement restore source",
    )
    plans = []
    result = restore(
        remote,
        config_value,
        replacement.backup_id,
        replace_unfinished=True,
        confirm=lambda plan: plans.append(plan) or True,
    )

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, " | ".join(runtime["events"])
    assert plans[0]["previous_backup_id"] == host_restore_tests.INPUT_BACKUP
    assert plans[0]["backup_id"] == replacement.backup_id
    assert f"dump-loaded:{replacement.backup_id}" in runtime["events"]
    assert ("safety-backup" in runtime["events"]) is safety_copy_required
    assert not Path(paths.local(paths.restore_target_path)).exists()


def test_public_packaged_dry_run_previews_replacement_without_flag_or_writes(
    tmp_path, monkeypatch
):
    config_value, runtime_path, paths, remote, _old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family="registered",
        )
    )
    replacement = host_restore_tests._backup(
        paths,
        host_restore_tests.REPLACEMENT_BACKUP,
        host_restore_tests.TARGET,
        b"replacement restore source",
    )

    result = restore(remote, config_value, replacement.backup_id, dry_run=True)

    assert result.stage == "planned"
    assert result.facts["replace_unfinished_required"] is True
    assert "--replace-unfinished" in result.next_action
    assert json.loads(runtime_path.read_text())["events"] == []


def test_public_packaged_third_target_skips_unusable_abandoned_inputs_and_reconfirms(
    tmp_path, monkeypatch
):
    config_value, runtime_path, paths, remote, old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family="replacement-pending",
        )
    )
    pending_dump = Path(
        paths.local(
            paths.backup_root
            / f"{host_restore_tests.REPLACEMENT_BACKUP}.dump"
        )
    )
    Path(
        paths.local(paths.backup_root / f"{old_source.backup_id}.dump")
    ).unlink()
    pending_dump.write_bytes(b"corrupt pending restore source")
    third = host_restore_tests._backup(
        paths,
        host_restore_tests.THIRD_BACKUP,
        host_restore_tests.TARGET,
        b"third restore source",
    )
    plans = []

    result = restore(
        remote,
        config_value,
        third.backup_id,
        replace_unfinished=True,
        confirm=lambda plan: plans.append(plan) or True,
    )

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, (
        result.facts.get("failed_boundary"),
        result.facts.get("observations"),
        runtime["events"],
    )
    assert len(plans) == 2
    assert all(plan["backup_id"] == third.backup_id for plan in plans)
    assert plans[0]["remaining_consequences"] == ["normalize-pending-replacement"]
    assert f"dump-loaded:{host_restore_tests.REPLACEMENT_BACKUP}" not in runtime[
        "events"
    ]
    assert f"dump-loaded:{third.backup_id}" in runtime["events"]
    assert any("unavailable or corrupt" in warning for warning in result.warnings)


def test_public_packaged_completed_restore_cleans_before_confirming_new_backup(
    tmp_path, monkeypatch
):
    config_value, runtime_path, paths, remote, _old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family="durable-retired",
        )
    )
    from taskman_ops.workflows import restore as restore_workflow

    dispatched_expected_states = []
    run_restore_request = restore_workflow.run_restore_request

    def capture_request(*args, **kwargs):
        dispatched_expected_states.append(dict(kwargs["request"].expected_state))
        return run_restore_request(*args, **kwargs)

    monkeypatch.setattr(restore_workflow, "run_restore_request", capture_request)
    replacement = host_restore_tests._backup(
        paths,
        host_restore_tests.REPLACEMENT_BACKUP,
        host_restore_tests.TARGET,
        b"new restore after completed cleanup",
    )
    plans = []

    result = restore(
        remote,
        config_value,
        replacement.backup_id,
        confirm=lambda plan: plans.append(plan) or True,
    )

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.OK, runtime["events"]
    assert len(plans) == 1
    assert plans[0]["restore_target"] is None
    assert runtime["events"].index("retired-dropped") < runtime["events"].index(
        "safety-backup"
    )
    assert f"dump-loaded:{replacement.backup_id}" in runtime["events"]
    assert result.facts["starting_state"] == mutable(dispatched_expected_states[-1])
    assert result.facts["starting_state"] != mutable(dispatched_expected_states[0])


def test_public_reapply_dry_run_previews_cleanup_and_fresh_restore_without_writes(
    tmp_path, monkeypatch
):
    config_value, runtime_path, _paths, remote, old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family="durable-retired",
        )
    )

    result = restore(
        remote, config_value, old_source.backup_id, dry_run=True, reapply=True
    )

    assert result.exit_status is ExitStatus.OK
    assert result.facts["planned_pre_restore_backup"] is True
    assert result.facts["remaining_restore_bytes"] > 0
    assert result.facts["remaining_consequences"] == [
        "cleanup-retired",
        "cleanup-binding",
        "refresh-scheduled-backup-helper",
        "fresh-safety-backup",
        "publish-restore-binding",
        "load-temporary",
        "swap-databases",
        "select-source-release",
        "verify",
        "publish-success",
        "cleanup-retired",
        "cleanup-binding",
    ]
    assert json.loads(runtime_path.read_text())["events"] == []


@pytest.mark.parametrize("failure", ("checksum", "list"))
def test_public_restore_preview_refuses_invalid_required_safety_content(
    failure, tmp_path, monkeypatch
):
    config_value, runtime_path, paths, remote, old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family="registered",
        )
    )
    safety_id = "backup-" + "c" * 32
    dump_path = Path(paths.local(paths.backup_root / f"{safety_id}.dump"))
    dump_path.write_bytes(b"invalid required safety archive")
    if failure == "list":
        manifest_path = Path(
            paths.local(paths.backup_root / f"{safety_id}.json")
        )
        manifest = json.loads(manifest_path.read_text())
        manifest["dump_sha256"] = hashlib.sha256(dump_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
        manifest_path.chmod(0o600)
        runtime = json.loads(runtime_path.read_text())
        runtime["invalid_backup_list_id"] = safety_id
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    result = restore(remote, config_value, old_source.backup_id, dry_run=True)

    assert result.exit_status is ExitStatus.BACKUP
    assert json.loads(runtime_path.read_text())["events"] == []


@pytest.mark.parametrize("role", ("canonical", "retired"))
def test_controller_admits_pending_replacement_after_exact_discard(
    role, tmp_path, monkeypatch
):
    release, backup, _selection, state, _attempt_ids = _replacement_authority(
        tmp_path
    )
    old = RestoreTarget.from_mapping(
        {key: value for key, value in state["restore_target"].items() if key != "sha256"}
    )
    pending = replace(
        old,
        replacement={
            "backup_id": BACKUP,
            "dump_sha256": backup.dump_sha256,
            "source_release_id": backup.source_release_id,
            "discard_database_oid": old.restored_database_oid,
        },
    )
    state["restore_target"] = {
        **pending.to_mapping(),
        "sha256": restore_target_sha256(pending),
    }
    original = {
        "oid": old.original_database_oid,
        "owner": "taskman",
        "migration_table_present": True,
        "applied_migrations": [MIGRATION],
    }
    state["restore_database_state"] = {
        "canonical": original if role == "canonical" else None,
        "temporary": None,
        "retired": original if role == "retired" else None,
    }
    state["applied_migrations"] = [MIGRATION] if role == "canonical" else None
    mutations = []
    _install_controller_fakes(monkeypatch, release, backup, [state], mutations)

    result = restore(
        object(),
        config(),
        BACKUP,
        replace_unfinished=True,
        confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.OK
    assert len(mutations) == 1


def _seed_prunable_safety_attempts(paths):
    binding_path = Path(paths.local(paths.restore_target_path))
    target = RestoreTarget.from_mapping(json.loads(binding_path.read_text()))
    attempts = [dict(target.safety_backup_attempts[0])]
    for number in range(1, 6):
        backup_id = f"backup-{512 + number:032x}"
        host_restore_tests._backup(
            paths,
            backup_id,
            host_restore_tests.CURRENT,
            f"retained safety {number}".encode("ascii"),
        )
        attempts.append({"backup_id": backup_id, "attempt_number": number})
    target = replace(target, safety_backup_attempts=tuple(attempts))
    replace_restore_target(paths, target)
    return target, tuple(item["backup_id"] for item in attempts)


def test_public_packaged_replacement_refuses_unusable_required_safety_role(
    tmp_path, monkeypatch
):
    config_value, runtime_path, paths, remote, _old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family="registered",
        )
    )
    replacement = host_restore_tests._backup(
        paths,
        host_restore_tests.REPLACEMENT_BACKUP,
        host_restore_tests.TARGET,
        b"replacement restore source",
    )
    Path(
        paths.local(
            paths.backup_root / f"{'backup-' + 'c' * 32}.dump"
        )
    ).unlink()

    result = restore(
        remote,
        config_value,
        replacement.backup_id,
        replace_unfinished=True,
        confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert json.loads(runtime_path.read_text())["events"] == []


def test_public_packaged_failed_restored_safety_failure_precedes_discard(
    tmp_path, monkeypatch
):
    config_value, runtime_path, paths, remote, _old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family="swapped",
        )
    )
    replacement = host_restore_tests._backup(
        paths,
        host_restore_tests.REPLACEMENT_BACKUP,
        host_restore_tests.TARGET,
        b"replacement restore source",
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["safety_failure"] = True
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    result = restore(
        remote,
        config_value,
        replacement.backup_id,
        replace_unfinished=True,
        confirm=lambda _plan: True,
    )

    runtime = json.loads(runtime_path.read_text())
    assert result.exit_status is ExitStatus.BACKUP
    assert "safety-backup" in runtime["events"]
    assert not any(
        event.startswith("restored-dropped") for event in runtime["events"]
    )


@pytest.mark.parametrize(
    ("fault", "event", "expected_mutation"),
    (
        ("lose_registration_reply", "registration-reply-lost", "changed"),
        ("lose_retirement_reply", "safety-references-retired", "changed"),
        ("interrupt_backup_deletion", "backup-deletion-interrupted", "changed"),
    ),
)
def test_public_packaged_replacement_preserves_interrupted_reference_evidence(
    fault, event, expected_mutation, tmp_path, monkeypatch
):
    family = "binding" if fault == "lose_registration_reply" else "swapped"
    config_value, runtime_path, paths, remote, _old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family=family,
        )
    )
    if family == "swapped":
        _seed_prunable_safety_attempts(paths)
    replacement = host_restore_tests._backup(
        paths,
        host_restore_tests.REPLACEMENT_BACKUP,
        host_restore_tests.TARGET,
        b"replacement restore source",
    )
    runtime = json.loads(runtime_path.read_text())
    runtime[fault] = True
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    interrupted = restore(
        remote,
        config_value,
        replacement.backup_id,
        replace_unfinished=True,
        confirm=lambda _plan: True,
    )

    runtime = json.loads(runtime_path.read_text())
    assert interrupted.exit_status is ExitStatus.RESTORE
    assert interrupted.changed is True
    assert interrupted.facts["mutation_state"] == expected_mutation
    assert event in runtime["events"]
    if fault != "lose_registration_reply":
        assert not any(
            item.startswith("restored-dropped") for item in runtime["events"]
        )


def test_public_packaged_more_than_sixty_four_replacements_remain_bounded_during_clock_rollback(
    tmp_path, monkeypatch
):
    config_value, runtime_path, paths, remote, _old_source = (
        _install_public_restore_controller(
            monkeypatch,
            tmp_path,
            scheduler_failure=False,
            state_family="swapped",
        )
    )
    runtime = json.loads(runtime_path.read_text())
    runtime["verification"] = "failing"
    runtime["backup_clock_rollback"] = True
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    displayed_prunes = []

    for index in range(65):
        replacement = host_restore_tests._backup(
            paths,
            f"backup-{4096 + index:032x}",
            host_restore_tests.TARGET,
            f"replacement source {index}".encode("ascii"),
        )
        result = restore(
            remote,
            config_value,
            replacement.backup_id,
            replace_unfinished=True,
            confirm=lambda plan: displayed_prunes.append(
                tuple(plan["prune_backup_ids"])
            )
            or True,
        )
        assert result.exit_status is ExitStatus.READINESS, (index, result.facts)
        runtime = json.loads(runtime_path.read_text())
        runtime["events"] = []
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))

    target = RestoreTarget.from_mapping(
        json.loads(Path(paths.local(paths.restore_target_path)).read_text())
    )
    assert len(target.safety_backup_attempts) == 5
    assert target.backup_id == f"backup-{4096 + 64:032x}"
    assert any(displayed_prunes)
