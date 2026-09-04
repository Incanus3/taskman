from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tests.fakes import ScriptedRemote
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host.facts import (
    MINIMUM_DISK_BYTES,
    MINIMUM_MEMORY_BYTES,
    HostFacts,
    validate_supported_host,
)
from taskman_ops.remote import CommandResult

from tests.test_config import valid_environment


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment())


def direct_dns(_hostname: str) -> tuple[str, ...]:
    return ("203.0.113.10",)


def fact_responses() -> list[CommandResult]:
    return [
        CommandResult(0, 'ID=ubuntu\nVERSION_ID="26.04"\n'),
        CommandResult(0, "x86_64\n"),
        CommandResult(0, "systemd\n"),
        CommandResult(0),
        CommandResult(0, f"{MINIMUM_MEMORY_BYTES}\n"),
        CommandResult(0, f"Avail\n{MINIMUM_DISK_BYTES}\n"),
        CommandResult(0, "2202\n"),
        CommandResult(0, "LISTEN 0 4096 *:2202 0.0.0.0:*\n"),
        CommandResult(0),
        CommandResult(0),
        CommandResult(0),
        CommandResult(0),
    ]


def test_valid_supported_host_returns_only_normalized_immutable_facts() -> None:
    remote = ScriptedRemote.from_responses(fact_responses())

    facts = validate_supported_host(remote, config(), resolver=direct_dns)

    assert isinstance(facts, HostFacts)
    assert facts.os_id == "ubuntu"
    assert facts.ubuntu_release == "26.04"
    assert facts.architecture == "amd64"
    assert facts.pid1 == "systemd"
    assert facts.sudo_available is True
    assert facts.active_ssh_port == 2202
    assert facts.memory_bytes == MINIMUM_MEMORY_BYTES
    assert facts.available_disk_bytes == MINIMUM_DISK_BYTES
    assert facts.dns_addresses == ("203.0.113.10",)
    assert facts.listeners[0].port == 2202
    assert not remote.responses
    with pytest.raises(FrozenInstanceError):
        facts.architecture = "arm64"  # type: ignore[misc]


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
        (6, CommandResult(0, "22\n")),
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


def test_direct_public_dns_must_include_only_the_configured_vps_address() -> None:
    remote = ScriptedRemote.from_responses(fact_responses())

    with pytest.raises(OpsError) as raised:
        validate_supported_host(remote, config(), resolver=lambda _hostname: ("198.51.100.7",))

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize(
    ("index", "result"),
    [
        (7, CommandResult(0, "LISTEN 0 4096 *:4000 0.0.0.0:*\n")),
        (8, CommandResult(0, "/opt/taskman\n")),
        (9, CommandResult(0, "taskman.service enabled\n")),
        (10, CommandResult(0, "taskman:x:1000:1000::/nonexistent:/usr/sbin/nologin\n")),
        (11, CommandResult(0, "taskman_prod\n")),
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
