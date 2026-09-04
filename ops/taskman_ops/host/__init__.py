"""Supported-host discovery and convergence capabilities."""

from .baseline import build_baseline_plan, converge_baseline, converge_baseline_host
from .facts import (
    CaddyState,
    HostFacts,
    Listener,
    ProvisioningDiscovery,
    ProvisioningMarkerState,
    ProvisioningState,
    collect_host_facts,
    validate_operational_host,
    validate_provisionable_host,
    validate_supported_host,
)
from .firewall import apply_firewall, build_firewall_plan, verify_fresh_ssh_connection

__all__ = [
    "CaddyState",
    "HostFacts",
    "Listener",
    "ProvisioningDiscovery",
    "ProvisioningMarkerState",
    "ProvisioningState",
    "apply_firewall",
    "build_baseline_plan",
    "build_firewall_plan",
    "collect_host_facts",
    "validate_operational_host",
    "validate_provisionable_host",
    "converge_baseline",
    "converge_baseline_host",
    "validate_supported_host",
    "verify_fresh_ssh_connection",
]
