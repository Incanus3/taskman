from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskman_ops.cli import Invocation, dispatch, parse_invocation
from taskman_ops.config import EnvironmentConfig
from taskman_ops.manifests import ArtifactManifest, BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, VerifiedArtifact
from taskman_ops.output import WorkflowResult
from taskman_ops.workflows import DiscoveryResult
from taskman_ops.workflows.cleanup import cleanup
from taskman_ops.workflows.provision import ProvisionCapabilities, provision
from taskman_ops.workflows.restore import restore

from test_config import valid_environment


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CANDIDATE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FIRST = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
SECOND = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)

MUTATING_COMMANDS = (
    "build",
    "provision",
    "deploy",
    "backup",
    "rollback",
    "restore",
    "create-admin",
    "cleanup",
)


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in ("backup", "cleanup", "deploy", "restore", "rollback", "create_admin"):
        monkeypatch.setattr(
            f"taskman_ops.workflows.{module}.validate_operational_preflight",
            lambda *_args: object(),
            raising=False,
        )
READ_ONLY_COMMANDS = ("verify", "releases", "backups")


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(name="production"))


def _artifact(tmp_path: Path, *, release_id: str = CANDIDATE) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"release")
    revision = ("b" if release_id == CANDIDATE else "a") * 40
    manifest = ArtifactManifest(
        2,
        "taskman",
        "0.2.0",
        revision,
        release_id,
        SECOND,
        "ubuntu26.04",
        "amd64",
        "27.3.4.6",
        "1.18.3",
        "22.22.1",
        BUILDER_BASE_TAG,
        BUILDER_BASE_DIGEST,
        (),
        "taskman",
    )
    return VerifiedArtifact(
        archive,
        tmp_path / "taskman.manifest.json",
        tmp_path / "taskman.tar.gz.sha256",
        "c" * 64,
        manifest,
    )


def _manifest(release_id: str) -> dict[str, object]:
    revision = ("b" if release_id == CANDIDATE else "a") * 40
    return ArtifactManifest(
        2,
        "taskman",
        "0.2.0",
        revision,
        release_id,
        SECOND,
        "ubuntu26.04",
        "amd64",
        "27.3.4.6",
        "1.18.3",
        "22.22.1",
        BUILDER_BASE_TAG,
        BUILDER_BASE_DIGEST,
        (),
        "taskman",
    ).to_mapping()


@pytest.mark.parametrize(
    ("command", "identifiers"),
    [
        ("build", ()),
        ("provision", ("production",)),
        ("deploy", ("production",)),
        ("backup", ("production",)),
        (
            "rollback",
            ("production", "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"),
        ),
        ("restore", ("production", "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")),
        ("create-admin", ("production",)),
        ("cleanup", ("production",)),
    ],
)
def test_every_mutating_command_accepts_the_common_dry_run_flag(
    command: str,
    identifiers: tuple[str, ...],
) -> None:
    invocation = parse_invocation([command, *identifiers, "--dry-run"])

    assert invocation.command == command
    assert invocation.dry_run is True


def test_provision_receives_the_complete_dry_run_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Invocation] = []
    expected = WorkflowResult("provision", "production", False, "planned", {})
    monkeypatch.setattr(
        "taskman_ops.workflows.provision.provision",
        lambda invocation: seen.append(invocation) or expected,
    )

    result = dispatch(Invocation("provision", environment="production", dry_run=True))

    assert result is expected
    assert seen == [Invocation("provision", environment="production", dry_run=True)]


def test_explicit_backup_receives_dry_run_after_environment_and_ssh_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    remote = object()
    seen: list[tuple[object, object, bool]] = []
    expected = WorkflowResult("backup", "production", False, "planned", {})
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda _name: config)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda _config: remote)
    monkeypatch.setattr(
        "taskman_ops.workflows.backup.run_backup",
        lambda actual_remote, context, *, dry_run: (
            seen.append((actual_remote, context, dry_run)) or expected
        ),
    )

    result = dispatch(Invocation("backup", environment="production", dry_run=True))

    assert result is expected
    assert seen[0][0] is remote
    assert seen[0][1] is config
    assert seen[0][2] is True


@pytest.mark.parametrize("command", READ_ONLY_COMMANDS)
def test_read_only_commands_remain_the_same_with_dry_run(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    remote = object()
    calls: list[tuple[str, tuple[object, EnvironmentConfig]]] = []
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda _name: config)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda _config: remote)

    if command == "verify":
        expected = WorkflowResult("verify", "production", False, "verified", {})
        monkeypatch.setattr(
            "taskman_ops.workflows.verify.run_verify",
            lambda actual_remote, actual_config: (
                calls.append(("verify", (actual_remote, actual_config))) or expected
            ),
        )
    else:
        result = DiscoveryResult(({"identifier": "one"},), ("read-only warning",))
        monkeypatch.setattr(
            f"taskman_ops.workflows.{command}.list_{command}",
            lambda actual_remote, actual_config: (
                calls.append((command, (actual_remote, actual_config))) or result
            ),
        )

    normal = dispatch(Invocation(command, environment="production"))
    dry = dispatch(Invocation(command, environment="production", dry_run=True))

    assert normal.command == command
    assert dry.command == command
    assert normal.changed is False
    assert dry.changed is False
    assert normal.stage == dry.stage
    assert normal.facts == dry.facts
    assert calls == [(command, (remote, config)), (command, (remote, config))]


def test_build_dry_run_may_create_the_same_local_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        "taskman_ops.build.build_release",
        lambda repo, root: calls.append((repo, root)) or artifact,
    )

    result = dispatch(Invocation("build", dry_run=True))

    assert len(calls) == 1
    assert result.stage == "built"
    assert result.facts["release_id"] == CANDIDATE


def test_provision_dry_run_performs_local_checks_and_remote_discovery_without_confirmation_or_mutation(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class Remote:
        def close(self) -> None:
            events.append("close")

    @dataclass(frozen=True)
    class Caddy:
        caddyfile: str

    remote = Remote()

    def mutation(name: str):
        return lambda *_args, **_kwargs: pytest.fail(
            f"provision dry run reached {name} mutation"
        )

    capabilities = ProvisionCapabilities(
        load_environment=lambda _name: events.append("config") or _config(),
        decrypt_secrets=lambda _name: events.append("secrets") or SimpleNamespace(database_password="database-password"),
        resolve_artifact=lambda _invocation: events.append("artifact") or _artifact(tmp_path),
        render_runtime_environment=lambda *_args: events.append("runtime") or b"runtime",
        render_pgpass=lambda *_args: events.append("pgpass") or b"pgpass",
        render_role_password_input=lambda *_args: events.append("role-password") or b"role-password",
        render_plan=lambda *_args: events.append("plan") or {"environment": "production"},
        present_plan=lambda _plan: events.append("present"),
        confirm=lambda _plan: pytest.fail("provision dry run requested confirmation"),
        connect=lambda _config: events.append("ssh") or remote,
        discover=lambda *_args, **_kwargs: events.append("facts") or object(),
        provisioning=mutation("programmatic pyinfra provisioning"),
            caddy_plan=lambda _config: events.append("caddy-plan")
            or Caddy("taskman.acme.tld {\n  reverse_proxy 127.0.0.1:4000\n}\n"),
            release_transaction=mutation("release"),
        )

    result = provision(
        Invocation("provision", environment="production", dry_run=True),
        capabilities=capabilities,
    )

    assert result.stage == "planned"
    assert result.changed is False
    assert events == [
        "config",
        "secrets",
        "artifact",
        "runtime",
        "pgpass",
        "role-password",
        "caddy-plan",
        "plan",
        "present",
        "ssh",
        "facts",
        "close",
    ]


def test_restore_dry_run_resolves_exact_remote_authority_without_confirmation_or_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskman_ops.host_protocol import HostResult

    lifecycle = {
        "records": {
            "releases": (), "adoptions": (),
            "activations": ({"candidate_release_id": CANDIDATE},),
            "backups": ({
                "backup_id": BACKUP,
                "current_release_id": CURRENT,
            },),
        },
        "manifests": {
            CURRENT: {"migrations": ()},
        },
    }
    requests: list[object] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.discover_lifecycle",
        lambda *_args: (lifecycle, ()),
    )

    def inspect(_remote: object, request: object) -> HostResult:
        requests.append(request)
        return HostResult(
            1, "restore", request.operation_id, "succeeded",
            "restore-inspected", (),
            {
                "backup_id": BACKUP,
                "dump_path": f"/var/backups/taskman/{BACKUP}.dump",
                "dump_size_bytes": 2048,
                "source_database_size_bytes": 1_048_576,
                "current_release_id": CANDIDATE,
                "intended_release_id": CURRENT,
                "dump_validated": True,
            },
            {}, {"format": "custom", "validated": True}, (), (), (),
        )

    monkeypatch.setattr("taskman_ops.workflows.restore.run_request", inspect)
    result = restore(
        object(),
        _config(),
        BACKUP,
        confirm=lambda _plan: pytest.fail("restore dry run requested confirmation"),
        dry_run=True,
    )
    assert result.stage == "planned"
    assert result.changed is False
    assert result.facts["backup_id"] == BACKUP
    assert len(requests) == 1


def test_cleanup_dry_run_discovers_exact_remote_targets_without_confirmation_or_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskman_ops.host_protocol import HostResult

    lifecycle = {
        "records": {
            "releases": ({"release_id": CURRENT},),
            "activations": ({"activation_id": "activation-" + "a" * 32},),
            "backups": (), "adoptions": (),
        }
    }
    requests: list[object] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.discover_lifecycle",
        lambda *_args: (lifecycle, ()),
    )

    def inspect(_remote: object, request: object) -> HostResult:
        requests.append(request)
        return HostResult(
            1, "cleanup", request.operation_id, "succeeded",
            "cleanup-inspected", (),
            {"lifecycle": request.expected_state["lifecycle"], "targets": ()},
            {}, {}, (), (), (),
        )

    monkeypatch.setattr("taskman_ops.workflows.cleanup.run_request", inspect)
    result = cleanup(
        object(),
        _config(),
        confirm=lambda _plan: pytest.fail("cleanup dry run requested confirmation"),
        dry_run=True,
    )
    assert result.stage == "planned"
    assert result.changed is False
    assert result.facts["targets"] == ()
    assert len(requests) == 1
