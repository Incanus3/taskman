"""Production-path proof for programmatic pyinfra provisioning."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyinfra.api import Config, Inventory, State, deploy
from pyinfra.operations import files
from taskman_ops.cli import Invocation
from taskman_ops.output import WorkflowResult
from taskman_ops.pyinfra import conditional_convergence
from taskman_ops.provisioning import taskman_provisioning
from taskman_ops.remote import ChangeSet, PyinfraRemote
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.provisioning import ProvisioningInputs
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository
from tests.test_config import valid_environment
from tests.workflows.test_provision import artifact, config


_CADDY_PLAN = CaddyPlan(
    repository=CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
    repository_packages=(),
    packages=("caddy",),
    caddyfile="taskman.acme.tld {\n}\n",
)


@dataclass
class DeployTraceRemote(PyinfraRemote):
    """Records the one programmatic deploy expected from production provision."""

    added: list[object] = field(default_factory=list)
    executions: int = 0
    closed: int = 0

    def run_deploy(self, deploy: object, *, inputs: object) -> ChangeSet:
        self.added.append((deploy, inputs))
        self.executions += 1
        return ChangeSet(changed=True, operations=("Converge Taskman host",))

    def close(self) -> None:
        self.closed += 1


def test_default_provision_path_adds_and_executes_one_pyinfra_deploy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Production must not fall back to any of the former direct-shell capabilities."""

    workflow = importlib.import_module("taskman_ops.workflows.provision")
    provisioning = importlib.import_module("taskman_ops.provisioning")
    remote = DeployTraceRemote()

    def legacy_direct_shell(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("production invoked a legacy direct-shell capability")

    for name in (
        "converge_baseline_host",
        "apply_caddy_install",
        "apply_postgresql_native_configuration",
        "apply_systemd_assets",
    ):
        monkeypatch.setattr(workflow, name, legacy_direct_shell, raising=False)

    monkeypatch.setattr(provisioning, "converge_database", lambda *_args, **_kwargs: ChangeSet(changed=False))
    monkeypatch.setattr(provisioning, "install_runtime_environment", lambda *_args, **_kwargs: ChangeSet(changed=False))
    monkeypatch.setattr(workflow, "load_environment", lambda _name: config())
    monkeypatch.setattr(workflow, "decrypt_secrets", lambda _name: SimpleNamespace(database_password="database-password"))
    monkeypatch.setattr(workflow, "_resolve_artifact", lambda _invocation: artifact())
    monkeypatch.setattr(workflow, "render_runtime_environment", lambda _config, _secrets: b"RUNTIME=value\n")
    monkeypatch.setattr(workflow, "render_pgpass", lambda _config, _secrets: b"pgpass\n")
    monkeypatch.setattr(workflow, "build_caddy_plan", lambda _config: _CADDY_PLAN)
    monkeypatch.setattr(workflow, "_render_plan", lambda _config, _artifact: {"candidate_release_id": "release"})
    monkeypatch.setattr(workflow, "_present_plan", lambda _plan: None)
    monkeypatch.setattr(workflow, "_confirm", lambda _plan: True)
    monkeypatch.setattr(workflow, "connect", lambda _config: remote)
    monkeypatch.setattr(workflow, "validate_provisionable_host", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        workflow,
        "deploy_first_release",
        lambda *_args: WorkflowResult(
            command="deploy", environment="production", changed=False, stage="already-current", facts={}
        ),
    )

    result = workflow.provision(
        Invocation(command="provision", environment="production"),
        capabilities=workflow._default_capabilities(),
    )

    assert result.stage == "provisioned"
    assert remote.executions == 1
    assert len(remote.added) == 1
    assert remote.added[0][0] is taskman_provisioning
    assert remote.closed == 1


def test_real_taskman_deploy_converges_declared_state_then_repairs_drift(monkeypatch, tmp_path: Path) -> None:
    """The packaged production callable reaches add_deploy/run_ops in declaration order."""

    declaration_order: list[str] = []
    targets = {
        "baseline": tmp_path / "baseline",
        "caddy": tmp_path / "caddy",
        "postgresql": tmp_path / "postgresql",
        "systemd": tmp_path / "systemd",
    }

    def declaration(name: str):
        def declare(*_args: object) -> None:
            declaration_order.append(name)
            files.put(
                StringIO(f"{name}=desired\n"),
                targets[name].as_posix(),
                add_deploy_dir=False,
                name=f"Test {name} desired state",
            )

        return declare

    monkeypatch.setattr("taskman_ops.host.baseline.declare_baseline", declaration("baseline"))
    monkeypatch.setattr("taskman_ops.services.caddy.declare_caddy", declaration("caddy"))
    monkeypatch.setattr("taskman_ops.services.postgresql.declare_postgresql", declaration("postgresql"))
    monkeypatch.setattr("taskman_ops.services.systemd.declare_systemd", declaration("systemd"))

    def run_once() -> ChangeSet:
        inventory = Inventory((["@local"], {}))
        state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
        host = inventory.get_host("@local")
        state.activate_host(host)
        remote = PyinfraRemote(
            host,
            EnvironmentConfig.model_validate(valid_environment()),
            inventory=inventory,
            state=state,
        )
        inputs = ProvisioningInputs(
            config=EnvironmentConfig.model_validate(valid_environment()),
            caddy_plan=_CADDY_PLAN,
            runtime_environment=b"RUNTIME=value\n",
            pgpass=b"pgpass\n",
            role_password_input=b"role-password-input\n",
        )
        return remote.run_deploy(taskman_provisioning, inputs=inputs)

    first = run_once()
    second = run_once()
    targets["systemd"].write_text("drift\n", encoding="utf-8")
    repaired = run_once()

    assert declaration_order == ["baseline", "caddy", "postgresql", "systemd"] * 3
    assert first.changed is True
    assert second == ChangeSet(changed=False)
    assert repaired.changed is True
    assert targets["systemd"].read_text(encoding="utf-8") == "systemd=desired\n"


def test_converge_provisioning_orders_secret_and_database_boundaries_after_the_deploy(monkeypatch) -> None:
    """The actual convergence function runs later custom boundaries in their safe order."""

    remote = DeployTraceRemote()
    events: list[str] = []

    def database(*_args: object, **_kwargs: object) -> ChangeSet:
        events.append("postgresql")
        return ChangeSet(changed=True, operations=("postgresql",))

    def runtime(*_args: object, **_kwargs: object) -> ChangeSet:
        events.append("runtime")
        return ChangeSet(changed=True, operations=("runtime",))

    provisioning = importlib.import_module("taskman_ops.provisioning")
    monkeypatch.setattr(provisioning, "converge_database", database)
    monkeypatch.setattr(provisioning, "install_runtime_environment", runtime)
    changes = provisioning.converge_provisioning(
        remote,
        ProvisioningInputs(
            config=EnvironmentConfig.model_validate(valid_environment()),
            caddy_plan=_CADDY_PLAN,
            runtime_environment=b"RUNTIME=value\n",
            pgpass=b"pgpass\n",
            role_password_input=b"role-password-input\n",
        ),
    )

    assert len(remote.added) == 1
    assert remote.added[0][0] is taskman_provisioning
    assert events == ["postgresql", "runtime"]
    assert changes.operations == ("Converge Taskman host", "postgresql", "runtime")


def test_converge_provisioning_stops_later_mutation_after_a_database_refusal(monkeypatch) -> None:
    """A material database refusal must prevent protected-runtime mutation."""

    remote = DeployTraceRemote()
    provisioning = importlib.import_module("taskman_ops.provisioning")
    refusal = OpsError(ExitStatus.SAFETY, "postgresql", "ambiguous database authority", changed=False)
    monkeypatch.setattr(provisioning, "converge_database", lambda *_args, **_kwargs: (_ for _ in ()).throw(refusal))
    monkeypatch.setattr(
        provisioning,
        "install_runtime_environment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime mutation after refusal")),
    )

    with pytest.raises(OpsError, match="ambiguous database authority") as error:
        provisioning.converge_provisioning(
            remote,
            ProvisioningInputs(
                config=EnvironmentConfig.model_validate(valid_environment()),
                caddy_plan=_CADDY_PLAN,
                runtime_environment=b"RUNTIME=value\n",
                pgpass=b"pgpass\n",
                role_password_input=b"role-password-input\n",
            ),
        )

    assert error.value.status is ExitStatus.SAFETY
    assert remote.executions == 1


def test_categorized_custom_pyinfra_failure_preserves_safety_and_stops_later_operations(tmp_path: Path) -> None:
    """A custom refusal keeps its public category without allowing later mutation."""

    target = tmp_path / "must-not-exist"
    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host("@local")
    state.activate_host(host)
    remote = PyinfraRemote(
        host,
        EnvironmentConfig.model_validate(valid_environment()),
        inventory=inventory,
        state=state,
    )
    refusal = OpsError(ExitStatus.SAFETY, "firewall", "conflicting firewall policy", changed=False)

    @deploy("Categorized custom refusal")
    def converge() -> None:
        conditional_convergence(
            name="Refuse conflicting firewall",
            probe="printf 'changed=1\\n'",
            script="exit 10",
            sudo=False,
            failure=refusal,
        )
        files.put(
            StringIO("must not write\n"),
            target.as_posix(),
            add_deploy_dir=False,
            name="Later mutation",
        )

    with pytest.raises(OpsError, match="conflicting firewall policy") as error:
        remote.run_deploy(converge)

    assert error.value.status is ExitStatus.SAFETY
    assert target.exists() is False


def test_conflicting_ufw_policy_returns_safety_through_the_pyinfra_path(monkeypatch, tmp_path: Path) -> None:
    """The actual UFW guarded operation retains its refusal category and halts the deploy."""

    firewall = importlib.import_module("taskman_ops.host.firewall")
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    fake_ufw = binary_directory / "ufw"
    fake_ufw.write_text(
        "#!/bin/sh\ncase \"$1 ${2-}\" in\n"
        "  'status numbered') printf 'Status: active\\n[ 1] 4000/tcp ALLOW IN Anywhere\\n' ;;\n"
        "  'status verbose') printf 'Status: active\\nDefault: deny (incoming), allow (outgoing), disabled (routed)\\n' ;;\n"
        "  *) exit 1 ;;\nesac\n",
        encoding="utf-8",
    )
    fake_ufw.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_directory}:{os.environ['PATH']}")
    from pyinfra.operations import apt

    monkeypatch.setattr(apt, "packages", lambda **_kwargs: None)
    conditional = firewall.conditional_convergence
    monkeypatch.setattr(
        firewall,
        "conditional_convergence",
        lambda **kwargs: conditional(**{**kwargs, "sudo": False}),
    )
    target = tmp_path / "later-mutation"
    remote = _local_pyinfra_remote()

    @deploy("Conflicting UFW")
    def converge() -> None:
        firewall.declare_firewall(EnvironmentConfig.model_validate(valid_environment()))
        _later_touch(target)

    with pytest.raises(OpsError) as error:
        remote.run_deploy(converge)

    assert error.value.status is ExitStatus.SAFETY
    assert target.exists() is False


def test_ambiguous_postgresql_cluster_returns_safety_through_the_pyinfra_path(monkeypatch, tmp_path: Path) -> None:
    """The actual PostgreSQL guarded operation retains its refusal category and halts the deploy."""

    postgresql = importlib.import_module("taskman_ops.services.postgresql")
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    fake_clusters = binary_directory / "pg_lsclusters"
    fake_clusters.write_text(
        "#!/bin/sh\nprintf '16 main 5432 online postgres /var/lib/postgresql/16/main /var/log/postgresql/postgresql-16-main.log\\n'\n"
        "printf '16 secondary 5433 online postgres /var/lib/postgresql/16/secondary /var/log/postgresql/postgresql-16-secondary.log\\n'\n",
        encoding="utf-8",
    )
    fake_clusters.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_directory}:{os.environ['PATH']}")
    from pyinfra.operations import apt, files

    monkeypatch.setattr(apt, "packages", lambda **_kwargs: None)
    monkeypatch.setattr(files, "put", lambda *_args, **_kwargs: None)
    conditional = postgresql.conditional_convergence
    monkeypatch.setattr(
        postgresql,
        "conditional_convergence",
        lambda **kwargs: conditional(**{**kwargs, "sudo": False}),
    )
    target = tmp_path / "later-mutation"
    remote = _local_pyinfra_remote()

    @deploy("Ambiguous PostgreSQL")
    def converge() -> None:
        postgresql.declare_postgresql(EnvironmentConfig.model_validate(valid_environment()))
        _later_touch(target)

    with pytest.raises(OpsError) as error:
        remote.run_deploy(converge)

    assert error.value.status is ExitStatus.SAFETY
    assert target.exists() is False


def _local_pyinfra_remote() -> PyinfraRemote:
    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host("@local")
    state.activate_host(host)
    return PyinfraRemote(
        host,
        EnvironmentConfig.model_validate(valid_environment()),
        inventory=inventory,
        state=state,
    )


def _later_touch(target: Path) -> None:
    from pyinfra.api import operation

    @operation(is_idempotent=True)
    def touch(path: str):
        yield f"touch {path}"

    touch(target.as_posix(), name="Later mutation")


def test_programmatic_pyinfra_deploy_converges_then_repairs_ordinary_drift(tmp_path: Path) -> None:
    """Change evidence comes from completed pyinfra results, not console text."""

    target = tmp_path / "ordinary-state"

    def run_once() -> ChangeSet:
        inventory = Inventory((["@local"], {}))
        state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
        host = inventory.get_host("@local")
        state.activate_host(host)
        remote = PyinfraRemote(
            host,
            EnvironmentConfig.model_validate(valid_environment()),
            inventory=inventory,
            state=state,
        )

        @deploy("Converge ordinary state")
        def converge() -> None:
            files.put(
                StringIO("desired\n"),
                target.as_posix(),
                add_deploy_dir=False,
                name="Install ordinary state",
            )

        return remote.run_deploy(converge)

    first = run_once()
    second = run_once()
    target.write_text("drift\n", encoding="utf-8")
    repaired = run_once()

    assert first.changed is True
    assert second == ChangeSet(changed=False)
    assert repaired.changed is True
    assert target.read_text(encoding="utf-8") == "desired\n"


def test_execution_time_condition_observes_the_file_staged_by_an_earlier_operation(tmp_path: Path) -> None:
    """A same-deploy conditional must inspect state at execution, not prepare time."""

    staged = tmp_path / "staged"
    live = tmp_path / "live"

    def run_once() -> ChangeSet:
        inventory = Inventory(([("@local")], {}))
        state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
        host = inventory.get_host("@local")
        state.activate_host(host)
        remote = PyinfraRemote(
            host,
            EnvironmentConfig.model_validate(valid_environment()),
            inventory=inventory,
            state=state,
        )

        @deploy("Converge staged ordinary state")
        def converge() -> None:
            files.put(
                StringIO("desired\n"),
                staged.as_posix(),
                add_deploy_dir=False,
                name="Stage ordinary state",
            )
            conditional_convergence(
                name="Promote staged ordinary state",
                probe=(
                    "set -eu; if cmp -s "
                    f"{staged} {live}; then printf 'changed=0\\n'; else printf 'changed=1\\n'; fi"
                ),
                script=f"set -eu; install -m 0644 {staged} {live}",
                sudo=False,
            )

        return remote.run_deploy(converge)

    first = run_once()
    second = run_once()
    live.write_text("drift\n", encoding="utf-8")
    repaired = run_once()

    assert first.changed is True
    assert second == ChangeSet(changed=False)
    assert repaired.changed is True
    assert live.read_text(encoding="utf-8") == "desired\n"
