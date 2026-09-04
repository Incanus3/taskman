"""Shared operation settings and strict change-probe results."""

from types import SimpleNamespace

import pytest

from taskman_ops.host.pyinfra_support import operation_sudo, probe_changed


@pytest.mark.parametrize("host, expected", (
    (SimpleNamespace(), True),
    (SimpleNamespace(current_op_global_arguments=None), True),
    (SimpleNamespace(current_op_global_arguments=[]), True),
    (SimpleNamespace(current_op_global_arguments={}), True),
    (SimpleNamespace(current_op_global_arguments={"_sudo": True}), True),
    (SimpleNamespace(current_op_global_arguments={"_sudo": False}), False),
    (SimpleNamespace(current_op_global_arguments={"_sudo": 0}), False),
    (SimpleNamespace(current_op_global_arguments={"_sudo": "yes"}), True),
))
def test_sudo_preserves_operation_override_and_privileged_default(host: object, expected: bool) -> None:
    assert operation_sudo(host) is expected


@pytest.mark.parametrize("lines, expected", (
    (["changed=0"], False),
    (["changed=1"], True),
    (["diagnostic", "changed=1", "other output"], True),
))
def test_probe_accepts_one_canonical_marker(lines: list[str], expected: bool) -> None:
    assert probe_changed(SimpleNamespace(stdout_lines=lines), "inspection") is expected


@pytest.mark.parametrize("output", (
    SimpleNamespace(),
    SimpleNamespace(stdout_lines=[]),
    SimpleNamespace(stdout_lines=["changed=2"]),
    SimpleNamespace(stdout_lines=["changed=1", "changed=1"]),
    SimpleNamespace(stdout_lines=["changed=0", "changed=1"]),
    SimpleNamespace(stdout_lines=[" changed=1"]),
    SimpleNamespace(stdout_lines=["changed=1 "]),
))
@pytest.mark.parametrize("operation", ("firewall state inspection", "PostgreSQL state inspection"))
def test_probe_refuses_ambiguous_output_with_operation_context(output: object, operation: str) -> None:
    with pytest.raises(RuntimeError) as failure:
        probe_changed(output, operation)
    assert str(failure.value) == f"{operation} returned an invalid change result"
