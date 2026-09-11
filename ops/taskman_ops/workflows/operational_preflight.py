"""Read-only preflight shared by existing-host mutating workflows."""

from __future__ import annotations

from collections.abc import Callable

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..host.acceptance import validate_operational_host
from ..host.facts import HostFacts, collect_operational_preflight
from ..remote import Remote


HostValidator = Callable[[Remote, EnvironmentConfig], HostFacts | object]


def validate_operational_preflight(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    host_validator: HostValidator | None = None,
) -> HostFacts | object:
    """Validate host, secret-file shape, database health, and backup capacity.

    The commands intentionally emit no values. They execute before lifecycle
    planning, staging, confirmation, or any host mutation.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("operational preflight requires an environment configuration")
    facts = (host_validator or validate_operational_host)(remote, config)
    runtime, database = collect_operational_preflight(remote, config)
    if not runtime.succeeded:
        raise _preflight(
            "runtime environment ownership, mode, required keys, or distro Python is invalid"
        )
    if not database.succeeded:
        raise _preflight("managed database health or backup capacity preflight failed")
    return facts


def _preflight(message: str) -> OpsError:
    return OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "preflight",
        message,
        changed=False,
        next_action=f"{message}; correct this prerequisite before retrying",
    )


__all__ = ["validate_operational_preflight"]
