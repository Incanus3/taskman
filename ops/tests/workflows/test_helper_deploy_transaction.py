"""Controller-to-helper deployment transaction boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from taskman_ops.config import EnvironmentConfig
from taskman_ops.helper_package import HelperPackage
from taskman_ops.helper_runner import HelperInvocation
from taskman_ops.host_protocol import HostResult
from taskman_ops.manifests import ArtifactManifest, BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, VerifiedArtifact
from taskman_ops.remote import CommandResult, UploadReceipt
from taskman_ops.workflows.helper_deploy import run_helper_deployment


RELEASE_ID = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
PREVIOUS = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


def _successful_verification() -> dict[str, object]:
    checks = (
        ("taskman-service", "taskman.service is active with a positive MainPID"),
        ("release-identity", "systemd MainPID executable is under the selected release"),
        ("caddy-service", "caddy.service is active"),
        ("listener-topology", "Taskman, distribution, and PostgreSQL listeners have the required topology"),
        ("startup-journal", "recent startup journal evidence is clean"),
        ("local-readiness", "loopback health endpoint returned exact ready response"),
        ("public-readiness", "public HTTPS health endpoint returned exact ready response"),
        ("public-hsts", "public HTTPS response includes HSTS"),
    )
    return {
        "schema_version": 1, "status": "ok", "exit_status": 0,
        "release_id": RELEASE_ID, "expected_release_id": RELEASE_ID,
        "checks": tuple(
            {"schema_version": 1, "name": name, "status": "passed", "summary": summary}
            for name, summary in checks
        ),
        "next_action": None,
    }


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production", "ssh_host": "203.0.113.10", "ssh_port": 22, "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43, "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10", "target_os": "ubuntu26.04", "architecture": "amd64",
            "application_port": 4000, "distribution_port": 6789, "database_name": "taskman_prod",
            "database_role": "taskman", "mail_from": "no-reply@example.test",
        }
    )


def _artifact(tmp_path: Path) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"artifact")
    return VerifiedArtifact(
        archive, tmp_path / "manifest.json", tmp_path / "checksum", "c" * 64,
        ArtifactManifest(
            2, "taskman", "0.2.0", "b" * 40, RELEASE_ID,
            datetime(2026, 9, 5, tzinfo=UTC), "ubuntu26.04", "amd64", "27.3.4.6",
            "1.18.3", "22.22.1", BUILDER_BASE_TAG, BUILDER_BASE_DIGEST, (), "taskman",
        ),
    )


def test_transaction_transfers_artifact_then_invokes_one_correlated_helper_request(
    tmp_path: Path,
) -> None:
    """The controller cannot recreate lifecycle stages after helper migration.

    Reintroducing an embedded transaction, using a different operation ID for
    transfer and helper execution, or accepting unrelated helper evidence
    must make this request/result contract fail.
    """

    config = _config()
    artifact = _artifact(tmp_path)
    calls: list[tuple[str, object]] = []

    class Remote:
        def run(self, argv, **kwargs):
            calls.append(("run", tuple(argv)))
            return CommandResult(0)

        def put(self, source, destination, **kwargs):
            calls.append(("put", destination))
            return UploadReceipt()

    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)
    package.path.write_bytes(b"helper")

    def invoke(remote, actual_package, request):
        calls.append(("invoke", request))
        assert remote is instance
        assert actual_package is package
        assert request.operation == "deploy"
        assert request.expected_state == {"previous_release_id": PREVIOUS, "current_migrations": ()}
        assert request.parameters["candidate_release_id"] == RELEASE_ID
        assert request.parameters["manual_adoption"] is None
        assert request.parameters["artifact_path"].endswith(f".upload-{RELEASE_ID}-{request.operation_id}.tar.gz")
        token = request.operation_id.removeprefix("op-")
        return HelperInvocation(
            HostResult(
                protocol_version=1, operation="deploy", operation_id=request.operation_id,
                outcome="succeeded", stage="records",
                changed_stages=("staging", "backup", "stop", "migration", "selection", "start", "records"),
                lifecycle={
                    "previous_release_id": PREVIOUS, "candidate_release_id": RELEASE_ID,
                    "selected_release_id": RELEASE_ID, "backup_id": "backup-" + token,
                    "activation_id": "activation-" + token,
                    "service_state": "active", "database_state": "unchanged", "activation_recorded": True,
                },
                runtime_state={}, verification=_successful_verification(),
                residue_paths=(), recovery_actions=(), warnings=(),
            )
        )

    instance = Remote()
    result = run_helper_deployment(
        instance, config, artifact, previous_release_id=PREVIOUS, current_migrations=(), migration_policy="no-change",
        package=package, invoker=invoke,
    )

    assert [kind for kind, _value in calls] == ["run", "put", "invoke"]
    assert result["stage"] == "deployed"
    assert result["previous_release_id"] == PREVIOUS
    assert result["selected_release_id"] == RELEASE_ID
    assert result["changed_stages"] == ("staging", "backup", "stop", "migration", "selection", "start", "records")
