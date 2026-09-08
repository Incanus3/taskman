"""Read-only supported-host discovery and refusal checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import ipaddress
import re
import socket
from pathlib import PurePosixPath
from typing import Callable, Iterable

from ..config import EnvironmentConfig
from ..remote import CommandResult, Remote
from ..services.caddy import render_caddyfile


# These minimums deliberately describe the smallest useful single-host
# footprint.  A future capacity model belongs in accepted configuration, not
# in ad-hoc workflow branching.
MINIMUM_MEMORY_BYTES = 1 * 1024**3
MINIMUM_DISK_BYTES = 10 * 1024**3

_SYSTEMD_UNITS = ("taskman.service", "taskman-backup.service", "taskman-backup.timer", "caddy.service")
_ACCOUNT_NAME = "taskman"
_PROVISIONING_MARKER = PurePosixPath("/var/lib/taskman-provisioning.state")
_PROVISIONING_MARKER_SCRIPT = (
    'path=$1; if [ ! -e "$path" ] && [ ! -L "$path" ]; then printf absent; '
    'elif [ -f "$path" ] && [ ! -L "$path" ] '
    '&& [ "$(stat --format=\'%U:%G:%a\' "$path")" = root:root:600 ] '
    '&& [ "$(cat "$path")" = taskman-provisioning-v1 ]; then printf managed; '
    'else printf unknown; fi'
)
_CADDYFILE = PurePosixPath("/etc/caddy/Caddyfile")
_CADDY_CONFIG_KEYS = ("config", "config_hash", "config_metadata")
_CADDY_CONFIG_SCRIPT = r'''set -eu
config=$1
if [ ! -e "$config" ] && [ ! -L "$config" ]; then
  printf 'config=absent\nconfig_hash=\nconfig_metadata=\n'
elif [ -f "$config" ] && [ ! -L "$config" ]; then
  printf 'config=regular\n'
  sha256sum "$config" | awk '{printf "config_hash=%s\n", $1}'
  stat --format='config_metadata=%U:%G:%a' "$config"
else
  printf 'config=invalid\nconfig_hash=\nconfig_metadata=\n'
fi'''
_CAPACITY_SCRIPT = (
    'path=$1; while [ ! -e "$path" ]; do parent=${path%/*}; '
    '[ "$parent" != "$path" ] || exit 1; path=$parent; done; '
    'df -B1 --output=avail "$path"'
)
_RUNTIME_ENVIRONMENT = "/etc/taskman/taskman.env"
_PGPASS = "/etc/taskman/pgpass"
_REQUIRED_RUNTIME_KEYS = (
    "DATABASE_URL",
    "SECRET_KEY_BASE",
    "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET",
    "PHX_HOST",
    "RESEND_API_KEY",
    "MAIL_FROM",
    "PORT",
    "POOL_SIZE",
    "PHX_SERVER",
)
_RUNTIME_PREFLIGHT = r'''set -eu
path=$1
shift
test -f "$path" && test ! -L "$path"
test "$(stat -c '%U:%G:%a' -- "$path")" = root:root:600
for required do
  awk -F= -v required="$required" '
    $1 == required { count += 1; if (length($0) <= length(required) + 1) empty = 1 }
    END { exit count == 1 && !empty ? 0 : 1 }
  ' "$path"
done
command -v python3 >/dev/null 2>&1
'''
_DATABASE_PREFLIGHT = r'''set -eu
database_host=$1; database_port=$2; database_role=$3; database_name=$4; backup_root=$5; pgpass=$6
test -f "$pgpass" && test ! -L "$pgpass"
test "$(stat -c '%U:%G:%a' -- "$pgpass")" = root:root:600
export PGPASSFILE=$pgpass
psql --no-psqlrc --host "$database_host" --port "$database_port" --username "$database_role" --dbname "$database_name" --tuples-only --no-align --command 'SELECT 1' >/dev/null 2>&1
database_bytes=$(psql --no-psqlrc --host "$database_host" --port "$database_port" --username "$database_role" --dbname "$database_name" --tuples-only --no-align --command 'SELECT pg_database_size(current_database())' 2>/dev/null)
available_bytes=$(df -B1 --output=avail "$backup_root" 2>/dev/null | awk 'NR > 1 && $1 ~ /^[0-9]+$/ { value=$1 } END { print value }')
case "$database_bytes:$available_bytes" in *[!0-9:]*|*::*|:*) exit 1;; esac
test "$database_bytes" -gt 0 && test "$database_bytes" -le 900000000000000000
margin=$(( (database_bytes + 9) / 10 ))
test "$margin" -ge 67108864 || margin=67108864
required=$(( database_bytes + margin ))
test "$available_bytes" -ge "$required"
'''


class DiscoveryState(str, Enum):
    """Whether a conflict probe is clean, detects state, or cannot inspect."""

    CLEAN = "clean"
    DETECTED = "detected"
    UNAVAILABLE = "unavailable"


class ProvisioningMarkerState(str, Enum):
    """Whether the durable Taskman provisioning anchor is trustworthy."""

    ABSENT = "absent"
    MANAGED = "managed"
    UNKNOWN = "unknown"


class CaddyState(str, Enum):
    """Validated lifecycle state of the public HTTPS ownership boundary."""

    ABSENT = "absent"
    PREPARED = "prepared"
    STAGED = "staged"
    ACTIVE = "active"
    INVALID = "invalid"


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
    postgres_available: bool
    postgres_sudo_available: bool | None
    active_ssh_port: int | None
    memory_bytes: int
    available_disk_bytes: int
    backup_available_disk_bytes: int
    dns_addresses: tuple[str, ...]
    listeners: tuple[Listener, ...]
    existing_paths: tuple[PurePosixPath, ...]
    provisioning_marker: ProvisioningMarkerState
    caddy_state: CaddyState
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
    expected_caddyfile_sha256: str | None = None,
) -> HostFacts:
    """Gather the complete immutable preflight snapshot without mutation."""

    paths = _managed_paths(config)

    # Keep this collection step unconditional. An unsupported fact must not
    # short-circuit later reads: refusal happens only after the snapshot is
    # complete, and this function never invokes a mutating remote operation.
    os_release = remote.run(("cat", "/etc/os-release"))
    architecture = remote.run(("uname", "-m"))
    pid1 = remote.run(("cat", "/proc/1/comm"))
    sudo = remote.run(("sudo", "-n", "true"))
    memory = remote.run(
        ("sh", "-c", "awk '/MemTotal:/{printf \"%.0f\\n\", $2 * 1024; exit}' /proc/meminfo")
    )
    disk = _capacity(remote, config.install_root)
    backup_disk = _capacity(remote, config.backup_root)
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
    provisioning_marker = remote.run(
        (
            "sh",
            "-c",
            _PROVISIONING_MARKER_SCRIPT,
            "taskman-provisioning-marker",
            str(_PROVISIONING_MARKER),
        )
    )
    units = remote.run(("systemctl", "list-unit-files", "--no-legend", "--no-pager", *_SYSTEMD_UNITS))
    account = remote.run(("getent", "passwd", _ACCOUNT_NAME))
    account_group = remote.run(("getent", "group", _ACCOUNT_NAME))
    postgres_account = remote.run(("getent", "passwd", "postgres"))
    postgres_client = remote.run(("sh", "-c", "command -v psql"))

    account_states = (
        _getent_state(account),
        _getent_state(account_group),
    )
    postgres_account_state = _getent_state(postgres_account)
    postgres_client_state = _command_state(postgres_client)
    postgres_available = (
        postgres_account_state is DiscoveryState.DETECTED
        and postgres_client_state is DiscoveryState.DETECTED
    )
    postgres_partially_present = (
        postgres_account_state is DiscoveryState.DETECTED
        or postgres_client_state is DiscoveryState.DETECTED
    ) and not postgres_available

    postgres_sudo: CommandResult | None = None
    databases: CommandResult | None = None
    if postgres_available:
        postgres_sudo = remote.run(("sudo", "-n", "-u", "postgres", "true"))
        databases = remote.run(
            (
                "sh",
                "-c",
                "sudo -n -u postgres psql -Atq -c 'SELECT datname FROM pg_database'",
                "taskman-host-facts",
            )
        )

    caddy_listener_owners = remote.run(("ss", "-H", "-ltnp"), sudo=True)
    caddy_config = remote.run(
        (
            "sh",
            "-c",
            _CADDY_CONFIG_SCRIPT,
            "taskman-caddy-config",
            str(_CADDYFILE),
        ),
        sudo=True,
    )

    found_paths = _existing_paths(_stdout(existing_paths), paths)
    found_units = _existing_units(_stdout(units))
    found_accounts = (_ACCOUNT_NAME,) if DiscoveryState.DETECTED in account_states else ()
    found_databases = _existing_databases(
        _stdout(databases) if databases is not None else "", config.database_name
    )
    expected_config_hash = _expected_caddyfile_hash(config, expected_caddyfile_sha256)
    caddy_state = _caddy_state(
        paths=found_paths,
        units=found_units,
        listeners=_listeners(_stdout(listeners)),
        listener_owners=_listener_owners(_stdout(caddy_listener_owners)),
        config=_caddy_config(_stdout(caddy_config)),
        expected_config_hash=expected_config_hash,
    )
    conflict_states = (
        _output_state(existing_paths, detected=bool(found_paths)),
        _output_state(units, detected=bool(found_units)),
        _combine_states(account_states),
        _output_state(databases, detected=bool(found_databases))
        if databases is not None
        else DiscoveryState.CLEAN,
    )
    required_results = (
        ("operating-system", os_release),
        ("architecture", architecture),
        ("PID 1", pid1),
        ("administrator sudo", sudo),
        ("memory", memory),
        ("install-root disk", disk),
        ("backup-root disk", backup_disk),
        ("active SSH connection", active_ssh),
        ("TCP listeners", listeners),
        ("provisioning marker", provisioning_marker),
        ("Caddy listener ownership", caddy_listener_owners),
        ("Caddy configuration", caddy_config),
    )
    failed_checks = [name for name, result in required_results if not result.succeeded]
    failed_checks.extend(
        name
        for name, state in (
            ("managed paths", conflict_states[0]),
            ("managed units", conflict_states[1]),
            ("managed accounts", conflict_states[2]),
            ("PostgreSQL account", postgres_account_state),
            ("PostgreSQL client", postgres_client_state),
            ("managed databases", conflict_states[3]),
        )
        if state is DiscoveryState.UNAVAILABLE
    )
    if postgres_partially_present:
        failed_checks.append("incomplete PostgreSQL discovery")
    if postgres_sudo is not None and not postgres_sudo.succeeded:
        failed_checks.append("postgres sudo")

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
        postgres_available=postgres_available,
        postgres_sudo_available=postgres_sudo.succeeded if postgres_sudo is not None else None,
        active_ssh_port=_port(_stdout(active_ssh)),
        memory_bytes=_byte_count(_stdout(memory)),
        available_disk_bytes=_disk_bytes(_stdout(disk)),
        backup_available_disk_bytes=_disk_bytes(_stdout(backup_disk)),
        dns_addresses=dns_addresses,
        listeners=_listeners(_stdout(listeners)),
        existing_paths=found_paths,
        provisioning_marker=_provisioning_marker_state(_stdout(provisioning_marker)),
        caddy_state=caddy_state,
        existing_units=found_units,
        existing_accounts=found_accounts,
        existing_databases=found_databases,
        failed_checks=tuple(failed_checks),
    )


def _managed_paths(config: EnvironmentConfig) -> tuple[PurePosixPath, ...]:
    return (
        config.install_root,
        config.release_root,
        config.deployment_root,
        config.backup_root,
        PurePosixPath("/etc/taskman"),
        _PROVISIONING_MARKER,
        PurePosixPath("/etc/systemd/system/taskman.service"),
        _CADDYFILE,
    )


def _expected_caddyfile_hash(config: EnvironmentConfig, supplied_hash: str | None) -> str:
    """Use provisioning's rendered Caddy bytes when they are available."""

    if supplied_hash is not None:
        if re.fullmatch(r"[0-9a-f]{64}", supplied_hash) is None:
            raise ValueError("expected Caddyfile hash must be a lowercase SHA-256 digest")
        return supplied_hash
    return hashlib.sha256(render_caddyfile(config).encode("utf-8")).hexdigest()


def collect_operational_preflight(
    remote: Remote, config: EnvironmentConfig
) -> tuple[CommandResult, CommandResult]:
    """Collect non-secret runtime and database prerequisite evidence."""

    runtime = remote.run(
        (
            "sh",
            "-ceu",
            _RUNTIME_PREFLIGHT,
            "taskman-runtime-preflight",
            _RUNTIME_ENVIRONMENT,
            *_REQUIRED_RUNTIME_KEYS,
        ),
        sudo=True,
        stdin=None,
        sensitive=True,
    )
    database = remote.run(
        (
            "sh",
            "-ceu",
            _DATABASE_PREFLIGHT,
            "taskman-database-preflight",
            config.database_host,
            str(config.database_port),
            config.database_role,
            config.database_name,
            config.backup_root.as_posix(),
            _PGPASS,
        ),
        sudo=True,
        stdin=None,
        sensitive=True,
    )
    return runtime, database


def _capacity(remote: Remote, root: PurePosixPath) -> CommandResult:
    """Read available bytes from the nearest existing ancestor without mutation."""

    return remote.run(("sh", "-c", _CAPACITY_SCRIPT, "taskman-capacity", str(root)))


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


def _listener_owners(value: str) -> dict[Listener, tuple[str, int]] | None:
    """Parse only unambiguous process owners from privileged ``ss`` output."""

    owners: dict[Listener, tuple[str, int]] = {}
    for line in value.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 4 or fields[0].upper() != "LISTEN":
            continue
        listener = _listener_from_fields(fields)
        if listener is None or listener.port not in {80, 443}:
            continue
        if len(fields) != 6:
            return None
        match = re.fullmatch(
            r'users:\(\("(?P<name>[^"]+)",pid=(?P<pid>[1-9][0-9]*),fd=[0-9]+\)\)',
            fields[5],
        )
        if match is None:
            return None
        owner = (match.group("name"), int(match.group("pid")))
        if listener in owners:
            return None
        owners[listener] = owner
    return owners


def _listener_from_fields(fields: list[str]) -> Listener | None:
    address, separator, port_text = fields[3].rpartition(":")
    if not separator or not port_text.isdecimal():
        return None
    port = int(port_text)
    if not 1 <= port <= 65_535:
        return None
    return Listener(address=address or "*", port=port)


def _caddy_config(value: str) -> dict[str, str] | None:
    evidence: dict[str, str] = {}
    for line in value.splitlines():
        key, separator, field = line.partition("=")
        if not separator or key not in _CADDY_CONFIG_KEYS or key in evidence:
            return None
        evidence[key] = field
    return evidence if tuple(evidence) == _CADDY_CONFIG_KEYS else None


def _caddy_state(
    *,
    paths: tuple[PurePosixPath, ...],
    units: tuple[str, ...],
    listeners: tuple[Listener, ...],
    listener_owners: dict[Listener, tuple[str, int]] | None,
    config: dict[str, str] | None,
    expected_config_hash: str,
) -> CaddyState:
    """Classify only the Caddy state that admission must distinguish."""

    if config is None or listener_owners is None:
        return CaddyState.INVALID
    caddy_path_present = _CADDYFILE in paths
    public_listeners = tuple(listener for listener in listeners if listener.port in {80, 443})
    config_state = config["config"]

    caddy_unit_present = "caddy.service" in units
    no_caddy_evidence = not caddy_path_present and not caddy_unit_present and not public_listeners
    if no_caddy_evidence:
        return CaddyState.ABSENT if config_state == "absent" else CaddyState.INVALID

    if not caddy_path_present and not public_listeners:
        return CaddyState.PREPARED if config_state == "absent" else CaddyState.INVALID
    if not caddy_path_present or not _trusted_caddy_config(config, expected_config_hash):
        return CaddyState.INVALID
    if not public_listeners:
        return CaddyState.STAGED
    if {listener.port for listener in public_listeners} != {80, 443}:
        return CaddyState.INVALID
    if any(listener_owners.get(listener, ("", 0))[0] != "caddy" for listener in public_listeners):
        return CaddyState.INVALID
    if set(listener_owners) != set(public_listeners):
        return CaddyState.INVALID
    return CaddyState.ACTIVE


def _trusted_caddy_config(config: dict[str, str], expected_hash: str) -> bool:
    return (
        config["config"] == "regular"
        and config["config_hash"] == expected_hash
        and config["config_metadata"] == "root:root:644"
    )


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


def _provisioning_marker_state(value: str) -> ProvisioningMarkerState:
    marker = value.strip()
    if marker == "absent":
        return ProvisioningMarkerState.ABSENT
    if marker == "managed":
        return ProvisioningMarkerState.MANAGED
    return ProvisioningMarkerState.UNKNOWN


def _getent_state(result: CommandResult) -> DiscoveryState:
    return _presence_state(result, absence_returncode=2)


def _command_state(result: CommandResult) -> DiscoveryState:
    return _presence_state(result, absence_returncode=1)


def _presence_state(result: CommandResult, *, absence_returncode: int) -> DiscoveryState:
    if result.succeeded:
        return DiscoveryState.DETECTED
    if result.returncode == absence_returncode:
        return DiscoveryState.CLEAN
    return DiscoveryState.UNAVAILABLE


def _output_state(result: CommandResult | None, *, detected: bool) -> DiscoveryState:
    if result is None:
        return DiscoveryState.CLEAN
    if not result.succeeded:
        return DiscoveryState.UNAVAILABLE
    return DiscoveryState.DETECTED if detected else DiscoveryState.CLEAN


def _combine_states(states: tuple[DiscoveryState, ...]) -> DiscoveryState:
    if DiscoveryState.UNAVAILABLE in states:
        return DiscoveryState.UNAVAILABLE
    if DiscoveryState.DETECTED in states:
        return DiscoveryState.DETECTED
    return DiscoveryState.CLEAN


def _existing_databases(value: str, database_name: str) -> tuple[str, ...]:
    return (database_name,) if database_name in {line.strip() for line in value.splitlines()} else ()


def _normalise_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    normalised = {str(ipaddress.ip_address(address)) for address in addresses}
    return tuple(sorted(normalised))


def _resolve_public_dns(hostname: str) -> tuple[str, ...]:
    records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return tuple(record[4][0] for record in records)


def _stdout(result: CommandResult) -> str:
    return result.stdout if isinstance(result.stdout, str) else ""


__all__ = [
    "CaddyState",
    "HostFacts",
    "Listener",
    "MINIMUM_DISK_BYTES",
    "MINIMUM_MEMORY_BYTES",
    "ProvisioningMarkerState",
    "collect_host_facts",
]
