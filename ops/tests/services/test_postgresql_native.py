"""Opt-in native proof in a new synthetic cluster inside an authorized disposable container.

Setting TASKMAN_TEST_POSTGRES_CONTAINER requires separately approved disposable fixture
and cleanup scope. Every test owns a unique data/config directory and loopback port;
it never uses or reconfigures the container's existing PostgreSQL cluster.
"""

from io import BytesIO
import hashlib
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from taskman_ops.errors import OpsError
from taskman_ops.host_helper import database as native_database
from taskman_ops.host_helper.commands import CommandError
from taskman_ops.host_helper.operations import preflight
from taskman_ops.remote import CommandResult
from taskman_ops.services.postgresql import (
    build_postgresql_ownership_plan, converge_database, render_role_password_input,
)
from tests.support.environments import environment_config


@pytest.fixture
def native_cluster(monkeypatch):
    container = os.environ.get("TASKMAN_TEST_POSTGRES_CONTAINER")
    if container is None:
        pytest.skip("requires separately approved disposable TASKMAN_TEST_POSTGRES_CONTAINER fixture and cleanup")
    suffix = uuid4().hex
    role, name = f"taskman_{suffix}", f"taskman_db_{suffix}"
    password = f"synthetic-{suffix}"
    root = f"/tmp/taskman-native-{suffix}"
    data, passfile = f"{root}/data", f"{root}/pgpass"
    port = 20000 + int(suffix[:4], 16) % 30000

    def execute(argv, *, stdin=None, env=None, postgres=False):
        command = ["docker", "exec", "-i"]
        if postgres:
            command += ["--user", "postgres"]
        for key in env or {}:
            command += ["--env", key]
        completed = subprocess.run((*command, container, *argv), input=stdin,
            capture_output=True, check=False, timeout=60,
            env={**os.environ, **(env or {})})
        return CommandResult(completed.returncode, completed.stdout.decode(), completed.stderr.decode())

    def require(argv, **kwargs):
        result = execute(argv, **kwargs)
        assert result.succeeded, "synthetic native fixture command failed (diagnostics suppressed)"
        return result.stdout

    version = re.fullmatch(r"psql \(PostgreSQL\) ([0-9]+)\.[^\r\n]+\n", require(("psql", "--version")))
    assert version is not None and int(version[1]) >= 16, "fixture needs a require_auth-capable packaged client"
    config_dir = f"/etc/postgresql/{version[1]}/taskman_native_{suffix}"
    config, hba = f"{config_dir}/postgresql.conf", f"{config_dir}/pg_hba.conf"
    # Refuse collisions; cleanup later is restricted to these new UUID-owned directories.
    require(("mkdir", "-m", "700", root))
    require(("mkdir", "-p", f"/etc/postgresql/{version[1]}"))
    require(("mkdir", "-m", "700", config_dir))
    require(("chown", "postgres:postgres", root, config_dir))
    require(("initdb", "-D", data, "--auth-local=peer", "--auth-host=scram-sha-256"), postgres=True)
    require(("tee", config), stdin=(
        f"data_directory = '{data}'\nhba_file = '{hba}'\nport = {port}\n"
        "listen_addresses = '127.0.0.1'\nunix_socket_directories = '/var/run/postgresql'\n"
        "password_encryption = 'md5'\n").encode(), postgres=True)

    def hba_policy(method):
        require(("tee", hba), stdin=("local all all peer\n"
            f"host all all 127.0.0.1/32 {method}\n").encode(), postgres=True)

    hba_policy("scram-sha-256")
    start = ("pg_ctl", "-D", data, "-l", f"{root}/server.log", "-o", f"-c config_file={config}", "-w")
    started = False

    def query(sql, database="postgres"):
        return require(("psql", "--no-psqlrc", "--no-password", "--host", "/var/run/postgresql",
            "--port", str(port), "--username", "postgres", "--dbname", database,
            "--tuples-only", "--no-align", "--set=ON_ERROR_STOP=1", "--command", sql), postgres=True)

    def ownership(role_state="absent", database_state="absent"):
        return build_postgresql_ownership_plan(environment_config(database_port=port,
            database_role=role, database_name=name, postgres_package_track=version[1]),
            {"branch": "existing", "cluster": {"version": version[1], "name": f"taskman_native_{suffix}",
                "port": port, "data_directory": data, "config_file": config, "hba_file": hba},
             "role": role_state, "database": database_state, "access": "unproven"},
            force_postgresql_config=False)

    class NativeRemote:
        def run(self, argv, **kwargs):
            postgres = argv[:4] == ("runuser", "-u", "postgres", "--")
            if postgres:
                argv = argv[4:]
            adapted = tuple(f"PGPASSFILE={passfile}" if arg == "PGPASSFILE=/etc/taskman/pgpass"
                else passfile if arg == "/etc/taskman/pgpass" else arg for arg in argv)
            assert password not in repr(adapted), "secret entered native argv"
            if kwargs.get("stdin") is not None:
                assert kwargs.get("sensitive") is True
            return execute(adapted, stdin=kwargs.get("stdin"), postgres=postgres)

    def host_command(argv, **kwargs):
        env = kwargs.get("env")
        if env and "PGPASSFILE" in env:
            env = {**env, "PGPASSFILE": passfile}
        result = execute(argv, env=env)
        if not result.succeeded:
            raise CommandError("synthetic native authentication failed (diagnostics suppressed)")
        return subprocess.CompletedProcess(argv, 0, result.stdout.encode(), b"")

    monkeypatch.setattr(native_database, "run_command", host_command)
    raw = f"127.0.0.1:{port}:{name}:{role}:{password}\n".encode()
    def converge(plan):
        return converge_database(NativeRemote(), plan.desired,
            role_password_input=render_role_password_input(plan.desired.role, password), pgpass=raw)

    def change_policy(method):
        hba_policy(method)
        # A bounded restart belongs only to this test-created cluster, and makes policy effective deterministically.
        require((*start, "restart", "-m", "fast"), postgres=True)

    try:
        require((*start, "start"), postgres=True)
        started = True
        sibling = f"sibling_{suffix}"
        query(f'CREATE DATABASE "{sibling}"')
        query("CREATE TABLE sibling_health (value integer); INSERT INTO sibling_health VALUES (42)", sibling)
        yield SimpleNamespace(role=role, name=name, port=port, password=password, raw=raw,
            passfile=Path(passfile), query=query, ownership=ownership, converge=converge,
            policy=change_policy, require=require, data=data, sibling=sibling,
            settings={"host": "127.0.0.1", "port": port, "role": role, "name": name})
        assert query("SELECT value FROM sibling_health", sibling).strip() == "42"
    finally:
        if started:
            require(("pg_ctl", "-D", data, "-w", "stop", "-m", "fast"), postgres=True)
        require(("rm", "-rf", "--", root, config_dir))


def test_native_creation_uses_session_scram_and_retry_preserves_adopted_password(native_cluster, tmp_path, monkeypatch):
    cluster = native_cluster
    monkeypatch.setattr(preflight, "_PGPASS_PATH", tmp_path / "absent-pgpass")
    arguments = ("127.0.0.1", str(cluster.port), cluster.role, cluster.name, "absent", "authenticate")
    assert preflight.provision_pgpass_authority(arguments, BytesIO(cluster.raw)) == 0
    assert cluster.query("SHOW password_encryption").strip() == "md5"
    cluster.policy("reject")
    with pytest.raises(OpsError) as failed:
        cluster.converge(cluster.ownership())
    assert failed.value.status == 5
    assert failed.value.state["postgresql_resources"] == {"role": "created", "database": "created", "credentials": "installed"}
    verifier = cluster.query(f"SELECT rolpassword FROM pg_authid WHERE rolname = '{cluster.role}'").strip()
    assert verifier.startswith("SCRAM-SHA-256$"), "new role did not receive a SCRAM verifier"
    before_digest = hashlib.sha256(verifier.encode()).hexdigest()
    cluster.policy("scram-sha-256")
    result = cluster.converge(cluster.ownership("compatible", "compatible"))
    assert not result.changed
    after_digest = hashlib.sha256(cluster.query(f"SELECT rolpassword FROM pg_authid WHERE rolname = '{cluster.role}'").strip().encode()).hexdigest()
    assert before_digest == after_digest, "retry rotated an adopted password"
    native_database.prove_scram_access(cluster.settings, cluster.passfile)


@pytest.mark.parametrize("method,verifier,accepted", [
    ("scram-sha-256", "scram-sha-256", True),
    ("trust", "scram-sha-256", False),
    ("password", "scram-sha-256", False),
    ("md5", "md5", False),
    ("md5", "scram-sha-256", True),
])
def test_native_access_requires_actual_scram_exchange(native_cluster, method, verifier, accepted):
    cluster = native_cluster
    cluster.converge(cluster.ownership())
    if verifier == "md5":
        # Synthetic fixture setup only: production adoption never changes a password.
        cluster.require(("psql", "--no-psqlrc", "--host", "/var/run/postgresql", "--port", str(cluster.port),
            "--username", "postgres", "--dbname", "postgres", "--set=ON_ERROR_STOP=1"),
            stdin=(f"SET password_encryption = 'md5';\n\\password {cluster.role}\n"
                f"{cluster.password}\n{cluster.password}\n").encode(), postgres=True)
    cluster.policy(method)
    if accepted:
        native_database.prove_scram_access(cluster.settings, cluster.passfile)
    else:
        with pytest.raises(CommandError):
            native_database.prove_scram_access(cluster.settings, cluster.passfile)


def test_native_access_refuses_an_unsupported_client_before_application_query(tmp_path, monkeypatch):
    """A real synthetic executable exercises the bounded prerequisite runner without a cluster."""
    executable = tmp_path / "psql"
    executable.write_text("#!/bin/sh\nif [ \"$1\" = --version ]; then printf 'psql (PostgreSQL) 15.9\\n'; else exit 99; fi\n")
    executable.chmod(0o700)
    from taskman_ops.host_helper.commands import run_command
    def synthetic_client(argv, **kwargs):
        return run_command(argv, **{**kwargs, "env": {**kwargs.get("env", {}), "PATH": str(tmp_path)}})
    monkeypatch.setattr(native_database, "run_command", synthetic_client)
    with pytest.raises(CommandError, match="version 16 or later"):
        native_database.prove_scram_access({"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, tmp_path / "absent")
