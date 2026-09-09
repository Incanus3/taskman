from __future__ import annotations

import ast
from contextlib import nullcontext
from datetime import UTC, datetime
import inspect

import pytest

from taskman_ops.host_helper import verification as verification_module
from taskman_ops.host_helper.commands import CommandTimeout
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.state import HostState, StateAmbiguityError
from taskman_ops.host_protocol import HostRequest
from taskman_ops.workflows.helper import verification_settings
from tests.support.environments import environment_config


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


def _preflight_paths(tmp_path) -> ManagedPaths:
    return ManagedPaths.from_mapping(
        {
            "install_root": (tmp_path / "install").as_posix(),
            "backup_root": (tmp_path / "backups").as_posix(),
        }
    )


@pytest.mark.parametrize("status_line", ["HTTP/1.1 200 OK", "HTTP/1.1 200 ", "HTTP/2 200", "HTTP/2 200 ", "HTTP/3 200 "])
def test_http_readiness_accepts_curl_status_lines_with_empty_reason_phrase(status_line: str) -> None:
    response = verification_module._http(
        status_line + "\r\ncache-control: no-store\r\nstrict-transport-security: max-age=31536000\r\n\r\nready"
    )

    assert verification_module._ready(response)
    assert verification_module._hsts(response)


@pytest.mark.parametrize("status_line", ["HTTP/2 20 ", "HTTP/2 2000 ", "HTTP/2 200\nforged", "HTTP/2 200\rforged"])
def test_http_readiness_rejects_malformed_status_lines(status_line: str) -> None:
    assert verification_module._http(status_line + "\r\ncache-control: no-store\r\n\r\nready") is None


def test_environment_verification_settings_accept_connection_timeout_above_command_bound() -> None:
    config = environment_config()

    settings = verification_module._settings(verification_settings(config))

    assert settings["connection_timeout"] == 10.0


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), float("-inf"), True, "10", None])
def test_settings_reject_non_positive_or_non_finite_connection_timeout(timeout: object) -> None:
    parameters = dict(_request().parameters)
    parameters["connection_timeout"] = timeout

    with pytest.raises(ValueError, match="verification timeout"):
        verification_module._settings(parameters)


def test_fixed_host_commands_retain_the_three_second_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verification_module.time, "monotonic", lambda: 100.0)

    assert verification_module._command_timeout(110.0) == verification_module._MAX_COMMAND_SECONDS
    assert verification_module._command_timeout(102.5) == 2.5


def _host_command_response(argv: tuple[str, ...]) -> tuple[bool, str]:
    if argv == ("cat", "/etc/os-release"):
        return True, 'ID=ubuntu\nVERSION_ID="26.04"\n'
    if argv == ("uname", "-m"):
        return True, "amd64\n"
    if argv == ("cat", "/proc/1/comm"):
        return True, "systemd\n"
    if argv == ("free", "--bytes"):
        return True, "Mem: 1073741824\n"
    if argv[:2] == ("getent", "ahosts"):
        return True, "203.0.113.10 STREAM taskman.example.test\n"
    if argv[0] == "df":
        return True, "Avail\n10737418240\n"
    if argv[0] == "runuser":
        return True, ""
    raise AssertionError(f"unexpected host preflight command: {argv!r}")


def test_host_preflight_does_not_repeat_controller_immutable_admission(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-reading immutable platform evidence would duplicate controller admission."""

    commands: list[tuple[str, ...]] = []

    def successful(argv: tuple[str, ...], _timeout: float) -> tuple[bool, str]:
        commands.append(argv)
        return _host_command_response(argv)

    monkeypatch.setattr(verification_module, "_successful", successful)
    monkeypatch.setenv("SUDO_USER", "deployer")
    monkeypatch.setenv("SSH_CONNECTION", "198.51.100.7 51324 203.0.113.10 22")

    assert verification_module.host_preflight(_preflight_paths(tmp_path), _request().parameters) is None
    assert not any(command[0] in {"cat", "uname", "free", "getent"} for command in commands)


@pytest.mark.parametrize(
    ("sudo_user", "ssh_connection"),
    (
        ("other-admin", "198.51.100.7 51324 203.0.113.10 22"),
        ("deployer", "198.51.100.7 51324 203.0.113.10 2202"),
    ),
)
def test_host_preflight_refuses_changed_administrator_or_ssh_session_identity(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    sudo_user: str,
    ssh_connection: str,
) -> None:
    """An identity change after controller admission must stop a consequence."""

    monkeypatch.setattr(
        verification_module,
        "_successful",
        lambda argv, _timeout: _host_command_response(argv),
    )
    monkeypatch.setenv("SUDO_USER", sudo_user)
    monkeypatch.setenv("SSH_CONNECTION", ssh_connection)

    assert verification_module.host_preflight(_preflight_paths(tmp_path), _request().parameters) == "preflight"


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
    [
        None,
        (("0.0.0.0", 4000), ("127.0.0.1", 6789), ("127.0.0.1", 5432)),
        (("127.0.0.1", 4000), ("127.0.0.1", 6789), ("127.0.0.1", 5432), ("127.0.0.1", 4369)),
    ],
)
def test_malformed_public_or_epmd_listener_topology_is_a_release_failure(
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


def test_listener_topology_accepts_scoped_loopback_addresses_from_live_ss_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = """\
LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*
LISTEN 0 128 127.0.0.1:4000 0.0.0.0:*
LISTEN 0 128 [::1%lo]:6789 [::]:*
LISTEN 0 244 [::1]:5432 [::]:*
"""
    monkeypatch.setattr(verification_module, "_successful", lambda *_args: (True, output))

    assert verification_module._listener_topology(4000, 6789, 5432, 1.0)


def test_listener_topology_rejects_a_scoped_public_managed_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = """\
LISTEN 0 128 203.0.113.10%lo:4000 0.0.0.0:*
LISTEN 0 128 127.0.0.1:6789 0.0.0.0:*
LISTEN 0 244 127.0.0.1:5432 0.0.0.0:*
"""
    monkeypatch.setattr(verification_module, "_successful", lambda *_args: (True, output))

    assert not verification_module._listener_topology(4000, 6789, 5432, 1.0)


@pytest.mark.parametrize(
    "listener",
    (
        "127.0.0.53%:53",
        "127.0.0.53%lo%bad:53",
        "127.0.0.53%lo/bad:53",
        "not-an-ip%lo:53",
        "[::1%]:6789",
        "[::1%lo%bad]:6789",
    ),
)
def test_listener_rejects_invalid_scoped_address_or_interface_suffix(listener: str) -> None:
    assert verification_module._listener(listener) is None


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


def test_verification_runs_each_host_command_through_the_shared_bounded_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second subprocess loop could diverge from the helper timeout and output contract."""

    seen: dict[str, object] = {}

    def bounded(argv: tuple[str, ...], *, timeout_seconds: float, output_limit: int):
        seen["argv"] = argv
        seen["timeout"] = timeout_seconds
        seen["output_limit"] = output_limit
        raise CommandTimeout("command timed out")

    monkeypatch.setattr(verification_module, "run_command", bounded, raising=False)

    assert verification_module._successful(("journalctl", "--no-pager"), 0.5) == (False, "")
    assert seen == {
        "argv": ("journalctl", "--no-pager"),
        "timeout": 0.5,
        "output_limit": 9_216,
    }


def test_verification_has_no_competing_popen_or_select_command_loop() -> None:
    """Adding a second command engine would bypass the shared forced-termination boundary."""

    tree = ast.parse(inspect.getsource(verification_module))
    calls = {
        (node.func.value.id, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }

    assert ("subprocess", "Popen") not in calls
    assert ("select", "select") not in calls


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
