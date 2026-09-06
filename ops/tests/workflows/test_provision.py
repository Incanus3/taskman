"""Provisioning workflow contracts for the single pyinfra convergence path."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from types import SimpleNamespace

from tests.test_config import valid_environment
from taskman_ops.cli import Invocation
from taskman_ops.config import EnvironmentConfig
from taskman_ops.output import WorkflowResult
from taskman_ops.remote import ChangeSet
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository
from taskman_ops.workflows.provision import ProvisionCapabilities, provision


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment())


def artifact() -> object:
    return SimpleNamespace(
        manifest=SimpleNamespace(
            target_os="ubuntu26.04",
            architecture="amd64",
            release_id="0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
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
    assert first.facts["converged_stages"] == ("provisioning", "release")
    assert second.changed is False
    assert second.facts["converged_stages"] == ()
    assert host.events.index("discovery") < host.events.index("provisioning") < host.events.index("release")
    assert "verify" not in host.events
    assert host.closed == 2


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
    assert host.events == ["plan", "discovery"]
    assert host.closed == 1


def test_provision_passes_the_one_preconfirmed_caddy_plan_to_discovery_and_convergence() -> None:
    """The Caddy bytes confirmed by the operator are the deploy input."""

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
    confirm=None,
) -> ProvisionCapabilities:
    value = artifact()
    return ProvisionCapabilities(
        load_environment=lambda _name: config(),
        decrypt_secrets=lambda _name: SimpleNamespace(database_password="database-password"),
        resolve_artifact=lambda _invocation: value,
        render_runtime_environment=lambda _config, _secrets: b"RUNTIME=value\n",
        render_pgpass=lambda _config, _secrets: b"pgpass\n",
        render_role_password_input=lambda _role, _password: b"role-password-input\n",
        render_plan=lambda _config, _artifact: host.events.append("plan") or {"candidate_release_id": value.manifest.release_id},
        present_plan=lambda _plan: None,
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
        release_transaction=release
        or (lambda _remote, _config, _artifact: WorkflowResult(
            command="deploy", environment="production", changed=False, stage="already-current", facts={}
        )),
    )
