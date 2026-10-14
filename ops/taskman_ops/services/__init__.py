"""Narrow host-service capability contracts."""

from .caddy import build_caddy_ownership_plan, declare_caddy_sources
from .postgresql import build_postgresql_plan, declare_postgresql
from .systemd import build_systemd_plan, declare_systemd

__all__ = [
    "build_caddy_ownership_plan",
    "build_postgresql_plan",
    "build_systemd_plan",
    "declare_caddy_sources",
    "declare_postgresql",
    "declare_systemd",
]
