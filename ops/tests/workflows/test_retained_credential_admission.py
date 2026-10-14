"""Public provisioning must establish retained-byte authority before native access."""

from contextlib import nullcontext
from dataclasses import replace
from io import BytesIO
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from taskman_ops import provisioning
from taskman_ops.cli import Invocation
from taskman_ops.host_helper import database as native
from taskman_ops.host_helper.operations import discover, preflight
from taskman_ops.host_helper.state import HostState
from taskman_ops.host_protocol import decode_result, encode_result
from taskman_ops.output import render_human, render_json
from taskman_ops.remote import CommandResult
from taskman_ops.workflows import helper
from taskman_ops.workflows.provision import provision
from tests.support.shell import write_shell_script
from tests.workflows.test_provision import Host, _authority, _capabilities


SUPPLIED = b"127.0.0.1:5432:taskman_prod:taskman:credential-secret-canary\n"


@pytest.mark.parametrize("force,dry_run,retained,mode,expected", [
    (False, False, SUPPLIED, 0o600, None),
    (False, True, SUPPLIED, 0o600, None),
    (True, False, SUPPLIED, 0o600, None),
    (True, True, SUPPLIED, 0o600, None),
    (True, True, b"127.0.0.1:5432:taskman_prod:taskman:different-secret-canary\n", 0o600, 10),
    (False, False, SUPPLIED, 0o644, 10),
])
def test_public_provision_checks_retained_authority_before_application_use(
    tmp_path, monkeypatch, force, dry_run, retained, mode, expected,
):
    """Moving exact comparison after observation must fail ordinary mismatch admission."""
    credentials = tmp_path / "pgpass"
    credentials.write_bytes(retained)
    credentials.chmod(mode)
    # The host calls run as root. Emulate only that ownership in this unprivileged
    # fixture; the shell still inspects real file kind/mode and compares real bytes.
    tools = tmp_path / "bin"
    tools.mkdir()
    write_shell_script(tools / "stat", "exec /usr/bin/stat --format='root:root:%a' -- \"$3\"")
    environment = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    checks = []

    class Remote(Host):
        def run(self, argv, **kwargs):
            assert kwargs["sudo"] is True and kwargs["sensitive"] is True
            assert argv[:2] == ("sh", "-ceu")
            assert kwargs["stdin"] not in repr(argv).encode()
            local = credentials if argv[-1] == "/etc/taskman/pgpass" else tmp_path / "runtime.env"
            result = subprocess.run((*argv[:-1], str(local)), input=kwargs["stdin"],
                capture_output=True, env=environment, check=False)
            assert result.stdout == result.stderr == b""
            checks.append(argv[-1])
            return CommandResult(result.returncode)

    class RootCredentialPath(type(Path())):
        def lstat(self):
            details = list(super().lstat())
            details[4:6] = [0, 0]
            return os.stat_result(details)

    monkeypatch.setattr(preflight, "_PGPASS_PATH", RootCredentialPath(credentials))
    monkeypatch.setattr(discover, "lifecycle_lock", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(discover, "_validate_managed_resources", lambda *_a, **_k: None)
    state = HostState(selected_release_id=None, releases=(), backups=(), selections=(),
        applied_migrations=(), service_state="stopped", database_state="ready", temporary_paths=(), warnings=())
    monkeypatch.setattr(discover, "observe_host_state", lambda *_a, **_k: state)
    selected = _authority()["postgresql"]
    selected.update(branch="configure-existing" if force else "existing", access="unproven")
    observations = []
    monkeypatch.setattr(discover, "_observe_postgresql_authority",
        lambda *_a: observations.append("selected-resources") or selected)
    original_observe = discover._observe_provision_database
    monkeypatch.setattr(discover, "_observe_provision_database", lambda db, _path, track, override:
        original_observe(db, credentials, track, override))
    admin_reads = []
    monkeypatch.setattr(discover, "observe_database_state_or_empty_as_admin", lambda *_a:
        admin_reads.append("schema") or {"state": "ready", "applied_migrations": (), "initial_empty": True})
    scheduler = _authority()
    monkeypatch.setattr(discover, "_scheduler_facts", lambda *_a: {key: scheduler[key] for key in (
        "scheduled_backup_sha256", "backup_timer_enabled", "backup_timer_state")})
    monkeypatch.setattr(discover, "_scheduler_resources", lambda *_a: scheduler["scheduler_resources"])
    commands = []
    access_checks = []

    def command(argv, **kwargs):
        commands.append(argv)
        if argv == ("psql", "--version"):
            return subprocess.CompletedProcess(argv, 0, b"psql (PostgreSQL) 18.6\n", b"")
        access_checks.append(tuple(checks))
        assert "require_auth=scram-sha-256" in argv[argv.index("--dbname") + 1]
        return subprocess.CompletedProcess(argv, 1, b"", b"native-secret-canary")

    monkeypatch.setattr(native, "run_command", command)
    original_run = helper.run_request
    monkeypatch.setattr(helper, "run_request", lambda remote, request:
        original_run(remote, request, invoker=lambda _r, _p, req:
            decode_result(encode_result(discover.provision_authority(req)))))
    modes = []

    def sensitive(_remote, _package, **kwargs):
        modes.append(kwargs["authentication_mode"])
        status = preflight.provision_pgpass_authority((kwargs["host"], str(kwargs["port"]),
            kwargs["role"], kwargs["database"], kwargs["database_state"],
            kwargs["authentication_mode"]), BytesIO(kwargs["pgpass"]))
        return SimpleNamespace(exit_status=status, warnings=())

    monkeypatch.setattr("taskman_ops.helper_client.runner.invoke_sensitive_pgpass_authority", sensitive)
    remote = Remote()
    caps = replace(_capabilities(remote), preflight=provisioning.validate_existing_authority,
        render_pgpass=lambda *_a: SUPPLIED,
        downgrade_authority=lambda *_a, **_k: (False, ()))
    result = provision(Invocation(command="provision", environment="production",
        artifact=Path("explicit.tar.gz"), dry_run=dry_run, force_postgresql_config=force), capabilities=caps)

    assert result.exit_status == (expected if expected is not None else 0 if force else 5)
    assert result.changed is (expected is None and force and not dry_run)
    assert credentials.read_bytes() == retained
    assert credentials.stat().st_mode & 0o777 == mode
    assert remote.closed == 1
    if expected == 10:
        assert commands == observations == admin_reads == modes == []
        assert not remote.events
        assert result.facts["release_started"] is False
    elif force:
        observations_count = 1 if dry_run else 2
        assert modes == ["defer-for-configuration"] * observations_count
        assert commands == [("psql", "--version")] * observations_count
        assert admin_reads == ["schema"] * observations_count
        assert result.stage == ("planned" if dry_run else "provisioned")
        if dry_run:
            assert result.facts["plan"]["postgresql"]["access"] == "unproven"
    else:
        assert len(access_checks) == 1 and admin_reads == modes == []
        assert access_checks == [("/etc/taskman/pgpass",)]
        assert result.facts["release_started"] is False
        assert result.facts["postgresql"]["failed_check"] == "scram-application-access"
        assert not remote.events
    for output in (render_human(result), render_json(result)):
        for secret in ("credential-secret-canary", "malformed-secret-canary",
            "different-secret-canary", "native-secret-canary"):
            assert secret not in output
