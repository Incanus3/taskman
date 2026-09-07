from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime

import pytest

from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper.records import BackupRecord, ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.state import HostState, StateAmbiguityError
from taskman_ops.host_protocol import HostRequest


CORRELATION = "op-0123456789abcdef0123456789abcdef"
RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP_ID = "backup-cccccccccccccccccccccccccccccccc"
SELECTED_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _request(operation: str = "discover") -> HostRequest:
    return HostRequest(
        2,
        operation,
        CORRELATION,
        {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {},
    )


def _release() -> ReleaseRecord:
    return ReleaseRecord(RELEASE_ID, "a" * 40, "d" * 64, ())


def _backup() -> BackupRecord:
    return BackupRecord(BACKUP_ID, "e" * 64, RELEASE_ID, (20260905120000,), 128)


def _selection() -> SelectionRecord:
    return SelectionRecord(RELEASE_ID, None, BACKUP_ID, SELECTED_AT)


def _state(
    *,
    selected_release_id: str | None = RELEASE_ID,
    warnings: tuple[str, ...] = (),
) -> HostState:
    return HostState(
        selected_release_id=selected_release_id,
        releases=(_release(),),
        backups=(_backup(),),
        selections=(_selection(),),
        applied_migrations=(20260905120000,),
        service_state="running",
        database_state="ready",
        temporary_paths=(),
        warnings=warnings,
    )


def _install_observer(monkeypatch: pytest.MonkeyPatch, observed: HostState) -> None:
    monkeypatch.setattr(
        discover_module,
        "observe_host_state",
        lambda *_args, **_kwargs: observed,
        raising=False,
    )
    monkeypatch.setattr(
        discover_module,
        "lifecycle_lock",
        lambda *_args, **_kwargs: nullcontext(),
        raising=False,
    )


def test_discover_projects_completed_host_state_without_lifecycle_records() -> None:
    """The old lifecycle-shaped operation cannot satisfy a final request."""

    result = discover_module.discover(_request())

    assert result.outcome == "refused"


def test_discover_projects_selected_release_records_and_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _state()
    _install_observer(monkeypatch, observed)

    result = discover_module.discover(_request())

    assert result.outcome == "succeeded"
    assert result.state["selected_release_id"] == RELEASE_ID
    assert dict(result.state["releases"][0]) == {**observed.releases[0].to_mapping(), "migrations": ()}
    assert dict(result.state["backups"][0]) == {**observed.backups[0].to_mapping(), "migration_versions": (20260905120000,)}
    assert dict(result.state["selections"][0]) == observed.selections[0].to_mapping()
    assert result.state["applied_migrations"] == (20260905120000,)
    assert result.state["release_migrations"] == (
        {"release_id": RELEASE_ID, "migrations": ()},
    )
    assert "activations" not in result.state
    assert "adoptions" not in result.state


def test_list_releases_projects_record_rows_and_bounded_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warning = "unknown release entry: operator-notes.txt"
    _install_observer(monkeypatch, _state(warnings=(warning,)))

    result = discover_module.list_releases(_request("list_releases"))

    assert result.outcome == "succeeded"
    assert dict(result.state["releases"][0]) == {**_release().to_mapping(), "migrations": ()}
    assert result.warnings == (warning,)


def test_list_backups_projects_record_rows_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warning = "unknown backup entry: notes.txt"
    _install_observer(monkeypatch, _state(warnings=(warning,)))

    result = discover_module.list_backups(_request("list_backups"))

    assert result.outcome == "succeeded"
    assert dict(result.state["backups"][0]) == {**_backup().to_mapping(), "migration_versions": (20260905120000,)}
    assert result.warnings == (warning,)


def test_read_only_discovery_maps_lock_contention_to_retryable_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def locked(*_args: object, **_kwargs: object):
        class Lock:
            def __enter__(self):
                raise discover_module.LifecycleLockContention("held")

            def __exit__(self, *_exc: object) -> None:
                return None

        return Lock()

    monkeypatch.setattr(discover_module, "lifecycle_lock", locked, raising=False)

    result = discover_module.discover(_request())

    assert result.outcome == "retryable"
    assert result.state == {"locked": True}


def test_authoritative_state_ambiguity_is_a_bounded_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_observer(monkeypatch, _state())
    monkeypatch.setattr(
        discover_module,
        "observe_host_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StateAmbiguityError("current selection is ambiguous")
        ),
    )

    result = discover_module.discover(_request())

    assert result.outcome == "refused"
    assert result.state == {}
    assert result.warnings == ()
