"""State-changing helper results remain redacted at the CLI boundary."""

from __future__ import annotations

from io import StringIO

from taskman_ops.cli import main
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import WorkflowResult, register_secret, clear_secrets


def test_helper_deployment_failure_preserves_primary_stage_and_redacts_residue() -> None:
    secret = "controller-failure-canary-5f5fb156"
    register_secret(secret)
    try:
        error = OpsError(ExitStatus.MIGRATION, "migration", f"host failed: {secret}", changed=True)
        result = WorkflowResult(
            command="deploy", environment="production", changed=True, stage="migration-failed",
            facts={"failure_stage": "migration", "changed_stages": ("staging", "backup", "migration")},
            warnings=("unable to remove operation residue",), next_action=error.next_action,
            exit_status=error.status,
        )
        stdout, stderr = StringIO(), StringIO()
        status = main(["deploy", "production", "--json"], dispatch_fn=lambda _invocation: result, stdout=stdout, stderr=stderr)

        assert status == int(ExitStatus.MIGRATION)
        assert secret not in stdout.getvalue() + stderr.getvalue()
        assert "migration-failed" in stdout.getvalue()
        assert "unable to remove operation residue" in stdout.getvalue()
    finally:
        clear_secrets()


def test_helper_deployment_noop_is_successful_without_mutation_claim() -> None:
    result = WorkflowResult(command="deploy", environment="production", changed=False, stage="already-current", facts={"selected_release_id": "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"})
    stdout, stderr = StringIO(), StringIO()

    status = main(["deploy", "production", "--json"], dispatch_fn=lambda _invocation: result, stdout=stdout, stderr=stderr)

    assert status == 0
    assert '"changed": false' in stdout.getvalue()
    assert stderr.getvalue() == ""
