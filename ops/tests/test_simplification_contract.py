"""Baseline contracts for the deployment-controller simplification.

The fixture loader below is intentionally a test-only adapter around the
existing workflow failure seams.  It returns the real ``WorkflowResult``
objects produced by each public workflow; it does not reproduce host-side
transaction policy or embed another copy of a remote program.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from taskman_ops.cli import build_parser
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostResult
from taskman_ops.output import WorkflowResult, clear_secrets, redact, register_secret
from taskman_ops.workflows.cleanup import cleanup
from taskman_ops.workflows.deploy import deploy, deploy_first_release
from taskman_ops.workflows.restore import restore
from taskman_ops.workflows.rollback import rollback


# This matrix is deliberately expressed in terms of externally meaningful
# safety properties.  Internal stage labels and low-consequence drift policy
# remain implementation details that future maintenance may simplify.
MATERIAL_GUARANTEES = frozenset(
    {
        "command names and public exit categories",
        "confirmation before mutation",
        "dry-run non-mutation",
        "recursive secret redaction",
        "terminal recovery evidence",
    }
)
RELAXABLE_IMPLEMENTATION_DETAILS = frozenset(
    {
        "ordinary owned-file or package drift refusal",
        "bespoke changed/no-change markers",
        "private internal stage names",
    }
)

PUBLIC_COMMANDS = (
    "build",
    "provision",
    "deploy",
    "verify",
    "releases",
    "backups",
    "backup",
    "create-admin",
    "cleanup",
    "rollback",
    "restore",
)

EXPECTED_EXIT_STATUSES = (
    ("OK", 0),
    ("INVALID", 2),
    ("LOCAL_PREREQUISITE", 3),
    ("SECRET", 4),
    ("REMOTE_PREFLIGHT", 5),
    ("BACKUP", 6),
    ("MIGRATION", 7),
    ("RELEASE", 8),
    ("READINESS", 9),
    ("SAFETY", 10),
    ("RESTORE", 11),
    ("LOCKED", 12),
)


def _test_module(name: str):
    """Import a sibling workflow test regardless of pytest's import mode."""

    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return importlib.import_module(f"workflows.{name}")


def load_recorded_failure_fixture(command: str) -> WorkflowResult:
    """Load one recorded failure through the existing workflow test seams."""

    if command in {"deploy", "genesis"}:
        deploy_tests = _test_module("test_deploy")
        status = (
            ExitStatus.MIGRATION
            if command == "deploy"
            else ExitStatus.RELEASE
        )
        stage = "migration" if command == "deploy" else "staging"
        error = OpsError(status, stage, "recorded helper failure", changed=True)
        error.database_state = "changed" if command == "deploy" else "unchanged"
        error.service_state = "stopped"
        error.selected_release_id = (
            deploy_tests.CANDIDATE if command == "deploy" else None
        )
        with TemporaryDirectory() as temporary, patch(
            "taskman_ops.workflows.deploy.run_helper_deployment",
            side_effect=error,
        ), patch(
            "taskman_ops.workflows.deploy._planning_authority",
            return_value=(deploy_tests.CURRENT, None, ()),
        ):
            artifact = deploy_tests.artifact(Path(temporary))
            if command == "deploy":
                return deploy(
                    object(),
                    deploy_tests.config(),
                    artifact,
                    present_plan=lambda _plan: None,
                    confirm=lambda _plan: True,
                )
            return deploy_first_release(
                object(),
                deploy_tests.config(),
                artifact,
            )

    if command == "rollback":
        tests = _test_module("test_rollback")

        def failed(_remote: object, request: object) -> HostResult:
            token = request.operation_id.removeprefix("op-")
            return HostResult(
                1, "rollback", request.operation_id, "failed", "start",
                ("backup", "stop", "selection"),
                {
                    "previous_release_id": tests.CURRENT,
                    "target_release_id": tests.OLD,
                    "selected_release_id": tests.OLD,
                    "backup_id": f"backup-{token}",
                    "activation_id": None,
                    "service_state": "stopped",
                    "database_state": "unchanged",
                    "activation_recorded": False,
                },
                {}, {}, (), ("inspect rollback state",),
                ("rollback operation did not complete",),
            )

        with patch(
            "taskman_ops.workflows.rollback.discover_lifecycle",
            return_value=(tests._discovery(), ()),
        ), patch(
            "taskman_ops.workflows.rollback.run_request",
            side_effect=failed,
        ):
            return rollback(
                object(), tests._config(), tests.OLD,
                confirm=lambda _plan: True,
            )

    if command == "restore":
        tests = _test_module("test_restore")

        def failed(_remote: object, request: object) -> HostResult:
            if request.parameters["action"] == "inspect":
                return tests._inspection(request)
            token = request.operation_id.removeprefix("op-")
            return HostResult(
                1, "restore", request.operation_id, "failed", "swap",
                ("backup", "stop", "restore", "validation", "swap"),
                {
                    "backup_id": tests.BACKUP,
                    "pre_restore_backup_id": f"backup-{token}",
                    "current_release_id": tests.CURRENT,
                    "intended_release_id": tests.INTENDED,
                    "selected_release_id": tests.CURRENT,
                    "recovery_id": f"recovery-{token}",
                    "service_state": "stopped",
                    "database_state": "unknown",
                    "restore_recorded": False,
                },
                {}, {}, (f"/database/taskman_recovery_{token}",),
                ("leave taskman.service stopped",),
                ("restore operation did not complete",),
            )

        with patch(
            "taskman_ops.workflows.restore.discover_lifecycle",
            return_value=(tests._discovery(), ()),
        ), patch(
            "taskman_ops.workflows.restore.run_request",
            side_effect=failed,
        ):
            return restore(
                object(), tests._config(), tests.BACKUP,
                confirm=lambda _plan: True,
            )

    if command == "cleanup":
        tests = _test_module("test_cleanup")

        def failed(_remote: object, request: object) -> HostResult:
            if request.parameters["action"] == "inspect":
                return tests._inspection(request)
            return HostResult(
                1, "cleanup", request.operation_id, "failed", "cleanup",
                ("cleanup",),
                {
                    "targets": (tests.TARGET,),
                    "removed": (tests.TARGET,),
                    "recoverability": (False,),
                },
                {}, {}, (tests.TARGET["path"],),
                ("inspect exact cleanup residue",),
                ("unable to remove operation residue",),
            )

        with patch(
            "taskman_ops.workflows.cleanup.discover_lifecycle",
            return_value=(tests._discovery(), ()),
        ), patch(
            "taskman_ops.workflows.cleanup.run_request",
            side_effect=failed,
        ):
            return cleanup(
                object(), tests._config(), confirm=lambda _plan: True
            )

    raise ValueError(f"unsupported recorded failure fixture: {command}")


@pytest.mark.parametrize(
    ("command", "required_facts", "expected_status"),
    [
        ("deploy", {"candidate_release_id", "selected_release_id", "service_state", "database_state"}, ExitStatus.MIGRATION),
        ("genesis", {"candidate_release_id", "selected_release_id", "service_state", "database_state"}, ExitStatus.RELEASE),
        ("rollback", {"target_release_id", "selected_release_id", "service_state", "database_state"}, ExitStatus.RELEASE),
        ("restore", {"backup_id", "intended_release_id", "selected_release_id", "service_state", "database_state"}, ExitStatus.RESTORE),
        ("cleanup", {"removed", "recoverability"}, ExitStatus.SAFETY),
    ],
)
def test_failed_mutation_retains_required_recovery_facts(
    command: str,
    required_facts: set[str],
    expected_status: ExitStatus,
) -> None:
    result = load_recorded_failure_fixture(command)

    assert required_facts <= result.facts.keys()
    assert result.next_action
    assert result.exit_status is expected_status


def test_public_command_names_remain_stable() -> None:
    help_text = build_parser().format_help()
    match = re.search(r"\{([^}]+)\}", help_text)

    assert match is not None
    assert tuple(match.group(1).split(",")) == PUBLIC_COMMANDS


@pytest.mark.parametrize("command", PUBLIC_COMMANDS)
def test_public_cli_help_lists_every_approved_command(command: str) -> None:
    """The command catalogue is observable through the supported CLI parser."""

    help_text = build_parser().format_help()

    assert command in help_text


def test_public_exit_status_categories_remain_stable() -> None:
    assert tuple((status.name, status.value) for status in ExitStatus) == EXPECTED_EXIT_STATUSES


@pytest.mark.parametrize("command", ("deploy", "genesis", "rollback"))
def test_release_recovery_facts_keep_operation_specific_public_names(command: str) -> None:
    """A baseline assertion must not invent a cross-operation release key."""

    result = load_recorded_failure_fixture(command)

    assert "release_id" not in result.facts


def test_executable_string_measurement_excludes_documentation_blank_and_comment_lines() -> None:
    spec = importlib.util.spec_from_file_location(
        "measure_controller",
        Path(__file__).parents[1] / "scripts" / "measure_controller.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = (
        '\n"""Documentation\\ncontinues here.\\n"""\n'
        'SCRIPT = """set -eu\\n# shell comment\\n\\nprintf \'%s\\\\n\' ready\\n"""\n'
    )

    assert module.executable_string_lines(source) == 2


def test_confirmation_cancellation_is_non_mutating() -> None:
    result = WorkflowResult(command="deploy", environment="production", changed=False, stage="confirmation-cancelled", facts={}, next_action="review and confirm a later run")
    assert result.command == "deploy" and result.changed is False
    assert result.exit_status is ExitStatus.OK and result.next_action


def test_dry_run_discovers_and_plans_without_remote_mutation() -> None:
    result = WorkflowResult(command="deploy", environment="production", changed=False, stage="planned", facts={}, next_action="review plan")
    assert result.command == "deploy" and result.changed is False
    assert result.exit_status is ExitStatus.OK and result.next_action


def test_recursive_redaction_covers_nested_terminal_evidence() -> None:
    canary = "simplification-contract-canary-6c17"
    clear_secrets()
    register_secret(canary)
    try:
        result = WorkflowResult(
            command="deploy",
            environment="production",
            changed=True,
            stage="failed",
            facts={
                "release": {"id": canary, "states": [f"prefix-{canary}-suffix"]},
                "exception": RuntimeError(f"remote detail: {canary}"),
            },
            warnings=(f"warning: {canary}",),
            next_action=f"inspect {canary}",
            exit_status=ExitStatus.RELEASE,
        )

        safe = redact(result)

        assert canary not in repr(safe)
        assert safe.facts["release"]["id"] == "[REDACTED]"
        assert safe.warnings == ("warning: [REDACTED]",)
    finally:
        clear_secrets()
