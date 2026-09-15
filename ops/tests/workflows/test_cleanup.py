from __future__ import annotations

import json

import pytest
from taskman_ops.cli import Invocation, dispatch
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostRequest, HostResult, ProtocolError
from taskman_ops.output import WorkflowResult
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.workflows import cleanup as cleanup_module
from tests.support.environments import environment_config

RELEASE = build_release_id(
    "0.2.0", "a" * 40, artifact_sha256="c" * 64, source_dirty=False
)
FACTS = {
    "selected_release_id": RELEASE,
    "last_successful_selection_id": "selection-" + "d" * 64 + ".json",
    "backup_protection_sha256": "e" * 64,
    "restore_target_sha256": None,
}


def _target(index: int) -> dict[str, object]:
    name = f".release-{index:03d}"
    return {
        "kind": "temporary",
        "identifier": name,
        "path": f"/opt/taskman/releases/{name}",
    }


def _success(request: HostRequest, completed=(), *, warnings=()) -> HostResult:
    return HostResult(
        3,
        "cleanup",
        request.correlation_id,
        "succeeded",
        "completed",
        {
            "mutation_state": "changed" if completed else "unchanged",
            "exit_code": 0,
            "failed_boundary": None,
            "observations": dict(FACTS),
            "unavailable_fields": (),
            "inspection_error": None,
            "report": None,
            "completed_targets": tuple(completed),
        },
        tuple(warnings),
    )


def _page(
    request: HostRequest,
    all_targets: list[dict[str, object]],
    start: int,
    *,
    warnings=(),
) -> HostResult:
    page = all_targets[start : start + 64]
    digest = cleanup_module._inventory_sha256(FACTS, all_targets)
    more = start + len(page) < len(all_targets)
    cursor = (
        {
            "inventory_sha256": digest,
            "after_id": json.dumps(
                cleanup_module._identity(page[-1]), separators=(",", ":")
            ),
        }
        if more
        else None
    )
    return HostResult(
        3,
        "cleanup",
        request.correlation_id,
        "succeeded",
        "inspected",
        {
            **FACTS,
            "targets": tuple(page),
            "inventory_sha256": digest,
            "next_cursor": cursor,
        },
        tuple(warnings),
    )


@pytest.fixture(autouse=True)
def _valid_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cleanup_module, "validate_cleanup_preflight", lambda *_args: None
    )


def test_cleanup_collects_all_pages_confirms_once_and_executes_bounded_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [_target(index) for index in range(65)]
    requests: list[HostRequest] = []
    confirmations: list[object] = []

    def invoke(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        requests.append(request)
        if request.parameters["action"] == "inspect":
            cursor = request.parameters["cursor"]
            return _page(
                request,
                targets,
                0 if cursor is None else 64,
                warnings=("page warning",),
            )
        return _success(
            request, request.parameters["targets"], warnings=("batch warning",)
        )

    monkeypatch.setattr(cleanup_module, "run_request", invoke)
    result = cleanup_module.cleanup(
        object(),
        environment_config(),
        confirm=lambda plan: confirmations.append(plan) or True,
    )

    assert [request.parameters["action"] for request in requests] == [
        "inspect",
        "inspect",
        "execute",
        "execute",
    ]
    assert all(
        request.expected_state == {} and request.parameters["targets"] == ()
        for request in requests[:2]
    )
    assert [len(request.parameters["targets"]) for request in requests[2:]] == [64, 1]
    assert all(
        request.expected_state == FACTS and request.parameters["cursor"] is None
        for request in requests[2:]
    )
    assert len(confirmations) == 1
    assert result.stage == "cleaned"
    assert result.facts["starting_state"] == FACTS
    assert tuple(result.facts["completed_targets"]) == tuple(targets)
    assert result.warnings == ("page warning", "batch warning")


def test_cleanup_splits_execution_at_the_encoded_request_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [_target(index) for index in range(5)]
    batch_sizes: list[int] = []
    original = cleanup_module.encode_request

    def bounded(request: HostRequest) -> bytes:
        if (
            request.parameters["action"] == "execute"
            and len(request.parameters["targets"]) > 2
        ):
            raise ProtocolError("simulated byte budget")
        return original(request)

    def invoke(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        if request.parameters["action"] == "inspect":
            return _page(request, targets, 0)
        batch_sizes.append(len(request.parameters["targets"]))
        return _success(request, request.parameters["targets"])

    monkeypatch.setattr(cleanup_module, "encode_request", bounded)
    monkeypatch.setattr(cleanup_module, "run_request", invoke)
    result = cleanup_module.cleanup(
        object(), environment_config(), confirm=lambda _plan: True
    )

    assert batch_sizes == [2, 2, 1]
    assert result.stage == "cleaned"


def test_later_batch_loss_preserves_first_batch_completion_without_reusing_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [_target(index) for index in range(65)]
    executes = 0

    def invoke(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        nonlocal executes
        if request.parameters["action"] == "inspect":
            return _page(
                request, targets, 0 if request.parameters["cursor"] is None else 64
            )
        executes += 1
        if executes == 1:
            return _success(request, request.parameters["targets"])
        return HostResult(
            3,
            "cleanup",
            request.correlation_id,
            "retryable",
            "reply evidence is unavailable",
            {
                "mutation_state": "unknown",
                "exit_code": 10,
                "failed_boundary": "cleanup",
                "observations": {key: None for key in FACTS},
                "unavailable_fields": tuple(sorted(FACTS)),
                "inspection_error": "inspection-failed",
                "report": None,
                "completed_targets": (),
            },
            (),
        )

    monkeypatch.setattr(cleanup_module, "run_request", invoke)
    result = cleanup_module.cleanup(
        object(), environment_config(), confirm=lambda _plan: True
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.changed is True
    assert result.facts["starting_state"] == FACTS
    assert tuple(result.facts["completed_targets"]) == tuple(targets[:64])
    assert result.facts["mutation_state"] == "changed"
    assert result.facts["observations"] == {key: None for key in FACTS}


def test_cleanup_refuses_inventory_drift_between_pages_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [_target(index) for index in range(65)]
    calls = 0

    def invoke(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        nonlocal calls
        calls += 1
        result = _page(request, targets, 0 if calls == 1 else 64)
        if calls == 2:
            result = HostResult(
                3,
                "cleanup",
                request.correlation_id,
                "succeeded",
                "inspected",
                {**result.state, "inventory_sha256": "f" * 64},
                (),
            )
        return result

    monkeypatch.setattr(cleanup_module, "run_request", invoke)
    result = cleanup_module.cleanup(
        object(),
        environment_config(),
        confirm=lambda _plan: pytest.fail("drifted inventory must not be confirmed"),
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_dry_run_collects_plan_without_confirmation_or_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [_target(1)]
    actions: list[str] = []

    def invoke(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        actions.append(str(request.parameters["action"]))
        return _page(request, targets, 0)

    monkeypatch.setattr(cleanup_module, "run_request", invoke)
    result = cleanup_module.cleanup(
        object(),
        environment_config(),
        dry_run=True,
        confirm=lambda _plan: pytest.fail("dry run must not confirm"),
    )
    assert actions == ["inspect"]
    assert result.stage == "planned"
    assert result.facts["targets"] == tuple(targets)
    assert result.facts["starting_state"] is None


def test_empty_plan_never_prompts_and_keeps_starting_state_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cleanup_module,
        "run_request",
        lambda _remote, request, **_kwargs: _page(request, [], 0),
    )
    result = cleanup_module.cleanup(
        object(),
        environment_config(),
        confirm=lambda _plan: pytest.fail("empty cleanup must not confirm"),
    )
    assert result.stage == "nothing-to-clean"
    assert result.facts["starting_state"] is None
    assert result.facts["mutation_state"] == "unchanged"


def test_cleanup_refuses_preflight_before_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cleanup_module,
        "validate_cleanup_preflight",
        lambda *_args: (_ for _ in ()).throw(
            OpsError(ExitStatus.REMOTE_PREFLIGHT, "preflight", "unsafe")
        ),
    )
    monkeypatch.setattr(
        cleanup_module,
        "run_request",
        lambda *_args, **_kwargs: pytest.fail(
            "preflight refusal must precede inspection"
        ),
    )
    result = cleanup_module.cleanup(object(), environment_config())
    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.stage == "preflight-failed"


def test_cleanup_dispatch_connects_and_routes_to_public_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = environment_config()
    remote = object()
    expected = WorkflowResult(
        "cleanup", "production", False, "planned", {"targets": ()}
    )
    seen: list[tuple[object, EnvironmentConfig, bool]] = []
    monkeypatch.setattr(
        "taskman_ops.config.load_environment",
        lambda name: config if name == "production" else None,
    )
    monkeypatch.setattr(
        "taskman_ops.remote.connect", lambda value: remote if value is config else None
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.cleanup",
        lambda actual_remote, actual_config, *, dry_run: (
            seen.append((actual_remote, actual_config, dry_run)) or expected
        ),
    )
    result = dispatch(
        Invocation(command="cleanup", environment="production", dry_run=True)
    )
    assert result is expected
    assert seen == [(remote, config, True)]
