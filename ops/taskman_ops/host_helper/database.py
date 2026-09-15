"""Validated database facts shared by explicit host procedures."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from .commands import run_command
from ..migrations import validate_migration_versions


_DATABASE_KEYS = frozenset({"host", "port", "role", "name"})
_MIGRATION_FILENAME_RE = re.compile(r"([0-9]{14})_[a-z0-9_]+\.exs\Z")
_MIGRATION_VERSION_RE = re.compile(rb"[0-9]+\Z")
_COMMAND_TIMEOUT_SECONDS = 60.0
_PRISTINE_LANGUAGE_KEYS = frozenset(
    {"name", "trusted", "handler", "inline_handler", "validator", "owner", "acl"}
)
_PRISTINE_LANGUAGES = (
    ("internal", False, None, None, "pg_catalog.fmgr_internal_validator", "postgres", None),
    ("c", False, None, None, "pg_catalog.fmgr_c_validator", "postgres", None),
    ("sql", True, None, None, "pg_catalog.fmgr_sql_validator", "postgres", None),
    ("plpgsql", True, "pg_catalog.plpgsql_call_handler", "pg_catalog.plpgsql_inline_handler", "pg_catalog.plpgsql_validator", "postgres", None),
)


class DatabaseObservationError(ValueError):
    """The selected database does not provide authoritative migration facts."""


def pristine_language_rows_match(rows: object) -> bool:
    """Compare a projected ``pg_language`` catalog against the empty template."""

    if not isinstance(rows, (tuple, list)) or len(rows) != len(_PRISTINE_LANGUAGES):
        return False
    observed: list[tuple[object, ...]] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _PRISTINE_LANGUAGE_KEYS:
            return False
        value = tuple(row[key] for key in ("name", "trusted", "handler", "inline_handler", "validator", "owner", "acl"))
        if type(value[0]) is not str or type(value[1]) is not bool:
            return False
        if any(item is not None and type(item) is not str for item in value[2:]):
            return False
        observed.append(value)
    return tuple(sorted(observed)) == tuple(sorted(_PRISTINE_LANGUAGES))


def _pristine_language_values_sql() -> str:
    """Render the fixed controlled-empty language template without inputs."""

    def sql_text(value: str | None, cast: str = "text") -> str:
        return f"NULL::{cast}" if value is None else "'" + value + "'"

    return ",".join(
        "(" + ",".join(
            (
                sql_text(name),
                "true" if trusted else "false",
                sql_text(handler),
                sql_text(inline_handler),
                sql_text(validator),
                sql_text(owner),
                sql_text(acl, "aclitem[]"),
            )
        ) + ")"
        for name, trusted, handler, inline_handler, validator, owner, acl in _PRISTINE_LANGUAGES
    )


def database_mapping(value: object) -> Mapping[str, object]:
    """Validate the exact connection mapping used in argv-only PostgreSQL calls."""

    if not isinstance(value, Mapping) or set(value) != _DATABASE_KEYS:
        raise ValueError("database settings are invalid")
    if (
        type(value["host"]) is not str
        or not value["host"]
        or type(value["role"]) is not str
        or not value["role"]
        or type(value["name"]) is not str
        or not value["name"]
        or type(value["port"]) is not int
        or not 0 < value["port"] < 65_536
    ):
        raise ValueError("database settings are invalid")
    return value


def migration_versions(value: object) -> tuple[int, ...]:
    """Validate a monotonically ordered sequence of observed migration versions."""

    try:
        return validate_migration_versions(value)
    except ValueError:
        raise ValueError("migration versions are invalid") from None


def release_migration_versions(migrations: object) -> tuple[int, ...]:
    """Read the immutable migration versions encoded by one release record."""

    if not isinstance(migrations, tuple):
        raise ValueError("release migration records are invalid")
    versions: list[int] = []
    for migration in migrations:
        if not isinstance(migration, Mapping):
            raise ValueError("release migration records are invalid")
        filename = migration.get("filename")
        match = _MIGRATION_FILENAME_RE.fullmatch(filename) if type(filename) is str else None
        if match is None:
            raise ValueError("release migration records are invalid")
        versions.append(int(match.group(1)))
    return migration_versions(tuple(versions))


def observe_database_state(database: Mapping[str, object], credentials: Path) -> dict[str, object]:
    """Observe the migration table and versions that make a database authoritative."""

    table = _migration_table(database, credentials)
    if table != b"1":
        raise DatabaseObservationError("database migration authority is unavailable")
    return _state_from_versions(database, credentials)


def observe_database_migrations(database: Mapping[str, object], credentials: Path) -> dict[str, object]:
    """Observe migrations where the selected completed release already proves the table authority."""

    return _state_from_versions(database, credentials)


def observe_database_state_or_empty(database: Mapping[str, object], credentials: Path) -> dict[str, object]:
    """Observe a database where direct empty-schema evidence permits genesis."""

    table = _migration_table(database, credentials)
    if table == b"":
        if _initial_database_empty(database, credentials) != b"1":
            raise DatabaseObservationError("initial database is not empty")
        # Keep this proof internal to the host procedure.  An empty migration
        # table is not equivalent: only this direct catalog proof may skip the
        # null-baseline recovery backup during first-install genesis.
        return {"state": "ready", "applied_migrations": (), "initial_empty": True}
    if table != b"1":
        raise DatabaseObservationError("database migration authority is ambiguous")
    return {**_state_from_versions(database, credentials), "initial_empty": False}


def observe_database_state_or_empty_as_admin(database: Mapping[str, object]) -> dict[str, object]:
    """Read a ready database through its already-validated local cluster.

    This is the narrow missing-pgpass recovery observation.  The caller has
    already proved the cluster, database, and unprivileged role identity with
    the native PostgreSQL authority check; the controller separately proves
    the supplied pgpass can authenticate before any managed write.  No secret
    crosses this admin-only catalog observation.
    """

    table = _migration_table(database, None)
    if table == b"":
        if _initial_database_empty(database, None) != b"1":
            raise DatabaseObservationError("initial database is not empty")
        return {"state": "ready", "applied_migrations": (), "initial_empty": True}
    if table != b"1":
        raise DatabaseObservationError("database migration authority is ambiguous")
    return {**_state_from_versions(database, None), "initial_empty": False}


def _migration_table(database: Mapping[str, object], credentials: Path | None) -> bytes:
    return _database_query(
        database,
        credentials,
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'schema_migrations'",
    ).strip()


def _initial_database_empty(database: Mapping[str, object], credentials: Path | None) -> bytes:
    """Compare every user-extensible catalog to the controlled empty template.

    ``schema_migrations`` is application provenance, not proof that a database
    is new.  In particular, a relation-only inspection would incorrectly
    admit functions, domains, extensions, collations, policies, and full-text
    objects left by another application.  The empty Ubuntu PostgreSQL template
    contains the ``public`` schema and the built-in ``plpgsql`` extension; any
    additional user namespace or user-extensible catalog entry is authority we
    do not own and must refuse.
    """

    return _database_query(
        database,
        credentials,
        "WITH user_namespaces AS ("
            "SELECT oid FROM pg_catalog.pg_namespace "
            "WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'public') "
            "AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp_%'"
            "), unexpected AS ("
            "SELECT 1 FROM pg_catalog.pg_namespace namespace "
            "WHERE namespace.oid IN (SELECT oid FROM user_namespaces) "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_class relation "
            "WHERE relation.relnamespace IN (SELECT oid FROM user_namespaces) "
            "OR (relation.relnamespace = 'public'::pg_catalog.regnamespace "
            "AND relation.relkind IN ('r','p','v','m','S','f')) "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_proc procedure "
            "WHERE procedure.pronamespace IN (SELECT oid FROM user_namespaces) "
            "OR procedure.pronamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_type type "
            "WHERE type.typnamespace IN (SELECT oid FROM user_namespaces) "
            "OR type.typnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_collation collation "
            "WHERE collation.collnamespace IN (SELECT oid FROM user_namespaces) "
            "OR collation.collnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_operator operator "
            "WHERE operator.oprnamespace IN (SELECT oid FROM user_namespaces) "
            "OR operator.oprnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_extension extension "
            "WHERE extension.extname <> 'plpgsql' "
            "OR extension.extversion <> '1.0' "
            "OR extension.extnamespace <> 'pg_catalog'::pg_catalog.regnamespace "
            "OR pg_catalog.pg_get_userbyid(extension.extowner) <> 'postgres' "
            "UNION ALL SELECT 1 FROM (VALUES " + _pristine_language_values_sql() +
            ") AS expected_language(name,trusted,handler,inline_handler,validator,owner,acl) "
            "FULL JOIN pg_catalog.pg_language language ON language.lanname = expected_language.name "
            "WHERE expected_language.name IS NULL OR language.lanname IS NULL "
            "OR language.lanpltrusted IS DISTINCT FROM expected_language.trusted "
            "OR (SELECT namespace.nspname || '.' || procedure.proname FROM pg_catalog.pg_proc procedure JOIN pg_catalog.pg_namespace namespace ON namespace.oid = procedure.pronamespace WHERE procedure.oid = language.lanplcallfoid) IS DISTINCT FROM expected_language.handler "
            "OR (SELECT namespace.nspname || '.' || procedure.proname FROM pg_catalog.pg_proc procedure JOIN pg_catalog.pg_namespace namespace ON namespace.oid = procedure.pronamespace WHERE procedure.oid = language.laninline) IS DISTINCT FROM expected_language.inline_handler "
            "OR (SELECT namespace.nspname || '.' || procedure.proname FROM pg_catalog.pg_proc procedure JOIN pg_catalog.pg_namespace namespace ON namespace.oid = procedure.pronamespace WHERE procedure.oid = language.lanvalidator) IS DISTINCT FROM expected_language.validator "
            "OR pg_catalog.pg_get_userbyid(language.lanowner) IS DISTINCT FROM expected_language.owner "
            "OR language.lanacl IS DISTINCT FROM expected_language.acl "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_event_trigger "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_default_acl "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_largeobject_metadata "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_foreign_server "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_user_mapping "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_publication "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_subscription "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_conversion conversion "
            "WHERE conversion.connamespace IN (SELECT oid FROM user_namespaces) "
            "OR conversion.connamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_opclass opclass "
            "WHERE opclass.opcnamespace IN (SELECT oid FROM user_namespaces) "
            "OR opclass.opcnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_opfamily opfamily "
            "WHERE opfamily.opfnamespace IN (SELECT oid FROM user_namespaces) "
            "OR opfamily.opfnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_ts_config tsconfig "
            "WHERE tsconfig.cfgnamespace IN (SELECT oid FROM user_namespaces) "
            "OR tsconfig.cfgnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_ts_dict tsdict "
            "WHERE tsdict.dictnamespace IN (SELECT oid FROM user_namespaces) "
            "OR tsdict.dictnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_ts_parser tsparser "
            "WHERE tsparser.prsnamespace IN (SELECT oid FROM user_namespaces) "
            "OR tsparser.prsnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_ts_template tstemplate "
            "WHERE tstemplate.tmplnamespace IN (SELECT oid FROM user_namespaces) "
            "OR tstemplate.tmplnamespace = 'public'::pg_catalog.regnamespace "
            "UNION ALL SELECT 1 FROM pg_catalog.pg_namespace public_schema "
            "WHERE public_schema.nspname = 'public' AND ("
            "public_schema.nspowner <> (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'pg_database_owner') "
            "OR public_schema.nspacl IS DISTINCT FROM ARRAY['pg_database_owner=UC/pg_database_owner','=U/pg_database_owner']::aclitem[])"
            ") SELECT CASE WHEN EXISTS (SELECT 1 FROM unexpected) THEN 0 ELSE 1 END",
    ).strip()


def _state_from_versions(database: Mapping[str, object], credentials: Path | None) -> dict[str, object]:
    output = _database_query(
        database, credentials, "SELECT version FROM schema_migrations ORDER BY version"
    ).splitlines()
    try:
        if any(item and _MIGRATION_VERSION_RE.fullmatch(item) is None for item in output):
            raise ValueError("migration evidence is not decimal")
        versions = migration_versions(tuple(int(item) for item in output if item))
    except ValueError as error:
        raise DatabaseObservationError("database migration versions are invalid") from error
    return {"state": "ready", "applied_migrations": versions}


def _psql_argv(database: Mapping[str, object]) -> tuple[str, ...]:
    return (
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--host",
        str(database["host"]),
        "--port",
        str(database["port"]),
        "--username",
        str(database["role"]),
        "--dbname",
        str(database["name"]),
        "--no-password",
    )


def _database_query(
    database: Mapping[str, object], credentials: Path | None, command: str
) -> bytes:
    if credentials is None:
        argv = (
            "runuser", "-u", "postgres", "--", "psql", "--no-psqlrc",
            "--tuples-only", "--no-align", "--host", "/var/run/postgresql",
            "--port", str(database["port"]), "--username", "postgres",
            "--dbname", str(database["name"]), "--no-password", "--command", command,
        )
        env = None
    else:
        argv = (*_psql_argv(database), "--command", command)
        env = {"PGPASSFILE": credentials.as_posix()}
    return run_command(argv, env=env, timeout_seconds=_COMMAND_TIMEOUT_SECONDS).stdout


__all__ = [
    "DatabaseObservationError",
    "database_mapping",
    "migration_versions",
    "observe_database_migrations",
    "observe_database_state",
    "observe_database_state_or_empty",
    "observe_database_state_or_empty_as_admin",
    "pristine_language_rows_match",
    "release_migration_versions",
]
