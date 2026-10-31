"""Bounded native PostgreSQL configuration with scoped HBA recovery.

Callers supply validated parameters and hold the lifecycle lock. Paths and native
identities are fixed here; configuration authority is distinct from admission.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import grp
import hashlib
import os
from pathlib import Path
import pwd
import re
import stat
import tempfile
from threading import Event
import time

from .commands import CommandCancelled, CommandCleanupError, CommandError, CommandTimeout, run_command


_IDENTITY_SQL = (
    "SELECT current_setting('port'), current_setting('data_directory'), "
    "(regexp_match(pg_read_file('/proc/self/status'), '^PPid:[[:space:]]+([0-9]+)$', 'm'))[1]"
)
_PARSER_SQL = "SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL"
_STAGE = "/etc/taskman/pg_hba.conf.staged"
_SOCKET = "/var/run/postgresql"
_FILE_LIMIT = 1024 * 1024
_IPV4_ASSIGNMENT = re.compile(
    rb"(?m)^([ \t]*listen_addresses[ \t]*=[ \t]*)127\.0\.0\.1([ \t]*(?:#[^\r\n]*)?)$"
)


class PostgreSQLConfigurationError(RuntimeError):
    """Bounded classification; no command diagnostics or configuration bytes."""

    def __init__(self, *, reason: str, mutation_state: str, manual: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.mutation_state = mutation_state
        self.manual = manual


@dataclass(frozen=True)
class _Cluster:
    version: str
    name: str
    port: int
    data_directory: str
    config_file: str
    hba_file: str

    @property
    def binary(self) -> str:
        return f"/usr/lib/postgresql/{self.version}/bin/postgres"


@dataclass
class _HBA:
    destination: Path
    backup: Path
    original: bytes
    metadata: tuple[int, int, int]
    created: bool = False
    installed: bool = False
    validated: bool = False


@dataclass
class _Procedure:
    deadline: float
    cancellation: Event
    mutation: str = "unchanged"

    def check(self, *, restoration: bool = False) -> float:
        if not restoration and self.cancellation.is_set():
            raise CommandCancelled("command cancelled")
        remaining = self.deadline - (0 if restoration else 30) - time.monotonic()
        if remaining <= 0:
            raise CommandTimeout("command timed out")
        return min(60.0, remaining)

    def command(self, argv: tuple[str, ...], *, mutating: bool = False) -> str:
        timeout = self.check()
        if mutating:
            self.uncertain()
        result = run_command(argv, env={**os.environ, "LC_ALL": "C"},
                             timeout_seconds=timeout, output_limit=16384,
                             cancellation=self.cancellation)
        if mutating:
            self.mutation = "changed"
        return result.stdout.decode("ascii").rstrip("\n")

    def uncertain(self) -> None:
        if self.mutation != "changed":
            self.mutation = "unknown"


def _local_path(path: str) -> Path:
    return Path(path)


def _postgres_identity() -> tuple[int, int]:
    account = pwd.getpwnam("postgres")
    return account.pw_uid, grp.getgrnam("postgres").gr_gid


def _unsafe() -> None:
    raise PostgreSQLConfigurationError(reason="unsafe_state", mutation_state="unchanged")


def _regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        _unsafe()


def _canonical_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir() or path.resolve() != path:
        _unsafe()


def _read(path: Path) -> bytes:
    _regular(path)
    with path.open("rb") as stream:
        content = stream.read(_FILE_LIMIT + 1)
    if len(content) > _FILE_LIMIT:
        _unsafe()
    return content


def _metadata(path: Path) -> tuple[int, int, int]:
    state = path.stat()
    return state.st_uid, state.st_gid, stat.S_IMODE(state.st_mode)


def _show(procedure: _Procedure, cluster: _Cluster, key: str) -> str:
    return procedure.command(("pg_conftool", "-s", cluster.version, cluster.name, "show", key))


def _admin(procedure: _Procedure, port: int, sql: str) -> str:
    return procedure.command(("runuser", "-u", "postgres", "--", "psql", "--no-psqlrc",
        "--tuples-only", "--no-align", "--field-separator", "|", "--host", _SOCKET,
        "--port", str(port), "--username", "postgres", "--dbname=postgres", "--command", sql))


def _select(procedure: _Procedure, parameters: Mapping[str, object]) -> _Cluster:
    rows = []
    for line in procedure.command(("pg_lsclusters", "--no-header")).splitlines():
        fields = line.split()
        if not fields:
            if parameters["package_track"] is None:
                _unsafe()
            continue
        if parameters["package_track"] is not None and fields[0] != parameters["package_track"]:
            continue
        # Preserve configuration's selected-row policy, not admission's complete inventory policy.
        if (len(fields) < 5 or re.fullmatch(r"[0-9]+", fields[0]) is None or
                re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", fields[1]) is None or
                re.fullmatch(r"[0-9]+", fields[2]) is None or not 0 < int(fields[2]) < 65536 or
                fields[3] not in {"online", "down"} or fields[4] != "postgres"):
            _unsafe()
        rows.append(fields)
    if len(rows) != 1 or rows[0][3] != "online":
        _unsafe()
    version, name, port = rows[0][:3]
    data = procedure.command(("pg_conftool", "-s", version, name, "show", "data_directory"))
    if not data.startswith("/") or re.fullmatch(r"[A-Za-z0-9_./-]+", data) is None:
        _unsafe()
    _canonical_directory(_local_path(data))
    cluster = _Cluster(version, name, int(port), data,
                       f"/etc/postgresql/{version}/{name}/postgresql.conf",
                       f"/etc/postgresql/{version}/{name}/pg_hba.conf")
    _canonical_directory(_local_path(cluster.config_file).parent)
    _regular(_local_path(cluster.config_file))
    _regular(_local_path(cluster.hba_file))
    if parameters["cluster"] is not None and parameters["cluster"] != vars(cluster):
        _unsafe()
    return cluster


def _runtime(procedure: _Procedure, cluster: _Cluster, *, port: int | None = None) -> int:
    lines = _read(_local_path(cluster.data_directory) / "postmaster.pid").decode("ascii").split("\n")
    if len(lines) != 9 or lines.pop() != "":
        _unsafe()
    if (len(lines) != 8 or re.fullmatch(r"[0-9]+", lines[0]) is None or int(lines[0]) <= 1 or
            lines[1] != cluster.data_directory or re.fullmatch(r"[0-9]+", lines[2]) is None or
            re.fullmatch(r"[0-9]+", lines[3]) is None or not 0 < int(lines[3]) < 65536 or
            lines[4] != _SOCKET or lines[7].rstrip() != "ready"):
        _unsafe()
    live_port = int(lines[3])
    if port is not None and live_port != port:
        _unsafe()
    procedure.command(("pg_ctlcluster", cluster.version, cluster.name, "status"))
    if _admin(procedure, live_port, _IDENTITY_SQL) != f"{live_port}|{cluster.data_directory}|{lines[0]}":
        _unsafe()
    if (_admin(procedure, live_port, "SHOW config_file") != cluster.config_file or
            _admin(procedure, live_port, "SHOW hba_file") != cluster.hba_file):
        _unsafe()
    if _admin(procedure, live_port, _PARSER_SQL):
        _unsafe()
    return live_port


def _desired(parameters: Mapping[str, object], cluster: _Cluster) -> dict[str, str]:
    database = parameters["database"]
    return {"hba_file": cluster.hba_file, "listen_addresses": database["host"],
            "port": str(database["port"]), "password_encryption": "scram-sha-256"}


def _effective(procedure: _Procedure, cluster: _Cluster, desired: Mapping[str, str]) -> bool:
    return all(procedure.command(("runuser", "-u", "postgres", "--", cluster.binary,
                   f"--config-file={cluster.config_file}", "-C", key)) == value
               for key, value in desired.items())


def _stage(parameters: Mapping[str, object]) -> bytes:
    content = _read(_local_path(_STAGE))
    if hashlib.sha256(content).hexdigest() != parameters["hba_sha256"]:
        _unsafe()
    return content


def _hba(cluster: _Cluster) -> _HBA:
    destination = _local_path(cluster.hba_file)
    recovery = destination.with_name(destination.name + ".taskman-backup")
    if recovery.exists() or recovery.is_symlink():
        raise PostgreSQLConfigurationError(reason="recovery_required", mutation_state="unchanged", manual=True)
    return _HBA(destination, recovery, _read(destination), _metadata(destination))


def _atomic_write(path: Path, content: bytes, metadata: tuple[int, int, int], procedure: _Procedure,
                  *, restoration: bool = False) -> None:
    procedure.check(restoration=restoration)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".taskman.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            os.fchown(stream.fileno(), metadata[0], metadata[1])
            os.fchmod(stream.fileno(), metadata[2])
        procedure.check(restoration=restoration)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _backup(hba: _HBA, procedure: _Procedure) -> None:
    procedure.check()
    hba.backup.mkdir(mode=0o700)
    hba.created = True
    procedure.mutation = "changed"
    hba.backup.chmod(0o700)
    for name, content in (("pg_hba.conf", hba.original),
                          ("metadata", ("%d:%d:%o\n" % hba.metadata).encode("ascii"))):
        fd = os.open(hba.backup / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), 0o600)


def _remove_backup(hba: _HBA, procedure: _Procedure) -> None:
    if hba.created:
        procedure.check(restoration=True)
        (hba.backup / "pg_hba.conf").unlink(missing_ok=True)
        (hba.backup / "metadata").unlink(missing_ok=True)
        hba.backup.rmdir()
        hba.created = False


def inspect_configuration(parameters: Mapping[str, object], *, deadline: float, cancellation: Event) -> bool:
    """Return required configuration for drift or an unprovable native predicate."""
    procedure = _Procedure(deadline, cancellation)
    try:
        procedure.check()
        cluster = _select(procedure, parameters)
        live_port = _runtime(procedure, cluster)
        hba = _hba(cluster)
        desired = _desired(parameters, cluster)
        required = not (live_port == parameters["database"]["port"] and
                    _stage(parameters) == hba.original and
                    hba.metadata == (*_postgres_identity(), 0o640) and
                    all(_show(procedure, cluster, key) == value for key, value in desired.items()) and
                    _effective(procedure, cluster, desired))
        procedure.check()
        return required
    except CommandCleanupError as error:
        raise PostgreSQLConfigurationError(reason="recovery_required", mutation_state="unchanged", manual=True) from error
    except (CommandCancelled, CommandTimeout) as error:
        raise PostgreSQLConfigurationError(reason="command_failed", mutation_state="unchanged") from error
    except (PostgreSQLConfigurationError, CommandError, OSError, UnicodeError, KeyError):
        return True


def configure_cluster(parameters: Mapping[str, object], *, deadline: float, cancellation: Event) -> bool:
    """Recheck native authority, converge settings/HBA, restart and validate."""
    procedure = _Procedure(deadline, cancellation)
    hba = None
    try:
        procedure.check()
        try:
            cluster = _select(procedure, parameters)
            hba = _hba(cluster)
            live_port = _runtime(procedure, cluster)
            stage = _stage(parameters)
        except (CommandCancelled, CommandTimeout, CommandCleanupError):
            raise
        except (CommandError, OSError, UnicodeError, KeyError) as error:
            raise PostgreSQLConfigurationError(reason="unsafe_state", mutation_state="unchanged") from error
        desired = _desired(parameters, cluster)
        restart_required = live_port != parameters["database"]["port"]
        for key, value in desired.items():
            try:
                current = _show(procedure, cluster, key)
            except (CommandCancelled, CommandTimeout, CommandCleanupError):
                raise
            except CommandError:
                current = None
            if current != value:
                procedure.command(("pg_conftool", cluster.version, cluster.name, "set", key, value), mutating=True)
                restart_required = True
        if desired["listen_addresses"] == "127.0.0.1":
            path = _local_path(cluster.config_file)
            original = _read(path)
            normalized = _IPV4_ASSIGNMENT.sub(rb"\1'127.0.0.1'\2", original)
            if normalized != original:
                _atomic_write(path, normalized, _metadata(path), procedure)
                procedure.mutation = "changed"
                restart_required = True
        if not _effective(procedure, cluster, desired):
            raise CommandError("effective configuration differs")
        if stage != hba.original or hba.metadata != (*_postgres_identity(), 0o640):
            _backup(hba, procedure)
            hba.installed = True
            _atomic_write(hba.destination, stage, (*_postgres_identity(), 0o640), procedure)
            if _admin(procedure, live_port, _PARSER_SQL):
                _unsafe()
            procedure.check()
            hba.validated = True
            restart_required = True
        if restart_required:
            procedure.command(("pg_ctlcluster", cluster.version, cluster.name, "restart"), mutating=True)
        _runtime(procedure, cluster, port=parameters["database"]["port"])
        procedure.check()
        _remove_backup(hba, procedure)
        return procedure.mutation == "changed"
    except (PostgreSQLConfigurationError, CommandError, OSError, UnicodeError) as error:
        reason = error.reason if isinstance(error, PostgreSQLConfigurationError) else "command_failed"
        manual = isinstance(error, PostgreSQLConfigurationError) and error.manual
        if isinstance(error, CommandCleanupError):
            # Restoration must never race a command whose quiescence is unproven.
            reason, manual = "recovery_required", True
        elif hba is not None and hba.installed and not hba.validated:
            try:
                _atomic_write(hba.destination, hba.original, hba.metadata, procedure, restoration=True)
                _remove_backup(hba, procedure)
            except (CommandError, OSError, PostgreSQLConfigurationError):
                reason, manual = "recovery_required", True
        raise PostgreSQLConfigurationError(reason=reason, mutation_state=procedure.mutation,
                                           manual=manual or hba is not None and hba.created) from error


__all__ = ["PostgreSQLConfigurationError", "inspect_configuration", "configure_cluster"]
