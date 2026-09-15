"""Restore-specific PostgreSQL observation and bounded database mechanics.

This module owns native facts and narrowly scoped PostgreSQL actions.  The
restore operation remains responsible for deciding which recognized
arrangement and consequence is appropriate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
import re

from .commands import CommandError, run_command
from .database import (
    database_mapping,
    migration_versions,
    observe_database_state_or_empty,
)
from .paths import ManagedPaths
from .restore_target import RestoreTarget, replace_restore_target


_DATABASE_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,49}\Z")
_ROLE_KEYS = ("canonical", "temporary", "retired")
_ENTRY_KEYS = frozenset(
    {"oid", "owner", "migration_table_present", "applied_migrations"}
)
_COMMAND_TIMEOUT_SECONDS = 60.0


class RestoreDatabaseError(ValueError):
    """Restore database identity or direct observation is not authoritative."""


def restore_database_names(database: Mapping[str, object]) -> dict[str, str]:
    """Derive the only three database names owned by restore."""

    value = database_mapping(database)
    name = value["name"]
    role = value["role"]
    if (
        type(name) is not str
        or _DATABASE_NAME_RE.fullmatch(name) is None
        or type(role) is not str
        or _DATABASE_NAME_RE.fullmatch(role) is None
    ):
        raise RestoreDatabaseError("restore database settings are invalid")
    names = {
        "canonical": name,
        "temporary": f"{name}__restore_tmp",
        "retired": f"{name}__restore_old",
    }
    if any(len(item) > 63 for item in names.values()):
        raise RestoreDatabaseError("derived restore database name is oversized")
    return names


def validate_restore_database_state(value: object) -> dict[str, object]:
    """Validate exact absent/table-missing/table-present response shapes."""

    if not isinstance(value, Mapping) or set(value) != set(_ROLE_KEYS):
        raise RestoreDatabaseError("restore database state fields are invalid")
    result: dict[str, object] = {}
    seen_oids: set[int] = set()
    for role in _ROLE_KEYS:
        entry = value[role]
        if entry is None:
            result[role] = None
            continue
        if not isinstance(entry, Mapping) or set(entry) != _ENTRY_KEYS:
            raise RestoreDatabaseError("restore database entry fields are invalid")
        oid = entry["oid"]
        owner = entry["owner"]
        table_present = entry["migration_table_present"]
        applied = entry["applied_migrations"]
        if type(oid) is not int or oid <= 0 or oid in seen_oids:
            raise RestoreDatabaseError("restore database OID is invalid")
        if type(owner) is not str or not owner:
            raise RestoreDatabaseError("restore database owner is invalid")
        if type(table_present) is not bool:
            raise RestoreDatabaseError("restore migration-table observation is invalid")
        if table_present:
            try:
                versions = migration_versions(applied)
            except (TypeError, ValueError) as error:
                raise RestoreDatabaseError("restore migration versions are invalid") from error
        else:
            if applied is not None:
                raise RestoreDatabaseError("missing migration table cannot have versions")
            versions = None
        seen_oids.add(oid)
        result[role] = {
            "oid": oid,
            "owner": owner,
            "migration_table_present": table_present,
            "applied_migrations": versions,
        }
    return result


def observe_restore_databases(
    database: Mapping[str, object], credentials: Path
) -> dict[str, object]:
    """Observe canonical, temporary, and retired identities without inference."""

    names = restore_database_names(database)
    settings = database_mapping(database)
    credentials = _credentials_path(credentials)
    try:
        rows = _catalog_rows(settings, names)
        observed: dict[str, object] = {role: None for role in _ROLE_KEYS}
        role_by_name = {name: role for role, name in names.items()}
        for line in rows.splitlines():
            parts = line.split("\t")
            if len(parts) != 3 or parts[0] not in role_by_name:
                raise RestoreDatabaseError("restore database catalog result is invalid")
            role = role_by_name[parts[0]]
            if observed[role] is not None:
                raise RestoreDatabaseError("restore database catalog result is duplicated")
            try:
                oid = int(parts[1])
            except ValueError as error:
                raise RestoreDatabaseError("restore database OID is invalid") from error
            owner = parts[2]
            if owner != settings["role"]:
                raise RestoreDatabaseError("restore database ownership is invalid")
            table_present, applied = _migration_observation(
                settings, credentials, parts[0]
            )
            observed[role] = {
                "oid": oid,
                "owner": owner,
                "migration_table_present": table_present,
                "applied_migrations": applied,
            }
        return validate_restore_database_state(observed)
    except RestoreDatabaseError:
        raise
    except (CommandError, UnicodeError, TypeError, ValueError) as error:
        raise RestoreDatabaseError("restore database observation failed") from error


def prove_temporary_database_empty(
    database: Mapping[str, object], credentials: Path
) -> int:
    """Prove an unregistered temporary matches the controlled empty template."""

    observed = observe_restore_databases(database, credentials)
    temporary = observed["temporary"]
    if not isinstance(temporary, Mapping):
        raise RestoreDatabaseError("restore temporary database is absent")
    if temporary["owner"] != database_mapping(database)["role"]:
        raise RestoreDatabaseError("restore temporary database ownership is invalid")
    if temporary["migration_table_present"] is not False:
        raise RestoreDatabaseError("restore temporary database is not empty")
    oid = temporary["oid"]
    if type(oid) is not int or oid <= 0:
        raise RestoreDatabaseError("restore temporary database OID is invalid")
    if _active_writer_count(database, oid) != 0:
        raise RestoreDatabaseError("restore temporary database has active writers")
    if not _empty_template_matches(database, credentials):
        raise RestoreDatabaseError("restore temporary database is not empty")
    return oid


def register_restored_database(
    paths: ManagedPaths,
    target: RestoreTarget,
    database: Mapping[str, object],
    credentials: Path,
) -> RestoreTarget:
    """Durably register a proved-empty temporary OID before any dump load."""

    if not isinstance(target, RestoreTarget) or not target.temporary_creation_pending:
        raise RestoreDatabaseError("restore temporary creation intent is not pending")
    oid = prove_temporary_database_empty(database, credentials)
    if oid == target.original_database_oid:
        raise RestoreDatabaseError("restore temporary database matches the original OID")
    if target.restored_database_oid is not None and _oid_present(
        database, target.restored_database_oid
    ):
        raise RestoreDatabaseError("previous restored database OID is still present")
    updated = replace(
        target, restored_database_oid=oid, temporary_creation_pending=False
    )
    replace_restore_target(paths, updated)
    return updated


def begin_temporary_rebuild(paths: ManagedPaths, target: RestoreTarget) -> RestoreTarget:
    """Durably record rebuild intent while retaining the old registered OID."""

    if (
        not isinstance(target, RestoreTarget)
        or target.temporary_creation_pending
        or target.restored_database_oid is None
    ):
        raise RestoreDatabaseError("registered restore temporary is required")
    updated = replace(target, temporary_creation_pending=True)
    replace_restore_target(paths, updated)
    return updated


def create_temporary_database(
    database: Mapping[str, object], credentials: Path
) -> None:
    """Create only the exact derived temporary database."""

    names = restore_database_names(database)
    observed = observe_restore_databases(database, credentials)
    if observed["temporary"] is not None:
        raise RestoreDatabaseError("restore temporary database already exists")
    _admin_query(
        database,
        f'CREATE DATABASE "{names["temporary"]}" OWNER "{database["role"]}"',
    )


def drop_registered_temporary(
    database: Mapping[str, object], credentials: Path, expected_oid: int
) -> None:
    """Drop only a temporary database with the exact registered OID."""

    expected_oid = _positive_oid(expected_oid)
    names = restore_database_names(database)
    observed = observe_restore_databases(database, credentials)
    temporary = observed["temporary"]
    if temporary is None:
        if _oid_present(database, expected_oid):
            raise RestoreDatabaseError("registered restore database OID moved unexpectedly")
        return
    if not isinstance(temporary, Mapping) or temporary["oid"] != expected_oid:
        raise RestoreDatabaseError("restore temporary database OID changed")
    _admin_query(database, f'DROP DATABASE "{names["temporary"]}" WITH (FORCE)')


def rename_registered_database(
    database: Mapping[str, object],
    credentials: Path,
    source_role: str,
    destination_role: str,
    expected_oid: int,
) -> None:
    """Rename one exact restore-owned identity without accepting arbitrary names."""

    if source_role not in _ROLE_KEYS or destination_role not in _ROLE_KEYS or source_role == destination_role:
        raise RestoreDatabaseError("restore database rename roles are invalid")
    expected_oid = _positive_oid(expected_oid)
    names = restore_database_names(database)
    observed = observe_restore_databases(database, credentials)
    source = observed[source_role]
    if not isinstance(source, Mapping) or source["oid"] != expected_oid:
        raise RestoreDatabaseError("restore database rename source changed")
    if observed[destination_role] is not None:
        raise RestoreDatabaseError("restore database rename destination exists")
    _admin_query(
        database,
        f'ALTER DATABASE "{names[source_role]}" RENAME TO "{names[destination_role]}"',
    )


def load_registered_temporary(
    target: RestoreTarget,
    database: Mapping[str, object],
    credentials: Path,
    dump: Path,
) -> None:
    """Load a dump only after the binding durably identifies the temporary OID."""

    if (
        not isinstance(target, RestoreTarget)
        or target.temporary_creation_pending
        or target.restored_database_oid is None
    ):
        raise RestoreDatabaseError("restore temporary database is not durably registered")
    credentials = _credentials_path(credentials)
    if not isinstance(dump, Path) or not dump.is_absolute():
        raise RestoreDatabaseError("restore dump path is invalid")
    observed = observe_restore_databases(database, credentials)
    temporary = observed["temporary"]
    if not isinstance(temporary, Mapping) or temporary["oid"] != target.restored_database_oid:
        raise RestoreDatabaseError("registered restore temporary database is unavailable")
    names = restore_database_names(database)
    settings = database_mapping(database)
    try:
        run_command(
            (
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--host",
                str(settings["host"]),
                "--port",
                str(settings["port"]),
                "--username",
                str(settings["role"]),
                "--dbname",
                names["temporary"],
                "--no-password",
                dump.as_posix(),
            ),
            env={"PGPASSFILE": credentials.as_posix()},
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
        )
    except CommandError as error:
        raise RestoreDatabaseError("restore dump load failed") from error


def _catalog_rows(database: Mapping[str, object], names: Mapping[str, str]) -> str:
    return _admin_query(
        database,
        "SELECT datname || E'\\t' || oid::text || E'\\t' || "
        "pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database "
        "WHERE datname IN (:'canonical', :'temporary', :'retired') ORDER BY datname",
        variables=names,
    ).decode("utf-8", "strict")


def _migration_observation(
    database: Mapping[str, object], credentials: Path, name: str
) -> tuple[bool, tuple[int, ...] | None]:
    table = _application_query(
        database,
        credentials,
        name,
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' "
        "AND table_name = 'schema_migrations'",
    ).strip()
    if table == b"0":
        return False, None
    if table != b"1":
        raise RestoreDatabaseError("restore migration-table observation is invalid")
    rows = _application_query(
        database,
        credentials,
        name,
        "SELECT version FROM schema_migrations ORDER BY version",
    ).splitlines()
    try:
        versions = migration_versions(tuple(int(item) for item in rows if item))
    except (TypeError, ValueError) as error:
        raise RestoreDatabaseError("restore migration versions are invalid") from error
    return True, versions


def _active_writer_count(database: Mapping[str, object], oid: int) -> int:
    output = _admin_query(
        database,
        "SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE datid = :'oid'::oid "
        "AND pid <> pg_catalog.pg_backend_pid()",
        variables={"oid": str(_positive_oid(oid))},
    ).strip()
    try:
        count = int(output)
    except ValueError as error:
        raise RestoreDatabaseError("restore writer observation is invalid") from error
    if count < 0:
        raise RestoreDatabaseError("restore writer observation is invalid")
    return count


def _empty_template_matches(
    database: Mapping[str, object], credentials: Path
) -> bool:
    names = restore_database_names(database)
    temporary = {**database_mapping(database), "name": names["temporary"]}
    try:
        state = observe_database_state_or_empty(temporary, _credentials_path(credentials))
    except (CommandError, TypeError, ValueError):
        return False
    return state.get("state") == "ready" and state.get("initial_empty") is True


def _oid_present(database: Mapping[str, object], oid: int) -> bool:
    output = _admin_query(
        database,
        "SELECT count(*) FROM pg_catalog.pg_database WHERE oid = :'oid'::oid",
        variables={"oid": str(_positive_oid(oid))},
    ).strip()
    if output not in {b"0", b"1"}:
        raise RestoreDatabaseError("restore database OID absence is unprovable")
    return output == b"1"


def _application_query(
    database: Mapping[str, object], credentials: Path, name: str, sql: str
) -> bytes:
    settings = database_mapping(database)
    return run_command(
        (
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--host",
            str(settings["host"]),
            "--port",
            str(settings["port"]),
            "--username",
            str(settings["role"]),
            "--dbname",
            name,
            "--no-password",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            sql,
        ),
        env={"PGPASSFILE": credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout


def _admin_query(
    database: Mapping[str, object],
    sql: str,
    *,
    variables: Mapping[str, str] | None = None,
) -> bytes:
    settings = database_mapping(database)
    variable_argv = tuple(
        item
        for key, value in sorted((variables or {}).items())
        for item in ("--set", f"{key}={value}")
    )
    return run_command(
        (
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--host",
            "/var/run/postgresql",
            "--port",
            str(settings["port"]),
            "--username",
            "postgres",
            "--dbname",
            "postgres",
            "--no-password",
            "--set",
            "ON_ERROR_STOP=1",
            *variable_argv,
            "--command",
            sql,
        ),
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout


def _credentials_path(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise RestoreDatabaseError("restore credentials path is invalid")
    return value


def _positive_oid(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise RestoreDatabaseError("restore database OID is invalid")
    return value


__all__ = [
    "RestoreDatabaseError",
    "begin_temporary_rebuild",
    "create_temporary_database",
    "drop_registered_temporary",
    "load_registered_temporary",
    "observe_restore_databases",
    "prove_temporary_database_empty",
    "register_restored_database",
    "rename_registered_database",
    "restore_database_names",
    "validate_restore_database_state",
]
