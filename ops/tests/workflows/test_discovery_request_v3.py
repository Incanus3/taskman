from __future__ import annotations

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.workflows.helper import discovery_request


BACKUP_ID = "backup-cccccccccccccccccccccccccccccccc"


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43,
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@acme.tld",
        }
    )


@pytest.mark.parametrize("mode", ("strict", "deploy"))
def test_v3_discovery_request_has_only_common_mode_parameters(mode: str) -> None:
    """Adding a legacy state field would make inspection authority ambiguous."""

    request = discovery_request(_config(), mode=mode)

    assert request.protocol_version == 3
    assert request.operation == "discover"
    assert request.expected_state == {}
    assert request.parameters == {
        "credentials_path": "/etc/taskman/pgpass",
        "database": {
            "host": "127.0.0.1",
            "port": 5432,
            "role": "taskman",
            "name": "taskman_prod",
        },
        "mode": mode,
    }


def test_v3_restore_discovery_request_requires_and_carries_only_its_backup_identifier() -> None:
    """A restore request without its input identity could inspect the wrong recovery state."""

    request = discovery_request(_config(), mode="restore", backup_id=BACKUP_ID)

    assert request.parameters["backup_id"] == BACKUP_ID
    assert set(request.parameters) == {"credentials_path", "database", "mode", "backup_id"}


@pytest.mark.parametrize(
    ("mode", "backup_id"),
    (("unknown", None), ("restore", None), ("strict", BACKUP_ID), ("deploy", BACKUP_ID)),
)
def test_v3_discovery_request_rejects_invalid_mode_specific_authority(
    mode: str,
    backup_id: str | None,
) -> None:
    """Relaxing mode validation could send restore-only authority to another inspection."""

    with pytest.raises(ValueError):
        discovery_request(_config(), mode=mode, backup_id=backup_id)


def test_provision_discovery_binds_configured_track_and_strict_false_configuration_context():
    config = _config().model_copy(update={"postgres_package_track": "18"})
    request = discovery_request(config, mode="provision")
    assert request.parameters["postgres_package_track"] == "18"
    assert request.parameters["force_postgresql_config"] is False
    assert set(request.parameters) == {"credentials_path", "database", "mode",
                                       "postgres_package_track", "force_postgresql_config"}


def test_controller_rejects_provision_observation_for_another_track():
    from taskman_ops.host_protocol import HostResult
    from taskman_ops.errors import OpsError
    from taskman_ops.workflows.helper import run_request
    config = _config().model_copy(update={"postgres_package_track": "18"})
    request = discovery_request(config, mode="provision")
    result = HostResult.for_request(request, "succeeded", "observed", {
        "database_state": "ready", "applied_migrations": (),
        "postgresql": {"branch": "existing", "cluster": {
            "version": "16", "name": "main", "port": 5432,
            "data_directory": "/var/lib/postgresql/16/main",
            "config_file": "/etc/postgresql/16/main/postgresql.conf",
            "hba_file": "/etc/postgresql/16/main/pg_hba.conf"},
            "role": "compatible", "database": "compatible", "access": "unproven"},
    })
    with pytest.raises(OpsError):
        run_request(object(), request, package=object(), invoker=lambda *_args: result)


def test_provision_discovery_carries_explicit_configuration_context():
    assert discovery_request(_config(), mode="provision", force_postgresql_config=True).parameters["force_postgresql_config"] is True


@pytest.mark.parametrize("mode,value", [("strict", True), ("deploy", True), ("restore", True), ("provision", "true"), ("provision", 1), ("provision", None)])
def test_configuration_override_requires_boolean_provision_context(mode, value):
    with pytest.raises(ValueError):
        discovery_request(_config(), mode=mode, force_postgresql_config=value, **({"backup_id": BACKUP_ID} if mode == "restore" else {}))


@pytest.mark.parametrize("site", ["deployment_admission_authority", "_confirmed_expected_state", "_downgrade_acknowledgment"])
def test_deploy_provision_mode_consumers_preserve_explicit_context(monkeypatch, site):
    force = True
    from taskman_ops.workflows import deploy as module
    class Observed(Exception):
        pass
    captured = []
    def observe(_remote, request):
        captured.append(request)
        raise Observed
    monkeypatch.setattr(module, "run_request", observe)
    with pytest.raises(Observed):
        if site == "_downgrade_acknowledgment":
            module._downgrade_acknowledgment(object(), _config(), object(), None, mode="provision", force_postgresql_config=force)
        else:
            getattr(module, site)(object(), _config(), mode="provision", force_postgresql_config=force)
    assert captured[0].parameters["force_postgresql_config"] is force
