from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from taskman_ops.cli import Invocation, dispatch, parse_invocation
from taskman_ops.config import EnvironmentConfig
from taskman_ops.manifests import ArtifactManifest, VerifiedArtifact
from taskman_ops.output import WorkflowResult
from taskman_ops.remote import CommandResult
from taskman_ops.releases.records import (
    ActivationRecord,
    BackupRecord,
    LifecycleRecords,
    ReleaseRecord,
)
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
        )
READ_ONLY_COMMANDS = ("verify", "releases", "backups")


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(name="production"))


def _artifact(tmp_path: Path, *, release_id: str = CANDIDATE) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"release")
    revision = ("b" if release_id == CANDIDATE else "a") * 40
    manifest = ArtifactManifest(
        1,
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
        1,
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
    assert seen[0][1].config is config
    assert seen[0][1].store.remote is remote
    assert seen[0][2] is True


@pytest.mark.parametrize("command", READ_ONLY_COMMANDS)
def test_read_only_commands_remain_the_same_with_dry_run(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    remote = object()
    calls: list[tuple[str, object]] = []
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
            lambda store: calls.append((command, store)) or result,
        )

    normal = dispatch(Invocation(command, environment="production"))
    dry = dispatch(Invocation(command, environment="production", dry_run=True))

    assert normal.command == command
    assert dry.command == command
    assert normal.changed is False
    assert dry.changed is False
    assert normal.stage == dry.stage
    assert normal.facts == dry.facts
    assert len(calls) == 2


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
        decrypt_secrets=lambda _name: events.append("secrets") or object(),
        resolve_artifact=lambda _invocation: events.append("artifact") or _artifact(tmp_path),
        render_runtime_environment=lambda *_args: events.append("runtime") or b"runtime",
        render_pgpass=lambda *_args: events.append("pgpass") or b"pgpass",
        render_plan=lambda *_args: events.append("plan") or {"environment": "production"},
        present_plan=lambda _plan: events.append("present"),
        confirm=lambda _plan: pytest.fail("provision dry run requested confirmation"),
        connect=lambda _config: events.append("ssh") or remote,
        discover=lambda *_args, **_kwargs: events.append("facts") or object(),
        baseline=mutation("baseline"),
        firewall=mutation("firewall"),
        postgresql_plan=lambda _config: events.append("postgresql-plan") or object(),
        postgresql_native=mutation("postgresql"),
        database=mutation("database"),
        install_runtime_environment=mutation("runtime environment"),
        systemd_plan=lambda _config: events.append("systemd-plan") or object(),
        systemd=mutation("systemd"),
        caddy_plan=lambda _config: events.append("caddy-plan")
        or Caddy("taskman.acme.tld {\n  reverse_proxy 127.0.0.1:4000\n}\n"),
        caddy=mutation("caddy"),
        release_transaction=mutation("release"),
        verify=mutation("verification"),
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
        "postgresql-plan",
        "systemd-plan",
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
    from taskman_ops.workflows.operational_preflight import (
        validate_operational_preflight as validate_real_preflight,
    )

    events: list[str] = []
    records = LifecycleRecords(
        releases=(
            ReleaseRecord(1, CURRENT, "a" * 64, FIRST, FIRST, None, None, "no-change"),
            ReleaseRecord(
                1,
                CANDIDATE,
                "b" * 64,
                SECOND,
                SECOND,
                CURRENT,
                None,
                "no-change",
            ),
        ),
        activations=(
            ActivationRecord(
                1,
                "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                CURRENT,
                FIRST,
                None,
                "no-change",
            ),
            ActivationRecord(
                1,
                "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                CURRENT,
                CANDIDATE,
                SECOND,
                None,
                "no-change",
            ),
        ),
        backups=(
            BackupRecord(
                1,
                BACKUP,
                FIRST,
                2048,
                1_048_576,
                "taskman_prod",
                CURRENT,
                CANDIDATE,
                "pre-deploy",
                True,
                PurePosixPath("/var/backups/taskman/backup-a.dump"),
            ),
        ),
        adoptions=(),
        warnings=(),
    )
    snapshot = {
        "manifests": {
            CURRENT: _manifest(CURRENT),
            CANDIDATE: _manifest(CANDIDATE),
        },
        "dump_states": {"/var/backups/taskman/backup-a.dump": "present"},
    }

    class Remote:
        def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            events.append(argv[3])
            return CommandResult(0)

    remote = Remote()

    class Store:
        def __init__(self) -> None:
            self.remote = remote
            self.release_root = PurePosixPath("/opt/taskman/releases")
            self.backup_root = PurePosixPath("/var/backups/taskman")

        def read(self, **_kwargs: object) -> tuple[LifecycleRecords, dict[str, object]]:
            events.append("snapshot")
            return records, snapshot

    monkeypatch.setattr(
        "taskman_ops.workflows.restore.run_locked_restore",
        lambda *_args, **_kwargs: pytest.fail("restore dry run changed the database"),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_operational_preflight",
        lambda actual_remote, actual_config: validate_real_preflight(
            actual_remote,
            actual_config,
            host_validator=lambda *_args: object(),
        ),
    )

    result = restore(
        remote,
        _config(),
        BACKUP,
        lifecycle_store=Store(),  # type: ignore[arg-type]
        confirm=lambda _plan: pytest.fail("restore dry run requested confirmation"),
        dry_run=True,
    )

    assert result.stage == "planned"
    assert result.changed is False
    assert result.facts["backup_id"] == BACKUP
    assert events == [
        "taskman-runtime-preflight",
        "taskman-database-preflight",
        "snapshot",
        "taskman-validate-restore-dump",
    ]


def test_cleanup_dry_run_discovers_exact_remote_targets_without_confirmation_or_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskman_ops.workflows.operational_preflight import (
        validate_operational_preflight as validate_real_preflight,
    )

    events: list[str] = []

    class Remote:
        def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            if argv[3] in {
                "taskman-runtime-preflight",
                "taskman-database-preflight",
            }:
                events.append(argv[3])
                return CommandResult(0)
            events.append("recovery-inventory")
            return CommandResult(
                0,
                '{"schema_version":1,"retained_databases":[],"completed_staging":[]}',
            )

    remote = Remote()
    records = LifecycleRecords(
        releases=(
            ReleaseRecord(
                1,
                CURRENT,
                "a" * 64,
                FIRST,
                FIRST,
                None,
                None,
                "no-change",
            ),
        ),
        activations=(
            ActivationRecord(
                1,
                "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                CURRENT,
                FIRST,
                None,
                "no-change",
            ),
        ),
        backups=(),
        adoptions=(),
        warnings=(),
    )

    class Store:
        def __init__(self) -> None:
            self.remote = remote

        def read(self, **_kwargs: object) -> tuple[LifecycleRecords, dict[str, object]]:
            events.append("snapshot")
            return records, {"records": "validated"}

    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.run_locked_cleanup",
        lambda *_args, **_kwargs: pytest.fail("cleanup dry run deleted an artifact"),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.cleanup.validate_operational_preflight",
        lambda actual_remote, actual_config: validate_real_preflight(
            actual_remote,
            actual_config,
            host_validator=lambda *_args: object(),
        ),
    )

    result = cleanup(
        remote,
        _config(),
        lifecycle_store=Store(),  # type: ignore[arg-type]
        confirm=lambda _plan: pytest.fail("cleanup dry run requested confirmation"),
        dry_run=True,
    )

    assert result.stage == "planned"
    assert result.changed is False
    assert result.facts["targets"] == ()
    assert events == [
        "taskman-runtime-preflight",
        "taskman-database-preflight",
        "snapshot",
        "recovery-inventory",
    ]
