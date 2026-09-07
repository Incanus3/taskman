"""Shared environment fixture for workflow tests during protocol migration."""

from __future__ import annotations

from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_protocol import HostResult
from tests.test_config import valid_environment


def config() -> EnvironmentConfig:
    values = valid_environment()
    values["ssh_port"] = 22
    return EnvironmentConfig.model_validate(values)


def test_deploy_final_result_has_no_private_operation_identifier() -> None:
    result = HostResult(
        2, "deploy", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {"changed": True, "selected_release_id": "2026.9.7-deadbeef"}, (),
    )

    assert result.to_mapping()["correlation_id"] == result.correlation_id
