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
    patch_local_ready: bool = True,
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
    if patch_local_ready:
        monkeypatch.setattr(verification_module, "_local_ready", lambda *_a, **_k: service_ok)
    monkeypatch.setattr(verification_module, "_curl", lambda *_a, **_k: (200, b"ready", (("cache-control", "no-store"), ("strict-transport-security", "max-age=31536000"))))


def test_empty_host_returns_a_bounded_release_selection_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state(selected_release_id=None))

    result = verification_module.verify(_request())

    assert result.outcome == "refused"
    assert result.state == {"selected_release_id": None}


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


@pytest.mark.parametrize(
    "listeners",
    [None, (("0.0.0.0", 4000), ("127.0.0.1", 6789), ("127.0.0.1", 5432))],
)
def test_malformed_or_public_listener_topology_is_a_release_failure(
    monkeypatch: pytest.MonkeyPatch,
    listeners: tuple[tuple[str, int], ...] | None,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state(), patch_local_ready=False)
    monkeypatch.setattr(verification_module, "_listener_values", lambda _deadline: listeners)

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 8
    assert report["checks"][3]["name"] == "listener-topology"
    assert report["checks"][3]["status"] == "failed"


def test_executable_mismatch_is_a_release_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state())
    monkeypatch.setattr(verification_module, "_main_pid_matches_release", lambda *_a, **_k: False)

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 8
    assert report["checks"][1]["name"] == "release-identity"
    assert report["checks"][1]["status"] == "failed"


def test_startup_journal_failure_is_a_release_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state())

    def command(argv: tuple[str, ...], _timeout: float) -> tuple[bool, str]:
        if argv[0] == "journalctl":
            return True, "application failed to start"
        return True, ""

    monkeypatch.setattr(verification_module, "_successful", command)

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 8
    assert report["checks"][4]["name"] == "startup-journal"
    assert report["checks"][4]["status"] == "failed"


@pytest.mark.parametrize(
    "command,timeout",
    [
        (("python3", "-c", "import time; time.sleep(1)"), 0.01),
        (("python3", "-c", "print('x' * 10000)"), 1.0),
    ],
)
def test_subprocess_timeout_and_output_bounds_are_safe(
    command: tuple[str, ...],
    timeout: float,
) -> None:
    succeeded, output = verification_module._successful(command, timeout)

    assert succeeded is False
    assert output == ""


@pytest.mark.parametrize(
    "response",
    [
        (200, b"ready\n", (("cache-control", "no-store"),)),
        (200, b"ready", (("cache-control", "max-age=60"),)),
    ],
)
def test_local_readiness_requires_exact_body_and_cache_control(
    monkeypatch: pytest.MonkeyPatch,
    response: tuple[int, bytes, tuple[tuple[str, str], ...]],
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state(), patch_local_ready=False)
    monkeypatch.setattr(verification_module, "_curl", lambda *_a, **_k: response)

    result = verification_module.verify(
        _request(expected_release_id=RELEASE_ID)
    )

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 9
    assert report["checks"][-1]["name"] == "local-readiness"
    assert report["checks"][-1]["status"] == "failed"


def test_public_readiness_requires_exact_body_and_cache_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state())
    monkeypatch.setattr(verification_module, "_local_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(
        verification_module,
        "_curl",
        lambda *_a, **_k: (200, b"ready\n", (("cache-control", "no-store"), ("strict-transport-security", "max-age=31536000"))),
    )

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 9
    assert report["checks"][-2]["name"] == "public-readiness"
    assert report["checks"][-2]["status"] == "failed"


@pytest.mark.parametrize(
    "hsts",
    ["max-age=0", "max-age=abc", "max-age=60; max-age=120"],
)
def test_public_hsts_requires_a_single_positive_numeric_max_age(
    monkeypatch: pytest.MonkeyPatch,
    hsts: str,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state())
    monkeypatch.setattr(verification_module, "_local_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(
        verification_module,
        "_curl",
        lambda *_a, **_k: (200, b"ready", (("cache-control", "no-store"), ("strict-transport-security", hsts))),
    )

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 9
    assert report["checks"][-1]["name"] == "public-hsts"
    assert report["checks"][-1]["status"] == "failed"
