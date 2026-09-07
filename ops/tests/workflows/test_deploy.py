"""Shared environment fixture for workflow tests during protocol migration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus
from taskman_ops.host_protocol import HostResult
from taskman_ops.manifests import (
    ArtifactManifest,
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    VerifiedArtifact,
)
from taskman_ops.workflows.verification_results import (
    CheckStatus,
    VerificationCheck,
    VerificationReport,
)
from tests.test_config import valid_environment


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CANDIDATE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
_CHECKS = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)


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


def artifact(tmp_path: Path) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"release")
    manifest = ArtifactManifest(
        2,
        "taskman",
        "0.2.0",
        "b" * 40,
        CANDIDATE,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "ubuntu26.04",
        "amd64",
        "27.3.4.6",
        "1.18.3",
        "22.22.1",
        BUILDER_BASE_TAG,
        BUILDER_BASE_DIGEST,
        (),
        "taskman",
    )
    return VerifiedArtifact(
        archive,
        tmp_path / "manifest.json",
        tmp_path / "checksum",
        "c" * 64,
        manifest,
    )


def verified(release_id: str) -> dict[str, object]:
    return VerificationReport(
        ExitStatus.OK,
        release_id,
        release_id,
        tuple(VerificationCheck(name, CheckStatus.PASSED, "passed") for name in _CHECKS),
        None,
    ).to_mapping()


def test_deploy_refuses_success_without_proven_ready_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protocol-valid but partial deployment result cannot claim success."""

    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: (CURRENT, (), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: HostResult(
            2,
            "deploy",
            "op-0123456789abcdef0123456789abcdef",
            "succeeded",
            "completed",
            {"changed": True, "selected_release_id": CANDIDATE},
            (),
        ),
    )

    result = deploy(
        object(),
        config(),
        artifact(tmp_path),
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "safety-refused"


def test_deploy_publishes_only_a_complete_verified_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal deployed result is available only after all published facts validate."""

    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: (CURRENT, (), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: HostResult(
            2,
            "deploy",
            "op-0123456789abcdef0123456789abcdef",
            "succeeded",
            "completed",
            {
                "changed": True,
                "selected_release_id": CANDIDATE,
                "backup_id": "backup-cccccccccccccccccccccccccccccccc",
                "database_state": "unchanged",
                "service_state": "running",
                "report": verified(CANDIDATE),
            },
            (),
        ),
    )

    result = deploy(
        object(),
        config(),
        artifact(tmp_path),
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.OK
    assert result.stage == "deployed"
    assert result.facts["verification"]["expected_release_id"] == CANDIDATE
    assert "activation_recorded" not in result.facts


def test_deploy_failure_keeps_the_observed_selected_release_as_a_public_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coarse retry results retain the final helper's observable authority."""

    from taskman_ops.workflows.deploy import deploy

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy._planning_authority",
        lambda *_args: (CURRENT, (), ()),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_deployment_request",
        lambda *_args, **_kwargs: HostResult(
            2,
            "deploy",
            "op-0123456789abcdef0123456789abcdef",
            "retryable",
            "migration result lost",
            {
                "failed_boundary": "migration",
                "selected_release_id": CURRENT,
                "applied_migrations": (),
            },
            (),
        ),
    )

    result = deploy(
        object(),
        config(),
        artifact(tmp_path),
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
    )

    assert result.exit_status is ExitStatus.RELEASE
    assert result.facts["previous_release_id"] == CURRENT
    assert result.facts["selected_release_id"] == CURRENT
