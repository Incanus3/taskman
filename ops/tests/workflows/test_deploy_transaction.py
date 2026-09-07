"""Final-protocol controller behavior for deployment requests."""

from __future__ import annotations

from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_protocol import HostResult
from taskman_ops.remote import CommandResult, UploadReceipt
from tests.test_config import valid_environment


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


def test_deployment_planning_reads_completed_host_state_without_legacy_adoption(
    monkeypatch,
) -> None:
    from taskman_ops.workflows import deploy as workflow

    observed = HostResult(
        2,
        "discover",
        "op-0123456789abcdef0123456789abcdef",
        "succeeded",
        "observed",
        {
            "selected_release_id": CURRENT,
            "applied_migrations": (20260905120000,),
            "releases": (
                {
                    "release_id": CURRENT,
                    "source_revision": "a" * 40,
                    "artifact_sha256": "b" * 64,
                    "migrations": (
                        {"filename": "20260905120000_create_tasks.exs", "sha256": "c" * 64},
                    ),
                },
            ),
        },
        (),
    )
    monkeypatch.setattr(workflow, "run_request", lambda *_args, **_kwargs: observed)

    config = EnvironmentConfig.model_validate(valid_environment())
    previous, fingerprints, versions = workflow._planning_authority(object(), config)

    assert previous == CURRENT
    assert versions == (20260905120000,)
    assert fingerprints[0].filename == "20260905120000_create_tasks.exs"


def test_deployment_result_uses_final_correlation_not_a_legacy_operation_id() -> None:
    result = HostResult(
        2,
        "deploy",
        "op-0123456789abcdef0123456789abcdef",
        "succeeded",
        "completed",
        {"changed": True, "selected_release_id": CURRENT},
        (),
    )

    assert result.to_mapping()["correlation_id"] == result.correlation_id
    assert "operation_id" not in repr(result.to_mapping())


def test_uploaded_deploy_request_carries_only_final_protocol_authority(
    tmp_path,
    monkeypatch,
) -> None:
    from tests.workflows.test_deploy import artifact
    from taskman_ops.workflows import helper

    class Remote:
        def run(self, *_args, **_kwargs):
            return CommandResult(0)

        def put(self, *_args, **_kwargs):
            return UploadReceipt()

    captured = []

    def invoke(_remote, request, **_kwargs):
        captured.append(request)
        return HostResult(
            2, request.operation, request.correlation_id, "succeeded", "completed", {}, (),
        )

    monkeypatch.setattr(helper, "run_request", invoke)
    config = EnvironmentConfig.model_validate(valid_environment())
    helper.run_deployment_request(
        Remote(),
        config,
        artifact(tmp_path),
        previous_release_id=CURRENT,
        applied_migrations=(20260905120000,),
        migration_policy="backward-compatible",
    )

    request = captured[0]
    assert request.expected_state == {
        "selected_release_id": CURRENT,
        "applied_migrations": (20260905120000,),
    }
    assert set(request.parameters) == {
        "candidate_release_id", "artifact_sha256", "artifact_path", "manifest",
        "migration_policy", "credentials_path", "database", "verification",
    }
    assert "manual_adoption" not in request.parameters


def test_uploaded_genesis_request_keeps_restore_required_migration_authority(
    tmp_path,
    monkeypatch,
) -> None:
    from taskman_ops.manifests import MigrationFingerprint
    from tests.workflows.test_deploy import artifact
    from taskman_ops.workflows import helper

    class Remote:
        def run(self, *_args, **_kwargs):
            return CommandResult(0)

        def put(self, *_args, **_kwargs):
            return UploadReceipt()

    captured = []

    def invoke(_remote, request, **_kwargs):
        captured.append(request)
        return HostResult(2, request.operation, request.correlation_id, "succeeded", "completed", {}, ())

    monkeypatch.setattr(helper, "run_request", invoke)
    config = EnvironmentConfig.model_validate(valid_environment())
    helper.run_deployment_request(
        Remote(),
        config,
        artifact(tmp_path, migrations=(MigrationFingerprint("20260905120000_create_tasks.exs", "d" * 64),)),
        previous_release_id=None,
        applied_migrations=(),
        migration_policy="restore-required",
        genesis=True,
    )

    request = captured[0]
    assert request.operation == "genesis"
    assert request.expected_state == {"selected_release_id": None, "applied_migrations": ()}
    assert request.parameters["migration_policy"] == "restore-required"
    assert request.parameters["manifest"]["migrations"] == (
        {"filename": "20260905120000_create_tasks.exs", "sha256": "d" * 64},
    )
