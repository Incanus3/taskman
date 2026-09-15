"""Restore-specific controller admission, confirmation, and result translation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import re
import time

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..host_helper.backup_protection import BackupProtection
from ..host_helper.database import release_migration_versions
from ..host_helper.records import BackupRecord, RecordError, ReleaseRecord, SelectionRecord, selection_filename
from ..host_helper.restore_database import RestoreDatabaseError, validate_restore_database_state
from ..host_helper.restore_target import RestoreTarget, restore_target_sha256
from ..host_protocol import HostRequest, PROTOCOL_VERSION
from ..output import WorkflowResult
from ..remote import Remote
from ..releases.identifiers import validate_release_id
from .helper import (
    aggregate_mutation_state,
    database_settings,
    discovery_request,
    helper_paths,
    merge_warnings,
    mutable,
    mutation_result_facts,
    result_error,
    run_request,
    run_restore_request,
    successful_verification,
    temporary_scheduled_backup_helper_package,
    verification_settings,
)
from .inventory import collect_inventory
from .operational_preflight import (
    validate_restore_inspection_preflight,
    validate_restore_preflight,
)


_PGPASS = "/etc/taskman/pgpass"
_BACKUP_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DISCOVERY_KEYS = frozenset({
    "selected_release_id", "last_successful_selection_id", "last_successful_selection",
    "previous_successful_selection", "applied_migrations", "service_state", "database_state",
    "backup_protections", "backup_protection_sha256", "scheduled_backup_sha256",
    "backup_timer_enabled", "backup_timer_state", "restore_target", "restore_database_state",
})
_EXPECTED_KEYS = frozenset({
    "selected_release_id", "last_successful_selection_id", "applied_migrations",
    "backup_protection_sha256", "scheduled_backup_sha256", "backup_timer_enabled",
    "backup_id", "restore_target_sha256", "restore_database_state",
})


@dataclass(frozen=True)
class _Authority:
    selected_release_id: str | None
    latest_selection_id: str | None
    latest_selection: SelectionRecord | None
    source: BackupRecord
    source_release: ReleaseRecord
    backups: tuple[BackupRecord, ...]
    releases: tuple[ReleaseRecord, ...]
    protections: tuple[BackupProtection, ...]
    target: RestoreTarget | None
    target_sha256: str | None
    databases: Mapping[str, object]
    expected_state: Mapping[str, object]
    service_state: str
    timer_state: str
    warnings: tuple[str, ...]


def restore(
    remote: Remote,
    config: EnvironmentConfig,
    backup_id: str,
    *,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
    replace_unfinished: bool = False,
    reapply: bool = False,
) -> WorkflowResult:
    """Inspect, confirm, and dispatch one exact restore consequence request."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("restore requires a validated environment configuration")
    if _BACKUP_RE.fullmatch(backup_id) is None:
        raise ValueError("restore backup identifier is invalid")
    if not all(type(value) is bool for value in (dry_run, replace_unfinished, reapply)):
        raise TypeError("restore flags must be boolean")
    if replace_unfinished and reapply:
        raise ValueError("restore recovery flags are mutually exclusive")

    starting_state: Mapping[str, object] | None = None
    plan: Mapping[str, object] | None = None
    warnings: tuple[str, ...] = ()
    prior_mutation_state = "unchanged"
    try:
        validate_restore_inspection_preflight(remote, config)
        cleanup_cycles = 0
        while True:
            authority = _collect_authority(remote, config, backup_id)
            warnings = merge_warnings(warnings, authority.warnings)
            starting_state = authority.expected_state
            completed = _durably_completed(authority)
            completed_without_binding = _completed_same_backup(authority)
            unfinished = authority.target is not None and not completed
            if not dry_run and reapply and unfinished:
                raise _safety(
                    "an unfinished restore cannot be reapplied",
                    "resume with an ordinary retry or use --replace-unfinished for a different backup",
                )
            capacity_required = not completed and not (
                completed_without_binding and not reapply
            )
            preflight = (
                validate_restore_preflight(remote, config)
                if capacity_required
                else None
            )
            with temporary_scheduled_backup_helper_package() as scheduler_package:
                plan = _plan(
                    config,
                    authority,
                    scheduler_sha256=scheduler_package.sha256,
                    available_bytes=getattr(
                        preflight, "database_available_disk_bytes", None
                    ),
                    backup_available_bytes=getattr(preflight, "backup_available_disk_bytes", None),
                    database_size_bytes=getattr(preflight, "database_size_bytes", None),
                    requested_backup_id=backup_id,
                    replace_unfinished=replace_unfinished,
                    reapply=reapply,
                )
                # A completed binding is cleanup authority, not permission to
                # start a fresh reapply from stale capacity or confirmation.
                # Finish it first, then recollect preflight and restore facts.
                if (
                    not dry_run
                    and reapply
                    and authority.target is not None
                    and _durably_completed(authority)
                ):
                    if cleanup_cycles:
                        raise _safety("completed restore cleanup did not converge")
                    cleanup_source = _source_for_id(
                        authority.backups,
                        authority.releases,
                        authority.target.backup_id,
                    )
                    cleanup_expected = {
                        **authority.expected_state,
                        "backup_id": cleanup_source.backup_id,
                    }
                    cleanup_request = _request(
                        config,
                        cleanup_expected,
                        cleanup_source.backup_id,
                        backup_helper={
                            "sha256": scheduler_package.sha256,
                            "upload_path": None,
                        },
                        replace_unfinished=False,
                        reapply=True,
                    )
                    cleanup_result = run_restore_request(
                        remote,
                        config,
                        request=cleanup_request,
                        prior_mutation_state=prior_mutation_state,
                    )
                    if cleanup_result.outcome != "succeeded":
                        raise result_error(
                            cleanup_result,
                            starting_state=cleanup_expected,
                            prior_mutation_state=prior_mutation_state,
                        )
                    cleanup_facts = mutation_result_facts(
                        cleanup_result,
                        starting_state=cleanup_expected,
                        prior_mutation_state=prior_mutation_state,
                    )
                    prior_mutation_state = str(cleanup_facts["mutation_state"])
                    cleanup_cycles += 1
                    validate_restore_inspection_preflight(remote, config)
                    continue
                if (
                    plan["remaining_restore_bytes"] != 0
                    and plan["remaining_capacity_sufficient"] is not True
                ):
                    raise _safety(
                        "remaining restore capacity is insufficient",
                        "free database volume capacity and rerun restore inspection",
                    )
                if (
                    plan["required_safety_backup_bytes"] != 0
                    and (
                        type(plan["required_safety_backup_bytes"]) is not int
                        or type(plan["available_backup_bytes"]) is not int
                        or plan["available_backup_bytes"] < plan["required_safety_backup_bytes"]
                    )
                ):
                    raise _safety(
                        "remaining safety-backup capacity is insufficient",
                        "free backup capacity and rerun restore inspection",
                    )
                different_unfinished = (
                    authority.target is not None
                    and authority.target.backup_id != backup_id
                    and not _durably_completed(authority)
                )
                if dry_run:
                    next_action = (
                        "review this replacement preview; execution requires --replace-unfinished and fresh typed confirmation"
                        if different_unfinished and not replace_unfinished
                        else "review the exact restore plan and rerun without --dry-run for fresh typed confirmation"
                    )
                    return WorkflowResult("restore", config.name or "", False, "planned", plan, warnings, next_action)
                if different_unfinished and not replace_unfinished:
                    raise _safety(
                        "a different unfinished restore target is already bound",
                        "preview it with --dry-run, then use --replace-unfinished with fresh typed confirmation",
                    )

                if not (confirm or _confirm)(plan):
                    return WorkflowResult(
                        "restore", config.name or "", prior_mutation_state != "unchanged",
                        "confirmation-cancelled",
                        {**plan, "mutation_state": prior_mutation_state, "starting_state": mutable(starting_state)},
                        warnings,
                        "review the exact restore plan and confirm a later run when ready",
                    )
                verification_required = reapply or not (
                    _durably_completed(authority) or _completed_same_backup(authority)
                )
                refresh_required = (
                    verification_required
                    and authority.expected_state["scheduled_backup_sha256"]
                    != scheduler_package.sha256
                )
                request = _request(
                    config,
                    authority.expected_state,
                    backup_id,
                    backup_helper={
                        "sha256": scheduler_package.sha256,
                        "upload_path": "pending-controller-upload" if refresh_required else None,
                    },
                    replace_unfinished=replace_unfinished,
                    reapply=reapply,
                )
                result = run_restore_request(
                    remote,
                    config,
                    request=request,
                    backup_helper_package=scheduler_package if refresh_required else None,
                    prior_mutation_state=prior_mutation_state,
                )
                if result.outcome != "succeeded":
                    raise result_error(
                        result,
                        starting_state=authority.expected_state,
                        prior_mutation_state=prior_mutation_state,
                    )
                facts = mutation_result_facts(
                    result,
                    starting_state=authority.expected_state,
                    prior_mutation_state=prior_mutation_state,
                )
                _validate_success(
                    facts,
                    authority.source,
                    verification_required=verification_required,
                )
                changed = facts["mutation_state"] != "unchanged"
                return WorkflowResult(
                    "restore",
                    config.name or "",
                    changed,
                    "reapplied" if reapply else "restored" if changed else "already-restored",
                    facts,
                    merge_warnings(warnings, result.warnings),
                    "inspect current health separately when completion did not run readiness",
                )
    except OpsError as error:
        state = mutable(error.state)
        state_facts = dict(state) if isinstance(state, Mapping) else {}
        if prior_mutation_state != "unchanged":
            state_facts["mutation_state"] = (
                aggregate_mutation_state(
                    prior_mutation_state,
                    str(state_facts.get("mutation_state", "changed")),
                )
                if error.changed
                else prior_mutation_state
            )
        facts = {
            "backup_id": backup_id,
            "starting_state": None if starting_state is None else mutable(starting_state),
            "plan": None if plan is None else mutable(plan),
            **state_facts,
        }
        changed = error.changed or prior_mutation_state != "unchanged"
        return WorkflowResult(
            "restore",
            config.name or "",
            changed,
            "lock-contended" if error.status is ExitStatus.LOCKED else "safety-refused" if error.status is ExitStatus.SAFETY else f"{error.stage}-failed",
            facts,
            merge_warnings(warnings, tuple(getattr(error, "warnings", ()))),
            error.next_action,
            error.status,
        )


def _collect_authority(remote: Remote, config: EnvironmentConfig, backup_id: str) -> _Authority:
    result = run_request(remote, discovery_request(config, mode="restore", backup_id=backup_id))
    if result.outcome != "succeeded":
        raise result_error(result)
    if not isinstance(result.state, Mapping) or set(result.state) != _DISCOVERY_KEYS:
        raise _safety("restore discovery returned invalid fields")
    state = mutable(result.state)
    assert isinstance(state, Mapping)
    try:
        selected = state["selected_release_id"]
        if selected is not None:
            selected = validate_release_id(selected)
        latest_id = state["last_successful_selection_id"]
        latest_value = state["last_successful_selection"]
        if latest_id is None:
            if latest_value is not None:
                raise ValueError("successful selection identity is inconsistent")
            latest = None
        else:
            latest = SelectionRecord.from_mapping(latest_value)
            if selection_filename(latest) != latest_id:
                raise ValueError("successful selection filename is inconsistent")
        if state["previous_successful_selection"] is not None:
            SelectionRecord.from_mapping(state["previous_successful_selection"])
        databases = validate_restore_database_state(state["restore_database_state"])
        canonical = databases["canonical"]
        canonical_migrations = None if canonical is None or not canonical["migration_table_present"] else canonical["applied_migrations"]
        if (
            None if state["applied_migrations"] is None else tuple(state["applied_migrations"])
        ) != canonical_migrations:
            raise ValueError("canonical migration observations disagree")
        protections = tuple(BackupProtection.from_mapping(item) for item in state["backup_protections"])
        protection_rows = [item.to_mapping() for item in protections]
        if protection_rows != sorted(protection_rows, key=lambda item: item["backup_id"]):
            raise ValueError("backup protections are not ordered")
        protection_sha = hashlib.sha256(_canonical_ascii(protection_rows)).hexdigest()
        if state["backup_protection_sha256"] != protection_sha:
            raise ValueError("backup protection digest is inconsistent")
        target_value = state["restore_target"]
        if target_value is None:
            target, target_sha = None, None
        else:
            if not isinstance(target_value, Mapping) or "sha256" not in target_value:
                raise ValueError("restore target fields are invalid")
            target = RestoreTarget.from_mapping({key: value for key, value in target_value.items() if key != "sha256"})
            target_sha = target_value["sha256"]
            if target_sha != restore_target_sha256(target):
                raise ValueError("restore target digest is inconsistent")
        scheduler_sha = state["scheduled_backup_sha256"]
        if scheduler_sha is not None and (type(scheduler_sha) is not str or _SHA256_RE.fullmatch(scheduler_sha) is None):
            raise ValueError("scheduled backup digest is invalid")
        if type(state["backup_timer_enabled"]) is not bool or state["backup_timer_state"] not in {"active", "inactive", "unknown"}:
            raise ValueError("backup timer authority is invalid")
        if state["service_state"] not in {"running", "stopped", "failed", "unknown"} or state["database_state"] not in {"ready", "absent", "unknown"}:
            raise ValueError("restore health authority is invalid")
        deadline = time.monotonic() + 660.0
        backups = tuple(BackupRecord.from_mapping(item) for item in collect_inventory(remote, config, "list_backups", deadline=deadline))
        releases = tuple(ReleaseRecord.from_mapping(item) for item in collect_inventory(remote, config, "list_releases", deadline=deadline))
        source = _source_for_id(backups, releases, backup_id)
        source_release = next(item for item in releases if item.release_id == source.source_release_id)
        if target is not None:
            _validate_target_authority(target, backups, releases)
        if selected is not None and not any(item.release_id == selected for item in releases):
            raise ValueError("physical current is not an installed release")
        if latest is not None and selected is None:
            raise ValueError("successful restore history requires physical current")
        if latest is not None and not any(item.release_id == latest.release_id for item in releases):
            raise ValueError("successful selection release is not installed")
        expected = {
            "selected_release_id": selected,
            "last_successful_selection_id": latest_id,
            "applied_migrations": canonical_migrations,
            "backup_protection_sha256": protection_sha,
            "scheduled_backup_sha256": scheduler_sha,
            "backup_timer_enabled": state["backup_timer_enabled"],
            "backup_id": backup_id,
            "restore_target_sha256": target_sha,
            "restore_database_state": databases,
        }
        if set(expected) != _EXPECTED_KEYS:
            raise AssertionError("restore expected state changed")
        authority = _Authority(
            selected, latest_id, latest, source, source_release, backups, releases, protections,
            target, target_sha, databases, expected, str(state["service_state"]),
            str(state["backup_timer_state"]), result.warnings,
        )
        _validate_arrangement(authority, config.database_role)
        if (
            target is not None
            and not _durably_completed(authority)
            and target.base_selection_id != latest_id
        ):
            raise ValueError("unfinished restore base selection no longer matches authority")
    except (KeyError, StopIteration, TypeError, ValueError, RecordError, RestoreDatabaseError):
        raise _safety("restore discovery returned invalid authority") from None
    return authority


def _source_for_id(backups: tuple[BackupRecord, ...], releases: tuple[ReleaseRecord, ...], backup_id: str) -> BackupRecord:
    source = next((item for item in backups if item.backup_id == backup_id), None)
    if source is None:
        raise ValueError("selected backup is unavailable")
    release = next((item for item in releases if item.release_id == source.source_release_id), None)
    if release is None or release_migration_versions(release.migrations) != source.migration_versions:
        raise ValueError("selected backup source authority is inconsistent")
    return source


def _validate_target_authority(
    target: RestoreTarget,
    backups: tuple[BackupRecord, ...],
    releases: tuple[ReleaseRecord, ...],
) -> None:
    bound = _source_for_id(backups, releases, target.backup_id)
    if (bound.dump_sha256, bound.source_release_id) != (
        target.dump_sha256,
        target.source_release_id,
    ):
        raise ValueError("restore binding input authority is inconsistent")
    required = {
        target.safety_backup_id,
        *(str(item["backup_id"]) for item in target.safety_backup_attempts),
    }
    if not required.issubset({item.backup_id for item in backups}):
        raise ValueError("restore binding safety authority is incomplete")


def _validate_arrangement(authority: _Authority, expected_owner: str) -> None:
    databases, target = authority.databases, authority.target
    roles = _roles(databases)
    for database in databases.values():
        if isinstance(database, Mapping) and database["owner"] != expected_owner:
            raise ValueError("restore database owner is inconsistent")
    if target is None:
        if roles != frozenset({"canonical"}):
            raise ValueError("unbound restore database arrangement is invalid")
        return
    if roles not in {
        frozenset({"canonical"}), frozenset({"canonical", "temporary"}),
        frozenset({"temporary", "retired"}), frozenset({"retired"}),
        frozenset({"canonical", "retired"}),
    }:
        raise ValueError("bound restore database arrangement is invalid")
    canonical = databases["canonical"]
    temporary = databases["temporary"]
    retired = databases["retired"]
    if roles == frozenset({"canonical"}):
        expected_oid = (
            target.restored_database_oid
            if _durably_completed(authority)
            else target.original_database_oid
        )
        if not isinstance(canonical, Mapping) or canonical["oid"] != expected_oid:
            raise ValueError("bound canonical database identity is inconsistent")
        if not _durably_completed(authority) and not target.temporary_creation_pending:
            raise ValueError("registered temporary database disappeared")
        return
    if roles in {
        frozenset({"canonical", "temporary"}),
        frozenset({"temporary", "retired"}),
    }:
        original = canonical if canonical is not None else retired
        if not isinstance(original, Mapping) or original["oid"] != target.original_database_oid:
            raise ValueError("bound original database identity is inconsistent")
        if not isinstance(temporary, Mapping):
            raise ValueError("bound temporary database is unavailable")
        if target.restored_database_oid is None:
            if (
                not target.temporary_creation_pending
                or temporary["migration_table_present"] is not False
                or temporary["applied_migrations"] is not None
            ):
                raise ValueError("unregistered temporary database is not proved empty")
        elif temporary["oid"] != target.restored_database_oid:
            raise ValueError("bound temporary database identity is inconsistent")
        return
    if roles == frozenset({"retired"}):
        if (
            not isinstance(retired, Mapping)
            or retired["oid"] != target.original_database_oid
            or not target.temporary_creation_pending
        ):
            raise ValueError("retired original database identity is inconsistent")
        return
    if (
        not isinstance(canonical, Mapping)
        or canonical["oid"] != target.restored_database_oid
        or not isinstance(retired, Mapping)
        or retired["oid"] != target.original_database_oid
        or target.temporary_creation_pending
    ):
        raise ValueError("completed restore swap identity is inconsistent")


def _durably_completed(authority: _Authority) -> bool:
    target, latest, canonical = authority.target, authority.latest_selection, authority.databases["canonical"]
    if target is None or latest is None or not isinstance(canonical, Mapping):
        return False
    required = {target.backup_id, *(str(item["backup_id"]) for item in target.safety_backup_attempts)}
    return (
        latest.release_id == target.source_release_id
        and latest.backup_id == target.safety_backup_id
        and latest.observed_previous_release_id == target.observed_previous_release_id
        and required.issubset(set(latest.recovery_backup_ids))
        and canonical["oid"] == target.restored_database_oid
        and authority.selected_release_id == target.source_release_id
        and _roles(authority.databases) in {frozenset({"canonical"}), frozenset({"canonical", "retired"})}
    )


def _plan(
    config: EnvironmentConfig,
    authority: _Authority,
    *,
    scheduler_sha256: str,
    available_bytes: object,
    backup_available_bytes: object,
    database_size_bytes: object,
    requested_backup_id: str,
    replace_unfinished: bool,
    reapply: bool,
) -> dict[str, object]:
    roles = sorted(_roles(authority.databases))
    target, completed = authority.target, _durably_completed(authority)
    different_unfinished = target is not None and target.backup_id != requested_backup_id and not completed
    completed_without_binding = target is None and _completed_same_backup(authority)
    swapped = (
        target is not None
        and _roles(authority.databases) == frozenset({"canonical", "retired"})
        and isinstance(authority.databases["canonical"], Mapping)
        and authority.databases["canonical"]["oid"] == target.restored_database_oid
        and isinstance(authority.databases["retired"], Mapping)
        and authority.databases["retired"]["oid"] == target.original_database_oid
    )
    needs_load = (reapply and not completed) or (
        not completed and not completed_without_binding and not swapped
    )
    needs_safety = (
        reapply and not completed
        or target is None and not completed_without_binding
        or target is not None
        and isinstance(authority.databases["canonical"], Mapping)
        and authority.databases["canonical"]["oid"] == target.original_database_oid
    )
    if target is not None and completed:
        consequences = ["cleanup-retired", "cleanup-binding"]
    elif completed_without_binding and not reapply:
        consequences, needs_load = ["completion-check"], False
    else:
        consequences = ["refresh-scheduled-backup-helper"]
        if needs_safety:
            consequences.extend(["fresh-safety-backup", "publish-restore-binding"])
        if needs_load:
            consequences.extend(["load-temporary", "swap-databases"])
        consequences.extend([
            "select-source-release", "verify", "publish-success",
            "cleanup-retired", "cleanup-binding",
        ])
    remaining_bytes = authority.source.source_database_size_bytes * 2 if needs_load else 0
    sizes = database_size_bytes if isinstance(database_size_bytes, Mapping) else {}
    required_safety_bytes = sizes.get("canonical") if needs_safety else 0
    return {
        "backup_id": requested_backup_id,
        "physical_current_release_id": authority.selected_release_id,
        "last_successful_selection": None if authority.latest_selection is None else authority.latest_selection.to_mapping(),
        "canonical_applied_migrations": mutable(authority.expected_state["applied_migrations"]),
        "requested_backup": authority.source.to_mapping(),
        "source_release_id": authority.source_release.release_id,
        "restore_target": None if target is None else {**target.to_mapping(), "sha256": authority.target_sha256},
        "restore_target_sha256": authority.target_sha256,
        "restore_database_state": mutable(authority.databases),
        "physical_database_arrangement": roles,
        "retained_backup_protections": [item.to_mapping() for item in authority.protections],
        "retained_safety_material": [] if target is None else [dict(item) for item in target.safety_backup_attempts],
        "remaining_restore_bytes": remaining_bytes,
        "required_safety_backup_bytes": required_safety_bytes,
        "available_database_bytes": available_bytes,
        "available_backup_bytes": backup_available_bytes,
        "remaining_capacity_sufficient": remaining_bytes == 0 or (
            type(available_bytes) is int and available_bytes >= remaining_bytes
        ),
        "remaining_consequences": consequences,
        "scheduled_backup_sha256": authority.expected_state["scheduled_backup_sha256"],
        "required_scheduled_backup_sha256": scheduler_sha256,
        "scheduler_refresh_required": authority.expected_state["scheduled_backup_sha256"] != scheduler_sha256,
        "backup_timer_enabled": authority.expected_state["backup_timer_enabled"],
        "backup_timer_state": authority.timer_state,
        "service_state": authority.service_state,
        "planned_pre_restore_backup": needs_safety,
        "replace_unfinished": replace_unfinished,
        "replace_unfinished_required": different_unfinished,
        "reapply": reapply,
        "data_loss_warning": "restoring replaces the database and discards changes made after the selected backup",
        "typed_confirmation": f"restore {config.name or ''} {requested_backup_id}",
    }


def _completed_same_backup(authority: _Authority) -> bool:
    latest, canonical = authority.latest_selection, authority.databases["canonical"]
    return (
        authority.target is None and latest is not None
        and latest.release_id == authority.source.source_release_id
        and authority.source.backup_id in latest.recovery_backup_ids
        and authority.selected_release_id == authority.source.source_release_id
        and _roles(authority.databases) == frozenset({"canonical"})
        and isinstance(canonical, Mapping) and canonical["migration_table_present"] is True
        and canonical["applied_migrations"] == authority.source.migration_versions
    )


def _request(
    config: EnvironmentConfig,
    expected_state: Mapping[str, object],
    backup_id: str,
    *,
    backup_helper: Mapping[str, object],
    replace_unfinished: bool,
    reapply: bool,
) -> HostRequest:
    from ..helper_client.runner import new_correlation_id

    return HostRequest(
        PROTOCOL_VERSION,
        "restore",
        new_correlation_id(),
        expected_state,
        helper_paths(config),
        {
            "backup_id": backup_id,
            "credentials_path": _PGPASS,
            "database": database_settings(config),
            "verification": verification_settings(config),
            "backup_helper": backup_helper,
            "prune_backup_ids": [],
            "replace_unfinished": replace_unfinished,
            "reapply": reapply,
        },
    )


def _validate_success(
    facts: Mapping[str, object],
    source: BackupRecord,
    *,
    verification_required: bool,
) -> None:
    if facts.get("desired_release_id") != source.source_release_id or facts.get("backup_id") != source.backup_id:
        raise _safety("restore helper returned inconsistent completion authority")
    report = facts.get("report")
    if verification_required and report is None:
        raise _safety("restore helper omitted required verification evidence")
    if report is not None:
        try:
            successful_verification(report, source.source_release_id)
        except ValueError:
            raise _safety("restore helper returned invalid verification evidence") from None


def _roles(databases: Mapping[str, object]) -> frozenset[str]:
    return frozenset(key for key, value in databases.items() if value is not None)


def _canonical_ascii(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _confirm(plan: Mapping[str, object]) -> bool:
    expected = str(plan["typed_confirmation"])
    return input(
        f"Restore {plan['backup_id']} from {plan['source_release_id']}? Type '{expected}' to continue: "
    ).strip() == expected


def _safety(message: str, next_action: str | None = None) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "restore",
        message,
        False,
        next_action=next_action or "inspect the selected backup and restore-specific host authority before retrying",
    )


__all__ = ["restore"]
