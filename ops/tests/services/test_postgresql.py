from __future__ import annotations

from dataclasses import asdict, replace
import stat

from pathlib import Path
import pytest
from pyinfra.api import Config, Inventory, State, deploy
from pyinfra.api.deploy import add_deploy
from pyinfra.api.operations import run_ops

from tests.support.environments import environment_config
from taskman_ops.errors import ExitStatus, OpsError
import taskman_ops.services.postgresql as postgresql
from taskman_ops.services.postgresql import (
    ExistingDatabase,
    ExistingRole,
    build_postgresql_plan,
    converge_database,
    install_pgpass,
    render_role_password_input,
    validate_existing_database_state,
)
from taskman_ops.remote import CommandResult, PyinfraRemote
from taskman_ops.remote import ChangeSet
from tests.support.remotes import LocalProtectedRemote, ScriptedRemote


def test_postgresql_plan_binds_only_to_configured_loopback_and_enforces_scram() -> None:
    """Changing a public listener or weakening password encryption must fail this."""

    plan = build_postgresql_plan(environment_config())

    assert plan.packages == ("postgresql", "postgresql-client")
    assert plan.settings == {
        "listen_addresses": "127.0.0.1",
        "port": "5432",
        "password_encryption": "scram-sha-256",
    }
    assert plan.native_validation == (
        "postgres",
        "--config-file",
        "managed-cluster-config",
        "-C",
        "listen_addresses",
    )


def test_postgresql_plan_uses_an_explicit_requested_ubuntu_package_track() -> None:
    """Ignoring an accepted PostgreSQL package-track choice must fail this."""

    plan = build_postgresql_plan(environment_config(postgres_package_track="17"))

    assert plan.packages == ("postgresql-17", "postgresql-client-17")


def test_postgresql_plan_uses_the_validated_database_port_for_listener_and_application_connection() -> None:
    """Hard-coding PostgreSQL's default port would break an accepted configured topology."""

    plan = build_postgresql_plan(environment_config(database_port=5433))

    assert plan.settings["port"] == "5433"


def test_postgresql_keeps_package_and_staged_hba_convergence_built_in(monkeypatch) -> None:
    """A generic conditional wrapper must not own ordinary package or staged-file state."""

    package_calls: list[dict[str, object]] = []
    staged: list[tuple[str, str]] = []
    configured: list[object] = []
    from pyinfra.operations import apt, files

    monkeypatch.setattr(apt, "packages", lambda **kwargs: package_calls.append(kwargs))
    monkeypatch.setattr(
        files,
        "put",
        lambda source, destination, **_kwargs: staged.append((source.getvalue(), destination)),
    )
    monkeypatch.setattr(
        postgresql,
        "_configure_postgresql_cluster",
        lambda plan, **_kwargs: configured.append(plan),
        raising=False,
    )
    monkeypatch.setattr(
        postgresql,
        "conditional_convergence",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("PostgreSQL must not use generic convergence")),
        raising=False,
    )

    ownership = postgresql.build_postgresql_ownership_plan(environment_config(),
        _ownership_observation(branch="bootstrap"), force_postgresql_config=False)
    expected = ownership.desired
    assert postgresql.declare_postgresql(ownership, config=environment_config()) == expected

    assert package_calls == [{"packages": list(expected.packages), "name": "Install PostgreSQL"}]
    assert staged == [(expected.hba, "/etc/taskman/pg_hba.conf.staged")]
    assert configured == [expected]


def test_postgresql_applies_staged_hba_mode_through_actual_pyinfra_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Pyinfra applies the staged HBA file's intended 0640 permission bits."""

    remote_root = tmp_path / "remote"
    config = environment_config()
    from pyinfra.operations import apt, files

    original_put = files.put

    def remote_path(path: str) -> str:
        return (remote_root / path.lstrip("/")).as_posix()

    def put(source: object, destination: str, **kwargs: object) -> object:
        return original_put(
            source,
            remote_path(destination),
            **{**kwargs, "user": None, "group": None},
        )

    monkeypatch.setattr(apt, "packages", lambda **_kwargs: None)
    monkeypatch.setattr(files, "put", put)
    monkeypatch.setattr(postgresql, "_configure_postgresql_cluster", lambda *_args, **_kwargs: None)

    @deploy("PostgreSQL mode semantics")
    def converge() -> None:
        postgresql.declare_postgresql(postgresql.build_postgresql_ownership_plan(config, _ownership_observation(branch="bootstrap"), force_postgresql_config=False), config=config)

    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    state.activate_host(inventory.get_host("@local"))
    add_deploy(state, converge)
    run_ops(state)

    staged_path = Path(remote_path("/etc/taskman/pg_hba.conf.staged"))
    assert stat.S_IMODE(staged_path.stat().st_mode) == 0o640


def test_postgresql_plan_has_a_least_authority_role_database_and_root_only_pgpass() -> None:
    """Granting elevated database authority or relaxing pgpass permissions must fail this."""

    plan = build_postgresql_plan(environment_config())

    assert plan.role.name == "taskman"
    assert plan.role.login is True
    assert plan.role.superuser is False
    assert plan.role.createdb is False
    assert plan.role.createrole is False
    assert plan.role.replication is False
    assert plan.role.bypassrls is False
    assert plan.database.name == "taskman_prod"
    assert plan.database.owner == "taskman"
    assert (plan.pgpass.path, plan.pgpass.owner, plan.pgpass.group, plan.pgpass.mode) == (
        "/etc/taskman/pgpass",
        "root",
        "root",
        0o600,
    )


def test_role_password_input_is_sensitive_stdin_not_a_password_argument() -> None:
    """Moving the password to a command argument must fail this secret-boundary test."""

    plan = build_postgresql_plan(environment_config())
    password = "postgres-sensitive-canary"

    rendered = render_role_password_input(plan.role, password)

    assert rendered == (
        b"\\set ON_ERROR_STOP on\n"
        b"BEGIN;\n"
        b"SET LOCAL password_encryption = 'scram-sha-256';\n"
        b"CREATE ROLE taskman LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;\n"
        b"\\password taskman\npostgres-sensitive-canary\npostgres-sensitive-canary\nCOMMIT;\n"
    )
    assert password not in plan.role_setup_argv
    assert plan.role_setup_argv == (
        "psql",
        "--no-psqlrc",
        "--set=ON_ERROR_STOP=1",
        "--host",
        "/var/run/postgresql",
        "--port",
        "5432",
        "--username",
        "postgres",
        "--dbname=postgres",
    )


def test_database_convergence_uses_peer_socket_administration_but_loopback_tcp_for_the_application() -> None:
    """The managed HBA permits postgres only through local peer authentication."""

    plan = build_postgresql_plan(environment_config(database_port=5433))
    remote = _PeerOnlyAdministrativeRemote()

    converge_database(
        remote,
        plan,
        role_password_input=render_role_password_input(plan.role, "database-sensitive-canary"),
        pgpass=b"pgpass-sensitive-canary\n",
    )

    assert remote.administrative_connections == [
        ("/var/run/postgresql", "5433"),
        ("/var/run/postgresql", "5433"),
        ("/var/run/postgresql", "5433"),
        ("/var/run/postgresql", "5433"),
    ]
    assert remote.application_connections == [("127.0.0.1", "5433")]


@pytest.mark.parametrize(
    ("role", "database"),
    [
        (ExistingRole("taskman", False, False, False, False, False, False), None),
        (ExistingRole("taskman", True, True, False, False, False, False), None),
        (ExistingRole("taskman", True, False, True, False, False, False), None),
        (ExistingRole("taskman", True, False, False, True, False, False), None),
        (ExistingRole("taskman", True, False, False, False, True, False), None),
        (ExistingRole("taskman", True, False, False, False, False, True), None),
        (ExistingRole("taskman", True, False, False, False, False, False), ExistingDatabase("taskman_prod", "postgres")),
    ],
)
def test_existing_incompatible_role_or_database_is_a_safety_refusal(
    role: ExistingRole, database: ExistingDatabase | None
) -> None:
    """Replacing a role/database with contradictory ownership or authority must fail this."""

    with pytest.raises(OpsError) as raised:
        validate_existing_database_state(
            build_postgresql_plan(environment_config()), role=role, database=database
        )

    assert raised.value.status is ExitStatus.SAFETY


def test_existing_compatible_role_and_database_are_adopted_idempotently() -> None:
    """Rejecting an already-correct database would break a provision rerun."""

    plan = build_postgresql_plan(environment_config())

    validate_existing_database_state(
        plan,
        role=ExistingRole("taskman", True, False, False, False, False, False),
        database=ExistingDatabase("taskman_prod", "taskman"),
    )


def test_existing_role_memberships_are_part_of_the_least_authority_adoption_check() -> None:
    plan = build_postgresql_plan(environment_config())

    with pytest.raises(OpsError) as raised:
        validate_existing_database_state(
            plan,
            role=ExistingRole("taskman", True, False, False, False, False, False, memberships=("analytics",)),
            database=ExistingDatabase("taskman_prod", "taskman"),
        )

    assert raised.value.status is ExitStatus.SAFETY


def test_database_convergence_creates_a_missing_least_authority_role_and_database_with_sensitive_stdin() -> None:
    """Passing a password in argv, or missing role/database creation, must fail this."""

    remote = ScriptedRemote.from_responses(
        [
            CommandResult(0),
            CommandResult(0),
            CommandResult(0, "psql (PostgreSQL) 18.6\n"),
            CommandResult(3),
            CommandResult(0),
            CommandResult(0),
            CommandResult(0, "taskman|taskman_prod|127.0.0.1|5432\n"),
        ]
    )
    plan = build_postgresql_plan(environment_config())
    canary = "database-sensitive-canary"

    converge_database(
        remote,
        plan,
        role_password_input=render_role_password_input(plan.role, canary),
        pgpass=b"pgpass-sensitive-canary\n",
    )

    commands = [argv for argv, _kwargs in remote.calls]
    assert commands[4][-len(plan.role_setup_argv) :] == plan.role_setup_argv
    assert commands[5][-len(plan.database_creation_argv) :] == plan.database_creation_argv
    assert "dbname=taskman_prod require_auth=scram-sha-256" in commands[-1]
    assert commands[-1][-1] == "SELECT current_user, current_database(), inet_server_addr(), inet_server_port()"
    assert all(canary not in " ".join(command) for command in commands)
    assert remote.calls[4][1]["stdin"] == render_role_password_input(plan.role, canary)
    assert remote.calls[4][1]["sensitive"] is True


def test_database_safety_refusal_happens_before_pgpass_or_role_mutation() -> None:
    """A contradictory existing authority must leave password files and roles untouched."""

    remote = ScriptedRemote.from_responses(
        [
            CommandResult(0, "taskman|t|t|f|f|f|f|t\n"),
            CommandResult(0, ""),
            CommandResult(0, "taskman_prod|taskman\n"),
        ]
    )

    with pytest.raises(OpsError) as raised:
        plan = build_postgresql_plan(environment_config())
        converge_database(
            remote,
            plan,
            role_password_input=render_role_password_input(plan.role, "database-sensitive-canary"),
            pgpass=b"pgpass-sensitive-canary\n",
        )

    assert raised.value.status is ExitStatus.SAFETY
    assert all(command[0] != "sh" for command, _kwargs in remote.calls)


def test_existing_compatible_database_rerun_only_repairs_pgpass_and_verifies_the_application_connection() -> None:
    """Recreating an existing compatible role/database would make reruns non-idempotent."""

    remote = ScriptedRemote.from_responses(
        [
            CommandResult(0, "taskman|t|f|f|f|f|f|t\n"),
            CommandResult(0, ""),
            CommandResult(0, "taskman_prod|taskman\n"),
            CommandResult(0, "psql (PostgreSQL) 18.6\n"),
            CommandResult(0),
            CommandResult(0, "taskman|taskman_prod|127.0.0.1|5432\n"),
        ]
    )

    plan = build_postgresql_plan(environment_config())
    result = converge_database(
        remote,
        plan,
        role_password_input=render_role_password_input(plan.role, "database-sensitive-canary"),
        pgpass=b"pgpass-sensitive-canary\n",
    )

    assert result == ChangeSet(changed=False, operations=())
    commands = [argv for argv, _kwargs in remote.calls]
    assert not any("createuser" in command or "createdb" in command for command in commands)
    assert not any(command[-1:] == ("psql",) for command in commands)


def test_pgpass_installer_uses_a_non_sensitive_exit_status_receipt() -> None:
    """A sensitive transport that suppresses streams still reports a changed write."""

    remote = ScriptedRemote.from_responses([CommandResult(3)])
    canary = b"pgpass-sensitive-canary\n"

    result = install_pgpass(remote, canary)

    assert result == ChangeSet(changed=True, operations=("pgpass",))
    command, kwargs = remote.calls[0]
    assert command[-1] == "/etc/taskman/pgpass"
    assert "pgpass-sensitive-canary" not in " ".join(command)
    assert kwargs["stdin"] == canary
    assert kwargs["sensitive"] is True


def test_pgpass_installer_performs_actual_idempotent_and_mode_repairing_writes(tmp_path: Path) -> None:
    """Protected writes change on first install and mode drift, but not on a rerun."""

    destination = tmp_path / "pgpass"
    remote = LocalProtectedRemote(destination, tmp_path)
    content = b"127.0.0.1:5432:taskman_prod:taskman:secret\n"

    first = install_pgpass(remote, content)
    second = install_pgpass(remote, content)
    destination.chmod(0o644)
    repaired = install_pgpass(remote, content)

    assert first == ChangeSet(changed=True, operations=("pgpass",))
    assert second == ChangeSet(changed=False, operations=())
    assert repaired == ChangeSet(changed=True, operations=("pgpass",))
    assert destination.read_bytes() == content
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert remote.results == [(3, b"", b""), (0, b"", b""), (3, b"", b"")]


def test_pgpass_installer_normalizes_actual_remote_write_errors(tmp_path: Path) -> None:
    """A protected shell failure is generic, non-success, and stream-free."""

    destination = tmp_path / "missing" / "pgpass"
    remote = LocalProtectedRemote(destination, tmp_path)

    with pytest.raises(OpsError) as raised:
        install_pgpass(remote, b"pgpass-sensitive-canary\n")

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.changed is True
    assert remote.results == [(1, b"", b"")]


@pytest.mark.parametrize("failure", ("cleanup", "signal"))
def test_pgpass_installer_marks_possible_mutation_for_protected_failures(
    tmp_path: Path, *, failure: str
) -> None:
    """Cleanup and signal failures after replacement retain conservative change evidence."""

    destination = tmp_path / "pgpass"
    remote = LocalProtectedRemote(destination, tmp_path, failure=failure)
    content = b"127.0.0.1:5432:taskman_prod:taskman:secret\n"

    with pytest.raises(OpsError) as raised:
        install_pgpass(remote, content)

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.changed is True
    assert destination.read_bytes() == content
    assert remote.results == [(1, b"", b"")]


def test_pgpass_installer_preserves_a_structured_transport_error(tmp_path: Path) -> None:
    """A transport error stays intact while recording possible protected mutation."""

    error = OpsError(
        ExitStatus.SECRET,
        "transport",
        "protected transport failed",
        changed=False,
        next_action="retry the protected operation",
        state={"boundary": "pgpass"},
        warnings=("transport warning",),
    )
    remote = ScriptedRemote.from_responses([error])

    with pytest.raises(OpsError) as raised:
        install_pgpass(remote, b"pgpass-sensitive-canary\n")

    assert raised.value is error
    assert raised.value.status is ExitStatus.SECRET
    assert raised.value.stage == "transport"
    assert raised.value.message == "protected transport failed"
    assert raised.value.next_action == "retry the protected operation"
    assert raised.value.state == {"boundary": "pgpass"}
    assert raised.value.warnings == ("transport warning",)
    assert raised.value.changed is True


def test_database_convergence_preserves_pgpass_change_when_role_creation_fails() -> None:
    """A later protected database failure must retain evidence of a changed pgpass."""

    remote = ScriptedRemote.from_responses(
        [
            CommandResult(0, ""),
            CommandResult(0, ""),
            CommandResult(0, "psql (PostgreSQL) 18.6\n"),
            CommandResult(3),
            CommandResult(1),
        ]
    )
    plan = build_postgresql_plan(environment_config())

    with pytest.raises(OpsError) as raised:
        converge_database(
            remote,
            plan,
            role_password_input=render_role_password_input(plan.role, "database-sensitive-canary"),
            pgpass=b"pgpass-sensitive-canary\n",
        )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.changed is True


def test_database_convergence_preserves_role_change_when_database_creation_fails() -> None:
    """A completed role write must remain visible when the next write fails."""

    remote = ScriptedRemote.from_responses(
        [
            CommandResult(0, ""),
            CommandResult(0, ""),
            CommandResult(0, "psql (PostgreSQL) 18.6\n"),
            CommandResult(0),
            CommandResult(0),
            CommandResult(1),
        ]
    )
    plan = build_postgresql_plan(environment_config())

    with pytest.raises(OpsError) as raised:
        converge_database(
            remote,
            plan,
            role_password_input=render_role_password_input(plan.role, "database-sensitive-canary"),
            pgpass=b"pgpass-sensitive-canary\n",
        )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.changed is True


class _PeerOnlyAdministrativeRemote:
    """Representative HBA: postgres peer works only on the Unix socket."""

    def __init__(self) -> None:
        self.administrative_connections: list[tuple[str, str]] = []
        self.application_connections: list[tuple[str, str]] = []

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        command = " ".join(argv)
        if argv == ("psql", "--version"):
            return CommandResult(0, "psql (PostgreSQL) 18.6\n")
        if "taskman-pgpass" in argv:
            return CommandResult(3)
        if "--username postgres" in command:
            host = argv[argv.index("--host") + 1]
            port = argv[argv.index("--port") + 1]
            if host != "/var/run/postgresql":
                return CommandResult(1, stderr="no pg_hba.conf entry for host connection")
            self.administrative_connections.append((host, port))
            if "FROM pg_roles" in command or "FROM pg_database" in command:
                return CommandResult(0, "")
            return CommandResult(0)
        if "--username taskman" in command:
            host = argv[argv.index("--host") + 1]
            port = argv[argv.index("--port") + 1]
            self.application_connections.append((host, port))
            return CommandResult(0, f"taskman|taskman_prod|{host}|{port}\n")
        return CommandResult(0)


def _ownership_observation(*, branch="existing", role="absent", database="absent"):
    return {"branch": branch, "role": role, "database": database, "access": "unproven",
        "cluster": None if branch == "bootstrap" else {"version": "18", "name": "main", "port": 5432,
            "data_directory": "/var/lib/postgresql/18/main", "config_file": "/etc/postgresql/18/main/postgresql.conf",
            "hba_file": "/etc/postgresql/18/main/pg_hba.conf"}}


def test_existing_postgresql_declares_no_pyinfra_operations():
    """Existing installations, including previously bootstrapped ones, never gain global authority."""
    ownership = postgresql.build_postgresql_ownership_plan(environment_config(),
        _ownership_observation(role="compatible", database="compatible"), force_postgresql_config=False)
    @deploy("Observe PostgreSQL declaration")
    def converge():
        postgresql.declare_postgresql(ownership)
    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    state.activate_host(inventory.get_host("@local"))
    add_deploy(state, converge)
    assert all(not operations for operations in state.ops.values())


def test_postgresql_declaration_rejects_unobserved_configuration():
    with pytest.raises(TypeError):
        postgresql.declare_postgresql(environment_config())


def test_application_proof_requires_scram_and_exact_identity_without_native_diagnostics():
    remote = ScriptedRemote.from_responses([CommandResult(0, "taskman|taskman_prod|127.0.0.1|5432\n")])
    postgresql._verify_application_connection(remote, build_postgresql_plan(environment_config()))
    argv, kwargs = remote.calls[0]
    assert "dbname=taskman_prod require_auth=scram-sha-256" in argv
    assert "--no-password" in argv
    assert kwargs["timeout"] == 60 and kwargs["stdout_limit"] == 1024
    wrong = ScriptedRemote.from_responses([CommandResult(0, "taskman|foreign|127.0.0.1|5432\n", "secret-canary")])
    with pytest.raises(OpsError) as raised:
        postgresql._verify_application_connection(wrong, build_postgresql_plan(environment_config()))
    assert raised.value.status == ExitStatus.REMOTE_PREFLIGHT
    assert "secret-canary" not in str(raised.value)


def test_role_only_adoption_preserves_password_and_creates_database():
    remote = ScriptedRemote.from_responses([
        CommandResult(0, "taskman|t|f|f|f|f|f|t\n"), CommandResult(0), CommandResult(0),
        CommandResult(0, "psql (PostgreSQL) 18.6\n"), CommandResult(0), CommandResult(0),
        CommandResult(0, "taskman|taskman_prod|127.0.0.1|5432\n")])
    plan = build_postgresql_plan(environment_config())
    result = converge_database(remote, plan, role_password_input=render_role_password_input(plan.role, "secret"), pgpass=b"pgpass\n")
    assert result.operations == ("database",)
    assert not any(kwargs.get("stdin") == render_role_password_input(plan.role, "secret") for _, kwargs in remote.calls)


@pytest.mark.parametrize("late", [CommandResult(1, stderr="password-secret"), OpsError(ExitStatus.REMOTE_PREFLIGHT, "transport", "reply lost", changed=False)])
def test_late_access_failure_retains_created_resources(late):
    remote = ScriptedRemote.from_responses([CommandResult(0), CommandResult(0),
        CommandResult(0, "psql (PostgreSQL) 18.6\n"), CommandResult(3), CommandResult(0), CommandResult(0), late])
    plan = build_postgresql_plan(environment_config())
    with pytest.raises(OpsError) as raised:
        converge_database(remote, plan, role_password_input=render_role_password_input(plan.role, "secret"), pgpass=b"pgpass\n")
    assert raised.value.status == ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.stage == "postgresql"
    assert raised.value.changed is True
    assert raised.value.state["mutation_state"] == "changed"
    assert not any("dropdb" in argv for argv, _ in remote.calls)
    assert raised.value.state["failed_check"] == "scram-application-access"
    assert raised.value.state["postgresql_resources"] == {"role": "created", "database": "created", "credentials": "installed"}
    assert "password-secret" not in repr(raised.value.state)
    assert "reply lost" not in repr(raised.value.state)


def test_resource_drift_refuses_before_credential_installation():
    ownership = postgresql.build_postgresql_ownership_plan(environment_config(), _ownership_observation(), force_postgresql_config=False)
    remote = ScriptedRemote.from_responses([
        CommandResult(0, "/var/lib/postgresql/18/main|/etc/postgresql/18/main/postgresql.conf|/etc/postgresql/18/main/pg_hba.conf|5432\n"),
        CommandResult(0, "taskman|t|f|f|f|f|f|t\n"), CommandResult(0), CommandResult(0),
        CommandResult(0, "psql (PostgreSQL) 18.6\n"), CommandResult(0), CommandResult(0),
        CommandResult(0, "taskman|taskman_prod|127.0.0.1|5432\n")])
    with pytest.raises(OpsError) as raised:
        converge_database(remote, ownership.desired, role_password_input=render_role_password_input(ownership.desired.role, "secret"), pgpass=b"pgpass\n")
    assert raised.value.status == ExitStatus.SAFETY
    assert not any("sh" == argv[0] for argv, _ in remote.calls)


def test_unsupported_native_client_refuses_before_pgpass_and_resource_creation():
    remote = ScriptedRemote.from_responses([CommandResult(0), CommandResult(0), CommandResult(0, "psql (PostgreSQL) 15.9\n")])
    plan = build_postgresql_plan(environment_config())
    with pytest.raises(OpsError) as raised:
        converge_database(remote, plan, role_password_input=render_role_password_input(plan.role, "secret"), pgpass=b"pgpass\n")
    assert raised.value.status == ExitStatus.REMOTE_PREFLIGHT
    assert "require_auth" in raised.value.message
    assert raised.value.state["failed_check"] == "scram-client-prerequisite"
    assert not any(argv[0] == "sh" or "createdb" in argv for argv, _ in remote.calls)


@pytest.mark.parametrize("mutation", ["unchanged", "unknown"])
def test_selected_target_transport_failure_is_postgresql_inspection_with_preserved_evidence(mutation):
    ownership = postgresql.build_postgresql_ownership_plan(environment_config(),
        _ownership_observation(), force_postgresql_config=False)
    transport = OpsError(ExitStatus.REMOTE_PREFLIGHT, "remote", "native-diagnostic-canary",
        changed=mutation != "unchanged", state={"mutation_state": mutation, "inspection_dispatched": True},
        warnings=("bounded transport cleanup was incomplete",))
    remote = ScriptedRemote.from_responses([transport])
    with pytest.raises(OpsError) as refused:
        converge_database(remote, ownership.desired,
            role_password_input=render_role_password_input(ownership.desired.role, "secret"), pgpass=b"pgpass\n")
    assert refused.value.status == ExitStatus.REMOTE_PREFLIGHT
    assert refused.value.stage == "postgresql"
    assert refused.value.message == "unable to revalidate selected PostgreSQL target"
    assert "selected PostgreSQL" in refused.value.next_action
    assert refused.value.changed is (mutation != "unchanged")
    assert refused.value.state == transport.state
    assert refused.value.warnings == transport.warnings
    assert "native-diagnostic-canary" not in str(refused.value)
    assert len(remote.calls) == 1


def test_override_declaration_requires_bound_environment_and_excludes_packages(monkeypatch):
    from pyinfra.operations import apt, files
    config = environment_config()
    ownership = postgresql.build_postgresql_ownership_plan(config,
        _ownership_observation(branch="configure-existing"), force_postgresql_config=True)
    with pytest.raises((TypeError, ValueError)):
        postgresql.declare_postgresql(ownership)
    native = []
    staged = []
    monkeypatch.setattr(apt, "packages", lambda **_: pytest.fail("existing override cannot install packages"))
    monkeypatch.setattr(files, "put", lambda source, path, **kwargs: staged.append((source.getvalue(), path, kwargs)))
    monkeypatch.setattr(postgresql, "_configure_postgresql_cluster", lambda plan, **kwargs: native.append(kwargs))
    postgresql.declare_postgresql(ownership, config=config)
    assert len(staged) == len(native) == 1
    content, path, options = staged[0]
    assert content == ownership.desired.hba
    assert path == "/etc/taskman/pg_hba.conf.staged"
    assert (options["user"], options["group"], options["mode"]) == ("root", "postgres", "640")
    assert native[0]["recovery_request"].parameters["cluster"]["version"] == "18"
    assert native[0]["recovery_request"].paths["install_root"] == config.install_root.as_posix()
    with pytest.raises(ValueError):
        postgresql.declare_postgresql(ownership, config=environment_config(database_port=5433))


def test_lost_role_creation_reply_does_not_claim_created_database_resources():
    remote = ScriptedRemote.from_responses([CommandResult(0), CommandResult(0),
        CommandResult(0, "psql (PostgreSQL) 18.6\n"), CommandResult(0),
        OpsError(5, "remote", "lost reply")])
    plan = build_postgresql_plan(environment_config())
    with pytest.raises(OpsError) as raised:
        converge_database(remote, plan, role_password_input=render_role_password_input(plan.role, "synthetic-secret"), pgpass=b"synthetic-passfile\n")
    assert raised.value.changed
    assert raised.value.state["mutation_state"] == "unknown"
    assert raised.value.state["postgresql_resources"] == {"role": "creation-unproven", "database": "absent", "credentials": "retained"}


@pytest.mark.parametrize("branch,required,configured", [
    ("bootstrap", True, True), ("bootstrap", False, False),
    ("configure-existing", True, False), ("configure-existing", False, False),
])
def test_pyinfra_configuration_uses_helper_on_same_host_state(monkeypatch, branch, required, configured):
    from taskman_ops.workflows import helper
    from taskman_ops.host_protocol import HostResult
    from tests.host_helper.test_postgresql_recovery import recovery_request, recovery_result
    config = environment_config()
    ownership = postgresql.build_postgresql_ownership_plan(config, _ownership_observation(branch=branch),
        force_postgresql_config=branch == "configure-existing")
    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host("@local")
    state.activate_host(host)
    calls = []
    def invoke(remote, request, **kwargs):
        assert remote._host is host
        assert remote._state is state
        assert remote._config is config
        calls.append(request.operation)
        if request.operation == "inspect_postgresql":
            return HostResult.for_request(request, "succeeded", "checked", {"changed": False, "configuration_required": required})
        if request.operation == "postgresql_config_recovery":
            assert request.parameters["cluster"] == calls_target
            return replace(recovery_result(request), warnings=("transient capture cleanup was incomplete",))
        assert request.operation == "configure_postgresql"
        assert request.parameters["cluster"] == calls_target
        return HostResult.for_request(request, "succeeded", "configured", {"changed": configured})
    calls_target = None if branch == "bootstrap" else asdict(ownership.cluster)
    monkeypatch.setattr(helper, "run_request", invoke)
    @deploy("Configure PostgreSQL")
    def converge():
        postgresql._configure_postgresql_cluster(ownership.desired, config=config, _sudo=False,
            recovery_request=None if branch == "bootstrap" else recovery_request())
    remote = PyinfraRemote(host, config, inventory=inventory, state=state)
    changes = remote.run_deploy(converge)
    assert changes.changed is required
    if branch == "configure-existing" and required:
        assert "transient capture cleanup was incomplete" in changes.warnings
    assert calls == (["inspect_postgresql", *(["postgresql_config_recovery"] if branch == "configure-existing" else []), "configure_postgresql"] if required else ["inspect_postgresql"])
    assert (getattr(state, "_taskman_postgresql_recovery", None) is not None) == (branch == "configure-existing" and required)


def test_bootstrap_declaration_prepares_without_helper_then_executes_packages_stage_inspect(monkeypatch):
    from pyinfra.api import operation
    from pyinfra.api.command import FunctionCommand
    from pyinfra.operations import apt, files
    from taskman_ops.workflows import helper
    from taskman_ops.host_protocol import HostResult
    events = []
    @operation(is_idempotent=True)
    def package(**kwargs):
        yield FunctionCommand(lambda: events.append('packages'), (), {})
    @operation(is_idempotent=True)
    def stage(*args, **kwargs):
        yield FunctionCommand(lambda: events.append('stage'), (), {})
    monkeypatch.setattr(apt, 'packages', package)
    monkeypatch.setattr(files, 'put', stage)
    def invoke(remote, request, **kwargs):
        events.append(request.operation)
        assert events[:2] == ['packages', 'stage']
        return HostResult.for_request(request, 'succeeded', 'checked',
            {'changed': False, 'configuration_required': False}, ('transient helper cleanup was incomplete',))
    monkeypatch.setattr(helper, 'run_request', invoke)
    config = environment_config()
    ownership = postgresql.build_postgresql_ownership_plan(config, _ownership_observation(branch='bootstrap'),
        force_postgresql_config=False)
    inventory = Inventory((['@local'], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host('@local')
    state.activate_host(host)
    @deploy('Bootstrap PostgreSQL')
    def converge():
        postgresql.declare_postgresql(ownership, config=config)
    add_deploy(state, converge)
    assert events == []
    run_ops(state)
    assert events == ['packages', 'stage', 'inspect_postgresql']
    assert state._taskman_warnings == ('transient helper cleanup was incomplete',)


@pytest.mark.parametrize('failure', ['capture', 'configure', 'loss'])
def test_override_failure_keeps_capture_category_and_prevents_unauthorized_followup(monkeypatch, failure):
    from types import SimpleNamespace
    from tests.host_helper.test_postgresql_recovery import recovery_request, recovery_result, DIRECTORY
    from taskman_ops.workflows import helper
    from taskman_ops.host_protocol import HostResult
    config = environment_config()
    ownership = postgresql.build_postgresql_ownership_plan(config,
        _ownership_observation(branch='configure-existing'), force_postgresql_config=True)
    configuration = postgresql._configuration_request(ownership.desired, config, 'configure_postgresql')
    events = []
    def invoke(remote, request, **kwargs):
        events.append(request.operation)
        if request.operation == 'postgresql_config_recovery':
            return recovery_result(request, mutation='unchanged', directory=None, exit_code=10) if failure == 'capture' else recovery_result(request)
        if failure == 'loss':
            raise OpsError(5, 'postgresql', 'lost', changed=True, state={'mutation_state': 'unknown'}, warnings=('transient helper cleanup was incomplete',))
        return HostResult.for_request(request, 'manual', 'failed', dict(exit_code=5, changed=True,
            mutation_state='changed', reason='command_failed', failed_boundary='postgresql'))
    monkeypatch.setattr(helper, 'run_request', invoke)
    state = SimpleNamespace()
    with pytest.raises(OpsError) as caught:
        postgresql._run_postgresql_configuration(state, object(), configuration,
            recovery_request=recovery_request(), config=config)
    assert caught.value.status == (10 if failure == 'capture' else 5)
    assert events == (['postgresql_config_recovery'] if failure == 'capture' else ['postgresql_config_recovery', 'configure_postgresql'])
    if failure != 'capture':
        assert caught.value.state['mutation_state'] == 'changed'
        assert caught.value.state['recovery_directory'] == DIRECTORY
        assert state._taskman_postgresql_recovery.state['recovery_directory'] == DIRECTORY
    if failure == 'loss':
        assert 'transient helper cleanup was incomplete' in caught.value.warnings
    assert state._taskman_categorized_error is caught.value


def test_inspection_failure_is_categorized_before_capture_in_actual_pyinfra(monkeypatch):
    from taskman_ops.workflows import helper
    from tests.host_helper.test_postgresql_recovery import recovery_request
    config = environment_config()
    ownership = postgresql.build_postgresql_ownership_plan(config,
        _ownership_observation(branch='configure-existing'), force_postgresql_config=True)
    def fail(_remote, request, **kwargs):
        assert request.operation == 'inspect_postgresql'
        raise OpsError(12, 'postgresql', 'locked', changed=False,
            state={'mutation_state': 'unchanged'}, warnings=('transient helper cleanup was incomplete',))
    monkeypatch.setattr(helper, 'run_request', fail)
    inventory = Inventory((['@local'], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host('@local')
    state.activate_host(host)
    @deploy('Inspect PostgreSQL')
    def converge():
        postgresql._configure_postgresql_cluster(ownership.desired, config=config,
            recovery_request=recovery_request(), _sudo=False)
    with pytest.raises(OpsError) as caught:
        PyinfraRemote(host, config, inventory=inventory, state=state).run_deploy(converge)
    assert caught.value.status == 12
    assert caught.value.stage == 'postgresql'
    assert 'transient helper cleanup was incomplete' in caught.value.warnings
    assert getattr(state, '_taskman_postgresql_recovery', None) is None
