"""Opt-in proof of production convergence against test-owned native identities."""

from io import BytesIO
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from taskman_ops.host_helper.commands import CommandError
from taskman_ops.host_helper.operations import preflight
from taskman_ops.remote import CommandResult
from taskman_ops.services.postgresql import (
    build_postgresql_plan,
    converge_database,
    render_role_password_input,
)
from tests.support.environments import environment_config


def test_native_absent_identities_are_created_then_authenticated_without_replacing_existing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapt location and protected path only; run real inspection/create/verify commands."""

    container = os.environ.get("TASKMAN_TEST_POSTGRES_CONTAINER")
    if container is None:
        pytest.skip("set TASKMAN_TEST_POSTGRES_CONTAINER to run native fresh database convergence")
    suffix = uuid4().hex
    role = f"taskman_fresh_{suffix}"
    database = f"taskman_fresh_db_{suffix}"
    directory = f"/tmp/taskman-fresh-{suffix}"
    pgpass_path = f"{directory}/pgpass"
    password = f"synthetic-{suffix}"
    plan = build_postgresql_plan(environment_config(database_role=role, database_name=database))
    raw = f"127.0.0.1:5432:{database}:{role}:{password}\n".encode()
    inspect = subprocess.run(
        ("docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container),
        capture_output=True, check=True, timeout=60,
    )
    address = inspect.stdout.decode().strip()
    assert address and len(address.split(".")) == 4

    def execute(argv, *, stdin=None, environment=None, postgres=False):
        command = ["docker", "exec", "-i"]
        if postgres:
            command += ["--user", "postgres"]
        for key in environment or {}:
            command += ["--env", key]
        completed = subprocess.run(
            (*command, container, *argv), input=stdin, capture_output=True, check=False,
            timeout=60, env={**os.environ, **(environment or {})},
        )
        return CommandResult(completed.returncode, completed.stdout.decode(), completed.stderr.decode())

    def query(sql):
        result = execute(
            ("psql", "--no-psqlrc", "--tuples-only", "--no-align", "--username", "postgres",
             "--dbname", "postgres", "--set=ON_ERROR_STOP=1", "--command", sql), postgres=True,
        )
        assert result.succeeded, "native test administration failed"
        return result.stdout

    before_roles = query("SELECT rolname FROM pg_roles ORDER BY rolname")
    before_databases = query("SELECT datname FROM pg_database ORDER BY datname")
    assert role not in before_roles.splitlines()
    assert database not in before_databases.splitlines()
    assert execute(("mkdir", "-m", "700", directory)).succeeded

    def adapt(argv):
        # The existing container trusts loopback; its network address selects
        # the existing SCRAM rule without modifying any HBA or server setting.
        return tuple(
            address if argument == "127.0.0.1" else
            f"PGPASSFILE={pgpass_path}" if argument == "PGPASSFILE=/etc/taskman/pgpass" else
            pgpass_path if argument == "/etc/taskman/pgpass" else
            argument.replace("stat --format=", "stat -c ") if argument.startswith("exec >/dev/null") else argument
            for argument in argv
        )

    class NativeRemote:
        def run(self, argv, **kwargs):
            postgres = argv[:4] == ("runuser", "-u", "postgres", "--")
            if postgres:
                argv = argv[4:]
            stdin = kwargs.get("stdin")
            if argv[:2] == ("sh", "-c"):
                assert kwargs["sensitive"] is True
                stdin = stdin.replace(b"127.0.0.1:", f"{address}:".encode(), 1)
            if stdin == render_role_password_input(plan.role, password):
                assert kwargs["sensitive"] is True
            assert password not in repr(argv)
            return execute(adapt(argv), stdin=stdin, postgres=postgres)

    def authenticate(argv, **kwargs):
        result = execute(adapt(argv), environment=kwargs.get("env"))
        if not result.succeeded:
            raise CommandError("native authentication failed")
        return subprocess.CompletedProcess(argv, 0, result.stdout.encode(), b"")

    monkeypatch.setattr(preflight, "_PGPASS_PATH", tmp_path / "absent-pgpass")
    monkeypatch.setattr(preflight, "run_command", authenticate)
    arguments = ("127.0.0.1", "5432", role, database)
    try:
        assert preflight.provision_pgpass_authority((*arguments, "absent"), BytesIO(raw)) == 0
        created = converge_database(
            NativeRemote(), plan, role_password_input=render_role_password_input(plan.role, password), pgpass=raw,
        )
        assert created.operations == ("pgpass", "role", "database")
        assert query(
            f"SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole "
            f"AND NOT rolreplication AND NOT rolbypassrls AND rolinherit FROM pg_roles WHERE rolname = '{role}'"
        ).strip() == "t"
        assert query(f"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '{database}'").strip() == role
        assert preflight.provision_pgpass_authority((*arguments, "ready"), BytesIO(raw)) == 0
        wrong = raw.replace(password.encode(), b"wrong-synthetic-password")
        assert preflight.provision_pgpass_authority((*arguments, "ready"), BytesIO(wrong)) == 10
        rerun = converge_database(
            NativeRemote(), plan, role_password_input=render_role_password_input(plan.role, password), pgpass=raw,
        )
        assert rerun.changed is False
    finally:
        query(f'DROP DATABASE IF EXISTS "{database}"')
        query(f'DROP ROLE IF EXISTS "{role}"')
        assert execute(("rm", "-f", "--", pgpass_path)).succeeded
        assert execute(("rmdir", "--", directory)).succeeded
        assert query("SELECT rolname FROM pg_roles ORDER BY rolname") == before_roles
        assert query("SELECT datname FROM pg_database ORDER BY datname") == before_databases
