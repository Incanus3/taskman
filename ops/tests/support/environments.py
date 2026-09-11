"""Shared environment baseline for controller tests."""

from taskman_ops.config import EnvironmentConfig


def two_root_environment(**overrides: object) -> dict[str, object]:
    value = valid_environment()
    value.update(overrides)
    return value


def valid_environment(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "production",
        "ssh_host": "203.0.113.10",
        "ssh_port": 2202,
        "ssh_user": "deployer",
        "host_key_fingerprint": "SHA256:" + "A" * 43,
        "public_hostname": "taskman.acme.tld",
        "public_ipv4": "203.0.113.10",
        "public_ipv6": None,
        "target_os": "ubuntu26.04",
        "architecture": "x86_64",
        "application_port": 4000,
        "distribution_port": 6789,
        "database_name": "taskman_prod",
        "database_role": "taskman",
        "postgres_package_track": None,
        "database_host": "127.0.0.1",
        "database_port": 5432,
        "install_root": "/opt/taskman",
        "backup_root": "/var/backups/taskman",
        "backup_schedule": "*-*-* 02:15:00",
        "backup_retention": 14,
        "release_retention": 3,
        "readiness_timeout": 30,
        "connection_timeout": 10,
        "pool_size": 10,
        "mail_from": "no-reply@acme.tld",
    }
    value.update(overrides)
    return value


def environment_config(**overrides: object) -> EnvironmentConfig:
    """Validate a fresh baseline with explicit scenario overrides."""
    return EnvironmentConfig.model_validate(valid_environment(**overrides))
