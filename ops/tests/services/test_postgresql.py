from __future__ import annotations

import os
from pathlib import Path
import pytest
import subprocess
from pyinfra.api import Config, Inventory, State, deploy

from tests.support.environments import environment_config
from tests.support.shell import write_shell_script
from taskman_ops.errors import ExitStatus, OpsError
import taskman_ops.services.postgresql as postgresql
from taskman_ops.services.postgresql import (
    ExistingDatabase,
    ExistingRole,
    build_postgresql_plan,
    converge_database,
    install_pgpass,
    render_postgresql_native_configuration_script,
    render_role_password_input,
    select_postgresql_cluster,
    validate_existing_database_state,
)
from taskman_ops.remote import CommandResult, PyinfraRemote
from taskman_ops.remote import ChangeSet
from tests.support.remotes import ScriptedRemote


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

    expected = build_postgresql_plan(environment_config())
    assert postgresql.declare_postgresql(environment_config()) == expected

    assert package_calls == [{"packages": list(expected.packages), "name": "Install PostgreSQL"}]
    assert staged == [(expected.hba, "/etc/taskman/pg_hba.conf.staged")]
    assert configured == [expected]


def test_cluster_selection_allows_one_initial_cluster_for_a_custom_port_and_refuses_ambiguity() -> None:
    """A custom-port rerun must not silently configure the first unrelated cluster."""

    plan = build_postgresql_plan(environment_config(database_port=5433))

    assert select_postgresql_cluster("16 main 5432 online postgres /var/lib/postgresql/16/main /var/log/postgresql/postgresql-16-main.log\n", plan) == (
        "16",
        "main",
        "5432",
    )

    with pytest.raises(OpsError) as raised:
        select_postgresql_cluster(
            "16 main 5432 online postgres /var/lib/postgresql/16/main /var/log/postgresql/postgresql-16-main.log\n"
            "17 main 5433 online postgres /var/lib/postgresql/17/main /var/log/postgresql/postgresql-17-main.log\n",
            plan,
        )

    assert raised.value.status is ExitStatus.SAFETY


def test_native_configuration_script_has_fail_fast_config_and_hba_gates_before_restart() -> None:
    """A failed native config or HBA check must prevent the restart command."""

    script = render_postgresql_native_configuration_script(build_postgresql_plan(environment_config()))

    assert "set -eu" in script
    assert "pg_hba_file_rules" in script
    assert "postgres --config-file" in script
    assert "-C listen_addresses | grep -Fx 127.0.0.1" in script
    assert '-C port | grep -Fx "$desired_port"' in script
    assert "-C password_encryption | grep -Fx scram-sha-256" in script
    assert script.index("pg_hba_file_rules") < script.rindex("pg_ctlcluster")


def test_native_configuration_places_the_final_hba_file_in_a_postgres_traversable_directory() -> None:
    script = render_postgresql_native_configuration_script(build_postgresql_plan(environment_config()))

    assert "/etc/postgresql/taskman/pg_hba.conf" in script
    assert "install -d -o root -g postgres -m 0750 /etc/postgresql/taskman" in script


def test_native_configuration_script_stops_before_restart_when_pg_conftool_fails(tmp_path: Path) -> None:
    """This executes the rendered adapter script rather than asserting plan metadata."""

    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text("local all all peer\n", encoding="utf-8")
    destination.write_text(stage.read_text(encoding="utf-8"), encoding="utf-8")
    destination.chmod(0o640)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    write_shell_script(bin_dir / "pg_lsclusters", "printf '16 main 5432 online postgres /data /log\\n'")
    write_shell_script(bin_dir / "pg_conftool", "exit 1")
    write_shell_script(bin_dir / "pg_ctlcluster", f"printf '%s\\n' \"$*\" >> {restart_log}")

    completed = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                build_postgresql_plan(environment_config()),
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert completed.returncode != 0
    assert not restart_log.exists()


def test_native_configuration_script_stops_before_restart_when_hba_validation_reports_an_error(
    tmp_path: Path,
) -> None:
    """A syntactically valid postgresql.conf is insufficient when HBA parsing fails."""

    plan = build_postgresql_plan(environment_config())
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(stage.read_text(encoding="utf-8"), encoding="utf-8")
    destination.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_500
    _write_postmaster_pid(data_directory, port=5432, start_time=start_time)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    hba_check_log = tmp_path / "hba-check.log"
    write_shell_script(
        bin_dir / "pg_lsclusters",
        f"printf '16 main 5432 online postgres {data_directory} /log\\n'",
    )
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''case "${{5:-$4}}" in
  data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
  hba_file) printf '%s\\n' {destination.as_posix()} ;;
  listen_addresses) printf '%s\\n' 127.0.0.1 ;;
  port) printf '%s\\n' 5432 ;;
  password_encryption) printf '%s\\n' scram-sha-256 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "postgres",
        f'''case "$*" in
  *listen_addresses*) printf '%s\\n' 127.0.0.1 ;;
  *port*) printf '%s\\n' 5432 ;;
  *password_encryption*) printf '%s\\n' scram-sha-256 ;;
  *hba_file*) printf '%s\\n' {destination.as_posix()} ;;
esac''',
    )
    write_shell_script(
        bin_dir / "runuser",
        f'''case "$*" in
  *current_setting*) printf '%s\\n' '5432|{data_directory}|{start_time}' ;;
  *'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'SHOW hba_file'*) printf '%s\\n' {destination} ;;
  *pg_hba_file_rules*) : > {hba_check_log}; printf '%s\\n' 'bad HBA entry' ;;
  *) exit 91 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "pg_ctlcluster",
        f'''case "$3" in
  status) exit 0 ;;
  *) printf '%s\\n' "$*" >> {restart_log} ;;
esac''',
    )

    completed = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                plan,
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert completed.returncode != 0
    assert hba_check_log.exists()
    assert not restart_log.exists()


@pytest.mark.parametrize(
    ("reload_activates_desired_hba", "hba_errors", "parser_queried"),
    (
        (False, "", False),
        (True, "bad HBA entry", True),
    ),
    ids=("reload-path-mismatch", "reloaded-hba-parser-error"),
)
def test_native_configuration_validates_a_reloaded_hba_path_before_restart(
    tmp_path: Path,
    *,
    reload_activates_desired_hba: bool,
    hba_errors: str,
    parser_queried: bool,
) -> None:
    """A first HBA-path activation must be accepted by the live parser before restart."""

    plan = build_postgresql_plan(environment_config())
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    old_hba = tmp_path / "old_pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(plan.hba, encoding="utf-8")
    destination.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_600
    _write_postmaster_pid(data_directory, port=5432, start_time=start_time)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    service_log = tmp_path / "service.log"
    admin_log = tmp_path / "admin.log"
    hba_check_log = tmp_path / "hba-check.log"
    reloaded = tmp_path / "reloaded"
    reloaded_hba = destination if reload_activates_desired_hba else old_hba
    hba_error_output = f"printf '%s\\n' {hba_errors!r}" if hba_errors else ":"
    write_shell_script(
        bin_dir / "pg_lsclusters",
        f"printf '16 main 5432 online postgres {data_directory} /log\\n'",
    )
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''case "${{5:-$4}}" in
  data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
  hba_file) printf '%s\\n' {destination.as_posix()} ;;
  listen_addresses) printf '%s\\n' 127.0.0.1 ;;
  port) printf '%s\\n' 5432 ;;
  password_encryption) printf '%s\\n' scram-sha-256 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "postgres",
        f'''case "$*" in
  *listen_addresses*) printf '%s\\n' 127.0.0.1 ;;
  *port*) printf '%s\\n' 5432 ;;
  *password_encryption*) printf '%s\\n' scram-sha-256 ;;
  *hba_file*) printf '%s\\n' {destination.as_posix()} ;;
esac''',
    )
    write_shell_script(
        bin_dir / "runuser",
        f'''printf '%s\\n' "$*" >> {admin_log}
case "$*" in
  *current_setting*) printf '%s\\n' '5432|{data_directory}|{start_time}' ;;
  *'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'SHOW hba_file'*)
    if [ -f {reloaded} ]; then printf '%s\\n' {reloaded_hba}; else printf '%s\\n' {old_hba}; fi
    ;;
  *pg_hba_file_rules*) : > {hba_check_log}; {hba_error_output} ;;
  *) exit 91 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "pg_ctlcluster",
        f'''case "$3" in
  status) exit 0 ;;
  reload) printf '%s\\n' "$*" >> {service_log}; : > {reloaded} ;;
  restart) printf '%s\\n' "$*" >> {service_log} ;;
  *) exit 92 ;;
esac''',
    )

    completed = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                plan,
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert completed.returncode != 0
    assert reloaded.exists()
    assert service_log.read_text(encoding="utf-8").splitlines() == ["16 main reload"]
    admin_commands = admin_log.read_text(encoding="utf-8").splitlines()
    post_reload_hba_query = next(
        index
        for index, command in enumerate(admin_commands)
        if index > 2 and "SHOW hba_file" in command
    )
    if parser_queried:
        parser_query = next(
            index
            for index, command in enumerate(admin_commands)
            if "pg_hba_file_rules" in command
        )
        assert post_reload_hba_query < parser_query
        assert "--port 5432" in admin_commands[parser_query]
        assert hba_check_log.exists()
    else:
        assert not any("pg_hba_file_rules" in command for command in admin_commands)
        assert not hba_check_log.exists()


def test_native_configuration_uses_the_live_old_socket_before_restart_and_the_requested_socket_afterward(
    tmp_path: Path,
) -> None:
    """A 5432→5433 transition must not query desired port until restart succeeds."""

    plan = build_postgresql_plan(environment_config(database_port=5433))
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(stage.read_text(encoding="utf-8"), encoding="utf-8")
    destination.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_200
    _write_postmaster_pid(data_directory, port=5432, start_time=start_time)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    admin_log = tmp_path / "admin.log"
    restart_log = tmp_path / "restarts.log"
    restarted = tmp_path / "restarted"
    write_shell_script(
        bin_dir / "pg_lsclusters",
        f"printf '16 main 5432 online postgres {data_directory} /log\\n'",
    )
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''if [ "$1" = -s ]; then key=$5
elif [ "$3" = set ]; then exit 0
else exit 93
fi
case "$key" in
  data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
  hba_file) printf '%s\\n' {destination.as_posix()} ;;
  listen_addresses) printf '%s\\n' 127.0.0.1 ;;
  port) printf '%s\\n' 5432 ;;
  password_encryption) printf '%s\\n' scram-sha-256 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "postgres",
        f'''case "$*" in
  *listen_addresses*) printf '%s\\n' 127.0.0.1 ;;
  *port*) printf '%s\\n' 5433 ;;
  *password_encryption*) printf '%s\\n' scram-sha-256 ;;
  *hba_file*) printf '%s\\n' {destination.as_posix()} ;;
esac''',
    )
    write_shell_script(
        bin_dir / "runuser",
        f'''printf '%s\\n' "$*" >> {admin_log}
case "$*" in
  *'--port 5432'*'current_setting'*) printf '%s\\n' '5432|{data_directory}|{start_time}' ;;
  *'--port 5432'*'SHOW port'*) printf '%s\\n' 5432 ;;
  *'--port 5432'*'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'--port 5432'*'SHOW hba_file'*) printf '%s\\n' {destination} ;;
  *'--port 5432'*'pg_hba_file_rules'*) ;;
  *'--port 5433'*'current_setting'*) [ -f {restarted} ] && printf '%s\\n' '5433|{data_directory}|{start_time}' ;;
  *'--port 5433'*'SHOW port'*) [ -f {restarted} ] && printf '%s\\n' 5433 ;;
  *'--port 5433'*'SHOW config_file'*) [ -f {restarted} ] && printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'--port 5433'*'SHOW hba_file'*) [ -f {restarted} ] && printf '%s\\n' {destination} ;;
  *'--port 5433'*'pg_hba_file_rules'*) [ -f {restarted} ] ;;
  *) exit 91 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "pg_ctlcluster",
        f'''case "$3" in
  status) exit 0 ;;
  restart)
    printf '%s\\n' "$*" >> {restart_log}
    : > {restarted}
    printf '%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n' \
      {os.getpid()} {data_directory} {start_time} 5433 /var/run/postgresql 127.0.0.1 1 'ready ' \
      > {data_directory / "postmaster.pid"}
    ;;
  *) printf '%s\\n' "$*" >> {restart_log} ;;
esac''',
    )

    completed = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                plan,
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert admin_log.read_text(encoding="utf-8").splitlines() == [
        f"-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SELECT current_setting('port'), current_setting('data_directory'), extract(epoch from pg_postmaster_start_time())::bigint",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SHOW config_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SHOW hba_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SHOW hba_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL",
        f"-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5433 --username postgres --dbname=postgres --command SELECT current_setting('port'), current_setting('data_directory'), extract(epoch from pg_postmaster_start_time())::bigint",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5433 --username postgres --dbname=postgres --command SHOW config_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5433 --username postgres --dbname=postgres --command SHOW hba_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5433 --username postgres --dbname=postgres --command SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL",
    ]
    assert restart_log.read_text(encoding="utf-8").splitlines() == ["16 main reload", "16 main restart"]


def test_native_configuration_recovers_an_interrupted_custom_port_transition(tmp_path: Path) -> None:
    """Configured 5433 must not hide a selected process still live on 5432."""

    plan = build_postgresql_plan(environment_config(database_port=5433))
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(stage.read_text(encoding="utf-8"), encoding="utf-8")
    destination.chmod(0o640)
    parent = destination.parent
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_000
    _write_postmaster_pid(data_directory, port=5432, start_time=start_time)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    restarted = tmp_path / "restarted"
    write_shell_script(
        bin_dir / "pg_lsclusters",
        f"printf '16 main 5433 online postgres {data_directory} /log\\n'",
    )
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''case "${{5:-$4}}" in
  data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
  hba_file) printf '%s\\n' {destination.as_posix()} ;;
  listen_addresses) printf '%s\\n' 127.0.0.1 ;;
  port) printf '%s\\n' 5433 ;;
  password_encryption) printf '%s\\n' scram-sha-256 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "stat",
        f'''case "$*" in
  *'%U:%G:%a'*{destination.as_posix()}) printf '%s\\n' root:postgres:640 ;;
  *'%U:%G:%a'*{parent.as_posix()}) printf '%s\\n' root:postgres:750 ;;
  *) exec /usr/bin/stat "$@" ;;
esac''',
    )
    write_shell_script(
        bin_dir / "postgres",
        f'''case "$*" in
  *listen_addresses*) printf '%s\\n' 127.0.0.1 ;;
  *port*) printf '%s\\n' 5433 ;;
  *password_encryption*) printf '%s\\n' scram-sha-256 ;;
  *hba_file*) printf '%s\\n' {destination.as_posix()} ;;
esac''',
    )
    write_shell_script(
        bin_dir / "runuser",
        f'''case "$*" in
  *'--port 5432'*'current_setting'*) printf '%s\\n' '5432|{data_directory}|{start_time}' ;;
  *'--port 5432'*'SHOW port'*) printf '%s\\n' 5432 ;;
  *'--port 5432'*'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'--port 5432'*'SHOW hba_file'*) printf '%s\\n' {destination} ;;
  *'--port 5432'*'pg_hba_file_rules'*) ;;
  *'--port 5433'*'current_setting'*) [ -f {restarted} ] && printf '%s\\n' '5433|{data_directory}|{start_time}' ;;
  *'--port 5433'*'SHOW port'*) [ -f {restarted} ] && printf '%s\\n' 5433 ;;
  *'--port 5433'*'SHOW config_file'*) [ -f {restarted} ] && printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'--port 5433'*'SHOW hba_file'*) [ -f {restarted} ] && printf '%s\\n' {destination} ;;
  *'--port 5433'*'pg_hba_file_rules'*) [ -f {restarted} ] ;;
  *) exit 91 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "pg_ctlcluster",
        f'''case "$3" in
  status) exit 0 ;;
  restart)
    printf '%s\\n' "$*" >> {restart_log}
    : > {restarted}
    printf '%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n' \
      {os.getpid()} {data_directory} {start_time} 5433 /var/run/postgresql 127.0.0.1 1 'ready ' \
      > {data_directory / "postmaster.pid"}
    ;;
  *) printf '%s\\n' "$*" >> {restart_log} ;;
esac''',
    )
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    recovered = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                plan,
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout == ""
    assert restart_log.read_text(encoding="utf-8").splitlines() == ["16 main reload", "16 main restart"]


def test_native_configuration_recovers_a_stopped_selected_cluster(tmp_path: Path) -> None:
    """A stopped cluster starts only after offline HBA and native validation."""

    plan = build_postgresql_plan(environment_config(database_port=5433))
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text("host all all 0.0.0.0/0 trust\n", encoding="utf-8")
    destination.write_text(plan.hba, encoding="utf-8")
    destination.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_100
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    restarted = tmp_path / "restarted"
    restart_log = tmp_path / "restarts.log"
    admin_log = tmp_path / "admin.log"
    write_shell_script(
        bin_dir / "pg_lsclusters",
        f"printf '16 main 5433 down postgres {data_directory} /log\\n'",
    )
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''case "${{5:-$4}}" in
  data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
  hba_file) printf '%s\\n' {destination.as_posix()} ;;
  listen_addresses) printf '%s\\n' 127.0.0.1 ;;
  port) printf '%s\\n' 5433 ;;
  password_encryption) printf '%s\\n' scram-sha-256 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "stat",
        f'''case "$*" in
  *'%U:%G:%a'*{destination.as_posix()}) printf '%s\\n' root:postgres:640 ;;
  *'%U:%G:%a'*{destination.parent.as_posix()}) printf '%s\\n' root:postgres:750 ;;
  *) exec /usr/bin/stat "$@" ;;
esac''',
    )
    write_shell_script(
        bin_dir / "postgres",
        f'''case "$*" in
  *listen_addresses*) printf '%s\\n' 127.0.0.1 ;;
  *port*) printf '%s\\n' 5433 ;;
  *password_encryption*) printf '%s\\n' scram-sha-256 ;;
  *hba_file*) printf '%s\\n' {destination.as_posix()} ;;
esac''',
    )
    write_shell_script(
        bin_dir / "runuser",
        f'''printf '%s\\n' "$*" >> {admin_log}
[ -f {restarted} ] || exit 90
case "$*" in
  *'--port 5433'*'current_setting'*) printf '%s\\n' '5433|{data_directory}|{start_time}' ;;
  *'--port 5433'*'SHOW port'*) printf '%s\\n' 5433 ;;
  *'--port 5433'*'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'--port 5433'*'SHOW hba_file'*) printf '%s\\n' {destination} ;;
  *'--port 5433'*'pg_hba_file_rules'*) ;;
  *) exit 91 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "pg_ctlcluster",
        f'''case "$3" in
  status) [ -f {restarted} ] ;;
  restart)
    printf '%s\\n' "$*" >> {restart_log}
    : > {restarted}
    printf '%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n' \
      {os.getpid()} {data_directory} {start_time} 5433 /var/run/postgresql 127.0.0.1 1 'ready ' \
      > {data_directory / "postmaster.pid"}
    ;;
  *) exit 92 ;;
esac''',
    )
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    refused = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                plan,
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert refused.returncode == int(ExitStatus.SAFETY)
    assert not restart_log.exists()
    assert not admin_log.exists()

    stage.write_text(plan.hba, encoding="utf-8")
    recovered = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                plan,
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout == ""
    assert restart_log.read_text(encoding="utf-8").splitlines() == ["16 main restart"]
    assert admin_log.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    ("parser_query_succeeds", "expected_inspection"),
    ((True, "changed=0\n"), (False, "changed=1\n")),
    ids=("parser-clean", "parser-unavailable"),
)
def test_native_configuration_inspection_requires_a_successful_hba_parser_query(
    tmp_path: Path, *, parser_query_succeeds: bool, expected_inspection: str
) -> None:
    """A failed parser query must keep desired-state inspection conservative."""

    plan = build_postgresql_plan(environment_config(database_port=5433))
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(plan.hba, encoding="utf-8")
    destination.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_300
    _write_postmaster_pid(data_directory, port=5433, start_time=start_time)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    write_shell_script(
        bin_dir / "pg_lsclusters",
        f"printf '16 main 5433 online postgres {data_directory} /log\\n'",
    )
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''case "${{5:-$4}}" in
  data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
  hba_file) printf '%s\\n' {destination.as_posix()} ;;
  listen_addresses) printf '%s\\n' 127.0.0.1 ;;
  port) printf '%s\\n' 5433 ;;
  password_encryption) printf '%s\\n' scram-sha-256 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "stat",
        f'''case "$*" in
  *'%U:%G:%a'*{destination.as_posix()}) printf '%s\\n' root:postgres:640 ;;
  *'%U:%G:%a'*{destination.parent.as_posix()}) printf '%s\\n' root:postgres:750 ;;
  *) exec /usr/bin/stat "$@" ;;
esac''',
    )
    write_shell_script(
        bin_dir / "postgres",
        f'''case "$*" in
  *listen_addresses*) printf '%s\\n' 127.0.0.1 ;;
  *port*) printf '%s\\n' 5433 ;;
  *password_encryption*) printf '%s\\n' scram-sha-256 ;;
  *hba_file*) printf '%s\\n' {destination.as_posix()} ;;
esac''',
    )
    write_shell_script(
        bin_dir / "runuser",
        f'''case "$*" in
  *'--port 5433'*'current_setting'*) printf '%s\\n' '5433|{data_directory}|{start_time}' ;;
  *'--port 5433'*'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'--port 5433'*'SHOW hba_file'*) printf '%s\\n' {destination} ;;
  *'--port 5433'*'pg_hba_file_rules'*) {":" if parser_query_succeeds else "exit 91"} ;;
  *) exit 91 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "pg_ctlcluster",
        f'''case "$3" in
  status) exit 0 ;;
  *) printf '%s\\n' "$*" >> {restart_log} ;;
esac''',
    )
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    if parser_query_succeeds:
        converged = subprocess.run(
            (
                "sh",
                "-ceu",
                render_postgresql_native_configuration_script(
                    plan,
                    hba_stage=stage.as_posix(),
                    hba_final=destination.as_posix(),
                    hba_owner=None,
                    hba_group=None,
                ),
            ),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        assert converged.returncode == 0, converged.stderr
        assert converged.stdout == ""
        assert not restart_log.exists()

    inspected = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                plan,
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
                inspection=True,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert inspected.returncode == 0, inspected.stderr
    assert inspected.stdout == expected_inspection
    assert not restart_log.exists()


def test_direct_postgresql_operation_reports_no_change_for_a_validated_desired_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    """Yielding PostgreSQL's direct action after its native state is correct must fail this."""

    plan = build_postgresql_plan(environment_config(database_port=5433))
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(plan.hba, encoding="utf-8")
    destination.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_300
    _write_postmaster_pid(data_directory, port=5433, start_time=start_time)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_shell_script(bin_dir / "pg_lsclusters", f"printf '16 main 5433 online postgres {data_directory} /log\\n'")
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''case "${{5:-$4}}" in
  data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
  hba_file) printf '%s\\n' {destination.as_posix()} ;;
  listen_addresses) printf '%s\\n' 127.0.0.1 ;;
  port) printf '%s\\n' 5433 ;;
  password_encryption) printf '%s\\n' scram-sha-256 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "stat",
        f'''case "$*" in
  *'%U:%G:%a'*{destination.as_posix()}) printf '%s\\n' root:postgres:640 ;;
  *'%U:%G:%a'*{destination.parent.as_posix()}) printf '%s\\n' root:postgres:750 ;;
  *) exec /usr/bin/stat "$@" ;;
esac''',
    )
    write_shell_script(
        bin_dir / "postgres",
        f'''case "$*" in
  *listen_addresses*) printf '%s\\n' 127.0.0.1 ;;
  *port*) printf '%s\\n' 5433 ;;
  *password_encryption*) printf '%s\\n' scram-sha-256 ;;
  *hba_file*) printf '%s\\n' {destination.as_posix()} ;;
esac''',
    )
    write_shell_script(
        bin_dir / "runuser",
        f'''case "$*" in
  *'--port 5433'*'current_setting'*) printf '%s\\n' '5433|{data_directory}|{start_time}' ;;
  *'--port 5433'*'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'--port 5433'*'SHOW hba_file'*) printf '%s\\n' {destination} ;;
  *'--port 5433'*'pg_hba_file_rules'*) ;;
  *) exit 91 ;;
esac''',
    )
    write_shell_script(bin_dir / "pg_ctlcluster", "case \"$3\" in status) exit 0 ;; *) exit 91 ;; esac")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host("@local")
    state.activate_host(host)
    remote = PyinfraRemote(host, environment_config(database_port=5433), inventory=inventory, state=state)

    @deploy("Converged direct PostgreSQL")
    def converge() -> None:
        postgresql._configure_postgresql_cluster(
            plan,
            name="Validate and configure PostgreSQL",
            _sudo=False,
            hba_stage=stage.as_posix(),
            hba_final=destination.as_posix(),
            hba_owner=None,
            hba_group=None,
        )

    assert remote.run_deploy(converge) == ChangeSet(changed=False)


def test_native_mutation_refuses_a_contradictory_postmaster_identity(tmp_path: Path) -> None:
    """A pid file for another data directory must never authorize a restart."""

    plan = build_postgresql_plan(environment_config(database_port=5433))
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(plan.hba, encoding="utf-8")
    destination.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    other_data_directory = tmp_path / "other-data"
    other_data_directory.mkdir()
    (data_directory / "postmaster.pid").write_text(
        f"{os.getpid()}\n{other_data_directory}\n1725000400\n5432\n"
        "/var/run/postgresql\n127.0.0.1\n1\nready \n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    write_shell_script(
        bin_dir / "pg_lsclusters",
        f"printf '16 main 5433 online postgres {data_directory} /log\\n'",
    )
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''case "${{5:-$4}}" in
  data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
  *) exit 91 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "pg_ctlcluster",
        f"printf '%s\\n' \"$*\" >> {restart_log}",
    )
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    refused = subprocess.run(
        (
            "sh",
            "-ceu",
            render_postgresql_native_configuration_script(
                plan,
                hba_stage=stage.as_posix(),
                hba_final=destination.as_posix(),
                hba_owner=None,
                hba_group=None,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert refused.returncode == int(ExitStatus.SAFETY)
    assert "ambiguous PostgreSQL runtime state" in refused.stderr
    assert not restart_log.exists()


def _write_postmaster_pid(data_directory: Path, *, port: int, start_time: int) -> None:
    (data_directory / "postmaster.pid").write_text(
        "\n".join(
            (
                str(os.getpid()),
                data_directory.as_posix(),
                str(start_time),
                str(port),
                "/var/run/postgresql",
                "127.0.0.1",
                "1",
                "ready ",
                "",
            )
        ),
        encoding="utf-8",
    )


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
            CommandResult(0, "changed=1\n"),
            CommandResult(0),
            CommandResult(0),
            CommandResult(0, "1\n"),
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
    assert commands[3][-len(plan.role_setup_argv) :] == plan.role_setup_argv
    assert commands[4][-len(plan.database_creation_argv) :] == plan.database_creation_argv
    assert commands[-1] == (
        "env",
        "PGPASSFILE=/etc/taskman/pgpass",
        "psql",
        "--no-psqlrc",
        "--set=ON_ERROR_STOP=1",
        "--host",
        "127.0.0.1",
        "--port",
        "5432",
        "--username",
        "taskman",
        "--dbname",
        "taskman_prod",
        "--command",
        "SELECT 1",
    )
    assert all(canary not in " ".join(command) for command in commands)
    assert remote.calls[3][1]["stdin"] == render_role_password_input(plan.role, canary)
    assert remote.calls[3][1]["sensitive"] is True


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
            CommandResult(0, "changed=0\n"),
            CommandResult(0, "1\n"),
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


def test_pgpass_installer_uses_sensitive_standard_input_and_enforces_root_mode_600() -> None:
    """Persisting pgpass insecurely or exposing it in command text must fail this."""

    remote = ScriptedRemote.from_responses([CommandResult(0, "changed=1\n")])
    canary = b"pgpass-sensitive-canary\n"

    result = install_pgpass(remote, canary)

    assert result == ChangeSet(changed=True, operations=("pgpass",))
    command, kwargs = remote.calls[0]
    assert command[-1] == "/etc/taskman/pgpass"
    assert "pgpass-sensitive-canary" not in " ".join(command)
    assert kwargs["stdin"] == canary
    assert kwargs["sensitive"] is True


class _PeerOnlyAdministrativeRemote:
    """Representative HBA: postgres peer works only on the Unix socket."""

    def __init__(self) -> None:
        self.administrative_connections: list[tuple[str, str]] = []
        self.application_connections: list[tuple[str, str]] = []

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        command = " ".join(argv)
        if "taskman-pgpass" in argv:
            return CommandResult(0, "changed=1\n")
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
            return CommandResult(0, "1\n")
        return CommandResult(0)
