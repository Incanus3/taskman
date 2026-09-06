"""Helper deployment result translation is isolated from remote invocation."""

from __future__ import annotations


def test_result_translation_boundary_is_importable() -> None:
    from taskman_ops.workflows.helper_deploy_results import translate_helper_deployment_result

    assert callable(translate_helper_deployment_result)
