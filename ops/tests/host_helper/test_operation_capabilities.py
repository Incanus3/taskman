"""Focused contracts for reusable host-operation capabilities."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from tests.host_helper.support import database_mapping

from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_protocol import HostRequest


def _capability(name: str):
    qualified = f"taskman_ops.host_helper.{name}"
    assert importlib.util.find_spec(qualified) is not None
    return importlib.import_module(qualified)


def test_database_observation_accepts_an_empty_schema_only_for_first_release(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating an absent migration table as normal during a rollback would hide lost authority."""

    database = _capability("database")
    commands = iter((b"", b"1\n", b"1\n", b"20260905120000\n"))

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, next(commands), b"")

    monkeypatch.setattr(database, "run_command", run)

    assert database.observe_database_state_or_empty(database_mapping(), Path("/etc/taskman/pgpass")) == {
        "state": "ready",
        "applied_migrations": (),
        "initial_empty": True,
    }
    assert database.observe_database_state(database_mapping(), Path("/etc/taskman/pgpass")) == {
        "state": "ready",
        "applied_migrations": (20260905120000,),
    }


def test_initial_database_observation_refuses_populated_schema_without_migration_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing migration table cannot authorize adoption of existing application data."""

    database = _capability("database")
    commands = iter((b"", b"0\n"))

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, next(commands), b"")

    monkeypatch.setattr(database, "run_command", run)

    with pytest.raises(database.DatabaseObservationError, match="initial database is not empty"):
        database.observe_database_state_or_empty(database_mapping(), Path("/etc/taskman/pgpass"))


def test_initial_database_empty_proof_inspects_every_user_schema_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relation-only probe would adopt functions, types, or extensions as empty."""

    database = _capability("database")
    commands: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"" if len(commands) == 1 else b"1\n", b"")

    monkeypatch.setattr(database, "run_command", run)

    assert database.observe_database_state_or_empty(database_mapping(), Path("/etc/taskman/pgpass")) == {
        "state": "ready",
        "applied_migrations": (),
        "initial_empty": True,
    }
    query = commands[1][commands[1].index("--command") + 1]
    for catalog in (
        "pg_class", "pg_proc", "pg_type", "pg_extension", "pg_collation",
        "pg_largeobject_metadata", "pg_foreign_data_wrapper", "pg_foreign_server",
        "pg_user_mapping", "pg_publication", "pg_subscription", "pg_conversion",
        "pg_opclass", "pg_opfamily", "pg_ts_config", "pg_ts_dict", "pg_ts_parser",
        "pg_ts_template", "pg_database_owner", "nspacl",
        "pg_event_trigger", "pg_default_acl", "pg_language",
    ):
        assert catalog in query
    # These are present in a pristine PostgreSQL template and must be compared
    # by their complete expected identities, not rejected by name alone.
    for language in ("internal", "c", "sql", "plpgsql_call_handler", "plpgsql_validator"):
        assert language in query


def test_initial_database_empty_proof_executes_on_native_postgresql_when_configured(
) -> None:
    """The real catalog proof must reject a public function after accepting a pristine database."""

    raw_command = os.environ.get("TASKMAN_TEST_PSQL_COMMAND")
    if raw_command is None:
        pytest.skip("set TASKMAN_TEST_PSQL_COMMAND to a trusted JSON psql argv to run native catalog proof")
    try:
        command = json.loads(raw_command)
    except json.JSONDecodeError as error:
        pytest.fail(f"TASKMAN_TEST_PSQL_COMMAND must be JSON argv: {error}")
    if not isinstance(command, list) or not command or any(type(item) is not str or not item for item in command):
        pytest.fail("TASKMAN_TEST_PSQL_COMMAND must be a non-empty JSON argv of strings")

    database = _capability("database")
    captured: list[str] = []

    def capture_query(
        _database: object, _credentials: object, query: str
    ) -> bytes:
        captured.append(query)
        return b""

    original_query = database._database_query
    database._database_query = capture_query
    try:
        database._initial_database_empty(database_mapping(), None)
    finally:
        database._database_query = original_query

    assert len(captured) == 1
    probe = f"taskman_initial_empty_{uuid4().hex}"
    completed = subprocess.run(
        command,
        input=(
            "BEGIN;\n"
            f"{captured[0]};\n"
            f"CREATE FUNCTION public.{probe}() RETURNS integer LANGUAGE sql AS 'SELECT 1';\n"
            f"{captured[0]};\n"
            "ROLLBACK;\n"
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["1", "0"]


def test_restore_authority_sql_wrappers_use_native_psql_file_input_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read-only production SQL wrappers need psql parsing and strict SQL refusal."""

    container = os.environ.get("TASKMAN_TEST_POSTGRES_CONTAINER")
    if container is None:
        pytest.skip("set TASKMAN_TEST_POSTGRES_CONTAINER to run the local native psql wrapper proof")

    preflight = _capability("operations.preflight")
    restore_database = _capability("restore_database")
    command_error = _capability("commands").CommandError
    observed_stdin: list[bytes] = []

    def run(argv: tuple[str, ...], *, stdin: bytes | None = None, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert stdin is not None
        psql = argv.index("psql")
        completed = subprocess.run(
            ("docker", "exec", "-i", container, *argv[psql:]),
            input=stdin,
            capture_output=True,
            check=False,
            timeout=60,
        )
        observed_stdin.append(stdin)
        if completed.returncode != 0:
            raise command_error("native psql execution failed")
        return subprocess.CompletedProcess(argv, completed.returncode, completed.stdout, completed.stderr)

    monkeypatch.setattr(preflight, "run_command", run)
    monkeypatch.setattr(restore_database, "run_command", run)
    database = {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": f"taskman_native_{uuid4().hex}"}

    assert preflight._admin_query(database, "SELECT :'role'", variables=("role", "expanded-value")) == b"expanded-value\n"
    assert restore_database._admin_query(database, "SELECT :'oid'::integer", variables={"oid": "202"}) == b"202\n"
    with pytest.raises(command_error):
        preflight._admin_query(database, "SELEC malformed")

    assert observed_stdin == [
        b"SELECT :'role'\n",
        b"SELECT :'oid'::integer\n",
        b"SELEC malformed\n",
    ]


def test_pristine_language_template_accepts_real_builtin_rows_and_refuses_identity_drift() -> None:
    """Catalog comparison must admit PostgreSQL's actual built-in language rows."""

    database = _capability("database")
    pristine = (
        {"name": "internal", "trusted": False, "handler": None, "inline_handler": None,
         "validator": "pg_catalog.fmgr_internal_validator", "owner": "postgres", "acl": None},
        {"name": "c", "trusted": False, "handler": None, "inline_handler": None,
         "validator": "pg_catalog.fmgr_c_validator", "owner": "postgres", "acl": None},
        {"name": "sql", "trusted": True, "handler": None, "inline_handler": None,
         "validator": "pg_catalog.fmgr_sql_validator", "owner": "postgres", "acl": None},
        {"name": "plpgsql", "trusted": True, "handler": "pg_catalog.plpgsql_call_handler",
         "inline_handler": "pg_catalog.plpgsql_inline_handler", "validator": "pg_catalog.plpgsql_validator",
         "owner": "postgres", "acl": None},
    )

    assert database.pristine_language_rows_match(pristine)
    assert not database.pristine_language_rows_match(
        ({**pristine[0], "validator": None}, *pristine[1:])
    )
    assert not database.pristine_language_rows_match(
        (*pristine[:3], {**pristine[3], "acl": "=U/postgres"})
    )


@pytest.mark.parametrize(
    "raw_versions",
    (
        b"20260905_120000\n",
        b" 20260905120000\n",
        b"20260905120000 \n",
    ),
)
def test_database_observation_rejects_noncanonical_raw_migration_versions(
    monkeypatch: pytest.MonkeyPatch, raw_versions: bytes
) -> None:
    """Normalizing malformed database evidence could identify the wrong release as applied."""

    database = _capability("database")

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, raw_versions, b"")

    monkeypatch.setattr(database, "run_command", run)

    with pytest.raises(database.DatabaseObservationError, match="migration versions"):
        database.observe_database_migrations(database_mapping(), Path("/etc/taskman/pgpass"))


def test_credentials_refuse_a_group_readable_database_password_file(tmp_path: Path) -> None:
    """A credential file readable by another account would expose the database password."""

    credentials = _capability("credentials")
    path = tmp_path / "pgpass"
    path.write_text("127.0.0.1:5432:*:taskman:secret\n", encoding="utf-8")
    path.chmod(0o640)

    with pytest.raises(credentials.CredentialError):
        credentials.validate_credentials(path)


def test_service_capability_targets_only_the_taskman_systemd_unit(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping a different unit would leave Taskman's release active during selection."""

    services = _capability("services")
    commands: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(services, "run_command", run)

    services.change_service("stop")

    assert commands == [("systemctl", "stop", "taskman.service")]


def test_selection_replaces_current_with_the_confirmed_release(tmp_path: Path) -> None:
    """A non-atomic or wrong-target selection could start an unintended release."""

    selection = _capability("selection")
    paths = ManagedPaths.from_mapping(
        {"install_root": (tmp_path / "install").as_posix(), "backup_root": (tmp_path / "backups").as_posix()}
    )
    current = Path(paths.local(paths.current_link))
    previous = Path(paths.local(paths.release_root / "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"))
    target = Path(paths.local(paths.release_root / "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"))
    previous.mkdir(parents=True)
    target.mkdir()
    current.symlink_to(previous)

    selection.select_current(paths, target.name)

    assert current.resolve() == target
    assert not list(current.parent.glob(".current-*.tmp"))


def test_verification_request_preserves_correlation_and_sets_only_the_target_release() -> None:
    """A stale expected release would verify the wrong process after an atomic selection."""

    requests = _capability("verification")
    source = HostRequest(
        3,
        "deploy",
        "op-0123456789abcdef0123456789abcdef",
        {"selected_release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"},
        {"install_root": "/srv/taskman", "backup_root": "/var/backups/taskman"},
        {"application_port": 4000},
    )

    request = requests.verification_request(
        source,
        "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
        {"application_port": 4000},
    )

    assert request.operation == "verify"
    assert request.correlation_id == source.correlation_id
    assert request.expected_state == {"expected_release_id": "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"}
    assert request.paths == source.paths


def test_operations_do_not_reintroduce_a_second_host_capability_owner() -> None:
    """Recreating any of these helpers would let consequence procedures silently diverge again."""

    forbidden = {
        "_atomically_select",
        "_fsync_directory",
        "_migration_versions",
        "_observe_database",
        "_safe_credentials",
        "_service",
        "_sha256",
    }
    operation_root = Path(__file__).resolve().parents[2] / "taskman_ops" / "host_helper" / "operations"

    for name in ("backup", "deploy", "rollback", "restore"):
        tree = ast.parse((operation_root / f"{name}.py").read_text(encoding="utf-8"))
        definitions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert definitions.isdisjoint(forbidden), name
