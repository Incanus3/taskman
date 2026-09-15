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
    collect_runtime_preflight,
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
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoreInspectionFacts:
    host_facts: HostFacts | object
    warnings: tuple[str, ...] = ()


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
    runtime = collect_runtime_preflight(remote)
    if not runtime.succeeded:
        raise _preflight(
            "runtime environment ownership, mode, required keys, or distro Python is invalid"
        )
    result = _restore_helper_preflight(remote, config, "capacity")
    try:
        database_available_bytes = result.state["database_available_bytes"]
        database_sizes = result.state["database_size_bytes"]
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
        result.warnings,
    )


def validate_restore_inspection_preflight(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    host_validator: HostValidator | None = None,
) -> RestoreInspectionFacts:
    """Validate cleanup and discovery authority without observing capacity."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("restore inspection preflight requires an environment configuration")
    facts = (host_validator or validate_restore_inspection_host)(remote, config)
    runtime = collect_runtime_preflight(remote)
    if not runtime.succeeded:
        raise _preflight(
            "runtime environment ownership, mode, required keys, or distro Python is invalid"
        )
    result = _restore_helper_preflight(remote, config, "inspection")
    return RestoreInspectionFacts(facts, result.warnings)


def _restore_helper_preflight(remote: Remote, config: EnvironmentConfig, mode: str):
    from .helper import database_settings, request, run_request

    message = (
        "PostgreSQL maintenance access or database role preflight failed"
        if mode == "inspection"
        else "PostgreSQL maintenance access, database role, or restore capacity preflight failed"
    )
    try:
        result = run_request(
            remote,
            request(
                "restore_preflight",
                config,
                parameters={
                    "mode": mode,
                    "credentials_path": "/etc/taskman/pgpass",
                    "database": database_settings(config),
                },
            ),
        )
    except OpsError as error:
        raise _preflight(message, warnings=error.warnings) from None
    expected = {"mode"} if mode == "inspection" else {
        "mode", "database_available_bytes", "database_size_bytes"
    }
    if result.outcome != "succeeded" or set(result.state) != expected or result.state["mode"] != mode:
        raise _preflight(message, warnings=result.warnings)
    if mode == "capacity":
        sizes = result.state["database_size_bytes"]
        counts = (
            result.state["database_available_bytes"],
            *(sizes.values() if isinstance(sizes, Mapping) else ()),
        )
        if (
            not isinstance(sizes, Mapping)
            or set(sizes) != {"canonical", "temporary", "retired"}
            or type(result.state["database_available_bytes"]) is not int
            or not 0 < result.state["database_available_bytes"] <= (1 << 63) - 1
            or any(
                count is not None
                and (type(count) is not int or not 0 < count <= (1 << 63) - 1)
                for count in counts
            )
        ):
            raise _preflight(message, warnings=result.warnings)
    return result


def validate_cleanup_preflight(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    host_validator: HostValidator | None = None,
) -> HostFacts | object:
    """Validate filesystem cleanup authority without querying runtime or database state."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("cleanup preflight requires an environment configuration")
    return (host_validator or validate_restore_inspection_host)(remote, config)


def _preflight(message: str, *, warnings: tuple[str, ...] = ()) -> OpsError:
    return OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "preflight",
        message,
        changed=False,
        next_action=f"{message}; correct this prerequisite before retrying",
        warnings=warnings,
    )


__all__ = [
    "RestorePreflightFacts",
    "RestoreInspectionFacts",
    "validate_cleanup_preflight",
    "validate_operational_preflight",
    "validate_restore_inspection_preflight",
    "validate_restore_preflight",
]
