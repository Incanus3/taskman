from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
from pathlib import PurePosixPath
import sys

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host.facts import CaddyState, HostFacts, Listener, ProvisioningMarkerState
from taskman_ops.manifests import ArtifactManifest
from taskman_ops.output import clear_secrets, register_secret
from taskman_ops.releases.records import ActivationRecord, AdoptionRecord, ReleaseRecord
from taskman_ops.remote import CommandResult
from taskman_ops import verification
from taskman_ops.verification import (
    CheckStatus,
    HTTPResponse,
    VerificationCheck,
    VerificationReport,
    verify_installation,
)


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
ADOPTED_RELEASE_ID = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_PATH = PurePosixPath(f"/opt/taskman/releases/{RELEASE_ID}")
AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def test_verify_installation_returns_a_versioned_report() -> None:
    """Returning an untyped or unversioned verification result must fail this."""

    assert VerificationReport.schema_version == 1
    assert callable(verify_installation)


def test_verify_installation_reports_exact_ready_topology_without_public_network() -> None:
    """Dropping an exact health or listener check must fail this."""

    remote = VerificationRemote()
    public = RecordingPublicClient(
        HTTPResponse(200, b"ready", (("cache-control", "no-store"), ("strict-transport-security", "max-age=31536000")))
    )

    report = verify_installation(
        remote,
        config(),
        public_client=public,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    assert report.exit_status is ExitStatus.OK
    assert report.release_id == RELEASE_ID
    assert [check.name for check in report.checks] == [
        "taskman-service",
        "release-identity",
        "caddy-service",
        "listener-topology",
        "startup-journal",
        "local-readiness",
        "public-readiness",
        "public-hsts",
    ]
    assert all(check.status == "passed" for check in report.checks)
    assert public.calls == [("https://taskman.acme.tld/healthz", 10)]
    assert report.to_mapping()["schema_version"] == 1


def test_public_connection_failure_is_a_readiness_report_after_local_success() -> None:
    """Letting a public transport exception escape must fail this."""

    remote = VerificationRemote()

    report = verify_installation(remote, config(), public_client=FailingPublicClient())

    assert report.exit_status is ExitStatus.READINESS
    assert [check.status for check in report.checks[-2:]] == ["failed", "failed"]
    assert remote.local_probe_count == 1


def test_wrong_expected_release_is_a_safety_refusal() -> None:
    """Ignoring expected-release identity must fail this."""

    remote = VerificationRemote()
    different_release = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"

    with pytest.raises(OpsError) as raised:
        verify_installation(remote, config(), expected_release_id=different_release, public_client=unused_public)

    assert raised.value.status is ExitStatus.SAFETY


def test_local_readiness_failure_never_contacts_public_health_and_polling_is_bounded() -> None:
    """Calling public health before local readiness or sleeping in tests must fail this."""

    remote = VerificationRemote(
        local_responses=[
            "HTTP/1.1 503 Service Unavailable\r\nCache-Control: no-store\r\n\r\nunavailable",
            "HTTP/1.1 200 OK\r\nCache-Control: no-store\r\n\r\nnot-ready",
        ]
    )
    public = RecordingPublicClient(HTTPResponse(200, b"ready", (("cache-control", "no-store"), ("strict-transport-security", "max-age=1"))))
    clock = AdvancingClock()

    report = verify_installation(
        remote,
        config(),
        public_client=public,
        clock=clock,
        sleeper=clock.sleep,
        max_attempts=2,
    )

    assert report.exit_status is ExitStatus.READINESS
    assert report.checks[-1].name == "local-readiness"
    assert remote.local_probe_count == 2
    assert clock.sleeps == [1.0]
    assert public.calls == []


def test_malformed_socket_evidence_fails_closed_as_lifecycle_failure() -> None:
    """Accepting unparsable ss evidence would make a public listener invisible."""

    remote = VerificationRemote(ss_output="LISTEN broken socket evidence\n")

    report = verify_installation(remote, config(), public_client=unused_public)

    assert report.exit_status is ExitStatus.RELEASE
    assert report.checks[3].name == "listener-topology"
    assert report.checks[3].status == "failed"


def test_startup_journal_report_redacts_raw_evidence() -> None:
    """Including a journal line in the report could disclose an application secret."""

    canary = "taskman-journal-canary-83c55b"
    remote = VerificationRemote(journal_output=f"startup-failure\n{canary}\n")

    report = verify_installation(remote, config(), public_client=unused_public)

    assert report.exit_status is ExitStatus.RELEASE
    assert report.checks[4].summary == "recent startup journal evidence is unavailable"
    assert canary not in report.human()
    assert canary not in str(report.to_mapping())
    journal_call = next(argv for argv, _kwargs in remote.calls if argv[:2] == ("sh", "-ceu") and argv[3] == "taskman-verify-journal")
    assert "--lines=100" in journal_call[2]
    assert "grep" in journal_call[2]


def test_adopted_release_uses_its_recorded_arbitrary_directory_for_main_pid_identity() -> None:
    """Inferring an adopted release path from its identifier must fail this."""

    adopted_path = PurePosixPath("/opt/taskman/releases/operator-chosen-baseline")
    remote = VerificationRemote()
    remote.use_adopted_release(adopted_path)
    remote.executable_path = adopted_path / "erts-27.3.4.6/bin/beam.smp"
    public = RecordingPublicClient(HTTPResponse(200, b"ready", (("cache-control", "no-store"), ("strict-transport-security", "max-age=1"))))

    report = verify_installation(remote, config(), expected_release_id=ADOPTED_RELEASE_ID, public_client=public)

    assert report.exit_status is ExitStatus.OK
    assert report.release_id == ADOPTED_RELEASE_ID


def test_missing_hsts_is_reported_separately_from_public_ready() -> None:
    """Treating public readiness as proof of HSTS must fail this."""

    public = RecordingPublicClient(HTTPResponse(200, b"ready", (("cache-control", "no-store"),)))

    report = verify_installation(VerificationRemote(), config(), public_client=public)

    assert report.exit_status is ExitStatus.READINESS
    assert report.checks[-2].status == "passed"
    assert report.checks[-1].name == "public-hsts"
    assert report.checks[-1].status == "failed"


def test_main_pid_outside_selected_release_is_a_lifecycle_failure() -> None:
    """Accepting any active beam process would verify the wrong release."""

    remote = VerificationRemote()
    remote.executable_path = PurePosixPath("/opt/taskman/releases/other/erts-27.3.4.6/bin/beam.smp")

    report = verify_installation(remote, config(), public_client=unused_public)

    assert report.exit_status is ExitStatus.RELEASE
    assert report.checks[1].name == "release-identity"
    assert report.checks[1].status == "failed"
    assert remote.local_probe_count == 0


def test_epmd_listener_is_a_lifecycle_failure_even_when_loopback_only() -> None:
    """Allowing EPMD would expose unintended Erlang discovery on the host."""

    remote = VerificationRemote(
        ss_output=(
            "LISTEN 0 4096 127.0.0.1:4000 0.0.0.0:*\n"
            "LISTEN 0 4096 127.0.0.1:6789 0.0.0.0:*\n"
            "LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*\n"
            "LISTEN 0 4096 127.0.0.1:4369 0.0.0.0:*\n"
        )
    )

    report = verify_installation(remote, config(), public_client=unused_public)

    assert report.exit_status is ExitStatus.RELEASE
    assert report.checks[3].status == "failed"


def test_malformed_systemd_evidence_is_a_lifecycle_report_not_a_controller_error() -> None:
    """Calling splitlines on untrusted systemctl evidence must fail this."""

    remote = VerificationRemote(service_output=object())

    report = verify_installation(remote, config(), public_client=unused_public)

    assert report.exit_status is ExitStatus.RELEASE
    assert report.checks[0].status == "failed"


def test_local_poll_uses_the_configured_deadline_for_attempts_and_probe_timeouts() -> None:
    """A fixed three-probe loop would miss the configured readiness window."""

    clock = AdvancingClock()
    remote = VerificationRemote(
        local_responses=[
            "HTTP/1.1 503 Service Unavailable\r\nCache-Control: no-store\r\n\r\nunavailable"
        ] * 3
    )

    report = verify_installation(
        remote,
        config(readiness_timeout=3, connection_timeout=10),
        public_client=unused_public,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert report.exit_status is ExitStatus.READINESS
    assert remote.local_probe_timeouts == ["3", "2", "1"]
    assert clock.sleeps == [1.0, 1.0, 1.0]
    assert clock.now == 3.0


def test_local_poll_stops_when_an_injected_sleeper_cannot_advance_time() -> None:
    """A frozen test clock must not turn deadline polling into a tight loop."""

    remote = VerificationRemote(
        local_responses=[
            "HTTP/1.1 503 Service Unavailable\r\nCache-Control: no-store\r\n\r\nunavailable"
        ] * 3
    )

    report = verify_installation(
        remote,
        config(),
        public_client=unused_public,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    assert report.exit_status is ExitStatus.READINESS
    assert remote.local_probe_count == 1


def test_valid_unrelated_public_listener_does_not_make_topology_evidence_malformed() -> None:
    """The parser must accept numeric public listeners before applying port policy."""

    remote = VerificationRemote(
        ss_output=(
            "LISTEN 0 4096 127.0.0.1:4000 0.0.0.0:*\n"
            "LISTEN 0 4096 [::1]:6789 [::]:*\n"
            "LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*\n"
            "LISTEN 0 4096 203.0.113.10:22 0.0.0.0:*\n"
            "LISTEN 0 4096 [2001:db8::10]:443 [::]:*\n"
        )
    )
    public = RecordingPublicClient(
        HTTPResponse(200, b"ready", (("Cache-Control", "no-store"), ("Strict-Transport-Security", "max-age=1")))
    )

    report = verify_installation(remote, config(), public_client=public)

    assert report.exit_status is ExitStatus.OK
    assert report.checks[3].status is CheckStatus.PASSED


def test_public_duplicate_hsts_fields_and_directives_fail_closed() -> None:
    """Collapsing repeated security headers would hide ambiguous HSTS policy."""

    duplicate_header = RecordingPublicClient(
        HTTPResponse(
            200,
            b"ready",
            (
                ("Cache-Control", "no-store"),
                ("Strict-Transport-Security", "max-age=1"),
                ("Strict-Transport-Security", "max-age=2"),
            ),
        )
    )
    duplicate_directive = RecordingPublicClient(
        HTTPResponse(
            200,
            b"ready",
            (
                ("Cache-Control", "no-store"),
                ("Strict-Transport-Security", "max-age=1; max-age=2"),
            ),
        )
    )

    first = verify_installation(VerificationRemote(), config(), public_client=duplicate_header)
    second = verify_installation(VerificationRemote(), config(), public_client=duplicate_directive)

    assert first.exit_status is ExitStatus.READINESS
    assert first.checks[-2].status is CheckStatus.PASSED
    assert first.checks[-1].status is CheckStatus.FAILED
    assert second.exit_status is ExitStatus.READINESS
    assert second.checks[-1].status is CheckStatus.FAILED


def test_hsts_bare_duplicate_directive_fails_closed() -> None:
    """Ignoring a malformed duplicate max-age would accept an ambiguous policy."""

    public = RecordingPublicClient(
        HTTPResponse(
            200,
            b"ready",
            (
                ("Cache-Control", "no-store"),
                ("Strict-Transport-Security", "max-age=1; max-age"),
            ),
        )
    )

    report = verify_installation(VerificationRemote(), config(), public_client=public)

    assert report.checks[-2].status is CheckStatus.PASSED
    assert report.checks[-1].status is CheckStatus.FAILED
    assert report.exit_status is ExitStatus.READINESS


def test_journal_access_and_empty_evidence_have_distinct_fixed_summaries() -> None:
    """Journal failures must not be mistaken for clean startup evidence."""

    unavailable_remote = VerificationRemote(journal_output="unavailable\n")
    empty_remote = VerificationRemote(journal_output="empty\n")
    unavailable = verify_installation(unavailable_remote, config(), public_client=unused_public)
    empty = verify_installation(empty_remote, config(), public_client=unused_public)

    assert unavailable.checks[4].summary == "recent startup journal evidence is unavailable"
    assert empty.checks[4].summary == "recent startup journal evidence is empty"
    assert unavailable.exit_status is ExitStatus.RELEASE
    assert empty.exit_status is ExitStatus.RELEASE
    for remote in (unavailable_remote, empty_remote):
        journal_call = next(
            kwargs for argv, kwargs in remote.calls if len(argv) > 3 and argv[3] == "taskman-verify-journal"
        )
        assert journal_call["sudo"] is True


@pytest.mark.parametrize(
    ("exit_code", "canary"),
    ((28, "public-curl-slow-trickle-canary-056e"), (63, "public-curl-oversize-canary-056e")),
)
def test_public_runner_has_fixed_total_time_and_size_bounds_without_disclosing_output(
    exit_code: int, canary: str
) -> None:
    """Slow-trickle and oversized curl failures must remain bounded and secret-free."""

    runner = RecordingPublicRunner(CommandResult(exit_code, canary))

    report = verify_installation(
        VerificationRemote(),
        config(),
        public_runner=runner,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    assert report.exit_status is ExitStatus.READINESS
    assert runner.calls[0][:2] == ("curl", "--disable")
    assert "--max-time" in runner.calls[0]
    assert "--max-filesize" in runner.calls[0]
    assert canary not in report.human()
    assert canary not in str(report.to_mapping())


def test_public_runner_accepts_a_healthy_http2_status_line() -> None:
    """Rejecting an HTTP/2 Caddy response would turn healthy public readiness into a false failure."""

    runner = RecordingPublicRunner(
        CommandResult(
            0,
            "HTTP/2 200\r\n"
            "Cache-Control: no-store\r\n"
            "Strict-Transport-Security: max-age=31536000\r\n"
            "\r\n"
            "ready",
        )
    )

    report = verify_installation(
        VerificationRemote(),
        config(),
        public_runner=runner,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    assert report.exit_status is ExitStatus.OK
    assert all(check.status is CheckStatus.PASSED for check in report.checks[-2:])


def test_public_transport_stops_an_executable_oversize_stream_before_capture() -> None:
    """Capturing curl stdout before checking its size would allocate the complete hostile response."""

    result = verification._run_public_curl(
        (
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 262144); sys.stdout.flush()",
        ),
        1.0,
    )

    assert result.returncode == 63
    assert result.stdout == ""


def test_verification_reports_reject_incomplete_or_inconsistent_check_sets() -> None:
    """A report cannot claim success or a status category without matching checks."""

    with pytest.raises(ValueError):
        VerificationReport(ExitStatus.OK, RELEASE_ID, None, (), None)

    with pytest.raises(ValueError, match="ordered unique prefix"):
        VerificationReport(
            ExitStatus.READINESS,
            RELEASE_ID,
            None,
            (
                VerificationCheck("taskman-service", CheckStatus.PASSED, "service active"),
                VerificationCheck("taskman-service", CheckStatus.FAILED, "service inactive"),
            ),
            "inspect the fixed verification summaries and correct the reported host state before retrying",
        )


    with pytest.raises(ValueError, match="successful report"):
        VerificationReport(
            ExitStatus.OK,
            RELEASE_ID,
            None,
            tuple(
                VerificationCheck(name, CheckStatus.FAILED, "failed")
                for name in (
                    "taskman-service", "release-identity", "caddy-service", "listener-topology",
                    "startup-journal", "local-readiness", "public-readiness", "public-hsts",
                )
            ),
            None,
        )

    with pytest.raises(ValueError, match="readiness status"):
        VerificationReport(
            ExitStatus.READINESS,
            RELEASE_ID,
            None,
            (VerificationCheck("taskman-service", CheckStatus.FAILED, "service inactive"),),
            "inspect the fixed verification summaries and correct the reported host state before retrying",
        )

    with pytest.raises(ValueError, match="must not skip"):
        VerificationReport(
            ExitStatus.RELEASE,
            RELEASE_ID,
            None,
            (
                VerificationCheck("taskman-service", CheckStatus.SKIPPED, "service was not checked"),
                VerificationCheck("release-identity", CheckStatus.FAILED, "release identity failed"),
            ),
            "inspect the fixed verification summaries and correct the reported host state before retrying",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["checks"].append(value["checks"][-1].copy()),
        lambda value: value["checks"].__setitem__(3, {**value["checks"][3], "name": "unknown-check"}),
        lambda value: value.__setitem__("extra", True),
        lambda value: value.__setitem__("exit_status", "0"),
    ],
)
def test_verification_report_parser_rejects_malformed_or_duplicate_locked_evidence(
    mutation: object,
) -> None:
    """The lock-held verifier cannot bypass the exact report contract."""

    mapping = VerificationReport(
        ExitStatus.OK,
        RELEASE_ID,
        RELEASE_ID,
        tuple(
            VerificationCheck(name, CheckStatus.PASSED, f"{name} passed")
            for name in (
                "taskman-service",
                "release-identity",
                "caddy-service",
                "listener-topology",
                "startup-journal",
                "local-readiness",
                "public-readiness",
                "public-hsts",
            )
        ),
        None,
    ).to_mapping()
    mutation(mapping)  # type: ignore[operator]

    with pytest.raises((TypeError, ValueError)):
        VerificationReport.from_mapping(mapping)


def test_direct_report_rendering_uses_the_shared_secret_redaction_boundary() -> None:
    """A handwritten report renderer must not disclose a registered check summary value."""

    canary = "verification-direct-render-canary-018b"
    register_secret(canary)
    checks = tuple(
        VerificationCheck(name, CheckStatus.PASSED, canary if name == "taskman-service" else "passed")
        for name in (
            "taskman-service", "release-identity", "caddy-service", "listener-topology",
            "startup-journal", "local-readiness", "public-readiness", "public-hsts",
        )
    )
    report = VerificationReport(ExitStatus.OK, RELEASE_ID, None, checks, None)

    try:
        assert canary not in report.human()
        assert canary not in report.to_json()
    finally:
        clear_secrets()


class AdvancingClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RecordingPublicRunner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...], _timeout: float) -> CommandResult:
        self.calls.append(argv)
        return self.result


class FailingPublicClient:
    def __call__(self, _url: str, _timeout: int) -> HTTPResponse:
        raise OSError("test public DNS failure")


def unused_public(_url: str, _timeout: int) -> HTTPResponse:
    raise AssertionError("public health request must not occur")


def config(**overrides: object) -> EnvironmentConfig:
    values: dict[str, object] = {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43,
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@acme.tld",
            "readiness_timeout": 10,
            "connection_timeout": 10,
        }
    values.update(overrides)
    return EnvironmentConfig.model_validate(values)


def manifest() -> ArtifactManifest:
    return ArtifactManifest.from_mapping(
        {
            "schema_version": 1,
            "application": "taskman",
            "application_version": "0.2.0",
            "source_revision": "a" * 40,
            "release_id": RELEASE_ID,
            "built_at": "2026-09-05T09:00:00Z",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "27.3.4.6",
            "elixir_version": "1.18.3",
            "node_version": "22.22.1",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "migrations": [],
            "top_level": "taskman",
        }
    )


class RecordingPublicClient:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, timeout: int) -> HTTPResponse:
        self.calls.append((url, timeout))
        return self.response


class VerificationRemote:
    def __init__(
        self,
        *,
        local_responses: list[str] | None = None,
        ss_output: str | None = None,
        journal_output: str = "clean\n",
        service_output: object = "active\n123\n",
    ) -> None:
        release = ReleaseRecord(1, RELEASE_ID, "a" * 64, AT, AT, None, None, "no-change")
        activation = ActivationRecord(1, "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_ID, AT, None, "no-change")
        self.snapshot = {
            "schema_version": 1,
            "records": {
                "releases": [release.to_mapping()],
                "activations": [activation.to_mapping()],
                "backups": [],
                "adoptions": [],
            },
            "current_target": RELEASE_PATH.as_posix(),
            "manifests": {RELEASE_ID: manifest().to_mapping()},
            "dump_states": {},
            "warnings": [],
        }
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.executable_path = RELEASE_PATH / "erts-27.3.4.6/bin/beam.smp"
        self.local_responses = deque(local_responses or ["HTTP/1.1 200 OK\r\nCache-Control: no-store\r\n\r\nready"])
        self.local_probe_count = 0
        self.local_probe_timeouts: list[str] = []
        self.ss_output = ss_output or (
            "LISTEN 0 4096 127.0.0.1:4000 0.0.0.0:*\n"
            "LISTEN 0 4096 127.0.0.1:6789 0.0.0.0:*\n"
            "LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*\n"
            "LISTEN 0 4096 *:443 0.0.0.0:*\n"
        )
        self.journal_output = journal_output
        self.service_output = service_output

    def use_adopted_release(self, path: PurePosixPath) -> None:
        adoption = AdoptionRecord(1, ADOPTED_RELEASE_ID, AT, path, "b" * 64, "0.2.0", "unknown", "unknown", ())
        release = ReleaseRecord(1, ADOPTED_RELEASE_ID, None, AT, AT, None, None, "adopted")
        activation = ActivationRecord(1, "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, ADOPTED_RELEASE_ID, AT, None, "adopted")
        self.snapshot["records"] = {
            "releases": [release.to_mapping()],
            "activations": [activation.to_mapping()],
            "backups": [],
            "adoptions": [adoption.to_mapping()],
        }
        self.snapshot["current_target"] = path.as_posix()
        self.snapshot["manifests"] = {}

    def facts(self) -> HostFacts:
        return HostFacts(
            "ubuntu", "26.04", "amd64", "systemd", True, True, True, 22,
            2 * 1024**3, 20 * 1024**3, 20 * 1024**3, ("203.0.113.10",), (), (), ProvisioningMarkerState.ABSENT, CaddyState.ABSENT, (), (), (), (),
        )

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        command = tuple(argv)
        self.calls.append((command, kwargs))
        if command[:4] == ("sh", "-ceu", command[2], "taskman-lifecycle-snapshot"):
            return CommandResult(0, json.dumps(self.snapshot))
        if command[:2] == ("systemctl", "show"):
            return CommandResult(0, self.service_output)  # type: ignore[arg-type]
        if command[:2] == ("readlink", "-f"):
            return CommandResult(0, f"{self.executable_path}\n")
        if command[:2] == ("systemctl", "is-active"):
            return CommandResult(0, "active\n")
        if command[0] == "ss":
            return CommandResult(0, self.ss_output)
        if command[:2] == ("sh", "-ceu"):
            return CommandResult(0, self.journal_output)
        if command[0] == "curl":
            self.local_probe_count += 1
            self.local_probe_timeouts.append(command[command.index("--max-time") + 1])
            return CommandResult(0, self.local_responses.popleft())
        raise AssertionError(f"unexpected command: {command!r}")
