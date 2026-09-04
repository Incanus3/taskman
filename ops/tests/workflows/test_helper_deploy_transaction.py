from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.helper import result_error


def test_deployment_refusal_maps_without_reconstructing_private_stages() -> None:
    result = HostResult(
        2, "deploy", "op-0123456789abcdef0123456789abcdef", "refused", "refused", {}, (),
    )

    error = result_error(result)

    assert error.stage == "deploy"
