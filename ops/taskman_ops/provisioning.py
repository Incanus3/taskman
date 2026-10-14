"""The single programmatic pyinfra deploy for stable host convergence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import re

from pyinfra.api import deploy

from .config import EnvironmentConfig
from .errors import ExitStatus, OpsError
from .host_protocol import HostResult
from .remote import ChangeSet, PyinfraRemote, merge_change_warnings
from .services.caddy import CaddyOwnershipPlan, guard_caddy_install, prepare_caddy
from .services.postgresql import (
    build_postgresql_plan,
    build_postgresql_ownership_plan,
    PostgreSQLOwnershipPlan,
    converge_database,
)
from .services.systemd import SystemdPlan, install_runtime_environment


_CREDENTIAL_REUSE_SCRIPT = r'''set -eu
path=$1
if [ ! -e "$path" ] && [ ! -L "$path" ]; then exit 0; fi
test -f "$path" && test ! -L "$path"
test "$(stat --format='%U:%G:%a' -- "$path")" = root:root:600
cmp -s - "$path"
'''
_SCHEDULER_RESOURCE_PATHS = frozenset(
    {
        "/usr/local/lib/taskman/taskman-backup.pyz",
        "/etc/systemd/system/taskman-backup.service",
        "/etc/systemd/system/taskman-backup.timer",
        "/etc/taskman/taskman-backup.env",
    }
)
_SCHEDULER_RESOURCE_NAMES = {
    "helper": "/usr/local/lib/taskman/taskman-backup.pyz",
    "service": "/etc/systemd/system/taskman-backup.service",
    "timer": "/etc/systemd/system/taskman-backup.timer",
    "environment": "/etc/taskman/taskman-backup.env",
}


@dataclass(frozen=True)
class ProvisioningInputs:
    """Validated configuration, frozen service plans, and rendered secret bytes."""

    config: EnvironmentConfig
    caddy_plan: CaddyOwnershipPlan
    systemd_plan: SystemdPlan
    runtime_environment: bytes
    pgpass: bytes
    role_password_input: bytes
    # Only resources that were observed absent before confirmation may be
    # created by the generic pyinfra boundary.  Scheduler replacement remains
    # the locked genesis helper's responsibility.
    # Creation authority is supplied only after pre-convergence observation.
    # Preparation without that authority cannot create scheduler resources.
    scheduler_create: frozenset[str] = frozenset()
    firewall_authority: HostResult | None = None
    force_postgresql_config: bool = False
    postgresql_plan: PostgreSQLOwnershipPlan | None = None

    def __post_init__(self) -> None:
        if type(self.force_postgresql_config) is not bool:
            raise TypeError("PostgreSQL configuration context must be boolean")
        if not isinstance(self.config, EnvironmentConfig):
            raise TypeError("provisioning inputs require an environment configuration")
        if self.postgresql_plan is not None:
            if not isinstance(self.postgresql_plan, PostgreSQLOwnershipPlan):
                raise TypeError("provisioning requires frozen PostgreSQL ownership")
            ownership = self.postgresql_plan
            observed = {"branch": ownership.branch, "cluster": None if ownership.cluster is None else asdict(ownership.cluster),
                        "role": ownership.role, "database": ownership.database, "access": ownership.access}
            if build_postgresql_ownership_plan(self.config, observed, force_postgresql_config=self.force_postgresql_config) != ownership:
                raise ValueError("PostgreSQL ownership does not match provisioning inputs")
        if not isinstance(self.caddy_plan, CaddyOwnershipPlan):
            raise TypeError("provisioning inputs require a preconfirmed Caddy plan")
        if not isinstance(self.systemd_plan, SystemdPlan):
            raise TypeError("provisioning inputs require a preconfirmed systemd plan")
        for field_name in ("runtime_environment", "pgpass", "role_password_input"):
            value = getattr(self, field_name)
            if not isinstance(value, bytes) or not value:
                raise ValueError(f"{field_name} must be non-empty bytes")
        if not isinstance(self.scheduler_create, frozenset) or not self.scheduler_create.issubset(
            _SCHEDULER_RESOURCE_PATHS
        ):
            raise ValueError("scheduler creation authority is invalid")
        if self.firewall_authority is not None:
            from .host.firewall import validate_firewall_authority
            validate_firewall_authority(self.config, self.firewall_authority)


@dataclass(frozen=True)
class ProvisioningResult:
    """Convergence changes and the validated proxy preparation receipt."""

    changes: ChangeSet
    caddy: HostResult
    postgresql_recovery: HostResult | None = None


def _validate_postgresql_recovery(inputs, result):
    from .host_protocol import PROTOCOL_VERSION, HostRequest
    from .host_protocol.postgresql import validate_postgresql_recovery_result
    if inputs.postgresql_plan.branch != "configure-existing":
        raise TypeError("PostgreSQL recovery is outside confirmed authority")
    request = HostRequest(PROTOCOL_VERSION, "postgresql_config_recovery", result.correlation_id, {},
        {"install_root": inputs.config.install_root.as_posix(), "backup_root": inputs.config.backup_root.as_posix()},
        {"cluster": asdict(inputs.postgresql_plan.cluster)})
    validate_postgresql_recovery_result(request, result)
    if result.outcome != "succeeded":
        raise TypeError("successful provisioning requires completed PostgreSQL recovery capture")
    return result


class ProvisionAuthority(dict[str, object]):
    """Plan authority with cleanup warnings outside mapping equality."""

    def __init__(self, state: Mapping[str, object], warnings: tuple[str, ...] = ()) -> None:
        super().__init__(state)
        self.warnings = warnings


@deploy("Converge Taskman host")
def taskman_provisioning(*, inputs: ProvisioningInputs) -> None:
    """Declare exactly one host deploy without prepare-time mutable-fact branches."""

    from .host.baseline import declare_baseline
    from .services.caddy import declare_caddy_sources
    from .services.postgresql import declare_postgresql
    from .services.systemd import declare_systemd
    from .host.firewall import validate_firewall_authority

    _require_postgresql_ownership(inputs)
    validate_firewall_authority(inputs.config, inputs.firewall_authority)
    required_packages = ()
    if inputs.caddy_plan.branch != "existing":
        required_packages += (*inputs.caddy_plan.repository_packages, "caddy")
    if inputs.postgresql_plan.branch == "bootstrap":
        required_packages += inputs.postgresql_plan.desired.packages
    declare_baseline(
        inputs.config,
        confirmed_firewall=inputs.firewall_authority,
        required_packages=required_packages,
    )
    declare_caddy_sources(inputs.caddy_plan)
    declare_postgresql(inputs.postgresql_plan, config=inputs.config)
    declare_systemd(inputs)


def converge_provisioning(remote: PyinfraRemote, inputs: ProvisioningInputs) -> ProvisioningResult:
    """Guard missing proxy installation, converge stable sources, prepare the site, then secrets."""
    if not isinstance(remote, PyinfraRemote) or not isinstance(inputs, ProvisioningInputs):
        raise TypeError("provisioning convergence requires connected remote and inputs")
    from .host.firewall import validate_firewall_authority
    validate_firewall_authority(inputs.config, inputs.firewall_authority)
    _require_postgresql_ownership(inputs)
    prior = "unchanged"
    warnings: tuple[str, ...] = ()
    proxy: HostResult | None = None
    recovery: HostResult | None = None
    operations: list[str] = []
    try:
        guarded = None
        if inputs.caddy_plan.branch != "existing":
            guarded = guard_caddy_install(remote, inputs.caddy_plan)
            proxy = guarded
            prior = _merge_mutation(prior, guarded.state["mutation_state"])
            warnings = (*warnings, *guarded.warnings)
        deploy_changes = remote.run_deploy(taskman_provisioning, inputs=inputs)
        recovery = getattr(getattr(remote, "_state", None), "_taskman_postgresql_recovery", None)
        if recovery is not None:
            _validate_postgresql_recovery(inputs, recovery)
            prior = _merge_mutation(prior, recovery.state["mutation_state"])
            warnings = (*warnings, *recovery.warnings)
            operations.append("Retain PostgreSQL configuration recovery copies")
        operations.extend(deploy_changes.operations)
        prior = _merge_mutation(prior, "changed" if deploy_changes.changed else "unchanged")
        warnings = (*warnings, *deploy_changes.warnings)
        proxy = prepare_caddy(remote, inputs.caddy_plan, guarded)
        prior = _merge_mutation(prior, proxy.state["mutation_state"])
        warnings = (*warnings, *proxy.warnings)
        if proxy.state["mutation_state"] != "unchanged":
            operations.append("Prepare Caddy site")
        database_changes = converge_database(remote, inputs.postgresql_plan.desired,
            role_password_input=inputs.role_password_input, pgpass=inputs.pgpass)
        operations.extend(database_changes.operations)
        prior = _merge_mutation(prior, "changed" if database_changes.changed else "unchanged")
        warnings = (*warnings, *database_changes.warnings)
        runtime_changes = install_runtime_environment(remote, inputs.runtime_environment)
        operations.extend(runtime_changes.operations)
        prior = _merge_mutation(prior, "changed" if runtime_changes.changed else "unchanged")
        warnings = (*warnings, *runtime_changes.warnings)
    except OpsError as error:
        if recovery is None:
            recovery = getattr(getattr(remote, "_state", None), "_taskman_postgresql_recovery", None)
            if recovery is not None:
                _validate_postgresql_recovery(inputs, recovery)
                prior = _merge_mutation(prior, recovery.state["mutation_state"])
                warnings = (*warnings, *recovery.warnings)
        evidence = {} if proxy is None else _proxy_facts(proxy)
        if recovery is not None:
            evidence.update(postgresql_recovery=dict(recovery.state), recovery_directory=recovery.state["recovery_directory"])
        # A validated receipt is earlier evidence, never a replacement for a
        # missing final proxy authority or runtime observation.
        if "receipt" in error.state:
            evidence = {**evidence, "caddy": dict(error.state), "caddy_receipt": error.state["receipt"]}
        current = error.state.get("mutation_state", "unknown" if error.changed else "unchanged")
        aggregate = _merge_mutation(prior, current)
        error.state = {**evidence, **error.state, "mutation_state": aggregate}
        error.changed = aggregate != "unchanged"
        error.warnings = merge_change_warnings(warnings, error.warnings,
            changed=error.changed, next_action=error.next_action)
        raise
    return ProvisioningResult(ChangeSet(changed=prior != "unchanged", operations=tuple(dict.fromkeys(operations)),
        warnings=merge_change_warnings(warnings, changed=prior != "unchanged")), proxy, recovery)


def _require_postgresql_ownership(inputs: ProvisioningInputs) -> None:
    if inputs.postgresql_plan is None:
        raise OpsError(ExitStatus.SAFETY, "postgresql", "provisioning lacks frozen PostgreSQL ownership authority",
            changed=False, next_action="rediscover and confirm PostgreSQL ownership before convergence")


def _merge_mutation(*states: object) -> str:
    if "changed" in states:
        return "changed"
    return "unknown" if "unknown" in states else "unchanged"


def _proxy_facts(result: HostResult) -> dict[str, object]:
    state = result.to_mapping()["state"]
    return {"caddy": state, "caddy_receipt": state["receipt"]}


def _validate_retained_credential_file(remote: object, path: str, content: bytes) -> None:
    """Prove private retained bytes without assuming database presence or access."""
    runner = getattr(remote, "run", None)
    if not callable(runner):
        raise TypeError("credential authority requires a remote command runner")
    result = runner(
        ("sh", "-ceu", _CREDENTIAL_REUSE_SCRIPT, "taskman-credential-authority", path),
        sudo=True, stdin=content, sensitive=True,
    )
    if not getattr(result, "succeeded", False):
        raise OpsError(
            ExitStatus.SAFETY,
            "credential-preflight",
            "existing protected credential authority is incompatible and will not be replaced",
            changed=False,
            next_action="inspect the existing credential and database authority before retrying",
        )


def validate_existing_credential_authority(
    remote: object, inputs: ProvisioningInputs, *, database_state: str,
    authentication_mode: str = "authenticate",
) -> None:
    """Refuse conflicting existing secret authority before pyinfra can write.

    An absent file is a supported partial-installation boundary.  Once either
    protected file exists it is authority: it must be a root-owned, non-link
    regular file with exact bytes matching the just-decrypted input.  The
    database password file additionally proves that those retained bytes can
    authenticate to the configured role and database when both exist. Verified
    absent identities omit only that impossible authentication. ``cmp`` consumes the
    protected stdin directly. Application authentication uses a private transient
    passfile when no retained file exists; no secret enters argv, environment,
    output, or logs.
    """

    if not isinstance(inputs, ProvisioningInputs):
        raise TypeError("credential authority requires provisioning inputs")
    if type(authentication_mode) is not str or authentication_mode not in {"authenticate", "defer-for-configuration"}:
        raise ValueError("credential authority authentication mode is invalid")
    if type(database_state) is not str or database_state not in {"ready", "absent"}:
        raise ValueError("credential authority requires validated database presence")
    config = inputs.config
    _validate_retained_credential_file(remote, "/etc/taskman/taskman.env", inputs.runtime_environment)
    from .helper_client.package import temporary_helper_package
    from .helper_client.runner import invoke_sensitive_pgpass_authority, new_correlation_id

    with temporary_helper_package() as package:
        receipt = invoke_sensitive_pgpass_authority(
            remote, package, correlation_id=new_correlation_id(),
            host=config.database_host, port=config.database_port,
            role=config.database_role, database=config.database_name, pgpass=inputs.pgpass,
            database_state=database_state, authentication_mode=authentication_mode,
        )
    if receipt.exit_status == 5:
        raise OpsError(ExitStatus.REMOTE_PREFLIGHT, "postgresql",
            "PostgreSQL SCRAM application access or client prerequisite failed",
            changed=False,
            next_action="inspect the selected endpoint, SCRAM authentication and client capability before retrying",
            warnings=receipt.warnings)
    if receipt.exit_status != 0:
        raise OpsError(
            ExitStatus.SAFETY, "credential-preflight",
            "existing protected credential authority is incompatible and will not be replaced",
            changed=False,
            next_action="inspect the existing credential and database authority before retrying",
            warnings=receipt.warnings,
        )
    return receipt


def validate_existing_authority(remote: object, inputs: ProvisioningInputs) -> Mapping[str, object]:
    """Keep resource and secret validation as one pre-mutation capability."""

    authority = validate_preconvergence_authority(remote, inputs)
    from .host_protocol.postgresql import validate_postgresql_authority
    selected = validate_postgresql_authority(authority["postgresql"],
        package_track=inputs.config.postgres_package_track, port=inputs.config.database_port,
        force_postgresql_config=inputs.force_postgresql_config)
    authentication_mode = "defer-for-configuration" if selected["branch"] == "configure-existing" else "authenticate"
    try:
        credential_receipt = validate_existing_credential_authority(
            remote, inputs, database_state=authority["database_state"], authentication_mode=authentication_mode,
        )
    except OpsError as error:
        if error.stage == "postgresql":
            error.state = {**error.state, "failed_check": "scram-access-or-client-prerequisite",
                           "postgresql_observation": dict(selected)}
        raise
    return ProvisionAuthority(
        {**authority, "scheduler_create": _scheduler_create_authority(authority)},
        (*getattr(authority, "warnings", ()), *getattr(credential_receipt, "warnings", ())),
    )


def _scheduler_create_authority(authority: Mapping[str, object]) -> tuple[str, ...]:
    """Translate the closed helper presence projection into pyinfra deltas.

    This is intentionally a create-only projection.  A present scheduler file
    can be old but supported; adopting it by rewriting it in pyinfra would
    bypass the lifecycle-lock pause/wait/refresh protocol.
    """

    resources = authority.get("scheduler_resources")
    if not isinstance(resources, Mapping) or set(resources) != set(_SCHEDULER_RESOURCE_NAMES):
        raise OpsError(
            ExitStatus.SAFETY,
            "resource-preflight",
            "pre-convergence scheduler resource authority is incomplete",
            changed=False,
            next_action="inspect the existing scheduler resources before retrying",
        )
    if not all(type(value) is bool for value in resources.values()):
        raise OpsError(
            ExitStatus.SAFETY,
            "resource-preflight",
            "pre-convergence scheduler resource authority is invalid",
            changed=False,
            next_action="inspect the existing scheduler resources before retrying",
        )
    return tuple(
        sorted(path for name, path in _SCHEDULER_RESOURCE_NAMES.items() if not resources[name])
    )


def validate_preconvergence_authority(remote: object, inputs: ProvisioningInputs) -> Mapping[str, object]:
    """Validate record and PostgreSQL authority through the read-only helper."""

    if not isinstance(inputs, ProvisioningInputs):
        raise TypeError("preconvergence authority requires provisioning inputs")
    # The ordinary observer may authenticate with the retained passfile. Establish
    # its complete byte authority before that first use, including force admission
    # which defers only authentication. Absence remains a supported partial state.
    _validate_retained_credential_file(remote, "/etc/taskman/pgpass", inputs.pgpass)
    from .workflows.helper import request, result_error, run_request

    plan = build_postgresql_plan(inputs.config)
    systemd = inputs.systemd_plan
    assets = {asset.destination: _sha256(asset.content) for asset in systemd.assets}
    authority_request = request(
        "provision_authority",
        inputs.config,
        parameters={
            "database": {
                "host": inputs.config.database_host,
                "port": inputs.config.database_port,
                "role": inputs.config.database_role,
                "name": inputs.config.database_name,
            },
            "postgres_package_track": plan.package_track,
            "force_postgresql_config": inputs.force_postgresql_config,
            "resource_digests": {
                "taskman_service": assets["/etc/systemd/system/taskman.service"],
                "backup_environment": _sha256(systemd.backup_environment_content),
                "backup_service": assets["/etc/systemd/system/taskman-backup.service"],
                "backup_timer": assets["/etc/systemd/system/taskman-backup.timer"],
            },
        },
    )
    result = run_request(remote, authority_request)
    if result.outcome != "succeeded":
        raise result_error(result)
    state = result.state
    # This is the exact bounded projection returned by
    # ``discover.provision_authority``.  Keep this schema deliberately closed:
    # accepting arbitrary helper keys would turn an incomplete privileged
    # observation into authority the controller never reviewed.
    required = {
        "authority",
        "postgresql",
        "initial_database_empty",
        "selected_release_id",
        "last_successful_selection_id",
        "last_successful_selection",
        "previous_successful_selection",
        "applied_migrations",
        "service_state",
        "database_state",
        "backup_protections",
        "independently_held_backup_ids",
        "backup_protection_sha256",
        "scheduled_backup_sha256",
        "backup_timer_enabled",
        "backup_timer_state",
        "downgrade_baseline_sha256",
        "installed_release_count",
        "installed_release_sha256",
        "scheduler_resources",
    }
    if not isinstance(state, Mapping) or set(state) != required or state["authority"] != "validated":
        raise OpsError(
            ExitStatus.SAFETY,
            "authority-preflight",
            "pre-convergence authority observation returned invalid evidence",
            changed=False,
            next_action="inspect the existing record and PostgreSQL authority before retrying",
        )
    try:
        from .host_protocol.postgresql import validate_postgresql_authority
        validate_postgresql_authority(state["postgresql"], package_track=plan.package_track, port=inputs.config.database_port, force_postgresql_config=inputs.force_postgresql_config)
        if (state["database_state"] == "ready") != (state["postgresql"]["database"] == "compatible"):
            raise ValueError
        if state["database_state"] == "absent" and (state["initial_database_empty"] or state["applied_migrations"]):
            raise ValueError
        if state["selected_release_id"] is not None and not isinstance(state["selected_release_id"], str):
            raise ValueError
        if state["last_successful_selection_id"] is not None and not isinstance(state["last_successful_selection_id"], str):
            raise ValueError
        if not isinstance(state["applied_migrations"], tuple) or any(type(item) is not int for item in state["applied_migrations"]):
            raise ValueError
        if type(state["initial_database_empty"]) is not bool:
            raise ValueError
        if state["service_state"] not in {"running", "stopped", "unknown"} or state["database_state"] not in {"ready", "absent"}:
            raise ValueError
        if not isinstance(state["backup_protections"], tuple) or not isinstance(state["independently_held_backup_ids"], tuple):
            raise ValueError
        if type(state["installed_release_count"]) is not int or state["installed_release_count"] < 0:
            raise ValueError
        for key in ("backup_protection_sha256", "scheduled_backup_sha256", "downgrade_baseline_sha256", "installed_release_sha256"):
            value = state[key]
            if value is not None and (type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None):
                raise ValueError
        if type(state["backup_timer_enabled"]) is not bool or state["backup_timer_state"] not in {"active", "inactive"}:
            raise ValueError
        resources = state["scheduler_resources"]
        if (
            not isinstance(resources, Mapping)
            or set(resources) != set(_SCHEDULER_RESOURCE_NAMES)
            or not all(type(value) is bool for value in resources.values())
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise OpsError(
            ExitStatus.SAFETY, "authority-preflight",
            "pre-convergence authority observation returned invalid evidence",
            changed=False,
            next_action="inspect the existing record and PostgreSQL authority before retrying",
        ) from None
    return ProvisionAuthority(state, result.warnings)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


__all__ = [
    "ProvisioningInputs",
    "ProvisioningResult",
    "converge_provisioning",
    "taskman_provisioning",
    "validate_existing_authority",
    "validate_preconvergence_authority",
    "validate_existing_credential_authority",
]
