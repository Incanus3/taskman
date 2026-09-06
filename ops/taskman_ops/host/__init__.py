"""Supported-host discovery and convergence capabilities."""

from .baseline import build_baseline_plan, declare_baseline
from .acceptance import (
    ProvisioningDiscovery,
    ProvisioningState,
    validate_operational_host,
    validate_provisionable_host,
    validate_supported_host,
)
from .facts import (
    CaddyState,
    HostFacts,
    Listener,
    ProvisioningMarkerState,
    collect_host_facts,
)
from .firewall import build_firewall_plan, declare_firewall, verify_fresh_ssh_connection

__all__ = [
    "CaddyState",
    "HostFacts",
    "Listener",
    "ProvisioningDiscovery",
    "ProvisioningMarkerState",
    "ProvisioningState",
    "build_baseline_plan",
    "build_firewall_plan",
    "collect_host_facts",
    "validate_operational_host",
    "validate_provisionable_host",
    "declare_baseline",
    "declare_firewall",
    "validate_supported_host",
    "verify_fresh_ssh_connection",
]
