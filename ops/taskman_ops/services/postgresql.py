"""PostgreSQL desired state and guarded role/database adoption rules.

This module keeps declarative package/configuration state separate from the
one-time, secret-bearing role creation step.  The latter receives a password
only through sensitive standard input and is never prepared as a shell command
with the credential in its argv.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
from io import StringIO
import re
from types import MappingProxyType
from typing import Mapping

from pyinfra.api import operation
from pyinfra.api.command import FunctionCommand

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..remote import ChangeSet, CommandResult, Remote, merge_change_warnings


_POSTGRES_ADMIN_SOCKET = "/var/run/postgresql"
_PROTECTED_WRITE_CHANGED = 3


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
    cluster: SelectedPostgreSQLCluster | None = None
    resource_authority: tuple[str, str] | None = None


@dataclass(frozen=True)
class SelectedPostgreSQLCluster:
    """Immutable physical target supplied by privileged admission."""

    version: str
    name: str
    port: int
    data_directory: str
    config_file: str
    hba_file: str


@dataclass(frozen=True)
class PostgreSQLOwnershipPlan:
    """Confirmed resource scope and the selected physical PostgreSQL target."""

    branch: str
    cluster: SelectedPostgreSQLCluster | None
    role: str
    database: str
    access: str
    desired: PostgreSQLPlan

    def __post_init__(self) -> None:
        from ..host_protocol.postgresql import validate_postgresql_authority
        validate_postgresql_authority(
            {"branch": self.branch, "cluster": None if self.cluster is None else asdict(self.cluster),
             "role": self.role, "database": self.database, "access": self.access},
            package_track=self.desired.package_track, port=int(self.desired.settings["port"]),
            force_postgresql_config=self.branch == "configure-existing")
        if (self.desired.cluster != self.cluster or
                self.desired.resource_authority != (self.role, self.database)):
            raise ValueError("PostgreSQL desired target does not match ownership")


def build_postgresql_ownership_plan(
    config: EnvironmentConfig, observation: Mapping[str, object], *, force_postgresql_config: bool,
) -> PostgreSQLOwnershipPlan:
    """Bind closed host authority without rediscovering clusters on the controller."""
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("PostgreSQL ownership requires an environment configuration")
    from ..host_protocol.postgresql import validate_postgresql_authority
    selected = validate_postgresql_authority(observation, package_track=config.postgres_package_track,
        port=config.database_port, force_postgresql_config=force_postgresql_config)
    cluster = None if selected["cluster"] is None else SelectedPostgreSQLCluster(**selected["cluster"])
    return PostgreSQLOwnershipPlan(selected["branch"], cluster, selected["role"], selected["database"],
        selected["access"], replace(build_postgresql_plan(config), cluster=cluster,
            resource_authority=(selected["role"], selected["database"])))


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
        settings=MappingProxyType({
            "listen_addresses": config.database_host,
            "port": str(config.database_port),
            "password_encryption": "scram-sha-256",
        }),
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


def declare_postgresql(ownership: PostgreSQLOwnershipPlan, *, config: EnvironmentConfig | None = None) -> PostgreSQLPlan:
    """Declare only the native operations permitted by frozen ownership.

    Existing clusters return resource state without global operations. Bootstrap
    installs packages; explicit configuration authority uses the selected native
    cluster without packages. Resources converge after these operations execute.
    """

    from pyinfra.operations import apt, files

    if not isinstance(ownership, PostgreSQLOwnershipPlan):
        raise TypeError("PostgreSQL declaration requires frozen ownership authority")
    plan = ownership.desired
    if ownership.branch == "existing":
        return plan
    recovery_request = None
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("PostgreSQL configuration requires bound environment configuration")
    observation = {"branch": ownership.branch, "cluster": None if ownership.cluster is None else asdict(ownership.cluster),
        "role": ownership.role, "database": ownership.database, "access": ownership.access}
    if build_postgresql_ownership_plan(config, observation,
            force_postgresql_config=ownership.branch == "configure-existing") != ownership:
        raise ValueError("PostgreSQL configuration does not match environment configuration")
    if ownership.branch == "configure-existing":
        from ..host_protocol import HostRequest, PROTOCOL_VERSION
        from ..helper_client.runner import new_correlation_id
        recovery_request = HostRequest(PROTOCOL_VERSION, "postgresql_config_recovery", new_correlation_id(), {},
            {"install_root": config.install_root.as_posix(), "backup_root": config.backup_root.as_posix()},
            {"cluster": asdict(ownership.cluster)})
    if ownership.branch == "bootstrap":
        apt.packages(packages=list(plan.packages), name="Install PostgreSQL")
    files.put(
        StringIO(plan.hba),
        "/etc/taskman/pg_hba.conf.staged",
        user="root",
        group="postgres",
        mode="640",
        add_deploy_dir=False,
        name="Stage PostgreSQL HBA configuration",
    )
    _configure_postgresql_cluster(
        plan,
        name="Validate and configure PostgreSQL",
        recovery_request=recovery_request,
        config=config,
        _sudo=True,
    )
    return plan


def _configuration_request(plan, config, operation):
    from ..host_protocol.postgresql import validate_postgresql_configuration_request
    from ..workflows.helper import request, database_settings
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("PostgreSQL configuration requires bound environment configuration")
    return validate_postgresql_configuration_request(request(operation, config, parameters={
        "package_track": plan.package_track, "database": database_settings(config),
        "cluster": None if plan.cluster is None else asdict(plan.cluster),
        "hba_sha256": hashlib.sha256(plan.hba.encode()).hexdigest(),
    }))


@operation(is_idempotent=True)
def _configure_postgresql_cluster(plan: PostgreSQLPlan, *, recovery_request=None,
                                  config: EnvironmentConfig):
    """Inspect at execution time; let pyinfra account for executed functions."""
    from pyinfra.context import ctx_state, ctx_host
    state, host = ctx_state.get(), ctx_host.get()
    from ..remote import PyinfraRemote
    from ..workflows.helper import run_request, result_error

    inspection = _configuration_request(plan, config, "inspect_postgresql")
    configuration = _configuration_request(plan, config, "configure_postgresql")
    if state.is_executing:
        try:
            result = run_request(PyinfraRemote(host, config, state=state), inspection)
            state._taskman_warnings = merge_change_warnings(getattr(state, "_taskman_warnings", ()), result.warnings)
            if result.outcome != "succeeded":
                raise result_error(result)
            if not result.state["configuration_required"]:
                return
        except OpsError as error:
            setattr(state, "_taskman_categorized_error", error)
            raise
        except Exception:
            failure = OpsError(ExitStatus.REMOTE_PREFLIGHT, "postgresql", "PostgreSQL configuration inspection failed",
                changed=False, next_action="establish selected PostgreSQL native inspection access before retrying")
            setattr(state, "_taskman_categorized_error", failure)
            raise failure from None
    yield FunctionCommand(_run_postgresql_configuration, (configuration,),
        {"recovery_request": recovery_request, "config": config})


def _run_postgresql_configuration(state, host, configuration, *, recovery_request=None, config):
    """Keep retained capture independent of the later configuration reply."""
    from ..host_protocol.postgresql import validate_postgresql_recovery_request
    from ..remote import PyinfraRemote
    from ..workflows.helper import run_request, result_error, aggregate_mutation_state, merge_warnings

    recovery = None
    remote = PyinfraRemote(host, config, state=state)
    try:
        if recovery_request is not None:
            validate_postgresql_recovery_request(recovery_request)
            if (recovery_request.parameters["cluster"] != configuration.parameters["cluster"] or
                    recovery_request.paths != configuration.paths):
                raise ValueError("PostgreSQL recovery target mismatch")
            recovery = run_request(remote, recovery_request)
            if recovery.outcome != "succeeded":
                raise result_error(recovery)
            setattr(state, "_taskman_postgresql_recovery", recovery)
            state._taskman_warnings = merge_change_warnings(getattr(state, "_taskman_warnings", ()), recovery.warnings)
        result = run_request(remote, configuration,
            prior_mutation_state="changed" if recovery is not None else "unchanged")
        state._taskman_warnings = merge_change_warnings(getattr(state, "_taskman_warnings", ()), result.warnings)
        if result.outcome != "succeeded":
            raise result_error(result, prior_mutation_state="changed" if recovery is not None else "unchanged")
    except Exception as error:
        failure = error if isinstance(error, OpsError) else OpsError(
            ExitStatus.REMOTE_PREFLIGHT, "postgresql", "PostgreSQL configuration convergence failed",
            changed=True, state={"mutation_state": "unknown"},
            next_action="inspect selected PostgreSQL configuration and retained recovery evidence before retrying")
        if recovery is not None:
            mutation = aggregate_mutation_state(recovery.state["mutation_state"],
                failure.state.get("mutation_state", "unknown" if failure.changed else "unchanged"))
            failure.state = {**failure.state, "mutation_state": mutation,
                "recovery_directory": recovery.state["recovery_directory"], "postgresql_recovery": dict(recovery.state)}
            failure.changed = mutation != "unchanged"
            failure.warnings = merge_warnings(recovery.warnings, failure.warnings)
            failure.next_action = "inspect retained PostgreSQL recovery copies; HBA rollback does not restore all settings/runtime; restoration and cleanup require separate authorization"
        setattr(state, "_taskman_categorized_error", failure)
        raise failure from None


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
        "SET LOCAL password_encryption = 'scram-sha-256';\n"
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
    try:
        result = remote.run(
            (
                "sh",
                "-c",
                "exec >/dev/null 2>&1; set -eu; changed=0; stage=; "
                "finish() { status=$?; trap - EXIT HUP INT TERM; cleanup_status=0; "
                "if [ -n \"$stage\" ]; then rm -f -- \"$stage\" || cleanup_status=1; fi; "
                "if [ \"$status\" -ne 0 ] || [ \"$cleanup_status\" -ne 0 ]; then exit 1; fi; "
                f"if [ \"$changed\" -eq 1 ]; then exit {_PROTECTED_WRITE_CHANGED}; fi; exit 0; }}; "
                "trap finish EXIT; trap 'exit 1' HUP INT TERM; umask 077; "
                "stage=$(mktemp /tmp/taskman-pgpass.XXXXXX); cat > \"$stage\"; "
                "state=$(stat --format='%U:%G:%a' \"$1\" 2>/dev/null || true); "
                "if ! cmp -s \"$stage\" \"$1\" || [ \"$state\" != root:root:600 ]; then "
                "install -o root -g root -m 0600 \"$stage\" \"$1\"; changed=1; fi",
                "taskman-pgpass",
                "/etc/taskman/pgpass",
            ),
            sudo=True,
            stdin=content,
            sensitive=True,
        )
    except OpsError as error:
        error.changed = True
        raise
    changed = _changed_result(result, "unable to install protected PostgreSQL credentials")
    return ChangeSet(changed=changed, operations=("pgpass",) if changed else ())


def converge_database(
    remote: Remote,
    plan: PostgreSQLPlan,
    *,
    role_password_input: bytes,
    pgpass: bytes,
) -> ChangeSet:
    """Safely adopt or create database state after package convergence has run.

    This execution-time capability is deliberately separate from
    :func:`declare_postgresql`: pyinfra prepares package/configuration
    operations first, while role and database decisions may only read facts
    after that work has executed.  Existing incompatible authority is refused,
    and an already-rendered password exchange is consumed over sensitive stdin
    only for a newly created role.
    """

    if not isinstance(plan, PostgreSQLPlan):
        raise TypeError("database convergence requires a PostgreSQL plan")
    if not isinstance(role_password_input, bytes) or not role_password_input:
        raise ValueError("database role password input must be non-empty bytes")
    _validate_selected_resource_target(remote, plan)
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
    if plan.resource_authority is not None and plan.resource_authority != (
        "absent" if role is None else "compatible", "absent" if database is None else "compatible"
    ):
        raise OpsError(ExitStatus.SAFETY, "postgresql", "PostgreSQL resources changed before consequence",
            changed=False, next_action="inspect the selected resources and rerun provision for a new plan")
    _require_scram_client(remote)
    # Refuse contradictory authority before any persistent credential write.
    pgpass_result = install_pgpass(remote, pgpass)
    operations = list(pgpass_result.operations)
    resources = {"role": "retained" if role is not None else "absent",
                 "database": "retained" if database is not None else "absent",
                 "credentials": "installed" if pgpass_result.changed else "retained"}
    failed_check = "scram-application-access"
    try:
        if role is None:
            failed_check = "role-creation"
            resources["role"] = "creation-unproven"
            operations.append("role-attempt")
            _require_success(
                remote.run(
                    ("runuser", "-u", "postgres", "--", *plan.role_setup_argv),
                    sudo=True,
                    stdin=role_password_input,
                    sensitive=True,
                ),
                "unable to create and initialize PostgreSQL role",
            )
            operations.remove("role-attempt")
            operations.append("role")
            resources["role"] = "created"
        if database is None:
            failed_check = "database-creation"
            resources["database"] = "creation-unproven"
            operations.append("database-attempt")
            _require_success(
                remote.run(("runuser", "-u", "postgres", "--", *plan.database_creation_argv), sudo=True),
                "unable to create PostgreSQL database",
            )
            operations.remove("database-attempt")
            operations.append("database")
            resources["database"] = "created"
        failed_check = "scram-application-access"
        _verify_application_connection(remote, plan)
    except OpsError as error:
        if error.stage != "postgresql":
            error = OpsError(ExitStatus.REMOTE_PREFLIGHT, "postgresql",
                "PostgreSQL resource convergence reply could not be established",
                changed=error.changed, state=error.state, warnings=error.warnings,
                next_action="inspect retained PostgreSQL resources and credentials before retrying")
        error.state = {**error.state, "failed_check": failed_check, "postgresql_resources": resources}
        if operations:
            error.changed = True
            error.state = {**error.state, "mutation_state": "changed" if any(item in {"pgpass", "role", "database"} for item in operations) else "unknown"}
        raise error from None
    return ChangeSet(changed=bool(operations), operations=tuple(operations))


def _read_existing_role(remote: Remote, plan: PostgreSQLPlan) -> ExistingRole | None:
    query = (
        "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
        "rolreplication, rolbypassrls, rolinherit FROM pg_roles "
        f"WHERE rolname = '{plan.role.name}'"
    )
    result = _run_postgresql_admin_query(remote, plan, query, field_separator="|")
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
    result = _run_postgresql_admin_query(remote, plan, query)
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
    result = _run_postgresql_admin_query(remote, plan, query, field_separator="|")
    _require_success(result, "unable to inspect PostgreSQL database")
    fields = _one_record(result, 2, "PostgreSQL database inspection returned invalid data")
    if fields is None:
        return None
    return ExistingDatabase(*fields)


def _run_postgresql_admin_query(
    remote: Remote,
    plan: PostgreSQLPlan,
    query: str,
    *,
    field_separator: str | None = None,
) -> CommandResult:
    """Run one local peer-authenticated inspection query as PostgreSQL's admin."""

    arguments = (
        "runuser",
        "-u",
        "postgres",
        "--",
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
    )
    if field_separator is not None:
        arguments += ("--field-separator", field_separator)
    return remote.run(
        (
            *arguments,
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


def _require_scram_client(remote: Remote) -> None:
    """Inspect the host client after bootstrap installation and before resource writes."""
    try:
        result = remote.run(("psql", "--version"), sudo=True, timeout=60,
            stdout_limit=1024, stderr_limit=1024)
    except OpsError:
        raise _access_failure("PostgreSQL SCRAM client prerequisite could not be inspected", check="scram-client-prerequisite") from None
    version = re.fullmatch(r"psql \(PostgreSQL\) ([0-9]+)\.[^\r\n]+\n", result.stdout)
    if not result.succeeded or version is None or int(version[1]) < 16:
        raise _access_failure("PostgreSQL client must support require_auth (version 16 or later)", check="scram-client-prerequisite")


def _verify_application_connection(remote: Remote, plan: PostgreSQLPlan) -> None:
    from ..host_helper.database import scram_psql_argv
    database = {"host": plan.settings["listen_addresses"], "port": int(plan.settings["port"]),
                "role": plan.role.name, "name": plan.database.name}
    query = "SELECT current_user, current_database(), inet_server_addr(), inet_server_port()"
    try:
        result = remote.run(("env", "PGPASSFILE=/etc/taskman/pgpass", "LC_ALL=C",
            *scram_psql_argv(database, query=query)), sudo=True, timeout=60,
            stdout_limit=1024, stderr_limit=1024)
    except OpsError:
        raise _access_failure("PostgreSQL SCRAM application access proof failed") from None
    expected = f"{plan.role.name}|{plan.database.name}|{database['host']}|{database['port']}\n"
    if not result.succeeded or result.stdout != expected:
        raise _access_failure("PostgreSQL SCRAM application access proof failed")


def _access_failure(message: str, *, check: str = "scram-application-access") -> OpsError:
    return OpsError(ExitStatus.REMOTE_PREFLIGHT, "postgresql", message, changed=False,
        next_action="inspect selected-cluster SCRAM access and protected credentials, then rerun provision",
        state={"failed_check": check})


def _validate_selected_resource_target(remote: Remote, plan: PostgreSQLPlan) -> None:
    if plan.cluster is None:
        return
    cluster = plan.cluster
    query = "SELECT current_setting('data_directory'), current_setting('config_file'), current_setting('hba_file'), current_setting('port')"
    try:
        result = _run_postgresql_admin_query(remote, plan, query, field_separator="|")
    except OpsError as error:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "postgresql",
            "unable to revalidate selected PostgreSQL target",
            changed=error.changed,
            next_action="inspect the selected PostgreSQL administrator endpoint and service state, then rerun provision",
            state=error.state,
            warnings=error.warnings,
        ) from None
    _require_success(result, "unable to revalidate selected PostgreSQL target")
    expected = f"{cluster.data_directory}|{cluster.config_file}|{cluster.hba_file}|{cluster.port}\n"
    if result.stdout != expected:
        raise OpsError(ExitStatus.SAFETY, "postgresql", "selected PostgreSQL target changed before consequence",
            changed=False, next_action="inspect the selected target and rerun provision for a new plan")


def _require_success(result: CommandResult, message: str) -> None:
    if not result.succeeded:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "postgresql",
            message,
            changed=False,
            next_action="inspect PostgreSQL service state and retry the convergent operation",
        )


def _changed_result(result: CommandResult, operation: str) -> bool:
    if result.returncode == _PROTECTED_WRITE_CHANGED:
        return True
    if result.returncode == 0:
        return False
    try:
        _require_success(result, operation)
    except OpsError as error:
        error.changed = True
        raise
    return False


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


__all__ = [
    "Database",
    "DatabaseRole",
    "ExistingDatabase",
    "ExistingRole",
    "ManagedFile",
    "PostgreSQLPlan",
    "PostgreSQLOwnershipPlan",
    "SelectedPostgreSQLCluster",
    "build_postgresql_ownership_plan",
    "build_postgresql_plan",
    "converge_database",
    "declare_postgresql",
    "install_pgpass",
    "render_role_password_input",
    "validate_existing_database_state",
]
