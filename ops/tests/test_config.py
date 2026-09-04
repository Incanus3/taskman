from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from taskman_ops.config import EnvironmentConfig, load_environment
from taskman_ops.errors import ExitStatus, OpsError


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
        "managed_root": "/opt/taskman",
        "release_root": "/opt/taskman/releases",
        "deployment_root": "/opt/taskman/deployments",
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


def test_complete_environment_is_frozen_and_normalizes_architecture_alias() -> None:
    config = EnvironmentConfig.model_validate(valid_environment())

    assert config.architecture == "amd64"
    assert config.ssh_port == 2202
    assert config.public_ipv4 == "203.0.113.10"
    with pytest.raises(ValidationError):
        config.application_port = 4001  # type: ignore[misc]


@pytest.mark.parametrize("name", ["../production", "/tmp", "production/name", "Production", ""])
def test_path_like_or_invalid_environment_names_are_refused(name: str) -> None:
    with pytest.raises(OpsError) as raised:
        load_environment(name)

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize("field", ["ssh_host", "public_hostname"])
@pytest.mark.parametrize("hostname", ["localhost", "localhost.localdomain", "127.0.0.1", "::1", "foo.local"])
def test_reserved_or_local_hostnames_are_refused(field: str, hostname: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: hostname}))


@pytest.mark.parametrize("field,value", [("target_os", "ubuntu24.04"), ("target_os", "debian12"), ("architecture", "arm64")])
def test_unsupported_target_is_refused(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: value}))


@pytest.mark.parametrize("field", ["ssh_port", "application_port", "distribution_port"])
@pytest.mark.parametrize("port", [0, -1, 65536, "4000", True])
def test_invalid_ports_are_refused(field: str, port: object) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: port}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"application_port": 2202},
        {"distribution_port": 2202},
        {"distribution_port": 4000},
        {"application_port": 5432},
    ],
)
def test_service_ports_must_not_overlap(overrides: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**overrides))


@pytest.mark.parametrize("field", ["database_name", "database_role"])
@pytest.mark.parametrize("identifier", ["1taskman", "task-man", "task.man", "", "a" * 64])
def test_database_identifiers_use_the_postgresql_allowlist(field: str, identifier: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: identifier}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("managed_root", "/"),
        ("managed_root", "/tmp/taskman"),
        ("backup_root", "/tmp/backups"),
        ("managed_root", "relative/taskman"),
        ("release_root", "/srv/releases"),
        ("backup_root", "/opt/taskman/../outside"),
        ("deployment_root", "/opt/taskman"),
    ],
)
def test_managed_roots_must_be_absolute_and_contained(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: value}))


@pytest.mark.parametrize("field", ["backup_retention", "release_retention", "readiness_timeout", "connection_timeout", "pool_size"])
def test_retention_timeouts_and_pool_size_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: 0}))


@pytest.mark.parametrize("fingerprint", ["", "SHA256:short", "MD5:aa:bb", "SHA256:abc def", "not-a-fingerprint"])
def test_pinned_host_key_fingerprint_has_the_expected_shape(fingerprint: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(host_key_fingerprint=fingerprint))


def test_unknown_environment_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(unexpected="value"))


def test_load_environment_injects_name_and_wraps_yaml_validation_as_status_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "staging.yaml"
    payload = valid_environment(name=None)
    payload.pop("name")
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setattr("taskman_ops.config.ENVIRONMENTS_DIR", tmp_path)

    config = load_environment("staging")
    assert config.name == "staging"

    (tmp_path / "broken.yaml").write_text("not: [valid", encoding="utf-8")
    with pytest.raises(OpsError) as raised:
        load_environment("broken")
    assert raised.value.status is ExitStatus.INVALID
