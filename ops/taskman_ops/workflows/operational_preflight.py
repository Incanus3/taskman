"""Read-only preflight shared by existing-host mutating workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..host.acceptance import validate_operational_host, validate_restore_inspection_host
from ..host.facts import (
    HostFacts,
    collect_operational_preflight,
    collect_restore_inspection_preflight,
    collect_restore_preflight,
)
from ..remote import Remote


HostValidator = Callable[[Remote, EnvironmentConfig], HostFacts | object]


@dataclass(frozen=True)
class RestorePreflightFacts:
    """Capacity facts required only by restore planning."""

    available_disk_bytes: int
    backup_available_disk_bytes: int
    database_available_disk_bytes: int
    database_size_bytes: Mapping[str, int | None]


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


def validate_restore_preflight(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    host_validator: HostValidator | None = None,
) -> RestorePreflightFacts:
    """Validate restore access through PostgreSQL's maintenance database."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("restore preflight requires an environment configuration")
    facts = (host_validator or validate_operational_host)(remote, config)
    runtime, database = collect_restore_preflight(remote, config)
    if not runtime.succeeded:
        raise _preflight(
            "runtime environment ownership, mode, required keys, or distro Python is invalid"
        )
    if not database.succeeded:
        raise _preflight(
            "PostgreSQL maintenance access, database role, or restore capacity preflight failed"
        )
    try:
        lines = database.stdout.splitlines()
        if not lines or len(lines) > 4:
            raise ValueError
        database_available_bytes = int(lines[0])
        database_sizes: dict[str, int | None] = {
            "canonical": None,
            "temporary": None,
            "retired": None,
        }
        observed_roles: set[str] = set()
        for line in lines[1:]:
            role, separator, raw_size = line.partition("=")
            if (
                separator != "="
                or role not in database_sizes
                or role in observed_roles
            ):
                raise ValueError
            size = int(raw_size)
            if size <= 0:
                raise ValueError
            database_sizes[role] = size
            observed_roles.add(role)
        available_bytes = getattr(facts, "available_disk_bytes")
        backup_available_bytes = getattr(facts, "backup_available_disk_bytes")
        if (
            database_available_bytes <= 0
            or type(available_bytes) is not int
            or type(backup_available_bytes) is not int
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise _preflight("PostgreSQL data-volume capacity is unobservable") from None
    return RestorePreflightFacts(
        available_bytes,
        backup_available_bytes,
        database_available_bytes,
        database_sizes,
    )


def validate_restore_inspection_preflight(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    host_validator: HostValidator | None = None,
) -> HostFacts | object:
    """Validate cleanup and discovery authority without observing capacity."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("restore inspection preflight requires an environment configuration")
    facts = (host_validator or validate_restore_inspection_host)(remote, config)
    runtime, database = collect_restore_inspection_preflight(remote, config)
    if not runtime.succeeded:
        raise _preflight(
            "runtime environment ownership, mode, required keys, or distro Python is invalid"
        )
    if not database.succeeded:
        raise _preflight("PostgreSQL maintenance access or database role preflight failed")
    return facts


def _preflight(message: str) -> OpsError:
    return OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "preflight",
        message,
        changed=False,
        next_action=f"{message}; correct this prerequisite before retrying",
    )


__all__ = [
    "RestorePreflightFacts",
    "validate_operational_preflight",
    "validate_restore_inspection_preflight",
    "validate_restore_preflight",
]
