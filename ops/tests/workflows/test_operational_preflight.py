"""Read-only existing-host preflight shared by every mutating workflow."""

from __future__ import annotations

from collections import deque
from dataclasses import replace

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host.acceptance import validate_operational_host
from taskman_ops.host.facts import (
    CaddyState,
    HostFacts,
)
from taskman_ops.output import render_human, render_json
from taskman_ops.remote import CommandResult
from taskman_ops.workflows.backup import run_backup
from taskman_ops.workflows import operational_preflight as preflight_module
from taskman_ops.workflows.operational_preflight import validate_operational_preflight
from tests.workflows.test_deploy import config


class RecordingRemote:
    def __init__(self, responses: list[CommandResult]) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((argv, kwargs))
        return self.responses.popleft()


def _managed_host_facts() -> HostFacts:
    return HostFacts(
        os_id="ubuntu",
        ubuntu_release="26.04",
        architecture="amd64",
        pid1="systemd",
        sudo_available=True,
        postgres_available=True,
        postgres_sudo_available=True,
        active_ssh_port=22,
        memory_bytes=4 * 1024**3,
        available_disk_bytes=40 * 1024**3,
        backup_available_disk_bytes=40 * 1024**3,
        dns_addresses=("203.0.113.10",),
        listeners=(),
        existing_paths=(),
        path_metadata=(),
        caddy_state=CaddyState.ACTIVE,
        existing_units=(),
        existing_accounts=("taskman",),
        taskman_account_compatible=True,
        existing_databases=("taskman_prod",),
        failed_checks=(),
    )


def test_existing_host_preflight_uses_real_platform_dns_and_capacity_facts_before_scripts() -> None:
    class FactRemote(RecordingRemote):
        def __init__(self) -> None:
            super().__init__([CommandResult(0), CommandResult(0)])
            self.fact_calls = 0

        def facts(self) -> HostFacts:
            self.fact_calls += 1
            return _managed_host_facts()

    remote = FactRemote()

    facts = validate_operational_preflight(remote, config())

    assert facts == _managed_host_facts()
    assert remote.fact_calls == 1
    assert len(remote.calls) == 2


def test_existing_host_preflight_checks_runtime_metadata_keys_database_and_capacity() -> None:
    remote = RecordingRemote([CommandResult(0), CommandResult(0)])
    host_calls: list[object] = []

    validate_operational_preflight(
        remote,
        config(),
        host_validator=lambda actual_remote, actual_config: host_calls.append(
            (actual_remote, actual_config)
        ),
    )

    assert host_calls == [(remote, config())]
    assert len(remote.calls) == 2
    runtime_argv, runtime_options = remote.calls[0]
    database_argv, database_options = remote.calls[1]
    assert runtime_argv[:2] == ("sh", "-ceu")
    assert "/etc/taskman/taskman.env" in runtime_argv
    for key in (
        "DATABASE_URL",
        "SECRET_KEY_BASE",
        "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET",
        "PHX_HOST",
        "RESEND_API_KEY",
        "MAIL_FROM",
        "PORT",
        "POOL_SIZE",
        "PHX_SERVER",
    ):
        assert key in runtime_argv
    assert runtime_options == {"sudo": True, "stdin": None, "sensitive": True}
    assert database_argv[:2] == ("sh", "-ceu")
    assert "SELECT 1" in database_argv[2]
    assert "pg_database_size" in database_argv[2]
    assert "df -B1" in database_argv[2]
    assert database_options == {"sudo": True, "stdin": None, "sensitive": True}


def _stub_restore_result(monkeypatch, state, *, outcome="succeeded", warnings=()):
    from taskman_ops.host_protocol import HostResult

    captured = []
    def run(_remote, request):
        captured.append(request)
        return HostResult.for_request(request, outcome, "observed", state, warnings)
    monkeypatch.setattr("taskman_ops.workflows.helper.run_request", run)
    return captured


def test_restore_preflight_uses_packaged_capacity_projection(monkeypatch) -> None:
    remote = RecordingRemote([CommandResult(0)])
    captured = _stub_restore_result(monkeypatch, {
        "mode": "capacity", "database_available_bytes": 8589934592,
        "database_size_bytes": {"canonical": None, "temporary": None, "retired": 4096},
    }, warnings=("transient helper cleanup was incomplete",))

    facts = preflight_module.validate_restore_preflight(
        remote, config(), host_validator=lambda *_args: _managed_host_facts()
    )

    assert captured[0].operation == "restore_preflight"
    assert captured[0].parameters["mode"] == "capacity"
    assert captured[0].parameters["credentials_path"] == "/etc/taskman/pgpass"
    assert facts.database_available_disk_bytes == 8589934592
    assert facts.database_size_bytes["retired"] == 4096
    assert facts.warnings == ("transient helper cleanup was incomplete",)


def test_restore_inspection_preflight_omits_capacity(monkeypatch) -> None:
    remote = RecordingRemote([CommandResult(0)])
    captured = _stub_restore_result(monkeypatch, {"mode": "inspection"})

    observed = preflight_module.validate_restore_inspection_preflight(
        remote, config(), host_validator=lambda *_args: _managed_host_facts()
    )

    assert captured[0].parameters["mode"] == "inspection"
    assert observed.host_facts == _managed_host_facts()


@pytest.mark.parametrize(
    "facts",
    (
        replace(_managed_host_facts(), available_disk_bytes=1, backup_available_disk_bytes=1),
        replace(
            _managed_host_facts(), available_disk_bytes=0, backup_available_disk_bytes=0,
            failed_checks=("install-root disk", "backup-root disk"),
        ),
    ),
)
def test_restore_inspection_admits_low_or_unobservable_capacity_for_cleanup(facts, monkeypatch) -> None:
    remote = RecordingRemote([CommandResult(0)])
    _stub_restore_result(monkeypatch, {"mode": "inspection"})

    observed = preflight_module.validate_restore_inspection_preflight(
        remote, config(), host_validator=lambda *_args: facts
    )

    assert observed.host_facts is facts


def test_cleanup_preflight_uses_host_facts_without_runtime_or_database_commands() -> None:
    facts = replace(
        _managed_host_facts(), available_disk_bytes=0, backup_available_disk_bytes=0,
        failed_checks=("install-root disk", "backup-root disk"),
    )
    class FactRemote(RecordingRemote):
        def facts(self):
            return facts
    remote = FactRemote([])

    observed = preflight_module.validate_cleanup_preflight(remote, config())

    assert observed is facts
    assert remote.calls == []


def test_restore_preflight_refuses_invalid_packaged_capacity(monkeypatch) -> None:
    remote = RecordingRemote([CommandResult(0)])
    _stub_restore_result(monkeypatch, {"mode": "capacity", "database_available_bytes": 1})

    with pytest.raises(OpsError) as raised:
        preflight_module.validate_restore_preflight(
            remote, config(), host_validator=lambda *_args: _managed_host_facts()
        )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert "capacity" in raised.value.next_action


def test_restore_preflight_maps_helper_refusal_and_retains_cleanup_warning(monkeypatch) -> None:
    remote = RecordingRemote([CommandResult(0)])
    _stub_restore_result(
        monkeypatch, {}, outcome="refused",
        warnings=("transient helper cleanup was incomplete",),
    )

    with pytest.raises(OpsError) as raised:
        preflight_module.validate_restore_preflight(
            remote, config(), host_validator=lambda *_args: _managed_host_facts()
        )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.warnings == ("transient helper cleanup was incomplete",)


@pytest.mark.parametrize("failed_call", (0, 1))
def test_existing_host_preflight_refuses_failed_runtime_or_database_evidence(
    failed_call: int,
) -> None:
    responses = [CommandResult(0), CommandResult(0)]
    responses[failed_call] = CommandResult(1, "secret-like-output", "secret-like-error")
    remote = RecordingRemote(responses)

    with pytest.raises(OpsError) as raised:
        validate_operational_preflight(
            remote,
            config(),
            host_validator=lambda *_args: None,
        )

    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.changed is False
    assert "secret-like" not in str(raised.value)


@pytest.mark.parametrize(
    "failed_call,reason",
    [(0, "runtime environment"), (1, "database health or backup capacity")],
)
def test_workflow_reports_identify_failed_preflight_without_remote_output(
    monkeypatch: pytest.MonkeyPatch, failed_call: int, reason: str
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.operational_preflight.validate_operational_host",
        lambda *_args: None,
    )
    responses = [CommandResult(0), CommandResult(0)]
    responses[failed_call] = CommandResult(1, "secret-like-output", "secret-like-error")

    result = run_backup(RecordingRemote(responses), config())

    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.changed is False
    assert reason in result.next_action
    for report in (render_human(result), render_json(result)):
        assert reason in report
        assert "secret-like" not in report
