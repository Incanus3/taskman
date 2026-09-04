"""Read-only verification of a deployed Taskman host.

Evidence is deliberately bounded: checks consume fixed read-only commands and
produce fixed summaries. Raw journal, environment, socket, and HTTP output
never crosses the reporting boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import ipaddress
import json
import math
import os
import re
import select
import subprocess
import time
from pathlib import PurePosixPath
from typing import ClassVar

from .config import EnvironmentConfig
from .errors import ExitStatus, OpsError
from .host.facts import HostFacts, MINIMUM_DISK_BYTES, MINIMUM_MEMORY_BYTES
from .manifests import ArtifactManifest
from .releases.identifiers import validate_release_id
from .releases.records import LifecycleRecords, RemoteLifecycleStore
from .remote import CommandResult, Remote


_SYSTEMD_UNIT = "taskman.service"
_CADDY_UNIT = "caddy.service"
_EPMD_PORT = 4369
_CHECK_NAMES = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)
_LIFECYCLE_CHECK_NAMES = frozenset(_CHECK_NAMES[:5])
_READINESS_CHECK_NAMES = frozenset(_CHECK_NAMES[5:])
_FAILED_NEXT_ACTION = "inspect the fixed verification summaries and correct the reported host state before retrying"
_PUBLIC_RESPONSE_MAX_BYTES = 1_024
_PUBLIC_HEADER_MAX_BYTES = 8_192
_PUBLIC_OUTPUT_MAX_BYTES = _PUBLIC_HEADER_MAX_BYTES + _PUBLIC_RESPONSE_MAX_BYTES
_JOURNAL_QUERY = (
    "if ! journalctl --no-pager --output=cat --unit taskman.service --lines=100 "
    ">/dev/null 2>&1; then printf '%s\\n' unavailable; "
    "elif ! journalctl --no-pager --output=cat --unit taskman.service --lines=100 "
    "| grep --quiet '[^[:space:]]'; then printf '%s\\n' empty; "
    "elif journalctl --no-pager --output=cat --unit taskman.service --lines=100 "
    "| grep --extended-regexp --ignore-case --quiet "
    "'failed to start|boot failed|application.*(failed|error)|database.*(failed|error)'; "
    "then printf '%s\\n' startup-failure; else printf '%s\\n' clean; fi"
)
_HTTP_STATUS_RE = re.compile(r"HTTP/(?:1\.[01]|2|3) ([0-9]{3})(?: [^\r\n]+)?\Z")

# The deploy transaction cannot invoke the controller-side verifier without
# dropping its inherited remote flock. This already-locked form takes no
# lifecycle lock and emits the same exact VerificationReport mapping for the
# controller to parse below.
_LOCKED_VERIFICATION_BODY = r'''verify_candidate() {
  python3 - "$candidate" "$candidate_release_id" "$application_port" "$distribution_port" "$database_port" "$public_hostname" "$readiness_timeout" "$connection_timeout" <<'PY'
import ipaddress
import json
import math
import os
import re
import select
import subprocess
import sys
import time

candidate, release_id, application_port, distribution_port, database_port, public_hostname, readiness_text, connection_text = sys.argv[1:]
ports = tuple(int(value) for value in (application_port, distribution_port, database_port))
readiness_timeout = float(readiness_text)
connection_timeout = float(connection_text)
checks = []
next_action = "inspect the fixed verification summaries and correct the reported host state before retrying"
status_re = re.compile(r"HTTP/(?:1[.][01]|2|3) ([0-9]{3})(?: [^\r\n]+)?\Z")
failure_re = re.compile(r"failed to start|boot failed|application.*(failed|error)|database.*(failed|error)", re.I)

def run(argv, timeout, maximum=9216):
    if not math.isfinite(timeout) or timeout < 0.001:
        return 28, ""
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return 28, ""
    output = bytearray()
    try:
        assert process.stdout is not None
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill(); process.wait()
                return 28, ""
            readable, _, _ = select.select((process.stdout,), (), (), remaining)
            if not readable:
                process.kill(); process.wait()
                return 28, ""
            available = maximum - len(output)
            if available == 0:
                if os.read(process.stdout.fileno(), 1):
                    process.kill(); process.wait()
                    return 63, ""
                break
            chunk = os.read(process.stdout.fileno(), min(4096, available))
            if not chunk:
                break
            output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill(); process.wait()
            return 28, ""
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            return 28, ""
        return returncode, output.decode("utf-8", errors="replace")
    except OSError:
        if process.poll() is None:
            process.kill()
        process.wait()
        return 28, ""
    finally:
        if process.stdout is not None:
            process.stdout.close()

def add(name, passed, success, failure):
    checks.append({"schema_version": 1, "name": name, "status": "passed" if passed else "failed", "summary": success if passed else failure})

def emit(exit_status):
    print(json.dumps({
        "schema_version": 1,
        "status": "ok" if exit_status == 0 else "failed",
        "exit_status": exit_status,
        "release_id": release_id,
        "expected_release_id": release_id,
        "checks": checks,
        "next_action": None if exit_status == 0 else next_action,
    }, separators=(",", ":")))
    raise SystemExit(exit_status)

def absolute_path(value):
    lines = value.splitlines()
    if len(lines) != 1 or not lines[0].startswith("/"):
        return None
    parts = lines[0].split("/")[1:]
    return lines[0] if all(part not in {"", ".", ".."} for part in parts) else None

def parse_listener(value):
    if value.startswith("["):
        closing = value.find("]:")
        if closing < 0:
            return None
        address, port = value[1:closing], value[closing + 2:]
    else:
        address, separator, port = value.rpartition(":")
        if not separator:
            return None
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        return None
    if address != "*":
        try:
            ipaddress.ip_address(address)
        except ValueError:
            return None
    return address, int(port)

def listeners(value):
    parsed = []
    for line in value.splitlines():
        fields = line.split()
        if len(fields) != 5 or fields[0] != "LISTEN" or not fields[1].isdecimal() or not fields[2].isdecimal():
            return None
        listener = parse_listener(fields[3])
        if listener is None:
            return None
        parsed.append(listener)
    return tuple(parsed)

def loopback_only(values, port):
    matching = [address for address, candidate_port in values if candidate_port == port]
    if not matching:
        return False
    try:
        return all(ipaddress.ip_address(address).is_loopback for address in matching)
    except ValueError:
        return False

def parse_http(value):
    if len(value.encode("utf-8")) > 9216:
        return None
    header_block, separator, body = value.partition("\r\n\r\n")
    if not separator or len(header_block.encode("utf-8")) > 8192 or len(body.encode("utf-8")) > 1024:
        return None
    lines = header_block.split("\r\n")
    match = status_re.fullmatch(lines[0]) if lines else None
    if match is None:
        return None
    headers = []
    for line in lines[1:]:
        key, separator, field = line.partition(":")
        if not separator or not key:
            return None
        headers.append((key, field.strip()))
    return int(match.group(1)), body.encode(), tuple(headers)

def one_header(headers, name):
    values = [value for key, value in headers if key.lower() == name]
    return values[0] if len(values) == 1 else None

def ready(response):
    return response is not None and response[0] == 200 and response[1] == b"ready" and one_header(response[2], "cache-control") == "no-store"

def hsts(response):
    if response is None:
        return False
    value = one_header(response[2], "strict-transport-security")
    if value is None:
        return False
    directives = []
    for directive in value.split(";"):
        name, separator, field = directive.strip().partition("=")
        if name.lower() == "max-age":
            directives.append((separator, field.strip()))
    return len(directives) == 1 and directives[0][0] == "=" and directives[0][1].isdecimal() and int(directives[0][1]) > 0

service_code, service_output = run(("systemctl", "show", "taskman.service", "--property=ActiveState", "--property=MainPID", "--value"), connection_timeout)
service_lines = service_output.splitlines()
service_ok = service_code == 0 and len(service_lines) == 2 and service_lines[0] == "active" and service_lines[1].isdecimal() and int(service_lines[1]) > 0
main_pid = int(service_lines[1]) if service_ok else 0
add("taskman-service", service_ok, "taskman.service is active with a positive MainPID", "taskman.service is not active with a usable MainPID")

executable_ok = False
if service_ok:
    executable_code, executable_output = run(("readlink", "-f", f"/proc/{main_pid}/exe"), connection_timeout)
    executable = absolute_path(executable_output) if executable_code == 0 else None
    executable_ok = executable is not None and executable.startswith(candidate + "/")
add("release-identity", executable_ok, "systemd MainPID executable is under the selected release", "systemd MainPID executable does not match the selected release")

caddy_code, caddy_output = run(("systemctl", "is-active", "--quiet", "caddy.service"), connection_timeout)
caddy_ok = caddy_code == 0 and caddy_output in {"", "active\n"}
add("caddy-service", caddy_ok, "caddy.service is active", "caddy.service is not active")

socket_code, socket_output = run(("ss", "-H", "-ltn"), connection_timeout)
socket_values = listeners(socket_output) if socket_code == 0 else None
topology_ok = (
    socket_values is not None
    and all(loopback_only(socket_values, port) for port in ports)
    and not any(port == 4369 for _address, port in socket_values)
)
add("listener-topology", topology_ok, "Taskman, distribution, and PostgreSQL listeners have the required topology", "listener topology is missing, public, malformed, or ambiguous")

journal_code, journal_output = run(("journalctl", "--no-pager", "--output=cat", "--unit", "taskman.service", "--lines=100"), connection_timeout)
if journal_code != 0:
    journal_state = "unavailable"
elif not journal_output.strip():
    journal_state = "empty"
elif failure_re.search(journal_output):
    journal_state = "startup-failure"
else:
    journal_state = "clean"
journal_summaries = {
    "clean": "recent startup journal evidence is clean",
    "startup-failure": "recent startup journal evidence indicates a startup failure",
    "empty": "recent startup journal evidence is empty",
    "unavailable": "recent startup journal evidence is unavailable",
}
add("startup-journal", journal_state == "clean", journal_summaries[journal_state], journal_summaries[journal_state])

if any(check["status"] != "passed" for check in checks):
    emit(8)

deadline = time.monotonic() + readiness_timeout
local_ok = False
attempt_limit = max(1, math.ceil(readiness_timeout) + 1)
for attempt in range(attempt_limit):
    remaining = deadline - time.monotonic()
    if remaining < 0.001:
        break
    probe_timeout = min(connection_timeout, remaining)
    timeout_argument = f"{math.floor(probe_timeout * 1000) / 1000:.3f}".rstrip("0").rstrip(".")
    local_code, local_output = run((
        "curl", "--silent", "--show-error", "--max-time", timeout_argument,
        "--dump-header", "-", "--output", "-",
        f"http://127.0.0.1:{application_port}/healthz",
    ), probe_timeout)
    if local_code == 0 and ready(parse_http(local_output)) and time.monotonic() <= deadline:
        local_ok = True
        break
    if attempt + 1 >= attempt_limit:
        break
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        break
    time.sleep(min(1.0, remaining))
add("local-readiness", local_ok, "loopback health endpoint returned exact ready response", "loopback health endpoint did not return exact ready response before its bounded deadline")
if not local_ok:
    emit(9)

remaining = deadline - time.monotonic()
public_response = None
if remaining >= 0.001:
    public_timeout = min(connection_timeout, remaining)
    timeout_argument = f"{math.floor(public_timeout * 1000) / 1000:.3f}".rstrip("0").rstrip(".")
    public_code, public_output = run((
        "curl", "--disable", "--silent", "--show-error", "--max-time", timeout_argument,
        "--max-filesize", "1024", "--range", "0-1023", "--dump-header", "-", "--output", "-", "--",
        f"https://{public_hostname}/healthz",
    ), public_timeout)
    if public_code == 0:
        public_response = parse_http(public_output)
public_ok = ready(public_response)
add("public-readiness", public_ok, "public HTTPS health endpoint returned exact ready response", "public HTTPS health endpoint did not return exact ready response")
hsts_ok = hsts(public_response)
add("public-hsts", hsts_ok, "public HTTPS response includes HSTS", "public HTTPS response does not include valid HSTS")
emit(0 if public_ok and hsts_ok else 9)
PY
}'''


class CheckStatus(str, Enum):
    """One check's stable, reportable outcome."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class HTTPResponse:
    """One bounded public HTTPS response, without transport detail."""

    status_code: int
    body: bytes
    headers: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("HTTP status code must be an integer between 100 and 599")
        if not isinstance(self.body, bytes):
            raise TypeError("HTTP response body must be bytes")
        if not isinstance(self.headers, tuple) or not all(
            isinstance(field, tuple)
            and len(field) == 2
            and isinstance(field[0], str)
            and isinstance(field[1], str)
            for field in self.headers
        ):
            raise TypeError("HTTP response headers must be an ordered tuple of string fields")


@dataclass(frozen=True)
class VerificationCheck:
    """A secret-free, versioned individual verification fact."""

    schema_version: ClassVar[int] = 1
    name: str
    status: CheckStatus
    summary: str

    def __post_init__(self) -> None:
        if self.name not in _CHECK_NAMES:
            raise ValueError("invalid verification check name")
        if not isinstance(self.status, CheckStatus):
            raise TypeError("verification check status must be typed")
        if not isinstance(self.summary, str) or not self.summary:
            raise ValueError("verification check summary must be a non-empty string")

    def to_mapping(self) -> dict[str, str | int]:
        return {"schema_version": self.schema_version, "name": self.name, "status": self.status.value, "summary": self.summary}

    @classmethod
    def from_mapping(cls, value: object) -> VerificationCheck:
        """Parse one exact check emitted by either verifier form."""

        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "name", "status", "summary"}
            or value.get("schema_version") != cls.schema_version
        ):
            raise ValueError("verification check must use the exact schema")
        try:
            status = CheckStatus(value["status"])
        except (TypeError, ValueError):
            raise ValueError("verification check status is invalid") from None
        return cls(value["name"], status, value["summary"])  # type: ignore[arg-type]


@dataclass(frozen=True)
class VerificationReport:
    """The complete, secret-free outcome of one read-only verification."""

    schema_version: ClassVar[int] = 1
    exit_status: ExitStatus
    release_id: str | None
    expected_release_id: str | None
    checks: tuple[VerificationCheck, ...]
    next_action: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.exit_status, ExitStatus):
            raise TypeError("verification report exit status must be typed")
        for release_id in (self.release_id, self.expected_release_id):
            if release_id is not None:
                validate_release_id(release_id)
        if not isinstance(self.checks, tuple) or not all(isinstance(check, VerificationCheck) for check in self.checks):
            raise TypeError("verification report checks must be typed")
        names = tuple(check.name for check in self.checks)
        if not names or names != _CHECK_NAMES[: len(names)]:
            raise ValueError("verification report checks must be a non-empty ordered unique prefix")
        if any(check.status is CheckStatus.SKIPPED for check in self.checks):
            raise ValueError("verification report checks must not skip required evidence")
        if self.exit_status is ExitStatus.OK:
            if names != _CHECK_NAMES or any(check.status is not CheckStatus.PASSED for check in self.checks):
                raise ValueError("successful report requires the complete required check set to pass")
            if self.next_action is not None:
                raise ValueError("successful report must not include a next action")
            return
        if self.exit_status not in {ExitStatus.RELEASE, ExitStatus.READINESS}:
            raise ValueError("verification report status must be release, readiness, or ok")
        if self.next_action != _FAILED_NEXT_ACTION:
            raise ValueError("failed report requires the fixed next action")
        failed_names = {check.name for check in self.checks if check.status is CheckStatus.FAILED}
        if not failed_names:
            raise ValueError("failed report requires a failed check")
        if self.exit_status is ExitStatus.RELEASE:
            if len(self.checks) > len(_LIFECYCLE_CHECK_NAMES) or not failed_names <= _LIFECYCLE_CHECK_NAMES:
                raise ValueError("release status must match a lifecycle failure")
        elif (
            len(self.checks) < len(_LIFECYCLE_CHECK_NAMES) + 1
            or any(check.status is not CheckStatus.PASSED for check in self.checks[: len(_LIFECYCLE_CHECK_NAMES)])
            or not failed_names <= _READINESS_CHECK_NAMES
        ):
            raise ValueError("readiness status must match a readiness failure")

    @property
    def successful(self) -> bool:
        return self.exit_status is ExitStatus.OK

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": "ok" if self.successful else "failed",
            "exit_status": self.exit_status.value,
            "release_id": self.release_id,
            "expected_release_id": self.expected_release_id,
            "checks": [check.to_mapping() for check in self.checks],
            "next_action": self.next_action,
        }

    @classmethod
    def from_mapping(cls, value: object) -> VerificationReport:
        """Parse exact secret-free evidence from an already-locked verifier."""

        expected = {
            "schema_version",
            "status",
            "exit_status",
            "release_id",
            "expected_release_id",
            "checks",
            "next_action",
        }
        if not isinstance(value, Mapping) or set(value) != expected or value.get("schema_version") != cls.schema_version:
            raise ValueError("verification report must use the exact schema")
        if type(value.get("exit_status")) is not int:
            raise TypeError("verification report exit status must be an integer")
        try:
            exit_status = ExitStatus(value["exit_status"])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("verification report exit status is invalid") from None
        expected_status = "ok" if exit_status is ExitStatus.OK else "failed"
        if value.get("status") != expected_status:
            raise ValueError("verification report status conflicts with its exit status")
        checks_value = value.get("checks")
        if not isinstance(checks_value, list):
            raise TypeError("verification report checks must be a list")
        checks = tuple(VerificationCheck.from_mapping(check) for check in checks_value)
        return cls(
            exit_status,
            value.get("release_id"),  # type: ignore[arg-type]
            value.get("expected_release_id"),  # type: ignore[arg-type]
            checks,
            value.get("next_action"),  # type: ignore[arg-type]
        )

    def human(self) -> str:
        """Render exactly the facts exposed by :meth:`to_mapping`."""

        from .output import redact

        return json.dumps(redact(self.to_mapping()), ensure_ascii=False, sort_keys=True)

    def to_json(self) -> str:
        """Render the same report facts as compact, redacted JSON."""

        from .output import redact

        return json.dumps(redact(self.to_mapping()), ensure_ascii=False)


PublicHTTPClient = Callable[[str, float], HTTPResponse]
PublicHTTPRunner = Callable[[tuple[str, ...], float], CommandResult]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


def verify_installation(
    remote: Remote,
    config: EnvironmentConfig,
    expected_release_id: str | None = None,
    *,
    lifecycle_store: RemoteLifecycleStore | None = None,
    public_client: PublicHTTPClient | None = None,
    public_runner: PublicHTTPRunner | None = None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    max_attempts: int | None = None,
) -> VerificationReport:
    """Verify one installed release without mutating the remote host.

    Verification observations return in a report. Configuration, preflight,
    lifecycle-lock, and lifecycle-state errors retain their established
    ``OpsError`` semantics rather than being collapsed into readiness errors.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("verification requires a validated environment configuration")
    if expected_release_id is not None:
        validate_release_id(expected_release_id)
    if max_attempts is not None and (type(max_attempts) is not int or max_attempts < 1):
        raise ValueError("verification attempts must be a positive integer")
    store = lifecycle_store or _lifecycle_store(remote, config)
    if not isinstance(store, RemoteLifecycleStore):
        raise TypeError("verification requires a remote lifecycle store")
    if store.remote is not remote:
        raise ValueError("verification remote does not match lifecycle store")
    _validate_store(store, config)
    _validate_existing_host(remote.facts(), config)

    records, snapshot = store.read(operation="verify", lock_timeout_seconds=5)
    release_id, release_path = _selected_release(records, snapshot, store, expected_release_id)
    checks: list[VerificationCheck] = []

    service_active, main_pid = _service_state(remote)
    checks.append(_check("taskman-service", service_active, "taskman.service is active with a positive MainPID", "taskman.service is not active with a usable MainPID"))
    executable_matches = service_active and _main_pid_matches_release(remote, main_pid, release_path)
    checks.append(_check("release-identity", executable_matches, "systemd MainPID executable is under the selected release", "systemd MainPID executable does not match the selected release"))
    caddy_active = _unit_active(remote, _CADDY_UNIT)
    checks.append(_check("caddy-service", caddy_active, "caddy.service is active", "caddy.service is not active"))
    topology_ok = _listener_topology(remote, config)
    checks.append(_check("listener-topology", topology_ok, "Taskman, distribution, and PostgreSQL listeners have the required topology", "listener topology is missing, public, malformed, or ambiguous"))
    journal_status = _startup_journal_status(remote)
    checks.append(_journal_check(journal_status))
    if not all(check.status is CheckStatus.PASSED for check in checks):
        return _report(ExitStatus.RELEASE, release_id, expected_release_id, checks)

    clock = clock or time.monotonic
    readiness_deadline = clock() + config.readiness_timeout
    local_ok = _poll_local_ready(
        remote,
        config,
        deadline=readiness_deadline,
        clock=clock,
        sleeper=sleeper or time.sleep,
        max_attempts=max_attempts,
    )
    checks.append(_check("local-readiness", local_ok, "loopback health endpoint returned exact ready response", "loopback health endpoint did not return exact ready response before its bounded deadline"))
    if not local_ok:
        return _report(ExitStatus.READINESS, release_id, expected_release_id, checks)

    remaining = readiness_deadline - clock()
    try:
        public = (
            public_client(f"https://{config.public_hostname}/healthz", min(config.connection_timeout, remaining))
            if public_client is not None and remaining > 0
            else _public_https_request(
                f"https://{config.public_hostname}/healthz",
                min(config.connection_timeout, remaining),
                runner=public_runner,
            )
            if remaining > 0
            else HTTPResponse(599, b"", ())
        )
    except Exception:
        public = HTTPResponse(599, b"", ())
    public_ok = _ready_response(public.status_code, public.body, public.headers)
    checks.append(_check("public-readiness", public_ok, "public HTTPS health endpoint returned exact ready response", "public HTTPS health endpoint did not return exact ready response"))
    hsts_ok = _hsts_present(public.headers)
    checks.append(_check("public-hsts", hsts_ok, "public HTTPS response includes HSTS", "public HTTPS response does not include valid HSTS"))
    return _report(ExitStatus.OK if public_ok and hsts_ok else ExitStatus.READINESS, release_id, expected_release_id, checks)


def _lifecycle_store(remote: Remote, config: EnvironmentConfig) -> RemoteLifecycleStore:
    return RemoteLifecycleStore(remote, config.deployment_root, config.managed_root, config.release_root, config.backup_root, application_port=config.application_port, distribution_port=config.distribution_port, database_port=config.database_port)


def _validate_store(store: RemoteLifecycleStore, config: EnvironmentConfig) -> None:
    if (store.deployment_root, store.managed_root, store.release_root, store.backup_root, store.application_port, store.distribution_port, store.database_port) != (config.deployment_root, config.managed_root, config.release_root, config.backup_root, config.application_port, config.distribution_port, config.database_port):
        raise ValueError("verification lifecycle store does not match environment configuration")


def _validate_existing_host(facts: HostFacts, config: EnvironmentConfig) -> None:
    """Apply preflight facts without classing expected installed state as conflict."""

    if not isinstance(facts, HostFacts):
        raise TypeError("verification remote facts must be typed")
    if facts.failed_checks or not facts.sudo_available or facts.postgres_sudo_available is not True:
        raise _preflight("required host fact collection failed")
    if facts.os_id != "ubuntu" or facts.ubuntu_release != "26.04":
        raise _unsupported("host must run Ubuntu 26.04")
    if facts.architecture != "amd64" or not facts.systemd:
        raise _unsupported("host must use amd64 with systemd as PID 1")
    if facts.memory_bytes < MINIMUM_MEMORY_BYTES or min(facts.available_disk_bytes, facts.backup_available_disk_bytes) < MINIMUM_DISK_BYTES:
        raise _unsupported("host does not meet the minimum capacity requirement")
    expected_dns = tuple(sorted({config.public_ipv4, *(() if config.public_ipv6 is None else (config.public_ipv6,))}))
    if facts.dns_addresses != expected_dns:
        raise _unsupported("public DNS does not resolve directly to the configured VPS address")
    if facts.active_ssh_port != config.ssh_port:
        raise _preflight("active SSH connection port does not match configuration")


def _selected_release(records: LifecycleRecords, snapshot: Mapping[str, object], store: RemoteLifecycleStore, expected_release_id: str | None) -> tuple[str, PurePosixPath]:
    release_id = records.current_release_id
    if release_id is None:
        raise _safety("no current release is recorded")
    if expected_release_id is not None and expected_release_id != release_id:
        raise _safety("current release does not match the expected release")
    adoption_paths = {adoption.release_id: adoption.release_path for adoption in records.adoptions}
    release_path = adoption_paths.get(release_id, store.release_root / release_id)
    if snapshot.get("current_target") != release_path.as_posix():
        raise _safety("current release path does not match lifecycle records")
    if release_id not in adoption_paths:
        manifests = snapshot.get("manifests")
        try:
            manifest = ArtifactManifest.from_mapping(manifests.get(release_id) if isinstance(manifests, Mapping) else None)
        except ValueError:
            raise _safety("selected release manifest is invalid") from None
        if manifest.release_id != release_id:
            raise _safety("selected release manifest does not match lifecycle records")
    return release_id, release_path


def _service_state(remote: Remote) -> tuple[bool, int]:
    result = remote.run(("systemctl", "show", _SYSTEMD_UNIT, "--property=ActiveState", "--property=MainPID", "--value"), sudo=False, stdin=None, sensitive=False)
    if not result.succeeded or not isinstance(result.stdout, str):
        return False, 0
    lines = result.stdout.splitlines()
    if len(lines) != 2 or lines[0] != "active" or not lines[1].isdecimal():
        return False, 0
    pid = int(lines[1])
    return pid > 0, pid


def _main_pid_matches_release(remote: Remote, pid: int, release_path: PurePosixPath) -> bool:
    result = remote.run(("readlink", "-f", f"/proc/{pid}/exe"), sudo=False, stdin=None, sensitive=False)
    if not result.succeeded:
        return False
    executable = _one_absolute_path(result.stdout)
    if executable is None:
        return False
    try:
        executable.relative_to(release_path)
    except ValueError:
        return False
    return executable != release_path


def _unit_active(remote: Remote, unit: str) -> bool:
    result = remote.run(("systemctl", "is-active", "--quiet", unit), sudo=False, stdin=None, sensitive=False)
    return result.succeeded and result.stdout in {"", "active\n"}


def _listener_topology(remote: Remote, config: EnvironmentConfig) -> bool:
    result = remote.run(("ss", "-H", "-ltn"), sudo=False, stdin=None, sensitive=False)
    listeners = _parse_listeners(result.stdout) if result.succeeded else None
    return listeners is not None and _loopback_only(listeners, config.application_port) and _loopback_only(listeners, config.distribution_port) and _loopback_only(listeners, config.database_port) and not any(port == _EPMD_PORT for _address, port in listeners)


def _parse_listeners(value: str) -> tuple[tuple[str, int], ...] | None:
    if not isinstance(value, str):
        return None
    listeners: list[tuple[str, int]] = []
    for line in value.splitlines():
        fields = line.split()
        if len(fields) != 5 or fields[0] != "LISTEN" or not fields[1].isdecimal() or not fields[2].isdecimal():
            return None
        parsed = _listener_address(fields[3])
        if parsed is None:
            return None
        listeners.append(parsed)
    return tuple(listeners)


def _listener_address(value: str) -> tuple[str, int] | None:
    if value.startswith("["):
        closing = value.find("]:")
        if closing < 0:
            return None
        address, port = value[1:closing], value[closing + 2 :]
    else:
        address, separator, port = value.rpartition(":")
        if not separator:
            return None
    if not port.isdecimal() or not 1 <= int(port) <= 65_535:
        return None
    if address != "*":
        try:
            ipaddress.ip_address(address)
        except ValueError:
            return None
    return address, int(port)


def _loopback_only(listeners: Sequence[tuple[str, int]], port: int) -> bool:
    matching = [address for address, candidate_port in listeners if candidate_port == port]
    if not matching:
        return False
    try:
        return all(ipaddress.ip_address(address).is_loopback for address in matching)
    except ValueError:
        return False


def _startup_journal_status(remote: Remote) -> str:
    result = remote.run(("sh", "-ceu", _JOURNAL_QUERY, "taskman-verify-journal"), sudo=True, stdin=None, sensitive=False)
    if not result.succeeded or not isinstance(result.stdout, str):
        return "unavailable"
    return result.stdout if result.stdout in {"clean\n", "startup-failure\n", "empty\n", "unavailable\n"} else "unavailable"


def _journal_check(status: str) -> VerificationCheck:
    summaries = {
        "clean\n": (True, "recent startup journal evidence is clean"),
        "startup-failure\n": (False, "recent startup journal evidence indicates a startup failure"),
        "empty\n": (False, "recent startup journal evidence is empty"),
        "unavailable\n": (False, "recent startup journal evidence is unavailable"),
    }
    passed, summary = summaries.get(status, (False, "recent startup journal evidence is unavailable"))
    return _check("startup-journal", passed, summary, summary)


def _poll_local_ready(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    deadline: float,
    clock: Clock,
    sleeper: Sleeper,
    max_attempts: int | None,
) -> bool:
    attempt_limit = max_attempts if max_attempts is not None else max(1, math.ceil(config.readiness_timeout) + 1)
    for attempt in range(attempt_limit):
        remaining = deadline - clock()
        if remaining < 0.001:
            break
        result = remote.run(
            (
                "curl",
                "--silent",
                "--show-error",
                "--max-time",
                _timeout_argument(min(config.connection_timeout, remaining)),
                "--dump-header",
                "-",
                "--output",
                "-",
                f"http://127.0.0.1:{config.application_port}/healthz",
            ),
            sudo=False,
            stdin=None,
            sensitive=False,
        )
        if result.succeeded and _local_ready_response(result.stdout) and clock() <= deadline:
            return True
        if attempt + 1 >= attempt_limit:
            break
        before_sleep = clock()
        remaining = deadline - before_sleep
        if remaining <= 0:
            break
        sleeper(min(1.0, remaining))
        if clock() <= before_sleep:
            break
    return False


def _local_ready_response(value: str) -> bool:
    if not isinstance(value, str):
        return False
    header_block, separator, body = value.partition("\r\n\r\n")
    if not separator:
        return False
    header_lines = header_block.split("\r\n")
    match = _HTTP_STATUS_RE.fullmatch(header_lines[0]) if header_lines else None
    if match is None or match.group(1) != "200":
        return False
    headers = _headers(header_lines[1:])
    return headers is not None and _ready_response(200, body.encode("utf-8"), headers)


def _headers(lines: Sequence[str]) -> tuple[tuple[str, str], ...] | None:
    result: list[tuple[str, str]] = []
    for line in lines:
        key, separator, value = line.partition(":")
        if not separator or not key:
            return None
        result.append((key, value.strip()))
    return tuple(result)


def _ready_response(status_code: int, body: bytes, headers: tuple[tuple[str, str], ...]) -> bool:
    return status_code == 200 and body == b"ready" and _header(headers, "cache-control") == "no-store"


def _hsts_present(headers: tuple[tuple[str, str], ...]) -> bool:
    value = _header(headers, "strict-transport-security")
    if value is None:
        return False
    max_age_directives = [
        (separator, directive_value.strip())
        for directive in value.split(";")
        for directive_name, separator, directive_value in [directive.strip().partition("=")]
        if directive_name.lower() == "max-age"
    ]
    return (
        len(max_age_directives) == 1
        and max_age_directives[0][0] == "="
        and max_age_directives[0][1].isdecimal()
        and int(max_age_directives[0][1]) > 0
    )


def _header(headers: tuple[tuple[str, str], ...], name: str) -> str | None:
    values = [value for key, value in headers if key.lower() == name]
    return values[0] if len(values) == 1 else None


def _public_https_request(url: str, timeout: float, *, runner: PublicHTTPRunner | None = None) -> HTTPResponse:
    """Issue one normally TLS/hostname-validating bounded HTTPS request."""

    if timeout <= 0:
        return HTTPResponse(599, b"", ())
    argv = (
        "curl",
        "--disable",
        "--silent",
        "--show-error",
        "--max-time",
        _timeout_argument(timeout),
        "--max-filesize",
        str(_PUBLIC_RESPONSE_MAX_BYTES),
        "--range",
        f"0-{_PUBLIC_RESPONSE_MAX_BYTES - 1}",
        "--dump-header",
        "-",
        "--output",
        "-",
        "--",
        url,
    )
    try:
        result = (runner or _run_public_curl)(argv, timeout)
    except Exception:
        return HTTPResponse(599, b"", ())
    if not result.succeeded or not isinstance(result.stdout, str):
        return HTTPResponse(599, b"", ())
    return _parse_public_response(result.stdout)


def _run_public_curl(argv: tuple[str, ...], timeout: float) -> CommandResult:
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return CommandResult(28, "")
    if process.stdout is None:  # pragma: no cover - guaranteed by stdout=PIPE
        _stop_public_process(process)
        return CommandResult(28, "")

    output = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_public_process(process)
                return CommandResult(28, "")
            readable, _writable, _exceptional = select.select((process.stdout,), (), (), remaining)
            if not readable:
                _stop_public_process(process)
                return CommandResult(28, "")
            available = _PUBLIC_OUTPUT_MAX_BYTES - len(output)
            if available == 0:
                extra = os.read(process.stdout.fileno(), 1)
                if extra:
                    _stop_public_process(process)
                    return CommandResult(63, "")
                break
            chunk = os.read(process.stdout.fileno(), min(4_096, available))
            if not chunk:
                break
            output.extend(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_public_process(process)
            return CommandResult(28, "")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _stop_public_process(process)
            return CommandResult(28, "")
        return CommandResult(returncode, output.decode("utf-8", errors="replace"))
    except OSError:
        _stop_public_process(process)
        return CommandResult(28, "")
    finally:
        process.stdout.close()


def _stop_public_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()


def _parse_public_response(value: str) -> HTTPResponse:
    if len(value.encode("utf-8")) > _PUBLIC_OUTPUT_MAX_BYTES:
        return HTTPResponse(599, b"", ())
    header_block, separator, body = value.partition("\r\n\r\n")
    if not separator:
        return HTTPResponse(599, b"", ())
    header_lines = header_block.split("\r\n")
    match = _HTTP_STATUS_RE.fullmatch(header_lines[0]) if header_lines else None
    headers = _headers(header_lines[1:])
    if match is None or headers is None or len(body.encode("utf-8")) > _PUBLIC_RESPONSE_MAX_BYTES:
        return HTTPResponse(599, b"", ())
    return HTTPResponse(int(match.group(1)), body.encode("utf-8"), headers)


def _timeout_argument(value: float) -> str:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("verification timeout must be a finite positive value")
    milliseconds = math.floor(value * 1_000)
    if milliseconds < 1:
        raise ValueError("verification timeout must be at least one millisecond")
    return str(milliseconds // 1_000) if milliseconds % 1_000 == 0 else f"{milliseconds / 1_000:.3f}"


def _one_absolute_path(value: str) -> PurePosixPath | None:
    if not isinstance(value, str):
        return None
    lines = value.splitlines()
    if len(lines) != 1 or not lines[0].startswith("/"):
        return None
    path = PurePosixPath(lines[0])
    return None if any(part in {"", ".", ".."} for part in path.parts[1:]) else path


def _check(name: str, passed: bool, success: str, failure: str) -> VerificationCheck:
    return VerificationCheck(name, CheckStatus.PASSED if passed else CheckStatus.FAILED, success if passed else failure)


def _report(status: ExitStatus, release_id: str, expected_release_id: str | None, checks: Sequence[VerificationCheck]) -> VerificationReport:
    next_action = None if status is ExitStatus.OK else "inspect the fixed verification summaries and correct the reported host state before retrying"
    return VerificationReport(status, release_id, expected_release_id, tuple(checks), next_action)


def _unsupported(message: str) -> OpsError:
    return OpsError(ExitStatus.INVALID, "host-preflight", message, changed=False, next_action="use a supported Ubuntu 26.04 amd64 host and correct the environment configuration")


def _preflight(message: str) -> OpsError:
    return OpsError(ExitStatus.REMOTE_PREFLIGHT, "host-preflight", message, changed=False, next_action="restore SSH administrator connectivity and required sudo access before retrying")


def _safety(message: str) -> OpsError:
    return OpsError(ExitStatus.SAFETY, "verification", message, changed=False, next_action="inspect the managed lifecycle metadata and resolve the contradiction before retrying")


__all__ = [
    "CheckStatus",
    "HTTPResponse",
    "PublicHTTPClient",
    "PublicHTTPRunner",
    "VerificationCheck",
    "VerificationReport",
    "verify_installation",
]
