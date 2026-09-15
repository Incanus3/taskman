from __future__ import annotations

from io import StringIO
from pathlib import Path
import json

import pytest

from taskman_ops.releases.artifacts import DeploymentTarget
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import OTP_VERSION
from taskman_ops.cli import APPROVED_COMMANDS, Invocation, build_parser, dispatch, main, parse_invocation
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import WorkflowResult, register_secret
from tests.support.secrets import no_registered_secrets_between_tests as no_registered_secrets_between_tests


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


def test_deploy_help_marks_manual_adoption_as_unsupported(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(["deploy", "--help"])

    assert raised.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "unsupported; manual adoption is refused" in help_text


def test_root_help_makes_every_operator_workflow_discoverable() -> None:
    help_text = build_parser().format_help()

    for summary in (
        "build a target-compatible release artifact",
        "provision a supported clean host",
        "deploy an immutable release",
        "verify the deployed topology",
        "list managed releases",
        "list retained backups",
        "create a validated local backup",
        "select a compatible installed release",
        "restore an exact retained backup",
        "create an administrator through an SSH TTY",
        "remove only eligible managed artifacts",
    ):
        assert summary in help_text
    assert "without mutating remote state" in help_text


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


@pytest.mark.parametrize("command", ("deploy", "provision"))
def test_deployment_flags_are_scoped_to_deploy_and_provision(command: str) -> None:
    invocation = parse_invocation(
        [command, "production", "--yes", "--allow-dirty", "--allow-downgrade"]
    )

    assert invocation.yes is True
    assert invocation.allow_dirty is True
    assert invocation.allow_downgrade is True


def test_build_allows_only_dirty_source_permission() -> None:
    invocation = parse_invocation(["build", "--allow-dirty"])

    assert invocation.allow_dirty is True
    assert invocation.yes is False
    assert invocation.allow_downgrade is False


@pytest.mark.parametrize("flag", ("--reapply", "--replace-unfinished"))
def test_restore_only_recovery_flags_are_typed(flag: str) -> None:
    invocation = parse_invocation(
        ["restore", "production", "backup-" + "a" * 32, flag]
    )

    assert invocation.reapply is (flag == "--reapply")
    assert invocation.replace_unfinished is (flag == "--replace-unfinished")


def test_restore_recovery_flags_are_mutually_exclusive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(
        [
            "restore",
            "production",
            "backup-" + "a" * 32,
            "--reapply",
            "--replace-unfinished",
        ]
    ) == ExitStatus.INVALID
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    (
        ["build", "--yes"],
        ["verify", "production", "--allow-dirty"],
        ["backup", "production", "--allow-downgrade"],
        ["deploy", "production", "--reapply"],
        ["cleanup", "production", "--replace-unfinished"],
    ),
)
def test_deployment_consent_flags_are_rejected_outside_their_command_scope(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == ExitStatus.INVALID
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize("command,listing", [("rollback", "releases"), ("restore", "backups")])
@pytest.mark.parametrize("identifier", ["not-an-id", "../secret-input", "", "backup-" + "a" * 32 + "\n"])
@pytest.mark.parametrize("json_output", [False, True])
def test_invalid_recovery_id_is_rejected_before_configuration_or_ssh(
    command: str, listing: str, identifier: str, json_output: bool,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda *_: pytest.fail("invalid IDs must not load configuration"))
    monkeypatch.setattr("taskman_ops.remote.connect", lambda *_: pytest.fail("invalid IDs must not attempt SSH"))

    argv = [command, "production", identifier] + (["--json"] if json_output else [])
    assert main(argv) == ExitStatus.INVALID
    captured = capsys.readouterr()
    assert captured.out == ""
    if identifier:
        assert identifier not in captured.err
    if json_output:
        report = json.loads(captured.err)
        assert report["error"]["code"] == 2
        assert report["changed"] is False
        assert listing in report["next_action"]
    else:
        assert "error (2)" in captured.err
        assert listing in captured.err


@pytest.mark.parametrize("command,identifier", [
    ("rollback", build_release_id("0.2.0", "a" * 40, artifact_sha256="a" * 64, source_dirty=False, otp_version=OTP_VERSION)),
    ("restore", "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
])
def test_dispatch_routes_valid_recovery_ids_to_the_gated_workflow(
    command: str, identifier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid recovery identities still reach their safety-gated workflows."""

    environment = object()
    remote = object()
    seen: list[tuple[object, object, str, bool]] = []
    expected = WorkflowResult(command, "production", False, "confirmation-cancelled", {})

    monkeypatch.setattr("taskman_ops.config.load_environment", lambda _name: environment)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda _environment: remote)
    monkeypatch.setattr(
        f"taskman_ops.workflows.{command}.{command}",
        lambda actual_remote, actual_environment, release_id, *, dry_run, **_kwargs: seen.append((actual_remote, actual_environment, release_id, dry_run)) or expected,
    )

    result = dispatch(parse_invocation([command, "production", identifier]))

    assert result is expected
    assert seen == [(remote, environment, identifier, False)]


def test_dispatch_forwards_restore_recovery_mode_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = object()
    remote = object()
    backup_id = "backup-" + "a" * 32
    seen: list[dict[str, object]] = []
    expected = WorkflowResult("restore", "production", False, "planned", {})
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda _name: environment)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda _environment: remote)
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.restore",
        lambda actual_remote, actual_environment, actual_backup, **kwargs: (
            seen.append(
                {
                    "remote": actual_remote,
                    "environment": actual_environment,
                    "backup_id": actual_backup,
                    **kwargs,
                }
            )
            or expected
        ),
    )

    result = dispatch(
        Invocation(
            "restore",
            environment="production",
            backup_id=backup_id,
            reapply=True,
        )
    )

    assert result is expected
    assert seen == [
        {
            "remote": remote,
            "environment": environment,
            "backup_id": backup_id,
            "dry_run": False,
            "replace_unfinished": False,
            "reapply": True,
        }
    ]


def test_dispatch_routes_create_admin_to_the_interactive_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = object()
    remote = object()
    seen: list[tuple[object, object, bool]] = []
    expected = WorkflowResult("create-admin", "production", False, "planned", {})

    monkeypatch.setattr("taskman_ops.config.load_environment", lambda _name: environment)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda _environment: remote)
    monkeypatch.setattr(
        "taskman_ops.workflows.create_admin.run_create_admin",
        lambda actual_remote, actual_environment, *, dry_run: (
            seen.append((actual_remote, actual_environment, dry_run)) or expected
        ),
    )

    result = dispatch(Invocation("create-admin", environment="production", dry_run=True))

    assert result is expected
    assert seen == [(remote, environment, True)]


def test_main_propagates_an_interactive_remote_process_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = WorkflowResult(
        "create-admin",
        "production",
        False,
        "administrator-command-failed",
        {"remote_status": 37},
        exit_status=ExitStatus.RELEASE,
        process_status=37,
    )

    assert main(["create-admin", "production"], dispatch_fn=lambda _invocation: result) == 37
    assert "administrator-command-failed" in capsys.readouterr().out


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
            interactive=False,
        )
    ]
    captured = capsys.readouterr()
    assert '"schema_version": 1' in captured.out
    assert canary not in captured.out
    assert canary not in captured.err
    assert captured.err == ""


@pytest.mark.parametrize(
    ("argv", "stdin"),
    (
        (["deploy", "production", "--json"], StringIO()),
        (["deploy", "production"], StringIO()),
    ),
)
def test_main_refuses_unattended_deploy_without_yes_without_dispatching_or_prompting(
    argv: list[str], stdin: StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON and non-TTY execution cannot turn an implicit prompt into consent."""

    assert main(
        argv,
        stdin=stdin,
        dispatch_fn=lambda _invocation: pytest.fail("missing consent must stop before dispatch"),
    ) == ExitStatus.SAFETY

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "confirmation" in captured.err


def test_public_verify_dispatch_uses_optional_current_release_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public verify command must not synthesize a deploy-only release assertion."""

    environment = object()
    remote = object()
    seen: list[tuple[object, object, object]] = []
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda _name: environment)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda _environment: remote)
    monkeypatch.setattr(
        "taskman_ops.workflows.verify.run_verify",
        lambda actual_remote, actual_environment, expected_release_id=None: seen.append(
            (actual_remote, actual_environment, expected_release_id)
        )
        or WorkflowResult(command="verify", environment="production", changed=False, stage="verified", facts={}),
    )

    result = dispatch(Invocation(command="verify", environment="production"))

    assert result.stage == "verified"
    assert seen == [(remote, environment, None)]


def test_deploy_resolves_the_artifact_before_connecting_and_reports_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.workflows.support import deployment_artifact

    environment = object()
    remote = object()
    artifact = deployment_artifact(tmp_path)
    events: list[str] = []

    def load(_name: str) -> object:
        events.append("load")
        return environment

    def resolve(_repo: Path, supplied: Path | None, **_kwargs: object) -> DeploymentTarget:
        events.append(f"resolve:{supplied}")
        return DeploymentTarget(artifact=artifact, release_record=None, source="cached")  # type: ignore[arg-type]

    def connect(_environment: object) -> object:
        events.append("connect")
        return remote

    def run_deploy(actual_remote: object, actual_environment: object, actual_target: object, **_kwargs: object) -> WorkflowResult:
        events.append("deploy")
        assert (actual_remote, actual_environment, actual_target.release_id) == (remote, environment, artifact.manifest.release_id)
        return WorkflowResult("deploy", "production", False, "planned", {"candidate_release_id": "candidate"})

    monkeypatch.setattr("taskman_ops.config.load_environment", load)
    monkeypatch.setattr("taskman_ops.releases.artifacts.resolve_deploy_target", resolve)
    monkeypatch.setattr("taskman_ops.remote.connect", connect)
    monkeypatch.setattr("taskman_ops.workflows.deploy.deploy", run_deploy)

    result = dispatch(Invocation(command="deploy", environment="production", artifact=Path("release.tar.gz")))

    assert events == ["load", "resolve:release.tar.gz", "connect", "deploy"]
    assert result.facts["artifact_source"] == "cached"


def test_deploy_does_not_connect_when_artifact_resolution_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = object()
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda _name: environment)
    error = OpsError(
        ExitStatus.LOCAL_PREREQUISITE,
        "artifact",
        "artifact resolution failed",
        changed=False,
    )
    monkeypatch.setattr("taskman_ops.releases.artifacts.resolve_deploy_target", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr("taskman_ops.remote.connect", lambda _environment: pytest.fail("SSH must wait for artifact resolution"))

    with pytest.raises(OpsError) as raised:
        dispatch(Invocation(command="deploy", environment="production", artifact=Path("bad.tar.gz")))

    assert raised.value is error


def test_automatic_deploy_refresh_uses_the_production_authority_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean-source retry must repeat host authority resolution, not only replace a target stub."""
    from taskman_ops.releases.artifacts import CleanInputs
    from taskman_ops.releases.manifests import OTP_VERSION
    from taskman_ops.workflows.deploy import DeploymentAdmissionAuthority
    from tests.workflows.support import deployment_artifact

    environment = object()
    remote = object()
    artifact = deployment_artifact(tmp_path)
    first = CleanInputs("a" * 40, "0.2.0", "ubuntu26.04", "amd64", OTP_VERSION, "1.20.4", "22.22.1", "2.5.1", "3.24.0", "tag", "b" * 64, "taskman", ())
    second = CleanInputs(**{**first.__dict__, "source_revision": "c" * 40})
    identified = iter((first, second))
    resolution_inputs: list[CleanInputs] = []
    authority_calls: list[object] = []

    monkeypatch.setattr("taskman_ops.config.load_environment", lambda _name: environment)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda _environment: remote)
    monkeypatch.setattr("taskman_ops.releases.artifacts.identify_clean_inputs", lambda _repo: next(identified))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.deployment_admission_authority",
        lambda actual_remote, actual_environment: authority_calls.append((actual_remote, actual_environment))
        or DeploymentAdmissionAuthority((), None, None),
    )
    monkeypatch.setattr(
        "taskman_ops.releases.artifacts.resolve_deploy_target",
        lambda _repo, _artifact, **kwargs: resolution_inputs.append(kwargs["clean_inputs"])
        or DeploymentTarget(artifact=artifact, release_record=None, source="built"),
    )

    def run_deploy(_remote, _environment, _target, **kwargs):
        kwargs["refresh_clean_target"]()
        return WorkflowResult("deploy", "production", False, "planned", {})

    monkeypatch.setattr("taskman_ops.workflows.deploy.deploy", run_deploy)

    dispatch(Invocation(command="deploy", environment="production"))

    assert resolution_inputs == [first, second]
    assert authority_calls == [(remote, environment), (remote, environment)]


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
