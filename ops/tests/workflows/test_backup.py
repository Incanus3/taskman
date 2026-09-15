from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_helper import backups as backup_capability
from taskman_ops.host_helper.backup_protection import BackupProtection
from taskman_ops.host_helper.operations import backup as backup_operation
from taskman_ops.host_helper.records import BackupRecord, ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.state import HostState
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, ArtifactManifest
from taskman_ops.workflows.backup import run_backup
from tests.support.environments import environment_config


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
BACKUP = "backup-0123456789abcdef0123456789abcdef"
TARGET = build_release_id(
    "0.2.1", "c" * 40, artifact_sha256="d" * 64, source_dirty=False
)
AT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.validate_operational_preflight",
        lambda *_args: None,
    )


def test_backup_requests_discovery_then_final_backup_and_merges_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping discovery or omitting final facts would lose the controller's backup boundary."""

    config = environment_config()
    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        if request.operation == "discover":
            assert request.parameters["mode"] == "deploy"
            return HostResult(3, "discover", request.correlation_id, "succeeded", "observed", {"selected_release_id": RELEASE}, ("discovery warning",))
        return HostResult(
            3,
            "backup",
            request.correlation_id,
            "succeeded",
            "completed",
            {
                "backup_id": BACKUP,
                "dump_path": f"/var/backups/taskman/{BACKUP}.dump",
                "size_bytes": 1024,
                "source_database_size_bytes": 2048,
                "source_release_id": RELEASE,
                "migration_versions": (20260914000000,),
                "selected_release_id": RELEASE,
                "service_state": "running",
                "database_state": "ready",
            },
            ("backup warning",),
        )

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", invoke)

    result = run_backup(object(), config)

    assert [request.operation for request in requests] == ["discover", "backup"]
    assert requests[1].expected_state == {}
    assert requests[1].parameters == {
        "credentials_path": "/etc/taskman/pgpass",
        "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman_prod"},
        "purpose": "scheduled",
    }
    assert result.stage == "backed-up"
    assert result.exit_status is ExitStatus.OK
    assert result.facts["backup_id"] == BACKUP
    assert result.facts["source_release_id"] == RELEASE
    assert result.facts["migration_versions"] == [20260914000000]
    assert result.warnings == ("discovery warning", "backup warning")


def test_backup_refuses_preflight_before_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.validate_operational_preflight",
        lambda *_args: (_ for _ in ()).throw(
            OpsError(ExitStatus.REMOTE_PREFLIGHT, "preflight", "unsafe")
        ),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.run_request",
        lambda *_args: pytest.fail("preflight refusal must precede discovery"),
    )

    result = run_backup(object(), environment_config())

    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.stage == "preflight-failed"


def test_backup_maps_final_retryable_result_to_backup_exit_and_preserves_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discarding planning warnings or changing BACKUP classification hides actionable final state."""

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        if request.operation == "discover":
            return HostResult(3, "discover", request.correlation_id, "succeeded", "observed", {"selected_release_id": RELEASE}, ("discovery warning",))
        return HostResult(3, "backup", request.correlation_id, "retryable", "rerun backup", {}, ("backup warning",))

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", invoke)

    result = run_backup(object(), environment_config())

    assert result.stage == "backup-failed"
    assert result.exit_status is ExitStatus.BACKUP
    assert result.warnings == ("discovery warning", "backup warning")


def _release(
    release_id: str,
    revision: str,
    digest: str,
    migrations: tuple[dict[str, str], ...],
) -> ReleaseRecord:
    manifest = ArtifactManifest.from_mapping(
        {
            "schema_version": 3,
            "application": "taskman",
            "application_version": release_id.split("-", 1)[0],
            "source_revision": revision,
            "release_id": release_id,
            "built_at": "2026-09-14T12:00:00Z",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "29.0.6",
            "elixir_version": "1.20.4",
            "node_version": "22.22.1",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "builder_base_tag": BUILDER_BASE_TAG,
            "builder_base_digest": BUILDER_BASE_DIGEST,
            "migrations": list(migrations),
            "top_level": "taskman",
            "artifact_sha256": digest,
            "source_dirty": False,
        }
    )
    return ReleaseRecord(release_id, revision, digest, migrations, 2, manifest)


def _public_host_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: HostState,
) -> tuple[object, HostState]:
    config = environment_config().model_copy(
        update={
            "install_root": PurePosixPath((tmp_path / "install").as_posix()),
            "backup_root": PurePosixPath((tmp_path / "backups").as_posix()),
        }
    )
    monkeypatch.setattr(backup_operation, "lifecycle_lock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(backup_operation, "prepare_backup_root", lambda *_args: None)
    monkeypatch.setattr(backup_operation, "normalize_temporary_dumps", lambda *_args: None)
    monkeypatch.setattr(backup_operation, "validate_completed_backups", lambda *_args: None)
    monkeypatch.setattr(backup_operation, "validate_credentials", lambda *_args: None)
    monkeypatch.setattr(
        backup_operation,
        "observe_database_migrations",
        lambda *_args: {"state": "ready", "applied_migrations": state.applied_migrations},
    )

    def observe(*_args: object, **kwargs: object) -> HostState:
        assert kwargs.get("allow_selection_transition") is True
        return state

    monkeypatch.setattr(backup_operation, "observe_host_state", observe)

    def create(
        observed: HostState,
        paths: object,
        *_args: object,
        **_kwargs: object,
    ) -> BackupRecord:
        source = backup_capability.select_backup_source(observed)
        dump = tmp_path / "backups" / f"{BACKUP}.dump"
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_bytes(b"validated backup")
        return BackupRecord(BACKUP, AT, "e" * 64, source.release_id, observed.applied_migrations, 1024)

    monkeypatch.setattr(backup_operation, "create_validated_backup", create)

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        if request.operation == "discover":
            assert request.parameters["mode"] == "deploy"
            return HostResult.for_request(
                request,
                "succeeded",
                "observed",
                {"selected_release_id": state.selected_release_id},
            )
        return backup_operation.backup(request)

    monkeypatch.setattr("taskman_ops.workflows.backup.run_request", invoke)
    return run_backup(object(), config), state


def test_public_manual_backup_accepts_valid_current_history_mismatch_without_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict discovery would block a safe backup during an unfinished deployment."""
    migration = ({"filename": "20260914000000_one.exs", "sha256": "1" * 64},)
    current = _release(RELEASE, "a" * 40, "b" * 64, migration)
    target = _release(TARGET, "c" * 40, "d" * 64, migration)
    selection = SelectionRecord(TARGET, RELEASE, None, AT, 2, RELEASE, ())
    state = HostState(RELEASE, (current, target), (), (selection,), (20260914000000,), "running", "ready", (), ())

    result, original = _public_host_backup(tmp_path, monkeypatch, state)

    assert result.exit_status is ExitStatus.OK
    assert result.facts["source_release_id"] == RELEASE
    assert original.selected_release_id == RELEASE
    assert original.latest_successful_selection == selection


def test_public_manual_backup_uses_protected_target_for_partial_live_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selected code that lacks the live prefix cannot own backup provenance."""
    migrations = tuple(
        {"filename": f"2026091400000{index}_migration.exs", "sha256": str(index) * 64}
        for index in (1, 2, 3)
    )
    current = _release(RELEASE, "a" * 40, "b" * 64, migrations[:1])
    target = _release(TARGET, "c" * 40, "d" * 64, migrations)
    selection = SelectionRecord(RELEASE, None, None, AT, 2, None, ())
    protection = BackupProtection(1, "backup-" + "f" * 32, None, TARGET, 0, AT)
    state = HostState(
        RELEASE,
        (current, target),
        (),
        (selection,),
        (20260914000001, 20260914000002),
        "running",
        "ready",
        (),
        (),
        (protection,),
    )

    result, original = _public_host_backup(tmp_path, monkeypatch, state)

    assert result.exit_status is ExitStatus.OK
    assert result.facts["source_release_id"] == TARGET
    assert result.facts["migration_versions"] == [20260914000001, 20260914000002]
    assert original.selected_release_id == RELEASE
    assert original.latest_successful_selection == selection


def test_public_manual_backup_refuses_conflicting_live_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preferred source must not override another relevant fingerprint."""
    current_migration = ({"filename": "20260914000001_current.exs", "sha256": "1" * 64},)
    target_migrations = (
        {"filename": "20260914000001_target.exs", "sha256": "2" * 64},
        {"filename": "20260914000002_next.exs", "sha256": "3" * 64},
    )
    current = _release(RELEASE, "a" * 40, "b" * 64, current_migration)
    target = _release(TARGET, "c" * 40, "d" * 64, target_migrations)
    selection = SelectionRecord(RELEASE, None, None, AT, 2, None, ())
    protection = BackupProtection(1, "backup-" + "f" * 32, None, TARGET, 0, AT)
    state = HostState(
        RELEASE,
        (current, target),
        (),
        (selection,),
        (20260914000001,),
        "running",
        "ready",
        (),
        (),
        (protection,),
    )

    result, original = _public_host_backup(tmp_path, monkeypatch, state)

    assert result.exit_status is ExitStatus.BACKUP
    assert result.facts["backup_id"] is None
    assert original.selected_release_id == RELEASE
    assert original.latest_successful_selection == selection
