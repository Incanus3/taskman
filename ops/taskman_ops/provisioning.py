"""The single programmatic pyinfra deploy for stable host convergence."""

from __future__ import annotations

from dataclasses import dataclass

from pyinfra.api import deploy

from .config import EnvironmentConfig
from .errors import OpsError
from .remote import ChangeSet, PyinfraRemote
from .services.caddy import CaddyPlan
from .services.postgresql import (
    build_postgresql_plan,
    converge_database,
)
from .services.systemd import install_runtime_environment


@dataclass(frozen=True)
class ProvisioningInputs:
    """Validated non-secret configuration plus already-rendered secret bytes."""

    config: EnvironmentConfig
    caddy_plan: CaddyPlan
    runtime_environment: bytes
    pgpass: bytes
    role_password_input: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.config, EnvironmentConfig):
            raise TypeError("provisioning inputs require an environment configuration")
        if not isinstance(self.caddy_plan, CaddyPlan):
            raise TypeError("provisioning inputs require a preconfirmed Caddy plan")
        for field_name in ("runtime_environment", "pgpass", "role_password_input"):
            value = getattr(self, field_name)
            if not isinstance(value, bytes) or not value:
                raise ValueError(f"{field_name} must be non-empty bytes")


@deploy("Converge Taskman host")
def taskman_provisioning(*, inputs: ProvisioningInputs) -> None:
    """Declare exactly one host deploy without prepare-time mutable-fact branches."""

    from .host.baseline import declare_baseline
    from .services.caddy import declare_caddy
    from .services.postgresql import declare_postgresql
    from .services.systemd import declare_systemd

    declare_baseline(inputs.config)
    declare_caddy(inputs.caddy_plan)
    declare_postgresql(inputs.config)
    declare_systemd(inputs)


def converge_provisioning(remote: PyinfraRemote, inputs: ProvisioningInputs) -> ChangeSet:
    """Execute stable pyinfra convergence, then the three secret/database guards.

    The remaining imperative work has concrete material-risk ownership:
    PostgreSQL validates the selected cluster before HBA installation and the
    candidate before restart, then validates database authority and connectivity;
    runtime and pgpass bytes never enter pyinfra's command or logging path.
    """

    if not isinstance(remote, PyinfraRemote):
        raise TypeError("provisioning convergence requires a connected PyinfraRemote")
    if not isinstance(inputs, ProvisioningInputs):
        raise TypeError("provisioning convergence requires ProvisioningInputs")

    deploy_changes = remote.run_deploy(taskman_provisioning, inputs=inputs)
    try:
        database_changes = converge_database(
            remote,
            build_postgresql_plan(inputs.config),
            role_password_input=inputs.role_password_input,
            pgpass=inputs.pgpass,
        )
    except OpsError as error:
        error.changed = error.changed or deploy_changes.changed
        raise
    try:
        runtime_changes = install_runtime_environment(remote, inputs.runtime_environment)
    except OpsError as error:
        error.changed = error.changed or deploy_changes.changed or database_changes.changed
        raise
    operations = tuple(dict.fromkeys((*deploy_changes.operations, *database_changes.operations, *runtime_changes.operations)))
    return ChangeSet(changed=bool(operations), operations=operations)


__all__ = ["ProvisioningInputs", "converge_provisioning", "taskman_provisioning"]
