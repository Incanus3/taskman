from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from taskman_ops.host_helper.backup_protection import (
    BackupProtection,
    write_backup_protection,
)
from taskman_ops.host_helper.operations import cleanup as cleanup_module
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import (
    BackupRecord,
    ReleaseRecord,
    SelectionRecord,
    append_selection,
    write_backup_manifest,
    write_release_manifest,
)
from taskman_ops.host_helper.state import mutation_observations, observe_host_state
from taskman_ops.host_protocol import HostRequest, ProtocolError
from taskman_ops.releases.identifiers import (
    build_release_id,
    release_application_version,
)
from taskman_ops.releases.manifests import (
    APPLICATION,
    ARCHITECTURE,
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    ELIXIR_VERSION,
    HEX_VERSION,
    NODE_VERSION,
    OTP_VERSION,
    REBAR3_VERSION,
    SCHEMA_VERSION,
    TARGET_OS,
    ArtifactManifest,
)
from tests.host_helper.support import managed_paths

CURRENT_REVISION = "a" * 40
STALE_REVISION = "b" * 40
CURRENT_DIGEST = "c" * 64
STALE_DIGEST = "d" * 64
RELEASE = build_release_id(
    "0.2.0", CURRENT_REVISION, artifact_sha256=CURRENT_DIGEST, source_dirty=False
)
STALE_RELEASE = build_release_id(
    "0.2.1", STALE_REVISION, artifact_sha256=STALE_DIGEST, source_dirty=False
)
STALE_BACKUP = "backup-" + "0" * 32
IN_USE_BACKUP = "backup-" + "1" * 32
RETAINED_BACKUP = "backup-" + "2" * 32
BACKUP_AT = datetime(2026, 9, 7, 12, tzinfo=UTC)


def _release(paths: ManagedPaths, release_id: str, revision: str, digest: str) -> None:
    directory = Path(paths.local(paths.release_root / release_id))
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o750)
    manifest = ArtifactManifest(
        SCHEMA_VERSION,
        APPLICATION,
        release_application_version(release_id),
        revision,
        release_id,
        BACKUP_AT,
        TARGET_OS,
        ARCHITECTURE,
        OTP_VERSION,
        ELIXIR_VERSION,
        NODE_VERSION,
        BUILDER_BASE_TAG,
        BUILDER_BASE_DIGEST,
        (),
        "taskman",
        HEX_VERSION,
        REBAR3_VERSION,
        digest,
        False,
    )
    write_release_manifest(
        paths, ReleaseRecord(release_id, revision, digest, (), 2, manifest)
    )


def _backup(
    paths: ManagedPaths, backup_id: str, *, created_at: datetime = BACKUP_AT
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


def _seed(tmp_path: Path) -> ManagedPaths:
    paths = managed_paths(tmp_path)
    _release(paths, RELEASE, CURRENT_REVISION, CURRENT_DIGEST)
    _release(paths, STALE_RELEASE, STALE_REVISION, STALE_DIGEST)
    _backup(paths, STALE_BACKUP, created_at=BACKUP_AT)
    _backup(paths, IN_USE_BACKUP, created_at=BACKUP_AT + timedelta(minutes=1))
    _backup(paths, RETAINED_BACKUP, created_at=BACKUP_AT + timedelta(minutes=2))
    append_selection(
        paths, SelectionRecord(RELEASE, None, IN_USE_BACKUP, BACKUP_AT, 2, None, ())
    )
    Path(paths.local(paths.current_link)).symlink_to(
        Path(paths.local(paths.release_root / RELEASE))
    )
    return paths


def _request(
    paths: ManagedPaths,
    *,
    action: str,
    targets: tuple[dict[str, object], ...] = (),
    expected_state: dict[str, object] | None = None,
    cursor: dict[str, object] | None = None,
) -> HostRequest:
    if action == "execute" and expected_state is None:
        state = observe_host_state(paths, allow_selection_transition=True)
        expected_state = dict(mutation_observations(state, "cleanup"))
    return HostRequest(
        3,
        "cleanup",
        "op-0123456789abcdef0123456789abcdef",
        expected_state or {},
        {
            "install_root": paths.install_root.as_posix(),
            "backup_root": paths.backup_root.as_posix(),
        },
        {
            "action": action,
            "targets": targets,
            "release_retention": 1,
            "backup_retention": 1,
            "cursor": cursor,
        },
    )


def _inspect(paths: ManagedPaths, *, cursor: dict[str, object] | None = None):
    result = cleanup_module.cleanup(_request(paths, action="inspect", cursor=cursor))
    assert result.outcome == "succeeded"
    return result


def _facts(result) -> dict[str, object]:
    return {key: result.state[key] for key in cleanup_module._EXPECTED_KEYS}


def test_inspection_is_filesystem_only_and_excludes_all_recovery_references(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    result = _inspect(paths)
    assert set(result.state) == {
        *cleanup_module._EXPECTED_KEYS,
        "targets",
        "inventory_sha256",
        "next_cursor",
    }
    identifiers = {target["identifier"] for target in result.state["targets"]}
    assert RELEASE not in identifiers
    assert IN_USE_BACKUP not in identifiers
    assert {STALE_RELEASE, STALE_BACKUP} <= identifiers


def test_inspection_preserves_and_reports_metadata_only_backup(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump")).unlink()
    result = _inspect(paths)
    assert Path(paths.local(paths.backup_manifest(STALE_BACKUP))).is_file()
    assert STALE_BACKUP not in {
        target["identifier"] for target in result.state["targets"]
    }
    assert any(STALE_BACKUP in warning for warning in result.warnings)


def test_inspection_lists_incomplete_dump_without_mutating_it(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    incomplete = Path(paths.local(paths.backup_root / ("backup-" + "9" * 32 + ".dump")))
    incomplete.write_bytes(b"partial")
    incomplete.chmod(0o600)
    result = _inspect(paths)
    assert incomplete.read_bytes() == b"partial"
    assert any(
        target["path"] == incomplete.as_posix() for target in result.state["targets"]
    )


def test_execute_deletes_only_confirmed_subset_and_leaves_new_target(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    inspected = _inspect(paths)
    confirmed = (dict(inspected.state["targets"][0]),)
    new_path = Path(paths.local(paths.release_root / ".release-new"))
    new_path.mkdir()
    new_path.chmod(0o750)
    result = cleanup_module.cleanup(
        _request(
            paths, action="execute", targets=confirmed, expected_state=_facts(inspected)
        )
    )
    assert result.outcome == "succeeded"
    assert result.state["completed_targets"] == confirmed
    assert new_path.exists()


def test_execute_refuses_checksum_changed_confirmed_target(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    inspected = _inspect(paths)
    targets = tuple(dict(item) for item in inspected.state["targets"])
    stale_dump = Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump"))
    stale_dump.write_bytes(b"changed")
    result = cleanup_module.cleanup(
        _request(
            paths, action="execute", targets=targets, expected_state=_facts(inspected)
        )
    )
    assert result.outcome in {"manual", "refused"}
    assert stale_dump.is_file()


def test_execute_refuses_a_backup_protected_after_confirmation(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    inspected = _inspect(paths)
    stale = next(
        dict(item)
        for item in inspected.state["targets"]
        if item["identifier"] == STALE_BACKUP
    )
    observed = observe_host_state(paths)
    write_backup_protection(
        paths,
        BackupProtection(
            1,
            STALE_BACKUP,
            observed.latest_successful_selection_filename,
            STALE_RELEASE,
            0,
            BACKUP_AT,
        ),
    )

    result = cleanup_module.cleanup(
        _request(
            paths, action="execute", targets=(stale,), expected_state=_facts(inspected)
        )
    )

    assert result.outcome == "refused"
    assert Path(paths.local(paths.backup_root / f"{STALE_BACKUP}.dump")).is_file()


def test_execute_reports_unknown_observations_after_partial_pair_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed(tmp_path)
    inspected = _inspect(paths)
    backup = next(
        dict(item) for item in inspected.state["targets"] if item["kind"] == "backup"
    )
    manifest = Path(paths.local(paths.backup_manifest(STALE_BACKUP)))

    def interrupt(_paths: ManagedPaths, _record: BackupRecord) -> None:
        manifest.unlink()
        raise OSError("lost after manifest")

    monkeypatch.setattr(cleanup_module, "delete_completed_backup", interrupt)
    result = cleanup_module.cleanup(
        _request(
            paths, action="execute", targets=(backup,), expected_state=_facts(inspected)
        )
    )
    assert result.outcome == "retryable"
    assert result.state["mutation_state"] == "unknown"
    assert result.state["completed_targets"] == ()
    assert result.state["unavailable_fields"] == tuple(
        sorted(cleanup_module._EXPECTED_KEYS)
    )


def test_unfinished_selection_transition_preserves_every_release(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    Path(paths.local(paths.current_link)).unlink()
    result = _inspect(paths)
    assert not [
        target for target in result.state["targets"] if target["kind"] == "release"
    ]


def test_unfinished_genesis_with_unavailable_database_preserves_every_release(
    tmp_path: Path,
) -> None:
    paths = managed_paths(tmp_path)
    _release(paths, RELEASE, CURRENT_REVISION, CURRENT_DIGEST)
    _release(paths, STALE_RELEASE, STALE_REVISION, STALE_DIGEST)

    result = _inspect(paths)

    assert result.state["selected_release_id"] is None
    assert result.state["last_successful_selection_id"] is None
    assert not [
        target for target in result.state["targets"] if target["kind"] == "release"
    ]


def test_execute_treats_an_already_absent_confirmed_target_as_completed(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    inspected = _inspect(paths)
    release = next(
        dict(item) for item in inspected.state["targets"] if item["kind"] == "release"
    )
    path = Path(str(release["path"]))
    shutil.rmtree(path)
    result = cleanup_module.cleanup(
        _request(
            paths,
            action="execute",
            targets=(release,),
            expected_state=_facts(inspected),
        )
    )

    assert result.outcome == "succeeded"
    assert result.state["mutation_state"] == "unchanged"
    assert result.state["completed_targets"] == (release,)


def test_execute_does_not_accept_a_forged_absent_path_as_completed(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    inspected = _inspect(paths)
    release = next(
        dict(item) for item in inspected.state["targets"] if item["kind"] == "release"
    )
    forged = {**release, "path": (tmp_path / "absent-outside").as_posix()}

    result = cleanup_module.cleanup(
        _request(
            paths, action="execute", targets=(forged,), expected_state=_facts(inspected)
        )
    )

    assert result.outcome == "refused"
    assert Path(str(release["path"])).is_dir()


def test_inspection_pages_more_than_sixty_four_targets_with_stable_digest(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    for index in range(70):
        revision = f"{index + 16:040x}"
        digest = f"{index + 16:064x}"
        release_id = build_release_id(
            f"1.0.{index}", revision, artifact_sha256=digest, source_dirty=False
        )
        _release(paths, release_id, revision, digest)
    first = _inspect(paths)
    second = _inspect(paths, cursor=dict(first.state["next_cursor"]))
    assert len(first.state["targets"]) == 64
    assert second.state["targets"]
    assert second.state["next_cursor"] is None
    assert first.state["inventory_sha256"] == second.state["inventory_sha256"]
    assert tuple(first.state["targets"] + second.state["targets"]) == tuple(
        sorted(
            first.state["targets"] + second.state["targets"],
            key=cleanup_module._identity,
        )
    )


def test_inspection_stops_a_page_at_the_encoded_byte_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed(tmp_path)
    root = Path(paths.local(paths.backup_root))
    for index in range(3, 7):
        incomplete = root / f"backup-{index:032x}.dump"
        incomplete.write_bytes(b"partial")
        incomplete.chmod(0o600)
    original = cleanup_module.encode_result

    def bounded(result):
        if len(result.state.get("targets", ())) > 2:
            raise ProtocolError("simulated byte budget")
        return original(result)

    monkeypatch.setattr(cleanup_module, "encode_result", bounded)
    first = _inspect(paths)
    second = _inspect(paths, cursor=dict(first.state["next_cursor"]))

    assert len(first.state["targets"]) == 2
    assert first.state["inventory_sha256"] == second.state["inventory_sha256"]


def test_inspect_requires_empty_expected_state_and_execute_requires_exact_four_facts(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    inspect = _request(paths, action="inspect")
    malformed_inspect = HostRequest(
        3,
        inspect.operation,
        inspect.correlation_id,
        {"selected_release_id": RELEASE},
        inspect.paths,
        inspect.parameters,
    )
    execute = _request(paths, action="execute")
    malformed_execute = HostRequest(
        3,
        execute.operation,
        execute.correlation_id,
        {},
        execute.paths,
        execute.parameters,
    )
    assert cleanup_module.cleanup(malformed_inspect).outcome == "refused"
    assert cleanup_module.cleanup(malformed_execute).outcome == "refused"
