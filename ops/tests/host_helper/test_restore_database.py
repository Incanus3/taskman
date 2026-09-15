"""Restore-specific PostgreSQL observation and bounded identity mechanics."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

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


def test_observation_distinguishes_absent_table_missing_table_empty_and_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collapsing a missing table into [] would claim migration evidence that was not observed."""

    import taskman_ops.host_helper.restore_database as restore_database

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        sql = argv[-1]
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


def test_observation_preserves_a_present_but_empty_migration_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty table is authoritative and must remain distinct from a missing table."""

    import taskman_ops.host_helper.restore_database as restore_database

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        sql = argv[-1]
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


def test_dump_load_requires_a_durably_registered_matching_oid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dump bytes must never load while creation intent is still pending."""

    import taskman_ops.host_helper.restore_database as restore_database

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
            _target(), DATABASE, Path("/etc/taskman/pgpass"), Path("/safe/source.dump")
        )
    assert called == []

    restore_database.load_registered_temporary(
        _target(restored_database_oid=202, temporary_creation_pending=False),
        DATABASE,
        Path("/etc/taskman/pgpass"),
        Path("/safe/source.dump"),
    )

    assert called[0][0] == "pg_restore"
    assert "taskman__restore_tmp" in called[0]


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
    calls: list[tuple[str, ...]] = []
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
        lambda argv, **_kwargs: calls.append(argv)
        or _completed(
            argv,
            b"0\n"
            if "SELECT count(*) FROM pg_catalog.pg_database" in argv[-1]
            else b"",
        ),
    )

    restore_database.drop_registered_temporary(
        DATABASE, Path("/etc/taskman/pgpass"), pending.restored_database_oid
    )

    assert pending.temporary_creation_pending is True
    assert pending.restored_database_oid == 202
    assert calls[0][0] == "runuser"
    assert calls[0][-1] == 'DROP DATABASE "taskman__restore_tmp" WITH (FORCE)'


def test_rename_requires_the_exact_registered_oid_and_absent_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching name must never authorize promoting a replacement OID."""

    import taskman_ops.host_helper.restore_database as restore_database

    called: list[tuple[str, ...]] = []
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
        lambda argv, **_kwargs: called.append(argv) or _completed(argv, b""),
    )

    with pytest.raises(restore_database.RestoreDatabaseError):
        restore_database.rename_registered_database(
            DATABASE, Path("/etc/taskman/pgpass"), "temporary", "canonical", 999
        )
    assert called == []

    restore_database.rename_registered_database(
        DATABASE, Path("/etc/taskman/pgpass"), "temporary", "canonical", 202
    )
    assert called[0][-1] == (
        'ALTER DATABASE "taskman__restore_tmp" RENAME TO "taskman"'
    )
