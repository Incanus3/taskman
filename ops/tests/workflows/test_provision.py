"""Clean-host provisioning composition contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import subprocess
import tarfile

import pytest

from taskman_ops.cli import Invocation
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host.facts import validate_provisionable_host
from taskman_ops.manifests import ArtifactManifest, VerifiedArtifact
from taskman_ops.output import REDACTED, WorkflowResult, clear_secrets, register_secret, render_error, render_human, render_json
from taskman_ops.remote import ChangeSet, CommandResult
from taskman_ops.workflows.deploy import deploy_first_release
from taskman_ops.workflows.provision import ProvisionCapabilities, _default_capabilities, provision
from tests.workflows.test_deploy_transaction import _stubs


RELEASE_ID = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
_CADDYFILE = "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n"


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
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
        }
    )


def artifact(tmp_path: Path) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"release")
    manifest = ArtifactManifest(
        1,
        "taskman",
        "0.2.0",
        "b" * 40,
        RELEASE_ID,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "ubuntu26.04",
        "amd64",
        "27.3.4.6",
        "1.18.3",
        "22.22.1",
        (),
        "taskman",
    )
    return VerifiedArtifact(archive, tmp_path / "manifest.json", tmp_path / "checksum", "c" * 64, manifest)


@dataclass
class StatefulHost:
    """A minimal managed-state fake; procedural checks never mutate it."""

    converged: set[str] = field(default_factory=set)
    events: list[str] = field(default_factory=list)
    verification_runs: int = 0
    closed_connections: int = 0

    def converge(self, stage: str) -> ChangeSet:
        self.events.append(stage)
        changed = stage not in self.converged
        self.converged.add(stage)
        return ChangeSet(changed=changed, operations=(stage,))

    def close(self) -> None:
        self.closed_connections += 1


@dataclass
class DefaultPathHost:
    """A stateful Remote whose later discovery exposes only prior mutations."""

    stages: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    units: set[str] = field(default_factory=set)
    account: bool = False
    postgres: bool = False
    role: bool = False
    database: bool = False
    pgpass: bool = False
    runtime_environment: bool = False
    caddy: bool = False
    released: bool = False
    provisioning_marker: bool = False
    verification_runs: int = 0
    closed_connections: int = 0

    def changed(self, stage: str) -> bool:
        changed = stage not in self.stages
        self.stages.add(stage)
        return changed

    def baseline(self, value: EnvironmentConfig) -> ChangeSet:
        changed = self.changed("baseline")
        self.account = True
        self.provisioning_marker = True
        self.paths.update(
            {
                value.managed_root.as_posix(),
                value.release_root.as_posix(),
                value.deployment_root.as_posix(),
                value.backup_root.as_posix(),
                "/etc/taskman",
                "/var/lib/taskman-provisioning.state",
            }
        )
        return ChangeSet(changed=changed, operations=("baseline",))

    def firewall(self) -> ChangeSet:
        return ChangeSet(changed=self.changed("firewall"), operations=("firewall",))

    def postgresql_native(self) -> ChangeSet:
        changed = self.changed("postgresql")
        self.postgres = True
        return ChangeSet(changed=changed, operations=("postgresql",))

    def systemd(self) -> ChangeSet:
        changed = self.changed("backups-and-systemd")
        self.paths.add("/etc/systemd/system/taskman.service")
        self.units.update({"taskman.service", "taskman-backup.service", "taskman-backup.timer"})
        return ChangeSet(changed=changed, operations=("systemd",))

    def caddy_install(self) -> ChangeSet:
        changed = self.changed("caddy")
        self.paths.add("/etc/caddy/Caddyfile")
        self.units.add("caddy.service")
        self.caddy = True
        return ChangeSet(changed=changed, operations=("caddy",))

    def release(self) -> WorkflowResult:
        changed = self.changed("release")
        self.released = True
        return WorkflowResult(
            command="deploy",
            environment="production",
            changed=changed,
            stage="deployed" if changed else "already-current",
            facts={"selected_release_id": RELEASE_ID},
        )

    def verify(self) -> WorkflowResult:
        self.verification_runs += 1
        return WorkflowResult(
            command="verify",
            environment="production",
            changed=False,
            stage="verified",
            facts={"verification": {"status": "ok"}},
        )

    def close(self) -> None:
        self.closed_connections += 1

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        command = tuple(argv)
        rendered = " ".join(command)
        if command == ("cat", "/etc/os-release"):
            return CommandResult(0, 'ID=ubuntu\nVERSION_ID="26.04"\n')
        if command == ("uname", "-m"):
            return CommandResult(0, "x86_64\n")
        if command == ("cat", "/proc/1/comm"):
            return CommandResult(0, "systemd\n")
        if command == ("sudo", "-n", "true"):
            return CommandResult(0)
        if "MemTotal" in rendered:
            return CommandResult(0, f"{1024**3}\n")
        if "taskman-capacity" in command:
            return CommandResult(0, f"Avail\n{10 * 1024**3}\n")
        if "SSH_CONNECTION" in rendered:
            return CommandResult(0, "22\n")
        if command == ("ss", "-H", "-ltn"):
            return CommandResult(0, self._listeners())
        if "taskman-host-facts" in command:
            candidates = command[command.index("taskman-host-facts") + 1 :]
            return CommandResult(0, "".join(f"{path}\n" for path in candidates if path in self.paths))
        if "taskman-provisioning-marker" in command:
            return CommandResult(0, "managed\n" if self.provisioning_marker else "absent\n")
        if command[:3] == ("systemctl", "list-unit-files", "--no-legend"):
            return CommandResult(0, "".join(f"{unit} enabled\n" for unit in sorted(self.units)))
        if command == ("getent", "passwd", "taskman"):
            return CommandResult(0, "taskman:x:1000:1000::/var/lib/taskman:/usr/sbin/nologin\n") if self.account else CommandResult(2)
        if command == ("getent", "group", "taskman"):
            return CommandResult(0, "taskman:x:1000:\n") if self.account else CommandResult(2)
        if command == ("getent", "passwd", "postgres"):
            return CommandResult(0, "postgres:x:111:117::/var/lib/postgresql:/bin/bash\n") if self.postgres else CommandResult(2)
        if "command -v psql" in rendered:
            return CommandResult(0, "/usr/bin/psql\n") if self.postgres else CommandResult(1)
        if command == ("sudo", "-n", "-u", "postgres", "true"):
            return CommandResult(0) if self.postgres else CommandResult(1)
        if "psql -Atq" in rendered:
            return CommandResult(0, "taskman_prod\n" if self.database else "")
        if "FROM pg_roles" in rendered:
            return CommandResult(0, "taskman|t|f|f|f|f|f|t\n" if self.role else "")
        if "pg_auth_members" in rendered:
            return CommandResult(0, "")
        if "FROM pg_database" in rendered:
            return CommandResult(0, "taskman_prod|taskman\n" if self.database else "")
        if command == ("ss", "-H", "-ltnp"):
            return CommandResult(0, self._listener_owners())
        if "taskman-caddy-ownership" in command:
            return CommandResult(0, self._caddy_evidence())
        if "taskman-pgpass" in command:
            changed = not self.pgpass
            self.pgpass = True
            return CommandResult(0, f"changed={int(changed)}\n")
        if "taskman-runtime-environment" in command:
            changed = not self.runtime_environment
            self.runtime_environment = True
            return CommandResult(0, f"changed={int(changed)}\n")
        if command and command[0] == "runuser":
            if "createdb" in command:
                self.database = True
            elif "--command" not in command:
                self.role = True
            return CommandResult(0)
        if command and command[0] == "env":
            return CommandResult(0, "1\n")
        raise AssertionError(f"unexpected default-path command: {command!r}")

    def _listeners(self) -> str:
        listeners = ["LISTEN 0 4096 *:22 0.0.0.0:*"]
        if self.postgres:
            listeners.append("LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*")
        if self.caddy:
            listeners.extend(("LISTEN 0 4096 *:80 0.0.0.0:*", "LISTEN 0 4096 *:443 0.0.0.0:*"))
        if self.released:
            listeners.extend(
                (
                    "LISTEN 0 4096 127.0.0.1:4000 0.0.0.0:*",
                    "LISTEN 0 4096 127.0.0.1:6789 0.0.0.0:*",
                )
            )
        return "\n".join(listeners) + "\n"

    def _listener_owners(self) -> str:
        listeners = ['LISTEN 0 4096 *:22 0.0.0.0:* users:(("sshd",pid=101,fd=3))']
        if self.caddy:
            listeners.extend(
                (
                    'LISTEN 0 4096 *:80 0.0.0.0:* users:(("caddy",pid=402,fd=6))',
                    'LISTEN 0 4096 *:443 0.0.0.0:* users:(("caddy",pid=402,fd=7))',
                )
            )
        return "\n".join(listeners) + "\n"

    def _caddy_evidence(self) -> str:
        if not self.caddy:
            return (
                "config=absent\nconfig_hash=\nconfig_metadata=\n"
                "unit_load=not-found\nunit_active=inactive\nunit_pid=0\nunit_cgroup=\n"
                "unit_fragment=\nunit_metadata=\nunit_package=missing\nunit_verified=missing\n"
                "process_executable=\nprocess_arguments=\nprocess_cgroup=\n"
            )
        return (
            "config=regular\n"
            f"config_hash={hashlib.sha256(_CADDYFILE.encode()).hexdigest()}\n"
            "config_metadata=root:root:644\nunit_load=loaded\nunit_active=active\n"
            "unit_pid=402\nunit_cgroup=/system.slice/caddy.service\n"
            "unit_fragment=/usr/lib/systemd/system/caddy.service\nunit_metadata=root:root:644\n"
            "unit_package=caddy\nunit_verified=clean\nprocess_executable=/usr/bin/caddy\n"
            "process_arguments=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile\n"
            "process_cgroup=/system.slice/caddy.service\n"
        )


@dataclass(frozen=True)
class FakeSecrets:
    database_password: str


@dataclass(frozen=True)
class FakeCaddyPlan:
    """The test-only part of a Caddy plan consumed before host discovery."""

    caddyfile: str


def test_provision_orders_local_and_remote_capabilities_and_reports_idempotent_acceptance(
    tmp_path: Path,
) -> None:
    """Moving any mutable stage before discovery or release before prerequisites must fail this."""

    host = StatefulHost()
    visible_plans: list[dict[str, object]] = []
    confirmations: list[dict[str, object]] = []
    canary = "provision-secret-canary-51d7e6"
    secrets = FakeSecrets(canary)

    def capabilities() -> ProvisionCapabilities:
        def planned(value: EnvironmentConfig, candidate: VerifiedArtifact) -> dict[str, object]:
            host.events.append("plan")
            return {
                "environment": value.name,
                "candidate_release_id": candidate.manifest.release_id,
                "public_hostname": value.public_hostname,
            }

        def release(_remote: object, _config: EnvironmentConfig, _artifact: VerifiedArtifact) -> WorkflowResult:
            return WorkflowResult(
                command="deploy",
                environment="production",
                changed=host.converge("release").changed,
                stage="deployed",
                facts={"selected_release_id": RELEASE_ID},
            )

        def verify(_remote: object, _config: EnvironmentConfig, expected: str) -> WorkflowResult:
            assert expected == RELEASE_ID
            host.events.append("verify")
            host.verification_runs += 1
            return WorkflowResult(
                command="verify",
                environment="production",
                changed=False,
                stage="verified",
                facts={"verification": {"status": "ok"}},
            )

        def present(plan: dict[str, object]) -> None:
            host.events.append("plan-presented")
            visible_plans.append(dict(plan))

        def confirm(plan: dict[str, object]) -> bool:
            host.events.append("confirmation")
            confirmations.append(dict(plan))
            return True

        return ProvisionCapabilities(
            load_environment=lambda name: host.events.append(f"config:{name}") or config(),
            decrypt_secrets=lambda name: host.events.append(f"secrets:{name}") or secrets,
            resolve_artifact=lambda _invocation: host.events.append("artifact") or artifact(tmp_path),
            render_runtime_environment=lambda _config, secret: (
                host.events.append("runtime-render") or f"runtime={secret.database_password}".encode()
            ),
            render_pgpass=lambda _config, secret: host.events.append("pgpass-render") or f"pgpass={secret.database_password}".encode(),
            render_plan=planned,
            present_plan=present,
            confirm=confirm,
            connect=lambda _config: host.events.append("ssh") or host,
            discover=lambda _remote, _config, *, expected_caddyfile_sha256: (
                host.events.append("discovery") or object()
            ),
            baseline=lambda _remote, _config: host.converge("baseline"),
            firewall=lambda _remote, _config: host.converge("firewall"),
            postgresql_plan=lambda _config: host.events.append("postgresql-plan") or object(),
            postgresql_native=lambda _remote, _plan: host.converge("postgresql"),
            database=lambda _remote, _plan, *, password, pgpass: host.converge("database"),
            install_runtime_environment=lambda _remote, _content: host.converge("runtime-environment"),
            systemd_plan=lambda _config: host.events.append("systemd-plan") or object(),
            systemd=lambda _remote, _plan: host.converge("backups-and-systemd"),
            caddy_plan=lambda _config: host.events.append("caddy-plan") or FakeCaddyPlan(_CADDYFILE),
            caddy=lambda _remote, _plan: host.converge("caddy"),
            release_transaction=release,
            verify=verify,
        )

    first = provision(Invocation(command="provision", environment="production"), capabilities=capabilities())
    second = provision(Invocation(command="provision", environment="production"), capabilities=capabilities())

    assert first.changed is True
    assert first.facts["converged_stages"] == (
        "baseline",
        "firewall",
        "postgresql",
        "database",
        "runtime-environment",
        "backups-and-systemd",
        "caddy",
        "release",
    )
    assert second.changed is False
    assert second.facts["converged_stages"] == ()
    assert host.verification_runs == 2
    assert host.closed_connections == 2
    assert visible_plans == confirmations
    assert all(canary not in str(plan) for plan in visible_plans)

    first_run = host.events[:]
    assert first_run.index("artifact") < first_run.index("plan") < first_run.index("plan-presented")
    assert first_run.index("postgresql-plan") < first_run.index("plan")
    assert first_run.index("systemd-plan") < first_run.index("plan")
    assert first_run.index("caddy-plan") < first_run.index("plan")
    assert first_run.index("plan-presented") < first_run.index("confirmation") < first_run.index("ssh")
    assert first_run.index("discovery") < first_run.index("baseline") < first_run.index("firewall")
    assert first_run.index("postgresql") < first_run.index("backups-and-systemd") < first_run.index("caddy")
    assert first_run.index("caddy") < first_run.index("release") < first_run.index("verify")
    assert {
        "create the initial administrator interactively",
        "sign in over HTTPS",
        "send and receive a Resend invitation",
        "create and use an API key",
        "verify a LiveView route remains connected",
        "copy a verified local backup off-host",
    } == set(first.facts["acceptance_steps"])


def test_pre_release_failure_reports_rerunnable_partial_convergence_without_rollback(tmp_path: Path) -> None:
    """Wrapping a host-convergence failure in release rollback would destroy useful durable state."""

    host = StatefulHost()
    def unavailable_caddy(_remote: object, _plan: object) -> ChangeSet:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "caddy",
            "fixture failure",
            changed=False,
        )

    capabilities = _capabilities_for_failure(
        tmp_path,
        host,
        caddy=unavailable_caddy,
        release=lambda *_args: (_ for _ in ()).throw(AssertionError("release must not start")),
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.stage == "partial-convergence"
    assert result.changed is True
    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.facts["release_transaction_started"] is False
    assert result.facts["converged_stages"] == (
        "baseline",
        "firewall",
        "postgresql",
        "database",
        "runtime-environment",
        "backups-and-systemd",
    )
    assert "rollback" not in host.events

    retry = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_capabilities_for_failure(tmp_path, host),
    )

    assert retry.stage == "provisioned"
    assert retry.facts["converged_stages"] == ("caddy", "release")
    assert "rollback" not in host.events


def test_release_failure_is_returned_unchanged_after_the_transaction_starts(tmp_path: Path) -> None:
    """Reclassifying deployment migration/activation evidence would make recovery unsafe."""

    host = StatefulHost()
    release_failure = WorkflowResult(
        command="deploy",
        environment="production",
        changed=True,
        stage="migration-failed",
        facts={"selected_release_id": "previous-release", "database_state": "unknown"},
        exit_status=ExitStatus.MIGRATION,
    )
    capabilities = _capabilities_for_failure(
        tmp_path,
        host,
        release=lambda *_args: release_failure,
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result is release_failure
    assert "verify" not in host.events


def test_default_capability_boundary_retries_only_recognized_state_and_reports_real_database_runtime_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A positional database call, pristine-only retry, or None evidence must fail this."""

    host = DefaultPathHost()
    monkeypatch.setattr(
        "taskman_ops.host.facts._resolve_public_dns",
        lambda _hostname: ("203.0.113.10",),
    )

    first = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_default_path_capabilities(tmp_path, host),
    )
    second = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_default_path_capabilities(tmp_path, host),
    )

    assert first.facts["converged_stages"] == (
        "baseline",
        "firewall",
        "postgresql",
        "database",
        "runtime-environment",
        "backups-and-systemd",
        "caddy",
        "release",
    )
    assert second.stage == "already-provisioned"
    assert second.changed is False
    assert second.facts["converged_stages"] == ()
    assert host.verification_runs == 2
    assert host.closed_connections == 2


def test_default_discovery_uses_the_preconfirmation_caddy_plan_hash_without_rerendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendering Caddy again after SSH could validate bytes the operator never approved."""

    host = DefaultPathHost()
    events: list[str] = []
    observed_hashes: list[str] = []
    caddy_plan_calls = 0
    connected = False
    expected_hash = hashlib.sha256(_CADDYFILE.encode()).hexdigest()
    monkeypatch.setattr(
        "taskman_ops.host.facts._resolve_public_dns",
        lambda _hostname: ("203.0.113.10",),
    )

    def one_shot_renderer(_config: EnvironmentConfig) -> str:
        nonlocal caddy_plan_calls
        assert not connected
        caddy_plan_calls += 1
        if caddy_plan_calls != 1:
            raise AssertionError("Caddy rendering was repeated")
        events.append("caddy-plan")
        return _CADDYFILE

    def connect_once(_config: EnvironmentConfig) -> DefaultPathHost:
        nonlocal connected
        connected = True
        events.append("connect")
        return host

    def discover(
        remote: DefaultPathHost,
        value: EnvironmentConfig,
        *,
        expected_caddyfile_sha256: str,
    ) -> object:
        events.append("discover")
        observed_hashes.append(expected_caddyfile_sha256)
        return validate_provisionable_host(
            remote,
            value,
            expected_caddyfile_sha256=expected_caddyfile_sha256,
        )

    def forbidden_after_connection(_config: EnvironmentConfig) -> str:
        assert connected
        raise AssertionError("Caddy renderer ran after SSH connection")

    capabilities = replace(
        _default_path_capabilities(tmp_path, host),
        present_plan=lambda _plan: events.append("present-plan"),
        confirm=lambda _plan: events.append("confirmation") or True,
        connect=connect_once,
        discover=discover,
    )
    monkeypatch.setattr("taskman_ops.services.caddy.render_caddyfile", one_shot_renderer)
    monkeypatch.setattr("taskman_ops.host.facts.render_caddyfile", forbidden_after_connection)

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.stage == "provisioned"
    assert caddy_plan_calls == 1
    assert observed_hashes == [expected_hash]
    assert events[:5] == ["caddy-plan", "present-plan", "confirmation", "connect", "discover"]


def test_default_release_transaction_reuses_the_already_confirmed_provisioning_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provisioning must not prompt again after it has already started host convergence."""

    seen: list[dict[str, object]] = []
    expected = WorkflowResult(
        "deploy",
        "production",
        True,
        "deployed",
        {"selected_release_id": RELEASE_ID},
    )

    def deployed(
        _remote: object,
        _config: EnvironmentConfig,
        _artifact: VerifiedArtifact,
        **kwargs: object,
    ) -> WorkflowResult:
        seen.append(kwargs)
        return expected

    monkeypatch.setattr("taskman_ops.workflows.provision.deploy_first_release", deployed)

    result = _default_capabilities().release_transaction(
        object(),
        config(),
        artifact(tmp_path),
    )

    assert result is expected
    assert seen == [{}]


def test_provision_runs_the_real_genesis_transaction_and_reruns_as_a_verified_noop(
    tmp_path: Path,
) -> None:
    """The composition boundary must prove provision-to-activation without replacing deploy."""

    managed = tmp_path / "managed"
    release_root = managed / "releases"
    deployment_root = tmp_path / "deployments"
    backup_root = tmp_path / "backups"
    for directory in (managed, release_root, deployment_root, backup_root):
        directory.mkdir(parents=True)
        directory.chmod(0o750)
    commands = tmp_path / "commands"
    commands.mkdir()
    _stubs(commands)
    events = tmp_path / "events"

    candidate = artifact(tmp_path)
    archive = candidate.archive
    with tarfile.open(archive, "w:gz") as bundle:
        for name, mode in (
            ("taskman/bin/server", 0o750),
            ("taskman/bin/migrate", 0o750),
            ("taskman/lib/release", 0o640),
            ("taskman/releases/start", 0o640),
        ):
            entry = tarfile.TarInfo(name)
            entry.mode = mode
            entry.size = 0
            bundle.addfile(entry)
    candidate = replace(candidate, sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    value = config().model_copy(
        update={
            "managed_root": PurePosixPath(managed),
            "release_root": PurePosixPath(release_root),
            "deployment_root": PurePosixPath(deployment_root),
            "backup_root": PurePosixPath(backup_root),
        }
    )

    @dataclass
    class LocalTransactionRemote:
        closed: int = 0
        results: list[CommandResult] = field(default_factory=list)

        def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            command = list(argv)
            if len(command) >= 3 and command[0:2] == ["sh", "-ceu"]:
                command[2] = command[2].replace(
                    "lock_root=/var/lock/taskman;",
                    f"lock_root={tmp_path / 'canonical-lock'};",
                    1,
                ).replace("record_owner_uid=0", "record_owner_uid=$(id -u)", 1)
            command = [
                str(commands / "taskman-backup")
                if value == "/usr/local/lib/taskman/taskman-backup"
                else value
                for value in command
            ]
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{commands}:{os.environ['PATH']}",
                    "TASKMAN_EVENTS": str(events),
                    "TASKMAN_STATE": str(tmp_path / "service-state"),
                    "TASKMAN_ACTIVATION_RECORD": str(
                        deployment_root / "activations" / "not-yet-recorded.json"
                    ),
                    "TASKMAN_FAILURE": "",
                    "TASKMAN_CANDIDATE": str(release_root / RELEASE_ID),
                    "TASKMAN_DEPLOYMENT": str(deployment_root),
                    "TASKMAN_PUBLICATION_MARKER": str(tmp_path / "publication-failed"),
                    "TASKMAN_BACKUP_ROOT": str(backup_root),
                    "TASKMAN_CANDIDATE_RELEASE": RELEASE_ID,
                },
                check=False,
            )
            result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
            self.results.append(result)
            return result

        def put(
            self,
            source: Path,
            destination: PurePosixPath,
            *,
            mode: int,
            **_kwargs: object,
        ) -> None:
            shutil.copyfile(source, Path(destination))
            Path(destination).chmod(mode)

        def close(self) -> None:
            self.closed += 1

    remote = LocalTransactionRemote()
    capabilities = replace(
        _capabilities_for_failure(tmp_path, StatefulHost()),
        load_environment=lambda _name: value,
        resolve_artifact=lambda _invocation: candidate,
        connect=lambda _config: remote,
        discover=lambda *_args, **_kwargs: None,
        baseline=lambda *_args: False,
        firewall=lambda *_args: False,
        postgresql_native=lambda *_args: False,
        database=lambda *_args, **_kwargs: False,
        install_runtime_environment=lambda *_args: False,
        systemd=lambda *_args: False,
        caddy=lambda *_args: False,
        release_transaction=deploy_first_release,
        verify=lambda *_args: WorkflowResult(
            "verify", "production", False, "verified", {"verification": {"status": "ok"}}
        ),
    )

    first = provision(
        Invocation(command="provision", environment="production"),
        capabilities=capabilities,
    )
    second = provision(
        Invocation(command="provision", environment="production"),
        capabilities=capabilities,
    )

    assert first.stage == "provisioned", (first, remote.results)
    assert first.facts["release"]["previous_release_id"] is None
    assert first.facts["release"]["backup_id"] == "backup-" + "a" * 32
    assert second.stage == "already-provisioned"
    assert second.changed is False
    assert remote.closed == 2


def test_default_path_partial_failure_recovers_from_the_facts_created_before_the_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting the first run's marked partial state would strand safe convergence."""

    host = DefaultPathHost()
    monkeypatch.setattr(
        "taskman_ops.host.facts._resolve_public_dns",
        lambda _hostname: ("203.0.113.10",),
    )

    def unavailable_caddy(_remote: object, _plan: object) -> ChangeSet:
        raise OpsError(ExitStatus.REMOTE_PREFLIGHT, "caddy", "fixture failure", changed=False)

    failed = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_default_path_capabilities(tmp_path, host, caddy=unavailable_caddy),
    )
    retry = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_default_path_capabilities(tmp_path, host),
    )

    assert failed.stage == "partial-convergence"
    assert failed.facts["converged_stages"] == (
        "baseline",
        "firewall",
        "postgresql",
        "database",
        "runtime-environment",
        "backups-and-systemd",
    )
    assert retry.facts["converged_stages"] == ("caddy", "release")
    assert host.verification_runs == 1


@pytest.mark.parametrize("state", ("unanchored", "contradictory"))
def test_default_discovery_refuses_mixed_or_contradictory_managed_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """Dropping marker or service-boundary checks would make unsafe adoption possible."""

    host = DefaultPathHost()
    if state == "unanchored":
        host.account = True
        host.paths.add("/etc/taskman")
    else:
        host.provisioning_marker = True
        host.paths.update({"/etc/taskman", "/var/lib/taskman-provisioning.state"})
        host.units.add("caddy.service")
    monkeypatch.setattr(
        "taskman_ops.host.facts._resolve_public_dns",
        lambda _hostname: ("203.0.113.10",),
    )

    with pytest.raises(OpsError) as raised:
        _default_path_capabilities(tmp_path, host).discover(
            host,
            config(),
            expected_caddyfile_sha256=hashlib.sha256(_CADDYFILE.encode()).hexdigest(),
        )

    assert raised.value.status is ExitStatus.SAFETY
    assert host.stages == set()


def test_registered_plan_canary_is_redacted_before_presentation_confirmation_and_rendering(
    tmp_path: Path,
) -> None:
    """Removing plan redaction must expose this registered canary at four operator boundaries."""

    host = StatefulHost()
    canary = "plan-visible-secret-canary-51d7e6"
    presented: list[dict[str, object]] = []
    confirmed: list[dict[str, object]] = []
    clear_secrets()
    register_secret(canary)
    try:
        capabilities = replace(
            _capabilities_for_failure(tmp_path, host),
            render_plan=lambda _config, _artifact: {
                "candidate_release_id": RELEASE_ID,
                "operator_note": f"canary={canary}",
            },
            present_plan=lambda plan: presented.append(dict(plan)),
            confirm=lambda plan: confirmed.append(dict(plan)) or False,
        )

        result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)
        error = OpsError(
            ExitStatus.SECRET,
            "provision",
            f"refused {canary}",
            next_action=f"replace {canary}",
        )
        rendered = (
            render_human(result),
            render_json(result),
            render_error(error, command=f"provision {canary}"),
            render_error(error, command=f"provision {canary}", json_output=True),
        )

        assert presented == confirmed
        assert presented[0]["candidate_release_id"] == RELEASE_ID
        assert presented[0]["operator_note"] == f"canary={REDACTED}"
        assert all(canary not in value for value in rendered)
        assert all(REDACTED in value for value in rendered)
        assert RELEASE_ID in rendered[0]
        assert RELEASE_ID in rendered[1]
    finally:
        clear_secrets()


def _capabilities_for_failure(
    tmp_path: Path,
    host: StatefulHost,
    *,
    caddy=None,
    release=None,
) -> ProvisionCapabilities:
    """Build a real stateful composition fixture with one replaceable boundary."""

    return ProvisionCapabilities(
        load_environment=lambda _name: config(),
        decrypt_secrets=lambda _name: FakeSecrets("not-reported-secret"),
        resolve_artifact=lambda _invocation: artifact(tmp_path),
        render_runtime_environment=lambda _config, _secret: b"runtime",
        render_pgpass=lambda _config, _secret: b"pgpass",
        render_plan=lambda _config, _artifact: {"candidate_release_id": RELEASE_ID},
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
        connect=lambda _config: host,
        discover=lambda _remote, _config, *, expected_caddyfile_sha256: None,
        baseline=lambda _remote, _config: host.converge("baseline"),
        firewall=lambda _remote, _config: host.converge("firewall"),
        postgresql_plan=lambda _config: object(),
        postgresql_native=lambda _remote, _plan: host.converge("postgresql"),
        database=lambda _remote, _plan, *, password, pgpass: host.converge("database"),
        install_runtime_environment=lambda _remote, _content: host.converge("runtime-environment"),
        systemd_plan=lambda _config: object(),
        systemd=lambda _remote, _plan: host.converge("backups-and-systemd"),
        caddy_plan=lambda _config: FakeCaddyPlan(_CADDYFILE),
        caddy=caddy or (lambda _remote, _plan: host.converge("caddy")),
        release_transaction=release or (lambda *_args: WorkflowResult(
            command="deploy", environment="production", changed=host.converge("release").changed,
            stage="deployed", facts={"selected_release_id": RELEASE_ID},
        )),
        verify=lambda _remote, _config, _release: WorkflowResult(
            command="verify", environment="production", changed=False, stage="verified", facts={}
        ),
    )


def _default_path_capabilities(
    tmp_path: Path,
    host: DefaultPathHost,
    *,
    caddy=None,
) -> ProvisionCapabilities:
    """Keep default database/runtime/discovery callables while replacing external effects."""

    defaults = _default_capabilities()
    return replace(
        defaults,
        load_environment=lambda _name: config(),
        decrypt_secrets=lambda _name: FakeSecrets("default-path-secret"),
        resolve_artifact=lambda _invocation: artifact(tmp_path),
        render_runtime_environment=lambda _config, _secrets: b"RUNTIME=default-path\n",
        render_pgpass=lambda _config, _secrets: b"127.0.0.1:5432:*:taskman:default-path\n",
        render_plan=lambda _config, _artifact: {"candidate_release_id": RELEASE_ID},
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
        connect=lambda _config: host,
        baseline=lambda _remote, value: host.baseline(value),
        firewall=lambda _remote, _config: host.firewall(),
        postgresql_native=lambda _remote, _plan: host.postgresql_native(),
        systemd=lambda _remote, _plan: host.systemd(),
        caddy=caddy or (lambda _remote, _plan: host.caddy_install()),
        release_transaction=lambda _remote, _config, _artifact: host.release(),
        verify=lambda _remote, _config, _release_id: host.verify(),
    )
