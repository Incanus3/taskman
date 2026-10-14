from __future__ import annotations

import ast
from contextlib import nullcontext
from datetime import UTC, datetime
import inspect
import sys

import pytest

from taskman_ops.host_helper import verification as verification_module
from taskman_ops.host_helper.commands import CommandTimeout, run_command
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.records import ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.state import HostState, StateAmbiguityError
from taskman_ops.host_protocol import HostRequest
from taskman_ops.host_protocol.identifiers import ProtocolError
from taskman_ops.releases.identifiers import build_release_id
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
    TOP_LEVEL,
    ArtifactManifest,
)
from taskman_ops.workflows.helper import verification_settings
from tests.support.environments import environment_config


CORRELATION = "op-0123456789abcdef0123456789abcdef"
REVISION = "a" * 40
ARTIFACT_SHA256 = "d" * 64
RELEASE_ID = build_release_id(
    "0.2.0", REVISION, artifact_sha256=ARTIFACT_SHA256, source_dirty=False
)
OLD_DIGESTLESS_RELEASE_IDS = (
    "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
    "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6",
)
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


def _request(*, expected_release_id: str | None = None, readiness_timeout: int = 1) -> HostRequest:
    return HostRequest(
        3,
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
            "readiness_timeout": readiness_timeout,
            "connection_timeout": 1,
        },
    )


def _state(*, selected_release_id: str | None = RELEASE_ID) -> HostState:
    manifest = ArtifactManifest.from_mapping(
        {
            "schema_version": SCHEMA_VERSION,
            "application": APPLICATION,
            "application_version": "0.2.0",
            "source_revision": REVISION,
            "release_id": RELEASE_ID,
            "built_at": "2026-09-05T00:00:00Z",
            "target_os": TARGET_OS,
            "architecture": ARCHITECTURE,
            "otp_version": OTP_VERSION,
            "elixir_version": ELIXIR_VERSION,
            "node_version": NODE_VERSION,
            "hex_version": HEX_VERSION,
            "rebar3_version": REBAR3_VERSION,
            "builder_base_tag": BUILDER_BASE_TAG,
            "builder_base_digest": BUILDER_BASE_DIGEST,
            "migrations": [],
            "top_level": TOP_LEVEL,
            "artifact_sha256": ARTIFACT_SHA256,
            "source_dirty": False,
        }
    )
    release = ReleaseRecord(RELEASE_ID, REVISION, ARTIFACT_SHA256, (), 2, manifest)
    selection = SelectionRecord(
        RELEASE_ID,
        None,
        None,
        datetime(2026, 9, 5, tzinfo=UTC),
        2,
        None,
        (),
    )
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


def test_verification_expected_state_accepts_the_current_release_format() -> None:
    assert verification_module._expected_release({"expected_release_id": RELEASE_ID}) == RELEASE_ID


@pytest.mark.parametrize("release_id", OLD_DIGESTLESS_RELEASE_IDS)
def test_verification_expected_state_rejects_digestless_release_formats(
    release_id: str,
) -> None:
    with pytest.raises(ValueError, match="invalid expected release"):
        verification_module._expected_release({"expected_release_id": release_id})


def test_host_request_rejects_protocol_two() -> None:
    with pytest.raises(ProtocolError, match="unsupported protocol version"):
        HostRequest(
            2,
            "verify",
            CORRELATION,
            {"expected_release_id": None},
            {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
            {},
        )


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

    def successful(
        argv: tuple[str, ...], _timeout: float, **_kwargs: object
    ) -> tuple[bool, str]:
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
        lambda argv, _timeout, **_kwargs: _host_command_response(argv),
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
        lambda argv, _timeout, **_kwargs: (True, "" if argv[0] != "journalctl" else "clean startup"),
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
        (("127.0.0.1", 4000), ("127.0.0.1", 6789)),
    ],
)
def test_malformed_public_or_epmd_listener_topology_is_a_release_failure(
    monkeypatch: pytest.MonkeyPatch,
    listeners: tuple[tuple[str, int], ...] | None,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state(), patch_local_ready=False)
    monkeypatch.setattr(verification_module, "_listener_values", lambda _deadline: listeners)
    monkeypatch.setattr(
        verification_module.time,
        "sleep",
        lambda _seconds: pytest.fail("unsafe topology must not wait"),
    )

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 8
    assert report["checks"][3]["name"] == "listener-topology"
    assert report["checks"][3]["status"] == "failed"


def test_late_safe_listeners_leave_a_fresh_local_window_and_public_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newly active release may bind its safe listeners after systemd reports active."""

    _install_healthy_observation(monkeypatch, observed=_state())
    clock = [0.0]
    sleeps: list[float] = []
    observations = iter(
        (
            (("127.0.0.1", 5432),),
            (("127.0.0.1", 4000), ("127.0.0.1", 6789), ("127.0.0.1", 5432)),
        )
    )
    local_deadlines: list[float] = []
    public_timeouts: list[float] = []

    monkeypatch.setattr(verification_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        verification_module.time,
        "sleep",
        lambda seconds: (sleeps.append(seconds), clock.__setitem__(0, clock[0] + seconds)),
    )
    monkeypatch.setattr(verification_module, "_listener_values", lambda _deadline: next(observations))
    monkeypatch.setattr(
        verification_module,
        "_local_ready",
        lambda _port, _timeout, deadline: (local_deadlines.append(deadline), True)[1],
    )
    monkeypatch.setattr(
        verification_module,
        "_curl",
        lambda url, timeout: (
            public_timeouts.append(timeout),
            (200, b"ready", (("cache-control", "no-store"), ("strict-transport-security", "max-age=31536000"))),
        )[1],
    )

    result = verification_module.verify(
        _request(expected_release_id=RELEASE_ID, readiness_timeout=2)
    )

    assert result.outcome == "succeeded"
    assert sleeps == [1.0]
    assert local_deadlines == [3.0]
    assert public_timeouts == [1.0]


def test_verification_refuses_when_safe_listener_window_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state(), patch_local_ready=False)
    clock = [0.0]
    sleeps: list[float] = []

    monkeypatch.setattr(verification_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        verification_module.time,
        "sleep",
        lambda seconds: (sleeps.append(seconds), clock.__setitem__(0, clock[0] + seconds)),
    )
    monkeypatch.setattr(verification_module, "_listener_values", lambda _deadline: (("127.0.0.1", 5432),))
    monkeypatch.setattr(verification_module, "_local_ready", lambda *_args: pytest.fail("readiness must not start after topology exhaustion"))

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 8
    assert sleeps == [1.0]
    assert clock == [1.0]


def test_verification_refuses_an_unsafe_listener_observation_during_safe_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_healthy_observation(monkeypatch, observed=_state(), patch_local_ready=False)
    clock = [0.0]
    sleeps: list[float] = []
    observations = iter(
        (
            (("127.0.0.1", 5432),),
            (("0.0.0.0", 4000), ("127.0.0.1", 6789), ("127.0.0.1", 5432)),
        )
    )

    monkeypatch.setattr(verification_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        verification_module.time,
        "sleep",
        lambda seconds: (sleeps.append(seconds), clock.__setitem__(0, clock[0] + seconds)),
    )
    monkeypatch.setattr(verification_module, "_listener_values", lambda _deadline: next(observations))
    monkeypatch.setattr(verification_module, "_local_ready", lambda *_args: pytest.fail("unsafe topology must stop before readiness"))

    result = verification_module.verify(_request(expected_release_id=RELEASE_ID))

    report = result.state["report"]
    assert result.outcome == "retryable"
    assert report["exit_status"] == 8
    assert sleeps == [1.0]


def test_listener_topology_accepts_scoped_loopback_addresses_from_live_ss_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = """\
LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*
LISTEN 0 128 127.0.0.1:4000 0.0.0.0:*
LISTEN 0 128 [::1%lo]:6789 [::]:*
LISTEN 0 244 [::1]:5432 [::]:*
"""
    monkeypatch.setattr(verification_module, "_successful", lambda *_args, **_kwargs: (True, output))

    assert verification_module._listener_topology(4000, 6789, 5432, verification_module.time.monotonic() + 1.0)


def test_listener_topology_rejects_a_scoped_public_managed_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = """\
LISTEN 0 128 203.0.113.10%lo:4000 0.0.0.0:*
LISTEN 0 128 127.0.0.1:6789 0.0.0.0:*
LISTEN 0 244 127.0.0.1:5432 0.0.0.0:*
"""
    monkeypatch.setattr(verification_module, "_successful", lambda *_args, **_kwargs: (True, output))

    assert not verification_module._listener_topology(4000, 6789, 5432, verification_module.time.monotonic() + 1.0)


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

    def command(
        argv: tuple[str, ...], _timeout: float, **_kwargs: object
    ) -> tuple[bool, str]:
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


def test_startup_journal_accepts_a_normal_native_capture_above_the_general_output_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid 11,907-byte startup journal must not be refused at the 9 KiB default."""

    journal_argv = (
        "journalctl",
        "--no-pager",
        "--output=cat",
        "--unit",
        "taskman.service",
        "--lines=100",
    )

    def journal_fixture(
        argv: tuple[str, ...], *, timeout_seconds: float, output_limit: int
    ):
        assert argv == journal_argv
        assert output_limit == 65_536
        return run_command(
            (sys.executable, "-c", "import sys; sys.stdout.write('started\\n' + 'x' * 11899)"),
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
        )

    monkeypatch.setattr(verification_module, "run_command", journal_fixture)

    check = verification_module._journal_check()

    assert check["status"] == "passed"


@pytest.mark.parametrize("stream", ("stdout", "stderr"))
def test_journal_command_bound_accepts_exactly_64_kib_per_stream(stream: str) -> None:
    succeeded, output = verification_module._successful(
        (sys.executable, "-c", f"import sys; sys.{stream}.write('x' * 65536)"),
        1.0,
        output_limit=65_536,
    )

    assert succeeded is True
    assert len(output.encode("utf-8")) == (65_536 if stream == "stdout" else 0)


@pytest.mark.parametrize("stream", ("stdout", "stderr"))
def test_journal_command_bound_refuses_more_than_64_kib_from_either_stream(stream: str) -> None:
    succeeded, output = verification_module._successful(
        (sys.executable, "-c", f"import sys; sys.{stream}.write('x' * 65537)"),
        1.0,
        output_limit=65_536,
    )

    assert succeeded is False
    assert output == ""


def test_startup_journal_detects_a_failure_after_the_old_output_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_argv = (
        "journalctl",
        "--no-pager",
        "--output=cat",
        "--unit",
        "taskman.service",
        "--lines=100",
    )

    def journal_fixture(
        argv: tuple[str, ...], *, timeout_seconds: float, output_limit: int
    ):
        assert argv == journal_argv
        return run_command(
            (
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 10000 + ' application failed to start')",
            ),
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
        )

    monkeypatch.setattr(verification_module, "run_command", journal_fixture)

    check = verification_module._journal_check()

    assert check["status"] == "failed"
    assert check["summary"] == "recent startup journal evidence indicates a startup failure"


@pytest.mark.parametrize(
    ("script", "timeout", "summary"),
    (
        ("import time; time.sleep(1)", 0.01, "recent startup journal evidence is unavailable"),
        ("import sys; sys.exit(1)", 1.0, "recent startup journal evidence is unavailable"),
        ("pass", 1.0, "recent startup journal evidence is empty"),
    ),
)
def test_startup_journal_fails_closed_for_timeout_nonzero_and_empty_output(
    monkeypatch: pytest.MonkeyPatch,
    script: str,
    timeout: float,
    summary: str,
) -> None:
    def journal_fixture(
        argv: tuple[str, ...], *, timeout_seconds: float, output_limit: int
    ):
        assert argv == (
            "journalctl",
            "--no-pager",
            "--output=cat",
            "--unit",
            "taskman.service",
            "--lines=100",
        )
        return run_command(
            (sys.executable, "-c", script),
            timeout_seconds=min(timeout_seconds, timeout),
            output_limit=output_limit,
        )

    monkeypatch.setattr(verification_module, "run_command", journal_fixture)

    check = verification_module._journal_check()

    assert check["status"] == "failed"
    assert check["summary"] == summary


def test_other_commands_keep_the_9_kib_output_bound() -> None:
    succeeded, output = verification_module._successful(
        (sys.executable, "-c", "import sys; sys.stdout.write('x' * 9217)"), 1.0
    )

    assert succeeded is False
    assert output == ""


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


class _ApplicationHost:
    """Native-command boundary fixture; records/selection/locking stay real."""

    def __init__(self, request: HostRequest):
        self.request = request
        self.caddy_active = False
        self.local_response = b"HTTP/2 200\r\ncache-control: no-store\r\n\r\nready"
        self.public_response = b"HTTP/2 200\r\ncache-control: no-store\r\nstrict-transport-security: max-age=31536000\r\n\r\nready"
        self.listeners = "LISTEN 0 128 127.0.0.1:4000 0.0.0.0:*\nLISTEN 0 128 127.0.0.1:6789 0.0.0.0:*\nLISTEN 0 128 127.0.0.1:5432 0.0.0.0:*\n"
        self.journal = b"clean startup"
        self.service = b"active\n1234\n"
        self.executable = f"{request.paths['install_root']}/releases/{RELEASE_ID}/erts-16.0/bin/beam.smp\n".encode()
        self.clock = 100.0
        self.public_requests: list[float] = []
        self.local_advance = 0.0
        self.command_advance = 0.0
        self.limits: list[tuple[str, float, int]] = []

    def run(self, argv, *, timeout_seconds, output_limit):
        import subprocess
        from taskman_ops.host_helper.commands import CommandError
        self.limits.append((argv[0], timeout_seconds, output_limit))
        self.clock += self.command_advance
        if argv[0] == "df": output = b"Avail\n20000000000\n"
        elif argv[:2] == ("runuser", "-u"): output = b"1\n" if "psql" in argv else b""
        elif argv[:2] == ("systemctl", "show"): output = self.service
        elif argv[0] == "readlink": output = self.executable
        elif argv[:2] == ("systemctl", "is-active"):
            if not self.caddy_active: raise CommandError("inactive")
            output = b""
        elif argv[0] == "ss": output = self.listeners.encode()
        elif argv[0] == "journalctl": output = self.journal
        elif argv[0] == "curl":
            if argv[-1].startswith("https://"):
                self.public_requests.append(timeout_seconds)
                output = self.public_response
            else:
                self.clock += self.local_advance
                output = self.local_response
        else: pytest.fail(f"unexpected native command: {argv[0]}")
        return subprocess.CompletedProcess(argv, 0, output, b"")


def _application_host(tmp_path, monkeypatch, *, connection_timeout=1) -> tuple[HostRequest, _ApplicationHost]:
    from pathlib import Path
    from taskman_ops.host_helper.records import write_release_manifest
    request = _request(expected_release_id=RELEASE_ID, readiness_timeout=30)
    paths = {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}
    managed = ManagedPaths.from_mapping(paths)
    Path(managed.release_root / RELEASE_ID).mkdir(parents=True)
    Path(managed.backup_root).mkdir()
    write_release_manifest(managed, _state().releases[0])
    Path(managed.current_link).symlink_to(managed.release_root / RELEASE_ID)
    request = HostRequest(3, "verify", CORRELATION, request.expected_state, paths,
                          {**request.parameters, "connection_timeout": connection_timeout})
    host = _ApplicationHost(request)
    monkeypatch.setattr(verification_module, "run_command", host.run)
    monkeypatch.setattr(verification_module.time, "monotonic", lambda: host.clock)
    monkeypatch.setattr(verification_module.time, "sleep", lambda seconds: setattr(host, "clock", host.clock + seconds))
    monkeypatch.setenv("SUDO_USER", "deployer")
    monkeypatch.setenv("SSH_CONNECTION", "198.51.100.7 51324 203.0.113.10 22")
    return request, host


def _application_verify(request, **kwargs):
    verify_application = getattr(verification_module, "verify_application", None)
    assert callable(verify_application), "private application verifier is missing"
    return verify_application(request, **kwargs)


def test_application_verification_admits_selected_candidate_without_history_or_caddy(tmp_path, monkeypatch) -> None:
    from taskman_ops.host_protocol import validate_application_report
    request, host = _application_host(tmp_path, monkeypatch)
    result = _application_verify(request)
    report = validate_application_report(result.local_report)
    assert report["exit_status"] == 0
    assert [check["name"] for check in report["checks"]] == [
        "taskman-service", "release-identity", "listener-topology", "startup-journal", "local-readiness",
    ]
    assert result.public_probe == "not-attempted"
    assert not host.public_requests
    assert not (tmp_path / "install" / "deployments" / "selections").exists()
    assert all(0 < timeout <= 3 for _command, timeout, _limit in host.limits)
    assert all(limit == (65536 if command == "journalctl" else 9216) for command, _timeout, limit in host.limits)
    assert verification_module.verify(request).outcome == "refused"


@pytest.mark.parametrize("public", (
    b"HTTP/2 200\r\ncache-control: no-store\r\nstrict-transport-security: max-age=31536000\r\n\r\nready",
    b"HTTP/2 503\r\ncache-control: no-store\r\nstrict-transport-security: max-age=31536000\r\n\r\nunavailable",
    b"HTTP/2 200\r\ncache-control: no-store\r\n\r\nready",
))
def test_application_public_probe_is_single_advisory_health_and_hsts_attempt(tmp_path, monkeypatch, public) -> None:
    request, host = _application_host(tmp_path, monkeypatch)
    host.caddy_active = True
    host.public_response = public
    result = _application_verify(request)
    assert result.local_report["exit_status"] == 0
    assert result.public_probe == ("ready" if public.endswith(b"ready") and b"strict-transport-security" in public else "not-ready")
    assert len(host.public_requests) == 1
    assert 0 < host.public_requests[0] <= 3.0


@pytest.mark.parametrize("failure", ("service", "executable", "topology", "journal", "readiness"))
def test_application_failure_retains_fixed_attempted_prefix_and_never_probes_public(tmp_path, monkeypatch, failure) -> None:
    request, host = _application_host(tmp_path, monkeypatch)
    host.caddy_active = True
    if failure == "service": host.service = b"inactive\n0\n"
    elif failure == "executable": host.executable = b"/usr/bin/beam.smp\n"
    elif failure == "topology": host.listeners += "LISTEN 0 128 0.0.0.0:4000 0.0.0.0:*\n"
    elif failure == "journal": host.journal = b"database startup failed"
    else: host.local_response = b"HTTP/2 503\r\ncache-control: no-store\r\n\r\nunavailable"
    result = _application_verify(request)
    assert result.local_report["exit_status"] == (9 if failure == "readiness" else 8)
    assert len(result.local_report["checks"]) == (5 if failure == "readiness" else 4)
    assert result.local_report["status"] == "failed"
    if failure in {"readiness", "journal"}:
        assert result.local_report["checks"][-1]["status"] == "failed"
    assert result.public_probe == "not-attempted"
    assert not host.public_requests


def test_application_advisory_has_its_own_bound_after_late_local_success(tmp_path, monkeypatch) -> None:
    request, host = _application_host(tmp_path, monkeypatch, connection_timeout=30)
    host.caddy_active = True
    host.local_advance = 29.75
    result = _application_verify(request)
    assert result.public_probe == "ready"
    assert host.public_requests == [3.0]


@pytest.mark.parametrize("failure,expected", (("input", 2), ("administrator", 5), ("authority", 10), ("lock", 12)))
def test_application_preproof_errors_keep_existing_categories_without_fabricating_report(tmp_path, monkeypatch, failure, expected) -> None:
    request, host = _application_host(tmp_path, monkeypatch)
    if failure == "input": request = HostRequest(3, "verify", CORRELATION, {}, request.paths, {})
    elif failure == "administrator": monkeypatch.setenv("SUDO_USER", "other")
    elif failure == "authority": (tmp_path / "install" / "current").unlink()
    else:
        from taskman_ops.host_helper.lock import LifecycleLockContention
        from contextlib import contextmanager
        @contextmanager
        def contention(*args, **kwargs):
            raise LifecycleLockContention("busy")
            yield
        monkeypatch.setattr(verification_module, "lifecycle_lock", contention)
    error_class = getattr(verification_module, "ApplicationVerificationError", None)
    assert isinstance(error_class, type), "private application error is missing"
    with pytest.raises(error_class) as captured:
        _application_verify(request)
    assert captured.value.exit_code == expected
    assert not host.public_requests


@pytest.mark.parametrize("expected", (None,))
def test_application_requires_exact_expected_release_before_observing_host(tmp_path, monkeypatch, expected) -> None:
    request, host = _application_host(tmp_path, monkeypatch)
    request = HostRequest(3, "verify", CORRELATION, {"expected_release_id": expected}, request.paths, request.parameters)
    with pytest.raises(verification_module.ApplicationVerificationError) as captured:
        _application_verify(request)
    assert captured.value.exit_code == 2
    assert host.limits == []


def test_full_verification_requires_active_caddy(tmp_path, monkeypatch) -> None:
    from taskman_ops.host_helper.records import append_selection
    from taskman_ops.host_protocol import validate_verification_report
    request, host = _application_host(tmp_path, monkeypatch)
    append_selection(ManagedPaths.from_mapping(request.paths), _state().selections[0])
    host.caddy_active = False
    result = verification_module.verify(request)
    report = validate_verification_report(result.state["report"])
    assert report["exit_status"] == 8
    assert any(check["name"] == "caddy-service" and check["status"] == "failed" for check in report["checks"])


def test_application_retains_configured_local_http_timeout_and_caps_advisory_attempt(tmp_path, monkeypatch) -> None:
    request, host = _application_host(tmp_path, monkeypatch, connection_timeout=10)
    host.caddy_active = True
    result = _application_verify(request)
    assert result.local_report["exit_status"] == 0
    assert [timeout for command, timeout, _limit in host.limits if command == "curl"] == [10, 3]
    assert all(timeout <= 3 for command, timeout, _limit in host.limits if command != "curl")


def test_verification_observation_has_a_fresh_sixty_second_window(tmp_path, monkeypatch) -> None:
    from pathlib import Path
    from taskman_ops.host_helper.records import append_selection
    request, host = _application_host(tmp_path, monkeypatch)
    append_selection(ManagedPaths.from_mapping(request.paths), _state().selections[0])
    host.caddy_active = True
    host.command_advance = 3.0
    read_bytes = Path.read_bytes

    def delayed_manifest(path):
        raw = read_bytes(path)
        if path.name == ".taskman-release.json":
            host.clock += 59.0
        return raw

    monkeypatch.setattr(Path, "read_bytes", delayed_manifest)
    result = verification_module.verify(request)
    report = result.state["report"]
    assert report["exit_status"] == 0
    assert host.public_requests == [1.0]
    assert all(timeout == 3.0 for command, timeout, _limit in host.limits if command != "curl")


@pytest.mark.parametrize("application", (False, True))
def test_expired_inventory_refuses_authority_without_readiness_report(tmp_path, monkeypatch, application) -> None:
    from pathlib import Path
    request, host = _application_host(tmp_path, monkeypatch)
    read_bytes = Path.read_bytes

    def expired_manifest(path):
        raw = read_bytes(path)
        host.clock += 60.0
        return raw

    monkeypatch.setattr(Path, "read_bytes", expired_manifest)
    if application:
        with pytest.raises(verification_module.ApplicationVerificationError) as captured:
            _application_verify(request)
        assert (captured.value.exit_code, captured.value.failed_boundary) == (10, "authority")
    else:
        result = verification_module.verify(request)
        assert result.outcome == "refused"
        assert "report" not in result.state
    assert not host.public_requests
    assert [command for command, _timeout, _limit in host.limits] == ["df", "df", "runuser", "runuser"]


def test_late_topology_and_local_readiness_have_independent_windows(tmp_path, monkeypatch) -> None:
    from taskman_ops.host_helper.records import append_selection
    request, host = _application_host(tmp_path, monkeypatch, connection_timeout=60)
    append_selection(ManagedPaths.from_mapping(request.paths), _state().selections[0])
    host.caddy_active = True
    native_run = host.run

    def delayed_readiness(argv, **kwargs):
        result = native_run(argv, **kwargs)
        if argv[0] == "ss" and host.clock < 129.0:
            result.stdout = b"LISTEN 0 128 127.0.0.1:5432 0.0.0.0:*\n"
        if argv[0] == "curl" and argv[-1].startswith("http://") and host.clock < 158.0:
            result.stdout = b"HTTP/2 503\r\ncache-control: no-store\r\n\r\nunavailable"
        return result

    monkeypatch.setattr(verification_module, "run_command", delayed_readiness)
    result = _application_verify(request)
    report = result.local_report
    assert report["exit_status"] == 0
    assert host.clock == 158.0
    assert host.public_requests == [3.0]
    local_timeouts = [timeout for command, timeout, _limit in host.limits if command == "curl"][:-1]
    assert local_timeouts[0] == 30.0
    assert local_timeouts[-1] == 1.0


@pytest.mark.parametrize("phase", ("topology", "local"))
def test_polling_clips_the_final_attempt_and_stops_after_command_cleanup(monkeypatch, phase) -> None:
    from taskman_ops.host_helper.commands import CommandTimeout
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(verification_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(verification_module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def timed_out(argv, *, timeout_seconds, output_limit):
        timeouts.append(timeout_seconds)
        clock[0] += timeout_seconds + 0.4
        if phase == "topology" and len(timeouts) == 1:
            import subprocess
            return subprocess.CompletedProcess(argv, 0, b"LISTEN 0 128 127.0.0.1:5432 0.0.0.0:*\n", b"")
        raise CommandTimeout("timeout")

    monkeypatch.setattr(verification_module, "run_command", timed_out)
    if phase == "topology":
        assert not verification_module._listener_topology(4000, 6789, 5432, 5.5)
        assert timeouts == pytest.approx([3.0, 1.1])
    else:
        assert not verification_module._local_ready(4000, 3.0, 5.5)
        assert timeouts == pytest.approx([3.0, 1.1])
    assert clock[0] == pytest.approx(5.9)


@pytest.mark.parametrize("application", (False, True))
def test_public_command_timeout_keeps_full_failure_or_advisory_status(tmp_path, monkeypatch, application) -> None:
    from taskman_ops.host_helper.commands import CommandTimeout
    from taskman_ops.host_helper.records import append_selection
    request, host = _application_host(tmp_path, monkeypatch, connection_timeout=90)
    append_selection(ManagedPaths.from_mapping(request.paths), _state().selections[0])
    host.caddy_active = True
    native_run = host.run

    def public_timeout(argv, **kwargs):
        result = native_run(argv, **kwargs)
        if argv[0] == "curl" and argv[-1].startswith("https://"):
            raise CommandTimeout("request timeout")
        return result

    monkeypatch.setattr(verification_module, "run_command", public_timeout)
    result = _application_verify(request) if application else verification_module.verify(request)
    if application:
        assert result.local_report["exit_status"] == 0
        assert result.public_probe == "not-ready"
    else:
        assert result.state["report"]["exit_status"] == 9
        assert len(result.state["report"]["checks"]) == 8
    assert host.public_requests == ([3.0] if application else [30.0])


def test_timed_out_advisory_caddy_is_not_attempted(tmp_path, monkeypatch) -> None:
    from taskman_ops.host_helper.commands import CommandTimeout
    request, host = _application_host(tmp_path, monkeypatch)
    native_run = host.run

    def caddy_timeout(argv, **kwargs):
        if argv[:2] == ("systemctl", "is-active"):
            assert kwargs["timeout_seconds"] == 3.0
            raise CommandTimeout("Caddy timeout")
        return native_run(argv, **kwargs)

    monkeypatch.setattr(verification_module, "run_command", caddy_timeout)
    result = _application_verify(request)
    assert result.local_report["exit_status"] == 0
    assert result.public_probe == "not-attempted"
    assert not host.public_requests


def test_submillisecond_http_bound_never_starts_an_advisory_request(tmp_path, monkeypatch) -> None:
    request, host = _application_host(tmp_path, monkeypatch)
    settings = verification_module._settings(request.parameters)
    monkeypatch.setattr(verification_module, "_settings", lambda _parameters: {**settings, "connection_timeout": 0.0009})
    host.caddy_active = True
    # Local proof is already independently exercised; isolate the advisory's no-request branch.
    monkeypatch.setattr(verification_module, "_local_ready", lambda *_args: True)
    result = _application_verify(request)
    assert result.local_report["exit_status"] == 0
    assert result.public_probe == "not-attempted"
    assert not host.public_requests


@pytest.mark.parametrize("application,intentional_wait,envelope", ((False, 179.0, 185.0), (True, 152.0, 160.0)))
def test_independent_phase_waits_fit_the_derived_envelope(tmp_path, monkeypatch, application, intentional_wait, envelope) -> None:
    from contextlib import contextmanager
    from pathlib import Path
    from taskman_ops.host_helper.records import append_selection
    request, host = _application_host(tmp_path, monkeypatch, connection_timeout=90)
    append_selection(ManagedPaths.from_mapping(request.paths), _state().selections[0])
    host.caddy_active = True
    started = host.clock
    native_run = host.run
    read_bytes = Path.read_bytes
    actual_lock = verification_module.lifecycle_lock
    topology_started = []

    @contextmanager
    def delayed_lock(paths, timeout_seconds):
        assert timeout_seconds == 5.0
        host.clock += 5.0
        with actual_lock(paths, timeout_seconds) as lock:
            yield lock

    def delayed_manifest(path):
        raw = read_bytes(path)
        if path.name == ".taskman-release.json":
            host.clock += 59.999
        return raw

    def bounded_command(argv, **kwargs):
        result = native_run(argv, **kwargs)
        if argv[0] == "ss" and not topology_started:
            topology_started.append(host.clock)
        host.clock += kwargs["timeout_seconds"]
        if argv[0] == "ss" and host.clock - topology_started[0] < 29.999:
            result.stdout = b"LISTEN 0 128 127.0.0.1:5432 0.0.0.0:*\n"
        return result

    monkeypatch.setattr(verification_module, "lifecycle_lock", delayed_lock)
    monkeypatch.setattr(Path, "read_bytes", delayed_manifest)
    monkeypatch.setattr(verification_module, "run_command", bounded_command)
    result = _application_verify(request) if application else verification_module.verify(request)
    report = result.local_report if application else result.state["report"]
    assert report["exit_status"] == 0
    elapsed = host.clock - started
    assert elapsed == pytest.approx(intentional_wait - 0.001)
    # Eleven possible cleanup overruns contribute at most 4.4 seconds.
    assert elapsed + 4.4 < envelope


@pytest.mark.parametrize("application,caller_holds_lock", [(False, False), (True, True)])
def test_observation_lock_boundary_preserves_caller_ownership(tmp_path, monkeypatch, application, caller_holds_lock) -> None:
    from contextlib import nullcontext
    from taskman_ops.host_helper.lock import lifecycle_lock, LifecycleLockContention
    from taskman_ops.host_helper.records import append_selection
    request, host = _application_host(tmp_path, monkeypatch)
    paths = ManagedPaths.from_mapping(request.paths)
    append_selection(paths, _state().selections[0])
    host.caddy_active = True
    observer = verification_module.observe_host_state
    native_run = host.run
    observations = []

    def observed_under_lock(*args, **kwargs):
        with pytest.raises(LifecycleLockContention):
            with lifecycle_lock(paths, timeout_seconds=0):
                pytest.fail("state observation requires the lifecycle lock")
        observations.append(True)
        return observer(*args, **kwargs)

    def command_after_observation(argv, **kwargs):
        if argv[:2] == ("systemctl", "show"):
            if caller_holds_lock:
                with pytest.raises(LifecycleLockContention):
                    with lifecycle_lock(paths, timeout_seconds=0):
                        pytest.fail("verification must retain its caller's lock")
            else:
                with lifecycle_lock(paths, timeout_seconds=0):
                    pass
        return native_run(argv, **kwargs)

    monkeypatch.setattr(verification_module, "observe_host_state", observed_under_lock)
    monkeypatch.setattr(verification_module, "run_command", command_after_observation)
    with lifecycle_lock(paths, timeout_seconds=0) if caller_holds_lock else nullcontext() as lock:
        result = (_application_verify(request, lifecycle_locked=caller_holds_lock) if application
                  else verification_module.verify(request, lifecycle_locked=caller_holds_lock))
        report = result.local_report if application else result.state["report"]
        assert report["exit_status"] == 0
        if caller_holds_lock:
            assert lock.held
    assert observations == [True]
    with lifecycle_lock(paths, timeout_seconds=0):
        pass


def test_administrator_prerequisite_queries_the_configured_socket_and_port(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(verification_module, "_available_bytes", lambda *_: 20_000_000_000)
    monkeypatch.setattr(verification_module, "_invoking_administrator_ok", lambda *_: True)
    monkeypatch.setattr(verification_module, "_active_ssh_connection_ok", lambda *_: True)
    monkeypatch.setattr(verification_module, "_successful", lambda argv, *_: calls.append(argv) or (True, b"1\n"))
    paths = ManagedPaths.from_mapping({"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")})
    assert verification_module._host_authority({"ssh_user": "deployer", "ssh_port": 22, "database_port": 5433}, paths) is None
    argv = calls[0]
    assert argv[argv.index("--host") + 1] == "/var/run/postgresql"
    assert argv[argv.index("--port") + 1] == "5433"
    assert argv[argv.index("--username") + 1] == "postgres"
    assert "--no-psqlrc" in argv and "--no-password" in argv
