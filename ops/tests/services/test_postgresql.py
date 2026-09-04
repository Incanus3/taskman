from __future__ import annotations

import os
from pathlib import Path
import pytest
import subprocess
import stat
from pyinfra.api import Config, Inventory, State, deploy
from pyinfra.api.deploy import add_deploy
from pyinfra.api.operations import run_ops

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

    expected = build_postgresql_plan(environment_config())
    assert postgresql.declare_postgresql(environment_config()) == expected

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
        postgresql.declare_postgresql(config)

    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    state.activate_host(inventory.get_host("@local"))
    add_deploy(state, converge)
    run_ops(state)

    staged_path = Path(remote_path("/etc/taskman/pg_hba.conf.staged"))
    assert stat.S_IMODE(staged_path.stat().st_mode) == 0o640


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
    inspection_script = render_postgresql_native_configuration_script(
        build_postgresql_plan(environment_config()), inspection=True
    )

    assert "set -eu" in script
    assert "pg_hba_file_rules" in script
    assert '--config-file="$config_file"' in script
    assert script.count("floor(extract(epoch from pg_postmaster_start_time()))::bigint") == 2
    assert inspection_script.count("floor(extract(epoch from pg_postmaster_start_time()))::bigint") == 1
    assert 'postgres_binary="/usr/lib/postgresql/$version/bin/postgres"' in script
    assert script.count('runuser -u postgres -- "$postgres_binary" --config-file="$config_file" -C') == 4
    assert 'postgres_binary="/usr/lib/postgresql/$version/bin/postgres"' in inspection_script
    assert inspection_script.count('runuser -u postgres -- "$postgres_binary" --config-file="$config_file" -C') == 4
    assert "-C listen_addresses | grep -Fx 127.0.0.1" in script
    assert '-C port | grep -Fx "$desired_port"' in script
    assert "-C password_encryption | grep -Fx scram-sha-256" in script
    assert script.index("pg_hba_file_rules") < script.rindex("pg_ctlcluster")


def test_native_configuration_does_not_normalize_an_ipv6_loopback_listener() -> None:
    plan = build_postgresql_plan(
        environment_config(database_host="::1", public_ipv6="2001:db8::1")
    )

    script = render_postgresql_native_configuration_script(plan)

    assert "normalize_loopback_listen_address" not in script


def test_native_configuration_places_the_final_hba_file_in_a_postgres_traversable_directory() -> None:
    script = render_postgresql_native_configuration_script(build_postgresql_plan(environment_config()))

    assert 'native_hba_file="/etc/postgresql/$version/$cluster/pg_hba.conf"' in script
    assert "install -d" not in script


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


def test_native_configuration_stops_before_restart_when_effective_postgres_probe_fails(tmp_path: Path) -> None:
    """A failed selected-version postgres probe must not authorize a restart."""

    plan = build_postgresql_plan(environment_config())
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(plan.hba, encoding="utf-8")
    destination.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_500
    _write_postmaster_pid(data_directory, port=5432, start_time=start_time)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    admin_log = tmp_path / "admin.log"
    restart_log = tmp_path / "restarts.log"
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
  */bin/postgres*) exit 91 ;;
  *current_setting*) printf '%s\\n' '5432|{data_directory}|{start_time}' ;;
  *'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'SHOW hba_file'*) printf '%s\\n' {destination} ;;
  *pg_hba_file_rules*) ;;
  *) exit 92 ;;
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
    assert not restart_log.exists()
    assert admin_log.read_text(encoding="utf-8").splitlines()[-1] == (
        "-u postgres -- /usr/lib/postgresql/16/bin/postgres "
        "--config-file=/etc/postgresql/16/main/postgresql.conf -C listen_addresses"
    )


def test_native_configuration_repairs_an_unquoted_desired_loopback_before_restart(tmp_path: Path) -> None:
    """An unquoted desired IPv4 listener is repaired and validated before restart."""

    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config=(
            "# preserve this comment\n"
            "listen_addresses = 127.0.0.1 # preserve the pg_conftool metadata\n"
            "other_setting = 127.0.0.1\n"
            "# listen_addresses = 127.0.0.1\n"
        ),
    )
    before_inode = fixture["config_file"].stat().st_ino
    before_config_stat = fixture["config_file"].stat()

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == 0, completed.stderr
    assert fixture["config_file"].read_text(encoding="utf-8") == (
        "# preserve this comment\n"
        "listen_addresses = '127.0.0.1' # preserve the pg_conftool metadata\n"
        "other_setting = 127.0.0.1\n"
        "# listen_addresses = 127.0.0.1\n"
    )
    assert fixture["config_file"].stat().st_ino != before_inode
    assert fixture["config_file"].stat().st_uid == before_config_stat.st_uid
    assert fixture["config_file"].stat().st_gid == before_config_stat.st_gid
    assert stat.S_IMODE(fixture["config_file"].stat().st_mode) == stat.S_IMODE(before_config_stat.st_mode)
    assert stat.S_IMODE(fixture["config_file"].stat().st_mode) == 0o640
    assert fixture["restart_log"].read_text(encoding="utf-8").splitlines() == ["16 main restart"]
    events = fixture["events"].read_text(encoding="utf-8").splitlines()
    assert events.index("effective") < events.index("restart")


def test_native_configuration_repairs_a_pristine_pg_conftool_write(tmp_path: Path) -> None:
    """A first pg_conftool write is normalized before native parsing validates it."""

    fixture = _native_configuration_fixture(tmp_path, initial_config="", listen_setting="")

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == 0, completed.stderr
    assert fixture["config_file"].read_text(encoding="utf-8") == (
        "listen_addresses = '127.0.0.1' # generated by pg_conftool\n"
    )
    assert fixture["restart_log"].read_text(encoding="utf-8").splitlines() == ["16 main restart"]


def test_native_configuration_reports_a_converged_repaired_listener_without_restart(tmp_path: Path) -> None:
    """A second inspection after repair reports no change and leaves the service untouched."""

    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = 127.0.0.1 # generated by pg_conftool\n",
    )

    first = _run_native_configuration_fixture(fixture)
    assert first.returncode == 0, first.stderr
    restarts_after_convergence = fixture["restart_log"].read_text(encoding="utf-8")

    second = _run_native_configuration_fixture(fixture)
    assert second.returncode == 0, second.stderr
    assert fixture["restart_log"].read_text(encoding="utf-8") == restarts_after_convergence

    inspected = _run_native_configuration_fixture(fixture, inspection=True)

    assert inspected.returncode == 0, inspected.stderr
    assert inspected.stdout == "changed=0\n"
    assert fixture["restart_log"].read_text(encoding="utf-8") == restarts_after_convergence


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
{_postgres_effective_settings_cases(destination, port=5432)}
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
{_postgres_effective_settings_cases(destination, port=5432)}
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

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert not reloaded.exists()
    assert not service_log.exists()
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
    rounded_start_time = start_time + 1
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
{_postgres_effective_settings_cases(destination, port=5433)}
      *'--port 5432'*'current_setting'*)
        case "$*" in
          *'floor(extract(epoch from pg_postmaster_start_time()))::bigint'*) printf '%s\\n' '5432|{data_directory}|{start_time}' ;;
          *) printf '%s\\n' '5432|{data_directory}|{rounded_start_time}' ;;
        esac
        ;;
  *'--port 5432'*'SHOW port'*) printf '%s\\n' 5432 ;;
  *'--port 5432'*'SHOW config_file'*) printf '%s\\n' /etc/postgresql/16/main/postgresql.conf ;;
  *'--port 5432'*'SHOW hba_file'*) printf '%s\\n' {destination} ;;
  *'--port 5432'*'pg_hba_file_rules'*) ;;
      *'--port 5433'*'current_setting'*)
        if [ -f {restarted} ]; then
          case "$*" in
            *'floor(extract(epoch from pg_postmaster_start_time()))::bigint'*) printf '%s\\n' '5433|{data_directory}|{start_time}' ;;
            *) printf '%s\\n' '5433|{data_directory}|{rounded_start_time}' ;;
          esac
        fi
        ;;
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
    admin_commands = admin_log.read_text(encoding="utf-8").splitlines()
    assert admin_commands == [
        f"-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SELECT current_setting('port'), current_setting('data_directory'), floor(extract(epoch from pg_postmaster_start_time()))::bigint",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SHOW config_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SHOW hba_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --command SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL",
        "-u postgres -- /usr/lib/postgresql/16/bin/postgres --config-file=/etc/postgresql/16/main/postgresql.conf -C listen_addresses",
        "-u postgres -- /usr/lib/postgresql/16/bin/postgres --config-file=/etc/postgresql/16/main/postgresql.conf -C port",
        "-u postgres -- /usr/lib/postgresql/16/bin/postgres --config-file=/etc/postgresql/16/main/postgresql.conf -C password_encryption",
        "-u postgres -- /usr/lib/postgresql/16/bin/postgres --config-file=/etc/postgresql/16/main/postgresql.conf -C hba_file",
        f"-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5433 --username postgres --dbname=postgres --command SELECT current_setting('port'), current_setting('data_directory'), floor(extract(epoch from pg_postmaster_start_time()))::bigint",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5433 --username postgres --dbname=postgres --command SHOW config_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5433 --username postgres --dbname=postgres --command SHOW hba_file",
        "-u postgres -- psql --no-psqlrc --tuples-only --no-align --field-separator | --host /var/run/postgresql --port 5433 --username postgres --dbname=postgres --command SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL",
    ]
    assert restart_log.read_text(encoding="utf-8").splitlines() == ["16 main restart"]


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
{_postgres_effective_settings_cases(destination, port=5433)}
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
    assert restart_log.read_text(encoding="utf-8").splitlines() == ["16 main restart"]


def test_native_configuration_refuses_a_stopped_selected_cluster(tmp_path: Path) -> None:
    """A stopped cluster is refused without offline HBA or service mutation."""

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
case "$*" in
{_postgres_effective_settings_cases(destination, port=5433)}
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
    rounded_start_time = start_time + 1
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
{_postgres_effective_settings_cases(destination, port=5433)}
  *'--port 5433'*'current_setting'*)
    case "$*" in
      *'floor(extract(epoch from pg_postmaster_start_time()))::bigint'*) printf '%s\\n' '5433|{data_directory}|{start_time}' ;;
      *) printf '%s\\n' '5433|{data_directory}|{rounded_start_time}' ;;
    esac
    ;;
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
{_postgres_effective_settings_cases(destination, port=5433)}
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


def _postgres_effective_settings_cases(destination: Path, *, port: int) -> str:
    """Emulate the selected-version postgres binary behind the runuser boundary."""

    return f"""  */bin/postgres*' -C listen_addresses') printf '%s\\n' 127.0.0.1 ;;
  */bin/postgres*' -C port') printf '%s\\n' {port} ;;
  */bin/postgres*' -C password_encryption') printf '%s\\n' scram-sha-256 ;;
  */bin/postgres*' -C hba_file') printf '%s\\n' {destination.as_posix()} ;;
"""


def _native_configuration_fixture(
    tmp_path: Path,
    *,
    initial_config: str,
    listen_setting: str = "127.0.0.1",
    destination_content: str | None = None,
    parser_mode: str = "clean",
    active_hba_file: Path | None = None,
    configured_hba_file: str | Path | None = None,
    restart_mode: str = "clean",
    cluster_state: str = "online",
) -> dict[str, object]:
    plan = build_postgresql_plan(environment_config())
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    stage.write_text(plan.hba, encoding="utf-8")
    destination.write_text(plan.hba if destination_content is None else destination_content, encoding="utf-8")
    destination.chmod(0o640)
    config_file = tmp_path / "postgresql.conf"
    config_file.write_text(initial_config, encoding="utf-8")
    config_file.chmod(0o640)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    start_time = 1_725_000_500
    _write_postmaster_pid(data_directory, port=5432, start_time=start_time)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    admin_log = tmp_path / "admin.log"
    events = tmp_path / "events.log"
    restart_log = tmp_path / "restarts.log"
    parser_count = tmp_path / "parser-count"
    active_hba = destination if active_hba_file is None else active_hba_file
    configured_hba = destination if configured_hba_file is None else configured_hba_file
    config_log = tmp_path / "config.log"

    write_shell_script(
        bin_dir / "pg_lsclusters",
        f"printf '16 main 5432 {cluster_state} postgres {data_directory} /log\\n'",
    )
    write_shell_script(
        bin_dir / "pg_conftool",
        f'''if [ "$1" = -s ]; then
  key=$5
  case "$key" in
    data_directory) printf '%s\\n' {data_directory.as_posix()} ;;
    hba_file) printf '%s\\n' {configured_hba} ;;
    listen_addresses)
      if grep -Eq "^[[:space:]]*listen_addresses[[:space:]]*=[[:space:]]*'127\\.0\\.0\\.1'" {config_file}; then
        printf '%s\\n' 127.0.0.1
      else
        printf '%s\\n' {listen_setting}
      fi
      ;;
    port) printf '%s\\n' 5432 ;;
    password_encryption) printf '%s\\n' scram-sha-256 ;;
  esac
elif [ "$3" = set ]; then
  printf '%s\\n' "$*" >> {config_log}
  case "$4" in
    listen_addresses) printf '%s\\n' 'listen_addresses = 127.0.0.1 # generated by pg_conftool' >> {config_file} ;;
  esac
else
  exit 93
fi''',
    )
    write_shell_script(
        bin_dir / "runuser",
        f'''printf '%s\\n' "$*" >> {admin_log}
case "$*" in
  */bin/postgres*' -C listen_addresses')
    printf '%s\\n' effective >> {events}
    if grep -Eq "^[[:space:]]*listen_addresses[[:space:]]*=[[:space:]]*'127\\.0\\.0\\.1'" {config_file}; then
      printf '%s\\n' 127.0.0.1
    else
      exit 91
    fi
    ;;
  */bin/postgres*' -C port') printf '%s\\n' 5432 ;;
  */bin/postgres*' -C password_encryption') printf '%s\\n' scram-sha-256 ;;
  */bin/postgres*' -C hba_file') printf '%s\\n' {destination.as_posix()} ;;
  *current_setting*) printf '%s\\n' '5432|{data_directory}|{start_time}' ;;
  *'SHOW config_file'*) printf '%s\\n' {config_file} ;;
  *'SHOW hba_file'*) printf '%s\\n' {active_hba} ;;
  *pg_hba_file_rules*)
    parser_count_value=$(cat {parser_count} 2>/dev/null || printf '0')
    parser_count_value=$((parser_count_value + 1))
    printf '%s\\n' "$parser_count_value" > {parser_count}
    case {parser_mode!r} in
      unavailable) exit 91 ;;
      error-after-first)
        if [ "$parser_count_value" -gt 1 ]; then printf '%s\\n' 'bad HBA entry'; fi
        ;;
      term-after-first)
        if [ "$parser_count_value" -gt 1 ]; then kill -TERM "$PPID"; fi
        ;;
      clean) ;;
      *) exit 92 ;;
    esac
    ;;
  *) exit 92 ;;
esac''',
    )
    write_shell_script(
        bin_dir / "pg_ctlcluster",
        f'''case "$3" in
  status) exit 0 ;;
  reload) printf '%s\\n' "$*" >> {restart_log}; printf '%s\\n' reload >> {events} ;;
  restart)
    printf '%s\\n' "$*" >> {restart_log}
    case {restart_mode!r} in
      fail) exit 91 ;;
      clean) printf '%s\\n' restart >> {events} ;;
      *) exit 92 ;;
    esac
    ;;
  *) exit 92 ;;
esac''',
    )

    return {
        "plan": plan,
        "stage": stage,
        "destination": destination,
        "config_file": config_file,
        "admin_log": admin_log,
        "events": events,
        "restart_log": restart_log,
        "parser_count": parser_count,
        "config_log": config_log,
        "environment": {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    }


def _run_native_configuration_fixture(
    fixture: dict[str, object], *, inspection: bool = False
) -> subprocess.CompletedProcess[str]:
    plan = fixture["plan"]
    stage = fixture["stage"]
    destination = fixture["destination"]
    config_file = fixture["config_file"]
    script = render_postgresql_native_configuration_script(
        plan,
        hba_stage=stage.as_posix(),
        hba_final=destination.as_posix(),
        hba_owner=None,
        hba_group=None,
        inspection=inspection,
    ).replace(
        'config_file="/etc/postgresql/$version/$cluster/postgresql.conf"',
        f'config_file="{config_file.as_posix()}"',
    )
    return subprocess.run(
        ("sh", "-ceu", script),
        check=False,
        capture_output=True,
        text=True,
        env=fixture["environment"],
    )


def test_native_configuration_derives_the_selected_ubuntu_hba_path_and_preserves_native_parent_metadata() -> None:
    """Native cluster paths must be selected from validated version/name facts."""

    script = render_postgresql_native_configuration_script(build_postgresql_plan(environment_config()))

    assert 'native_hba_file="/etc/postgresql/$version/$cluster/pg_hba.conf"' in script
    assert "install -d" not in script
    assert "hba_parent_state" not in script
    assert "/etc/postgresql/taskman/pg_hba.conf" not in script
    assert '[ "$hba_state" != postgres:postgres:640 ]' in script
    assert "install -o postgres -g postgres -m 0640 /etc/taskman/pg_hba.conf.staged \"$hba_candidate\"" in script


def test_native_configuration_refuses_unavailable_runtime_before_hba_replacement(tmp_path: Path) -> None:
    """An unavailable live parser must not authorize a candidate install."""

    old_hba = "local all all peer\n"
    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content=old_hba,
        parser_mode="unavailable",
    )

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert fixture["destination"].read_text(encoding="utf-8") == old_hba
    assert not Path(f"{fixture['destination']}.taskman-backup").exists()
    assert not fixture["restart_log"].exists()


def test_native_configuration_refuses_a_foreign_active_hba_path_before_replacement(tmp_path: Path) -> None:
    """A live process reading an unexpected HBA must never be redirected implicitly."""

    old_hba = "local all all peer\n"
    foreign_hba = tmp_path / "foreign-pg_hba.conf"
    foreign_hba.write_text(old_hba, encoding="utf-8")
    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content=old_hba,
        active_hba_file=foreign_hba,
    )

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert fixture["destination"].read_text(encoding="utf-8") == old_hba
    assert not fixture["parser_count"].exists()
    assert not fixture["restart_log"].exists()


def test_native_configuration_restores_hba_bytes_and_metadata_when_candidate_parser_fails(
    tmp_path: Path,
) -> None:
    """A parser rejection restores the exact prior file and leaves no recovery residue."""

    old_hba = "local all all peer\n"
    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content=old_hba,
        parser_mode="error-after-first",
    )
    destination = fixture["destination"]
    destination.chmod(0o600)

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert destination.read_text(encoding="utf-8") == old_hba
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not Path(f"{destination}.taskman-backup").exists()
    assert not fixture["restart_log"].exists()
    assert fixture["parser_count"].read_text(encoding="utf-8") == "2\n"


def test_native_configuration_cleans_recovery_artifact_after_a_successful_atomic_hba_install(
    tmp_path: Path,
) -> None:
    """Successful candidate validation removes only its private recovery files."""

    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content="local all all peer\n",
    )
    before_inode = fixture["destination"].stat().st_ino

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == 0, completed.stderr
    assert fixture["destination"].read_text(encoding="utf-8") == fixture["plan"].hba
    assert fixture["destination"].stat().st_ino != before_inode
    assert stat.S_IMODE(fixture["destination"].stat().st_mode) == 0o640
    assert not Path(f"{fixture['destination']}.taskman-backup").exists()
    assert fixture["restart_log"].read_text(encoding="utf-8").splitlines() == ["16 main restart"]


def test_native_configuration_repairs_a_known_legacy_hba_setting_without_touching_legacy_residue(
    tmp_path: Path,
) -> None:
    """A live native HBA can adopt the native startup setting from a partial run."""

    legacy_hba = tmp_path / "legacy-pg_hba.conf"
    legacy_hba.write_text("legacy residue\n", encoding="utf-8")
    legacy_hba.chmod(0o600)
    legacy_inode = legacy_hba.stat().st_ino
    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content="local all all peer\n",
        configured_hba_file=legacy_hba,
    )

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == 0, completed.stderr
    assert any(
        f"16 main set hba_file {fixture['destination']}" in line
        for line in fixture["config_log"].read_text(encoding="utf-8").splitlines()
    )
    assert fixture["destination"].read_text(encoding="utf-8") == fixture["plan"].hba
    assert legacy_hba.read_text(encoding="utf-8") == "legacy residue\n"
    assert legacy_hba.stat().st_ino == legacy_inode
    assert stat.S_IMODE(legacy_hba.stat().st_mode) == 0o600


def test_native_configuration_refuses_a_stopped_cluster_before_hba_replacement(tmp_path: Path) -> None:
    """Without a reachable endpoint, native parser validation cannot authorize replacement."""

    old_hba = "local all all peer\n"
    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content=old_hba,
        cluster_state="down",
    )

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert fixture["destination"].read_text(encoding="utf-8") == old_hba
    assert not fixture["parser_count"].exists()
    assert not fixture["restart_log"].exists()


def test_native_configuration_reports_a_converged_native_hba_without_restart(tmp_path: Path) -> None:
    """A successful transition must be a no-op when inspected again."""

    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content="local all all peer\n",
    )

    first = _run_native_configuration_fixture(fixture)
    assert first.returncode == 0, first.stderr
    restart_log = fixture["restart_log"].read_text(encoding="utf-8")

    second = _run_native_configuration_fixture(fixture)
    assert second.returncode == 0, second.stderr
    assert fixture["restart_log"].read_text(encoding="utf-8") == restart_log

    inspected = _run_native_configuration_fixture(fixture, inspection=True)
    assert inspected.returncode == 0, inspected.stderr
    assert inspected.stdout == "changed=0\n"


def test_native_configuration_refuses_preexisting_recovery_artifact_without_overwriting_it(
    tmp_path: Path,
) -> None:
    """An interrupted transition remains operator-owned and is never silently adopted."""

    old_hba = "local all all peer\n"
    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content=old_hba,
    )
    recovery = Path(f"{fixture['destination']}.taskman-backup")
    recovery.mkdir()
    marker = recovery / "operator-recovery-note"
    marker.write_text("inspect before retry\n", encoding="utf-8")

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert marker.read_text(encoding="utf-8") == "inspect before retry\n"
    assert fixture["destination"].read_text(encoding="utf-8") == old_hba
    assert not fixture["restart_log"].exists()


def test_native_configuration_preserves_native_hba_parent_metadata(tmp_path: Path) -> None:
    """Candidate installation must not impose a dedicated-HBA parent policy."""

    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content="local all all peer\n",
    )
    parent = fixture["destination"].parent
    before_mode = stat.S_IMODE(parent.stat().st_mode)
    before_inode = parent.stat().st_ino

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode == 0, completed.stderr
    assert stat.S_IMODE(parent.stat().st_mode) == before_mode
    assert parent.stat().st_ino == before_inode


def test_native_configuration_signal_during_candidate_parser_restores_hba_and_fails(
    tmp_path: Path,
) -> None:
    """An interrupted candidate transition must not be reported as successful."""

    old_hba = "local all all peer\n"
    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content=old_hba,
        parser_mode="term-after-first",
    )
    destination = fixture["destination"]
    destination.chmod(0o600)

    completed = _run_native_configuration_fixture(fixture)

    assert completed.returncode != 0
    assert destination.read_text(encoding="utf-8") == old_hba
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not Path(f"{destination}.taskman-backup").exists()
    assert not fixture["restart_log"].exists()


def test_native_configuration_retains_recovery_state_when_restart_fails_and_blocks_retry(
    tmp_path: Path,
) -> None:
    """A post-parser restart failure leaves a recoverable candidate for operator review."""

    old_hba = "local all all peer\n"
    fixture = _native_configuration_fixture(
        tmp_path,
        initial_config="listen_addresses = '127.0.0.1'\n",
        destination_content=old_hba,
        restart_mode="fail",
    )
    destination = fixture["destination"]
    before_state = (
        f"{destination.stat().st_uid}:{destination.stat().st_gid}:"
        f"{stat.S_IMODE(destination.stat().st_mode):o}"
    )

    failed = _run_native_configuration_fixture(fixture)

    assert failed.returncode != 0
    recovery = Path(f"{destination}.taskman-backup")
    assert recovery.is_dir()
    assert (recovery / "pg_hba.conf").read_text(encoding="utf-8") == old_hba
    assert (recovery / "metadata").read_text(encoding="utf-8") == f"{before_state}\n"
    assert destination.read_text(encoding="utf-8") == fixture["plan"].hba

    inspected = _run_native_configuration_fixture(fixture, inspection=True)
    assert inspected.returncode == 0, inspected.stderr
    assert inspected.stdout == "changed=1\n"

    retried = _run_native_configuration_fixture(fixture)
    assert retried.returncode == int(ExitStatus.SAFETY)
    assert destination.read_text(encoding="utf-8") == fixture["plan"].hba
    assert recovery.is_dir()


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
            CommandResult(3),
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
            CommandResult(0),
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
            return CommandResult(0, "1\n")
        return CommandResult(0)
