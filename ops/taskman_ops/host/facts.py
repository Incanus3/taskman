"""Read-only supported-host discovery and refusal checks."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from enum import Enum
import ipaddress
import re
import socket
from pathlib import PurePosixPath
from typing import Callable, Iterable

from ..config import EnvironmentConfig
from ..remote import CommandResult, Remote
from ..services.caddy import build_caddy_ownership_plan, inspect_caddy
from ..host_protocol import HostResult
from ..host_protocol.caddy import validate_caddy_authority


# These minimums deliberately describe the smallest useful single-host
# footprint.  A future capacity model belongs in accepted configuration, not
# in ad-hoc workflow branching.
MINIMUM_MEMORY_BYTES = 1 * 1024**3
MINIMUM_DISK_BYTES = 10 * 1024**3

_SYSTEMD_UNITS = ("taskman.service", "taskman-backup.service", "taskman-backup.timer", "caddy.service")
_ACCOUNT_NAME = "taskman"
_CADDYFILE = PurePosixPath("/etc/caddy/Caddyfile")
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
_TASKMAN_SERVICE_AUTHORITY_SCRIPT = r'''set -eu
emit() { printf '%s=%s\n' "$1" "$2"; }
root=$1
properties=$(systemctl show taskman.service --property=ActiveState --property=MainPID --property=ControlGroup --value 2>/dev/null || true)
active=$(printf '%s\n' "$properties" | sed -n '1p')
pid=$(printf '%s\n' "$properties" | sed -n '2p')
cgroup=$(printf '%s\n' "$properties" | sed -n '3p')
executable=
owner=
process_cgroup=
release_root=
case "$pid" in ''|0|*[!0-9]*) ;; *)
  executable=$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)
  owner=$(stat --format='%U:%G' "/proc/$pid" 2>/dev/null || true)
  process_cgroup=$(sed -n '1s/^[0-9][0-9]*:://p' "/proc/$pid/cgroup" 2>/dev/null || true)
;; esac
if [ -d "$root/current" ]; then
  release_root=$(readlink -f "$root/current" 2>/dev/null || true)
  case "$release_root" in "$root"/releases/*) ;; *) release_root= ;; esac
fi
emit active "$active"
emit pid "$pid"
emit cgroup "$cgroup"
emit executable "$executable"
emit owner "$owner"
emit process_cgroup "$process_cgroup"
emit release_root "$release_root"'''


class DiscoveryState(str, Enum):
    """Whether a conflict probe is clean, detects state, or cannot inspect."""

    CLEAN = "clean"
    DETECTED = "detected"
    UNAVAILABLE = "unavailable"


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
    path_metadata: tuple[tuple[PurePosixPath, str], ...]
    caddy_state: CaddyState
    existing_units: tuple[str, ...]
    existing_accounts: tuple[str, ...]
    taskman_account_compatible: bool
    existing_databases: tuple[str, ...]
    failed_checks: tuple[str, ...]
    postgresql: Mapping[str, object] | None = None
    initial_database_empty: bool = False
    applied_migrations: tuple[int, ...] = ()
    taskman_service_pid: int | None = None
    taskman_service_executable: str = ""
    taskman_service_owner: str = ""
    taskman_service_cgroup: str = ""
    taskman_service_release_root: str = ""
    taskman_listener_owners: tuple[tuple[Listener, tuple[str, int]], ...] = ()

    @property
    def systemd(self) -> bool:
        return self.pid1 == "systemd"


def collect_host_facts(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
    caddy_authority: HostResult | None = None,
    provision_authority: Mapping[str, object] | None = None,
    force_postgresql_config: bool = False,
) -> HostFacts:
    """Gather the complete immutable preflight snapshot without mutation."""

    postgresql = None
    if provision_authority is not None:
        from ..host_protocol.postgresql import validate_postgresql_authority
        postgresql = validate_postgresql_authority(provision_authority["postgresql"],
            package_track=config.postgres_package_track, port=config.database_port,
            force_postgresql_config=force_postgresql_config)
        if (provision_authority["database_state"] == "ready") != (postgresql["database"] == "compatible"):
            raise ValueError("inconsistent PostgreSQL schema authority")
        if (type(provision_authority["initial_database_empty"]) is not bool or
                not isinstance(provision_authority["applied_migrations"], tuple)):
            raise ValueError("invalid PostgreSQL schema authority")
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
            'for path do if [ -e "$path" ] || [ -L "$path" ]; then '
            'printf "%s\\t%s\\n" "$path" "$(stat --format=\'%F:%U:%G:%a\' "$path")"; fi; done',
            "taskman-host-facts",
            *(str(path) for path in paths),
        )
    )
    # A filtered query returns status 1 when no requested units exist. Read the
    # inventory and filter locally so genuine systemctl failures remain failures.
    units = remote.run(
        ("systemctl", "list-unit-files", "--no-legend", "--no-pager"),
        stdout_limit=64 * 1024,
    )
    account = remote.run(("getent", "passwd", _ACCOUNT_NAME))
    account_group = remote.run(("getent", "group", _ACCOUNT_NAME))
    postgres_account = remote.run(("getent", "passwd", "postgres"))
    # POSIX shells differ on command-v absence (bash: 1, dash: 127).
    postgres_client = remote.run(("sh", "-c", "if command -v psql; then exit 0; else exit 1; fi"))

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
    if postgres_available and postgresql is None:
        postgres_sudo = remote.run(("sudo", "-n", "-u", "postgres", "true"))
        databases = remote.run(
            (
                "sh",
                "-c",
                "sudo -n -u postgres psql --no-psqlrc --host /var/run/postgresql --port \"$1\" --username postgres --dbname postgres --no-password --set=ON_ERROR_STOP=1 -Atq -c 'SELECT datname FROM pg_database'",
                "taskman-host-facts",
                str(config.database_port),
            )
        )
    if postgresql is not None:
        postgres_available = postgresql["cluster"] is not None
        postgres_partially_present = False
        postgres_sudo = CommandResult(0) if postgres_available else None

    caddy_listener_owners = remote.run(
        ("ss", "-H", "-ltnp",
         f"( sport = :80 or sport = :443 or sport = :{config.application_port} or sport = :{config.distribution_port} )"),
        sudo=True,
    )
    proxy_result = inspect_caddy(remote, config) if caddy_authority is None else caddy_authority
    if not isinstance(proxy_result, HostResult) or proxy_result.operation != "caddy" or proxy_result.outcome != "succeeded":
        raise ValueError("host facts require validated Caddy inspection")
    build_caddy_ownership_plan(config, proxy_result)
    proxy_authority = validate_caddy_authority(proxy_result.state["authority"])

    found_paths, path_metadata = _existing_paths(_stdout(existing_paths), paths)
    found_units = _existing_units(_stdout(units))
    observed_listeners = _listeners(_stdout(listeners))
    taskman_authority: CommandResult | None = None
    if (
        "taskman.service" in found_units
        and any(listener.port in {config.application_port, config.distribution_port} for listener in observed_listeners)
    ):
        taskman_authority = remote.run(
            ("sh", "-ceu", _TASKMAN_SERVICE_AUTHORITY_SCRIPT, "taskman-service-authority", config.install_root.as_posix()),
            sudo=True,
        )
    found_accounts = (_ACCOUNT_NAME,) if DiscoveryState.DETECTED in account_states else ()
    taskman_account_compatible = _managed_taskman_account(_stdout(account), _stdout(account_group))
    found_databases = _existing_databases(
        _stdout(databases) if databases is not None else "", config.database_name
    )
    if postgresql is not None:
        found_databases = (config.database_name,) if postgresql["database"] == "compatible" else ()
    parsed_listener_owners = _listener_owners(_stdout(caddy_listener_owners))
    caddy_state = _caddy_state(proxy_authority, observed_listeners, parsed_listener_owners)
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
        ("Caddy listener ownership", caddy_listener_owners),
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
        path_metadata=path_metadata,
        caddy_state=caddy_state,
        existing_units=found_units,
        existing_accounts=found_accounts,
        taskman_account_compatible=taskman_account_compatible,
        existing_databases=found_databases,
        failed_checks=tuple(failed_checks),
        postgresql=postgresql,
        initial_database_empty=provision_authority["initial_database_empty"] if provision_authority is not None else False,
        applied_migrations=provision_authority["applied_migrations"] if provision_authority is not None else (),
        **(_taskman_authority(_stdout(taskman_authority)) if taskman_authority is not None and taskman_authority.succeeded else {}),
        taskman_listener_owners=(
            tuple(sorted(
                (
                    (listener, owner)
                    for listener, owner in (parsed_listener_owners or {}).items()
                    if listener.port in {config.application_port, config.distribution_port}
                ),
                key=lambda item: (item[0].port, item[0].address),
            ))
        ),
    )


def _managed_paths(config: EnvironmentConfig) -> tuple[PurePosixPath, ...]:
    return (
        config.install_root,
        config.release_root,
        config.deployment_root,
        config.backup_root,
        PurePosixPath("/etc/taskman"),
        PurePosixPath("/etc/systemd/system/taskman.service"),
        _CADDYFILE,
    )


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


def collect_runtime_preflight(remote: Remote) -> CommandResult:
    """Collect the established protected runtime admission predicate."""

    return remote.run(
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
    """Parse unambiguous owners while retaining valid shared-socket evidence."""

    owners: dict[Listener, tuple[str, int]] = {}
    seen_listeners: set[Listener] = set()
    for line in value.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 4 or fields[0].upper() != "LISTEN":
            continue
        listener = _listener_from_fields(fields)
        if listener is None:
            continue
        if len(fields) != 6:
            return None
        fields[5] = fields[5].rstrip()
        if listener in seen_listeners:
            return None
        seen_listeners.add(listener)
        match = re.fullmatch(
            r'users:\(\("(?P<name>[^"]+)",pid=(?P<pid>[1-9][0-9]*),fd=[0-9]+\)\)',
            fields[5],
        )
        if match is not None:
            owners[listener] = (match.group("name"), int(match.group("pid")))
            continue
        if re.fullmatch(
            r'users:\(\("[^"]+",pid=[1-9][0-9]*,fd=[0-9]+\)(?:,\("[^"]+",pid=[1-9][0-9]*,fd=[0-9]+\))+\)',
            fields[5],
        ) is None:
            return None
    return owners


def _taskman_authority(value: str) -> dict[str, object]:
    """Return exact managed-unit process evidence or an empty invalid snapshot."""

    values: dict[str, str] = {}
    required = ("active", "pid", "cgroup", "executable", "owner", "process_cgroup", "release_root")
    for line in value.splitlines():
        key, separator, item = line.partition("=")
        if not separator or key not in required or key in values:
            return {}
        values[key] = item
    if tuple(values) != required or values["active"] != "active" or not values["pid"].isdecimal():
        return {}
    pid = int(values["pid"])
    if pid <= 0:
        return {}
    return {
        "taskman_service_pid": pid,
        "taskman_service_executable": values["executable"],
        "taskman_service_owner": values["owner"],
        "taskman_service_cgroup": values["cgroup"] if values["cgroup"] == values["process_cgroup"] else "",
        "taskman_service_release_root": values["release_root"],
    }


def _listener_from_fields(fields: list[str]) -> Listener | None:
    address, separator, port_text = fields[3].rpartition(":")
    if not separator or not port_text.isdecimal():
        return None
    port = int(port_text)
    if not 1 <= port <= 65_535:
        return None
    return Listener(address=address or "*", port=port)


def _caddy_state(authority, listeners, listener_owners) -> CaddyState:
    """Use native trust policy and require agreement with the host socket snapshot."""
    if listener_owners is None:
        return CaddyState.INVALID
    public = {listener for listener in listeners if listener.port in {80, 443}}
    expected = {(item["address"], item["port"]) for item in authority["public_listeners"]}
    observed = {(item.address.removeprefix("[").removesuffix("]"), item.port) for item in public}
    if observed != expected or any(listener_owners.get(item) != ("caddy", authority["main_pid"]) for item in public):
        return CaddyState.INVALID
    if authority["branch"] == "bootstrap":
        return CaddyState.ABSENT
    if authority["branch"] == "preserve-configuration":
        return CaddyState.PREPARED
    return CaddyState.ACTIVE if authority["service_active"] else CaddyState.STAGED


def _existing_paths(
    value: str, candidates: tuple[PurePosixPath, ...]
) -> tuple[tuple[PurePosixPath, ...], tuple[tuple[PurePosixPath, str], ...]]:
    allowed = {path.as_posix(): path for path in candidates}
    metadata: dict[PurePosixPath, str] = {}
    for line in value.splitlines():
        path, separator, details = line.partition("\t")
        if path in allowed:
            metadata[allowed[path]] = details if separator else ""
    paths = tuple(sorted(metadata))
    return paths, tuple((path, metadata[path]) for path in paths)


def _existing_units(value: str) -> tuple[str, ...]:
    found = {
        line.split(maxsplit=1)[0]
        for line in value.splitlines()
        if line.split(maxsplit=1) and line.split(maxsplit=1)[0] in _SYSTEMD_UNITS
    }
    return tuple(sorted(found))


def _managed_taskman_account(account: str, group: str) -> bool:
    """Recognize only the exact non-login account declared by the baseline."""

    fields = account.strip().split(":")
    group_fields = group.strip().split(":")
    return (
        len(fields) == 7
        and fields[0] == _ACCOUNT_NAME
        and fields[5] == "/var/lib/taskman"
        and fields[6] == "/usr/sbin/nologin"
        and len(group_fields) >= 3
        and group_fields[0] == _ACCOUNT_NAME
        and fields[3] == group_fields[2]
    )


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
    "collect_host_facts",
    "collect_operational_preflight",
    "collect_runtime_preflight",
]
