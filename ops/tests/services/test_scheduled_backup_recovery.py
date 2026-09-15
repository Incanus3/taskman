"""Scheduled backup behavior while deployment authority is unfinished."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from taskman_ops.host_helper import backups, scheduled_backup
from taskman_ops.host_helper.backup_protection import (
    BackupProtection,
    write_backup_protection,
)
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    selection_filename,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.host_helper.state import observe_host_state
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import MigrationFingerprint
from tests.host_helper import test_deploy as deploy_fixture

HISTORY_BACKUP = "backup-11111111111111111111111111111111"
PROTECTED_BACKUP = "backup-22222222222222222222222222222222"
ORDINARY_BACKUP = "backup-33333333333333333333333333333333"


def _environment(paths: ManagedPaths) -> dict[str, str]:
    return {
        "TASKMAN_BACKUP_DATABASE_HOST": "127.0.0.1",
        "TASKMAN_BACKUP_DATABASE_PORT": "5432",
        "TASKMAN_BACKUP_DATABASE_ROLE": "taskman",
        "TASKMAN_BACKUP_DATABASE_NAME": "taskman",
        "TASKMAN_BACKUP_BACKUP_ROOT": paths.backup_root.as_posix(),
        "TASKMAN_BACKUP_INSTALL_ROOT": paths.install_root.as_posix(),
        "TASKMAN_BACKUP_RETENTION": "1",
    }


def _write_backup(
    paths: ManagedPaths,
    backup_id: str,
    source_release_id: str,
    migration_versions: tuple[int, ...],
    created_at: datetime,
) -> BackupRecord:
    dump = Path(paths.local(paths.backup_root / f"{backup_id}.dump"))
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_bytes(f"validated-{backup_id}".encode("ascii"))
    dump.chmod(0o600)
    record = BackupRecord(
        backup_id,
        created_at,
        hashlib.sha256(dump.read_bytes()).hexdigest(),
        source_release_id,
        migration_versions,
        1024,
    )
    write_backup_manifest(paths, record)
    return record


def _command_double(
    calls: list[tuple[str, ...]],
) -> Callable[..., subprocess.CompletedProcess[bytes]]:
    def run(
        argv: tuple[str, ...], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        if argv[0] == "psql":
            return subprocess.CompletedProcess(argv, 0, b"1024\n", b"")
        if argv[0] == "pg_dump":
            output = Path(
                next(
                    item.removeprefix("--file=")
                    for item in argv
                    if item.startswith("--file=")
                )
            )
            output.write_bytes(b"new scheduled backup")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    return run


def _credentials(tmp_path: Path) -> Path:
    credentials = tmp_path / "pgpass"
    credentials.write_text("127.0.0.1:5432:taskman:taskman:secret\n")
    credentials.chmod(0o600)
    return credentials


def _baseline_selection(*, backup_id: str | None = None) -> SelectionRecord:
    return SelectionRecord(
        deploy_fixture.CURRENT,
        None,
        backup_id,
        datetime(2026, 9, 7, 11, tzinfo=UTC),
        2,
        None,
        (),
    )


def _install_additional_candidate(
    paths: ManagedPaths,
    *,
    artifact_sha256: str,
    migrations: tuple[MigrationFingerprint, ...],
) -> str:
    release_id = build_release_id(
        "0.2.0",
        deploy_fixture.CANDIDATE_REVISION,
        artifact_sha256=artifact_sha256,
        source_dirty=False,
    )
    manifest = deploy_fixture._manifest(
        release_id=release_id,
        artifact_sha256=artifact_sha256,
        migrations=migrations,
    )
    release_path = Path(paths.local(paths.release_root / release_id))
    deploy_fixture._release_tree(release_path)
    write_release_manifest(
        paths,
        ReleaseRecord(
            release_id,
            deploy_fixture.CANDIDATE_REVISION,
            artifact_sha256,
            tuple(item.to_mapping() for item in migrations),
            2,
            manifest,
        ),
    )
    return release_id


def test_scheduled_entry_admits_failed_selection_and_preserves_exact_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed candidate current may still own the live protected migration prefix."""

    request = deploy_fixture._request(
        tmp_path,
        migrations=(deploy_fixture.MIGRATION, deploy_fixture.SECOND_MIGRATION),
    )
    deploy_fixture._install_current(
        dict(request.paths), migrations=(deploy_fixture.MIGRATION,)
    )
    Path(request.paths["backup_root"]).rmdir()
    deploy_fixture._install_unselected_candidate(request)
    paths = deploy_fixture.deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    candidate = deploy_fixture._candidate_id(request)

    selection_root = Path(paths.local(paths.selection_root))
    next(selection_root.iterdir()).unlink()
    history = _write_backup(
        paths,
        HISTORY_BACKUP,
        deploy_fixture.CURRENT,
        (int(deploy_fixture.MIGRATION.filename[:14]),),
        datetime(2026, 9, 7, 9, tzinfo=UTC),
    )
    selection = _baseline_selection(backup_id=history.backup_id)
    append_selection(paths, selection)
    protected = _write_backup(
        paths,
        PROTECTED_BACKUP,
        deploy_fixture.CURRENT,
        (int(deploy_fixture.MIGRATION.filename[:14]),),
        datetime(2026, 9, 7, 10, tzinfo=UTC),
    )
    write_backup_protection(
        paths,
        BackupProtection(
            1,
            protected.backup_id,
            selection_filename(selection),
            candidate,
            0,
            datetime(2026, 9, 7, 10, tzinfo=UTC),
        ),
    )
    ordinary = _write_backup(
        paths,
        ORDINARY_BACKUP,
        deploy_fixture.CURRENT,
        (int(deploy_fixture.MIGRATION.filename[:14]),),
        datetime(2026, 9, 7, 8, tzinfo=UTC),
    )
    current = Path(paths.local(paths.current_link))
    current.unlink()
    current.symlink_to(Path(paths.local(paths.release_root / candidate)))

    credentials = _credentials(tmp_path)
    live_versions = (
        int(deploy_fixture.MIGRATION.filename[:14]),
        int(deploy_fixture.SECOND_MIGRATION.filename[:14]),
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(scheduled_backup, "_CREDENTIALS_PATH", credentials)
    monkeypatch.setattr(
        scheduled_backup,
        "observe_database_migrations",
        lambda *_args: {"state": "ready", "applied_migrations": live_versions},
    )
    monkeypatch.setattr(backups, "run_command", _command_double(calls))

    assert scheduled_backup.main(_environment(paths)) == 0
    assert capsys.readouterr().out == "taskman scheduled backup completed\n"

    completed = observe_host_state(
        paths,
        database={"state": "ready", "applied_migrations": live_versions},
        allow_selection_transition=True,
    )
    created = tuple(
        record
        for record in completed.backups
        if record.backup_id
        not in {history.backup_id, protected.backup_id, ordinary.backup_id}
    )
    assert len(created) == 1
    assert created[0].source_release_id == candidate
    assert created[0].migration_versions == live_versions
    assert completed.selected_release_id == candidate
    assert completed.latest_successful_selection == selection
    assert {record.backup_id for record in completed.backups} == {
        history.backup_id,
        protected.backup_id,
        created[0].backup_id,
    }
    assert [argv[0] for argv in calls] == ["psql", "pg_dump", "pg_restore"]


def test_scheduled_entry_chooses_deterministic_protected_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = deploy_fixture._request(
        tmp_path,
        migrations=(deploy_fixture.MIGRATION, deploy_fixture.SECOND_MIGRATION),
    )
    deploy_fixture._install_current(
        dict(request.paths), migrations=(deploy_fixture.MIGRATION,)
    )
    Path(request.paths["backup_root"]).rmdir()
    deploy_fixture._install_unselected_candidate(request)
    paths = deploy_fixture.deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    candidates = {
        deploy_fixture._candidate_id(request),
        _install_additional_candidate(
            paths,
            artifact_sha256="f" * 64,
            migrations=(deploy_fixture.MIGRATION, deploy_fixture.SECOND_MIGRATION),
        ),
    }
    baseline = selection_filename(_baseline_selection())
    first_version = int(deploy_fixture.MIGRATION.filename[:14])
    for attempt, (backup_id, candidate) in enumerate(
        zip(
            (PROTECTED_BACKUP, ORDINARY_BACKUP),
            sorted(candidates, reverse=True),
            strict=True,
        )
    ):
        protected = _write_backup(
            paths,
            backup_id,
            deploy_fixture.CURRENT,
            (first_version,),
            datetime(2026, 9, 7, 9 + attempt, tzinfo=UTC),
        )
        write_backup_protection(
            paths,
            BackupProtection(
                1,
                protected.backup_id,
                baseline,
                candidate,
                attempt,
                datetime(2026, 9, 7, 9 + attempt, tzinfo=UTC),
            ),
        )

    live_versions = (
        first_version,
        int(deploy_fixture.SECOND_MIGRATION.filename[:14]),
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(scheduled_backup, "_CREDENTIALS_PATH", _credentials(tmp_path))
    monkeypatch.setattr(
        scheduled_backup,
        "observe_database_migrations",
        lambda *_args: {"state": "ready", "applied_migrations": live_versions},
    )
    monkeypatch.setattr(backups, "run_command", _command_double(calls))

    scheduled_backup.run_scheduled_backup(
        scheduled_backup.inputs_from_environment(_environment(paths))
    )

    state = observe_host_state(
        paths,
        database={"state": "ready", "applied_migrations": live_versions},
        allow_selection_transition=True,
    )
    created = [
        record
        for record in state.backups
        if record.backup_id not in {PROTECTED_BACKUP, ORDINARY_BACKUP}
    ]
    assert len(created) == 1
    assert created[0].source_release_id == min(candidates)
    assert state.selected_release_id == deploy_fixture.CURRENT
    assert state.latest_successful_selection == _baseline_selection()


def test_scheduled_entry_refuses_conflicting_protected_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conflicting = MigrationFingerprint(deploy_fixture.MIGRATION.filename, "9" * 64)
    request = deploy_fixture._request(
        tmp_path,
        migrations=(conflicting, deploy_fixture.SECOND_MIGRATION),
    )
    deploy_fixture._install_current(
        dict(request.paths), migrations=(deploy_fixture.MIGRATION,)
    )
    Path(request.paths["backup_root"]).rmdir()
    deploy_fixture._install_unselected_candidate(request)
    paths = deploy_fixture.deploy_module.ManagedPaths.from_mapping(dict(request.paths))
    protected = _write_backup(
        paths,
        PROTECTED_BACKUP,
        deploy_fixture.CURRENT,
        (int(deploy_fixture.MIGRATION.filename[:14]),),
        datetime(2026, 9, 7, 10, tzinfo=UTC),
    )
    write_backup_protection(
        paths,
        BackupProtection(
            1,
            protected.backup_id,
            selection_filename(_baseline_selection()),
            deploy_fixture._candidate_id(request),
            0,
            datetime(2026, 9, 7, 10, tzinfo=UTC),
        ),
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(scheduled_backup, "_CREDENTIALS_PATH", _credentials(tmp_path))
    monkeypatch.setattr(backups, "run_command", _command_double(calls))

    assert scheduled_backup.main(_environment(paths)) == 10
    assert (
        capsys.readouterr().out == "taskman scheduled backup needs manual attention\n"
    )
    assert calls == []
    assert {
        path.stem for path in Path(paths.local(paths.backup_root)).glob("*.json")
    } == {protected.backup_id}
