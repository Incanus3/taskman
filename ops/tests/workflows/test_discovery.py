from taskman_ops.host_protocol import HostResult


def test_discovery_result_exposes_final_state_without_a_lifecycle_envelope() -> None:
    result = HostResult(
        2, "discover", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {"host_kind": "empty", "releases": (), "backups": (), "activations": ()}, (),
    )

    assert result.state["host_kind"] == "empty"
    assert "lifecycle" not in result.to_mapping()
