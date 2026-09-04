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


class DatabaseObservationError(ValueError):
    """The selected database does not provide authoritative migration facts."""


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
    """Observe a deploy database where only an absent migration table proves a clean genesis."""

    table = _migration_table(database, credentials)
    if table == b"":
        return {"state": "ready", "applied_migrations": ()}
    if table != b"1":
        raise DatabaseObservationError("database migration authority is ambiguous")
    return _state_from_versions(database, credentials)


def _migration_table(database: Mapping[str, object], credentials: Path) -> bytes:
    return run_command(
        (*_psql_argv(database), "--command", "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'schema_migrations'"),
        env={"PGPASSFILE": credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout.strip()


def _state_from_versions(database: Mapping[str, object], credentials: Path) -> dict[str, object]:
    output = run_command(
        (*_psql_argv(database), "--command", "SELECT version FROM schema_migrations ORDER BY version"),
        env={"PGPASSFILE": credentials.as_posix()},
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    ).stdout.splitlines()
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


__all__ = [
    "DatabaseObservationError",
    "database_mapping",
    "migration_versions",
    "observe_database_migrations",
    "observe_database_state",
    "observe_database_state_or_empty",
    "release_migration_versions",
]
