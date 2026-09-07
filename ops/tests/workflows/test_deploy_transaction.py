from taskman_ops.host_protocol import HostResult


def test_deployment_result_uses_final_correlation_not_a_legacy_operation_id() -> None:
    result = HostResult(
        2, "deploy", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {"changed": True, "selected_release_id": "2026.9.7-deadbeef"}, (),
    )

    assert result.correlation_id == "op-0123456789abcdef0123456789abcdef"
    assert "operation_id" not in result.to_mapping()
