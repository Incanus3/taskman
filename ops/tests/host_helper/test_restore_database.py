"""Restore-specific PostgreSQL observation and bounded identity mechanics."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from tests.host_helper.support import managed_paths

from taskman_ops.host_helper.commands import CommandError
from taskman_ops.host_helper.restore_target import (
    RestoreTarget,
    write_restore_target,
)
from taskman_ops.releases.identifiers import build_release_id


BACKUP = "backup-" + "a" * 32
SAFETY = "backup-" + "b" * 32
RELEASE = build_release_id(
    "0.2.0", "c" * 40, artifact_sha256="d" * 64, source_dirty=False
)
DATABASE = {
    "host": "127.0.0.1",
    "port": 5432,
    "role": "taskman",
    "name": "taskman",
}


def _target(**changes: object) -> RestoreTarget:
    values: dict[str, object] = {
        "schema_version": 1,
        "backup_id": BACKUP,
        "dump_sha256": "e" * 64,
        "source_release_id": RELEASE,
        "base_selection_id": None,
        "observed_previous_release_id": None,
        "original_database_oid": 101,
        "restored_database_oid": None,
        "temporary_creation_pending": True,
        "safety_backup_id": SAFETY,
        "replacement": None,
        "safety_backup_attempts": ({"backup_id": SAFETY, "attempt_number": 0},),
    }
    values.update(changes)
    return RestoreTarget(**values)  # type: ignore[arg-type]


def _completed(argv: tuple[str, ...], stdout: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv, 0, stdout, b"")


@pytest.mark.parametrize("available", (40 * 1024**3, 8 * 1024**3))
def test_database_capacity_uses_postgres_data_directory_filesystem(
    available: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import taskman_ops.host_helper.restore_database as restore_database

    calls: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return _completed(argv, f"{available}\n".encode())

    monkeypatch.setattr(restore_database, "run_command", run)

    assert restore_database.observe_database_available_bytes(DATABASE) == available
    assert calls[0][:2] == ("sh", "-ceu")
    assert "SHOW data_directory" in calls[0][2]
    assert 'df -B1 --output=avail "$data_directory"' in calls[0][2]


@pytest.mark.parametrize("stdout", (b"", b"unknown\n", b"0\n"))
def test_database_capacity_refuses_unobservable_values(
    stdout: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    import taskman_ops.host_helper.restore_database as restore_database

    monkeypatch.setattr(
        restore_database,
        "run_command",
        lambda argv, **_kwargs: _completed(argv, stdout),
    )

    with pytest.raises(restore_database.RestoreDatabaseError):
        restore_database.observe_database_available_bytes(DATABASE)


def test_admin_query_feeds_quoted_variables_to_psql_file_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quoted database OIDs require psql parsing before the server sees SQL."""

    import taskman_ops.host_helper.restore_database as restore_database

    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return _completed(argv, b"1\n")

    monkeypatch.setattr(restore_database, "run_command", run)

    assert restore_database._admin_query(
        DATABASE, "SELECT :'oid'::oid", variables={"oid": "202"}
    ) == b"1\n"

    argv, kwargs = calls[0]
    assert "--set" in argv
    assert "oid=202" in argv
    assert "--file=-" in argv
    assert "--command" not in argv
    assert kwargs["stdin"] == b"SELECT :'oid'::oid\n"


@pytest.mark.parametrize("count", (b"1\n", b"2\n", b"unknown\n"))
def test_recorded_discard_oid_must_be_proved_absent_cluster_wide(
    count: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    import taskman_ops.host_helper.restore_database as restore_database

    monkeypatch.setattr(
        restore_database,
        "run_command",
        lambda argv, **_kwargs: _completed(argv, count),
    )

    with pytest.raises(
        restore_database.RestoreDatabaseError,
        match="replacement discard database OID",
    ):
        restore_database.prove_database_oid_absent(DATABASE, 202)


def test_observation_distinguishes_absent_table_missing_table_empty_and_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collapsing a missing table into [] would claim migration evidence that was not observed."""

    import taskman_ops.host_helper.restore_database as restore_database

    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        sql = kwargs.get("stdin", argv[-1])
        assert isinstance(sql, bytes | str)
        if isinstance(sql, bytes):
            sql = sql.decode("utf-8")
        if "FROM pg_catalog.pg_database" in sql:
            return _completed(
                argv,
                b"taskman\t101\ttaskman\n"
                b"taskman__restore_tmp\t202\ttaskman\n",
            )
        name = argv[argv.index("--dbname") + 1]
        if "information_schema.tables" in sql:
            return _completed(argv, b"0\n" if name.endswith("__restore_tmp") else b"1\n")
        if "SELECT version FROM schema_migrations" in sql:
            return _completed(argv, b"20260901000000\n20260902000000\n")
        raise AssertionError(sql)

    monkeypatch.setattr(restore_database, "run_command", run)

    assert restore_database.observe_restore_databases(DATABASE, Path("/etc/taskman/pgpass")) == {
        "canonical": {
            "oid": 101,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": (20260901000000, 20260902000000),
        },
        "temporary": {
            "oid": 202,
            "owner": "taskman",
            "migration_table_present": False,
            "applied_migrations": None,
        },
        "retired": None,
    }
    canonical_call = next(
        (argv, kwargs)
        for argv, kwargs in calls
        if argv[0] == "psql" and argv[argv.index("--dbname") + 1] == "taskman"
    )
    temporary_call = next(
        (argv, kwargs)
        for argv, kwargs in calls
        if argv[0] == "runuser" and argv[argv.index("--dbname") + 1] == "taskman__restore_tmp"
    )
    assert canonical_call[1]["env"] == {"PGPASSFILE": "/etc/taskman/pgpass"}
    assert temporary_call[0][temporary_call[0].index("--host") + 1] == "/var/run/postgresql"
    assert "env" not in temporary_call[1]


def test_observation_preserves_a_present_but_empty_migration_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty table is authoritative and must remain distinct from a missing table."""

    import taskman_ops.host_helper.restore_database as restore_database

    def run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        sql = kwargs.get("stdin", argv[-1])
        assert isinstance(sql, bytes | str)
        if isinstance(sql, bytes):
            sql = sql.decode("utf-8")
        if "FROM pg_catalog.pg_database" in sql:
            return _completed(argv, b"taskman\t101\ttaskman\n")
        if "information_schema.tables" in sql:
            return _completed(argv, b"1\n")
        return _completed(argv, b"")

    monkeypatch.setattr(restore_database, "run_command", run)

    observed = restore_database.observe_restore_databases(
        DATABASE, Path("/etc/taskman/pgpass")
    )

    assert observed["canonical"]["migration_table_present"] is True  # type: ignore[index]
    assert observed["canonical"]["applied_migrations"] == ()  # type: ignore[index]


def test_failed_database_observation_never_becomes_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed catalog query must refuse instead of returning three null databases."""

    import taskman_ops.host_helper.restore_database as restore_database

    monkeypatch.setattr(
        restore_database,
        "run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CommandError("failed")),
    )

    with pytest.raises(restore_database.RestoreDatabaseError):
        restore_database.observe_restore_databases(
            DATABASE, Path("/etc/taskman/pgpass")
        )


def test_registers_only_a_proved_empty_expected_owner_temporary_and_fsyncs_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registering by name alone could adopt a populated foreign database before loading."""

    import taskman_ops.host_helper.restore_database as restore_database
    from taskman_ops.host_helper.state import _read_restore_target

    paths = managed_paths(tmp_path)
    pending = _target()
    write_restore_target(paths, pending)
    observed = {
        "canonical": {
            "oid": 101,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": (20260901000000,),
        },
        "temporary": {
            "oid": 202,
            "owner": "taskman",
            "migration_table_present": False,
            "applied_migrations": None,
        },
        "retired": None,
    }
    monkeypatch.setattr(
        restore_database, "observe_restore_databases", lambda *_args: observed
    )
    monkeypatch.setattr(
        restore_database, "prove_temporary_database_empty", lambda *_args: 202
    )

    registered = restore_database.register_restored_database(
        paths, pending, DATABASE, Path("/etc/taskman/pgpass")
    )

    assert registered.restored_database_oid == 202
    assert registered.temporary_creation_pending is False
    assert _read_restore_target(
        Path(paths.local(paths.restore_target_path)), os.geteuid()
    ) == registered


@pytest.mark.parametrize("owner", ("postgres", "foreign"))
def test_empty_temporary_proof_refuses_wrong_owner_or_active_writers(
    monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    """A schema-empty database is not adoptable when ownership/writer authority is wrong."""

    import taskman_ops.host_helper.restore_database as restore_database

    observed = {
        "canonical": None,
        "temporary": {
            "oid": 202,
            "owner": owner,
            "migration_table_present": False,
            "applied_migrations": None,
        },
        "retired": {
            "oid": 101,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": (20260901000000,),
        },
    }
    monkeypatch.setattr(
        restore_database, "observe_restore_databases", lambda *_args: observed
    )
    monkeypatch.setattr(
        restore_database,
        "_active_writer_count",
        lambda *_args: 1 if owner == "postgres" else 0,
    )
    monkeypatch.setattr(restore_database, "_empty_template_matches", lambda *_args: True)

    with pytest.raises(restore_database.RestoreDatabaseError):
        restore_database.prove_temporary_database_empty(
            DATABASE, Path("/etc/taskman/pgpass")
        )


def test_empty_temporary_proof_uses_the_existing_admin_pristine_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The derived temporary has no canonical pgpass admission path."""

    import taskman_ops.host_helper.restore_database as restore_database

    observed_database: list[dict[str, object]] = []
    monkeypatch.setattr(
        restore_database,
        "observe_restore_databases",
        lambda *_args: {
            "canonical": {"oid": 101, "owner": "taskman", "migration_table_present": True, "applied_migrations": ()},
            "temporary": {"oid": 202, "owner": "taskman", "migration_table_present": False, "applied_migrations": None},
            "retired": None,
        },
    )
    monkeypatch.setattr(restore_database, "_active_writer_count", lambda *_args: 0)
    monkeypatch.setattr(
        restore_database,
        "observe_database_state_or_empty_as_admin",
        lambda database: observed_database.append(database) or {"state": "ready", "applied_migrations": (), "initial_empty": True},
    )

    assert restore_database.prove_temporary_database_empty(
        DATABASE, Path("/unusable/pgpass")
    ) == 202
    assert observed_database == [{**DATABASE, "name": "taskman__restore_tmp"}]


def test_dump_load_requires_a_durably_registered_matching_oid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dump bytes must never load while creation intent is still pending."""

    import taskman_ops.host_helper.restore_database as restore_database

    dump = tmp_path / "source.dump"
    dump.write_bytes(b"PGDMP")
    dump.chmod(0o600)
    called: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        restore_database,
        "observe_restore_databases",
        lambda *_args: {
            "canonical": {"oid": 101, "owner": "taskman", "migration_table_present": True, "applied_migrations": ()},
            "temporary": {"oid": 202, "owner": "taskman", "migration_table_present": False, "applied_migrations": None},
            "retired": None,
        },
    )
    monkeypatch.setattr(
        restore_database,
        "run_command",
        lambda argv, **_kwargs: called.append(argv) or _completed(argv, b""),
    )

    with pytest.raises(restore_database.RestoreDatabaseError):
        restore_database.load_registered_temporary(
            _target(), DATABASE, Path("/etc/taskman/pgpass"), dump
        )
    assert called == []

    restore_database.load_registered_temporary(
        _target(
            dump_sha256=__import__("hashlib").sha256(dump.read_bytes()).hexdigest(),
            restored_database_oid=202,
            temporary_creation_pending=False,
        ),
        DATABASE,
        Path("/etc/taskman/pgpass"),
        dump,
    )

    assert called[0] == (
        "sh",
        "-ceu",
        'dump=$1; shift; exec "$@" < "$dump"',
        "taskman-restore-input",
        dump.as_posix(),
        "runuser",
        "-u",
        "postgres",
        "--",
        "pg_restore",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "--host",
        "/var/run/postgresql",
        "--port",
        "5432",
        "--username",
        "postgres",
        "--dbname",
        "taskman__restore_tmp",
        "--no-password",
        "--role=taskman",
    )


def test_registered_load_executes_native_dump_as_postgres_with_application_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real custom dump loads through FD0, with postgres session and app ownership."""

    container = os.environ.get("TASKMAN_TEST_POSTGRES_CONTAINER")
    if container is None:
        pytest.skip("set TASKMAN_TEST_POSTGRES_CONTAINER to run the native restore load proof")

    import taskman_ops.host_helper.restore_database as restore_database

    suffix = uuid4().hex
    role = f"taskman_restore_{suffix}"
    source_name = f"restore_source_{suffix}"
    database_name = f"restore_target_{suffix}"
    database = {"host": "127.0.0.1", "port": 5432, "role": role, "name": database_name}
    dump = tmp_path / "source.dump"
    bridge = tmp_path / "bin" / "runuser"
    bridge.parent.mkdir()
    bridge.write_text(
        "#!/bin/sh\nset -eu\n[ \"$1\" = \"-u\" ] && [ \"$2\" = \"postgres\" ] && [ \"$3\" = \"--\" ]\nshift 3\nexec docker exec -i "
        + container
        + " \"$@\"\n",
        encoding="utf-8",
    )
    bridge.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bridge.parent}:{os.environ['PATH']}")

    def psql(sql: str, database_name: str = "postgres") -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ("docker", "exec", "-i", container, "psql", "--no-psqlrc", "--tuples-only", "--no-align", "--username", "postgres", "--dbname", database_name, "--set", "ON_ERROR_STOP=1", "--command", sql),
            capture_output=True,
            check=False,
            timeout=60,
        )

    try:
        assert psql(f'CREATE ROLE "{role}" LOGIN').returncode == 0
        assert psql(f'CREATE DATABASE "{source_name}" OWNER "{role}"').returncode == 0
        assert psql(f'CREATE DATABASE "{database_name}__restore_tmp" OWNER "{role}"').returncode == 0
        assert psql(
            f'SET ROLE "{role}"; '
            "CREATE TABLE public.restore_probe (id integer PRIMARY KEY, "
            f"CHECK (current_user = '{role}' AND session_user = 'postgres')); "
            "INSERT INTO public.restore_probe VALUES (7)",
            source_name,
        ).returncode == 0
        created_dump = subprocess.run(
            ("docker", "exec", "-i", container, "pg_dump", "--format=custom", "--username", "postgres", "--dbname", source_name),
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert created_dump.returncode == 0, created_dump.stderr.decode("utf-8", "replace")
        dump.write_bytes(created_dump.stdout)
        dump.chmod(0o600)
        mode_before = stat.S_IMODE(dump.stat().st_mode)

        oid_result = psql(
            f"SELECT oid FROM pg_catalog.pg_database WHERE datname = '{database_name}__restore_tmp'"
        )
        assert oid_result.returncode == 0, oid_result.stderr.decode("utf-8", "replace")
        temporary_oid = int(oid_result.stdout)

        restore_database.load_registered_temporary(
            _target(dump_sha256=__import__("hashlib").sha256(dump.read_bytes()).hexdigest(), restored_database_oid=temporary_oid, temporary_creation_pending=False),
            database,
            Path("/unused/pgpass"),
            dump,
        )

        result = psql(
            f"SET ROLE \"{role}\"; SELECT current_user, session_user; RESET ROLE; "
            "SELECT (SELECT tableowner FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'restore_probe'), "
            "(SELECT count(*) FROM public.restore_probe)",
            f"{database_name}__restore_tmp",
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        assert result.stdout.decode().splitlines()[-3:] == [
            f"{role}|postgres",
            "RESET",
            f"{role}|1",
        ]
        assert stat.S_IMODE(dump.stat().st_mode) == mode_before == 0o600
    finally:
        for name in (f"{database_name}__restore_tmp", source_name):
            psql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        psql(f'DROP ROLE IF EXISTS "{role}"')


@pytest.mark.parametrize(
    "entry",
    (
        {"oid": 0, "owner": "taskman", "migration_table_present": False, "applied_migrations": None},
        {"oid": 101, "owner": "taskman", "migration_table_present": False, "applied_migrations": ()},
        {"oid": 101, "owner": "taskman", "migration_table_present": True, "applied_migrations": None},
        {"oid": 101, "owner": "taskman", "migration_table_present": 1, "applied_migrations": ()},
    ),
)
def test_restore_database_state_rejects_contradictory_conditional_shapes(
    entry: dict[str, object],
) -> None:
    """Malformed absence/table evidence must not cross the helper boundary."""

    import taskman_ops.host_helper.restore_database as restore_database

    with pytest.raises(restore_database.RestoreDatabaseError):
        restore_database.validate_restore_database_state(
            {"canonical": entry, "temporary": None, "retired": None}
        )


def test_rebuild_intent_is_durable_before_exact_registered_temporary_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping first would erase the old OID needed to classify an interrupted rebuild."""

    import taskman_ops.host_helper.restore_database as restore_database

    paths = managed_paths(tmp_path)
    registered = _target(restored_database_oid=202, temporary_creation_pending=False)
    write_restore_target(paths, registered)
    pending = restore_database.begin_temporary_rebuild(paths, registered)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        restore_database,
        "observe_restore_databases",
        lambda *_args: {
            "canonical": {"oid": 101, "owner": "taskman", "migration_table_present": True, "applied_migrations": ()},
            "temporary": {"oid": 202, "owner": "taskman", "migration_table_present": True, "applied_migrations": ()},
            "retired": None,
        },
    )
    monkeypatch.setattr(
        restore_database,
        "run_command",
        lambda argv, **kwargs: calls.append((argv, kwargs))
        or _completed(
            argv,
            b"0\n"
            if "SELECT count(*) FROM pg_catalog.pg_database" in kwargs.get("stdin", b"").decode("utf-8")
            else b"",
        ),
    )

    restore_database.drop_registered_temporary(
        DATABASE, Path("/etc/taskman/pgpass"), pending.restored_database_oid
    )

    assert pending.temporary_creation_pending is True
    assert pending.restored_database_oid == 202
    assert calls[0][0][0] == "runuser"
    assert calls[0][1]["stdin"] == b'DROP DATABASE "taskman__restore_tmp" WITH (FORCE)\n'


def test_rename_requires_the_exact_registered_oid_and_absent_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching name must never authorize promoting a replacement OID."""

    import taskman_ops.host_helper.restore_database as restore_database

    called: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        restore_database,
        "observe_restore_databases",
        lambda *_args: {
            "canonical": None,
            "temporary": {"oid": 202, "owner": "taskman", "migration_table_present": True, "applied_migrations": ()},
            "retired": {"oid": 101, "owner": "taskman", "migration_table_present": True, "applied_migrations": ()},
        },
    )
    monkeypatch.setattr(
        restore_database,
        "run_command",
        lambda argv, **kwargs: called.append((argv, kwargs)) or _completed(argv, b""),
    )

    with pytest.raises(restore_database.RestoreDatabaseError):
        restore_database.rename_registered_database(
            DATABASE, Path("/etc/taskman/pgpass"), "temporary", "canonical", 999
        )
    assert called == []

    restore_database.rename_registered_database(
        DATABASE, Path("/etc/taskman/pgpass"), "temporary", "canonical", 202
    )
    assert called[0][1]["stdin"] == b'ALTER DATABASE "taskman__restore_tmp" RENAME TO "taskman"\n'


def test_replacement_drops_exact_restored_canonical_only_with_retired_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical deletion is safe only while the bound original remains retired."""

    import taskman_ops.host_helper.restore_database as restore_database

    called: list[tuple[tuple[str, ...], dict[str, object]]] = []
    observed = {
        "canonical": {
            "oid": 202,
            "owner": "taskman",
            "migration_table_present": True,
            "applied_migrations": (),
        },
        "temporary": None,
        "retired": None,
    }
    monkeypatch.setattr(
        restore_database,
        "observe_restore_databases",
        lambda *_args: observed,
    )
    monkeypatch.setattr(restore_database, "_oid_present", lambda *_args: False)
    monkeypatch.setattr(
        restore_database,
        "run_command",
        lambda argv, **kwargs: called.append((argv, kwargs)) or _completed(argv, b""),
    )

    with pytest.raises(
        restore_database.RestoreDatabaseError,
        match="preserved original",
    ):
        restore_database.drop_registered_restored(
            DATABASE, Path("/etc/taskman/pgpass"), "canonical", 202
        )
    assert called == []

    observed["retired"] = {
        "oid": 101,
        "owner": "taskman",
        "migration_table_present": True,
        "applied_migrations": (),
    }
    restore_database.drop_registered_restored(
        DATABASE, Path("/etc/taskman/pgpass"), "canonical", 202
    )

    assert called[0][1]["stdin"] == b'DROP DATABASE "taskman" WITH (FORCE)\n'
