from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import pytest

from tests.support.remotes import ScriptedRemote
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host.acceptance import (
    ProvisioningState,
    _loopback,
    _provisioning_state,
    validate_provisionable_host,
)
from taskman_ops.host.facts import (
    MINIMUM_DISK_BYTES,
    MINIMUM_MEMORY_BYTES,
    HostFacts,
    Listener,
    _listener_owners,
)
from taskman_ops.remote import CommandResult

from tests.support.environments import environment_config, valid_environment
from tests.support.caddy import authority_result
from tests.support.listeners import write_ss_fixture


@pytest.fixture(autouse=True)
def inspected_proxy(monkeypatch):
    monkeypatch.setattr("taskman_ops.host.facts.inspect_caddy", lambda *_args: authority_result())


def direct_dns(_hostname: str) -> tuple[str, ...]:
    return ("203.0.113.10",)


def managed_postgresql_responses(*, database_listeners: tuple[str, ...]) -> list[CommandResult]:
    responses = fact_responses(postgres=True)
    responses[8] = CommandResult(
        0,
        "LISTEN 0 4096 0.0.0.0:80 0.0.0.0:*\n"
        "LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\n"
        + "".join(f"LISTEN 0 4096 {listener} *:*\n" for listener in database_listeners),
    )
    responses[9] = CommandResult(0, "/etc/caddy/Caddyfile\n")
    responses[10] = CommandResult(0, "caddy.service enabled\n")
    responses[11] = CommandResult(0, "taskman:x:1000:1000::/var/lib/taskman:/usr/sbin/nologin\n")
    responses[12] = CommandResult(0, "taskman:x:1000:1000::\n")
    responses[16] = CommandResult(0, "taskman_prod\n")
    responses[-1] = CommandResult(
        0,
        'LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:(("caddy",pid=123,fd=6))\n'
        'LISTEN 0 4096 0.0.0.0:443 0.0.0.0:* users:(("caddy",pid=123,fd=7))\n',
    )
    return responses



def fact_responses(*, postgres: bool = False) -> list[CommandResult]:
    responses = [
        CommandResult(0, 'ID=ubuntu\nVERSION_ID="26.04"\n'),
        CommandResult(0, "x86_64\n"),
        CommandResult(0, "systemd\n"),
        CommandResult(0),
        CommandResult(0, f"{MINIMUM_MEMORY_BYTES}\n"),
        CommandResult(0, f"Avail\n{MINIMUM_DISK_BYTES}\n"),
        CommandResult(0, f"Avail\n{MINIMUM_DISK_BYTES}\n"),
        CommandResult(0, "2202\n"),
        CommandResult(0, "LISTEN 0 4096 *:2202 0.0.0.0:*\n"),
        CommandResult(0),
        CommandResult(0),
        CommandResult(2),
        CommandResult(2),
        CommandResult(2),
        CommandResult(1),
    ]
    if postgres:
        responses[13] = CommandResult(0, "postgres:x:111:117:PostgreSQL administrator:/var/lib/postgresql:/bin/bash\n")
        responses[14] = CommandResult(0, "/usr/bin/psql\n")
        responses.extend((CommandResult(0), CommandResult(0)))
    responses.extend(
        (
            CommandResult(0, 'LISTEN 0 4096 *:2202 0.0.0.0:* users:(("sshd",pid=101,fd=3))\n'),
        )
    )
    return responses


def test_provisionable_pristine_host_returns_normalized_immutable_facts() -> None:
    remote = ScriptedRemote.from_responses(fact_responses())

    discovery = validate_provisionable_host(
        remote,
        environment_config(),
        resolver=direct_dns,
    )
    facts = discovery.facts

    assert isinstance(facts, HostFacts)
    assert discovery.state is ProvisioningState.PRISTINE
    assert facts.os_id == "ubuntu"
    assert facts.ubuntu_release == "26.04"
    assert facts.architecture == "amd64"
    assert facts.pid1 == "systemd"
    assert facts.sudo_available is True
    assert facts.postgres_available is False
    assert facts.postgres_sudo_available is None
    assert facts.active_ssh_port == 2202
    assert facts.memory_bytes == MINIMUM_MEMORY_BYTES
    assert facts.available_disk_bytes == MINIMUM_DISK_BYTES
    assert facts.backup_available_disk_bytes == MINIMUM_DISK_BYTES
    assert facts.dns_addresses == ("203.0.113.10",)
    assert facts.listeners[0].port == 2202
    assert not remote.responses
    with pytest.raises(FrozenInstanceError):
        facts.architecture = "arm64"  # type: ignore[misc]


def test_listener_owner_parser_accepts_ss_field_padding() -> None:
    listener_owners = (
        'LISTEN 0      4096               *:80         *:* users:(("caddy",pid=402,fd=6))                      \n'
        'LISTEN 0\t4096\t*:443\t*:*\tusers:(("caddy",pid=402,fd=7))\t\t\n'
    )

    assert _listener_owners(listener_owners) == {
        Listener("*", 80): ("caddy", 402),
        Listener("*", 443): ("caddy", 402),
    }


@pytest.mark.parametrize('socket_state', ['valid', 'malformed', 'oversized'])
def test_bounded_owner_query_retains_public_and_configured_taskman_evidence(tmp_path, monkeypatch, socket_state):
    from taskman_ops.host.facts import collect_host_facts
    from taskman_ops.host_helper.commands import run_command
    from taskman_ops.remote import PyinfraRemote

    config = environment_config(application_port=4101, distribution_port=9201)
    query = ('ss', '-H', '-ltnp', '( sport = :80 or sport = :443 or sport = :4101 or sport = :9201 )')
    public = ''.join(f'LISTEN 0 4096 0.0.0.0:{port} *:* users:(("caddy",pid=123,fd=8))\n' for port in (80, 443))
    taskman = ''.join(f'LISTEN 0 4096 127.0.0.1:{port} *:* users:(("beam.smp",pid=812,fd=9))\n' for port in (4101, 9201))
    selected = public + taskman
    if socket_state == 'malformed':
        selected = selected.replace('pid=812,fd=9))', 'pid=812,fd=9)) unexpected')
    if socket_state == 'oversized':
        selected += ' ' * (16 * 1024)
    executable = write_ss_fixture(tmp_path, query[1:], selected.encode())
    responses = fact_responses()
    responses[8] = CommandResult(0, public + taskman + 'LISTEN 0 4096 *:10050 *:*\n')
    responses[9] = CommandResult(0, '/etc/systemd/system/taskman.service\tregular file:root:root:644\n')
    responses[10] = CommandResult(0, 'taskman.service enabled\n')
    responses[-1] = CommandResult(0)
    responses.append(CommandResult(0,
        'active=active\npid=812\ncgroup=/system.slice/taskman.service\n'
        'executable=/opt/taskman/releases/release-a/erts-16.0/bin/beam.smp\n'
        'owner=taskman:taskman\nprocess_cgroup=/system.slice/taskman.service\n'
        'release_root=/opt/taskman/releases/release-a\n'))
    scripted = ScriptedRemote.from_responses(responses)
    remote = PyinfraRemote(object(), config)
    owner_calls = []

    def bounded_command(argv, **kwargs):
        response = scripted.run(argv)
        if argv[:3] == ('ss', '-H', '-ltnp'):
            owner_calls.append((argv, kwargs))
            assert kwargs['sudo'] is True
            assert kwargs['stdout_limit'] == 16 * 1024
            result = run_command((str(executable), *argv[1:]), timeout_seconds=kwargs['timeout'],
                                 output_limit=kwargs['stdout_limit'])
            return CommandResult(result.returncode, result.stdout.decode(), result.stderr.decode())
        return response

    monkeypatch.setattr(remote, '_run_bounded_command', bounded_command)
    if socket_state == 'oversized':
        with pytest.raises(OpsError) as error:
            collect_host_facts(remote, config, resolver=direct_dns, caddy_authority=authority_result(branch='existing', active=True))
        assert error.value.status is ExitStatus.REMOTE_PREFLIGHT
    else:
        facts = collect_host_facts(remote, config, resolver=direct_dns, caddy_authority=authority_result(branch='existing', active=True))
        assert Listener('*', 10050) in facts.listeners
        assert ('ss', '-H', '-ltn') in [argv for argv, _ in scripted.calls]
        assert not scripted.responses
        if socket_state == 'valid':
            assert facts.taskman_listener_owners == (
                (Listener('127.0.0.1', 4101), ('beam.smp', 812)),
                (Listener('127.0.0.1', 9201), ('beam.smp', 812)),
            )
            assert _provisioning_state(facts, config) is ProvisioningState.PARTIAL
        else:
            with pytest.raises(OpsError) as error:
                _provisioning_state(facts, config)
            assert error.value.status is ExitStatus.SAFETY
    assert [argv for argv, _ in owner_calls] == [query]


def test_listener_owner_parser_omits_shared_socket_owners_without_discarding_unique_owners() -> None:
    """A shared SSH socket must not invalidate independent, owned Caddy listeners."""

    listener_owners = (
        'LISTEN 0 4096 *:22 0.0.0.0:* users:(("sshd",pid=1115,fd=3),("systemd",pid=1,fd=199))\n'
        'LISTEN 0 4096 *:80 0.0.0.0:* users:(("caddy",pid=402,fd=6))\n'
        'LISTEN 0 4096 *:443 0.0.0.0:* users:(("caddy",pid=402,fd=7))\n'
    )

    assert _listener_owners(listener_owners) == {
        Listener("*", 80): ("caddy", 402),
        Listener("*", 443): ("caddy", 402),
    }


@pytest.mark.parametrize(
    "listener_owners",
    (
        'LISTEN 0 4096 *:80 *:* users:(("caddy",pid=402,fd=6),("sidecar",pid=403,fd=7)\n',
        'LISTEN 0 4096 *:80 *:* users:(("caddy",pid=402,fd=6)) unexpected\n',
    ),
)
def test_listener_owner_parser_rejects_malformed_owner_records(
    listener_owners: str,
) -> None:
    assert _listener_owners(listener_owners) is None


def test_listener_owner_parser_rejects_a_duplicate_after_an_omitted_shared_socket() -> None:
    listener_owners = (
        'LISTEN 0 4096 *:22 0.0.0.0:* users:(("sshd",pid=1115,fd=3),("systemd",pid=1,fd=199))\n'
        'LISTEN 0 4096 *:22 0.0.0.0:* users:(("sshd",pid=1115,fd=3))\n'
    )

    assert _listener_owners(listener_owners) is None












def test_managed_postgresql_bracketed_ipv6_loopback_is_admitted_as_partial() -> None:
    remote = ScriptedRemote.from_responses(
        managed_postgresql_responses(database_listeners=("127.0.0.1:5432", "[::1]:5432"))
    )

    discovery = validate_provisionable_host(
        remote,
        environment_config(),
        resolver=direct_dns,
        caddy_authority=authority_result(branch="existing", active=True, imported=True),
    )

    assert discovery.state is ProvisioningState.PARTIAL
    assert Listener("[::1]", 5432) in discovery.facts.listeners


@pytest.mark.parametrize(
    "address",
    (
        "[0.0.0.0]",
        "[::]",
        "[203.0.113.10]",
        "[2001:db8::10]",
        "[::1",
        "::1]",
        "[::1]unexpected",
    ),
)
def test_loopback_rejects_bracketed_wildcard_public_and_malformed_addresses(address: str) -> None:
    assert not _loopback(address)










def test_resource_based_pre_caddy_partial_state_remains_safe_without_any_public_listener() -> None:
    """The Caddy ownership proof must not reject a legitimate earlier convergence boundary."""

    responses = fact_responses()
    responses[9] = CommandResult(0, "/etc/taskman\tdirectory:root:taskman:750\n")
    discovery = validate_provisionable_host(
        ScriptedRemote.from_responses(responses),
        environment_config(),
        resolver=direct_dns,
    )

    assert discovery.state is ProvisioningState.PARTIAL
    assert discovery.caddy_state.value == "absent"


def test_resource_based_admission_refuses_an_unverified_managed_directory() -> None:
    """A directory name alone cannot prove ownership of an interrupted installation."""

    responses = fact_responses()
    responses[9] = CommandResult(0, "/opt/taskman\n")

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            ScriptedRemote.from_responses(responses),
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.SAFETY


def test_resource_based_admission_refuses_a_foreign_taskman_account() -> None:
    """The reserved account name is not sufficient evidence of Taskman ownership."""

    responses = fact_responses()
    responses[11] = CommandResult(0, "taskman:x:1000:1000::/root:/bin/bash\n")
    responses[12] = CommandResult(0, "taskman:x:1000:\n")

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            ScriptedRemote.from_responses(responses),
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.SAFETY






def test_unsupported_platform_is_refused_only_after_all_facts_are_collected() -> None:
    responses = fact_responses()
    responses[0] = CommandResult(0, 'ID=debian\nVERSION_ID="13"\n')
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.INVALID
    assert not remote.responses


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (0, CommandResult(0, 'ID=ubuntu\nVERSION_ID="24.04"\n')),
        (1, CommandResult(0, "aarch64\n")),
        (2, CommandResult(0, "init\n")),
        (4, CommandResult(0, f"{MINIMUM_MEMORY_BYTES - 1}\n")),
        (5, CommandResult(0, f"Avail\n{MINIMUM_DISK_BYTES - 1}\n")),
        (6, CommandResult(0, f"Avail\n{MINIMUM_DISK_BYTES - 1}\n")),
    ],
)
def test_unsupported_host_facts_map_to_status_two(index: int, result: CommandResult) -> None:
    responses = fact_responses()
    responses[index] = result
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (3, CommandResult(1, stderr="sudo unavailable")),
        (7, CommandResult(0, "22\n")),
    ],
)
def test_privilege_or_active_connection_port_failures_map_to_status_five(
    index: int, result: CommandResult
) -> None:
    responses = fact_responses()
    responses[index] = result
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT


def test_failed_required_fact_command_maps_to_status_five_after_the_snapshot() -> None:
    responses = fact_responses()
    responses[0] = CommandResult(1, stderr="os release unavailable")
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert not remote.responses


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (9, CommandResult(1, stderr="path inspection unavailable")),
        (10, CommandResult(1, stderr="unit inspection unavailable")),
        (11, CommandResult(1, stderr="passwd inspection unavailable")),
        (12, CommandResult(1, stderr="group inspection unavailable")),
    ],
)
def test_inability_to_inspect_non_database_managed_state_refuses_preflight(
    index: int, result: CommandResult
) -> None:
    responses = fact_responses()
    responses[index] = result
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert not remote.responses


def test_pristine_host_skips_postgresql_sudo_and_database_discovery() -> None:
    remote = ScriptedRemote.from_responses(fact_responses())

    discovery = validate_provisionable_host(
        remote,
        environment_config(),
        resolver=direct_dns,
    )
    facts = discovery.facts

    assert facts.postgres_available is False
    assert facts.postgres_sudo_available is None
    assert ("sudo", "-n", "-u", "postgres", "true") not in [argv for argv, _kwargs in remote.calls]
    assert not any("psql -Atq" in " ".join(argv) for argv, _kwargs in remote.calls)


def test_absent_managed_units_pass_native_systemd_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        pytest.skip("native systemctl is required")
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    (units / "ssh.service").write_text("[Service]\nExecStart=/usr/bin/true\n")
    remote = ScriptedRemote.from_responses(fact_responses())
    scripted_run = remote.run

    def run(argv, **kwargs):
        response = scripted_run(argv, **kwargs)
        if tuple(argv[:2]) == ("systemctl", "list-unit-files"):
            result = subprocess.run(
                [systemctl, "--root", str(tmp_path), *argv[1:]],
                capture_output=True, text=True, check=False,
            )
            return CommandResult(result.returncode, result.stdout, result.stderr)
        return response

    monkeypatch.setattr(remote, "run", run)
    discovery = validate_provisionable_host(
        remote,
        environment_config(),
        resolver=direct_dns,
    )
    facts = discovery.facts
    assert facts.existing_units == ()
    assert facts.failed_checks == ()


@pytest.mark.parametrize("absence_status", [1, 127])
def test_absent_postgresql_passes_shell_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, absence_status: int
) -> None:
    remote = ScriptedRemote.from_responses(fact_responses())
    scripted_run = remote.run

    def run(argv, **kwargs):
        response = scripted_run(argv, **kwargs)
        if tuple(argv[:2]) == ("sh", "-c") and "command -v psql" in argv[2]:
            result = subprocess.run(
                ["/bin/sh", "-c", f"command() {{ return {absence_status}; }}\n{argv[2]}"],
                env={"PATH": str(tmp_path)},
                capture_output=True, text=True, check=False,
            )
            return CommandResult(result.returncode, result.stdout, result.stderr)
        return response

    monkeypatch.setattr(remote, "run", run)
    discovery = validate_provisionable_host(
        remote,
        environment_config(),
        resolver=direct_dns,
    )
    facts = discovery.facts
    assert facts.postgres_available is False
    assert facts.failed_checks == ()


def test_present_postgresql_requires_sudo_and_detects_a_managed_database() -> None:
    responses = fact_responses(postgres=True)
    responses[16] = CommandResult(0, "taskman_prod\n")
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.SAFETY


def test_partial_postgresql_installation_refuses_without_running_privileged_inspection() -> None:
    responses = fact_responses()
    responses[14] = CommandResult(0, "postgres:x:111:117:PostgreSQL administrator:/var/lib/postgresql:/bin/bash\n")
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert ("sudo", "-n", "-u", "postgres", "true") not in [argv for argv, _kwargs in remote.calls]
    assert not any("psql -Atq" in " ".join(argv) for argv, _kwargs in remote.calls)


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (15, CommandResult(1, stderr="postgres sudo unavailable")),
        (16, CommandResult(2, stderr="psql connection unavailable")),
    ],
)
def test_inability_to_inspect_present_postgresql_refuses_preflight(
    index: int, result: CommandResult
) -> None:
    responses = fact_responses(postgres=True)
    responses[index] = result
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT


def test_preflight_requests_integer_memory_and_nearest_existing_ancestor_capacity() -> None:
    nested_config = EnvironmentConfig.model_validate(
        valid_environment(
            install_root="/srv/taskman/absent/managed",
            backup_root="/var/backups/taskman/absent/backups",
        )
    )
    remote = ScriptedRemote.from_responses(fact_responses())

    validate_provisionable_host(
        remote,
        nested_config,
        resolver=direct_dns,
    )

    memory_command, _kwargs = remote.calls[4]
    managed_disk_command, _kwargs = remote.calls[5]
    backup_disk_command, _kwargs = remote.calls[6]
    assert memory_command == (
        "sh",
        "-c",
        "awk '/MemTotal:/{printf \"%.0f\\n\", $2 * 1024; exit}' /proc/meminfo",
    )
    assert managed_disk_command == (
        "sh",
        "-c",
        'path=$1; while [ ! -e "$path" ]; do parent=${path%/*}; [ "$parent" != "$path" ] || exit 1; path=$parent; done; df -B1 --output=avail "$path"',
        "taskman-capacity",
        "/srv/taskman/absent/managed",
    )
    assert backup_disk_command == (
        "sh",
        "-c",
        'path=$1; while [ ! -e "$path" ]; do parent=${path%/*}; [ "$parent" != "$path" ] || exit 1; path=$parent; done; df -B1 --output=avail "$path"',
        "taskman-capacity",
        "/var/backups/taskman/absent/backups",
    )


def test_direct_public_dns_must_include_only_the_configured_vps_address() -> None:
    remote = ScriptedRemote.from_responses(fact_responses())

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=lambda _hostname: ("198.51.100.7",),
            )

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (8, CommandResult(0, "LISTEN 0 4096 *:4000 0.0.0.0:*\n")),
        (9, CommandResult(0, "/opt/taskman\n")),
        (10, CommandResult(0, "taskman.service enabled\n")),
        (11, CommandResult(0, "taskman:x:1000:1000::/nonexistent:/usr/sbin/nologin\n")),
        (12, CommandResult(0, "taskman:x:1000:1000::/nonexistent:/usr/sbin/nologin\n")),
    ],
)
def test_foreign_or_unverifiable_managed_listener_path_unit_or_account_is_a_safety_refusal(
    index: int, result: CommandResult
) -> None:
    responses = fact_responses()
    responses[index] = result
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            environment_config(),
            resolver=direct_dns,
            )

    assert raised.value.status is ExitStatus.SAFETY


def test_taskman_listener_requires_the_exact_managed_beam_process() -> None:
    """A loopback port plus a unit file cannot authorize a foreign process."""

    base = validate_provisionable_host(
        ScriptedRemote.from_responses(fact_responses()),
        environment_config(),
        resolver=direct_dns,
    )
    listener = Listener("127.0.0.1", 4000)
    managed = replace(
        base.facts,
        listeners=(listener,),
        existing_paths=(PurePosixPath("/etc/systemd/system/taskman.service"),),
        path_metadata=((PurePosixPath("/etc/systemd/system/taskman.service"), "regular file:root:root:644"),),
        existing_units=("taskman.service",),
        existing_accounts=("taskman",),
        taskman_account_compatible=True,
        taskman_service_pid=812,
        taskman_service_executable="/opt/taskman/releases/release-a/erts-16.0/bin/beam.smp",
        taskman_service_owner="taskman:taskman",
        taskman_service_cgroup="/system.slice/taskman.service",
        taskman_service_release_root="/opt/taskman/releases/release-a",
        taskman_listener_owners=((listener, ("beam.smp", 812)),),
    )

    assert _provisioning_state(managed, environment_config()) is ProvisioningState.PARTIAL
    for foreign in (
        replace(managed, taskman_listener_owners=((listener, ("beam.smp", 813)),)),
        replace(managed, taskman_listener_owners=()),
        replace(managed, taskman_service_executable="/usr/local/bin/foreign-beam.smp"),
        replace(managed, taskman_service_owner="root:root"),
    ):
        with pytest.raises(OpsError) as raised:
            _provisioning_state(foreign, environment_config())
        assert raised.value.status is ExitStatus.SAFETY


def test_precreated_database_without_taskman_account_requires_validated_schema_proof():
    from taskman_ops.host.facts import collect_host_facts
    config = environment_config()
    remote = ScriptedRemote.from_responses(fact_responses())
    facts = collect_host_facts(remote, config, resolver=direct_dns)
    cluster = {"version": "18", "name": "main", "port": 5432,
               "data_directory": "/var/lib/postgresql/18/main",
               "config_file": "/etc/postgresql/18/main/postgresql.conf",
               "hba_file": "/etc/postgresql/18/main/pg_hba.conf"}
    pg = {"branch": "existing", "cluster": cluster, "role": "compatible",
          "database": "compatible", "access": "unproven"}
    facts = replace(facts, postgres_available=True, existing_databases=(config.database_name,),
                    postgresql=pg, initial_database_empty=True)
    assert _provisioning_state(facts, config) is ProvisioningState.PARTIAL
    with pytest.raises(OpsError):
        _provisioning_state(replace(facts, initial_database_empty=False), config)


def test_provision_facts_use_selected_authority_without_a_second_catalog_query():
    config = environment_config()
    responses = fact_responses(postgres=True)
    del responses[15:17]
    remote = ScriptedRemote.from_responses(responses)
    observed = {
        "postgresql": {"branch": "existing", "cluster": {
            "version": "18", "name": "main", "port": 5432,
            "data_directory": "/var/lib/postgresql/18/main",
            "config_file": "/etc/postgresql/18/main/postgresql.conf",
            "hba_file": "/etc/postgresql/18/main/pg_hba.conf"},
            "role": "compatible", "database": "compatible", "access": "unproven"},
        "database_state": "ready", "initial_database_empty": True, "applied_migrations": (),
    }
    discovery = validate_provisionable_host(remote, config, resolver=direct_dns,
                                           provision_authority=observed)
    assert discovery.state is ProvisioningState.PARTIAL
    assert discovery.facts.existing_databases == ("taskman_prod",)
    assert discovery.facts.postgresql["cluster"]["version"] == "18"
    assert discovery.facts.initial_database_empty is True
    assert discovery.facts.failed_checks == ()


def test_operational_catalog_fallback_uses_the_configured_socket_port():
    from taskman_ops.host.facts import collect_host_facts
    config = environment_config(database_port=5439)
    remote = ScriptedRemote.from_responses(fact_responses(postgres=True))
    collect_host_facts(remote, config, resolver=direct_dns)
    queries = [argv for argv, _ in remote.calls if any("SELECT datname" in part for part in argv)]
    assert len(queries) == 1
    assert queries[0][-1] == "5439"
    assert '--host /var/run/postgresql --port "$1"' in queries[0][2]


@pytest.mark.parametrize("requested,branch,accepted", [
    (True, "configure-existing", True),
    (False, "configure-existing", False),
])
def test_provision_admission_binds_configuration_authority_to_requested_boolean(requested, branch, accepted):
    from taskman_ops.host_protocol import ProtocolError
    responses = fact_responses(postgres=True)
    del responses[15:17]
    remote = ScriptedRemote.from_responses(responses)
    observed = {"postgresql": {"branch": branch, "cluster": {
        "version": "18", "name": "main", "port": 5432,
        "data_directory": "/var/lib/postgresql/18/main",
        "config_file": "/etc/postgresql/18/main/postgresql.conf",
        "hba_file": "/etc/postgresql/18/main/pg_hba.conf"},
        "role": "compatible", "database": "compatible", "access": "unproven"},
        "database_state": "ready", "initial_database_empty": True, "applied_migrations": ()}
    if accepted:
        result = validate_provisionable_host(remote, environment_config(), resolver=direct_dns,
            provision_authority=observed, force_postgresql_config=requested)
        assert result.facts.postgresql["branch"] == branch
        assert result.state is ProvisioningState.PARTIAL
    else:
        with pytest.raises(ProtocolError):
            validate_provisionable_host(remote, environment_config(), resolver=direct_dns,
                provision_authority=observed, force_postgresql_config=requested)
        assert remote.calls == []
