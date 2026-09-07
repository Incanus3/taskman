"""Bounded host-local service, topology, release, and readiness verification."""

from __future__ import annotations

import ipaddress
import math
import os
from pathlib import Path
import re
import select
import subprocess
import time
from dataclasses import dataclass
from typing import Mapping

from taskman_ops.host_protocol import HostRequest, HostResult, PROTOCOL_VERSION

from .facts import classify_lifecycle, collect_lifecycle_facts
from .lifecycle import LifecycleError, LifecycleLockContention as LegacyLifecycleLockContention
from .lock import LifecycleLockContention, lifecycle_lock
from .paths import ManagedPaths, PathAuthorityError
from .state import HostState, StateAmbiguityError, observe_host_state


_CHECKS = (
    "taskman-service", "release-identity", "caddy-service", "listener-topology",
    "startup-journal", "local-readiness", "public-readiness", "public-hsts",
)
_NEXT_ACTION = "inspect the fixed verification summaries and correct the reported host state before retrying"
_STATUS_RE = re.compile(r"HTTP/(?:1\.[01]|2|3) ([0-9]{3})(?: [^\r\n]+)?\Z")
_FAILURE_RE = re.compile(r"failed to start|boot failed|application.*(failed|error)|database.*(failed|error)", re.I)
_MAX_OUTPUT = 9_216
_MAX_COMMAND_SECONDS = 3.0
_MAX_READINESS_SECONDS = 30.0
_VERIFY_DEADLINE_SECONDS = 45.0
_MINIMUM_MEMORY_BYTES = 1 * 1024**3
_MINIMUM_DISK_BYTES = 10 * 1024**3
_SUDO_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")


@dataclass(frozen=True)
class _LegacyVerificationResult:
    """In-process shape retained only for mutable procedures not yet migrated."""

    protocol_version: int
    operation: str
    operation_id: str
    outcome: str
    stage: str
    changed_stages: tuple[str, ...]
    lifecycle: Mapping[str, object]
    runtime_state: Mapping[str, object]
    verification: Mapping[str, object]
    residue_paths: tuple[str, ...]
    recovery_actions: tuple[str, ...]
    warnings: tuple[str, ...]


def verify(request: object, *, lifecycle_locked: bool = False):
    """Run read-only verification from a final request or legacy mutation call."""

    # Mutable procedures still pass the private legacy OperationRequest while
    # they own the lifecycle lock.  Keep that explicit type boundary until
    # those procedures migrate; a wire HostRequest always receives HostResult.
    if isinstance(request, HostRequest):
        return _verify_host_state(request, lifecycle_locked=lifecycle_locked)
    return _verify_legacy(request, lifecycle_locked=lifecycle_locked)


def _verify_legacy(request: object, *, lifecycle_locked: bool = False) -> _LegacyVerificationResult:
    """Retain the old in-process result only for mutation slices not yet migrated."""

    deadline = time.monotonic() + _VERIFY_DEADLINE_SECONDS
    try:
        paths = ManagedPaths.from_mapping(request.paths)
        # Capacity checks run before the lifecycle snapshot.  Establish root
        # authority first so ``df`` never follows a caller-controlled link.
        paths.validate_existing(owner_uid=os.geteuid())
        settings = _settings(request.parameters)
        expected = _expected_release(request.expected_state)
    except (PathAuthorityError, ValueError):
        return _host_failure(request, "unsupported")

    authority = _host_authority(settings, paths, deadline)
    if authority is not None:
        return _host_failure(request, authority)

    if lifecycle_locked:
        # The deploy/genesis operation already owns the exclusive lifecycle
        # lock and has atomically selected ``expected``. Reacquiring discovery's
        # shared flock would contend with that same process on Linux.
        if expected is None:
            return _release_selection_failure(request)
        release_id = expected
        release_path = (paths.release_root / release_id).as_posix()
    else:
        try:
            facts = collect_lifecycle_facts(paths, deadline=deadline, operation="verify")
            if classify_lifecycle(facts) != "managed":
                raise LifecycleError("no current managed release")
            release_id = facts.records.current_release_id
            assert release_id is not None
            if expected is not None and expected != release_id:
                return _release_selection_failure(request)
        except LegacyLifecycleLockContention as error:
            return _lock_failure(request, error)
        except (LifecycleError, PathAuthorityError, ValueError):
            return _release_selection_failure(request)

        adoption = {record.release_id: record.release_path.as_posix() for record in facts.records.adoptions}
        release_path = adoption.get(release_id, (paths.release_root / release_id).as_posix())
    checks: list[dict[str, object]] = []
    service_ok, pid = _service_state(deadline)
    checks.append(_check("taskman-service", service_ok, "taskman.service is active with a positive MainPID", "taskman.service is not active with a usable MainPID"))
    executable_ok = service_ok and _main_pid_matches_release(pid, release_path, deadline)
    checks.append(_check("release-identity", executable_ok, "systemd MainPID executable is under the selected release", "systemd MainPID executable does not match the selected release"))
    caddy_ok = _successful(("systemctl", "is-active", "--quiet", "caddy.service"), _command_timeout(deadline))[0]
    checks.append(_check("caddy-service", caddy_ok, "caddy.service is active", "caddy.service is not active"))
    topology_ok = _listener_topology(settings["application_port"], settings["distribution_port"], settings["database_port"], deadline)
    checks.append(_check("listener-topology", topology_ok, "Taskman, distribution, and PostgreSQL listeners have the required topology", "listener topology is missing, public, malformed, or ambiguous"))
    checks.append(_journal_check(deadline))
    if not _passed(checks):
        return _result(request, "failed", "verification", _report(8, release_id, expected, checks))

    readiness_deadline = min(deadline, time.monotonic() + settings["readiness_timeout"])
    local_ok = _local_ready(settings["application_port"], settings["connection_timeout"], readiness_deadline)
    checks.append(_check("local-readiness", local_ok, "loopback health endpoint returned exact ready response", "loopback health endpoint did not return exact ready response before its bounded deadline"))
    if not local_ok:
        return _result(request, "failed", "verification", _report(9, release_id, expected, checks))

    remaining = min(deadline, readiness_deadline) - time.monotonic()
    public = _curl(f"https://{settings['public_hostname']}/healthz", min(settings["connection_timeout"], remaining)) if remaining >= 0.001 else None
    ready = _ready(public)
    checks.append(_check("public-readiness", ready, "public HTTPS health endpoint returned exact ready response", "public HTTPS health endpoint did not return exact ready response"))
    hsts = _hsts(public)
    checks.append(_check("public-hsts", hsts, "public HTTPS response includes HSTS", "public HTTPS response does not include valid HSTS"))
    return _result(request, "succeeded" if ready and hsts else "failed", "verified" if ready and hsts else "verification", _report(0 if ready and hsts else 9, release_id, expected, checks))


def _verify_host_state(request: HostRequest, *, lifecycle_locked: bool = False) -> HostResult:
    """Verify health and topology against one completed HostState snapshot."""

    deadline = time.monotonic() + _VERIFY_DEADLINE_SECONDS
    try:
        paths = ManagedPaths.from_mapping(request.paths)
        paths.validate_existing(owner_uid=os.geteuid())
        settings = _settings(request.parameters)
        expected = _expected_release(request.expected_state)
    except (PathAuthorityError, ValueError):
        return _final_host_failure(request, "unsupported")

    authority = _host_authority(settings, paths, deadline)
    if authority is not None:
        return _final_host_failure(request, authority)

    if lifecycle_locked:
        try:
            state = observe_host_state(paths)
        except (PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
            return _final_refusal(request)
    else:
        try:
            with lifecycle_lock(paths, timeout_seconds=5.0):
                state = observe_host_state(paths)
        except LifecycleLockContention:
            return _final_lock_failure(request)
        except (PathAuthorityError, StateAmbiguityError, OSError, TypeError, ValueError):
            return _final_refusal(request)

    release_id = state.selected_release_id
    if release_id is None:
        return HostResult(
            protocol_version=PROTOCOL_VERSION,
            operation=request.operation,
            correlation_id=request.correlation_id,
            outcome="refused",
            message="no selected release is available for verification",
            state={"selected_release_id": None},
            warnings=state.warnings,
        )
    if expected is not None and expected != release_id:
        return _final_release_selection_failure(request, release_id, state.warnings)
    if not any(record.release_id == release_id for record in state.releases):
        return _final_release_selection_failure(request, release_id, state.warnings)

    release_path = (paths.release_root / release_id).as_posix()
    checks: list[dict[str, object]] = []
    service_ok, pid = _service_state(deadline)
    checks.append(_check("taskman-service", service_ok, "taskman.service is active with a positive MainPID", "taskman.service is not active with a usable MainPID"))
    executable_ok = service_ok and _main_pid_matches_release(pid, release_path, deadline)
    checks.append(_check("release-identity", executable_ok, "systemd MainPID executable is under the selected release", "systemd MainPID executable does not match the selected release"))
    caddy_ok = _successful(("systemctl", "is-active", "--quiet", "caddy.service"), _command_timeout(deadline))[0]
    checks.append(_check("caddy-service", caddy_ok, "caddy.service is active", "caddy.service is not active"))
    topology_ok = _listener_topology(settings["application_port"], settings["distribution_port"], settings["database_port"], deadline)
    checks.append(_check("listener-topology", topology_ok, "Taskman, distribution, and PostgreSQL listeners have the required topology", "listener topology is missing, public, malformed, or ambiguous"))
    checks.append(_journal_check(deadline))
    if not _passed(checks):
        return _final_result(request, "retryable", release_id, expected, checks, state)

    readiness_deadline = min(deadline, time.monotonic() + settings["readiness_timeout"])
    local_ok = _local_ready(settings["application_port"], settings["connection_timeout"], readiness_deadline)
    checks.append(_check("local-readiness", local_ok, "loopback health endpoint returned exact ready response", "loopback health endpoint did not return exact ready response before its bounded deadline"))
    if not local_ok:
        return _final_result(request, "retryable", release_id, expected, checks, state)

    remaining = min(deadline, readiness_deadline) - time.monotonic()
    public = _curl(f"https://{settings['public_hostname']}/healthz", min(settings["connection_timeout"], remaining)) if remaining >= 0.001 else None
    ready = _ready(public)
    checks.append(_check("public-readiness", ready, "public HTTPS health endpoint returned exact ready response", "public HTTPS health endpoint did not return exact ready response"))
    hsts = _hsts(public)
    checks.append(_check("public-hsts", hsts, "public HTTPS response includes HSTS", "public HTTPS response does not include valid HSTS"))
    return _final_result(request, "succeeded" if ready and hsts else "retryable", release_id, expected, checks, state)


def _final_result(
    request: HostRequest,
    outcome: str,
    release_id: str,
    expected: str | None,
    checks: list[dict[str, object]],
    state: HostState,
) -> HostResult:
    successful = outcome == "succeeded"
    # Keep release and readiness safety categories distinct in the public
    # report.  The first five checks are the release identity/topology proof;
    # readiness checks run only after that prefix passes.
    exit_status = 0 if successful else 8 if not _passed(checks[:5]) else 9
    report = _report(exit_status, release_id, expected, checks)
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome=outcome,
        message="verification completed" if successful else "verification failed",
        state={
            "report": report,
            "selected_release_id": release_id,
            "service_state": state.service_state,
            "database_state": state.database_state,
        },
        warnings=state.warnings,
    )


def _final_host_failure(request: HostRequest, authority: str) -> HostResult:
    if authority not in {"preflight", "unsupported"}:
        raise ValueError("invalid host authority failure")
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="retryable",
        message="host preflight failed",
        state={"preflight": authority},
        warnings=(),
    )


def _final_refusal(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="refused",
        message="authoritative host state is ambiguous",
        state={},
        warnings=(),
    )


def _final_lock_failure(request: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="retryable",
        message="lifecycle lock is unavailable",
        state={"locked": True},
        warnings=(),
    )


def _final_release_selection_failure(
    request: HostRequest,
    selected_release_id: str | None,
    warnings: tuple[str, ...],
) -> HostResult:
    return HostResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        correlation_id=request.correlation_id,
        outcome="refused",
        message="selected release does not match the expected release",
        state={"selected_release_id": selected_release_id},
        warnings=warnings,
    )


def host_preflight(paths: ManagedPaths, parameters: Mapping[str, object]) -> str | None:
    """Validate non-mutating host authority before a lifecycle operation.

    Deploy and genesis call this before creating derived roots or unpacking an
    archive.  Verification calls the same private checks later alongside its
    service/readiness observations, so platform, DNS, SSH/sudo and PostgreSQL
    authority have one implementation and one bounded deadline.
    """

    deadline = time.monotonic() + _VERIFY_DEADLINE_SECONDS
    paths.validate_existing(owner_uid=os.geteuid())
    settings = _settings(parameters)
    return _host_authority(settings, paths, deadline)


def available_bytes(path: Path) -> int:
    """Read safe filesystem capacity through the fixed host command."""

    return _available_bytes(path, time.monotonic() + _MAX_COMMAND_SECONDS)


def _expected_release(value: Mapping[str, object]) -> str | None:
    if set(value) - {"expected_release_id"}:
        raise ValueError("invalid expected state")
    expected = value.get("expected_release_id")
    if expected is None:
        return None
    if type(expected) is not str or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26\.04-amd64-otp27\.3\.4\.6", expected):
        raise ValueError("invalid expected release")
    return expected


def _settings(value: Mapping[str, object]) -> dict[str, int | str | float]:
    fields = {"application_port", "distribution_port", "database_port", "public_hostname", "public_ipv4", "public_ipv6", "ssh_port", "ssh_user", "readiness_timeout", "connection_timeout"}
    if set(value) != fields:
        raise ValueError("invalid verification parameters")
    ports = {name: value[name] for name in ("application_port", "distribution_port", "database_port", "ssh_port")}
    if any(type(port) is not int or not 1 <= port <= 65_535 for port in ports.values()) or len(set(ports.values())) != 4:
        raise ValueError("invalid verification ports")
    hostname = value["public_hostname"]
    if type(hostname) is not str or not hostname or len(hostname.encode("utf-8")) > 253 or any(char.isspace() for char in hostname):
        raise ValueError("invalid public hostname")
    ssh_user = value["ssh_user"]
    if type(ssh_user) is not str or _SUDO_USER_RE.fullmatch(ssh_user) is None:
        raise ValueError("invalid SSH administrator")
    timeouts: dict[str, float] = {}
    for name in ("readiness_timeout", "connection_timeout"):
        raw = value[name]
        maximum = _MAX_READINESS_SECONDS if name == "readiness_timeout" else _MAX_COMMAND_SECONDS
        if type(raw) not in {int, float} or not math.isfinite(float(raw)) or not 0 < float(raw) <= maximum:
            raise ValueError("invalid verification timeout")
        timeouts[name] = float(raw)
    public_ipv4 = _address(value["public_ipv4"], 4)
    public_ipv6 = value["public_ipv6"]
    if public_ipv6 is not None:
        public_ipv6 = _address(public_ipv6, 6)
    return {**ports, "public_hostname": hostname, "public_ipv4": public_ipv4, "public_ipv6": public_ipv6, "ssh_user": ssh_user, **timeouts}


def _address(value: object, version: int) -> str:
    if type(value) is not str:
        raise ValueError("invalid public address")
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("invalid public address") from error
    if parsed.version != version:
        raise ValueError("invalid public address")
    return str(parsed)


def _host_authority(
    settings: Mapping[str, int | str | float],
    paths: ManagedPaths,
    deadline: float,
) -> str | None:
    """Return the stable preflight class before lifecycle/service verification.

    The helper runs as root, so the old ``sudo -n true`` test would prove only
    root's authority. ``SUDO_USER`` is supplied by sudo, while the fixed
    helper runner explicitly preserves OpenSSH's real ``SSH_CONNECTION``
    across that transition. Checking both retains evidence of the actual SSH
    administrator and connection port without inventing a sudo variable.
    """

    unsupported = not all(
        (
            _os_release_ok(deadline),
            _architecture_ok(deadline),
            _pid_one_is_systemd(deadline),
            _memory_bytes(deadline) >= _MINIMUM_MEMORY_BYTES,
            all(_available_bytes(paths.local(path), deadline) >= _MINIMUM_DISK_BYTES for path in (paths.install_root, paths.backup_root)),
            _dns_addresses(
                str(settings["public_hostname"]),
                require_ipv6=settings["public_ipv6"] is not None,
                deadline=deadline,
            )
            == _expected_public_addresses(settings),
        )
    )
    if unsupported:
        return "unsupported"
    if not _invoking_administrator_ok(str(settings["ssh_user"]), deadline):
        return "preflight"
    if not _active_ssh_connection_ok(int(settings["ssh_port"])):
        return "preflight"
    if not _successful(("runuser", "-u", "postgres", "--", "psql", "-Atqc", "SELECT 1"), _command_timeout(deadline))[0]:
        return "preflight"
    return None


def _os_release_ok(deadline: float) -> bool:
    succeeded, output = _successful(("cat", "/etc/os-release"), _command_timeout(deadline))
    if not succeeded:
        return False
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, field = line.partition("=")
        if not separator or key in values:
            return False
        values[key] = field.strip('"')
    return values.get("ID") == "ubuntu" and values.get("VERSION_ID") == "26.04"


def _architecture_ok(deadline: float) -> bool:
    succeeded, output = _successful(("uname", "-m"), _command_timeout(deadline))
    return succeeded and output.strip().lower().replace("_", "") in {"amd64", "x8664"}


def _pid_one_is_systemd(deadline: float) -> bool:
    succeeded, output = _successful(("cat", "/proc/1/comm"), _command_timeout(deadline))
    return succeeded and output == "systemd\n"


def _invoking_administrator_ok(expected_user: str, deadline: float) -> bool:
    """Prove the configured non-root sudo invoker remains passwordless."""

    actual_user = os.environ.get("SUDO_USER")
    if actual_user != expected_user or _SUDO_USER_RE.fullmatch(actual_user) is None:
        return False
    return _successful(("runuser", "-u", actual_user, "--", "sudo", "-n", "true"), _command_timeout(deadline))[0]


def _active_ssh_connection_ok(expected_port: int) -> bool:
    """Use OpenSSH's preserved session fact, not an unrelated listener."""

    value = os.environ.get("SSH_CONNECTION")
    if value is None:
        return False
    fields = value.split()
    if len(fields) != 4:
        return False
    client_address, client_port, server_address, server_port = fields
    try:
        ipaddress.ip_address(client_address)
        ipaddress.ip_address(server_address)
    except ValueError:
        return False
    return (
        client_port.isdecimal()
        and server_port.isdecimal()
        and 1 <= int(client_port) <= 65_535
        and int(server_port) == expected_port
    )


def _memory_bytes(deadline: float) -> int:
    succeeded, output = _successful(("free", "--bytes"), _command_timeout(deadline))
    if not succeeded:
        return 0
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "Mem:" and fields[1].isdecimal():
            return int(fields[1])
    return 0


def _available_bytes(path: Path, deadline: float) -> int:
    candidate = path
    while True:
        try:
            candidate.lstat()
            break
        except FileNotFoundError:
            if candidate == candidate.parent:
                return 0
            candidate = candidate.parent
        except OSError:
            return 0
    succeeded, output = _successful(("df", "--block-size=1", "--output=avail", "--", candidate.as_posix()), _command_timeout(deadline))
    lines = output.splitlines()
    if not succeeded or len(lines) != 2 or lines[0].strip() != "Avail" or not lines[1].strip().isdecimal():
        return 0
    return int(lines[1].strip())


def _dns_addresses(hostname: str, *, require_ipv6: bool, deadline: float) -> frozenset[str]:
    """Resolve all configured public families through fixed local commands."""

    commands = [("getent", "ahosts", hostname)]
    if require_ipv6:
        commands.append(("getent", "ahostsv6", hostname))
    addresses: set[str] = set()
    for command in commands:
        succeeded, output = _successful(command, _command_timeout(deadline))
        if not succeeded:
            return frozenset()
        for line in output.splitlines():
            fields = line.split()
            if not fields:
                continue
            try:
                addresses.add(str(ipaddress.ip_address(fields[0])))
            except ValueError:
                return frozenset()
    return frozenset(addresses)


def _expected_public_addresses(settings: Mapping[str, int | str | float]) -> frozenset[str]:
    values = {str(settings["public_ipv4"])}
    if settings["public_ipv6"] is not None:
        values.add(str(settings["public_ipv6"]))
    return frozenset(values)


def _service_state(deadline: float) -> tuple[bool, int]:
    succeeded, output = _successful(("systemctl", "show", "taskman.service", "--property=ActiveState", "--property=MainPID", "--value"), _command_timeout(deadline))
    lines = output.splitlines()
    if not succeeded or len(lines) != 2 or lines[0] != "active" or not lines[1].isdecimal() or int(lines[1]) <= 0:
        return False, 0
    return True, int(lines[1])


def _main_pid_matches_release(pid: int, release_path: str, deadline: float) -> bool:
    succeeded, output = _successful(("readlink", "-f", f"/proc/{pid}/exe"), _command_timeout(deadline))
    return succeeded and _one_path(output) is not None and _one_path(output).startswith(f"{release_path}/")


def _listener_values(deadline: float) -> tuple[tuple[str, int], ...] | None:
    succeeded, output = _successful(("ss", "-H", "-ltn"), _command_timeout(deadline))
    return _listeners(output) if succeeded else None


def _listener_topology(application: int | str | float, distribution: int | str | float, database: int | str | float, deadline: float) -> bool:
    parsed = _listener_values(deadline)
    if parsed is None:
        return False
    ports = (int(application), int(distribution), int(database))
    return all(_loopback_only(parsed, port) for port in ports) and not any(port == 4369 for _address, port in parsed)


def _journal_check(deadline: float) -> dict[str, object]:
    succeeded, output = _successful(("journalctl", "--no-pager", "--output=cat", "--unit", "taskman.service", "--lines=100"), _command_timeout(deadline))
    state = "unavailable" if not succeeded else "empty" if not output.strip() else "startup-failure" if _FAILURE_RE.search(output) else "clean"
    summaries = {
        "clean": "recent startup journal evidence is clean",
        "startup-failure": "recent startup journal evidence indicates a startup failure",
        "empty": "recent startup journal evidence is empty",
        "unavailable": "recent startup journal evidence is unavailable",
    }
    return _check("startup-journal", state == "clean", summaries[state], summaries[state])


def _local_ready(port: int | str | float, timeout: int | str | float, deadline: float) -> bool:
    attempts = max(1, math.ceil(deadline - time.monotonic()) + 1)
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining < 0.001:
            return False
        response = _curl(f"http://127.0.0.1:{int(port)}/healthz", min(float(timeout), remaining))
        if _ready(response):
            return True
        if attempt + 1 < attempts:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return False


def _curl(url: str, timeout: float):
    if timeout < 0.001:
        return None
    milliseconds = math.floor(timeout * 1_000)
    timeout_arg = str(milliseconds // 1_000) if milliseconds % 1_000 == 0 else f"{milliseconds / 1_000:.3f}".rstrip("0").rstrip(".")
    succeeded, output = _successful(("curl", "--disable", "--silent", "--show-error", "--max-time", timeout_arg, "--max-filesize", "1024", "--range", "0-1023", "--dump-header", "-", "--output", "-", "--", url), timeout)
    return _http(output) if succeeded else None


def _command_timeout(deadline: float) -> float:
    return min(_MAX_COMMAND_SECONDS, max(0.0, deadline - time.monotonic()))


def _successful(argv: tuple[str, ...], timeout: float) -> tuple[bool, str]:
    """Run one fixed command under a deadline without unbounded buffering."""

    if timeout < 0.001 or not math.isfinite(timeout):
        return False, ""
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert process.stdout is not None
        deadline = time.monotonic() + timeout
        output = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, ""
            readable, _, _ = select.select((process.stdout,), (), (), remaining)
            if not readable:
                return False, ""
            available = _MAX_OUTPUT - len(output)
            chunk = os.read(process.stdout.fileno(), min(8192, max(1, available)))
            if not chunk:
                break
            if available == 0 or len(chunk) > available:
                return False, ""
            output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, ""
        try:
            status = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            return False, ""
        return status == 0, output.decode("utf-8", errors="replace")
    except OSError:
        return False, ""
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()


def _listeners(value: str) -> tuple[tuple[str, int], ...] | None:
    result: list[tuple[str, int]] = []
    for line in value.splitlines():
        fields = line.split()
        if len(fields) != 5 or fields[0] != "LISTEN" or not fields[1].isdecimal() or not fields[2].isdecimal():
            return None
        address = _listener(fields[3])
        if address is None:
            return None
        result.append(address)
    return tuple(result)


def _listener(value: str) -> tuple[str, int] | None:
    if value.startswith("["):
        closing = value.find("]:")
        if closing < 0: return None
        address, port = value[1:closing], value[closing + 2:]
    else:
        address, separator, port = value.rpartition(":")
        if not separator: return None
    if not port.isdecimal() or not 1 <= int(port) <= 65_535: return None
    if address != "*":
        try: ipaddress.ip_address(address)
        except ValueError: return None
    return address, int(port)


def _loopback_only(values: tuple[tuple[str, int], ...], port: int) -> bool:
    matching = [address for address, candidate in values if candidate == port]
    if not matching: return False
    try: return all(ipaddress.ip_address(address).is_loopback for address in matching)
    except ValueError: return False


def _http(value: str):
    if len(value.encode("utf-8")) > _MAX_OUTPUT: return None
    headers, separator, body = value.partition("\r\n\r\n")
    if not separator: return None
    lines = headers.split("\r\n")
    match = _STATUS_RE.fullmatch(lines[0]) if lines else None
    if match is None: return None
    parsed: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, separator, field = line.partition(":")
        if not separator or not name: return None
        parsed.append((name, field.strip()))
    return int(match.group(1)), body.encode(), tuple(parsed)


def _ready(response) -> bool:
    return response is not None and response[0] == 200 and response[1] == b"ready" and _header(response[2], "cache-control") == "no-store"


def _hsts(response) -> bool:
    if response is None: return False
    value = _header(response[2], "strict-transport-security")
    if value is None: return False
    directives = [(separator, field.strip()) for directive in value.split(";") for name, separator, field in [directive.strip().partition("=")] if name.lower() == "max-age"]
    return len(directives) == 1 and directives[0][0] == "=" and directives[0][1].isdecimal() and int(directives[0][1]) > 0


def _header(headers: tuple[tuple[str, str], ...], name: str) -> str | None:
    values = [value for key, value in headers if key.lower() == name]
    return values[0] if len(values) == 1 else None


def _one_path(value: str) -> str | None:
    lines = value.splitlines()
    return lines[0] if len(lines) == 1 and lines[0].startswith("/") and "/../" not in lines[0] else None


def _check(name: str, passed: bool, success: str, failure: str) -> dict[str, object]:
    return {"schema_version": 1, "name": name, "status": "passed" if passed else "failed", "summary": success if passed else failure}


def _passed(checks: list[dict[str, object]]) -> bool:
    return all(item["status"] == "passed" for item in checks)


def _report(exit_status: int, release_id: str, expected: str | None, checks: list[dict[str, object]]) -> dict[str, object]:
    return {"schema_version": 1, "status": "ok" if exit_status == 0 else "failed", "exit_status": exit_status, "release_id": release_id, "expected_release_id": expected, "checks": checks, "next_action": None if exit_status == 0 else _NEXT_ACTION}


def _result(request: object, outcome: str, stage: str, report: dict[str, object]) -> _LegacyVerificationResult:
    return _LegacyVerificationResult(
        protocol_version=PROTOCOL_VERSION,
        operation=str(request.operation),
        operation_id=str(request.operation_id),
        outcome=outcome,
        stage=stage,
        changed_stages=(),
        lifecycle={},
        runtime_state={},
        verification=report,
        residue_paths=(),
        recovery_actions=(),
        warnings=(),
    )


def _host_failure(request: object, authority: str) -> _LegacyVerificationResult:
    if authority not in {"preflight", "unsupported"}:
        raise ValueError("invalid host authority failure")
    action = (
        "restore SSH administrator connectivity and required sudo access before retrying"
        if authority == "preflight"
        else "use a supported Ubuntu 26.04 amd64 host and correct the environment configuration"
    )
    return _LegacyVerificationResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="failed",
        stage="host-preflight",
        changed_stages=(),
        lifecycle={},
        runtime_state={"host_authority": authority},
        verification={},
        residue_paths=(),
        recovery_actions=(action,),
        warnings=(),
    )


def _release_selection_failure(request: object) -> _LegacyVerificationResult:
    return _LegacyVerificationResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="refused",
        stage="release-selection",
        changed_stages=(),
        lifecycle={},
        runtime_state={},
        verification={},
        residue_paths=(),
        recovery_actions=("inspect the managed lifecycle metadata and resolve the contradiction before retrying",),
        warnings=(),
    )


def _lock_failure(request: object, error: object) -> _LegacyVerificationResult:
    holder = None if error.holder is None else error.holder.to_mapping()
    runtime = {"lock_holder": holder} if holder is not None else {}
    return _LegacyVerificationResult(
        protocol_version=PROTOCOL_VERSION,
        operation=request.operation,
        operation_id=request.operation_id,
        outcome="failed",
        stage="lifecycle-lock",
        changed_stages=(),
        lifecycle={},
        runtime_state=runtime,
        verification={},
        residue_paths=(),
        recovery_actions=("wait for the recorded lifecycle operation to finish and retry",),
        warnings=(),
    )


__all__ = ["verify"]
