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


@pytest.mark.parametrize("mode", ("strict", "deploy", "provision"))
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
