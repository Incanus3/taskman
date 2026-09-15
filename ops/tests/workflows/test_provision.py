"""Provisioning workflow contracts for the single pyinfra convergence path."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from types import SimpleNamespace

import pytest

from tests.support.environments import environment_config
from taskman_ops.cli import Invocation
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import WorkflowResult
from taskman_ops.remote import ChangeSet
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository
from taskman_ops.workflows.provision import ProvisionCapabilities, _present_plan, provision
from taskman_ops.workflows.deploy import _matches_confirmed_preconvergence


def artifact(*, migrations: tuple[object, ...] = ()) -> object:
    return SimpleNamespace(
        manifest=SimpleNamespace(
            target_os="ubuntu26.04",
            architecture="amd64",
            release_id="0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
            migrations=migrations,
        ),
        sha256="a" * 64,
    )


@dataclass
class Host:
    events: list[str] = field(default_factory=list)
    converged: set[str] = field(default_factory=set)
    closed: int = 0

    def converge(self, stage: str) -> ChangeSet:
        self.events.append(stage)
        changed = stage not in self.converged
        self.converged.add(stage)
        return ChangeSet(changed=changed, operations=(stage,) if changed else ())

    def close(self) -> None:
        self.closed += 1


def test_provision_orders_one_convergence_boundary_before_helper_genesis() -> None:
    host = Host()

    def release(_remote: object, _config: EnvironmentConfig, value: object) -> WorkflowResult:
        change = host.converge("release")
        return WorkflowResult(
            command="deploy",
            environment="production",
            changed=change.changed,
            stage="deployed" if change.changed else "already-current",
            facts={"selected_release_id": value.manifest.release_id},
        )

    capabilities = _capabilities(host, release=release)
    first = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)
    second = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert first.changed is True
    assert first.facts["provisioning_changed"] is True
    assert first.facts["release"]["selected_release_id"] == artifact().manifest.release_id
    assert second.changed is False
    assert second.facts["provisioning_changed"] is False
    assert host.events.index("discovery") < host.events.index("provisioning") < host.events.index("release")
    assert "verify" not in host.events
    assert host.closed == 2


def test_provision_builds_the_material_plan_after_all_preconvergence_authority() -> None:
    """Confirmed material facts must come from the admitted starting authority."""

    host = Host()
    capabilities = _capabilities(
        host,
        present_plan=lambda _plan: host.events.append("present-plan"),
        confirm=lambda _plan: host.events.append("confirm") or True,
    )
    capabilities = ProvisionCapabilities(
        **{
            **capabilities.__dict__,
            "preflight": lambda _remote, _inputs: host.events.append("preflight"),
        }
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.stage == "provisioned"
    assert host.events == [
        "discovery",
        "preflight",
        "plan",
        "present-plan",
        "confirm",
        "discovery",
        "preflight",
        "provisioning",
    ]


def test_provision_refuses_existing_credential_authority_before_pyinfra_mutation() -> None:
    """A conflicting recovered secret must never reach the convergence writer."""

    host = Host()

    def preflight(_remote: object, _inputs: object) -> None:
        host.events.append("credential-preflight")
        raise OpsError(
            ExitStatus.SAFETY,
            "credential-preflight",
            "existing credentials disagree with the supplied authority",
            changed=False,
            next_action="resolve the credential authority deliberately",
        )

    capabilities = _capabilities(host)
    capabilities = ProvisionCapabilities(**{**capabilities.__dict__, "preflight": preflight})

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.exit_status is ExitStatus.SAFETY
    assert host.events == ["discovery", "credential-preflight"]
    assert "provisioning" not in host.events


@pytest.mark.parametrize(
    "authority",
    ("current", "release-record", "selection-record", "backup-protection", "restore-record", "postgresql"),
)
def test_each_preconvergence_authority_refusal_stops_before_pyinfra_mutation(authority: str) -> None:
    """Every existing authority class is a read-only admission boundary."""

    host = Host()

    def preflight(_remote: object, _inputs: object) -> None:
        host.events.append(authority)
        raise OpsError(ExitStatus.SAFETY, "authority-preflight", "unsafe authority", changed=False)

    capabilities = ProvisionCapabilities(**{**_capabilities(host).__dict__, "preflight": preflight})
    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.exit_status is ExitStatus.SAFETY
    assert host.events == ["discovery", authority]
    assert "provisioning" not in host.events


def test_default_preflight_checks_existing_resources_before_the_secret_writers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact resource admission must precede both protected credential writes."""

    from taskman_ops import provisioning as provisioning_module

    host = Host()
    checks: list[str] = []

    monkeypatch.setattr(
        provisioning_module,
        "validate_preconvergence_authority",
        lambda *_args: checks.append("observer"),
    )

    monkeypatch.setattr(
        provisioning_module,
        "validate_existing_resource_authority",
        lambda *_args: checks.append("resources"),
    )
    monkeypatch.setattr(
        provisioning_module,
        "validate_existing_credential_authority",
        lambda *_args: checks.append("credentials"),
    )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=ProvisionCapabilities(
            **{**_capabilities(host).__dict__, "preflight": provisioning_module.validate_existing_authority}
        ),
    )

    assert result.exit_status is ExitStatus.OK
    assert checks == ["observer", "resources", "credentials"] * 2


def test_default_preflight_observes_record_and_postgresql_authority_before_local_reuse_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No existing record or database authority may reach pyinfra unobserved."""

    from taskman_ops import provisioning as provisioning_module

    host = Host()
    checks: list[str] = []
    monkeypatch.setattr(
        provisioning_module,
        "validate_preconvergence_authority",
        lambda *_args: checks.append("observer"),
        raising=False,
    )
    monkeypatch.setattr(
        provisioning_module,
        "validate_existing_resource_authority",
        lambda *_args: checks.append("resources"),
    )
    monkeypatch.setattr(
        provisioning_module,
        "validate_existing_credential_authority",
        lambda *_args: checks.append("credentials"),
    )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=ProvisionCapabilities(
            **{**_capabilities(host).__dict__, "preflight": provisioning_module.validate_existing_authority}
        ),
    )

    assert result.exit_status is ExitStatus.OK
    assert checks == ["observer", "resources", "credentials"] * 2


def test_provision_refuses_failed_immutable_admission_before_plan_or_mutation() -> None:
    """A rejected immutable snapshot must not reach operator or host consequences."""

    host = Host()

    def reject_discovery(_remote: object, _config: EnvironmentConfig, **_kwargs: object) -> None:
        host.events.append("discovery")
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "host-admission",
            "immutable host admission failed",
            changed=False,
            next_action="correct the immutable host evidence and retry",
        )

    capabilities = _capabilities(
        host,
        present_plan=lambda _plan: host.events.append("present-plan"),
        confirm=lambda _plan: host.events.append("confirm") or True,
    )
    capabilities = ProvisionCapabilities(**{**capabilities.__dict__, "discover": reject_discovery})

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.stage == "provisioning-incomplete"
    assert result.facts == {"failed_boundary": "host-admission", "release_started": False}
    assert host.events == ["discovery"]
    assert host.closed == 1


def test_presented_plan_says_confirmation_precedes_host_convergence(capsys) -> None:
    """Operator guidance must not imply that connection is the confirmation boundary."""

    _present_plan({"environment": "production"})

    assert "Next action: confirm before host convergence mutates the target host" in capsys.readouterr().out


def test_provision_passes_a_migrating_artifact_to_the_public_genesis_capability() -> None:
    host = Host()
    migration = SimpleNamespace(filename="20260905120000_create_tasks.exs", sha256="d" * 64)
    value = artifact(migrations=(migration,))

    def release(_remote: object, _config: EnvironmentConfig, supplied: object) -> WorkflowResult:
        assert supplied is value
        assert supplied.manifest.migrations == (migration,)
        return WorkflowResult(
            command="deploy",
            environment="production",
            changed=True,
            stage="deployed",
            facts={
                "selected_release_id": supplied.manifest.release_id,
                "migration_policy": "restore-required",
                "backup_id": None,
                "database_state": "changed",
            },
        )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_capabilities(host, release=release, artifact_value=value),
    )

    assert result.stage == "provisioned"
    assert result.facts["release"]["migration_policy"] == "restore-required"
    assert result.facts["release"]["backup_id"] is None


def test_provision_passes_migration_and_acknowledgement_authority_to_genesis() -> None:
    host = Host()
    observed: dict[str, object] = {}

    def genesis(_remote: object, _config: EnvironmentConfig, supplied: object, **kwargs: object) -> WorkflowResult:
        observed.update(kwargs)
        return WorkflowResult(
            command="deploy", environment="production", changed=False, stage="already-current", facts={}
        )

    capabilities = _capabilities(host)
    capabilities = ProvisionCapabilities(**{**capabilities.__dict__, "genesis": genesis})
    result = provision(
        Invocation(
            command="provision",
            environment="production",
            migration_policy="backward-compatible",
            yes=True,
            allow_downgrade=True,
        ),
        capabilities=capabilities,
    )

    assert result.exit_status is ExitStatus.OK
    assert {key: value for key, value in observed.items() if key != "starting_state"} == {
        "migration_policy": "backward-compatible",
        "yes": True,
        "allow_downgrade": True,
        "dry_run": False,
    }
    assert observed["starting_state"]["authority"] == "validated"


def test_provision_yes_acknowledges_the_confirmed_resource_plan_without_prompting() -> None:
    host = Host()
    result = provision(
        Invocation(command="provision", environment="production", yes=True),
        capabilities=_capabilities(
            host,
            confirm=lambda _plan: pytest.fail("--yes must not request another confirmation"),
        ),
    )

    assert result.exit_status is ExitStatus.OK


def test_provision_returns_a_manual_genesis_result_without_reclassifying_it() -> None:
    host = Host()

    def release(_remote: object, _config: EnvironmentConfig, _artifact: object) -> WorkflowResult:
        return WorkflowResult(
            command="deploy",
            environment="production",
            changed=False,
            stage="safety-refused",
            facts={"selected_release_id": None, "applied_migrations": (999,)},
            warnings=("release warning",),
            next_action="inspect the release state before retrying",
            exit_status=ExitStatus.SAFETY,
        )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_capabilities(host, release=release),
    )

    assert result.command == "provision"
    assert result.changed is True
    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"
    assert result.facts["applied_migrations"] == (999,)
    assert result.warnings == ("release warning",)
    assert result.next_action == "inspect the release state before retrying"
    assert host.events == ["discovery", "plan", "discovery", "provisioning"]


def test_provision_release_failure_keeps_unchanged_when_provisioning_did_not_change() -> None:
    host = Host()

    def release(_remote: object, _config: EnvironmentConfig, _artifact: object) -> WorkflowResult:
        return WorkflowResult(
            command="deploy",
            environment="production",
            changed=False,
            stage="release-refused",
            facts={"failure": "not-ready"},
            warnings=("release warning",),
            next_action="inspect release state before retrying",
            exit_status=ExitStatus.RELEASE,
        )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_capabilities(
            host,
            provisioning=lambda *_args: ChangeSet(changed=False),
            release=release,
        ),
    )

    assert result.command == "provision"
    assert result.changed is False
    assert result.exit_status is ExitStatus.RELEASE
    assert result.stage == "release-refused"
    assert result.facts["failure"] == "not-ready"
    assert result.facts["mutation_state"] == "unchanged"
    assert result.facts["starting_state"]["authority"] == "validated"
    assert result.warnings == ("release warning",)
    assert result.next_action == "inspect release state before retrying"


def test_provision_release_failure_retains_release_change_evidence() -> None:
    host = Host()

    def release(_remote: object, _config: EnvironmentConfig, _artifact: object) -> WorkflowResult:
        return WorkflowResult(
            command="deploy",
            environment="production",
            changed=True,
            stage="release-incomplete",
            facts={"failure": "started"},
            warnings=("release warning",),
            next_action="inspect the partially deployed release before retrying",
            exit_status=ExitStatus.RELEASE,
        )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_capabilities(
            host,
            provisioning=lambda *_args: ChangeSet(changed=False),
            release=release,
        ),
    )

    assert result.command == "provision"
    assert result.changed is True
    assert result.exit_status is ExitStatus.RELEASE
    assert result.stage == "release-incomplete"
    assert result.facts["failure"] == "started"
    assert result.facts["mutation_state"] == "changed"
    assert result.facts["starting_state"]["authority"] == "validated"
    assert result.warnings == ("release warning",)
    assert result.next_action == "inspect the partially deployed release before retrying"


def test_provision_aggregates_convergence_mutation_before_unknown_genesis_result() -> None:
    """A lost genesis reply must not erase an earlier proved host convergence."""

    host = Host()

    def release(_remote: object, _config: EnvironmentConfig, _artifact: object) -> WorkflowResult:
        return WorkflowResult(
            command="deploy",
            environment="production",
            changed=True,
            stage="deployment-incomplete",
            facts={
                "mutation_state": "unknown",
                "starting_state": {"selected_release_id": None},
            },
            exit_status=ExitStatus.RELEASE,
        )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_capabilities(host, release=release),
    )

    assert result.changed is True
    assert result.facts["mutation_state"] == "changed"
    assert result.facts["starting_state"] == {
        "authority": "validated",
        "candidate_release_id": artifact().manifest.release_id,
    }


def test_provision_preserves_the_confirmed_preconvergence_snapshot_through_genesis() -> None:
    """Later genesis observations must never replace the confirmed starting authority."""

    host = Host()
    received: dict[str, object] = {}

    def genesis(_remote: object, _config: EnvironmentConfig, _artifact: object, **kwargs: object) -> WorkflowResult:
        received.update(kwargs)
        return WorkflowResult(
            command="deploy",
            environment="production",
            changed=True,
            stage="deployed",
            facts={"starting_state": {"selected_release_id": "post-convergence"}},
        )

    capabilities = ProvisionCapabilities(**{**_capabilities(host).__dict__, "genesis": genesis})
    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert received["starting_state"] == {
        "authority": "validated",
        "candidate_release_id": artifact().manifest.release_id,
    }
    assert result.facts["starting_state"] == received["starting_state"]


@dataclass(frozen=True)
class _ObservedProvisionResources:
    reused: tuple[str, ...]
    converged: tuple[str, ...]
    database_state: str


def test_provision_binds_dataclass_resource_authority_and_full_host_snapshot_into_plan() -> None:
    """The confirmed transaction must not lose real discovery dataclass facts."""

    host = Host()
    presented: list[dict[str, object]] = []
    authority = {
        "authority": "validated",
        "selected_release_id": None,
        "last_successful_selection_id": None,
        "last_successful_selection": None,
        "previous_successful_selection": None,
        "applied_migrations": (),
        "service_state": "stopped",
        "database_state": "ready",
        "backup_protections": (),
        "independently_held_backup_ids": (),
        "backup_protection_sha256": "a" * 64,
        "scheduled_backup_sha256": None,
        "backup_timer_enabled": False,
        "backup_timer_state": "inactive",
        "downgrade_baseline_sha256": "b" * 64,
        "installed_release_count": 0,
        "installed_release_sha256": "c" * 64,
    }
    resources = _ObservedProvisionResources(("/etc/taskman",), ("taskman.service",), "empty")
    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "discover": lambda *_args, **_kwargs: resources,
            "preflight": lambda *_args: authority,
            "present_plan": lambda plan: presented.append(dict(plan)),
        }
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert presented[0]["starting_state"]["host_authority"] == authority
    assert presented[0]["starting_state"]["resource_authority"] == {
        "reused": ("/etc/taskman",),
        "converged": ("taskman.service",),
        "database_state": "empty",
    }
    assert result.facts["starting_state"] == presented[0]["starting_state"]


def test_provision_passes_only_confirmed_absent_scheduler_resources_to_pyinfra_and_genesis() -> None:
    """Existing scheduler files are not generic writes; absent ones are explicit deltas."""

    host = Host()
    observed_inputs: list[object] = []
    genesis_kwargs: dict[str, object] = {}
    authority = {
        "authority": "validated",
        "selected_release_id": None,
        "last_successful_selection_id": None,
        "last_successful_selection": None,
        "previous_successful_selection": None,
        "applied_migrations": (),
        "service_state": "stopped",
        "database_state": "ready",
        "backup_protections": (),
        "independently_held_backup_ids": (),
        "backup_protection_sha256": "a" * 64,
        "scheduled_backup_sha256": "b" * 64,
        "backup_timer_enabled": True,
        "backup_timer_state": "inactive",
        "downgrade_baseline_sha256": "c" * 64,
        "installed_release_count": 0,
        "installed_release_sha256": "d" * 64,
        "scheduler_resources": {"helper": True, "service": True, "timer": False, "environment": True},
        "scheduler_create": ("/etc/systemd/system/taskman-backup.timer",),
    }

    def provisioning(_remote: object, inputs: object) -> ChangeSet:
        observed_inputs.append(inputs)
        return ChangeSet(changed=False)

    def genesis(_remote: object, _config: object, _target: object, **kwargs: object) -> WorkflowResult:
        genesis_kwargs.update(kwargs)
        return WorkflowResult("deploy", "production", False, "already-current", {})

    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "preflight": lambda *_args: authority,
            "provisioning": provisioning,
            "genesis": genesis,
        }
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.exit_status is ExitStatus.OK
    assert observed_inputs[0].scheduler_create == frozenset({"/etc/systemd/system/taskman-backup.timer"})
    assert genesis_kwargs["scheduler_create"] == ("/etc/systemd/system/taskman-backup.timer",)


def test_post_pyinfra_authority_accepts_only_the_confirmed_absent_scheduler_delta() -> None:
    """A create delta cannot excuse concurrent release or existing-scheduler drift."""

    confirmed = {
        "selected_release_id": None,
        "last_successful_selection_id": None,
        "applied_migrations": (),
        "backup_protection_sha256": "a" * 64,
        "scheduled_backup_sha256": None,
        "backup_timer_enabled": False,
        "downgrade_baseline_sha256": "b" * 64,
    }
    created = {
        **confirmed,
        "scheduled_backup_sha256": "c" * 64,
        "backup_timer_enabled": True,
    }

    assert _matches_confirmed_preconvergence(
        created,
        confirmed,
        ("/usr/local/lib/taskman/taskman-backup.pyz", "/etc/systemd/system/taskman-backup.timer"),
    )
    assert not _matches_confirmed_preconvergence(
        {**created, "applied_migrations": (20260905120000,)},
        confirmed,
        ("/usr/local/lib/taskman/taskman-backup.pyz", "/etc/systemd/system/taskman-backup.timer"),
    )
    assert not _matches_confirmed_preconvergence(
        {**confirmed, "scheduled_backup_sha256": "c" * 64}, confirmed, ()
    )


def test_provision_preserves_confirmed_starting_state_when_pyinfra_refuses_before_release() -> None:
    """A post-confirmation provisioning error cannot erase the authorized plan."""

    host = Host()

    def refuse(*_args: object) -> ChangeSet:
        raise OpsError(ExitStatus.SAFETY, "pyinfra", "resource drift", changed=False)

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=_capabilities(host, provisioning=refuse),
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.facts["starting_state"] == {
        "authority": "validated",
        "candidate_release_id": artifact().manifest.release_id,
    }


def test_provision_discards_a_drifted_plan_and_repeats_authority_before_convergence() -> None:
    """A post-confirmation resource change must receive a new material plan."""

    host = Host()
    discoveries = iter(("first", "changed", "changed", "changed"))
    presented: list[object] = []

    capabilities = _capabilities(
        host,
        present_plan=lambda plan: presented.append(plan),
        confirm=lambda _plan: True,
    )
    capabilities = ProvisionCapabilities(
        **{
            **capabilities.__dict__,
            "discover": lambda _remote, _config, **_kwargs: next(discoveries),
        }
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.exit_status is ExitStatus.OK
    assert len(presented) == 2
    assert host.events == ["plan", "plan", "provisioning"]


def test_provision_yes_refuses_material_drift_after_confirmation_before_pyinfra() -> None:
    """`--yes` confirms one plan, not a later resource snapshot."""

    host = Host()
    discoveries = iter(("first", "changed"))
    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "discover": lambda _remote, _config, **_kwargs: next(discoveries),
        }
    )

    result = provision(
        Invocation(command="provision", environment="production", yes=True), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert host.events == ["plan"]


def test_provision_clean_input_drift_reidentifies_and_replans_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed clean checkout cannot reuse its pre-discovery identity."""

    from taskman_ops.workflows import provision as provision_module

    host = Host()
    old_inputs = object()
    fresh_inputs = object()
    identified = iter((old_inputs, fresh_inputs))
    matches = iter((True, False, True, True))
    resolved: list[object] = []
    monkeypatch.setattr(provision_module, "identify_clean_inputs", lambda _repo: next(identified))
    monkeypatch.setattr(provision_module, "clean_inputs_match", lambda *_args: next(matches), raising=False)
    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "target_resolution": lambda _remote, _config, _invocation, inputs: resolved.append(inputs) or artifact(),
        }
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.exit_status is ExitStatus.OK
    assert resolved == [old_inputs, old_inputs, fresh_inputs, fresh_inputs]
    assert host.events.count("plan") == 2
    assert host.events.count("provisioning") == 1


def test_provision_yes_refuses_clean_input_drift_before_pyinfra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--yes` cannot silently authorize a different clean source snapshot."""

    from taskman_ops.workflows import provision as provision_module

    host = Host()
    monkeypatch.setattr(provision_module, "identify_clean_inputs", lambda _repo: object())
    monkeypatch.setattr(provision_module, "clean_inputs_match", lambda *_args: False, raising=False)
    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "target_resolution": lambda *_args: artifact(),
        }
    )

    result = provision(
        Invocation(command="provision", environment="production", yes=True), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert "provisioning" not in host.events


def test_provision_dry_run_discovers_but_does_not_execute_the_pyinfra_deploy() -> None:
    host = Host()

    capabilities = _capabilities(
        host,
        provisioning=lambda *_args: (_ for _ in ()).throw(AssertionError("dry-run must not mutate")),
        confirm=lambda _plan: (_ for _ in ()).throw(AssertionError("dry-run must not prompt")),
    )

    result = provision(Invocation(command="provision", environment="production", dry_run=True), capabilities=capabilities)

    assert result.stage == "planned"
    assert result.changed is False
    assert host.events == ["discovery", "plan"]
    assert host.closed == 1


def test_provision_uses_one_rendered_caddy_plan_for_discovery_and_convergence() -> None:
    """One rendered Caddy plan is admitted, confirmed, then used for convergence."""

    host = Host()
    rendered = CaddyPlan(
        repository=CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
        repository_packages=(),
        packages=("caddy",),
        caddyfile="taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n",
    )
    renderer_calls = 0
    expected_digest = hashlib.sha256(rendered.caddyfile.encode("utf-8")).hexdigest()

    def caddy_plan(_config: EnvironmentConfig) -> CaddyPlan:
        nonlocal renderer_calls
        renderer_calls += 1
        return rendered

    def discover(_remote: object, _config: EnvironmentConfig, *, expected_caddyfile_sha256: str) -> None:
        assert expected_caddyfile_sha256 == expected_digest
        host.events.append("discovery")

    def provisioning(_remote: object, inputs: object) -> ChangeSet:
        assert inputs.caddy_plan is rendered
        assert hashlib.sha256(inputs.caddy_plan.caddyfile.encode("utf-8")).hexdigest() == expected_digest
        return host.converge("provisioning")

    capabilities = _capabilities(host, provisioning=provisioning)
    capabilities = ProvisionCapabilities(
        **{**capabilities.__dict__, "caddy_plan": caddy_plan, "discover": discover}
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.stage == "provisioned"
    assert renderer_calls == 1


def _capabilities(
    host: Host,
    *,
    provisioning=None,
    release=None,
    present_plan=None,
    confirm=None,
    artifact_value=None,
) -> ProvisionCapabilities:
    value = artifact() if artifact_value is None else artifact_value
    return ProvisionCapabilities(
        load_environment=lambda _name: environment_config(),
        decrypt_secrets=lambda _name: SimpleNamespace(database_password="database-password"),
        resolve_artifact=lambda _invocation: value,
        render_runtime_environment=lambda _config, _secrets: b"RUNTIME=value\n",
        render_pgpass=lambda _config, _secrets: b"pgpass\n",
        render_role_password_input=lambda _role, _password: b"role-password-input\n",
        render_plan=lambda _config, _artifact: host.events.append("plan") or {"candidate_release_id": value.manifest.release_id},
        present_plan=present_plan or (lambda _plan: None),
        confirm=confirm or (lambda _plan: True),
        connect=lambda _config: host,
        discover=lambda _remote, _config, **_kwargs: host.events.append("discovery"),
        provisioning=provisioning or (lambda _remote, _inputs: host.converge("provisioning")),
        caddy_plan=lambda _config: CaddyPlan(
            CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
            (),
            ("caddy",),
            "taskman.acme.tld {\n}\n",
        ),
        genesis=(
            (lambda remote, config, supplied, **_kwargs: release(remote, config, supplied))
            if release is not None
            else lambda _remote, _config, _artifact, **_kwargs: WorkflowResult(
            command="deploy", environment="production", changed=False, stage="already-current", facts={}
            )
        ),
    )
