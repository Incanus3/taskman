from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime

import pytest

from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper.records import ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.state import HostState, StateAmbiguityError
from taskman_ops.host_protocol import HostRequest
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, ArtifactManifest


CORRELATION = "op-0123456789abcdef0123456789abcdef"
REVISION = "a" * 40
ARTIFACT_SHA256 = "b" * 64
RELEASE_ID = build_release_id("0.2.0", REVISION, artifact_sha256=ARTIFACT_SHA256, source_dirty=False)
SELECTED_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _request(*, mode: str = "strict", backup_id: str | None = None) -> HostRequest:
    parameters: dict[str, object] = {
        "credentials_path": "/etc/taskman/pgpass",
        "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
        "mode": mode,
    }
    if backup_id is not None:
        parameters["backup_id"] = backup_id
    return HostRequest(3, "discover", CORRELATION, {}, {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"}, parameters)


def _release() -> ReleaseRecord:
    manifest = ArtifactManifest.from_mapping(
        {
            "schema_version": 3, "application": "taskman", "application_version": "0.2.0",
            "source_revision": REVISION, "release_id": RELEASE_ID, "built_at": "2026-09-05T12:00:00Z",
            "target_os": "ubuntu26.04", "architecture": "amd64", "otp_version": "29.0.6",
            "elixir_version": "1.20.4", "node_version": "22.22.1", "hex_version": "2.5.1",
            "rebar3_version": "3.24.0", "builder_base_tag": BUILDER_BASE_TAG,
            "builder_base_digest": BUILDER_BASE_DIGEST, "migrations": [], "top_level": "taskman",
            "artifact_sha256": ARTIFACT_SHA256, "source_dirty": False,
        }
    )
    return ReleaseRecord(RELEASE_ID, REVISION, ARTIFACT_SHA256, (), 2, manifest)


def _state(*, migrations: tuple[int, ...] = (20260905120000,)) -> HostState:
    selection = SelectionRecord(RELEASE_ID, None, None, SELECTED_AT, 2, None, ())
    return HostState(
        selected_release_id=RELEASE_ID, releases=(_release(),), backups=(), selections=(selection,),
        applied_migrations=migrations, service_state="running", database_state="ready",
        temporary_paths=(), warnings=(),
    )


def _install_observer(monkeypatch: pytest.MonkeyPatch, observed: HostState) -> None:
    monkeypatch.setattr(discover_module, "validate_credentials", lambda *_args: None)
    monkeypatch.setattr(discover_module, "observe_database_state", lambda *_args: {"state": observed.database_state, "applied_migrations": observed.applied_migrations})
    monkeypatch.setattr(discover_module, "observe_host_state", lambda *_args, **_kwargs: observed)
    monkeypatch.setattr(discover_module, "lifecycle_lock", lambda *_args, **_kwargs: nullcontext())


def test_strict_v3_discovery_projects_only_the_exact_bounded_common_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returning full inventories again would bypass the dedicated page protocol."""
    observed = _state()
    _install_observer(monkeypatch, observed)

    result = discover_module.discover(_request())

    selection = observed.latest_successful_selection.to_mapping()
    selection["recovery_backup_ids"] = ()
    assert result.outcome == "succeeded"
    assert result.state == {
        "selected_release_id": RELEASE_ID,
        "last_successful_selection_id": observed.latest_successful_selection_filename,
        "last_successful_selection": selection,
        "previous_successful_selection": None,
        "applied_migrations": (20260905120000,),
        "service_state": "running", "database_state": "ready",
    }


def test_strict_v3_discovery_round_trips_the_full_512_version_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Truncating a valid observed schema would make later migration admission unsound."""
    _install_observer(monkeypatch, _state(migrations=tuple(range(512))))

    result = discover_module.discover(_request())

    assert result.outcome == "succeeded"
    assert len(result.state["applied_migrations"]) == 512


@pytest.mark.parametrize(
    ("mode", "backup_id"),
    (("restore", None), ("strict", "backup-" + "c" * 32), ("unknown", None)),
)
def test_v3_discovery_refuses_malformed_mode_specific_parameters(monkeypatch: pytest.MonkeyPatch, mode: str, backup_id: str | None) -> None:
    """Accepting a partial restore shape could inspect a different recovery input."""
    _install_observer(monkeypatch, _state())

    result = discover_module.discover(_request(mode=mode, backup_id=backup_id))

    assert result.outcome == "refused"
    assert result.state == {}


def test_v3_non_strict_discovery_refuses_until_required_unknown_facts_are_observable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing unavailable scheduler authority with null would claim proven absence."""
    _install_observer(monkeypatch, _state())

    result = discover_module.discover(_request(mode="deploy"))

    assert result.outcome == "refused"
    assert result.state == {}


def test_read_only_discovery_maps_lock_contention_to_retryable_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(*_args: object, **_kwargs: object):
        class Lock:
            def __enter__(self):
                raise discover_module.LifecycleLockContention("held")
            def __exit__(self, *_exc: object) -> None:
                return None
        return Lock()
    monkeypatch.setattr(discover_module, "lifecycle_lock", locked)

    result = discover_module.discover(_request())

    assert result.outcome == "retryable"
    assert result.state == {"locked": True}


def test_authoritative_state_ambiguity_is_a_bounded_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_observer(monkeypatch, _state())
    monkeypatch.setattr(discover_module, "observe_host_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(StateAmbiguityError("ambiguous")))

    result = discover_module.discover(_request())

    assert result.outcome == "refused"
    assert result.state == {}
