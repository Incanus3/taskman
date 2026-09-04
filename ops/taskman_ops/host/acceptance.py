"""Host-admission policy applied after a complete read-only fact snapshot."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
import ipaddress
from pathlib import PurePosixPath

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..remote import Remote
from .facts import (
    CaddyState,
    HostFacts,
    Listener,
    ProvisioningMarkerState,
    _ACCOUNT_NAME,
    _CADDYFILE,
    _PROVISIONING_MARKER,
    _SYSTEMD_UNITS,
    MINIMUM_DISK_BYTES,
    MINIMUM_MEMORY_BYTES,
    collect_host_facts,
)


class ProvisioningState(str, Enum):
    """The host-admission outcome relevant to initial convergence."""

    PRISTINE = "pristine"
    PARTIAL = "partial"
    MANAGED = "managed"


@dataclass(frozen=True)
class ProvisioningDiscovery:
    """Fact evidence plus the policy classification used by provisioning."""

    facts: HostFacts
    state: ProvisioningState
    caddy_state: CaddyState


def validate_supported_host(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
) -> HostFacts:
    """Return validated host facts or refuse before any managed-state adoption."""

    facts = collect_host_facts(remote, config, resolver=resolver)
    _validate_host_platform(facts, config)
    if _managed_conflicts(facts, config) or facts.caddy_state is not CaddyState.ABSENT:
        raise _safety("existing managed state is ambiguous and will not be adopted")
    return facts


def validate_operational_host(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
) -> HostFacts:
    """Validate platform and managed PostgreSQL prerequisites without pristine-state rules."""

    cached_facts = getattr(remote, "facts", None)
    facts = (
        cached_facts()
        if callable(cached_facts) and resolver is None
        else collect_host_facts(remote, config, resolver=resolver)
    )
    if not isinstance(facts, HostFacts):
        raise TypeError("operational host discovery returned invalid facts")
    _validate_host_platform(facts, config)
    if not facts.postgres_available or facts.postgres_sudo_available is not True:
        raise _preflight("managed PostgreSQL prerequisites are unavailable")
    return facts


def validate_provisionable_host(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
    expected_caddyfile_sha256: str,
) -> ProvisioningDiscovery:
    """Classify a pristine host or one anchored by the managed provisioning marker."""

    facts = collect_host_facts(
        remote,
        config,
        resolver=resolver,
        expected_caddyfile_sha256=expected_caddyfile_sha256,
    )
    _validate_host_platform(facts, config)
    return ProvisioningDiscovery(
        facts=facts,
        state=_provisioning_state(facts, config),
        caddy_state=facts.caddy_state,
    )


def _validate_host_platform(facts: HostFacts, config: EnvironmentConfig) -> None:
    if facts.failed_checks:
        raise _preflight("required host fact collection failed")
    if facts.os_id != "ubuntu" or facts.ubuntu_release != "26.04":
        raise _unsupported("host must run Ubuntu 26.04")
    if facts.architecture != "amd64":
        raise _unsupported("host must use amd64 or x86_64 architecture")
    if not facts.systemd:
        raise _unsupported("host PID 1 must be systemd")
    if facts.memory_bytes < MINIMUM_MEMORY_BYTES:
        raise _unsupported("host does not meet the minimum memory requirement")
    if (
        facts.available_disk_bytes < MINIMUM_DISK_BYTES
        or facts.backup_available_disk_bytes < MINIMUM_DISK_BYTES
    ):
        raise _unsupported("host does not meet the minimum disk requirement")
    if facts.dns_addresses != _expected_addresses(config):
        raise _unsupported("public DNS does not resolve directly to the configured VPS address")
    if not facts.sudo_available:
        raise _preflight("configured administrator cannot use passwordless sudo")
    if facts.postgres_sudo_available is False:
        raise _preflight("configured administrator cannot inspect PostgreSQL as postgres")
    if facts.active_ssh_port != config.ssh_port:
        raise _preflight("active SSH connection port does not match configuration")


def _provisioning_state(facts: HostFacts, config: EnvironmentConfig) -> ProvisioningState:
    managed_evidence = bool(
        facts.existing_paths
        or facts.existing_units
        or facts.existing_accounts
        or facts.existing_databases
        or facts.postgres_available
        or _reserved_listeners(facts, config)
        or facts.caddy_state is not CaddyState.ABSENT
    )
    if facts.provisioning_marker is ProvisioningMarkerState.ABSENT:
        if managed_evidence:
            raise _safety("existing managed state has no Taskman provisioning marker and will not be adopted")
        return ProvisioningState.PRISTINE
    if facts.provisioning_marker is not ProvisioningMarkerState.MANAGED:
        raise _safety("Taskman provisioning marker is invalid and will not be adopted")
    _validate_managed_service_boundaries(facts, config)
    return ProvisioningState.MANAGED if _fully_managed(facts, config) else ProvisioningState.PARTIAL


def _reserved_listeners(facts: HostFacts, config: EnvironmentConfig) -> tuple[Listener, ...]:
    ports = {80, 443, config.application_port, config.distribution_port, config.database_port}
    return tuple(listener for listener in facts.listeners if listener.port in ports)


def _validate_managed_service_boundaries(facts: HostFacts, config: EnvironmentConfig) -> None:
    paths = set(facts.existing_paths)
    units = set(facts.existing_units)
    service_path = PurePosixPath("/etc/systemd/system/taskman.service")
    taskman_units = {"taskman.service", "taskman-backup.service", "taskman-backup.timer"}
    if facts.existing_databases and (not facts.postgres_available or not facts.existing_accounts):
        raise _safety("Taskman database evidence is missing its managed service boundaries")
    if units.intersection(taskman_units) and service_path not in paths:
        raise _safety("Taskman service units are missing their managed unit boundary")
    if facts.caddy_state is CaddyState.INVALID:
        raise _safety("Caddy ownership evidence is unrecognized or contradictory")
    if facts.caddy_state is CaddyState.ABSENT and (
        "caddy.service" in units or _CADDYFILE in paths
    ):
        raise _safety("Caddy artifacts are missing validated ownership evidence")
    if facts.caddy_state is CaddyState.PREPARED and _CADDYFILE in paths:
        raise _safety("Caddy configuration is missing validated ownership evidence")
    if facts.caddy_state in {CaddyState.STAGED, CaddyState.ACTIVE} and _CADDYFILE not in paths:
        raise _safety("Caddy service evidence is missing its managed configuration boundary")

    for listener in _reserved_listeners(facts, config):
        if listener.port in {80, 443}:
            valid = facts.caddy_state is CaddyState.ACTIVE
        elif listener.port == config.database_port:
            valid = facts.postgres_available and _loopback(listener.address)
        else:
            valid = "taskman.service" in units and _loopback(listener.address)
        if not valid:
            raise _safety("managed listener topology is unrecognized or contradictory")


def _fully_managed(facts: HostFacts, config: EnvironmentConfig) -> bool:
    required_paths = {
        config.install_root,
        config.release_root,
        config.deployment_root,
        config.backup_root,
        PurePosixPath("/etc/taskman"),
        _PROVISIONING_MARKER,
        PurePosixPath("/etc/systemd/system/taskman.service"),
        _CADDYFILE,
    }
    listener_ports = {listener.port for listener in _reserved_listeners(facts, config)}
    required_ports = {80, 443, config.application_port, config.distribution_port, config.database_port}
    return (
        required_paths.issubset(facts.existing_paths)
        and set(_SYSTEMD_UNITS).issubset(facts.existing_units)
        and facts.existing_accounts == (_ACCOUNT_NAME,)
        and facts.existing_databases == (config.database_name,)
        and facts.postgres_available
        and facts.caddy_state is CaddyState.ACTIVE
        and required_ports.issubset(listener_ports)
    )


def _loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _expected_addresses(config: EnvironmentConfig) -> tuple[str, ...]:
    values = {config.public_ipv4}
    if config.public_ipv6 is not None:
        values.add(config.public_ipv6)
    return tuple(sorted(values))


def _managed_conflicts(facts: HostFacts, config: EnvironmentConfig) -> bool:
    reserved_ports = {80, 443, config.application_port, config.distribution_port, config.database_port}
    return bool(
        any(listener.port in reserved_ports for listener in facts.listeners)
        or facts.existing_paths
        or facts.existing_units
        or facts.existing_accounts
        or facts.existing_databases
    )


def _unsupported(message: str) -> OpsError:
    return OpsError(
        status=ExitStatus.INVALID,
        stage="host-preflight",
        message=message,
        changed=False,
        next_action="use a supported clean Ubuntu 26.04 amd64 host and correct the environment configuration",
    )


def _preflight(message: str) -> OpsError:
    return OpsError(
        status=ExitStatus.REMOTE_PREFLIGHT,
        stage="host-preflight",
        message=message,
        changed=False,
        next_action="restore SSH administrator connectivity and required sudo access before retrying",
    )


def _safety(message: str) -> OpsError:
    return OpsError(
        status=ExitStatus.SAFETY,
        stage="host-preflight",
        message=message,
        changed=False,
        next_action="inspect the existing state and use an explicit later adoption workflow if authorized",
    )


__all__ = [
    "ProvisioningDiscovery",
    "ProvisioningState",
    "validate_operational_host",
    "validate_provisionable_host",
    "validate_supported_host",
]
