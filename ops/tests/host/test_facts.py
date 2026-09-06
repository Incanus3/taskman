from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import os
from pathlib import Path
import shlex
import subprocess

import pytest

from tests.fakes import ScriptedRemote
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host.acceptance import (
    ProvisioningState,
    validate_provisionable_host,
    validate_supported_host,
)
from taskman_ops.host.facts import (
    MINIMUM_DISK_BYTES,
    MINIMUM_MEMORY_BYTES,
    HostFacts,
    ProvisioningMarkerState,
    _CADDY_EVIDENCE_SCRIPT,
    _caddy_evidence,
)
from taskman_ops.remote import CommandResult

from tests.test_config import valid_environment


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment())


def direct_dns(_hostname: str) -> tuple[str, ...]:
    return ("203.0.113.10",)


_CADDYFILE = "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n"
_CADDYFILE_SHA256 = hashlib.sha256(_CADDYFILE.encode()).hexdigest()


def caddy_evidence(
    *,
    config_hash: str | None = None,
    config_metadata: str = "root:root:644",
    unit_metadata: str = "root:root:644",
    unit_verified: str = "clean",
) -> CommandResult:
    """Return one complete immutable snapshot of a running packaged Caddy."""

    expected_hash = config_hash or _CADDYFILE_SHA256
    return CommandResult(
        0,
        "\n".join(
            (
                "config=regular",
                f"config_hash={expected_hash}",
                f"config_metadata={config_metadata}",
                "unit_load=loaded",
                "unit_active=active",
                "unit_pid=402",
                "unit_cgroup=/system.slice/caddy.service",
                "unit_fragment=/usr/lib/systemd/system/caddy.service",
                f"unit_metadata={unit_metadata}",
                "unit_package=caddy",
                f"unit_verified={unit_verified}",
                "process_executable=/usr/bin/caddy",
                "process_arguments=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile",
                "process_cgroup=/system.slice/caddy.service",
                "",
            )
        ),
    )


def managed_caddy_responses(
    *,
    listener_owners: str,
    evidence: CommandResult | None = None,
) -> list[CommandResult]:
    """Model marker-anchored Caddy facts plus process ownership evidence."""

    responses = fact_responses()
    responses[8] = CommandResult(0, "LISTEN 0 4096 *:80 0.0.0.0:*\nLISTEN 0 4096 *:443 0.0.0.0:*\n")
    responses[9] = CommandResult(0, "/var/lib/taskman-provisioning.state\n/etc/caddy/Caddyfile\n")
    responses[10] = CommandResult(0, "managed\n")
    responses[11] = CommandResult(0, "caddy.service enabled\n")
    responses[-2:] = [CommandResult(0, listener_owners), evidence or caddy_evidence()]
    return responses


def absent_caddy_evidence() -> CommandResult:
    return CommandResult(
        0,
        "\n".join(
            (
                "config=absent",
                "config_hash=",
                "config_metadata=",
                "unit_load=not-found",
                "unit_active=inactive",
                "unit_pid=0",
                "unit_cgroup=",
                "unit_fragment=",
                "unit_metadata=",
                "unit_package=missing",
                "unit_verified=missing",
                "process_executable=",
                "process_arguments=",
                "process_cgroup=",
                "",
            )
        ),
    )


def inactive_caddy_evidence(*, configured: bool) -> CommandResult:
    config_hash = hashlib.sha256(_CADDYFILE.encode()).hexdigest() if configured else ""
    config_metadata = "root:root:644" if configured else ""
    return CommandResult(
        0,
        "\n".join(
            (
                f"config={'regular' if configured else 'absent'}",
                f"config_hash={config_hash}",
                f"config_metadata={config_metadata}",
                "unit_load=loaded",
                "unit_active=inactive",
                "unit_pid=0",
                "unit_cgroup=/system.slice/caddy.service",
                "unit_fragment=/usr/lib/systemd/system/caddy.service",
                "unit_metadata=root:root:644",
                "unit_package=caddy",
                "unit_verified=clean",
                "process_executable=",
                "process_arguments=",
                "process_cgroup=",
                "",
            )
        ),
    )


def test_caddy_evidence_verifies_the_unit_without_treating_its_managed_config_as_package_drift(
    tmp_path: Path,
) -> None:
    """Package-wide verification must not reject Taskman's owned Caddyfile bytes."""

    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(_CADDYFILE, encoding="utf-8")
    unit = tmp_path / "caddy.service"
    unit.write_text("[Service]\nExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(
        bin_dir / "systemctl",
        "case \"$3\" in "
        "--property=LoadState) printf loaded;; "
        "--property=ActiveState) printf inactive;; "
        "--property=MainPID) printf 0;; "
        "--property=ControlGroup) printf /system.slice/caddy.service;; "
        f"--property=FragmentPath) printf %s {shlex.quote(unit.as_posix())};; "
        "esac",
    )
    _fake_executable(bin_dir / "stat", "printf root:root:644")
    _fake_executable(
        bin_dir / "dpkg-query",
        "case \"$1\" in "
        "--showformat=*) printf installed;; "
        f"--search) printf 'caddy: %s\\n' {shlex.quote(unit.as_posix())};; "
        f"--control-show) printf '%s %s\\n' {hashlib.md5(unit.read_bytes()).hexdigest()} {shlex.quote(unit.as_posix().lstrip('/'))};; "
        "esac",
    )
    _fake_executable(bin_dir / "dpkg", "printf '??5?????? c /etc/caddy/Caddyfile\\n'")

    completed = subprocess.run(
        ("sh", "-ceu", _CADDY_EVIDENCE_SCRIPT, "taskman-caddy-ownership", caddyfile.as_posix()),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    evidence = _caddy_evidence(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert evidence is not None
    assert evidence["unit_verified"] == "clean"


def test_caddy_evidence_accepts_the_package_canonical_path_for_the_same_unit(
    tmp_path: Path,
) -> None:
    """usrmerge must not turn the package-owned Caddy unit into foreign state."""

    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(_CADDYFILE, encoding="utf-8")
    package_directory = tmp_path / "lib" / "systemd" / "system"
    package_directory.mkdir(parents=True)
    package_unit = package_directory / "caddy.service"
    package_unit.write_text("[Service]\nExecStart=/usr/bin/caddy\n", encoding="utf-8")
    (tmp_path / "usr").symlink_to(tmp_path / "lib", target_is_directory=True)
    unit = tmp_path / "usr" / "systemd" / "system" / "caddy.service"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(
        bin_dir / "systemctl",
        "case \"$3\" in "
        "--property=LoadState) printf loaded;; "
        "--property=ActiveState) printf inactive;; "
        "--property=MainPID) printf 0;; "
        "--property=ControlGroup) printf /system.slice/caddy.service;; "
        f"--property=FragmentPath) printf %s {shlex.quote(unit.as_posix())};; "
        "esac",
    )
    _fake_executable(bin_dir / "stat", "printf root:root:644")
    _fake_executable(
        bin_dir / "dpkg-query",
        "case \"$1\" in "
        "--showformat=*) printf installed;; "
        f"--search) printf 'caddy: %s\\n' {shlex.quote(package_unit.as_posix())};; "
        f"--control-show) printf '%s %s\\n' {hashlib.md5(package_unit.read_bytes()).hexdigest()} {shlex.quote(package_unit.as_posix().lstrip('/'))};; "
        "esac",
    )

    completed = subprocess.run(
        ("sh", "-ceu", _CADDY_EVIDENCE_SCRIPT, "taskman-caddy-ownership", caddyfile.as_posix()),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    evidence = _caddy_evidence(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert evidence is not None
    assert evidence["unit_package"] == "caddy"
    assert evidence["unit_verified"] == "clean"


def test_caddy_evidence_refuses_an_unresolvable_package_unit_path(tmp_path: Path) -> None:
    """A failed path-normalisation command must not compare as two empty paths."""

    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(_CADDYFILE, encoding="utf-8")
    unit = tmp_path / "caddy.service"
    unit.write_text("[Service]\nExecStart=/usr/bin/caddy\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(
        bin_dir / "systemctl",
        "case \"$3\" in "
        "--property=LoadState) printf loaded;; "
        "--property=ActiveState) printf inactive;; "
        "--property=MainPID) printf 0;; "
        "--property=ControlGroup) printf /system.slice/caddy.service;; "
        f"--property=FragmentPath) printf %s {shlex.quote(unit.as_posix())};; "
        "esac",
    )
    _fake_executable(bin_dir / "stat", "printf root:root:644")
    _fake_executable(
        bin_dir / "dpkg-query",
        "case \"$1\" in "
        "--showformat=*) printf installed;; "
        f"--search) printf 'caddy: %s\\n' {shlex.quote(unit.as_posix())};; "
        f"--control-show) printf '%s %s\\n' {hashlib.md5(unit.read_bytes()).hexdigest()} {shlex.quote(unit.as_posix().lstrip('/'))};; "
        "esac",
    )
    _fake_executable(bin_dir / "readlink", "exit 1")

    completed = subprocess.run(
        ("sh", "-ceu", _CADDY_EVIDENCE_SCRIPT, "taskman-caddy-ownership", caddyfile.as_posix()),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    evidence = _caddy_evidence(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert evidence is not None
    assert evidence["unit_package"] == "foreign"
    assert evidence["unit_verified"] == "unknown"


def _fake_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


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
        CommandResult(0, "absent\n"),
        CommandResult(0),
        CommandResult(2),
        CommandResult(2),
        CommandResult(2),
        CommandResult(1),
    ]
    if postgres:
        responses[14] = CommandResult(0, "postgres:x:111:117:PostgreSQL administrator:/var/lib/postgresql:/bin/bash\n")
        responses[15] = CommandResult(0, "/usr/bin/psql\n")
        responses.extend((CommandResult(0), CommandResult(0)))
    responses.extend(
        (
            CommandResult(0, 'LISTEN 0 4096 *:2202 0.0.0.0:* users:(("sshd",pid=101,fd=3))\n'),
            absent_caddy_evidence(),
        )
    )
    return responses


def test_valid_supported_host_returns_only_normalized_immutable_facts() -> None:
    remote = ScriptedRemote.from_responses(fact_responses())

    facts = validate_supported_host(remote, config(), resolver=direct_dns)

    assert isinstance(facts, HostFacts)
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
    assert facts.provisioning_marker is ProvisioningMarkerState.ABSENT
    assert not remote.responses
    with pytest.raises(FrozenInstanceError):
        facts.architecture = "arm64"  # type: ignore[misc]


@pytest.mark.parametrize(
    "listener_owners",
    (
        'LISTEN 0 4096 *:80 0.0.0.0:* users:(("nginx",pid=901,fd=6))\n'
        'LISTEN 0 4096 *:443 0.0.0.0:* users:(("nginx",pid=901,fd=7))\n',
        'LISTEN 0 4096 *:80 0.0.0.0:* users:(("caddy",fd=6))\n'
        'LISTEN 0 4096 *:443 0.0.0.0:* users:(("caddy",pid=402,fd=7))\n',
        'LISTEN 0 4096 *:80 0.0.0.0:* users:(("caddy",pid=403,fd=6))\n'
        'LISTEN 0 4096 *:443 0.0.0.0:* users:(("caddy",pid=403,fd=7))\n',
    ),
)
def test_marker_anchored_caddy_listener_requires_exact_process_and_unit_ownership(
    listener_owners: str,
) -> None:
    """Accepting an nginx, missing PID, or non-unit PID owner would adopt foreign HTTPS."""

    remote = ScriptedRemote.from_responses(managed_caddy_responses(listener_owners=listener_owners))

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            config(),
            resolver=direct_dns,
            expected_caddyfile_sha256=_CADDYFILE_SHA256,
        )

    assert raised.value.status is ExitStatus.SAFETY


@pytest.mark.parametrize(
    "evidence",
    (
        caddy_evidence(config_hash="f" * 64),
        caddy_evidence(config_metadata="root:root:600"),
        caddy_evidence(unit_metadata="root:root:600"),
        caddy_evidence(unit_verified="modified"),
    ),
)
def test_marker_anchored_caddy_requires_the_exact_owned_configuration_and_unit(
    evidence: CommandResult,
) -> None:
    """Matching paths alone must not adopt arbitrary Caddy bytes or a modified unit."""

    remote = ScriptedRemote.from_responses(
        managed_caddy_responses(
            listener_owners=(
                'LISTEN 0 4096 *:80 0.0.0.0:* users:(("caddy",pid=402,fd=6))\n'
                'LISTEN 0 4096 *:443 0.0.0.0:* users:(("caddy",pid=402,fd=7))\n'
            ),
            evidence=evidence,
        )
    )

    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(
            remote,
            config(),
            resolver=direct_dns,
            expected_caddyfile_sha256=_CADDYFILE_SHA256,
        )

    assert raised.value.status is ExitStatus.SAFETY


def test_marker_anchored_caddy_owner_is_recognized_on_a_rerun_without_mutation() -> None:
    """A known packaged Caddy owning both public ports remains an accepted partial state."""

    first_remote = ScriptedRemote.from_responses(
        managed_caddy_responses(
            listener_owners=(
                'LISTEN 0 4096 *:80 0.0.0.0:* users:(("caddy",pid=402,fd=6))\n'
                'LISTEN 0 4096 *:443 0.0.0.0:* users:(("caddy",pid=402,fd=7))\n'
            )
        )
    )
    second_remote = ScriptedRemote.from_responses(
        managed_caddy_responses(
            listener_owners=(
                'LISTEN 0 4096 *:80 0.0.0.0:* users:(("caddy",pid=402,fd=6))\n'
                'LISTEN 0 4096 *:443 0.0.0.0:* users:(("caddy",pid=402,fd=7))\n'
            )
        )
    )

    first = validate_provisionable_host(
        first_remote,
        config(),
        resolver=direct_dns,
        expected_caddyfile_sha256=_CADDYFILE_SHA256,
    )
    second = validate_provisionable_host(
        second_remote,
        config(),
        resolver=direct_dns,
        expected_caddyfile_sha256=_CADDYFILE_SHA256,
    )

    assert first.state is ProvisioningState.PARTIAL
    assert second.state is ProvisioningState.PARTIAL
    assert first.caddy_state.value == "active"
    assert second.caddy_state.value == "active"


def test_marker_anchored_pre_caddy_partial_state_remains_safe_without_any_public_listener() -> None:
    """The Caddy ownership proof must not reject a legitimate earlier convergence boundary."""

    responses = fact_responses()
    responses[9] = CommandResult(0, "/var/lib/taskman-provisioning.state\n")
    responses[10] = CommandResult(0, "managed\n")
    discovery = validate_provisionable_host(
        ScriptedRemote.from_responses(responses),
        config(),
        resolver=direct_dns,
        expected_caddyfile_sha256=_CADDYFILE_SHA256,
    )

    assert discovery.state is ProvisioningState.PARTIAL
    assert discovery.caddy_state.value == "absent"


@pytest.mark.parametrize(
    ("configured", "expected_state"),
    ((False, "prepared"), (True, "staged")),
)
def test_marker_anchored_caddy_installation_partial_states_remain_safe_to_retry(
    configured: bool,
    expected_state: str,
) -> None:
    """The Caddy package and a validated pre-start config are legitimate retry boundaries."""

    responses = fact_responses()
    paths = "/var/lib/taskman-provisioning.state\n"
    if configured:
        paths += "/etc/caddy/Caddyfile\n"
    responses[9] = CommandResult(0, paths)
    responses[10] = CommandResult(0, "managed\n")
    responses[11] = CommandResult(0, "caddy.service enabled\n")
    responses[-1] = inactive_caddy_evidence(configured=configured)

    discovery = validate_provisionable_host(
        ScriptedRemote.from_responses(responses),
        config(),
        resolver=direct_dns,
        expected_caddyfile_sha256=_CADDYFILE_SHA256,
    )

    assert discovery.state is ProvisioningState.PARTIAL
    assert discovery.caddy_state.value == expected_state


def test_pristine_only_validation_still_refuses_contradictory_caddy_evidence() -> None:
    """Conflicting immutable unit evidence must prevent a clean-host classification."""

    responses = fact_responses()
    responses[-1] = caddy_evidence()

    with pytest.raises(OpsError) as raised:
        validate_supported_host(
            ScriptedRemote.from_responses(responses), config(), resolver=direct_dns
        )

    assert raised.value.status is ExitStatus.SAFETY


def test_unsupported_platform_is_refused_only_after_all_facts_are_collected() -> None:
    responses = fact_responses()
    responses[0] = CommandResult(0, 'ID=debian\nVERSION_ID="13"\n')
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_supported_host(remote, config(), resolver=direct_dns)

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
        validate_supported_host(remote, config(), resolver=direct_dns)

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
        validate_supported_host(remote, config(), resolver=direct_dns)

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT


def test_failed_required_fact_command_maps_to_status_five_after_the_snapshot() -> None:
    responses = fact_responses()
    responses[0] = CommandResult(1, stderr="os release unavailable")
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_supported_host(remote, config(), resolver=direct_dns)

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert not remote.responses


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (9, CommandResult(1, stderr="path inspection unavailable")),
        (10, CommandResult(1, stderr="provisioning marker unavailable")),
        (11, CommandResult(1, stderr="unit inspection unavailable")),
        (12, CommandResult(1, stderr="passwd inspection unavailable")),
        (13, CommandResult(1, stderr="group inspection unavailable")),
    ],
)
def test_inability_to_inspect_non_database_managed_state_refuses_preflight(
    index: int, result: CommandResult
) -> None:
    responses = fact_responses()
    responses[index] = result
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_supported_host(remote, config(), resolver=direct_dns)

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert not remote.responses


def test_pristine_host_skips_postgresql_sudo_and_database_discovery() -> None:
    remote = ScriptedRemote.from_responses(fact_responses())

    facts = validate_supported_host(remote, config(), resolver=direct_dns)

    assert facts.postgres_available is False
    assert facts.postgres_sudo_available is None
    assert ("sudo", "-n", "-u", "postgres", "true") not in [argv for argv, _kwargs in remote.calls]
    assert not any("psql -Atq" in " ".join(argv) for argv, _kwargs in remote.calls)


def test_present_postgresql_requires_sudo_and_detects_a_managed_database() -> None:
    responses = fact_responses(postgres=True)
    responses[17] = CommandResult(0, "taskman_prod\n")
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_supported_host(remote, config(), resolver=direct_dns)

    assert raised.value.status is ExitStatus.SAFETY


def test_partial_postgresql_installation_refuses_without_running_privileged_inspection() -> None:
    responses = fact_responses()
    responses[14] = CommandResult(0, "postgres:x:111:117:PostgreSQL administrator:/var/lib/postgresql:/bin/bash\n")
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_supported_host(remote, config(), resolver=direct_dns)

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert ("sudo", "-n", "-u", "postgres", "true") not in [argv for argv, _kwargs in remote.calls]
    assert not any("psql -Atq" in " ".join(argv) for argv, _kwargs in remote.calls)


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (16, CommandResult(1, stderr="postgres sudo unavailable")),
        (17, CommandResult(2, stderr="psql connection unavailable")),
    ],
)
def test_inability_to_inspect_present_postgresql_refuses_preflight(
    index: int, result: CommandResult
) -> None:
    responses = fact_responses(postgres=True)
    responses[index] = result
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_supported_host(remote, config(), resolver=direct_dns)

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT


def test_preflight_requests_integer_memory_and_nearest_existing_ancestor_capacity() -> None:
    nested_config = EnvironmentConfig.model_validate(
        valid_environment(
            install_root="/srv/taskman/absent/managed",
            backup_root="/var/backups/taskman/absent/backups",
        )
    )
    remote = ScriptedRemote.from_responses(fact_responses())

    validate_supported_host(remote, nested_config, resolver=direct_dns)

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
        validate_supported_host(remote, config(), resolver=lambda _hostname: ("198.51.100.7",))

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (8, CommandResult(0, "LISTEN 0 4096 *:4000 0.0.0.0:*\n")),
        (9, CommandResult(0, "/opt/taskman\n")),
        (11, CommandResult(0, "taskman.service enabled\n")),
        (12, CommandResult(0, "taskman:x:1000:1000::/nonexistent:/usr/sbin/nologin\n")),
        (13, CommandResult(0, "taskman:x:1000:1000::/nonexistent:/usr/sbin/nologin\n")),
    ],
)
def test_existing_managed_listener_path_unit_account_or_database_is_a_safety_refusal(
    index: int, result: CommandResult
) -> None:
    responses = fact_responses()
    responses[index] = result
    remote = ScriptedRemote.from_responses(responses)

    with pytest.raises(OpsError) as raised:
        validate_supported_host(remote, config(), resolver=direct_dns)

    assert raised.value.status is ExitStatus.SAFETY
