from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from taskman_ops.host_helper.operations import cleanup as cleanup_module
from taskman_ops.host_helper import backups as backup_capability
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.host_protocol import HostRequest
from taskman_ops.host_helper.state import observe_host_state


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
STALE_RELEASE = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
STALE_BACKUP = "backup-00000000000000000000000000000000"
IN_USE_BACKUP = "backup-11111111111111111111111111111111"
RETAINED_BACKUP = "backup-22222222222222222222222222222222"
BACKUP_AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths.from_mapping(
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}
    )


def _release(release_id: str) -> ReleaseRecord:
    source = release_id.split("-")[1] + ("0" * 28)
    return ReleaseRecord(release_id, source, "c" * 64, ())


def _publish_release(paths: ManagedPaths, release_id: str) -> None:
    path = Path(paths.local(paths.release_root / release_id))
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o750)
    write_release_manifest(paths, _release(release_id))


def _publish_backup(
    paths: ManagedPaths,
    backup_id: str,
    created_at: datetime = BACKUP_AT,
) -> None:
    root = Path(paths.local(paths.backup_root))
    root.mkdir(parents=True, exist_ok=True)
    dump = root / f"{backup_id}.dump"
    dump.write_bytes(backup_id.encode())
    dump.chmod(0o600)
    write_backup_manifest(
        paths,
        BackupRecord(
            backup_id,
            created_at,
            hashlib.sha256(dump.read_bytes()).hexdigest(),
            RELEASE,
            (),
            1024,
        ),
    )


def _managed_state(paths: ManagedPaths) -> None:
    _publish_release(paths, RELEASE)
    _publish_release(paths, STALE_RELEASE)
    _publish_backup(paths, STALE_BACKUP)
    _publish_backup(paths, IN_USE_BACKUP)
    _publish_backup(paths, RETAINED_BACKUP)
    append_selection(
        paths,
        SelectionRecord(
            RELEASE,
            None,
            IN_USE_BACKUP,
            datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        ),
    )
    Path(paths.local(paths.current_link)).symlink_to(Path(paths.local(paths.release_root / RELEASE)))


def _request(
    paths: ManagedPaths,
    *,
    action: str,
    targets: tuple[dict[str, object], ...] = (),
) -> HostRequest:
    return HostRequest(
        2,
        "cleanup",
        "op-0123456789abcdef0123456789abcdef",
        {"selected_release_id": RELEASE},
        {"install_root": paths.install_root.as_posix(), "backup_root": paths.backup_root.as_posix()},
        {
            "action": action,
            "targets": targets,
            "release_retention": 1,
            "backup_retention": 1,
        },
    )


def _inspect(paths: ManagedPaths) -> tuple[dict[str, object], ...]:
    result = cleanup_module.cleanup(_request(paths, action="inspect"))
    assert result.outcome == "succeeded"
    return tuple(dict(target) for target in result.state["targets"])


def test_cleanup_inspection_excludes_selected_and_in_use_artifacts(tmp_path: Path) -> None:
    """Selecting the current release or its recovery backup would destroy live authority."""

    paths = _paths(tmp_path)
    _managed_state(paths)

    targets = _inspect(paths)

    identifiers = {target["identifier"] for target in targets}
    assert RELEASE not in identifiers
    assert IN_USE_BACKUP not in identifiers
    assert {STALE_RELEASE, STALE_BACKUP} <= identifiers


def test_cleanup_retains_newest_unprotected_backups_by_creation_time_then_identifier(
    tmp_path: Path,
) -> None:
    """Sorting by backup ID would prune a newer dump when UUID order disagrees with time."""

    paths = _paths(tmp_path)
    _publish_release(paths, RELEASE)
    _publish_release(paths, STALE_RELEASE)
    _publish_backup(paths, STALE_BACKUP, datetime(2026, 9, 7, 12, 2, tzinfo=UTC))
    _publish_backup(paths, IN_USE_BACKUP, datetime(2026, 9, 7, 12, 1, tzinfo=UTC))
    _publish_backup(paths, RETAINED_BACKUP, datetime(2026, 9, 7, 12, 0, tzinfo=UTC))
    append_selection(paths, SelectionRecord(RELEASE, None, IN_USE_BACKUP, BACKUP_AT))
    Path(paths.local(paths.current_link)).symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    targets = _inspect(paths)

    backup_targets = {target["identifier"] for target in targets if target["kind"] == "backup"}
    assert backup_targets == {RETAINED_BACKUP}


def test_retention_refuses_a_manifest_replaced_after_observation(tmp_path: Path) -> None:
    """A changed manifest must not authorize deleting the completed dump it no longer describes."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    state = observe_host_state(paths)
    stale_dump = Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump"))
    stale_manifest = Path(paths.local(paths.backup_manifest(STALE_BACKUP)))
    stale_manifest.write_text(
        json.dumps(
            BackupRecord(
                STALE_BACKUP,
                BACKUP_AT,
                hashlib.sha256(stale_dump.read_bytes()).hexdigest(),
                STALE_RELEASE,
                (),
                1024,
            ).to_mapping()
        ),
        encoding="utf-8",
    )

    with pytest.raises(backup_capability.BackupAuthorityError, match="manifest"):
        backup_capability.prune_backups(paths, state, retention=1)

    assert stale_manifest.is_file()
    assert stale_dump.is_file()


@pytest.mark.parametrize("replaced", ("manifest", "dump"))
def test_retention_refuses_a_completed_pair_replaced_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replaced: str
) -> None:
    """A pathname swap after validation must not authorize either completed file's removal."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    state = observe_host_state(paths)
    root = Path(paths.local(paths.backup_root))
    stale_dump = root / f"{STALE_BACKUP}.dump"
    stale_manifest = Path(paths.local(paths.backup_manifest(STALE_BACKUP)))
    target = stale_manifest if replaced == "manifest" else stale_dump
    replacement = tmp_path / f"replacement-{replaced}"
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(target.stat().st_mode & 0o777)
    original = backup_capability.validate_manifest_identity
    swapped = False

    def swap_after_validation(path: Path, expected: BackupRecord) -> None:
        nonlocal swapped
        original(path, expected)
        if not swapped:
            os.replace(replacement, target)
            swapped = True

    monkeypatch.setattr(backup_capability, "validate_manifest_identity", swap_after_validation)

    with pytest.raises(backup_capability.BackupAuthorityError, match="identity"):
        backup_capability.prune_backups(paths, state, retention=1)

    assert stale_manifest.is_file()
    assert stale_dump.is_file()


def test_retention_refuses_a_manifest_replaced_between_read_and_identity_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returning a replacement inode after parsing another file would delete unvalidated bytes."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    state = observe_host_state(paths)
    stale_dump = Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump"))
    stale_manifest = Path(paths.local(paths.backup_manifest(STALE_BACKUP)))
    replacement = tmp_path / "replacement-manifest"
    replacement.write_bytes(stale_manifest.read_bytes())
    replacement.chmod(stale_manifest.stat().st_mode & 0o777)
    original_read = Path.read_bytes
    swapped = False

    def swap_after_read(path: Path) -> bytes:
        nonlocal swapped
        content = original_read(path)
        if path == stale_manifest and not swapped:
            os.replace(replacement, stale_manifest)
            swapped = True
        return content

    monkeypatch.setattr(Path, "read_bytes", swap_after_read)

    with pytest.raises(backup_capability.BackupAuthorityError, match="manifest identity changed"):
        backup_capability.prune_backups(paths, state, retention=1)

    assert stale_manifest.is_file()
    assert stale_dump.is_file()


def test_retention_stops_after_a_dump_is_replaced_during_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading a manifest after dump identity changes would extend a stale destructive decision."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    state = observe_host_state(paths)
    stale_dump = Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump"))
    stale_manifest = Path(paths.local(paths.backup_manifest(STALE_BACKUP)))
    replacement = tmp_path / "replacement-dump"
    replacement.write_bytes(stale_dump.read_bytes())
    replacement.chmod(stale_dump.stat().st_mode & 0o777)
    original_hash = backup_capability.sha256_file

    def replace_after_hash(path: Path) -> str:
        digest = original_hash(path)
        if path == stale_dump:
            os.replace(replacement, stale_dump)
        return digest

    monkeypatch.setattr(backup_capability, "sha256_file", replace_after_hash)
    monkeypatch.setattr(
        backup_capability,
        "validate_manifest_identity",
        lambda *_args: pytest.fail("must not read a manifest after dump identity changes"),
    )

    with pytest.raises(backup_capability.BackupAuthorityError, match="dump identity changed"):
        backup_capability.prune_backups(paths, state, retention=1)

    assert stale_manifest.is_file()
    assert stale_dump.is_file()


def test_cleanup_executes_only_the_exact_confirmed_paths(tmp_path: Path) -> None:
    """Substituting an operator path after confirmation must not broaden deletion."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    targets = _inspect(paths)
    outside = tmp_path / "outside"
    outside.mkdir()
    forged = tuple(
        {
            **target,
            "path": outside.as_posix(),
        }
        if target["identifier"] == STALE_RELEASE
        else target
        for target in targets
    )

    result = cleanup_module.cleanup(_request(paths, action="execute", targets=forged))

    assert result.outcome == "refused"
    assert outside.exists()
    assert Path(paths.local(paths.release_root / STALE_RELEASE)).exists()


def test_cleanup_rerun_deletes_remaining_confirmed_targets_after_interruption(
    tmp_path: Path,
) -> None:
    """Rejecting an already-absent confirmed target would make partial cleanup unrecoverable."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    targets = _inspect(paths)
    stale_release = Path(paths.local(paths.release_root / STALE_RELEASE))
    shutil.rmtree(stale_release)

    result = cleanup_module.cleanup(_request(paths, action="execute", targets=targets))

    assert result.outcome == "succeeded"
    assert not Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump")).exists()
    assert not Path(paths.local(paths.backup_manifest(STALE_BACKUP))).exists()


def test_cleanup_rerun_normalizes_a_backup_interrupted_between_its_two_files(tmp_path: Path) -> None:
    """A manifest-less deterministic dump is recognizable incomplete work, not a manual state."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    targets = _inspect(paths)
    Path(paths.local(paths.backup_manifest(STALE_BACKUP))).unlink()

    result = cleanup_module.cleanup(_request(paths, action="execute", targets=targets))

    assert result.outcome == "succeeded"
    assert not Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump")).exists()


def test_cleanup_requires_a_replan_when_the_dangerous_target_set_grows(tmp_path: Path) -> None:
    """Executing a newly discovered target without a new confirmation would be destructive."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    targets = _inspect(paths)
    new_release = "0.2.2-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6"
    path = Path(paths.local(paths.release_root / new_release))
    path.mkdir()
    path.chmod(0o750)
    write_release_manifest(paths, ReleaseRecord(new_release, "c" * 40, "d" * 64, ()))

    result = cleanup_module.cleanup(_request(paths, action="execute", targets=targets))

    assert result.outcome == "refused"
    assert path.exists()
    assert Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump")).is_file()


def test_cleanup_returns_manual_for_an_ambiguous_authoritative_target(tmp_path: Path) -> None:
    """Following a release symlink during cleanup could delete data outside Taskman roots."""

    paths = _paths(tmp_path)
    _managed_state(paths)
    stale = Path(paths.local(paths.release_root / STALE_RELEASE))
    shutil.rmtree(stale)
    outside = tmp_path / "outside"
    outside.mkdir()
    stale.symlink_to(outside, target_is_directory=True)

    result = cleanup_module.cleanup(_request(paths, action="inspect"))

    assert result.outcome == "manual"
    assert outside.exists()


def test_cleanup_preserves_backups_referenced_by_older_selection_history(tmp_path: Path) -> None:
    """Dropping an old selection backup would make the final observed history contradictory."""

    paths = _paths(tmp_path)
    first_release = "0.1.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6"
    old_backup = "backup-11111111111111111111111111111111"
    _publish_release(paths, first_release)
    _publish_release(paths, RELEASE)
    _publish_release(paths, STALE_RELEASE)
    _publish_backup(paths, old_backup)
    _publish_backup(paths, STALE_BACKUP)
    _publish_backup(paths, RETAINED_BACKUP)
    append_selection(
        paths,
        SelectionRecord(first_release, None, old_backup, datetime(2026, 9, 7, 10, 0, tzinfo=UTC)),
    )
    append_selection(
        paths,
        SelectionRecord(RELEASE, first_release, None, datetime(2026, 9, 7, 11, 0, tzinfo=UTC)),
    )
    Path(paths.local(paths.current_link)).symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    targets = _inspect(paths)
    result = cleanup_module.cleanup(_request(paths, action="execute", targets=targets))

    assert old_backup not in {target["identifier"] for target in targets}
    assert result.outcome == "succeeded"
    assert Path(paths.local(paths.backup_root / f"{old_backup}.dump")).is_file()
    assert Path(paths.local(paths.backup_manifest(old_backup))).is_file()


def test_cleanup_does_not_normalize_a_manifestless_backup_referenced_by_older_selection(
    tmp_path: Path,
) -> None:
    """Selection history must be protected before cleanup removes interrupted backup output."""

    paths = _paths(tmp_path)
    first_release = "0.1.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6"
    interrupted_backup = "backup-33333333333333333333333333333333"
    _publish_release(paths, first_release)
    _publish_release(paths, RELEASE)
    root = Path(paths.local(paths.backup_root))
    root.mkdir(parents=True, exist_ok=True)
    dump = root / f"{interrupted_backup}.dump"
    dump.write_bytes(b"interrupted")
    dump.chmod(0o600)
    append_selection(
        paths,
        SelectionRecord(first_release, None, interrupted_backup, datetime(2026, 9, 7, 10, 0, tzinfo=UTC)),
    )
    append_selection(
        paths,
        SelectionRecord(RELEASE, first_release, None, datetime(2026, 9, 7, 11, 0, tzinfo=UTC)),
    )
    Path(paths.local(paths.current_link)).symlink_to(Path(paths.local(paths.release_root / RELEASE)))

    result = cleanup_module.cleanup(_request(paths, action="inspect"))

    assert result.outcome == "manual"
    assert dump.is_file()


def test_cleanup_reports_an_unsafe_authoritative_root_as_manual_before_locking(tmp_path: Path) -> None:
    """An unsafe root is ambiguous authority, not a lock held by another helper."""

    outside = tmp_path / "outside"
    outside.mkdir()
    install = tmp_path / "install"
    install.symlink_to(outside, target_is_directory=True)
    paths = ManagedPaths.from_mapping(
        {"install_root": install.as_posix(), "backup_root": (tmp_path / "backups").as_posix()}
    )

    result = cleanup_module.cleanup(_request(paths, action="inspect"))

    assert result.outcome == "manual"
    assert result.state == {}
    assert outside.is_dir()


def test_cleanup_keeps_actual_lock_contention_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an acquired-path lock deadline warrants a retryable lock outcome."""

    paths = _paths(tmp_path)

    def locked(*_args: object, **_kwargs: object):
        class Lock:
            def __enter__(self) -> None:
                raise cleanup_module.LifecycleLockContention("held")

            def __exit__(self, *_exception: object) -> None:
                return None

        return Lock()

    monkeypatch.setattr(cleanup_module, "lifecycle_lock", locked)

    result = cleanup_module.cleanup(_request(paths, action="inspect"))

    assert result.outcome == "retryable"
    assert result.state == {"locked": True}
