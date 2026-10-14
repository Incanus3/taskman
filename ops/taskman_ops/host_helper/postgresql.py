"""Bounded read-only Ubuntu cluster selection and Taskman resource observation."""

from pathlib import Path
import ipaddress
import os
import pwd
import re
import shutil

from .commands import CommandError, run_command
from .database import database_mapping
from ..host_protocol.postgresql import native_path, validate_postgresql_context, validate_postgresql_authority


_TIMEOUT = 5.0


class PostgresqlObservationError(ValueError):
    """A selected safety boundary or native access check could not be proven."""

    def __init__(self, message, exit_code=10, *, observation=None,
                 failed_check="selected-target-or-resource-safety"):
        super().__init__(message)
        self.exit_code = exit_code
        self.observation = observation
        self.failed_check = failed_check


def parse_cluster_rows(output, package_track=None):
    """Parse the complete Ubuntu inventory before narrowing to the requested major."""
    validate_postgresql_context(package_track, False)
    rows = []
    for line in output.splitlines():
        fields = line.split()
        if (len(fields) != 7 or re.fullmatch(r"[0-9]+", fields[0]) is None or
                re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", fields[1]) is None or
                not fields[2].isascii() or not fields[2].isdecimal() or
                not 0 < int(fields[2]) < 65536 or fields[3] not in {"online", "down"} or
                fields[4] != "postgres"):
            raise PostgresqlObservationError("registered cluster inventory is malformed; inspect PostgreSQL setup")
        try:
            native_path(fields[5])
            native_path(fields[6])
        except ValueError as error:
            raise PostgresqlObservationError("registered cluster inventory has unsafe paths; inspect PostgreSQL setup") from error
        rows.append(dict(zip(("version", "name", "port", "state", "owner", "data_directory", "log_file"), fields)))
        rows[-1]["port"] = int(fields[2])
    if len(rows) > 64 or len({(row["version"], row["name"]) for row in rows}) != len(rows):
        raise PostgresqlObservationError("registered cluster inventory is ambiguous; inspect PostgreSQL setup")
    return rows if package_track is None else [row for row in rows if row["version"] == package_track]


def _command(argv, **kwargs):
    return run_command(argv, env={**os.environ, "LC_ALL": "C"},
                       timeout_seconds=_TIMEOUT, **kwargs).stdout.decode("ascii").strip()


def _retained_authority():
    # Installed binaries, retained data/configuration, administrator account and
    # native units all contradict a genuinely absent installation.
    for path in (Path("/etc/postgresql"), Path("/var/lib/postgresql"), Path("/usr/lib/postgresql")):
        if path.is_symlink() or path.exists() and (not path.is_dir() or any(path.iterdir())):
            return True
    try:
        pwd.getpwnam("postgres")
    except KeyError:
        pass
    else:
        return True
    units = _command(("systemctl", "list-unit-files", "--no-legend", "--no-pager"), output_limit=65536)
    return any(line.split()[0].startswith("postgresql") for line in units.splitlines() if line.split())


def _listeners(port, pid=None, host=None):
    text = _command(("ss", "-H", "-ltnp", f"sport = :{port}"))
    found = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 5:
            raise PostgresqlObservationError("listener inspection is malformed; inspect PostgreSQL setup")
        endpoint = fields[3]
        address, separator, number = endpoint.rpartition(":")
        if not separator or not number.isdecimal():
            raise PostgresqlObservationError("listener inspection is malformed; inspect PostgreSQL setup")
        if int(number) != port:
            continue
        address = address.strip("[]")
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise PostgresqlObservationError("configured PostgreSQL port has an unsafe listener; inspect PostgreSQL setup") from None
        owners = re.findall(r'\("([^"\n]+)",pid=([0-9]+),fd=[0-9]+\)', line)
        if pid is None or not ip.is_loopback or owners != [("postgres", str(pid))]:
            raise PostgresqlObservationError("configured PostgreSQL port has a foreign or public listener; inspect PostgreSQL setup")
        found.add(str(ip))
    if pid is not None and host not in found:
        raise PostgresqlObservationError("selected cluster does not listen at the configured endpoint; inspect PostgreSQL setup")



def _native_paths(cluster):
    for field in ("data_directory", "config_file", "hba_file"):
        path = cluster[field]
        if _command(("readlink", "-f", "--", path)) != path:
            raise PostgresqlObservationError("selected PostgreSQL native path is linked; inspect operator setup")
        details = _command(("stat", "--format=%F:%U:%G:%a", "--", path)).split(":")
        kind = "directory" if field == "data_directory" else "regular file"
        if (len(details) != 4 or details[0] != kind or details[1] not in {"root", "postgres"} or
                details[2] not in {"root", "postgres"} or re.fullmatch(r"[0-7]{3,4}", details[3]) is None or
                int(details[3], 8) & 0o022 or field == "data_directory" and details[1] != "postgres"):
            raise PostgresqlObservationError("selected PostgreSQL native path ownership is unsafe; inspect operator setup")

def _admin(database, sql):
    return _command(("runuser", "-u", "postgres", "--", "psql", "--no-psqlrc", "--tuples-only",
        "--no-align", "--host", "/var/run/postgresql", "--port", str(database["port"]),
        "--username", "postgres", "--dbname", "postgres", "--no-password", "--set=ON_ERROR_STOP=1",
        "--set=role=" + str(database["role"]), "--set=database=" + str(database["name"]), "--file=-"),
        stdin=(sql + ";\n").encode())


def observe_postgresql_authority(database, package_track, force_postgresql_config=False):
    """Prove genuine absence or one running native target and compatible resources."""
    database = database_mapping(database)
    validate_postgresql_context(package_track, force_postgresql_config)
    if database["host"] not in {"127.0.0.1", "::1"}:
        raise PostgresqlObservationError("PostgreSQL endpoint must be loopback; inspect PostgreSQL setup")
    try:
        if shutil.which("pg_lsclusters") is None:
            inventory = []
        else:
            inventory = parse_cluster_rows(_command(("pg_lsclusters", "--no-header")))
        selected = [row for row in inventory if package_track is None or row["version"] == package_track]
        if not inventory:
            if _retained_authority():
                raise PostgresqlObservationError("retained PostgreSQL installation has no selected cluster; complete operator setup")
            _listeners(database["port"])
            return {"branch": "bootstrap", "cluster": None, "role": "absent", "database": "absent", "access": "unproven"}
        if len(selected) != 1:
            raise PostgresqlObservationError("requested PostgreSQL track has no unique cluster; complete operator setup")
        row = selected[0]
        if row["state"] != "online" or row["port"] != database["port"]:
            raise PostgresqlObservationError("selected PostgreSQL cluster must be online at the configured port; complete operator setup")
        version, name = row["version"], row["name"]
        data = _command(("pg_conftool", "-s", version, name, "show", "data_directory"))
        hba = _command(("pg_conftool", "-s", version, name, "show", "hba_file"))
        cluster = {"version": version, "name": name, "data_directory": data,
                   "config_file": f"/etc/postgresql/{version}/{name}/postgresql.conf", "hba_file": hba, "port": row["port"]}
        result = {"branch": "configure-existing" if force_postgresql_config else "existing",
                  "cluster": cluster, "role": "absent", "database": "absent", "access": "unproven"}
        try:
            validate_postgresql_authority(result, package_track=package_track, port=database["port"], force_postgresql_config=force_postgresql_config)
        except ValueError as error:
            raise PostgresqlObservationError("selected PostgreSQL configuration identity is unsupported; inspect native paths before configuration override") from error
        if data != row["data_directory"]:
            raise PostgresqlObservationError("native PostgreSQL data directory contradicts inventory; replan after operator setup")
        _native_paths(cluster)
        identity = _command(("cat", data + "/postmaster.pid")).splitlines()
        if (len(identity) < 5 or not identity[0].isascii() or not identity[0].isdecimal() or
                int(identity[0]) <= 0 or identity[1] != data or identity[3] != str(database["port"]) or
                "/var/run/postgresql" not in identity[4].split(",")):
            raise PostgresqlObservationError("native PostgreSQL process/socket identity is contradictory; inspect operator setup")
        pid = int(identity[0])
        if (_command(("readlink", "-f", f"/proc/{pid}/exe")) != f"/usr/lib/postgresql/{version}/bin/postgres" or
                _command(("stat", "--format=%U:%G", f"/proc/{pid}")) != "postgres:postgres"):
            raise PostgresqlObservationError("native PostgreSQL process ownership is contradictory; inspect operator setup")
        _listeners(database["port"], pid, database["host"])
        live = _admin(database, "SELECT current_setting('server_version_num')::integer / 10000, current_setting('data_directory'), current_setting('config_file'), current_setting('hba_file'), current_setting('port')")
        if live != f"{version}|{data}|{cluster['config_file']}|{hba}|{database['port']}":
            raise PostgresqlObservationError("administrator connection reaches a different PostgreSQL cluster; inspect operator setup")
        role = _admin(database, "SELECT CASE WHEN rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND rolinherit AND NOT EXISTS (SELECT 1 FROM pg_auth_members membership JOIN pg_roles member ON member.oid = membership.member WHERE member.rolname = :'role') THEN 1 ELSE 0 END FROM pg_roles WHERE rolname = :'role'")
        owned = _admin(database, "SELECT CASE WHEN pg_get_userbyid(datdba) = :'role' THEN 1 ELSE 0 END FROM pg_database WHERE datname = :'database'")
        if (role, owned) not in {("", ""), ("1", ""), ("1", "1")}:
            raise PostgresqlObservationError("Taskman PostgreSQL privileges, memberships or database owner are incompatible; inspect operator setup")
        result.update(role="compatible" if role else "absent", database="compatible" if owned else "absent")
        return result
    except PostgresqlObservationError:
        raise
    except (CommandError, OSError, UnicodeError) as error:
        raise PostgresqlObservationError("native PostgreSQL administrator inspection is unavailable; establish selected-cluster access", 5) from error
