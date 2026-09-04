"""PostgreSQL desired state and guarded role/database adoption rules.

This module keeps declarative package/configuration state separate from the
one-time, secret-bearing role creation step.  The latter receives a password
only through sensitive standard input and is never prepared as a shell command
with the credential in its argv.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import StringIO
from pathlib import PurePosixPath
import shlex
from typing import Mapping, Protocol

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..pyinfra import conditional_convergence
from ..remote import ChangeSet, CommandResult, Remote


_POSTGRES_ADMIN_SOCKET = "/var/run/postgresql"


@dataclass(frozen=True)
class DatabaseRole:
    name: str
    login: bool
    superuser: bool
    createdb: bool
    createrole: bool
    replication: bool
    bypassrls: bool
    inherit: bool


@dataclass(frozen=True)
class Database:
    name: str
    owner: str


@dataclass(frozen=True)
class ManagedFile:
    path: str
    owner: str
    group: str
    mode: int


@dataclass(frozen=True)
class ExistingRole:
    """A redacted authority snapshot read before any role mutation."""

    name: str
    login: bool
    superuser: bool
    createdb: bool
    createrole: bool
    replication: bool
    bypassrls: bool
    inherit: bool = True
    memberships: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExistingDatabase:
    """The only existing database fact needed to decide safe adoption."""

    name: str
    owner: str


@dataclass(frozen=True)
class PostgreSQLPlan:
    packages: tuple[str, ...]
    package_track: str | None
    settings: Mapping[str, str]
    hba: str
    role: DatabaseRole
    database: Database
    pgpass: ManagedFile
    native_validation: tuple[str, ...]
    role_setup_argv: tuple[str, ...]
    role_creation_argv: tuple[str, ...]
    database_creation_argv: tuple[str, ...]


@dataclass(frozen=True)
class PostgreSQLCluster:
    """The one concrete Ubuntu cluster that may receive Taskman settings."""

    version: str
    name: str
    configured_port: str


class PostgreSQLOperations(Protocol):
    """Stable desired-state operations implemented by the pyinfra adapter."""

    def packages(self, packages: tuple[str, ...]) -> None: ...

    def hba_file(self, content: str) -> None: ...

    def configure(self, plan: PostgreSQLPlan) -> None: ...


def build_postgresql_plan(config: EnvironmentConfig) -> PostgreSQLPlan:
    """Return a complete PostgreSQL target without reading a mutable host fact."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("PostgreSQL plan requires an environment configuration")
    suffix = "" if config.postgres_package_track is None else f"-{config.postgres_package_track}"
    role = DatabaseRole(config.database_role, True, False, False, False, False, False, True)
    database = Database(config.database_name, config.database_role)
    hba_address = "127.0.0.1/32" if config.database_host == "127.0.0.1" else "::1/128"
    return PostgreSQLPlan(
        packages=(f"postgresql{suffix}", f"postgresql-client{suffix}"),
        package_track=config.postgres_package_track,
        settings={
            "listen_addresses": config.database_host,
            "port": str(config.database_port),
            "password_encryption": "scram-sha-256",
        },
        hba=(
            "# Managed by Taskman; do not add public database access here.\n"
            "local   all             postgres                                peer\n"
            "local   all             all                                     peer\n"
            f"host    {database.name}    {role.name}    {hba_address}    scram-sha-256\n"
        ),
        role=role,
        database=database,
        pgpass=ManagedFile("/etc/taskman/pgpass", "root", "root", 0o600),
        native_validation=(
            "postgres",
            "--config-file",
            "managed-cluster-config",
            "-C",
            "listen_addresses",
        ),
        role_setup_argv=(
            "psql",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--host",
            _POSTGRES_ADMIN_SOCKET,
            "--port",
            str(config.database_port),
            "--username",
            "postgres",
            "--dbname=postgres",
        ),
        role_creation_argv=(),
        database_creation_argv=(
            "createdb",
            "--host",
            _POSTGRES_ADMIN_SOCKET,
            "--port",
            str(config.database_port),
            "--username",
            "postgres",
            "--owner",
            role.name,
            database.name,
        ),
    )


def converge_postgresql(
    config: EnvironmentConfig, *, operations: PostgreSQLOperations | None = None
) -> PostgreSQLPlan:
    """Declare package and native configuration convergence through pyinfra.

    Role/database discovery and creation are intentionally not hidden in this
    prepare-time function: they depend on the server just installed here and
    are coordinated after these operations have executed by a release or
    provision workflow.
    """

    plan = build_postgresql_plan(config)
    backend = operations or _PyinfraPostgreSQLOperations()
    backend.packages(plan.packages)
    backend.hba_file(plan.hba)
    backend.configure(plan)
    return plan


def render_role_password_input(role: DatabaseRole, password: str) -> bytes:
    """Render one transactional psql role/password initialization exchange.

    psql consumes the value from stdin twice.  It never becomes an argv value,
    environment variable, generated host command, or persisted SQL file.
    """

    if not isinstance(role, DatabaseRole):
        raise TypeError("role password input requires a database role")
    if not isinstance(password, str) or not password or any(char in password for char in "\r\n\x00"):
        raise ValueError("database password is invalid for protected stdin")
    return (
        "\\set ON_ERROR_STOP on\n"
        "BEGIN;\n"
        f"CREATE ROLE {role.name} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;\n"
        f"\\password {role.name}\n"
        f"{password}\n{password}\n"
        "COMMIT;\n"
    ).encode("utf-8")


def validate_existing_database_state(
    plan: PostgreSQLPlan,
    *,
    role: ExistingRole | None,
    database: ExistingDatabase | None,
) -> None:
    """Adopt only exactly compatible state; never repair contradictory authority."""

    if not isinstance(plan, PostgreSQLPlan):
        raise TypeError("database validation requires a PostgreSQL plan")
    if role is None and database is not None:
        raise _safety_refusal()
    if role is not None and (not _role_matches(plan.role, role) or role.memberships):
        raise _safety_refusal()
    if database is not None and (database.name != plan.database.name or database.owner != plan.database.owner):
        raise _safety_refusal()


def install_pgpass(remote: Remote, content: bytes) -> ChangeSet:
    """Install the rendered application password file from protected stdin.

    A unique restrictive staging file lets the remote command compare bytes and
    exact metadata before replacing the final root-only file.  Repeated runs
    therefore leave already-converged content untouched while never exposing a
    password through an argument, environment variable, or command log.
    """

    if not isinstance(content, bytes) or not content:
        raise ValueError("pgpass content must be non-empty bytes")
    result = remote.run(
        (
            "sh",
            "-c",
            "set -eu; changed=0; stage=$(mktemp /tmp/taskman-pgpass.XXXXXX); "
            "trap 'rm -f \"$stage\"' EXIT HUP INT TERM; umask 077; cat > \"$stage\"; "
            "state=$(stat --format='%U:%G:%a' \"$1\" 2>/dev/null || true); "
            "if ! cmp -s \"$stage\" \"$1\" || [ \"$state\" != root:root:600 ]; then "
            "install -o root -g root -m 0600 \"$stage\" \"$1\"; changed=1; fi; "
            "printf 'changed=%s\\n' \"$changed\"",
            "taskman-pgpass",
            "/etc/taskman/pgpass",
        ),
        sudo=True,
        stdin=content,
        sensitive=True,
    )
    _require_success(result, "unable to install protected PostgreSQL credentials")
    changed = _changed_result(result.stdout, "PostgreSQL credential convergence")
    return ChangeSet(changed=changed, operations=("pgpass",) if changed else ())


def converge_database(remote: Remote, plan: PostgreSQLPlan, *, password: str, pgpass: bytes) -> ChangeSet:
    """Safely adopt or create database state after package convergence has run.

    This execution-time capability is deliberately separate from
    :func:`converge_postgresql`: pyinfra prepares package/configuration
    operations first, while role and database decisions may only read facts
    after that work has executed.  Existing incompatible authority is refused,
    and a password is consumed by psql's prompt over sensitive stdin only for
    a newly created role.
    """

    if not isinstance(plan, PostgreSQLPlan):
        raise TypeError("database convergence requires a PostgreSQL plan")
    role = _read_existing_role(remote, plan)
    if role is not None:
        role = ExistingRole(
            role.name,
            role.login,
            role.superuser,
            role.createdb,
            role.createrole,
            role.replication,
            role.bypassrls,
            role.inherit,
            _read_existing_role_memberships(remote, plan),
        )
    database = _read_existing_database(remote, plan)
    validate_existing_database_state(plan, role=role, database=database)
    # Refuse contradictory authority before any persistent credential write.
    pgpass_result = install_pgpass(remote, pgpass)
    operations = list(pgpass_result.operations)
    if role is None:
        _require_success(
            remote.run(
                ("runuser", "-u", "postgres", "--", *plan.role_setup_argv),
                sudo=True,
                stdin=render_role_password_input(plan.role, password),
                sensitive=True,
            ),
            "unable to create and initialize PostgreSQL role",
        )
        operations.append("role")
    if database is None:
        _require_success(
            remote.run(("runuser", "-u", "postgres", "--", *plan.database_creation_argv), sudo=True),
            "unable to create PostgreSQL database",
        )
        operations.append("database")
    _verify_application_connection(remote, plan)
    return ChangeSet(changed=bool(operations), operations=tuple(operations))


def _read_existing_role(remote: Remote, plan: PostgreSQLPlan) -> ExistingRole | None:
    query = (
        "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
        "rolreplication, rolbypassrls, rolinherit FROM pg_roles "
        f"WHERE rolname = '{plan.role.name}'"
    )
    result = remote.run(
        (
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--field-separator",
            "|",
            "--dbname=postgres",
            "--host",
            _POSTGRES_ADMIN_SOCKET,
            "--port",
            plan.settings["port"],
            "--username",
            "postgres",
            "--command",
            query,
        ),
        sudo=True,
    )
    _require_success(result, "unable to inspect PostgreSQL role")
    fields = _one_record(result, 8, "PostgreSQL role inspection returned invalid data")
    if fields is None:
        return None
    try:
        return ExistingRole(fields[0], *(_postgres_boolean(field) for field in fields[1:]))
    except ValueError:
        raise _safety_refusal() from None


def _read_existing_role_memberships(remote: Remote, plan: PostgreSQLPlan) -> tuple[str, ...]:
    """Read every explicit parent role; implicit PUBLIC is not a membership row."""

    query = (
        "SELECT parent.rolname FROM pg_auth_members membership "
        "JOIN pg_roles parent ON parent.oid = membership.roleid "
        "JOIN pg_roles member ON member.oid = membership.member "
        f"WHERE member.rolname = '{plan.role.name}' ORDER BY parent.rolname"
    )
    result = remote.run(
        (
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--host",
            _POSTGRES_ADMIN_SOCKET,
            "--port",
            plan.settings["port"],
            "--username",
            "postgres",
            "--dbname=postgres",
            "--command",
            query,
        ),
        sudo=True,
    )
    _require_success(result, "unable to inspect PostgreSQL role memberships")
    memberships = tuple(line for line in result.stdout.splitlines() if line)
    if len(memberships) != len(set(memberships)):
        raise _safety_refusal()
    return memberships


def _read_existing_database(remote: Remote, plan: PostgreSQLPlan) -> ExistingDatabase | None:
    query = (
        "SELECT datname, pg_get_userbyid(datdba) FROM pg_database "
        f"WHERE datname = '{plan.database.name}'"
    )
    result = remote.run(
        (
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--field-separator",
            "|",
            "--host",
            _POSTGRES_ADMIN_SOCKET,
            "--port",
            plan.settings["port"],
            "--username",
            "postgres",
            "--dbname=postgres",
            "--command",
            query,
        ),
        sudo=True,
    )
    _require_success(result, "unable to inspect PostgreSQL database")
    fields = _one_record(result, 2, "PostgreSQL database inspection returned invalid data")
    if fields is None:
        return None
    return ExistingDatabase(*fields)


def _one_record(result: CommandResult, fields: int, message: str) -> tuple[str, ...] | None:
    records = [line.split("|") for line in result.stdout.splitlines() if line]
    if not records:
        return None
    if len(records) != 1 or len(records[0]) != fields:
        raise OpsError(ExitStatus.SAFETY, "postgresql", message, changed=False)
    return tuple(records[0])


def _postgres_boolean(value: str) -> bool:
    if value == "t":
        return True
    if value == "f":
        return False
    raise ValueError("invalid PostgreSQL boolean")


def _verify_application_connection(remote: Remote, plan: PostgreSQLPlan) -> None:
    result = remote.run(
        (
            "env",
            "PGPASSFILE=/etc/taskman/pgpass",
            "psql",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--host",
            plan.settings["listen_addresses"],
            "--port",
            plan.settings["port"],
            "--username",
            plan.role.name,
            "--dbname",
            plan.database.name,
            "--command",
            "SELECT 1",
        ),
        sudo=True,
    )
    _require_success(result, "application PostgreSQL connection verification failed")


def _require_success(result: CommandResult, message: str) -> None:
    if not result.succeeded:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "postgresql",
            message,
            changed=False,
            next_action="inspect PostgreSQL service state and retry the convergent operation",
        )


def _changed_result(stdout: str, operation: str) -> bool:
    markers = [line.removeprefix("changed=") for line in stdout.splitlines() if line.startswith("changed=")]
    if markers == ["0"]:
        return False
    if markers == ["1"]:
        return True
    raise OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "postgresql",
        f"{operation} returned an invalid change result",
        changed=False,
        next_action="inspect PostgreSQL convergence output before retrying",
    )


def _role_matches(expected: DatabaseRole, actual: ExistingRole) -> bool:
    return (
        actual.name == expected.name
        and actual.login == expected.login
        and actual.superuser == expected.superuser
        and actual.createdb == expected.createdb
        and actual.createrole == expected.createrole
        and actual.replication == expected.replication
        and actual.bypassrls == expected.bypassrls
        and actual.inherit == expected.inherit
    )


def _safety_refusal() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "postgresql",
        "existing PostgreSQL role or database is incompatible and will not be replaced",
        changed=False,
        next_action="inspect the existing PostgreSQL authority and resolve the contradiction deliberately",
    )


def select_postgresql_cluster(output: str, plan: PostgreSQLPlan) -> tuple[str, str, str]:
    """Select exactly one intended cluster without silently using the first row.

    A single fresh cluster may still have Ubuntu's default port before this
    capability sets a requested custom port. That unambiguous initial candidate
    is accepted; every zero/multiple candidate state is refused.
    """

    if not isinstance(plan, PostgreSQLPlan):
        raise TypeError("cluster selection requires a PostgreSQL plan")
    candidates: list[PostgreSQLCluster] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1] or not fields[2].isdigit():
            raise _cluster_refusal()
        if plan.package_track is not None and fields[0] != plan.package_track:
            continue
        candidates.append(PostgreSQLCluster(fields[0], fields[1], fields[2]))
    if len(candidates) == 1:
        cluster = candidates[0]
        return cluster.version, cluster.name, cluster.configured_port
    raise _cluster_refusal()


def apply_postgresql_native_configuration(remote: Remote, plan: PostgreSQLPlan) -> bool:
    """Run checked native configuration and parse its truthful change marker."""

    if not isinstance(plan, PostgreSQLPlan):
        raise TypeError("native PostgreSQL configuration requires a plan")
    result = remote.run(("sh", "-c", render_postgresql_native_configuration_script(plan)), sudo=True)
    if result.returncode == int(ExitStatus.SAFETY):
        raise _cluster_refusal()
    _require_success(result, "unable to converge native PostgreSQL configuration")
    values = [line.removeprefix("changed=") for line in result.stdout.splitlines() if line.startswith("changed=")]
    if values == ["0"]:
        return False
    if values == ["1"]:
        return True
    raise OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "postgresql",
        "native PostgreSQL convergence returned an invalid change result",
        changed=False,
        next_action="inspect PostgreSQL native configuration output before retrying",
    )


def render_postgresql_native_configuration_script(
    plan: PostgreSQLPlan,
    *,
    hba_stage: str = "/etc/taskman/pg_hba.conf.staged",
    hba_final: str = "/etc/postgresql/taskman/pg_hba.conf",
    hba_owner: str | None = "root",
    hba_group: str | None = "postgres",
) -> str:
    """Render fail-fast native config/HBA gates and conditional restart logic."""

    if not isinstance(plan, PostgreSQLPlan):
        raise TypeError("native PostgreSQL script requires a plan")
    if (hba_owner is None) != (hba_group is None):
        raise ValueError("PostgreSQL HBA owner and group must be provided together")
    if not isinstance(hba_stage, str) or not isinstance(hba_final, str):
        raise TypeError("PostgreSQL HBA paths must be strings")
    final_path = PurePosixPath(hba_final)
    if not final_path.is_absolute() or ".." in final_path.parts:
        raise ValueError("PostgreSQL HBA final path must be an absolute safe path")
    track = "" if plan.package_track is None else plan.package_track
    listen = plan.settings["listen_addresses"]
    port = plan.settings["port"]
    encryption = plan.settings["password_encryption"]
    hba_state = "$(stat --format='%a'" if hba_owner is None else "$(stat --format='%U:%G:%a'"
    expected_hba_state = "640" if hba_owner is None else f"{hba_owner}:{hba_group}:640"
    hba_install_owner = "" if hba_owner is None else f"-o {shlex.quote(hba_owner)} -g {shlex.quote(hba_group)} "
    hba_parent = final_path.parent.as_posix()
    expected_hba_digest = hashlib.sha256(plan.hba.encode("utf-8")).hexdigest()
    parent_convergence = ""
    if hba_owner is not None:
        parent_convergence = f"""hba_parent_state=$(stat --format='%U:%G:%a' {shlex.quote(hba_parent)} 2>/dev/null || true)
if [ \"$hba_parent_state\" != {shlex.quote(f'{hba_owner}:{hba_group}:750')} ]; then
  install -d -o {shlex.quote(hba_owner)} -g {shlex.quote(hba_group)} -m 0750 {shlex.quote(hba_parent)}
  changed=1
fi
"""
    return f"""set -eu
refuse_cluster() {{ echo 'ambiguous PostgreSQL cluster' >&2; exit {int(ExitStatus.SAFETY)}; }}
refuse_runtime() {{ echo 'ambiguous PostgreSQL runtime state' >&2; exit {int(ExitStatus.SAFETY)}; }}
clusters=$(pg_lsclusters --no-header)
if ! candidates=$(printf '%s\\n' \"$clusters\" | awk -v track={shlex.quote(track)} '
  track == \"\" || $1 == track {{
    if (NF < 5 || $1 !~ /^[0-9]+$/ || $2 !~ /^[A-Za-z0-9_][A-Za-z0-9_-]*$/ ||
        $3 !~ /^[0-9]+$/ || ($4 != \"online\" && $4 != \"down\") || $5 != \"postgres\") exit 2
    print $1, $2, $3, $4
  }}
'); then refuse_cluster; fi
candidate_count=$(printf '%s\\n' \"$candidates\" | sed '/^$/d' | wc -l | tr -d ' ')
[ \"$candidate_count\" -eq 1 ] || refuse_cluster
set -- $candidates
version=$1
cluster=$2
configured_port=$3
cluster_state=$4
desired_port={shlex.quote(port)}
data_directory=$(pg_conftool -s \"$version\" \"$cluster\" show data_directory 2>/dev/null) || refuse_runtime
case \"$data_directory\" in
  /*) ;;
  *) refuse_runtime ;;
esac
case \"$data_directory\" in
  *[!A-Za-z0-9_./-]*) refuse_runtime ;;
esac
[ -d \"$data_directory\" ] && [ \"$(readlink -f -- \"$data_directory\")\" = \"$data_directory\" ] || refuse_runtime
config_file=\"/etc/postgresql/$version/$cluster/postgresql.conf\"
pid_file=\"$data_directory/postmaster.pid\"
changed=0
admin_query() {{
  endpoint=$1
  query=$2
  runuser -u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator '|' --host {shlex.quote(_POSTGRES_ADMIN_SOCKET)} --port \"$endpoint\" --username postgres --dbname=postgres --command \"$query\"
}}
load_runtime_pid() {{
  [ -f \"$pid_file\" ] && [ ! -L \"$pid_file\" ] || return 1
  [ \"$(wc -l < \"$pid_file\" | tr -d ' ')\" -eq 8 ] || refuse_runtime
  runtime_pid=$(sed -n '1p' \"$pid_file\")
  runtime_data=$(sed -n '2p' \"$pid_file\")
  runtime_start=$(sed -n '3p' \"$pid_file\")
  runtime_port=$(sed -n '4p' \"$pid_file\")
  runtime_socket=$(sed -n '5p' \"$pid_file\")
  runtime_status=$(sed -n '8p' \"$pid_file\" | sed 's/[[:space:]]*$//')
  case \"$runtime_pid\" in ''|*[!0-9]*) refuse_runtime ;; esac
  case \"$runtime_start\" in ''|*[!0-9]*) refuse_runtime ;; esac
  case \"$runtime_port\" in ''|*[!0-9]*) refuse_runtime ;; esac
  [ \"$runtime_pid\" -gt 1 ] && [ \"$runtime_port\" -ge 1 ] && [ \"$runtime_port\" -le 65535 ] || refuse_runtime
  [ \"$runtime_data\" = \"$data_directory\" ] || refuse_runtime
  [ \"$runtime_socket\" = {shlex.quote(_POSTGRES_ADMIN_SOCKET)} ] || refuse_runtime
  [ \"$runtime_status\" = ready ] || refuse_runtime
  pg_ctlcluster \"$version\" \"$cluster\" status >/dev/null 2>&1 || refuse_runtime
}}
validate_runtime_identity() {{
  identity=$1
  old_ifs=$IFS
  IFS='|'
  set -- $identity
  IFS=$old_ifs
  [ \"$#\" -eq 3 ] || refuse_runtime
  [ \"$1\" = \"$runtime_port\" ] && [ \"$2\" = \"$data_directory\" ] && [ \"$3\" = \"$runtime_start\" ] || refuse_runtime
}}
runtime_state=stopped
active_hba_file=
case \"$cluster_state\" in
  online)
    load_runtime_pid || refuse_runtime
    runtime_state=unreachable
    if runtime_identity=$(admin_query \"$runtime_port\" \"SELECT current_setting('port'), current_setting('data_directory'), extract(epoch from pg_postmaster_start_time())::bigint\"); then
      validate_runtime_identity \"$runtime_identity\"
      runtime_state=reachable
      active_config_file=$(admin_query \"$runtime_port\" 'SHOW config_file')
      [ \"$active_config_file\" = \"$config_file\" ] || refuse_runtime
      active_hba_file=$(admin_query \"$runtime_port\" 'SHOW hba_file')
      case \"$active_hba_file\" in /*) ;; *) refuse_runtime ;; esac
    fi
    ;;
  down)
    [ ! -e \"$pid_file\" ] || refuse_runtime
    if pg_ctlcluster \"$version\" \"$cluster\" status >/dev/null 2>&1; then refuse_runtime; fi
    ;;
  *) refuse_runtime ;;
esac
if [ \"$runtime_state\" != reachable ] || [ \"$runtime_port\" != \"$desired_port\" ] ||
   [ \"$active_hba_file\" != {shlex.quote(hba_final)} ]; then changed=1; fi
stage_digest=$(sha256sum {shlex.quote(hba_stage)} | awk '{{print $1}}')
[ \"$stage_digest\" = {shlex.quote(expected_hba_digest)} ] || refuse_runtime
{parent_convergence}if ! cmp -s {shlex.quote(hba_stage)} {shlex.quote(hba_final)} || [ \"{hba_state} {shlex.quote(hba_final)} 2>/dev/null || true)\" != {shlex.quote(expected_hba_state)} ]; then
  install {hba_install_owner}-m 0640 {shlex.quote(hba_stage)} {shlex.quote(hba_final)}
  changed=1
fi
configure() {{
  key=$1 value=$2
  current=$(pg_conftool -s \"$version\" \"$cluster\" show \"$key\" 2>/dev/null || true)
  if [ \"$current\" != \"$value\" ]; then pg_conftool \"$version\" \"$cluster\" set \"$key\" \"$value\"; changed=1; fi
}}
configure hba_file {shlex.quote(hba_final)}
configure listen_addresses {shlex.quote(listen)}
configure port \"$desired_port\"
configure password_encryption {shlex.quote(encryption)}
postgres --config-file=\"$config_file\" -C listen_addresses | grep -Fx {shlex.quote(listen)} >/dev/null
postgres --config-file=\"$config_file\" -C port | grep -Fx {shlex.quote(port)} >/dev/null
postgres --config-file=\"$config_file\" -C password_encryption | grep -Fx {shlex.quote(encryption)} >/dev/null
postgres --config-file=\"$config_file\" -C hba_file | grep -Fx {shlex.quote(hba_final)} >/dev/null
if [ \"$runtime_state\" = reachable ] && [ \"$changed\" -eq 1 ]; then
  pg_ctlcluster \"$version\" \"$cluster\" reload
fi
if [ \"$runtime_state\" = reachable ]; then
  [ \"$(admin_query \"$runtime_port\" 'SHOW hba_file')\" = {shlex.quote(hba_final)} ] || refuse_runtime
  hba_errors=$(admin_query \"$runtime_port\" 'SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL')
  test -z \"$hba_errors\"
fi
if [ \"$changed\" -eq 1 ]; then
  pg_ctlcluster \"$version\" \"$cluster\" restart
fi
load_runtime_pid || refuse_runtime
[ \"$runtime_port\" = \"$desired_port\" ] || refuse_runtime
runtime_identity=$(admin_query \"$runtime_port\" \"SELECT current_setting('port'), current_setting('data_directory'), extract(epoch from pg_postmaster_start_time())::bigint\")
validate_runtime_identity \"$runtime_identity\"
[ \"$(admin_query \"$runtime_port\" 'SHOW config_file')\" = \"$config_file\" ] || refuse_runtime
[ \"$(admin_query \"$runtime_port\" 'SHOW hba_file')\" = {shlex.quote(hba_final)} ] || refuse_runtime
hba_errors=$(admin_query \"$runtime_port\" 'SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL')
test -z \"$hba_errors\"
printf 'changed=%s\\n' \"$changed\"
"""


def render_postgresql_native_configuration_probe(
    plan: PostgreSQLPlan,
    *,
    hba_stage: str = "/etc/taskman/pg_hba.conf.staged",
    hba_final: str = "/etc/postgresql/taskman/pg_hba.conf",
) -> str:
    """Return a read-only native configuration drift probe for pyinfra.

    Cluster selection happens at execution time. Ambiguous state intentionally
    reports drift so the guarded transaction can issue its stable safety
    refusal instead of treating it as an untracked no-op.
    """

    if not isinstance(plan, PostgreSQLPlan):
        raise TypeError("native PostgreSQL probe requires a plan")
    track = "" if plan.package_track is None else plan.package_track
    expected_hba_digest = hashlib.sha256(plan.hba.encode("utf-8")).hexdigest()
    checks = " && ".join(
        f'[ "$(pg_conftool -s "$version" "$cluster" show {key} 2>/dev/null || true)" = {shlex.quote(value)} ]'
        for key, value in (
            ("hba_file", hba_final),
            ("listen_addresses", plan.settings["listen_addresses"]),
            ("port", plan.settings["port"]),
            ("password_encryption", plan.settings["password_encryption"]),
        )
    )
    return f"""set -eu
clusters=$(pg_lsclusters --no-header)
if ! candidates=$(printf '%s\\n' "$clusters" | awk -v track={shlex.quote(track)} '
  track == "" || $1 == track {{
    if (NF < 5 || $1 !~ /^[0-9]+$/ || $2 !~ /^[A-Za-z0-9_][A-Za-z0-9_-]*$/ ||
        $3 !~ /^[0-9]+$/ || ($4 != "online" && $4 != "down") || $5 != "postgres") exit 2
    print $1, $2, $3, $4
  }}
'); then printf 'changed=1\\n'; exit 0; fi
candidate_count=$(printf '%s\\n' "$candidates" | sed '/^$/d' | wc -l | tr -d ' ')
if [ "$candidate_count" -ne 1 ]; then printf 'changed=1\\n'; exit 0; fi
set -- $candidates
version=$1
cluster=$2
cluster_state=$4
desired_port={shlex.quote(plan.settings["port"])}
if ! data_directory=$(pg_conftool -s "$version" "$cluster" show data_directory 2>/dev/null); then
  printf 'changed=1\\n'; exit 0
fi
case "$data_directory" in
  /*) ;;
  *) printf 'changed=1\\n'; exit 0 ;;
esac
case "$data_directory" in
  *[!A-Za-z0-9_./-]*) printf 'changed=1\\n'; exit 0 ;;
esac
if [ ! -d "$data_directory" ] || [ "$(readlink -f -- "$data_directory")" != "$data_directory" ]; then
  printf 'changed=1\\n'; exit 0
fi
config_file="/etc/postgresql/$version/$cluster/postgresql.conf"
pid_file="$data_directory/postmaster.pid"
if [ "$cluster_state" != online ] || [ ! -f "$pid_file" ] || [ -L "$pid_file" ]; then
  printf 'changed=1\\n'; exit 0
fi
if [ "$(wc -l < "$pid_file" | tr -d ' ')" -ne 8 ]; then printf 'changed=1\\n'; exit 0; fi
runtime_pid=$(sed -n '1p' "$pid_file")
runtime_data=$(sed -n '2p' "$pid_file")
runtime_start=$(sed -n '3p' "$pid_file")
runtime_port=$(sed -n '4p' "$pid_file")
runtime_socket=$(sed -n '5p' "$pid_file")
runtime_status=$(sed -n '8p' "$pid_file" | sed 's/[[:space:]]*$//')
case "$runtime_pid:$runtime_start:$runtime_port" in *[!0-9:]*) printf 'changed=1\\n'; exit 0 ;; esac
if [ -z "$runtime_pid" ] || [ -z "$runtime_start" ] || [ -z "$runtime_port" ] ||
   [ "$runtime_pid" -le 1 ] || [ "$runtime_port" -lt 1 ] || [ "$runtime_port" -gt 65535 ] ||
   [ "$runtime_data" != "$data_directory" ] || [ "$runtime_socket" != {shlex.quote(_POSTGRES_ADMIN_SOCKET)} ] ||
   [ "$runtime_status" != ready ] ||
   ! pg_ctlcluster "$version" "$cluster" status >/dev/null 2>&1; then
  printf 'changed=1\\n'; exit 0
fi
hba_state=$(stat --format='%U:%G:%a' {shlex.quote(hba_final)} 2>/dev/null || true)
hba_parent_state=$(stat --format='%U:%G:%a' {shlex.quote(PurePosixPath(hba_final).parent.as_posix())} 2>/dev/null || true)
stage_digest=$(sha256sum {shlex.quote(hba_stage)} 2>/dev/null | awk '{{print $1}}')
if ! runtime_identity=$(runuser -u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator '|' --host {shlex.quote(_POSTGRES_ADMIN_SOCKET)} --port "$runtime_port" --username postgres --dbname=postgres --command "SELECT current_setting('port'), current_setting('data_directory'), extract(epoch from pg_postmaster_start_time())::bigint"); then
  printf 'changed=1\\n'; exit 0
fi
old_ifs=$IFS
IFS='|'
set -- $runtime_identity
IFS=$old_ifs
if [ "$#" -ne 3 ] || [ "$1" != "$runtime_port" ] || [ "$2" != "$data_directory" ] || [ "$3" != "$runtime_start" ]; then
  printf 'changed=1\\n'; exit 0
fi
active_config_file=$(runuser -u postgres -- psql --no-psqlrc --tuples-only --no-align --host {shlex.quote(_POSTGRES_ADMIN_SOCKET)} --port "$runtime_port" --username postgres --dbname=postgres --command 'SHOW config_file') || {{
  printf 'changed=1\\n'; exit 0
}}
active_hba_file=$(runuser -u postgres -- psql --no-psqlrc --tuples-only --no-align --host {shlex.quote(_POSTGRES_ADMIN_SOCKET)} --port "$runtime_port" --username postgres --dbname=postgres --command 'SHOW hba_file') || {{
  printf 'changed=1\\n'; exit 0
}}
hba_errors=$(runuser -u postgres -- psql --no-psqlrc --tuples-only --no-align --host {shlex.quote(_POSTGRES_ADMIN_SOCKET)} --port "$runtime_port" --username postgres --dbname=postgres --command 'SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL') || {{
  printf 'changed=1\\n'; exit 0
}}
if [ "$runtime_port" = "$desired_port" ] &&
   [ "$active_config_file" = "$config_file" ] &&
   [ "$active_hba_file" = {shlex.quote(hba_final)} ] &&
   [ -z "$hba_errors" ] &&
   [ "$stage_digest" = {shlex.quote(expected_hba_digest)} ] &&
   cmp -s {shlex.quote(hba_stage)} {shlex.quote(hba_final)} &&
   [ "$hba_state" = root:postgres:640 ] &&
   [ "$hba_parent_state" = root:postgres:750 ] &&
   {checks} &&
   postgres --config-file="$config_file" -C listen_addresses | grep -Fx {shlex.quote(plan.settings["listen_addresses"])} >/dev/null &&
   postgres --config-file="$config_file" -C port | grep -Fx "$desired_port" >/dev/null &&
   postgres --config-file="$config_file" -C password_encryption | grep -Fx {shlex.quote(plan.settings["password_encryption"])} >/dev/null &&
   postgres --config-file="$config_file" -C hba_file | grep -Fx {shlex.quote(hba_final)} >/dev/null; then
  printf 'changed=0\\n'
else
  printf 'changed=1\\n'
fi
"""


def _cluster_refusal() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "postgresql",
        "PostgreSQL cluster selection is ambiguous or unsupported",
        changed=False,
        next_action="retain exactly one intended PostgreSQL cluster for the configured package track and port",
    )


class _PyinfraPostgreSQLOperations:
    """Native Ubuntu cluster configuration without prepare-time fact branching."""

    def packages(self, packages: tuple[str, ...]) -> None:
        from pyinfra.operations import apt

        apt.packages(packages=list(packages))

    def hba_file(self, content: str) -> None:
        from pyinfra.operations import files

        files.put(
            StringIO(content),
            "/etc/taskman/pg_hba.conf.staged",
            user="root",
            group="postgres",
            mode=0o640,
            add_deploy_dir=False,
        )

    def configure(self, plan: PostgreSQLPlan) -> None:
        conditional_convergence(
            probe=render_postgresql_native_configuration_probe(plan),
            script=render_postgresql_native_configuration_script(plan),
        )


__all__ = [
    "Database",
    "DatabaseRole",
    "ExistingDatabase",
    "ExistingRole",
    "ManagedFile",
    "PostgreSQLCluster",
    "PostgreSQLPlan",
    "apply_postgresql_native_configuration",
    "build_postgresql_plan",
    "converge_database",
    "converge_postgresql",
    "install_pgpass",
    "render_postgresql_native_configuration_script",
    "render_postgresql_native_configuration_probe",
    "render_role_password_input",
    "select_postgresql_cluster",
    "validate_existing_database_state",
]
