"""Dry-run results remain ordinary public workflow output."""

from taskman_ops.output import WorkflowResult


def test_dry_run_result_has_no_helper_protocol_evidence() -> None:
    result = WorkflowResult("backup", "test", False, "planned", {"planned_backup": True}, (), "rerun")

    assert result.changed is False
    assert result.stage == "planned"
    assert "operation_id" not in repr(result.facts)
