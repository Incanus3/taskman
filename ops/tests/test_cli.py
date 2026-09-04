from __future__ import annotations

from pathlib import Path

import pytest

from taskman_ops.cli import APPROVED_COMMANDS, Invocation, main, parse_invocation
from taskman_ops.errors import ExitStatus
from taskman_ops.output import WorkflowResult, clear_secrets, register_secret


@pytest.fixture(autouse=True)
def no_registered_secrets_between_tests() -> None:
    clear_secrets()
    yield
    clear_secrets()


def test_every_approved_command_has_a_typed_invocation() -> None:
    cases = [
        (["build"], ("build", None, None, None)),
        (["provision", "production"], ("provision", "production", None, None)),
        (["deploy", "production"], ("deploy", "production", None, None)),
        (["verify", "production"], ("verify", "production", None, None)),
        (["releases", "production"], ("releases", "production", None, None)),
        (["backups", "production"], ("backups", "production", None, None)),
        (["backup", "production"], ("backup", "production", None, None)),
        (["rollback", "production", "0.2.0-deadbeef"],
         ("rollback", "production", "0.2.0-deadbeef", None)),
        (["restore", "production", "backup-20260904"],
         ("restore", "production", None, "backup-20260904")),
        (["create-admin", "production"], ("create-admin", "production", None, None)),
        (["cleanup", "production"], ("cleanup", "production", None, None)),
    ]

    assert tuple(APPROVED_COMMANDS) == (
        "build",
        "provision",
        "deploy",
        "verify",
        "releases",
        "backups",
        "backup",
        "rollback",
        "restore",
        "create-admin",
        "cleanup",
    )

    for argv, (command, environment, release_id, backup_id) in cases:
        invocation = parse_invocation(argv)
        assert isinstance(invocation, Invocation)
        assert invocation.command == command
        assert invocation.environment == environment
        assert invocation.release_id == release_id
        assert invocation.backup_id == backup_id
        assert invocation.artifact is None
        assert invocation.json is False
        assert invocation.dry_run is False


@pytest.mark.parametrize(
    "argv",
    [
        ["provision"],
        ["deploy"],
        ["verify"],
        ["releases"],
        ["backups"],
        ["backup"],
        ["create-admin"],
        ["cleanup"],
        ["rollback", "production"],
        ["restore", "production"],
    ],
)
def test_missing_identifier_returns_status_two(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(argv) == ExitStatus.INVALID
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["unknown", "production"],
        ["verify", "production", "unexpected"],
        ["rollback", "production", "release", "extra"],
        ["restore", "production", "backup", "extra"],
        ["build", "production"],
        ["verify", "production", "--force"],
    ],
)
def test_unknown_or_extra_identifier_is_rejected(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(argv) == ExitStatus.INVALID
    assert "usage:" in capsys.readouterr().err


def test_artifact_option_is_typed_for_artifact_commands() -> None:
    for command in ("provision", "deploy"):
        invocation = parse_invocation([command, "production", "--artifact", "tmp/release.tar"])
        assert invocation.artifact == Path("tmp/release.tar")


def test_json_and_dry_run_reach_dispatch_invocation(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[Invocation] = []
    canary = "taskman-cli-output-canary-2ea9d4"
    register_secret(canary)

    def dispatch(invocation: Invocation):
        seen.append(invocation)
        return {
            "command": invocation.command,
            "environment": invocation.environment,
            "changed": False,
            "stage": "complete",
            "facts": {"controller_secret": canary},
            "warnings": (f"warning-{canary}",),
            "next_action": f"next-{canary}",
        }

    assert main(["verify", "production", "--json", "--dry-run"], dispatch_fn=dispatch) == 0
    assert seen == [
        Invocation(
            command="verify",
            environment="production",
            json=True,
            dry_run=True,
        )
    ]
    captured = capsys.readouterr()
    assert '"schema_version": 1' in captured.out
    assert canary not in captured.out
    assert canary not in captured.err
    assert captured.err == ""


def test_malformed_dispatch_result_maps_to_stable_secret_free_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = "taskman-malformed-result-canary-1d3a6c"
    register_secret(canary)

    def dispatch(_invocation: Invocation):
        return {"message": f"malformed {canary}"}

    assert main(["verify", "production"], dispatch_fn=dispatch) == ExitStatus.LOCAL_PREREQUISITE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert canary not in captured.out
    assert canary not in captured.err
    assert "Traceback" not in captured.err
    assert "controller operation failed" in captured.err


def test_rendering_failure_maps_to_stable_secret_free_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = "taskman-rendering-canary-7c48be"
    register_secret(canary)

    def render_failure(_result: WorkflowResult) -> str:
        raise RuntimeError(f"renderer leaked {canary}")

    monkeypatch.setattr("taskman_ops.cli.render_human", render_failure)
    assert main(["verify", "production"], dispatch_fn=lambda _invocation: WorkflowResult(
        command="verify",
        environment="production",
        changed=False,
        stage="complete",
        facts={},
    )) == ExitStatus.LOCAL_PREREQUISITE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert canary not in captured.out
    assert canary not in captured.err
    assert "Traceback" not in captured.err


def test_cyclic_result_facts_map_to_stable_secret_free_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    facts: dict[str, object] = {}
    facts["self"] = facts

    result = WorkflowResult(
        command="verify",
        environment="production",
        changed=False,
        stage="complete",
        facts=facts,
    )
    assert main(["verify", "production", "--json"], dispatch_fn=lambda _invocation: result) == ExitStatus.LOCAL_PREREQUISITE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err
    assert "Traceback" not in captured.err


def test_each_stable_exit_status_has_the_documented_numeric_code() -> None:
    assert {status.name: status.value for status in ExitStatus} == {
        "OK": 0,
        "INVALID": 2,
        "LOCAL_PREREQUISITE": 3,
        "SECRET": 4,
        "REMOTE_PREFLIGHT": 5,
        "BACKUP": 6,
        "MIGRATION": 7,
        "RELEASE": 8,
        "READINESS": 9,
        "SAFETY": 10,
        "RESTORE": 11,
        "LOCKED": 12,
    }
