"""Provisioning workflow contracts for the single pyinfra convergence path."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.environments import environment_config
from tests.support.secrets import no_registered_secrets_between_tests as no_secrets_between_tests
from taskman_ops.cli import Invocation
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.migrations import MigrationOrderError
from taskman_ops.output import WorkflowResult
from taskman_ops.remote import ChangeSet
from taskman_ops.services.caddy import CaddyOwnershipPlan, CaddyRepository, build_caddy_ownership_plan
from taskman_ops.provisioning import ProvisioningResult
from tests.support.caddy import authority_result, prepared_result
from taskman_ops.releases.artifacts import DeploymentTarget
from taskman_ops.releases.manifests import ArtifactManifest, MigrationFingerprint, VerifiedArtifact
from tests.workflows import support as workflow_support
from taskman_ops.workflows.provision import ProvisionCapabilities, _present_plan, provision
from taskman_ops.workflows.deploy import (
    _matches_confirmed_preconvergence,
    _starting_expected_state,
)


@pytest.fixture(autouse=True)
def controlled_clean_source_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep orchestration tests independent of this checkout's source state."""

    from taskman_ops.workflows import provision as provision_module

    monkeypatch.setattr(provision_module, "identify_clean_inputs", lambda _repo: object())
    monkeypatch.setattr(provision_module, "clean_inputs_match", lambda *_args: True)


def artifact(*, migrations: tuple[object, ...] = ()) -> DeploymentTarget:
    manifest = ArtifactManifest(
        3,
        "taskman",
        "0.2.0",
        "b" * 40,
        workflow_support.CANDIDATE,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "ubuntu26.04",
        "amd64",
        workflow_support.OTP_VERSION,
        workflow_support.ELIXIR_VERSION,
        workflow_support.NODE_VERSION,
        workflow_support.BUILDER_BASE_TAG,
        workflow_support.BUILDER_BASE_DIGEST,
        migrations,
        "taskman",
        workflow_support.HEX_VERSION,
        workflow_support.REBAR3_VERSION,
        workflow_support._ARTIFACT_SHA256,
        False,
    )
    return DeploymentTarget(
        artifact=VerifiedArtifact(
            Path("/nonexistent/taskman.tar.gz"),
            Path("/nonexistent/taskman.manifest.json"),
            Path("/nonexistent/taskman.tar.gz.sha256"),
            workflow_support._ARTIFACT_SHA256,
            manifest,
        ),
        release_record=None,
        source="built",
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
            "preflight": lambda _remote, _inputs: host.events.append("preflight") or _authority(),
        }
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.stage == "provisioned"
    assert host.events == [
        "preflight",
        "discovery",
        "plan",
        "present-plan",
        "confirm",
        "preflight",
        "discovery",
        "provisioning",
    ]


def test_provision_retains_admission_cleanup_warning_without_plan_drift() -> None:
    host = Host()

    class Authority(dict):
        warnings = ("transient helper cleanup was incomplete",)

    capabilities = _capabilities(host)
    capabilities = ProvisionCapabilities(
        **{**capabilities.__dict__, "preflight": lambda *_args: Authority(_authority())}
    )

    result = provision(
        Invocation(command="provision", environment="production", dry_run=True),
        capabilities=capabilities,
    )

    assert result.stage == "planned"
    assert result.warnings == ("transient helper cleanup was incomplete",)


def test_provision_refuses_missing_preflight_authority_before_plan_or_mutation() -> None:
    """Missing creation authority cannot fall through to generic convergence."""

    host = Host()
    capabilities = ProvisionCapabilities(
        **{**_capabilities(host).__dict__, "preflight": lambda *_args: None}
    )

    result = provision(
        Invocation(command="provision", environment="production"), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert host.events == []
    assert "plan" not in host.events
    assert "provisioning" not in host.events


def test_provision_refuses_missing_scheduler_creation_delta_before_plan_or_mutation() -> None:
    """Creation needs an explicit empty or absent-resource scheduler delta."""

    host = Host()
    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "preflight": lambda *_args: {"authority": "validated"},
        }
    )

    result = provision(
        Invocation(command="provision", environment="production"), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert host.events == []
    assert "plan" not in host.events
    assert "provisioning" not in host.events


@pytest.mark.parametrize(
    "authority_factory",
    (
        lambda: {**_authority(), "scheduler_create": ("/unmanaged/path",)},
        lambda: {"scheduler_create": ()},
        lambda: {**_authority(), "backup_protections": ({"invalid": "record"},)},
        lambda: {**_authority(), "independently_held_backup_ids": ("invalid",)},
    ),
)
def test_provision_refuses_invalid_first_observation_evidence_before_plan_or_mutation(
    authority_factory,
) -> None:
    """Projection evidence must fail safely before an operator can confirm it."""

    host = Host()

    class Authority(dict):
        warnings = ("observer cleanup warning",)

    capabilities = ProvisionCapabilities(
        **{**_capabilities(host).__dict__, "preflight": lambda *_args: Authority(authority_factory())}
    )

    result = provision(
        Invocation(command="provision", environment="production"), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.warnings == ("observer cleanup warning",)
    assert "plan" not in host.events
    assert "provisioning" not in host.events


def test_provision_refuses_invalid_refreshed_evidence_with_confirmed_snapshot_and_warning() -> None:
    """A refreshed invalid scheduler delta cannot lose the plan already confirmed."""

    host = Host()

    class Authority(dict):
        warnings = ("observer cleanup warning",)

    authorities = iter((
        Authority(_authority()),
        Authority({**_authority(), "scheduler_create": ("/unmanaged/path",)}),
    ))
    capabilities = ProvisionCapabilities(
        **{**_capabilities(host).__dict__, "preflight": lambda *_args: next(authorities)}
    )

    result = provision(
        Invocation(command="provision", environment="production"), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.warnings == ("observer cleanup warning",)
    assert result.facts["starting_state"] == _expected_starting_state()
    assert host.events == ["discovery", "plan", "discovery"]
    assert "provisioning" not in host.events


def test_provision_refuses_missing_host_resource_evidence_before_plan_or_mutation() -> None:
    """Unprojectable discovery is missing evidence, not an empty host state."""

    host = Host()
    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "discover": lambda *_args, **_kwargs: host.events.append("discovery") or None,
        }
    )

    result = provision(
        Invocation(command="provision", environment="production"), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert host.events == ["discovery"]
    assert "plan" not in host.events
    assert "provisioning" not in host.events


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
    assert host.events == ["credential-preflight"]
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
    assert host.events == [authority]
    assert "provisioning" not in host.events


def test_default_preflight_checks_packaged_resources_before_the_secret_writers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact resource admission must precede both protected credential writes."""

    from taskman_ops import provisioning as provisioning_module

    host = Host()
    checks: list[str] = []

    monkeypatch.setattr(
        provisioning_module,
        "validate_preconvergence_authority",
        lambda *_args: checks.append("observer") or _authority(),
    )

    monkeypatch.setattr(
        provisioning_module,
        "validate_existing_credential_authority",
        lambda *_args, **_kwargs: checks.append("credentials"),
    )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=ProvisionCapabilities(
            **{**_capabilities(host).__dict__, "preflight": provisioning_module.validate_existing_authority}
        ),
    )

    assert result.exit_status is ExitStatus.OK
    assert checks == ["observer", "credentials"] * 2


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
        lambda *_args: checks.append("observer") or _authority(),
        raising=False,
    )
    monkeypatch.setattr(
        provisioning_module,
        "validate_existing_credential_authority",
        lambda *_args, **_kwargs: checks.append("credentials"),
    )

    result = provision(
        Invocation(command="provision", environment="production"),
        capabilities=ProvisionCapabilities(
            **{**_capabilities(host).__dict__, "preflight": provisioning_module.validate_existing_authority}
        ),
    )

    assert result.exit_status is ExitStatus.OK
    assert checks == ["observer", "credentials"] * 2


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
    migration = MigrationFingerprint(filename="20260905120000_create_tasks.exs", sha256="d" * 64)
    value = artifact(migrations=(migration,))

    def release(_remote: object, _config: EnvironmentConfig, supplied: object) -> WorkflowResult:
        assert supplied is value
        assert supplied.manifest.migrations == (migration,)
        assert supplied.manifest.to_mapping()["migrations"] == [migration.to_mapping()]
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


def test_provision_preserves_invalid_migration_order_exit() -> None:
    """Invalid migration ordering remains the distinct local input classification."""

    host = Host()
    target = artifact(migrations=(
        MigrationFingerprint("20260905120000_create_lists.exs", "e" * 64),
        MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64),
    ))
    assert ArtifactManifest.from_mapping(target.manifest.to_mapping()) == target.manifest

    with pytest.raises(MigrationOrderError, match="migration versions must be sorted and unique"):
        provision(
            Invocation(command="provision", environment="production", artifact=target.artifact.archive),
            capabilities=_capabilities(host, artifact_value=target),
        )
    assert host.events == ["discovery"]
    assert host.closed == 1


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
        "prune_backup_ids": (),
        "scheduler_create": _authority()["scheduler_create"],
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
    assert result.facts["starting_state"] == _expected_starting_state()


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

    assert received["starting_state"] == _expected_starting_state()
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
        **_authority(),
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
        **_authority(),
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
        return ProvisioningResult(ChangeSet(changed=False), prepared_result(inputs.caddy_plan))

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


def test_absent_database_keeps_non_database_confirmation_authority() -> None:
    """Database creation is a delta, not a waiver for release-state drift."""

    authority = {
        "database_state": "absent",
        "selected_release_id": None,
        "last_successful_selection_id": None,
        "applied_migrations": (),
        "backup_protection_sha256": "a" * 64,
        "scheduled_backup_sha256": None,
        "backup_timer_enabled": False,
        "downgrade_baseline_sha256": "b" * 64,
    }

    assert _starting_expected_state({"host_authority": authority}) == {
        key: value for key, value in authority.items() if key != "database_state"
    }


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
    assert result.facts["starting_state"] == _expected_starting_state()


def test_provision_refuses_authority_drift_after_interactive_confirmation() -> None:
    """Interactive consent cannot be reused after the displayed host authority changes."""

    host = Host()
    discoveries = iter(({"state": "first"}, {"state": "changed"}, {"state": "changed"}, {"state": "changed"}))
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

    assert result.exit_status is ExitStatus.SAFETY
    assert len(presented) == 1
    assert host.events == ["plan"]


def test_provision_yes_refuses_material_drift_after_confirmation_before_pyinfra() -> None:
    """`--yes` confirms one plan, not a later resource snapshot."""

    host = Host()
    discoveries = iter(({"state": "first"}, {"state": "changed"}))
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


def test_provision_accepts_refreshed_free_space_counter_changes_after_confirmation() -> None:
    """Filesystem activity must not invalidate a plan when fresh admission still succeeds."""

    @dataclass(frozen=True)
    class DiscoveryFacts:
        available_disk_bytes: int
        backup_available_disk_bytes: int
        managed_path: str

    @dataclass(frozen=True)
    class Discovery:
        facts: DiscoveryFacts
        state: str

    host = Host()
    first_discovery = Discovery(
        DiscoveryFacts(40 * 1024**3, 41 * 1024**3, "/opt/taskman"), "partial"
    )
    refreshed_discovery = Discovery(
        DiscoveryFacts(40 * 1024**3 - 4096, 41 * 1024**3 - 4096, "/opt/taskman"), "partial"
    )
    discoveries = iter((first_discovery, refreshed_discovery))
    presented: list[object] = []
    capabilities = _capabilities(host, present_plan=lambda plan: presented.append(plan))
    capabilities = ProvisionCapabilities(
        **{
            **capabilities.__dict__,
            "discover": lambda _remote, _config, **_kwargs: next(discoveries),
        }
    )

    result = provision(
        Invocation(command="provision", environment="production", yes=True), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.OK
    assert host.events == ["plan", "provisioning"]
    assert presented[0]["starting_state"]["resource_authority"] == {
        "facts": {
            "available_disk_bytes": 40 * 1024**3,
            "backup_available_disk_bytes": 41 * 1024**3,
            "managed_path": "/opt/taskman",
        },
        "state": "partial",
    }
    assert presented[0]["resource_convergence"]["host"] == {
        "facts": {
            "available_disk_bytes": 40 * 1024**3,
            "backup_available_disk_bytes": 41 * 1024**3,
            "managed_path": "/opt/taskman",
        },
        "state": "partial",
    }


def test_provision_refuses_non_capacity_resource_drift_after_confirmation() -> None:
    """Ignoring transient capacity counters must not waive other resource authority."""

    host = Host()
    discoveries = iter(
        (
            {
                "facts": {
                    "available_disk_bytes": 40 * 1024**3,
                    "backup_available_disk_bytes": 41 * 1024**3,
                    "managed_path": "/opt/taskman",
                },
                "state": "partial",
            },
            {
                "facts": {
                    "available_disk_bytes": 40 * 1024**3,
                    "backup_available_disk_bytes": 41 * 1024**3,
                    "managed_path": "/unexpected-path",
                },
                "state": "partial",
            },
        )
    )
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


@pytest.mark.parametrize(
    "capacity_field", ("available_disk_bytes", "backup_available_disk_bytes")
)
def test_provision_refuses_refreshed_subthreshold_capacity_through_host_admission(
    monkeypatch: pytest.MonkeyPatch,
    capacity_field: str,
) -> None:
    """The confirmation refresh invokes host admission before capacity comparison."""

    from taskman_ops.host import acceptance as host_acceptance
    from taskman_ops.host.facts import CaddyState, HostFacts, MINIMUM_DISK_BYTES

    host = Host()
    config = environment_config()
    admitted_facts = HostFacts(
        os_id="ubuntu",
        ubuntu_release="26.04",
        architecture="amd64",
        pid1="systemd",
        sudo_available=True,
        postgres_available=False,
        postgres_sudo_available=None,
        active_ssh_port=config.ssh_port,
        memory_bytes=4 * 1024**3,
        available_disk_bytes=MINIMUM_DISK_BYTES,
        backup_available_disk_bytes=MINIMUM_DISK_BYTES,
        dns_addresses=(config.public_ipv4,),
        listeners=(),
        existing_paths=(),
        path_metadata=(),
        caddy_state=CaddyState.ABSENT,
        existing_units=(),
        existing_accounts=(),
        taskman_account_compatible=False,
        existing_databases=(),
        failed_checks=(),
    )
    refreshed_facts = replace(
        admitted_facts,
        **{capacity_field: MINIMUM_DISK_BYTES - 1},
    )
    facts = iter((admitted_facts, refreshed_facts))
    monkeypatch.setattr(host_acceptance, "collect_host_facts", lambda *_args, **_kwargs: next(facts))
    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "discover": host_acceptance.validate_provisionable_host,
        }
    )

    result = provision(
        Invocation(command="provision", environment="production", yes=True), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.INVALID
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
    matches = iter((False, True, True))
    resolved: list[object] = []
    from taskman_ops.services import systemd
    original_build = systemd.build_systemd_plan
    plans = []

    def build(config):
        assert len(resolved) == len(plans)
        plan = original_build(config)
        plans.append(plan)
        return plan

    monkeypatch.setattr(provision_module, "build_systemd_plan", build, raising=False)
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
    assert resolved == [old_inputs, fresh_inputs, fresh_inputs]
    # Draft host assets precede artifact resolution and are rebuilt on replan.
    assert len(plans) == 2
    assert host.events.count("plan") == 1
    assert host.events.count("provisioning") == 1


def test_provision_refuses_clean_input_drift_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any post-confirmation clean-source change requires a new invocation."""

    from taskman_ops.workflows import provision as provision_module

    host = Host()
    inputs = iter((object(), object()))
    matches = iter((True, False))
    resolved: list[object] = []
    monkeypatch.setattr(provision_module, "identify_clean_inputs", lambda _repo: next(inputs))
    monkeypatch.setattr(provision_module, "clean_inputs_match", lambda *_args: next(matches), raising=False)
    capabilities = ProvisionCapabilities(
        **{
            **_capabilities(host).__dict__,
            "target_resolution": lambda *_args: resolved.append(_args[-1]) or artifact(),
        }
    )

    result = provision(
        Invocation(command="provision", environment="production", yes=True), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert resolved == [resolved[0], resolved[0]]
    assert host.events == ["discovery", "plan", "discovery"]


def test_provision_refuses_clean_build_drift_during_post_confirmation_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build-time source mismatch after consent cannot become an invalid-input result."""
    from taskman_ops.workflows import provision as provision_module

    host = Host()
    inputs = object()
    resolutions = 0
    monkeypatch.setattr(provision_module, "identify_clean_inputs", lambda _repo: inputs)
    monkeypatch.setattr(provision_module, "clean_inputs_match", lambda *_args: True, raising=False)

    def resolve(*_args: object) -> object:
        nonlocal resolutions
        resolutions += 1
        if resolutions == 2:
            raise OpsError(
                ExitStatus.INVALID,
                "artifact",
                "source inputs changed before the fresh build completed",
                changed=False,
            )
        return artifact()

    capabilities = ProvisionCapabilities(
        **{**_capabilities(host).__dict__, "target_resolution": resolve}
    )

    result = provision(
        Invocation(command="provision", environment="production", yes=True), capabilities=capabilities
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "provisioning-incomplete"
    assert host.events == ["discovery", "plan", "discovery"]


def test_provision_retries_clean_build_drift_during_target_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build-time mismatch restarts provision's clean discovery and resolution cycle."""
    from taskman_ops.workflows import provision as provision_module

    host = Host()
    old_inputs = object()
    fresh_inputs = object()
    identified = iter((old_inputs, fresh_inputs))
    resolved: list[object] = []
    monkeypatch.setattr(provision_module, "identify_clean_inputs", lambda _repo: next(identified))
    monkeypatch.setattr(provision_module, "clean_inputs_match", lambda *_args: True, raising=False)

    def resolve(_remote: object, _config: EnvironmentConfig, _invocation: object, inputs: object) -> object:
        resolved.append(inputs)
        if len(resolved) == 1:
            raise OpsError(
                ExitStatus.INVALID,
                "artifact",
                "source inputs changed before the fresh build completed",
                changed=False,
            )
        return artifact()

    capabilities = ProvisionCapabilities(
        **{**_capabilities(host).__dict__, "target_resolution": resolve}
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.exit_status is ExitStatus.OK
    assert resolved == [old_inputs, fresh_inputs, fresh_inputs]
    assert host.events == ["discovery", "discovery", "plan", "discovery", "provisioning"]


def test_provision_refuses_after_exhausting_clean_resolution_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing provision's exhaustion guard would keep rediscovering an unstable checkout."""
    from taskman_ops.workflows import provision as provision_module

    host = Host()
    identified = 0
    resolutions = 0

    def identify(_repo: Path) -> object:
        nonlocal identified
        identified += 1
        return object()

    def resolve(*_args: object) -> object:
        nonlocal resolutions
        resolutions += 1
        if resolutions > 4:
            pytest.fail("provision resolution retried beyond its bounded attempt budget")
        raise OpsError(
            ExitStatus.INVALID,
            "artifact",
            "source inputs changed before the fresh build completed",
            changed=False,
        )

    monkeypatch.setattr(provision_module, "identify_clean_inputs", identify)
    capabilities = ProvisionCapabilities(
        **{**_capabilities(host).__dict__, "target_resolution": resolve}
    )

    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "provisioning-incomplete"
    assert resolutions == 4
    assert identified == 4
    assert host.events == ["discovery"] * 4


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


def test_provision_freezes_site_plan_through_confirmation():
    host = Host()
    rendered = build_caddy_ownership_plan(environment_config(), authority_result())
    calls = []
    def build(*_args):
        calls.append(rendered)
        return rendered
    def convergence(_remote, inputs):
        assert inputs.caddy_plan is rendered
        assert inputs.caddy_plan.site_content == "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n"
        return ProvisioningResult(host.converge("provisioning"), prepared_result(rendered))
    capabilities = replace(_capabilities(host), caddy_plan=build, provisioning=convergence)
    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)
    assert result.exit_status is ExitStatus.OK
    assert calls == [rendered]


def test_provision_freezes_systemd_bytes_through_confirmation(monkeypatch) -> None:
    """Re-rendering after confirmation would install bytes the operator never reviewed."""
    from taskman_ops.services import systemd

    host = Host()
    admitted = []

    def preflight(_remote, inputs):
        admitted.append(inputs.systemd_plan)
        # Source renderers may change after preparation, especially for dirty
        # source or explicit artifacts. Neither authority refresh nor installation
        # is allowed to consult them again.
        monkeypatch.setattr(systemd, "render_taskman_service", lambda _config: "changed source")
        return _authority()

    def provisioning(_remote, inputs):
        assert inputs.systemd_plan is admitted[0]
        content = inputs.systemd_plan.assets[0].content
        assert b"ExecStart=" in content
        assert b"changed source" not in content
        return host.converge("provisioning")

    capabilities = _capabilities(host, provisioning=provisioning)
    capabilities = ProvisionCapabilities(**{**capabilities.__dict__, "preflight": preflight})
    result = provision(
        Invocation(command="provision", environment="production", artifact=Path("explicit.tar.gz")),
        capabilities=capabilities,
    )

    assert result.exit_status is ExitStatus.OK
    assert len(admitted) == 2
    assert admitted[0] is admitted[1]


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
        render_runtime_environment=lambda _config, _secrets: b"RUNTIME=value\n",
        render_pgpass=lambda _config, _secrets: b"pgpass\n",
        render_role_password_input=lambda _role, _password: b"role-password-input\n",
        render_plan=lambda _config, _artifact: host.events.append("plan") or {"candidate_release_id": value.manifest.release_id},
        present_plan=present_plan or (lambda _plan: None),
        confirm=confirm or (lambda _plan: True),
        connect=lambda _config: host,
        discover=lambda _remote, _config, **_kwargs: host.events.append("discovery") or {"admission": "validated"},
        provisioning=lambda remote, inputs: _provisioned_result(
            provisioning(remote, inputs) if provisioning else host.converge("provisioning"), inputs
        ),
        caddy_plan=build_caddy_ownership_plan,
        caddy=lambda *_args: authority_result(),
        preparation=lambda *_args, **_kwargs: pytest.fail("bootstrap must use full deployment"),
        activation=lambda *_args, **_kwargs: pytest.fail("bootstrap is already activated"),
        completion=lambda *_args, **_kwargs: pytest.fail("bootstrap must use ordinary genesis"),
        genesis=(
            (lambda remote, config, supplied, **_kwargs: release(remote, config, supplied))
            if release is not None
            else lambda _remote, _config, _artifact, **_kwargs: WorkflowResult(
            command="deploy", environment="production", changed=False, stage="already-current", facts={}
            )
        ),
        preflight=lambda _remote, _inputs: _authority(),
        target_resolution=lambda _remote, _config, _invocation, _clean_inputs: value,
        downgrade_authority=lambda _remote, _config, _target: (False, ()),
        firewall=lambda *_args: _firewall_authority(),
    )


def _authority() -> dict[str, object]:
    return {
        "authority": "validated",
        "postgresql": {"branch": "existing", "cluster": {"version": "18", "name": "main", "port": 5432,
            "data_directory": "/var/lib/postgresql/18/main", "config_file": "/etc/postgresql/18/main/postgresql.conf",
            "hba_file": "/etc/postgresql/18/main/pg_hba.conf"}, "role": "compatible", "database": "compatible", "access": "ready"},
        "initial_database_empty": False,
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
        "scheduler_resources": {"helper": False, "service": False, "timer": False, "environment": False},
        "scheduler_create": (
            "/etc/systemd/system/taskman-backup.service",
            "/etc/systemd/system/taskman-backup.timer",
            "/etc/taskman/taskman-backup.env",
            "/usr/local/lib/taskman/taskman-backup.pyz",
        ),
    }


def _expected_starting_state() -> dict[str, object]:
    target = artifact()
    return {
        "authority": "validated",
        "candidate_release_id": target.release_id,
        "artifact_sha256": target.artifact_sha256,
        "artifact_source": "built",
        "source_dirty": False,
        "host_authority": _authority(),
        "resource_authority": {"admission": "validated"},
    }


def _firewall_authority(*, digest="a" * 64, source="192.0.2.1", missing=False):
    from taskman_ops.host_protocol import HostResult
    return HostResult(3, "firewall", "op-" + "1" * 32,
        "retryable" if missing else "succeeded", "firewall observation", {
            "reason": "ufw_missing" if missing else "compatible", "changed": False,
            "possibly_changed": False, "snapshot_sha256": None if missing else digest,
            "administrator_connection": {"source_address": source, "destination_address": "203.0.113.10"},
            "exceptions": (), "proposed": (), "completed": (),
        }, ())


def firewall_drift_capabilities():
    host = Host()
    observations = iter((_firewall_authority(), _firewall_authority(digest="b" * 64)))
    return host, replace(_capabilities(host), firewall=lambda *_args: next(observations))


def test_firewall_drift_stops_before_provisioning():
    host, capabilities = firewall_drift_capabilities()
    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)
    assert result.changed is False
    assert "provisioning" not in host.events
    assert result.stage == "firewall"
    assert result.exit_status is ExitStatus.SAFETY


def test_firewall_identity_change_stops_before_provisioning():
    host = Host()
    observations = iter((_firewall_authority(), _firewall_authority(source="192.0.2.2")))
    capabilities = replace(_capabilities(host), firewall=lambda *_args: next(observations))
    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)
    assert result.stage == "firewall"
    assert result.changed is False
    assert "provisioning" not in host.events


@pytest.mark.parametrize("dry_run,confirm", ((True, True), (False, False)))
def test_firewall_preview_remains_read_only_for_dry_run_and_cancellation(dry_run, confirm):
    host = Host()
    plans = []
    capabilities = _capabilities(host, confirm=lambda _plan: confirm, present_plan=plans.append)
    capabilities = replace(capabilities, firewall=lambda *_args: _firewall_authority(missing=True))
    result = provision(Invocation(command="provision", environment="production", dry_run=dry_run), capabilities=capabilities)
    assert result.changed is False
    assert "provisioning" not in host.events
    assert plans[0]["firewall"]["package_installation"] is True
    assert plans[0]["firewall"]["post_install_gate"] == "preserve compatible saved policy; actual coverage may reduce these maximum fallback additions"
    assert plans[0]["firewall"]["activation"] == "activate preserved compatible saved rules if UFW is inactive"
    assert plans[0]["firewall"]["proposed"][-1] == ("ufw", "--force", "enable")
    assert plans[0]["firewall"]["administrator_connection"]["source_address"] == "192.0.2.1"
    assert plans[0]["firewall"]["ownership_assumption"]
    assert plans[0]["firewall"]["defaults"]


def test_firewall_missing_api_stops_before_plan():
    host = Host()
    def inspect(*_args):
        raise OpsError(ExitStatus.REMOTE_PREFLIGHT, "firewall", "inspection_failed", False)
    result = provision(Invocation(command="provision", environment="production"), capabilities=replace(_capabilities(host), firewall=inspect))
    assert result.stage == "firewall"
    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert "plan" not in host.events
    assert "provisioning" not in host.events


def test_confirmed_firewall_authority_reaches_provisioning_inputs():
    host = Host()
    captured = []
    capabilities = _capabilities(host, provisioning=lambda _remote, inputs: captured.append(inputs) or ChangeSet(False))
    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)
    assert result.exit_status is ExitStatus.OK
    assert captured[0].firewall_authority == _firewall_authority()


def test_lost_firewall_helper_result_keeps_provisioning_incomplete_and_changed():
    host = Host()
    def converge(*_args):
        raise OpsError(ExitStatus.REMOTE_PREFLIGHT, "firewall", "lost helper result", True)
    result = provision(Invocation(command="provision", environment="production"), capabilities=_capabilities(host, provisioning=converge))
    assert result.changed is True
    assert result.stage == "firewall"
    assert result.facts["release_started"] is False


@pytest.mark.parametrize("release_fails", (False, True))
def test_public_provision_result_retains_execution_warnings(release_fails):
    host = Host()
    def convergence(_remote, _inputs):
        # Model the already-executed pyinfra capability receipt.
        return ChangeSet(True, ("firewall",), warnings=("transient helper cleanup was incomplete",))
    def release(_remote, _config, value):
        return WorkflowResult("deploy", "production", False,
            "failed" if release_fails else "already-current", {},
            exit_status=ExitStatus.REMOTE_PREFLIGHT if release_fails else ExitStatus.OK)
    result = provision(Invocation(command="provision", environment="production"),
        capabilities=_capabilities(host, provisioning=convergence, release=release))
    assert result.changed is True
    assert result.warnings == ("transient helper cleanup was incomplete",)


def test_application_ready_proxy_pending_preserves_real_facts():
    from tests.host_helper.test_deploy import _passing_verification_report
    host = Host()
    target = artifact()
    report = _passing_verification_report(target.release_id)
    report = {**report, 'scope': 'application', 'checks': [check for check in report['checks']
        if check['name'] not in {'caddy-service', 'public-readiness', 'public-hsts'}]}
    observations = dict(selected_release_id=target.release_id, last_successful_selection_id=None,
        applied_migrations=(), protected_backup_ids=(), backup_protection_sha256='a'*64,
        restore_target_sha256=None, database_state='ready', service_state='running',
        scheduled_backup_sha256='c'*64, backup_timer_enabled=True, backup_timer_state='active')
    capabilities = replace(_capabilities(host),
        caddy=lambda *_args: authority_result(branch='existing', active=True, imported=False, masked=False),
        preparation=lambda *_args, **_kwargs: WorkflowResult('deploy', 'production', False, 'application-prepared',
            dict(candidate_release_id=target.release_id, mutation_state='unchanged', local_report=report,
                 public_probe='ready', observations=observations, unavailable_fields=[])),
        genesis=lambda *_args, **_kwargs: pytest.fail('pending cannot finalize history'),
    )
    result = provision(Invocation(command='provision', environment='production', yes=True), capabilities=capabilities)
    assert result.exit_status == 13
    assert result.stage == 'application-provisioned-proxy-pending'
    assert result.facts['proxy_activation'] == 'pending'
    assert result.facts['deployment_verified'] is False
    assert result.facts['local_report'] == report
    assert result.facts['public_probe'] == 'ready'
    assert result.facts['observations'] == observations
    assert result.facts['caddy_receipt']['site_path'] == '/etc/caddy/sites-enabled/taskman.caddy'
    assert result.facts['starting_state']['host_authority']['selected_release_id'] is None
    assert 'ready on the deployment host' in result.next_action
    assert 'backup' in result.facts['scheduler_limitation']


def test_application_preparation_failure_never_becomes_proxy_pending():
    host = Host()
    capabilities = replace(_capabilities(host),
        caddy=lambda *_args: authority_result(branch='existing'),
        preparation=lambda *_args, **_kwargs: WorkflowResult('deploy', 'production', True, 'verification-failed',
            {'mutation_state': 'changed', 'local_report': None, 'public_probe': 'not-attempted',
             'observations': {'selected_release_id': None}, 'unavailable_fields': ['selected_release_id']}, exit_status=ExitStatus.READINESS),
    )
    result = provision(Invocation(command='provision', environment='production', yes=True), capabilities=capabilities)
    assert result.exit_status is ExitStatus.READINESS
    assert result.facts['mutation_state'] == 'changed'
    assert result.facts['caddy_receipt']['site_path'] == '/etc/caddy/sites-enabled/taskman.caddy'
    assert result.facts['observations']['selected_release_id'] is None


def _provisioned_result(value, inputs):
    return value if isinstance(value, ProvisioningResult) else ProvisioningResult(value, prepared_result(inputs.caddy_plan))


@pytest.mark.parametrize("force", [False, True])
def test_configuration_override_reaches_confirmation_refresh_without_application_access(force):
    host = Host()
    modes = []
    previews = []
    target_calls = []
    downgrade_calls = []
    def preflight(_remote, inputs):
        modes.append(inputs.force_postgresql_config)
        if not inputs.force_postgresql_config:
            raise OpsError(ExitStatus.REMOTE_PREFLIGHT, "postgresql", "SCRAM authentication failed", changed=False)
        authority = _authority()
        authority["postgresql"] = {**authority["postgresql"], "branch": "configure-existing", "access": "unproven"}
        return authority
    def target(_remote, _config, invocation, _clean):
        target_calls.append(invocation.force_postgresql_config)
        return artifact()
    def downgrade(_remote, _config, _target, *, force_postgresql_config=False):
        downgrade_calls.append(force_postgresql_config)
        return False, ()
    def convergence(_remote, inputs):
        host.events.append("access-proof")
        # Later convergence owns actual proof. Its failure must prevent any
        # application preparation or selection publication.
        raise OpsError(ExitStatus.REMOTE_PREFLIGHT, "postgresql", "post-convergence SCRAM proof failed", changed=False)
    capabilities = replace(_capabilities(host), preflight=preflight,
        target_resolution=target, downgrade_authority=downgrade,
        present_plan=lambda value: previews.append(value),
        provisioning=convergence,
        genesis=lambda *_args, **_kwargs: pytest.fail("failed proof cannot run genesis"),
        preparation=lambda *_args, **_kwargs: pytest.fail("failed proof cannot prepare application"))
    invocation = SimpleNamespace(**{**Invocation(command="provision", environment="production").__dict__, "force_postgresql_config": force})
    result = provision(invocation, capabilities=capabilities)
    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert modes == ([True, True] if force else [False])
    assert target_calls == ([True, True] if force else [])
    assert downgrade_calls == ([True, True] if force else [])
    if force:
        assert previews[0]["starting_state"]["host_authority"]["postgresql"]["access"] == "unproven"
        assert host.events[-1] == "access-proof"
    else:
        assert previews == []
        assert host.events == []


def test_provision_target_resolution_and_downgrade_bind_configuration_context(monkeypatch):
    from taskman_ops.workflows import provision as module
    from taskman_ops.workflows import deploy as deploy_module
    calls = []
    class Reached(Exception):
        pass
    def authority(*args, **kwargs):
        calls.append(kwargs)
        raise Reached
    monkeypatch.setattr(deploy_module, "deployment_admission_authority", authority)
    invocation = SimpleNamespace(force_postgresql_config=True)
    with pytest.raises(Reached):
        module._resolve_deployment_target(object(), environment_config(), invocation, None)
    assert calls == [{"mode": "provision", "force_postgresql_config": True}]
    monkeypatch.setattr(module, "_downgrade_acknowledgment", lambda *_args, **kwargs: calls.append(kwargs) or (False, ()))
    assert module._provision_downgrade_authority(object(), environment_config(), artifact(), force_postgresql_config=True) == (False, ())
    assert calls[-1] == {"mode": "provision", "force_postgresql_config": True}


@pytest.mark.parametrize("status,expected", [(0, None), (5, ExitStatus.REMOTE_PREFLIGHT), (10, ExitStatus.SAFETY)])
def test_sensitive_authority_maps_native_access_separately_from_credential_safety(monkeypatch, status, expected):
    from taskman_ops import provisioning as module
    from taskman_ops.services.systemd import build_systemd_plan
    config = environment_config()
    inputs = module.ProvisioningInputs(config=config,
        caddy_plan=build_caddy_ownership_plan(config, authority_result()), systemd_plan=build_systemd_plan(config),
        runtime_environment=b"runtime=value\n", pgpass=b"pgpass\n", role_password_input=b"role-password-input\n",
        force_postgresql_config=True)
    authority = _authority()
    authority["postgresql"] = {**authority["postgresql"], "branch": "configure-existing", "access": "unproven"}
    monkeypatch.setattr(module, "validate_preconvergence_authority", lambda *_: authority)
    modes = []
    def sensitive(*_args, **kwargs):
        modes.append(kwargs["authentication_mode"])
        return SimpleNamespace(exit_status=status, warnings=("cleanup warning",))
    monkeypatch.setattr("taskman_ops.helper_client.runner.invoke_sensitive_pgpass_authority", sensitive)
    remote = SimpleNamespace(run=lambda *_args, **_kwargs: SimpleNamespace(succeeded=True))
    if expected is None:
        assert module.validate_existing_authority(remote, inputs).warnings == ("cleanup warning",)
    else:
        with pytest.raises(OpsError) as raised:
            module.validate_existing_authority(remote, inputs)
        assert raised.value.status is expected
        assert raised.value.stage == ("postgresql" if status == 5 else "credential-preflight")
        assert raised.value.changed is False
        assert raised.value.warnings == ("cleanup warning",)
    assert modes == ["defer-for-configuration"]


def test_provision_downgrade_observation_preserves_native_postgresql_failure(monkeypatch):
    from taskman_ops.host_protocol import HostResult
    from taskman_ops.workflows import deploy as deploy_module
    from taskman_ops.workflows import provision as provision_module
    host = Host()
    def failed(_remote, request):
        return HostResult.for_request(request, "manual", "PostgreSQL SCRAM access proof failed",
            {"failed_boundary": "postgresql", "exit_code": 5}, ("transient helper cleanup was incomplete",))
    monkeypatch.setattr(deploy_module, "run_request", failed)
    capabilities = replace(_capabilities(host), downgrade_authority=provision_module._provision_downgrade_authority)
    result = provision(Invocation(command="provision", environment="production"), capabilities=capabilities)
    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.facts["failed_boundary"] == "postgresql"
    assert result.facts["exit_code"] == 5
    assert result.warnings == ("transient helper cleanup was incomplete",)
    assert result.changed is False
    assert result.facts["release_started"] is False
    assert "provisioning" not in host.events


def test_provision_binds_frozen_postgresql_observation_only_after_preflight():
    host = Host()
    drafts, final = [], []
    caps = _capabilities(host)
    def preflight(_remote, inputs):
        drafts.append(inputs.postgresql_plan)
        return _authority()
    def converge(_remote, inputs):
        final.append(inputs.postgresql_plan)
        return ProvisioningResult(host.converge("provisioning"), prepared_result(inputs.caddy_plan))
    result = provision(Invocation(command="provision", environment="production"),
        capabilities=replace(caps, preflight=preflight, provisioning=converge))
    assert result.stage == "provisioned"
    assert drafts == [None, None]
    assert final[0].cluster.version == "18"
    assert final[0].branch == "existing"


def test_provision_refuses_changed_postgresql_target_before_consequence():
    host = Host()
    calls = []
    def preflight(*_):
        authority = _authority()
        if calls:
            authority["postgresql"]["cluster"]["data_directory"] = "/var/lib/postgresql/18/other"
        calls.append(True)
        return authority
    result = provision(Invocation(command="provision", environment="production"),
        capabilities=replace(_capabilities(host), preflight=preflight))
    assert result.facts["failed_boundary"] == "postgresql"
    assert result.exit_status == ExitStatus.SAFETY
    assert "safety" in result.facts["postgresql"]["operator_setup"][0]
    assert "provisioning" not in host.events


def test_provision_does_not_prepare_or_publish_after_late_database_proof_failure():
    host = Host()
    def fail(_remote, _inputs):
        raise OpsError(ExitStatus.REMOTE_PREFLIGHT, "postgresql", "SCRAM proof failed", changed=True,
            state={"mutation_state": "changed"})
    result = provision(Invocation(command="provision", environment="production"),
        capabilities=replace(_capabilities(host), provisioning=fail))
    assert result.exit_status == ExitStatus.REMOTE_PREFLIGHT
    assert result.changed is True
    assert result.facts["release_started"] is False
    assert "release" not in host.events
    assert "provisioning_changed" not in result.facts


@pytest.mark.parametrize("earlier_change", [False, True])
def test_selected_target_transport_loss_keeps_postgresql_boundary_and_earlier_mutation(monkeypatch, earlier_change):
    from taskman_ops import provisioning as production
    from taskman_ops.remote import PyinfraRemote
    host = Host()
    commands = []
    class InspectionFailureRemote(PyinfraRemote):
        def run_deploy(self, _deploy, *, inputs):
            return ChangeSet(earlier_change, warnings=("prior deployment warning",))
        def run(self, argv, **_kwargs):
            commands.append(argv)
            raise OpsError(ExitStatus.REMOTE_PREFLIGHT, "remote", "native-diagnostic-canary",
                changed=False, state={"inspection_dispatched": True}, warnings=("inspection cleanup warning",))
        def close(self):
            host.close()
    remote = object.__new__(InspectionFailureRemote)
    monkeypatch.setattr(production, "guard_caddy_install", lambda *_: authority_result())
    monkeypatch.setattr(production, "prepare_caddy", lambda _remote, plan, _guarded: prepared_result(plan, changed=False))
    monkeypatch.setattr(production, "install_runtime_environment", lambda *_: pytest.fail("runtime write after failed inspection"))
    caps = replace(_capabilities(host), connect=lambda _: remote, provisioning=production.converge_provisioning)
    result = provision(Invocation(command="provision", environment="production"), capabilities=caps)
    assert result.exit_status == ExitStatus.REMOTE_PREFLIGHT
    assert result.facts["failed_boundary"] == "postgresql"
    assert result.facts["mutation_state"] == ("changed" if earlier_change else "unchanged")
    assert result.changed is earlier_change
    assert result.facts["inspection_dispatched"] is True
    assert result.facts["release_started"] is False
    assert result.warnings == ("prior deployment warning", "inspection cleanup warning")
    assert "native-diagnostic-canary" not in str(result.facts)
    assert len(commands) == 1


def test_override_dry_run_displays_selected_replacement_and_never_captures():
    host = Host()
    previews = []
    def preflight(*_):
        authority = _authority()
        authority["postgresql"].update(branch="configure-existing", access="unproven")
        return authority
    caps = replace(_capabilities(host), preflight=preflight, present_plan=previews.append,
        downgrade_authority=lambda *_a, **_k: (False, ()), provisioning=lambda *_: pytest.fail("configuration or recovery during dry run"))
    result = provision(Invocation(command="provision", environment="production", dry_run=True,
        force_postgresql_config=True), capabilities=caps)
    assert result.exit_status == 0 and result.stage == "planned" and not result.changed
    pg = previews[0]["postgresql"]
    assert pg["branch"] == "configure-existing" and pg["access"] == "unproven"
    assert pg["cluster"]["version"] == "18"
    assert pg["settings"]["listen_addresses"] == "127.0.0.1"
    assert "all consumers" in pg["warning"]
    assert "scram-sha-256" in pg["replacement_hba"]
    assert pg["recovery_parent"] == "/etc/taskman/postgresql-config-recovery"


@pytest.mark.parametrize("late_failure", [False, True, "later-deploy"])
def test_public_provision_retains_recovery_after_configuration(monkeypatch, late_failure):
    from taskman_ops import provisioning as production
    from taskman_ops.remote import PyinfraRemote
    from tests.host_helper.test_postgresql_recovery import recovery_request, recovery_result, DIRECTORY
    host = Host()
    class RemoteWithRecovery(PyinfraRemote):
        def run_deploy(self, _deploy, *, inputs):
            self._state._taskman_postgresql_recovery = recovery_result(recovery_request())
            if late_failure == "later-deploy":
                raise OpsError(8, "provisioning", "later deployment failed", changed=True)
            return ChangeSet(True, operations=("Validate and configure PostgreSQL",))
        def close(self):
            host.close()
    remote = object.__new__(RemoteWithRecovery)
    remote._state = SimpleNamespace()
    def preflight(*_):
        authority = _authority()
        authority["postgresql"].update(branch="configure-existing", access="unproven")
        return authority
    monkeypatch.setattr(production, "guard_caddy_install", lambda *_: authority_result())
    monkeypatch.setattr(production, "prepare_caddy", lambda _remote, plan, _guarded: prepared_result(plan, changed=False))
    def database(*_args, **_kwargs):
        if late_failure:
            raise OpsError(5, "postgresql", "SCRAM proof failed", changed=False)
        return ChangeSet(False)
    monkeypatch.setattr(production, "converge_database", database)
    monkeypatch.setattr(production, "install_runtime_environment", lambda *_: ChangeSet(False))
    caps = replace(_capabilities(host), connect=lambda _: remote, preflight=preflight,
        downgrade_authority=lambda *_a, **_k: (False, ()), provisioning=production.converge_provisioning)
    result = provision(Invocation(command="provision", environment="production", yes=True,
        force_postgresql_config=True), capabilities=caps)
    assert result.changed
    assert result.facts["recovery_directory"] == DIRECTORY
    assert result.facts["postgresql_recovery"]["cluster"]["version"] == "18"
    assert result.facts["mutation_state"] == "changed"
    if late_failure:
        assert result.exit_status == (8 if late_failure == "later-deploy" else 5)
        assert result.facts["release_started"] is False
        assert "release" not in host.events
    else:
        assert result.exit_status == 0

@pytest.mark.parametrize("host_address,network", [("127.0.0.1", "127.0.0.1/32"), ("::1", "::1/128")])
def test_postgresql_failure_renders_targeted_setup_and_retained_resource_facts(host_address, network):
    from taskman_ops.output import render_human, render_json
    host = Host()
    def failed(_remote, _inputs):
        raise OpsError(5, "postgresql", "SCRAM proof failed", changed=True,
            state={"mutation_state": "changed", "failed_check": "scram-application-access",
                "postgresql_resources": {"role": "created", "database": "created", "credentials": "installed"}})
    caps = replace(_capabilities(host),
        load_environment=lambda _: environment_config(database_host=host_address, database_port=5432,
            **({"public_ipv6": "2001:db8::10"} if host_address == "::1" else {})),
        provisioning=failed)
    result = provision(Invocation(command="provision", environment="production"), capabilities=caps)
    assert result.exit_status == 5 and result.changed
    context = result.facts["postgresql"]
    assert context["endpoint"] == {"host": host_address, "port": 5432, "role": "taskman", "database": "taskman_prod"}
    assert context["cluster"]["version"] == "18"
    assert context["hba_file"] == "/etc/postgresql/18/main/pg_hba.conf"
    assert context["failed_check"] == "scram-application-access"
    assert context["hba_example"] == f"host    taskman_prod    taskman    {network}    scram-sha-256"
    assert context["retry_command"] == "./ops/taskman provision production"
    for encoded in (render_human(result), render_json(result)):
        assert "pg_hba_file_rules" in encoded and "first matching" in encoded
        assert "credentials" in encoded and "all consumers" in encoded
        assert context["retry_command"] in encoded
    assert result.facts["postgresql_resources"]["role"] == "created"
    assert result.facts["release_started"] is False
    assert "release" not in host.events

@pytest.mark.parametrize("branch,role,database,access", [
    ("bootstrap", "absent", "absent", "unproven"),
    ("existing", "compatible", "absent", "unproven"),
    ("existing", "compatible", "compatible", "ready"),
    ("configure-existing", "compatible", "compatible", "unproven"),
])
def test_postgresql_preview_distinguishes_resource_delta_and_actual_access(branch, role, database, access):
    from taskman_ops.output import render_human, render_json
    host = Host()
    def preflight(*_):
        state = _authority()
        state["postgresql"].update(branch=branch, role=role, database=database, access=access)
        if branch == "bootstrap":
            state["postgresql"]["cluster"] = None
        return state
    caps = replace(_capabilities(host), preflight=preflight,
        downgrade_authority=lambda *_a, **_k: (False, ()),
        provisioning=lambda *_: pytest.fail("dry-run consequence"))
    result = provision(Invocation(command="provision", environment="production", dry_run=True,
        force_postgresql_config=branch == "configure-existing"), capabilities=caps)
    assert result.exit_status == 0 and result.stage == "planned" and not result.changed
    pg = result.facts["plan"]["postgresql"]
    assert pg["endpoint"] == {"host": "127.0.0.1", "port": 5432, "role": "taskman", "database": "taskman_prod"}
    assert pg["resource_actions"] == {"role": "create" if role == "absent" else "adopt", "database": "create" if database == "absent" else "adopt"}
    assert pg["access"] == access
    assert pg["required_authentication"] == "scram-sha-256"
    assert pg["post_creation_access_failure_possible"] is (role == "absent" or database == "absent")
    assert pg["restart_possible"] is (branch != "existing")
    for encoded in (render_human(result), render_json(result)):
        assert 'scram-sha-256' in encoded and access in encoded


def test_admission_access_failure_retains_selected_observation_for_operator_setup(monkeypatch):
    from taskman_ops import provisioning as production
    host = Host()
    monkeypatch.setattr(production, "validate_preconvergence_authority", lambda *_: _authority())
    def failed(*_args, **_kwargs):
        raise OpsError(5, "postgresql", "application access failed", warnings=("cleanup warning",))
    monkeypatch.setattr(production, "validate_existing_credential_authority", failed)
    result = provision(Invocation(command="provision", environment="production"),
        capabilities=replace(_capabilities(host), preflight=production.validate_existing_authority))
    assert result.exit_status == 5 and not result.changed
    assert result.facts["postgresql"]["hba_file"] == "/etc/postgresql/18/main/pg_hba.conf"
    assert result.facts["postgresql"]["retry_command"] == "./ops/taskman provision production"
    assert result.warnings == ("cleanup warning",)
    assert result.facts["release_started"] is False
    assert "plan" not in host.events


@pytest.mark.parametrize("prior_changed,lost_creation", [(True, False), (False, True)])
def test_real_database_failure_boundary_preserves_prior_or_possible_mutation(monkeypatch, prior_changed, lost_creation):
    from taskman_ops import provisioning as production
    from taskman_ops.remote import PyinfraRemote, CommandResult
    from taskman_ops.output import render_human, render_json
    from tests.support.remotes import ScriptedRemote
    host = Host()
    responses = [CommandResult(0, "/var/lib/postgresql/18/main|/etc/postgresql/18/main/postgresql.conf|/etc/postgresql/18/main/pg_hba.conf|5432\n"),
        CommandResult(0), CommandResult(0), CommandResult(0, "psql (PostgreSQL) 18.6\n"), CommandResult(0)]
    if lost_creation:
        responses += [OpsError(5, "remote", "native-secret-canary")]
    else:
        responses += [CommandResult(0), CommandResult(0), CommandResult(1, stderr="native-secret-canary")]
    scripted = ScriptedRemote.from_responses(responses)
    class Remote(PyinfraRemote):
        def run_deploy(self, _deploy, *, inputs):
            return ChangeSet(prior_changed, operations=("Install managed units",) if prior_changed else ())
        def run(self, argv, **kwargs):
            return scripted.run(argv, **kwargs)
        def close(self):
            host.close()
    remote = object.__new__(Remote)
    def preflight(*_):
        authority = _authority()
        authority["postgresql"].update(role="absent", database="absent", access="unproven")
        return authority
    monkeypatch.setattr(production, "guard_caddy_install", lambda *_: authority_result())
    monkeypatch.setattr(production, "prepare_caddy", lambda _r, plan, _g: prepared_result(plan, changed=prior_changed))
    monkeypatch.setattr(production, "install_runtime_environment", lambda *_: pytest.fail("runtime after failed access"))
    result = provision(Invocation(command="provision", environment="production"), capabilities=replace(_capabilities(host),
        connect=lambda _: remote, preflight=preflight, provisioning=production.converge_provisioning))
    assert result.exit_status == 5 and result.changed
    assert result.facts["mutation_state"] == ("changed" if prior_changed else "unknown")
    assert result.facts["postgresql_resources"]["role"] == ("creation-unproven" if lost_creation else "created")
    assert result.facts["postgresql_resources"]["database"] == ("absent" if lost_creation else "created")
    assert result.facts["postgresql"]["cluster"]["version"] == "18"
    assert result.facts["release_started"] is False
    for encoded in (render_human(result), render_json(result)):
        assert "native-secret-canary" not in encoded
        assert "./ops/taskman provision production" in encoded
    assert "release" not in host.events


@pytest.mark.parametrize("failure,failed_check", [
    ("unsupported-client", "scram-client-prerequisite"),
    ("retained-access", "scram-application-access"),
    ("unknown-selection", "selected-target-or-resource-safety"),
])
def test_first_helper_preflight_failure_retains_selected_setup_in_public_output(tmp_path, monkeypatch, failure, failed_check):
    from contextlib import nullcontext
    from subprocess import CompletedProcess
    from taskman_ops import provisioning as production
    from taskman_ops.host_helper import database as native
    from taskman_ops.host_helper.operations import discover as observer
    from taskman_ops.workflows import helper
    from taskman_ops.host_protocol import encode_result, decode_result
    from taskman_ops.output import render_human, render_json
    from tests.host_helper.test_discovery import _state
    credentials = tmp_path / "pgpass"
    credentials.write_bytes(b"credential-secret-canary")
    credentials.chmod(0o600)
    monkeypatch.setattr(observer, "lifecycle_lock", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(observer, "_validate_managed_resources", lambda *_a, **_k: None)
    monkeypatch.setattr(observer, "observe_host_state", lambda *_a, **_k: _state())
    selected = _authority()["postgresql"]
    selected["access"] = "unproven"
    def selected_observation(*_):
        if failure == "unknown-selection":
            raise observer.PostgresqlObservationError("selected cluster inspection unavailable", 5)
        return selected
    monkeypatch.setattr(observer, "_observe_postgresql_authority", selected_observation)
    monkeypatch.setattr(observer, "validate_credentials", lambda *_: None)
    original_observe = observer._observe_provision_database
    monkeypatch.setattr(observer, "_observe_provision_database", lambda db, _path, track, force:
        original_observe(db, credentials, track, force))
    calls = []
    def command(argv, **kwargs):
        calls.append(argv)
        if argv == ("psql", "--version"):
            return CompletedProcess(argv, 0, b"psql (PostgreSQL) 15.8\n" if failure == "unsupported-client" else b"psql (PostgreSQL) 18.6\n", b"")
        assert "require_auth=scram-sha-256" in argv[argv.index("--dbname") + 1]
        return CompletedProcess(argv, 1, b"", b"native-secret-canary")
    monkeypatch.setattr(native, "run_command", command)
    original_run = helper.run_request
    def local_request(remote, request):
        return original_run(remote, request, invoker=lambda _r, _p, req:
            decode_result(encode_result(observer.provision_authority(req))))
    monkeypatch.setattr(helper, "run_request", local_request)
    host = Host()
    # Retained-byte transport is external to this native failure projection test.
    host.run = lambda *_a, **_k: SimpleNamespace(succeeded=True)
    result = provision(Invocation(command="provision", environment="production"),
        capabilities=replace(_capabilities(host), preflight=production.validate_existing_authority))
    assert result.exit_status == 5 and not result.changed
    context = result.facts["postgresql"]
    assert context["cluster"] == (None if failure == "unknown-selection" else selected["cluster"])
    assert context["hba_file"] == (None if failure == "unknown-selection" else selected["cluster"]["hba_file"])
    assert context["failed_check"] == failed_check
    if failure == "unknown-selection":
        assert calls == []
    else:
        assert calls[0] == ("psql", "--version")
    for encoded in (render_human(result), render_json(result)):
        assert failed_check in encoded
        if context["hba_file"] is not None:
            assert context["hba_file"] in encoded
        assert "native-secret-canary" not in encoded and "credential-secret-canary" not in encoded
        assert context["retry_command"] in encoded
        if failure == "unsupported-client":
            assert "version 16 or later" in encoded and "require_auth" in encoded
    assert result.facts["release_started"] is False
    assert not {"plan", "discovery", "provisioning", "release"}.intersection(host.events)


@pytest.mark.parametrize("requested,dry_run", [(False, True), (True, False)])
def test_provision_projects_protocol_frozen_authority_through_real_host_admission(
    monkeypatch, requested, dry_run,
):
    """Frozen helper mappings must survive admission into the confirmed plan."""
    from taskman_ops import provisioning as production
    from taskman_ops.host.acceptance import validate_provisionable_host
    from taskman_ops.host_protocol import HostResult
    from taskman_ops.output import register_secret, render_human, render_json
    from taskman_ops.workflows import helper
    from taskman_ops.remote import CommandResult
    from tests.host.test_facts import fact_responses, direct_dns
    from tests.support.remotes import ScriptedRemote

    responses = fact_responses(postgres=True)
    del responses[15:17]  # PostgreSQL authority replaces these native database reads.
    scripted = ScriptedRemote.from_responses(responses * (1 if dry_run else 2))

    class RemoteHost(Host):
        def run(self, argv, **kwargs):
            if argv[:2] == ("sh", "-ceu") and argv[3] == "taskman-credential-authority":
                assert argv[-1] == "/etc/taskman/pgpass"
                assert kwargs["sensitive"] is True
                return CommandResult(0)
            return scripted.run(argv, **kwargs)

    host = RemoteHost()
    state = _authority()
    del state["scheduler_create"]  # The admitted scheduler delta is controller-derived.
    state["postgresql"].update(
        branch="configure-existing" if requested else "existing", access="unproven",
    )
    state["initial_database_empty"] = True
    receipts = []

    def helper_receipt(_remote, request):
        result = HostResult.for_request(request, "succeeded", "authority observed", state)
        receipts.append(result)
        return result

    monkeypatch.setattr(helper, "run_request", helper_receipt)
    # Credential transport is external; retain real closed projection validation and admission.
    monkeypatch.setattr(production, "validate_existing_credential_authority",
        lambda *_args, **_kwargs: SimpleNamespace(warnings=()))
    discoveries = []
    authorities = []

    def discover(remote, config, **kwargs):
        authorities.append(kwargs["provision_authority"])
        observed = validate_provisionable_host(remote, config, resolver=direct_dns, **kwargs)
        discoveries.append(observed)
        return observed

    canary = "projection-credential-canary"
    register_secret(canary)
    presented = []
    caps = replace(_capabilities(host, present_plan=presented.append),
        preflight=production.validate_existing_authority, discover=discover,
        render_plan=lambda *_args: {"credential_example": canary},
        render_runtime_environment=lambda *_args: canary.encode(),
        render_pgpass=lambda *_args: canary.encode(),
        render_role_password_input=lambda *_args: canary.encode(),
        downgrade_authority=lambda *_args, **_kwargs: (False, ()),
    )
    result = provision(Invocation(command="provision", environment="production",
        artifact=Path("explicit.tar.gz"), dry_run=dry_run,
        force_postgresql_config=requested), capabilities=caps)

    assert result.exit_status is ExitStatus.OK
    assert result.stage == ("planned" if dry_run else "provisioned")
    starting_state = presented[0]["starting_state"]
    resources = starting_state["resource_authority"]
    assert starting_state["host_authority"]["postgresql"] == state["postgresql"]
    assert resources["facts"]["postgresql"] == state["postgresql"]
    assert resources["facts"]["listeners"] == ({"address": "*", "port": 2202},)
    assert resources["facts"]["initial_database_empty"] is True
    assert resources["state"] == "partial"
    assert resources["caddy_state"] == resources["facts"]["caddy_state"]
    assert starting_state["host_authority"]["scheduler_create"] == _authority()["scheduler_create"]
    assert isinstance(authorities[0], production.ProvisionAuthority)
    assert discoveries[0].facts.postgresql is receipts[0].state["postgresql"]
    assert authorities[0]["postgresql"] is receipts[0].state["postgresql"]
    assert resources["facts"]["postgresql"] is not discoveries[0].facts.postgresql
    assert presented[0]["credential_example"] == "[REDACTED]"
    assert canary not in render_human(result)
    assert canary not in render_json(result)
    if dry_run:
        assert not result.changed
        assert not host.converged
    else:
        assert result.facts["starting_state"] == starting_state
        assert host.converged == {"provisioning"}
    # Mutating the owned plan projection must not thaw or alter the original receipt/facts.
    resources["facts"]["postgresql"]["cluster"]["name"] = "changed-plan"
    assert discoveries[0].facts.postgresql["cluster"]["name"] == "main"
    with pytest.raises(TypeError):
        discoveries[0].facts.postgresql["cluster"]["name"] = "changed-input"
    assert len(scripted.calls) == 16 * (1 if dry_run else 2)
    assert not scripted.responses
    assert host.closed == 1


@pytest.mark.parametrize("dataclass_root", [False, True])
def test_provision_compares_all_recursively_projected_authority_after_confirmation(dataclass_root):
    """Mappings/dataclasses inside nested sequences must remain comparison authority."""
    from types import MappingProxyType

    @dataclass(frozen=True)
    class Resource:
        identity: object
        metadata: str = field(default="preserved", init=False)

    def discovery(identity):
        nested = MappingProxyType({"resources": ([Resource(MappingProxyType({"name": identity}))],)})
        return Resource(nested) if dataclass_root else nested

    host = Host()
    observations = iter((discovery("first"), discovery("changed")))
    presented = []
    caps = replace(_capabilities(host, present_plan=presented.append),
        discover=lambda *_args, **_kwargs: next(observations))
    result = provision(Invocation(command="provision", environment="production", yes=True),
        capabilities=caps)

    assert result.exit_status is ExitStatus.SAFETY
    assert not result.changed
    assert not host.converged
    projected = presented[0]["starting_state"]["resource_authority"]
    if dataclass_root:
        assert projected["metadata"] == "preserved"
        projected = projected["identity"]
    assert projected == {
        "resources": ([{"identity": {"name": "first"}, "metadata": "preserved"}],),
    }
