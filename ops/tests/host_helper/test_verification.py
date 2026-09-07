from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime

import pytest

from taskman_ops.host_helper import verification as verification_module
from taskman_ops.host_helper.records import ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.state import HostState, StateAmbiguityError
from taskman_ops.host_protocol import HostRequest


CORRELATION = "op-0123456789abcdef0123456789abcdef"
RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CHECK_NAMES = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)


def _request(*, expected_release_id: str | None = None) -> HostRequest:
    return HostRequest(
        2,
        "verify",
        CORRELATION,
        {"expected_release_id": expected_release_id},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {
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
        },
    )


def _state(*, selected_release_id: str | None = RELEASE_ID) -> HostState:
    release = ReleaseRecord(RELEASE_ID, "a" * 40, "d" * 64, ())
    selection = SelectionRecord(RELEASE_ID, None, None, datetime(2026, 9, 5, tzinfo=UTC))
    return HostState(
        selected_release_id=selected_release_id,
        releases=(release,) if selected_release_id is not None else (),
        backups=(),
        selections=(selection,) if selected_release_id is not None else (),
        applied_migrations=(),
        service_state="running",
        database_state="ready",
        temporary_paths=(),
        warnings=(),
    )


def _install_healthy_observation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    observed: HostState,
    service_ok: bool = True,
) -> None:
    monkeypatch.setattr(verification_module, "observe_host_state", lambda *_a, **_k: observed, raising=False)
    monkeypatch.setattr(verification_module, "lifecycle_lock", lambda *_a, **_k: nullcontext(), raising=False)
    monkeypatch.setattr(verification_module, "_host_authority", lambda *_a, **_k: None)
    monkeypatch.setattr(verification_module, "_service_state", lambda *_a, **_k: (service_ok, 1234))
    monkeypatch.setattr(verification_module, "_main_pid_matches_release", lambda *_a, **_k: service_ok)
    monkeypatch.setattr(
        verification_module,
        "_successful",
        lambda argv, _timeout: (True, "" if argv[0] != "journalctl" else "clean startup"),
    )
    monkeypatch.setattr(verification_module, "_listener_values", lambda _deadline: (("127.0.0.1", 4000), ("127.0.0.1", 6789), ("127.0.0.1", 5432)))
    monkeypatch.setattr(verification_module, "_local_ready", lambda *_a, **_k: service_ok)
    monkeypatch.setattr(verification_module, "_curl", lambda *_a, **_k: (200, b"ready", (("cache-control", "no-store"), ("strict-transport-security", "max-age=31536000"))))


def test_empty_host_returns_a_bounded_release_selection_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state(selected_release_id=None))

    result = verification_module.verify(_request())

    assert result.outcome == "refused"
    assert result.state == {"selected_release_id": None}


def test_wire_host_request_never_enters_legacy_result_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = object()
    monkeypatch.setattr(
        verification_module,
        "_verify_host_state",
        lambda _request, **_kwargs: marker,
    )
    monkeypatch.setattr(
        verification_module,
        "_verify_legacy",
        lambda *_args, **_kwargs: pytest.fail("wire requests must not use legacy results"),
    )

    assert verification_module.verify(_request(), lifecycle_locked=True) is marker


def test_healthy_host_returns_complete_verification_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state())

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    assert result.outcome == "succeeded"
    report = result.state["report"]
    assert [check["name"] for check in report["checks"]] == list(CHECK_NAMES)
    assert all(check["status"] == "passed" for check in report["checks"])
    assert result.state["selected_release_id"] == RELEASE_ID
    assert result.state["database_state"] == "ready"


def test_failed_health_check_returns_retryable_report_without_stage_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state(), service_ok=False)

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    assert result.outcome == "retryable"
    assert result.state["report"]["exit_status"] != 0
    assert "stage" not in result.state
    assert "lifecycle" not in result.state


def test_read_only_verification_maps_lock_contention_to_retryable_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def locked(*_args: object, **_kwargs: object):
        class Lock:
            def __enter__(self):
                raise verification_module.LifecycleLockContention("held")

            def __exit__(self, *_exc: object) -> None:
                return None

        return Lock()

    monkeypatch.setattr(verification_module, "lifecycle_lock", locked, raising=False)
    monkeypatch.setattr(verification_module, "_host_authority", lambda *_a, **_k: None)

    result = verification_module.verify(_request())

    assert result.outcome == "retryable"
    assert result.state == {"locked": True}


def test_authoritative_state_ambiguity_is_not_reported_as_a_timeout_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verification_module, "_host_authority", lambda *_a, **_k: None)
    monkeypatch.setattr(
        verification_module,
        "lifecycle_lock",
        lambda *_a, **_k: nullcontext(),
        raising=False,
    )
    monkeypatch.setattr(
        verification_module,
        "observe_host_state",
        lambda *_a, **_k: (_ for _ in ()).throw(StateAmbiguityError("ambiguous")),
        raising=False,
    )

    result = verification_module.verify(_request())

    assert result.outcome == "refused"
    assert result.state == {}
