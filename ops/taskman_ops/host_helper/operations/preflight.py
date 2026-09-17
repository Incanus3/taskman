"""Finite read-only restore and supplied-credential admission procedures."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import stat
from typing import BinaryIO

from taskman_ops.host_protocol import HostRequest, HostResult, MAX_INPUT_BYTES

from ..commands import CommandError, run_command
from ..credentials import validate_credentials
from ..database import database_mapping
from ..lock import LifecycleLockContention, lifecycle_lock
from ..paths import ManagedPaths, PathAuthorityError
from ..restore_database import RestoreDatabaseError, restore_database_names


_PARAMETERS = frozenset({"mode", "credentials_path", "database"})
_MODES = frozenset({"inspection", "capacity"})
_PGPASS_PATH = Path("/etc/taskman/pgpass")
_PGPASS_FIELDS = 5
_ROLE_QUERY = (
    "SELECT 1 FROM pg_roles WHERE rolname = :'role' AND rolcanlogin"
)
_SIZE_QUERY = (
    "SELECT datname, pg_database_size(datname) FROM pg_database "
    "WHERE datname IN (:'canonical', :'temporary', :'retired') ORDER BY datname"
)
_COMMAND_TIMEOUT_SECONDS = 60.0
_MAX_SIGNED_64 = (1 << 63) - 1
_IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z", re.ASCII)


def restore_preflight(request: HostRequest) -> HostResult:
    """Observe the exact restore admission projection under the lifecycle lock."""

    try:
        if request.operation != "restore_preflight" or request.expected_state:
            raise ValueError
        if set(request.parameters) != _PARAMETERS:
            raise ValueError
        mode = request.parameters["mode"]
        if type(mode) is not str or mode not in _MODES:
            raise ValueError
        if request.parameters["credentials_path"] != _PGPASS_PATH.as_posix():
            raise ValueError
        database = _validated_database(request.parameters["database"])
        paths = ManagedPaths.from_mapping(request.paths)
        with lifecycle_lock(paths, timeout_seconds=5.0):
            validate_credentials(_PGPASS_PATH)
            state = observe_restore_preflight(database, _PGPASS_PATH, mode)
        _validate_state(state, mode)
    except LifecycleLockContention:
        return HostResult.for_request(request, "retryable", "lifecycle lock unavailable", {})
    except (CommandError, OSError, PathAuthorityError, RestoreDatabaseError, TypeError, ValueError):
        return HostResult.for_request(request, "refused", "restore preflight failed", {})
    return HostResult.for_request(request, "succeeded", "restore preflight completed", state)


def observe_restore_preflight(
    database: Mapping[str, object], credentials: Path, mode: str
) -> dict[str, object]:
    """Use native bounded calls for inspection and optional capacity facts."""

    names = restore_database_names(database)
    role = str(database["role"])
    role_result = _admin_query(database, _ROLE_QUERY, variables=("role", role))
    if role_result.strip() != b"1":
        raise RestoreDatabaseError("restore role login authority is invalid")
    if mode == "inspection":
        return {"mode": "inspection"}

    data_directory = _admin_query(database, "SHOW data_directory").decode("utf-8", "strict").strip()
    if not data_directory.startswith("/"):
        raise RestoreDatabaseError("PostgreSQL data directory is invalid")
    filesystem = os.statvfs(data_directory)
    available = filesystem.f_bavail * filesystem.f_frsize
    sizes: dict[str, int | None] = {name: None for name in names}
    variables = tuple(item for pair in names.items() for item in pair)
    output = _admin_query(database, _SIZE_QUERY, variables=variables)
    role_by_name = {name: role_name for role_name, name in names.items()}
    for line in output.decode("ascii", "strict").splitlines():
        name, separator, raw_size = line.partition("\t")
        if separator != "\t" or name not in role_by_name or sizes[role_by_name[name]] is not None:
            raise RestoreDatabaseError("restore database size result is invalid")
        sizes[role_by_name[name]] = int(raw_size)
    state: dict[str, object] = {
        "mode": "capacity",
        "database_available_bytes": available,
        "database_size_bytes": sizes,
    }
    _validate_state(state, "capacity")
    return state


def _admin_query(
    database: Mapping[str, object], command: str, *, variables: tuple[str, ...] = ()
) -> bytes:
    argv = [
        "runuser", "-u", "postgres", "--", "psql", "--no-psqlrc",
        "--tuples-only", "--no-align", "--field-separator=\t",
        "--host", "/var/run/postgresql",
        "--port", str(database["port"]), "--username", "postgres",
        "--dbname", "postgres", "--no-password",
    ]
    argv.append("--set=ON_ERROR_STOP=1")
    for index in range(0, len(variables), 2):
        argv.append(f"--set={variables[index]}={variables[index + 1]}")
    argv.append("--file=-")
    return run_command(
        tuple(argv), stdin=f"{command}\n".encode("utf-8"), timeout_seconds=_COMMAND_TIMEOUT_SECONDS
    ).stdout


def provision_pgpass_authority(arguments: tuple[str, ...], source: BinaryIO) -> int:
    """Prove one exact supplied pgpass record and expose only a status code."""

    try:
        if len(arguments) != 5:
            return 2
        host, port_text, role, database_name, database_state = arguments
        if (
            host not in {"127.0.0.1", "::1"}
            or not port_text.isdecimal()
            or str(int(port_text)) != port_text
            or not 1 <= int(port_text) <= 65_535
            or _IDENTIFIER_RE.fullmatch(role) is None
            or _IDENTIFIER_RE.fullmatch(database_name) is None
            or database_state not in {"ready", "absent"}
        ):
            return 2
        raw = source.read(MAX_INPUT_BYTES + 1)
        if not raw or len(raw) > MAX_INPUT_BYTES:
            return 2
        fields = _parse_pgpass(raw)
        if fields[:4] != (host, port_text, database_name, role):
            return 10
        authenticate_pgpass(
            _PGPASS_PATH, raw, host=host, port=port_text,
            role=role, database=database_name, password=fields[4],
            database_state=database_state,
        )
        return 0
    except (CommandError, OSError, UnicodeError, ValueError):
        return 10


def authenticate_pgpass(
    path: Path, raw: bytes, *, host: str, port: str,
    role: str, database: str, password: str,
    database_state: str,
) -> None:
    """Validate retained bytes or authenticate absent supplied bytes without writes."""

    try:
        details = path.lstat()
    except FileNotFoundError:
        if database_state == "absent":
            return
        argv = (
            "psql", "--no-psqlrc", "--quiet", "--host", host, "--port", port,
            "--username", role, "--dbname", database, "--no-password",
            "--command", "SELECT 1", "--output", "/dev/null",
        )
        run_command(
            argv, env={"PGPASSWORD": password},
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS, output_limit=1024,
        )
        return
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or path.read_bytes() != raw
    ):
        raise ValueError("existing pgpass authority is invalid")
    if database_state == "absent":
        return
    run_command(
        (
            "psql", "--no-psqlrc", "--quiet", "--host", host, "--port", port,
            "--username", role, "--dbname", database, "--no-password",
            "--command", "SELECT 1", "--output", "/dev/null",
        ),
        env={"PGPASSFILE": path.as_posix()}, timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
        output_limit=1024,
    )


def _parse_pgpass(raw: bytes) -> tuple[str, str, str, str, str]:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1 or b"\x00" in raw:
        raise ValueError("pgpass record is invalid")
    text = raw[:-1].decode("utf-8", "strict")
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in text:
        if escaped:
            if character not in {":", "\\"}:
                raise ValueError("pgpass record is invalid")
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            if len(fields) >= _PGPASS_FIELDS - 1:
                raise ValueError("pgpass record is invalid")
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        raise ValueError("pgpass record is invalid")
    fields.append("".join(current))
    if len(fields) != _PGPASS_FIELDS or not fields[4]:
        raise ValueError("pgpass record is invalid")
    return tuple(fields)  # type: ignore[return-value]


def _validated_database(value: object) -> Mapping[str, object]:
    database = database_mapping(value)
    if database["host"] not in {"127.0.0.1", "::1"}:
        raise ValueError("database host is invalid")
    restore_database_names(database)
    return database


def _validate_state(value: object, mode: str) -> None:
    if mode == "inspection":
        if value != {"mode": "inspection"}:
            raise ValueError("inspection state is invalid")
        return
    if not isinstance(value, Mapping) or set(value) != {
        "mode", "database_available_bytes", "database_size_bytes"
    } or value["mode"] != "capacity":
        raise ValueError("capacity state is invalid")
    sizes = value["database_size_bytes"]
    if not isinstance(sizes, Mapping) or set(sizes) != {"canonical", "temporary", "retired"}:
        raise ValueError("capacity sizes are invalid")
    available = value["database_available_bytes"]
    if type(available) is not int or not 0 < available <= _MAX_SIGNED_64:
        raise ValueError("capacity count is invalid")
    for count in (value["database_available_bytes"], *sizes.values()):
        if count is None:
            continue
        if type(count) is not int or not 0 < count <= _MAX_SIGNED_64:
            raise ValueError("capacity count is invalid")


__all__ = ["provision_pgpass_authority", "restore_preflight"]
