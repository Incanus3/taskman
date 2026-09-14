"""Exact protocol-v3 mutation evidence shared by helper and controller.

The wire envelope establishes bounded JSON.  This module establishes the
operation-specific meaning of that JSON before either side treats it as
completion, failure, or public mutation evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
import re

from taskman_ops.releases.identifiers import validate_release_id

from .identifiers import ProtocolError, validate_string


MUTATION_OPERATIONS = frozenset({"deploy", "genesis", "restore", "cleanup"})
MUTATION_STATES = frozenset({"unchanged", "changed", "unknown"})

_COMMON_FIELDS = frozenset(
    {
        "mutation_state",
        "exit_code",
        "failed_boundary",
        "observations",
        "unavailable_fields",
        "inspection_error",
        "report",
    }
)
_ADDITIONAL_FIELDS = {
    "deploy": frozenset({"desired_release_id", "backup_id"}),
    "genesis": frozenset({"desired_release_id", "backup_id"}),
    "restore": frozenset({"desired_release_id", "backup_id", "pre_restore_backup_id"}),
    "cleanup": frozenset({"completed_targets"}),
}
_COMMON_OBSERVATIONS = frozenset(
    {
        "selected_release_id",
        "last_successful_selection_id",
        "applied_migrations",
        "protected_backup_ids",
        "backup_protection_sha256",
        "restore_target_sha256",
        "database_state",
        "service_state",
        "scheduled_backup_sha256",
        "backup_timer_enabled",
        "backup_timer_state",
    }
)
_OBSERVATION_FIELDS = {
    "deploy": _COMMON_OBSERVATIONS,
    "genesis": _COMMON_OBSERVATIONS,
    "restore": _COMMON_OBSERVATIONS | {"restore_database_state"},
    "cleanup": frozenset(
        {
            "selected_release_id",
            "last_successful_selection_id",
            "backup_protection_sha256",
            "restore_target_sha256",
        }
    ),
}
_BOUNDARIES = frozenset(
    {
        "input",
        "lock",
        "authority",
        "expected_state",
        "backup_helper",
        "staging",
        "backup",
        "protection",
        "migration",
        "selection",
        "service",
        "verification",
        "history",
        "restore",
        "cleanup",
        "inspection",
    }
)
_INSPECTION_ERRORS = frozenset(
    {"lock-unavailable", "inspection-failed", "inspection-timed-out", "unsafe-observation"}
)
_OUTCOMES_BY_EXIT_CODE = {
    2: frozenset({"refused"}),
    5: frozenset({"retryable"}),
    6: frozenset({"retryable", "manual"}),
    7: frozenset({"retryable", "manual"}),
    8: frozenset({"retryable", "manual"}),
    9: frozenset({"retryable"}),
    10: frozenset({"refused", "retryable", "manual"}),
    11: frozenset({"refused", "retryable", "manual"}),
    12: frozenset({"retryable"}),
}
_EXIT_CODES_BY_BOUNDARY = {
    "input": frozenset({2}),
    "lock": frozenset({12}),
    "authority": frozenset({10}),
    "expected_state": frozenset({10}),
    "backup_helper": frozenset({8}),
    "staging": frozenset({8}),
    "backup": frozenset({6}),
    "protection": frozenset({8}),
    "migration": frozenset({7}),
    "selection": frozenset({8}),
    "service": frozenset({8}),
    "verification": frozenset({9}),
    "history": frozenset({8}),
    "restore": frozenset({11}),
    "cleanup": frozenset({10}),
    # Inspection can fail while establishing authority for more than one
    # operation category.  It never erases that operation's primary status.
    "inspection": frozenset({5, 8, 10, 11}),
}
_CHECK_NAMES = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)
_LIFECYCLE_CHECKS = frozenset(_CHECK_NAMES[:5])
_READINESS_CHECKS = frozenset(_CHECK_NAMES[5:])
_FAILED_REPORT_ACTION = (
    "inspect the fixed verification summaries and correct the reported host state before retrying"
)
_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "exit_status",
        "release_id",
        "expected_release_id",
        "checks",
        "next_action",
    }
)
_CHECK_FIELDS = frozenset({"schema_version", "name", "status", "summary"})
_DATABASE_FIELDS = frozenset(
    {"oid", "owner", "migration_table_present", "applied_migrations"}
)
_RESTORE_DATABASE_FIELDS = frozenset({"canonical", "temporary", "retired"})
_TARGET_FIELDS = frozenset({"kind", "identifier", "path"})
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SELECTION_ID_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DATABASE_OWNER_RE = re.compile(r"[a-z][a-z0-9_]{0,49}\Z")


def validate_mutation_state(
    operation: object,
    outcome: object,
    state: object,
) -> dict[str, object]:
    """Return one mutable exact mutation state or raise :class:`ProtocolError`."""

    if type(operation) is not str or operation not in MUTATION_OPERATIONS:
        raise ProtocolError("invalid mutation operation")
    if type(outcome) is not str or outcome not in {
        "succeeded",
        "refused",
        "retryable",
        "manual",
    }:
        raise ProtocolError("invalid mutation outcome")
    mapping = _exact(state, _COMMON_FIELDS | _ADDITIONAL_FIELDS[operation], "mutation state")

    mutation_state = mapping["mutation_state"]
    if type(mutation_state) is not str or mutation_state not in MUTATION_STATES:
        raise ProtocolError("invalid mutation classification")
    exit_code = mapping["exit_code"]
    if type(exit_code) is not int:
        raise ProtocolError("invalid mutation exit status")
    boundary = mapping["failed_boundary"]
    if outcome == "succeeded":
        if exit_code != 0 or boundary is not None or mutation_state == "unknown":
            raise ProtocolError("mutation success evidence is contradictory")
    else:
        if (
            exit_code not in _OUTCOMES_BY_EXIT_CODE
            or outcome not in _OUTCOMES_BY_EXIT_CODE[exit_code]
            or type(boundary) is not str
            or boundary not in _BOUNDARIES
            or not _valid_failure_category(operation, boundary, exit_code)
        ):
            raise ProtocolError("mutation failure evidence is contradictory")
        if boundary == "lock" and mutation_state != "unchanged":
            raise ProtocolError("lock refusal cannot claim an invocation mutation")

    observations = _validate_observations(operation, mapping["observations"])
    unavailable = _validate_unavailable(
        operation,
        observations,
        mapping["unavailable_fields"],
        mapping["inspection_error"],
    )
    verification_report = _optional_report(mapping["report"])

    if boundary == "verification":
        if verification_report is None or verification_report["exit_status"] != 9:
            raise ProtocolError("verification failure requires its failed report")
    if outcome == "succeeded":
        _validate_success_authority(operation, mapping, observations, unavailable, verification_report)

    result = {key: _mutable(value) for key, value in mapping.items()}
    if operation in {"deploy", "genesis", "restore"}:
        result["desired_release_id"] = _release_id(
            mapping["desired_release_id"], nullable=outcome != "succeeded"
        )
        result["backup_id"] = _backup_id(mapping["backup_id"], nullable=True)
        if operation == "restore":
            result["pre_restore_backup_id"] = _backup_id(
                mapping["pre_restore_backup_id"], nullable=True
            )
    else:
        completed = _completed_targets(mapping["completed_targets"])
        result["completed_targets"] = completed
        if verification_report is not None:
            raise ProtocolError("cleanup result cannot contain a verification report")
    result["observations"] = observations
    result["unavailable_fields"] = unavailable
    result["report"] = verification_report
    return result


def _valid_failure_category(operation: str, boundary: str, exit_code: int) -> bool:
    expected = _EXIT_CODES_BY_BOUNDARY[boundary]
    if boundary in {"selection", "service", "history", "inspection"}:
        expected = {
            "deploy": frozenset({8}),
            "genesis": frozenset({8}),
            "restore": frozenset({11}),
            "cleanup": frozenset({10}),
        }[operation]
    if boundary == "restore" and operation != "restore":
        return False
    if boundary == "cleanup" and operation != "cleanup":
        return False
    if boundary in {"backup_helper", "staging", "protection", "migration"} and operation not in {
        "deploy",
        "genesis",
    }:
        return False
    return exit_code in expected


def validate_verification_report(value: object) -> dict[str, object]:
    """Validate the one secret-free report schema used in mutation evidence."""

    report = _exact(value, _REPORT_FIELDS, "verification report")
    if report["schema_version"] != 1 or type(report["exit_status"]) is not int:
        raise ProtocolError("invalid verification report")
    exit_status = report["exit_status"]
    if exit_status not in {0, 8, 9}:
        raise ProtocolError("invalid verification report exit status")
    status = report["status"]
    if status != ("ok" if exit_status == 0 else "failed"):
        raise ProtocolError("verification report status is contradictory")
    release_id = _release_id(report["release_id"], nullable=True)
    expected_release_id = _release_id(report["expected_release_id"], nullable=True)

    raw_checks = report["checks"]
    if not isinstance(raw_checks, (list, tuple)) or not raw_checks:
        raise ProtocolError("invalid verification report checks")
    checks: list[dict[str, object]] = []
    for raw_check in raw_checks:
        check = _exact(raw_check, _CHECK_FIELDS, "verification check")
        if check["schema_version"] != 1:
            raise ProtocolError("invalid verification check schema")
        name = check["name"]
        status_value = check["status"]
        if (
            type(name) is not str
            or type(status_value) is not str
            or status_value not in {"passed", "failed"}
        ):
            raise ProtocolError("invalid verification check")
        summary = validate_string(check["summary"])
        if not summary:
            raise ProtocolError("invalid verification check summary")
        checks.append(
            {
                "schema_version": 1,
                "name": name,
                "status": status_value,
                "summary": summary,
            }
        )
    names = tuple(str(check["name"]) for check in checks)
    if names != _CHECK_NAMES[: len(names)]:
        raise ProtocolError("verification checks are not an ordered unique prefix")
    failed_names = {
        str(check["name"]) for check in checks if check["status"] == "failed"
    }
    if exit_status == 0:
        if (
            names != _CHECK_NAMES
            or failed_names
            or report["next_action"] is not None
        ):
            raise ProtocolError("successful verification report is incomplete")
    else:
        if report["next_action"] != _FAILED_REPORT_ACTION or not failed_names:
            raise ProtocolError("failed verification report is incomplete")
        if exit_status == 8 and (
            len(checks) > len(_LIFECYCLE_CHECKS) or not failed_names <= _LIFECYCLE_CHECKS
        ):
            raise ProtocolError("verification lifecycle report is contradictory")
        if exit_status == 9 and (
            len(checks) < len(_LIFECYCLE_CHECKS) + 1
            or any(check["status"] != "passed" for check in checks[: len(_LIFECYCLE_CHECKS)])
            or not failed_names <= _READINESS_CHECKS
        ):
            raise ProtocolError("verification readiness report is contradictory")

    return {
        "schema_version": 1,
        "status": status,
        "exit_status": exit_status,
        "release_id": release_id,
        "expected_release_id": expected_release_id,
        "checks": checks,
        "next_action": report["next_action"],
    }


def unavailable_observations(operation: object) -> tuple[dict[str, object], list[str]]:
    """Return the exact all-unavailable final observation shape for an operation."""

    if type(operation) is not str or operation not in MUTATION_OPERATIONS:
        raise ProtocolError("invalid mutation operation")
    observations: dict[str, object] = {}
    for field in _OBSERVATION_FIELDS[operation]:
        observations[field] = (
            "unknown"
            if field in {"database_state", "service_state", "backup_timer_state"}
            else None
        )
    return observations, sorted(observations)


def _validate_observations(operation: str, value: object) -> dict[str, object]:
    source = _exact(value, _OBSERVATION_FIELDS[operation], "final observations")
    result = {key: _mutable(item) for key, item in source.items()}

    result["selected_release_id"] = _release_id(source["selected_release_id"], nullable=True)
    result["last_successful_selection_id"] = _selection_id(
        source["last_successful_selection_id"], nullable=True
    )
    result["backup_protection_sha256"] = _sha256(
        source["backup_protection_sha256"], nullable=True
    )
    result["restore_target_sha256"] = _sha256(
        source["restore_target_sha256"], nullable=True
    )
    if operation == "cleanup":
        return result

    result["applied_migrations"] = _migration_versions(
        source["applied_migrations"], nullable=True
    )
    result["protected_backup_ids"] = _backup_ids(
        source["protected_backup_ids"], nullable=True
    )
    result["database_state"] = _enum(
        source["database_state"], {"ready", "absent", "unknown"}, "database state"
    )
    result["service_state"] = _enum(
        source["service_state"], {"running", "stopped", "failed", "unknown"}, "service state"
    )
    result["scheduled_backup_sha256"] = _sha256(
        source["scheduled_backup_sha256"], nullable=True
    )
    enabled = source["backup_timer_enabled"]
    if enabled is not None and type(enabled) is not bool:
        raise ProtocolError("invalid backup timer enablement")
    result["backup_timer_enabled"] = enabled
    result["backup_timer_state"] = _enum(
        source["backup_timer_state"], {"active", "inactive", "unknown"}, "backup timer state"
    )
    if operation == "restore":
        result["restore_database_state"] = _restore_database_state(
            source["restore_database_state"]
        )
        restore_databases = result["restore_database_state"]
        canonical = None if restore_databases is None else restore_databases["canonical"]
        if (
            canonical is not None
            and result["applied_migrations"]
            != (
                canonical["applied_migrations"]
                if canonical["migration_table_present"]
                else None
            )
        ):
            raise ProtocolError("restore canonical migrations contradict final observations")
    return result


def _validate_unavailable(
    operation: str,
    observations: Mapping[str, object],
    value: object,
    inspection_error: object,
) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(type(item) is str for item in value):
        raise ProtocolError("invalid unavailable observation fields")
    unavailable = list(value)
    if unavailable != sorted(set(unavailable)) or not set(unavailable) <= set(observations):
        raise ProtocolError("invalid unavailable observation fields")
    if inspection_error is not None and (
        type(inspection_error) is not str or inspection_error not in _INSPECTION_ERRORS
    ):
        raise ProtocolError("invalid final inspection error")
    if bool(unavailable) != (inspection_error is not None):
        raise ProtocolError("final inspection evidence is contradictory")

    enum_fields = {"database_state", "service_state", "backup_timer_state"}
    for field, observed in observations.items():
        if field in unavailable:
            expected = "unknown" if field in enum_fields else None
            if observed != expected:
                raise ProtocolError("unavailable observation contains a stale value")
        elif field in enum_fields and observed == "unknown":
            raise ProtocolError("unknown observation lacks its unavailable marker")

    null_requires_unavailable = {
        "backup_protection_sha256",
        "protected_backup_ids",
        "backup_timer_enabled",
        "restore_database_state",
    }
    if operation in {"deploy", "genesis"}:
        null_requires_unavailable.add("applied_migrations")
    for field in null_requires_unavailable & set(observations):
        if observations[field] is None and field not in unavailable:
            raise ProtocolError("missing unavailable observation marker")
    if inspection_error == "lock-unavailable" and set(unavailable) != set(observations):
        raise ProtocolError("lock failure requires all observations to be unavailable")
    return unavailable


def _validate_success_authority(
    operation: str,
    state: Mapping[str, object],
    observations: Mapping[str, object],
    unavailable: list[str],
    report: dict[str, object] | None,
) -> None:
    if state["inspection_error"] is not None:
        raise ProtocolError("successful mutation cannot have an inspection error")
    if operation == "cleanup":
        if unavailable or report is not None:
            raise ProtocolError("cleanup success lacks complete final authority")
        return

    desired = _release_id(state["desired_release_id"], nullable=False)
    essential = {
        "selected_release_id",
        "last_successful_selection_id",
        "applied_migrations",
        "protected_backup_ids",
        "backup_protection_sha256",
        "restore_target_sha256",
    }
    if unavailable and operation in {"deploy", "genesis"}:
        raise ProtocolError("deployment success lacks complete final authority")
    if essential & set(unavailable):
        raise ProtocolError("mutation success lacks completion authority")
    if (
        observations["selected_release_id"] != desired
        or observations["last_successful_selection_id"] is None
        or observations["restore_target_sha256"] is not None
    ):
        raise ProtocolError("mutation success does not prove the desired selection")

    if operation in {"deploy", "genesis"}:
        if (
            report is None
            or report["exit_status"] != 0
            or report["release_id"] != desired
            or report["expected_release_id"] != desired
            or observations["database_state"] != "ready"
            or observations["service_state"] != "running"
        ):
            raise ProtocolError("deployment success lacks passing verification")
        return

    # Restore may complete only authority-validated cleanup after durable
    # success, in which case no new verification report is required.
    if report is not None and (
        report["exit_status"] != 0
        or report["release_id"] != desired
        or report["expected_release_id"] != desired
    ):
        raise ProtocolError("restore success report is contradictory")
    restore_databases = observations["restore_database_state"]
    if restore_databases is None or restore_databases["canonical"] is None:
        raise ProtocolError("restore success lacks canonical database identity")


def _optional_report(value: object) -> dict[str, object] | None:
    return None if value is None else validate_verification_report(value)


def _restore_database_state(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    mapping = _exact(value, _RESTORE_DATABASE_FIELDS, "restore database state")
    return {key: _database_observation(mapping[key]) for key in ("canonical", "temporary", "retired")}


def _database_observation(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    mapping = _exact(value, _DATABASE_FIELDS, "database observation")
    oid = mapping["oid"]
    owner = mapping["owner"]
    table_present = mapping["migration_table_present"]
    if type(oid) is not int or oid <= 0:
        raise ProtocolError("invalid database OID")
    if type(owner) is not str or _DATABASE_OWNER_RE.fullmatch(owner) is None:
        raise ProtocolError("invalid database owner")
    if type(table_present) is not bool:
        raise ProtocolError("invalid migration table observation")
    migrations = mapping["applied_migrations"]
    if table_present:
        migrations = _migration_versions(migrations, nullable=False)
    elif migrations is not None:
        raise ProtocolError("absent migration table has migration versions")
    return {
        "oid": oid,
        "owner": owner,
        "migration_table_present": table_present,
        "applied_migrations": migrations,
    }


def _completed_targets(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        raise ProtocolError("invalid completed cleanup targets")
    targets = [_cleanup_target(item) for item in value]
    identities = [_target_identity(item) for item in targets]
    if identities != sorted(set(identities)):
        raise ProtocolError("completed cleanup targets are not sorted and unique")
    return targets


def _cleanup_target(value: object) -> dict[str, object]:
    mapping = _exact(value, _TARGET_FIELDS, "cleanup target")
    kind = mapping["kind"]
    identifier = mapping["identifier"]
    path = mapping["path"]
    if type(kind) is not str or kind not in {"release", "backup", "temporary"}:
        raise ProtocolError("invalid cleanup target kind")
    if type(identifier) is not str or not identifier:
        raise ProtocolError("invalid cleanup target identifier")
    if type(path) is not str:
        raise ProtocolError("invalid cleanup target path")
    candidate = PurePosixPath(path)
    if not candidate.is_absolute() or str(candidate) != path or ".." in candidate.parts:
        raise ProtocolError("invalid cleanup target path")
    if kind == "release":
        _release_id(identifier, nullable=False)
    elif kind == "backup":
        _backup_id(identifier, nullable=False)
    elif "/" in identifier or candidate.name != identifier:
        raise ProtocolError("invalid temporary cleanup target")
    return {"kind": kind, "identifier": identifier, "path": path}


def _target_identity(value: Mapping[str, object]) -> tuple[str, str, str]:
    return str(value["kind"]), str(value["identifier"]), str(value["path"])


def _migration_versions(value: object, *, nullable: bool) -> list[int] | None:
    if nullable and value is None:
        return None
    if not isinstance(value, (list, tuple)) or any(type(item) is not int or item < 0 for item in value):
        raise ProtocolError("invalid migration versions")
    result = list(value)
    if result != sorted(set(result)):
        raise ProtocolError("migration versions are not sorted and unique")
    return result


def _backup_ids(value: object, *, nullable: bool) -> list[str] | None:
    if nullable and value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ProtocolError("invalid protected backup identifiers")
    result = [_backup_id(item, nullable=False) for item in value]
    if result != sorted(set(result)):
        raise ProtocolError("protected backup identifiers are not sorted and unique")
    return result


def _release_id(value: object, *, nullable: bool) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str:
        raise ProtocolError("invalid release identifier")
    try:
        return validate_release_id(value)
    except ValueError as error:
        raise ProtocolError("invalid release identifier") from error


def _backup_id(value: object, *, nullable: bool) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or _BACKUP_ID_RE.fullmatch(value) is None:
        raise ProtocolError("invalid backup identifier")
    return value


def _selection_id(value: object, *, nullable: bool) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or _SELECTION_ID_RE.fullmatch(value) is None:
        raise ProtocolError("invalid successful selection identifier")
    return value


def _sha256(value: object, *, nullable: bool) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ProtocolError("invalid SHA-256")
    return value


def _enum(value: object, allowed: set[str], label: str) -> str:
    if type(value) is not str or value not in allowed:
        raise ProtocolError(f"invalid {label}")
    return value


def _exact(value: object, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields or not all(type(key) is str for key in value):
        raise ProtocolError(f"invalid {label} fields")
    return value


def _mutable(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _mutable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mutable(item) for item in value]
    return value


__all__ = [
    "MUTATION_OPERATIONS",
    "MUTATION_STATES",
    "unavailable_observations",
    "validate_mutation_state",
    "validate_verification_report",
]
