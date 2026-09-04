"""Read-only supported-host discovery and refusal checks."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from pathlib import PurePosixPath
from typing import Callable, Iterable

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..remote import CommandResult, Remote


# These minimums deliberately describe the smallest useful single-host
# footprint.  A future capacity model belongs in accepted configuration, not
# in ad-hoc workflow branching.
MINIMUM_MEMORY_BYTES = 1 * 1024**3
MINIMUM_DISK_BYTES = 10 * 1024**3

_SYSTEMD_UNITS = ("taskman.service", "taskman-backup.service", "taskman-backup.timer")
_ACCOUNT_NAME = "taskman"


@dataclass(frozen=True)
class Listener:
    """One parsed TCP listening socket, never raw ``ss`` output."""

    address: str
    port: int


@dataclass(frozen=True)
class HostFacts:
    """Immutable structured evidence collected before any host mutation."""

    os_id: str
    ubuntu_release: str
    architecture: str
    pid1: str
    sudo_available: bool
    active_ssh_port: int | None
    memory_bytes: int
    available_disk_bytes: int
    dns_addresses: tuple[str, ...]
    listeners: tuple[Listener, ...]
    existing_paths: tuple[PurePosixPath, ...]
    existing_units: tuple[str, ...]
    existing_accounts: tuple[str, ...]
    existing_databases: tuple[str, ...]
    failed_checks: tuple[str, ...]

    @property
    def systemd(self) -> bool:
        return self.pid1 == "systemd"


def collect_host_facts(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
) -> HostFacts:
    """Gather the complete immutable preflight snapshot without mutation."""

    paths = _managed_paths(config)

    # Keep this collection phase unconditional.  An unsupported fact must not
    # short-circuit later reads: refusal happens only after the snapshot is
    # complete, and this function never invokes a mutating remote operation.
    os_release = remote.run(("cat", "/etc/os-release"))
    architecture = remote.run(("uname", "-m"))
    pid1 = remote.run(("cat", "/proc/1/comm"))
    sudo = remote.run(("sudo", "-n", "true"))
    memory = remote.run(("sh", "-c", "awk '/MemTotal:/{print $2 * 1024}' /proc/meminfo"))
    disk = remote.run(("df", "-B1", "--output=avail", str(config.managed_root.parent)))
    active_ssh = remote.run(("sh", "-c", "printf '%s\\n' \"${SSH_CONNECTION##* }\""))
    listeners = remote.run(("ss", "-H", "-ltn"))
    existing_paths = remote.run(
        (
            "sh",
            "-c",
            'for path do if [ -e "$path" ] || [ -L "$path" ]; then printf "%s\\n" "$path"; fi; done',
            "taskman-host-facts",
            *(str(path) for path in paths),
        )
    )
    units = remote.run(("systemctl", "list-unit-files", "--no-legend", "--no-pager", *_SYSTEMD_UNITS))
    accounts = remote.run(
        ("sh", "-c", "getent passwd taskman; getent group taskman", "taskman-host-facts")
    )
    databases = remote.run(
        (
            "sh",
            "-c",
            "sudo -n -u postgres psql -Atq -c 'SELECT datname FROM pg_database'",
            "taskman-host-facts",
        )
    )
    required_results = (
        ("operating-system", os_release),
        ("architecture", architecture),
        ("PID 1", pid1),
        ("memory", memory),
        ("disk", disk),
        ("active SSH connection", active_ssh),
        ("TCP listeners", listeners),
    )

    os_id, ubuntu_release = _os_release(os_release)
    resolve = resolver or _resolve_public_dns
    try:
        dns_addresses = _normalise_addresses(resolve(config.public_hostname))
    except Exception:
        # DNS is configuration evidence, not a reason to expose resolver
        # diagnostics or a raw hostname in an operator-facing error.
        dns_addresses = ()

    return HostFacts(
        os_id=os_id,
        ubuntu_release=ubuntu_release,
        architecture=_normalise_architecture(_stdout(architecture)),
        pid1=_stdout(pid1).strip().lower(),
        sudo_available=sudo.succeeded,
        active_ssh_port=_port(_stdout(active_ssh)),
        memory_bytes=_byte_count(_stdout(memory)),
        available_disk_bytes=_disk_bytes(_stdout(disk)),
        dns_addresses=dns_addresses,
        listeners=_listeners(_stdout(listeners)),
        existing_paths=_existing_paths(_stdout(existing_paths), paths),
        existing_units=_existing_units(_stdout(units)),
        existing_accounts=_existing_accounts(_stdout(accounts)),
        existing_databases=_existing_databases(_stdout(databases), config.database_name),
        failed_checks=tuple(name for name, result in required_results if not result.succeeded),
    )


def validate_supported_host(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
) -> HostFacts:
    """Return validated host facts or refuse before any managed-state adoption."""

    facts = collect_host_facts(remote, config, resolver=resolver)

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
    if facts.available_disk_bytes < MINIMUM_DISK_BYTES:
        raise _unsupported("host does not meet the minimum disk requirement")
    if facts.dns_addresses != _expected_addresses(config):
        raise _unsupported("public DNS does not resolve directly to the configured VPS address")
    if not facts.sudo_available:
        raise _preflight("configured administrator cannot use passwordless sudo")
    if facts.active_ssh_port != config.ssh_port:
        raise _preflight("active SSH connection port does not match configuration")
    if _managed_conflicts(facts, config):
        raise _safety("existing managed state is ambiguous and will not be adopted")

    return facts


def _managed_paths(config: EnvironmentConfig) -> tuple[PurePosixPath, ...]:
    return (
        config.managed_root,
        config.release_root,
        config.deployment_root,
        config.backup_root,
        PurePosixPath("/etc/taskman"),
        PurePosixPath("/etc/systemd/system/taskman.service"),
        PurePosixPath("/etc/caddy/Caddyfile"),
    )


def _os_release(result: CommandResult) -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in _stdout(result).splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"ID", "VERSION_ID"}:
            values[key] = value.strip().strip('"')
    return values.get("ID", ""), values.get("VERSION_ID", "")


def _normalise_architecture(value: str) -> str:
    normalised = value.strip().lower().replace("-", "_")
    if normalised in {"amd64", "x86_64", "x64", "em64t", "intel64"}:
        return "amd64"
    return normalised


def _byte_count(value: str) -> int:
    candidate = value.strip()
    return int(candidate) if candidate.isdecimal() else 0


def _disk_bytes(value: str) -> int:
    for line in reversed(value.splitlines()):
        candidate = line.strip()
        if candidate.isdecimal():
            return int(candidate)
    return 0


def _port(value: str) -> int | None:
    candidate = value.strip()
    if not candidate.isdecimal():
        return None
    port = int(candidate)
    return port if 1 <= port <= 65_535 else None


def _listeners(value: str) -> tuple[Listener, ...]:
    listeners: set[Listener] = set()
    for line in value.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0].upper() != "LISTEN":
            continue
        address, separator, port_text = fields[3].rpartition(":")
        if not separator or not port_text.isdecimal():
            continue
        port = int(port_text)
        if 1 <= port <= 65_535:
            listeners.add(Listener(address=address or "*", port=port))
    return tuple(sorted(listeners, key=lambda listener: (listener.port, listener.address)))


def _existing_paths(value: str, candidates: tuple[PurePosixPath, ...]) -> tuple[PurePosixPath, ...]:
    allowed = {path.as_posix(): path for path in candidates}
    found = {allowed[line.strip()] for line in value.splitlines() if line.strip() in allowed}
    return tuple(sorted(found))


def _existing_units(value: str) -> tuple[str, ...]:
    found = {
        line.split(maxsplit=1)[0]
        for line in value.splitlines()
        if line.split(maxsplit=1) and line.split(maxsplit=1)[0] in _SYSTEMD_UNITS
    }
    return tuple(sorted(found))


def _existing_accounts(value: str) -> tuple[str, ...]:
    return (_ACCOUNT_NAME,) if any(line.startswith(f"{_ACCOUNT_NAME}:") for line in value.splitlines()) else ()


def _existing_databases(value: str, database_name: str) -> tuple[str, ...]:
    return (database_name,) if database_name in {line.strip() for line in value.splitlines()} else ()


def _normalise_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    normalised = {str(ipaddress.ip_address(address)) for address in addresses}
    return tuple(sorted(normalised))


def _resolve_public_dns(hostname: str) -> tuple[str, ...]:
    records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return tuple(record[4][0] for record in records)


def _expected_addresses(config: EnvironmentConfig) -> tuple[str, ...]:
    values = {config.public_ipv4}
    if config.public_ipv6 is not None:
        values.add(config.public_ipv6)
    return tuple(sorted(values))


def _managed_conflicts(facts: HostFacts, config: EnvironmentConfig) -> bool:
    reserved_ports = {
        80,
        443,
        config.application_port,
        config.distribution_port,
        config.database_port,
    }
    return bool(
        any(listener.port in reserved_ports for listener in facts.listeners)
        or facts.existing_paths
        or facts.existing_units
        or facts.existing_accounts
        or facts.existing_databases
    )


def _stdout(result: CommandResult) -> str:
    return result.stdout if isinstance(result.stdout, str) else ""


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
    "HostFacts",
    "Listener",
    "MINIMUM_DISK_BYTES",
    "MINIMUM_MEMORY_BYTES",
    "collect_host_facts",
    "validate_supported_host",
]
