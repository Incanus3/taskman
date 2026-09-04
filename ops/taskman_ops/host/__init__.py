"""Supported-host discovery and convergence capabilities."""

from .facts import HostFacts, Listener, collect_host_facts, validate_supported_host

__all__ = ["HostFacts", "Listener", "collect_host_facts", "validate_supported_host"]
