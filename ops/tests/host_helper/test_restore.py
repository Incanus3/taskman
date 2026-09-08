"""Observed-state restore convergence contracts."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import stat
import subprocess

import pytest

from taskman_ops.host_helper.operations import restore as restore_module
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.releases.identifiers import build_release_id


CORRELATION = "op-0123456789abcdef0123456789abcdef"
TARGET_REVISION = "a" * 40
CURRENT_REVISION = "b" * 40
TARGET = build_release_id("0.2.0", TARGET_REVISION)
CURRENT = build_release_id("0.2.1", CURRENT_REVISION)
BACKUP = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MIGRATION = {"filename": "20260905120000_create_tasks.exs", "sha256": "c" * 64}
MIGRATION_VERSION = 20260905120000
CURRENT_MIGRATION = {"filename": "20260906120000_add_task_notes.exs", "sha256": "e" * 64}
CURRENT_MIGRATION_VERSION = 20260906120000
DATABASE = "taskman"
TEMPORARY = f"{DATABASE}__restore_tmp"
RETIRED = f"{DATABASE}__restore_old"


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths.from_mapping(
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}
    )


def _database(name: str = DATABASE) -> dict[str, object]:
    return {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": name}


def _verification() -> dict[str, object]:
    return {
        "application_port": 4000,
        "distribution_port": 6789,
        "database_port": 5432,
        "public_hostname": "taskman.example.test",
        "public_ipv4": "203.0.113.10",
        "public_ipv6": None,
        "ssh_port": 22,
        "ssh_user": "deployer",
        "readiness_timeout": 1,
        "connection_timeout": 1,
    }


def _credentials(tmp_path: Path) -> Path:
    path = tmp_path / "pgpass"
    path.write_text("127.0.0.1:5432:*:taskman:database-password-canary\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _install_release(
    paths: ManagedPaths,
    release_id: str,
    revision: str,
    migrations: tuple[dict[str, str], ...] = (MIGRATION,),
) -> None:
    path = Path(paths.local(paths.release_root / release_id))
    path.mkdir(parents=True)
    path.chmod(0o750)
    write_release_manifest(paths, ReleaseRecord(release_id, revision, "d" * 64, migrations))


def _seed_state(
    paths: ManagedPaths,
    current_migrations: tuple[dict[str, str], ...] = (MIGRATION,),
) -> None:
    _install_release(paths, TARGET, TARGET_REVISION)
    _install_release(paths, CURRENT, CURRENT_REVISION, current_migrations)
    append_selection(paths, SelectionRecord(TARGET, None, None, datetime(2026, 9, 7, 11, 0, tzinfo=UTC)))
    append_selection(paths, SelectionRecord(CURRENT, TARGET, None, datetime(2026, 9, 7, 12, 0, tzinfo=UTC)))
    Path(paths.local(paths.current_link)).symlink_to(Path(paths.local(paths.release_root / CURRENT)))
    _write_source_backup(paths, TARGET, (MIGRATION_VERSION,))


def _write_source_backup(
    paths: ManagedPaths, source_release_id: str, migration_versions: tuple[int, ...]
) -> None:
    dump = Path(paths.local(paths.backup_root / f"{BACKUP}.dump"))
    dump.write_bytes(b"validated source dump")
    dump.chmod(0o600)
    manifest = Path(paths.local(paths.backup_root / f"{BACKUP}.json"))
    if manifest.exists():
        manifest.unlink()
    write_backup_manifest(
        paths,
        BackupRecord(
            BACKUP,
            datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            hashlib.sha256(dump.read_bytes()).hexdigest(),
            source_release_id,
            migration_versions,
            1024,
        ),
    )


def _request(paths: ManagedPaths, credentials: Path) -> HostRequest:
    return HostRequest(
        2,
        "restore",
        CORRELATION,
        {"selected_release_id": CURRENT, "backup_id": BACKUP},
        {"install_root": paths.install_root.as_posix(), "backup_root": paths.backup_root.as_posix()},
        {
            "backup_id": BACKUP,
            "credentials_path": credentials.as_posix(),
            "database": _database(),
            "verification": _verification(),
        },
    )


class _Runtime:
    def __init__(self, databases: set[str] | None = None, *, fail_start: bool = False) -> None:
        self.databases = {DATABASE} if databases is None else set(databases)
        self.events: list[str] = []
        self.backups = 0
        self.fail_start = fail_start

    def observe_database(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"state": "ready", "applied_migrations": (MIGRATION_VERSION,)}

    def backup(self, state: object, paths: ManagedPaths, database: object, *_args: object, **_kwargs: object) -> BackupRecord:
        self.events.append(f"backup:{database['name']}")
        self.backups += 1
        backup_id = f"backup-{self.backups:032x}"
        dump = Path(paths.local(paths.backup_root / f"{backup_id}.dump"))
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_bytes(b"fresh restore safety backup")
        dump.chmod(0o600)
        record = BackupRecord(
            backup_id,
            datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            hashlib.sha256(dump.read_bytes()).hexdigest(),
            state.selected_release_id,
            state.applied_migrations,
            1024,
        )
        write_backup_manifest(paths, record)
        return record

    def command(self, argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        rendered = " ".join(argv)
        if argv[0] == "pg_restore" and "--list" in argv:
            self.events.append("source-validation")
        elif argv[0] == "pg_restore":
            self.events.append("restore")
        elif argv[:2] == ("systemctl", "stop"):
            self.events.append("stop")
        elif argv[:2] == ("systemctl", "start"):
            self.events.append("start")
            if self.fail_start:
                raise restore_module.CommandError("start interrupted")
        elif "psql" in argv:
            command = argv[argv.index("--command") + 1]
            if "FROM pg_database" in command:
                return subprocess.CompletedProcess(argv, 0, ("\n".join(sorted(self.databases)) + "\n").encode(), b"")
            if command.startswith("CREATE DATABASE"):
                self.databases.add(TEMPORARY)
            elif f'ALTER DATABASE "{DATABASE}" RENAME TO "{RETIRED}"' in command:
                self.databases.remove(DATABASE)
                self.databases.add(RETIRED)
            elif f'ALTER DATABASE "{TEMPORARY}" RENAME TO "{DATABASE}"' in command:
                self.databases.remove(TEMPORARY)
                self.databases.add(DATABASE)
            elif f'DROP DATABASE "{RETIRED}"' in command:
                self.databases.remove(RETIRED)
            elif f'DROP DATABASE "{TEMPORARY}"' in command:
                self.databases.remove(TEMPORARY)
            elif "schema_migrations" in command and "SELECT 1" in command:
                return subprocess.CompletedProcess(argv, 0, b"1\n", b"")
            elif "SELECT version FROM schema_migrations" in command:
                return subprocess.CompletedProcess(argv, 0, f"{MIGRATION_VERSION}\n".encode(), b"")
        assert CORRELATION not in rendered
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def verify(self, request: HostRequest, **_kwargs: object) -> HostResult:
        self.events.append("verify")
        return HostResult(2, "verify", request.correlation_id, "succeeded", "verified", {"report": {"ok": True}}, ())


class _DifferentSchemaRuntime(_Runtime):
    """Database facts for restoring an older backup over a newer current database."""

    def __init__(self) -> None:
        super().__init__()
        self.schemas = {DATABASE: (MIGRATION_VERSION, CURRENT_MIGRATION_VERSION)}
        self.created_backups: list[BackupRecord] = []

    def observe_database(self, database: object, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"state": "ready", "applied_migrations": self.schemas[database["name"]]}

    def backup(self, *args: object, **kwargs: object) -> BackupRecord:
        record = super().backup(*args, **kwargs)
        self.created_backups.append(record)
        return record

    def command(self, argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        completed = super().command(argv, **kwargs)
        if argv[0] == "pg_restore" and "--list" not in argv:
            self.schemas[argv[argv.index("--dbname") + 1]] = (MIGRATION_VERSION,)
        elif "psql" in argv:
            command = argv[argv.index("--command") + 1]
            if command.startswith("CREATE DATABASE"):
                self.schemas[TEMPORARY] = ()
            elif f'ALTER DATABASE "{DATABASE}" RENAME TO "{RETIRED}"' in command:
                self.schemas[RETIRED] = self.schemas.pop(DATABASE)
            elif f'ALTER DATABASE "{TEMPORARY}" RENAME TO "{DATABASE}"' in command:
                self.schemas[DATABASE] = self.schemas.pop(TEMPORARY)
            elif f'DROP DATABASE "{RETIRED}"' in command:
                self.schemas.pop(RETIRED)
        return completed


class _IncompleteTemporaryRuntime(_Runtime):
    """A recognized temporary database left incomplete by an interrupted restore."""

    def __init__(self) -> None:
        super().__init__({DATABASE, TEMPORARY})
        self.temporary_valid = False

    def command(self, argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if (
            "psql" in argv
            and "--dbname" in argv
            and argv[argv.index("--dbname") + 1] == TEMPORARY
            and "schema_migrations" in argv[argv.index("--command") + 1]
            and "SELECT 1" in argv[argv.index("--command") + 1]
        ):
            table = b"1\n" if self.temporary_valid else b""
            return subprocess.CompletedProcess(argv, 0, table, b"")
        completed = super().command(argv, **kwargs)
        if argv[0] == "pg_restore" and "--list" not in argv:
            self.temporary_valid = True
        return completed


def _install_runtime(monkeypatch: pytest.MonkeyPatch, runtime: _Runtime) -> None:
    monkeypatch.setattr(restore_module, "_observe_database", runtime.observe_database, raising=False)
    monkeypatch.setattr(restore_module, "create_validated_backup", runtime.backup, raising=False)
    monkeypatch.setattr(restore_module, "run_command", runtime.command, raising=False)
    monkeypatch.setattr(restore_module, "verify", runtime.verify, raising=False)
    monkeypatch.setattr(restore_module, "available_bytes", lambda _path: 10_000, raising=False)


def test_restore_validates_source_and_converges_through_a_deterministic_temporary_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source dump, safety backup, and temporary database are ordered safety boundaries."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == TARGET
    assert result.state["database_state"] == "restored"
    assert result.state["backup_id"] == BACKUP
    assert result.state["pre_restore_backup_id"] == "backup-00000000000000000000000000000001"
    assert runtime.events.index("source-validation") < runtime.events.index(f"backup:{DATABASE}") < runtime.events.index("restore")
    assert runtime.databases == {DATABASE}
    assert Path(paths.local(paths.current_link)).resolve().name == TARGET


def test_restore_repairs_a_safely_owned_group_or_other_readable_source_dump_before_using_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Using a readable backup dump would expose the restored production data."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    dump = Path(paths.local(paths.backup_root / f"{BACKUP}.dump"))
    dump.chmod(0o644)
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "succeeded"
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600


def test_restore_of_the_selected_release_restarts_and_verifies_without_publishing_a_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating a same-release restore as a transition leaves its service stopped after the swap."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    _write_source_backup(paths, CURRENT, (MIGRATION_VERSION,))
    selection_root = Path(paths.local(paths.selection_root))
    selection_count = len(tuple(selection_root.iterdir()))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == CURRENT
    assert result.state["pre_restore_backup_id"] == "backup-00000000000000000000000000000001"
    assert runtime.events[-2:] == ["start", "verify"]
    assert len(tuple(selection_root.iterdir())) == selection_count


def test_same_release_restore_rerun_finishes_after_a_lost_start_result_without_a_new_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-release replay after the swap must retain completed-selection authority."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    _write_source_backup(paths, CURRENT, (MIGRATION_VERSION,))
    selection_root = Path(paths.local(paths.selection_root))
    selection_count = len(tuple(selection_root.iterdir()))
    runtime = _Runtime(fail_start=True)
    _install_runtime(monkeypatch, runtime)
    request = _request(paths, _credentials(tmp_path))

    interrupted = restore_module.restore(request)

    assert interrupted.outcome == "retryable"
    assert runtime.databases == {DATABASE, RETIRED}
    runtime.fail_start = False
    completed = restore_module.restore(request)

    assert completed.outcome == "succeeded"
    assert completed.state["selected_release_id"] == CURRENT
    assert completed.state["pre_restore_backup_id"] == "backup-00000000000000000000000000000002"
    assert len(tuple(selection_root.iterdir())) == selection_count


def test_same_release_restore_refuses_a_link_history_contradiction_before_backup_or_database_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-release source does not make a contradictory selected link safe to repair."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    _write_source_backup(paths, CURRENT, (MIGRATION_VERSION,))
    current = Path(paths.local(paths.current_link))
    current.unlink()
    current.symlink_to(Path(paths.local(paths.release_root / TARGET)))
    runtime = _Runtime()
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "manual"
    assert runtime.events == ["source-validation"]


def test_restore_converges_the_recognized_post_rename_database_arrangement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp/live swap boundary remains recoverable without a restore journal."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    runtime = _Runtime({TEMPORARY, RETIRED})
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == TARGET
    assert runtime.databases == {DATABASE}
    assert runtime.events[0] == "source-validation"
    assert f"backup:{RETIRED}" in runtime.events


def test_restore_rebuilds_a_deterministic_temporary_left_after_create_or_during_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Those interruption points share one safe live-and-temporary observation."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    runtime = _IncompleteTemporaryRuntime()
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == TARGET
    assert runtime.databases == {DATABASE}
    assert runtime.events.index("source-validation") < runtime.events.index(f"backup:{DATABASE}")
    assert "restore" in runtime.events


def test_restore_rerun_after_atomic_selection_creates_a_fresh_backup_from_the_retired_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reusing an old matching backup would make a post-swap replay ambiguous."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    safety_id = "backup-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    safety_dump = Path(paths.local(paths.backup_root / f"{safety_id}.dump"))
    safety_dump.write_bytes(b"pre-restore safety dump")
    safety_dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(
            safety_id,
            datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            hashlib.sha256(safety_dump.read_bytes()).hexdigest(),
            CURRENT,
            (MIGRATION_VERSION,),
            1024,
        ),
    )
    historical_id = "backup-cccccccccccccccccccccccccccccccc"
    historical_dump = Path(paths.local(paths.backup_root / f"{historical_id}.dump"))
    historical_dump.write_bytes(b"ordinary historical backup")
    historical_dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(
            historical_id,
            datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            hashlib.sha256(historical_dump.read_bytes()).hexdigest(),
            CURRENT,
            (MIGRATION_VERSION,),
            1024,
        ),
    )
    current = Path(paths.local(paths.current_link))
    current.unlink()
    current.symlink_to(Path(paths.local(paths.release_root / TARGET)))
    runtime = _Runtime({DATABASE, RETIRED})
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "succeeded"
    assert result.state["pre_restore_backup_id"] == "backup-00000000000000000000000000000001"
    assert runtime.databases == {DATABASE}
    assert f"backup:{RETIRED}" in runtime.events


def test_restore_keeps_a_fresh_current_schema_backup_when_the_source_backup_is_older(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-restore backup records the current database, not the source backup schema."""

    paths = _paths(tmp_path)
    _seed_state(paths, (MIGRATION, CURRENT_MIGRATION))
    runtime = _DifferentSchemaRuntime()
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == TARGET
    assert runtime.created_backups[0].migration_versions == (MIGRATION_VERSION, CURRENT_MIGRATION_VERSION)


def test_restore_returns_manual_before_mutation_for_an_unrecognized_database_identity_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A third authoritative database identity cannot be repaired by guessing."""

    paths = _paths(tmp_path)
    _seed_state(paths)
    runtime = _Runtime({DATABASE, TEMPORARY, RETIRED})
    _install_runtime(monkeypatch, runtime)

    result = restore_module.restore(_request(paths, _credentials(tmp_path)))

    assert result.outcome == "manual"
    assert not any(event.startswith("backup:") for event in runtime.events)
