from __future__ import annotations

import pytest

from taskman_ops.cli import Invocation, dispatch
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.output import WorkflowResult
from taskman_ops.workflows.cleanup import cleanup
from tests.support.environments import environment_config


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
TARGET = {
    "identifier": "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
    "kind": "release",
    "path": "/opt/taskman/releases/0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
}


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.validate_operational_preflight",
        lambda *_args: None,
    )


def test_cleanup_inspects_confirms_executes_and_merges_final_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing the confirmed request or dropping a helper warning would hide destructive context."""

    requests: list[HostRequest] = []

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        requests.append(request)
        if request.operation == "discover":
            return HostResult(2, "discover", request.correlation_id, "succeeded", "observed", {"selected_release_id": RELEASE}, ("discovery warning",))
        if request.parameters["action"] == "inspect":
            assert request.expected_state == {"selected_release_id": RELEASE}
            assert request.parameters["targets"] == ()
            return HostResult(
                2,
                "cleanup",
                request.correlation_id,
                "succeeded",
                "inspected",
                {"selected_release_id": RELEASE, "targets": (TARGET,), "service_state": "running", "database_state": "ready"},
                ("inspection warning",),
            )
        assert request.expected_state == {"selected_release_id": RELEASE}
        assert request.parameters["targets"] == (TARGET,)
        return HostResult(
            2,
            "cleanup",
            request.correlation_id,
            "succeeded",
            "completed",
            {"selected_release_id": RELEASE, "changed": True, "service_state": "running", "database_state": "ready"},
            ("execution warning",),
        )

    monkeypatch.setattr("taskman_ops.workflows.cleanup.run_request", invoke)

    result = cleanup(object(), environment_config(), confirm=lambda _plan: True)

    assert [request.operation for request in requests] == ["discover", "cleanup", "cleanup"]
    assert [request.parameters.get("action") for request in requests[1:]] == ["inspect", "execute"]
    assert result.stage == "cleaned"
    assert result.exit_status is ExitStatus.OK
    assert result.facts["targets"] == (TARGET,)
    assert result.warnings == ("discovery warning", "inspection warning", "execution warning")


def test_cleanup_refuses_preflight_before_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.validate_operational_preflight",
        lambda *_args: (_ for _ in ()).throw(
            OpsError(ExitStatus.REMOTE_PREFLIGHT, "preflight", "unsafe")
        ),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.run_request",
        lambda *_args: pytest.fail("preflight refusal must precede inspection"),
    )

    result = cleanup(object(), environment_config())

    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.stage == "preflight-failed"


def test_cleanup_maps_a_final_refusal_to_safety_and_preserves_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final cleanup refusal remains a safety failure after a valid confirmation plan."""

    def invoke(_remote: object, request: HostRequest) -> HostResult:
        if request.operation == "discover":
            return HostResult(2, "discover", request.correlation_id, "succeeded", "observed", {"selected_release_id": RELEASE}, ("discovery warning",))
        if request.parameters["action"] == "inspect":
            return HostResult(
                2,
                "cleanup",
                request.correlation_id,
                "succeeded",
                "inspected",
                {"selected_release_id": RELEASE, "targets": (TARGET,), "service_state": "running", "database_state": "ready"},
                ("inspection warning",),
            )
        return HostResult(2, "cleanup", request.correlation_id, "refused", "targets changed", {}, ("execution warning",))

    monkeypatch.setattr("taskman_ops.workflows.cleanup.run_request", invoke)

    result = cleanup(object(), environment_config(), confirm=lambda _plan: True)

    assert result.stage == "safety-refused"
    assert result.exit_status is ExitStatus.SAFETY
    assert result.warnings == ("discovery warning", "inspection warning", "execution warning")


def test_cleanup_dispatch_connects_and_routes_to_the_public_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI cleanup command must not bypass the concrete confirmation workflow."""

    config = environment_config()
    remote = object()
    expected = WorkflowResult("cleanup", "production", False, "planned", {"targets": ()})
    seen: list[tuple[object, EnvironmentConfig, bool]] = []
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda name: config if name == "production" else None)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda value: remote if value is config else None)
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.cleanup",
        lambda actual_remote, actual_config, *, dry_run: seen.append((actual_remote, actual_config, dry_run)) or expected,
    )

    result = dispatch(Invocation(command="cleanup", environment="production", dry_run=True))

    assert result is expected
    assert seen == [(remote, config, True)]
