from __future__ import annotations

import ast
from dataclasses import replace
import inspect
from pathlib import Path, PurePosixPath
import re
import textwrap

import pytest

from fakes import HelperRunnerRemote
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops import helper_runner as helper_runner_module
from taskman_ops.helper_package import HelperPackage, build_helper_package
from taskman_ops.host_protocol import (
    HostRequest,
    HostResult,
    MAX_COLLECTION_ITEMS,
    MAX_OUTPUT_BYTES,
    encode_result,
)
from taskman_ops.remote import CommandResult


def request(correlation_id: str = "op-0123456789abcdef0123456789abcdef") -> HostRequest:
    return HostRequest(
        protocol_version=2,
        operation="discover",
        correlation_id=correlation_id,
        expected_state={"lifecycle": "unknown"},
        paths={"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        parameters={"dry_run": False},
    )


def success_result(value: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=value.protocol_version,
        operation=value.operation,
        correlation_id=value.correlation_id,
        outcome="succeeded",
        message="helper operation completed",
        state={"host_kind": "empty"},
        warnings=(),
    )


def package(tmp_path: Path) -> HelperPackage:
    return build_helper_package(tmp_path / "taskman-host.pyz")


def remote_for(value: HostRequest, helper: HelperPackage) -> HelperRunnerRemote:
    return HelperRunnerRemote(
        checksum=helper.sha256,
        helper_result=CommandResult(0, encode_result(success_result(value)).decode("utf-8")),
    )


def commands(remote: HelperRunnerRemote) -> list[tuple[str, ...]]:
    return [command for command, _kwargs in remote.calls]


def test_new_correlation_id_is_unique_and_uses_the_protocol_allowlist() -> None:
    from taskman_ops.helper_runner import new_correlation_id

    values = {new_correlation_id() for _ in range(16)}

    assert len(values) == 16
    assert all(re.fullmatch(r"op-[0-9a-f]{32}", value) for value in values)


def test_helper_runner_has_one_created_path_cleanup_tracker() -> None:
    """Reintroducing parallel cleanup flags would split exact-path cleanup authority."""

    tree = ast.parse(textwrap.dedent(inspect.getsource(helper_runner_module.invoke_helper)))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert not {"transfer_created", "invocation_created", "completed", "cleanup_needed"} & names


def test_invoke_helper_keeps_correlation_transport_only(tmp_path: Path) -> None:
    from taskman_ops.helper_runner import MAX_STDERR_BYTES, invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)

    result = invoke_helper(remote, helper, value)

    transfer = PurePosixPath("/tmp/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    installed = PurePosixPath("/run/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    assert result == success_result(value)
    assert remote.uploads[0][1] == transfer
    assert ("sha256sum", "--", transfer.as_posix()) in commands(remote)
    assert ("sha256sum", "--", installed.as_posix()) in commands(remote)
    helper_call = next(
        kwargs for command, kwargs in remote.calls
        if command == ("sudo", "--preserve-env=SSH_CONNECTION", "--", "python3", installed.as_posix())
    )
    assert helper_call["stdout_limit"] == MAX_OUTPUT_BYTES
    assert helper_call["stderr_limit"] == MAX_STDERR_BYTES
    assert value.correlation_id.encode() in helper_call["stdin"]


def test_invoke_helper_returns_one_result_with_one_cleanup_warning(tmp_path: Path) -> None:
    """Returning a wrapper or duplicate warning would split the final result boundary."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    installed = PurePosixPath("/run/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    remote.add_response(("rm", "--", installed.as_posix()), CommandResult(1))

    result = invoke_helper(remote, helper, value)

    assert isinstance(result, HostResult)
    assert result.warnings == ("transient helper cleanup was incomplete",)


def test_invoke_helper_bounds_cleanup_warning_when_result_is_full(tmp_path: Path) -> None:
    """A cleanup warning must not turn an otherwise valid helper result into a protocol error."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    warnings = tuple(f"warning-{index}" for index in range(MAX_COLLECTION_ITEMS))
    full_result = replace(success_result(value), warnings=warnings)
    remote.helper_result = CommandResult(0, encode_result(full_result).decode("utf-8"))
    installed = PurePosixPath("/run/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    remote.add_response(("rm", "--", installed.as_posix()), CommandResult(1))

    result = invoke_helper(remote, helper, value)

    assert result.warnings == (*warnings[1:], "transient helper cleanup was incomplete")


def test_run_request_preserves_only_local_cleanup_warning_for_an_unrelated_result(
    tmp_path: Path,
) -> None:
    """A rejected helper result must not erase exact-path cleanup evidence or trust its warnings."""

    from taskman_ops.workflows.helper import run_request

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    unrelated = replace(
        success_result(value),
        operation="verify",
        warnings=("untrusted remote warning",),
    )
    remote.helper_result = CommandResult(0, encode_result(unrelated).decode("utf-8"))
    installed = PurePosixPath("/run/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    remote.add_response(("rm", "--", installed.as_posix()), CommandResult(1))

    with pytest.raises(OpsError) as raised:
        run_request(remote, value, package=helper)

    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.warnings == ("transient helper cleanup was incomplete",)
    assert ("rm", "--", installed.as_posix()) in commands(remote)


def test_invoke_helper_refuses_package_checksum_before_transfer(tmp_path: Path) -> None:
    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = replace(package(tmp_path), sha256="0" * 64)
    remote = remote_for(value, helper)

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert remote.uploads == []


@pytest.mark.parametrize(
    "failure",
    (
        OpsError(ExitStatus.SAFETY, "helper", "command failed", False),
        RuntimeError("transport failed"),
    ),
)
def test_invoke_helper_attempts_generic_cleanup_after_unconfirmed_dispatch(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    """A missing correlated result never leaves the root helper archive unexamined."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    remote.helper_result = failure  # type: ignore[assignment]

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    installed = PurePosixPath("/run/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    invocation_directory = installed.parent
    assert ("rm", "--", installed.as_posix()) in commands(remote)
    assert ("rmdir", "--", invocation_directory.as_posix()) in commands(remote)
    assert raised.value.warnings == ("transient helper cleanup was incomplete",)
    assert value.correlation_id not in repr(raised.value.warnings)


@pytest.mark.parametrize("location", ("transfer", "invocation"))
def test_invoke_helper_cleans_attempted_private_paths_after_lost_creation_response(
    tmp_path: Path,
    location: str,
) -> None:
    """A lost mkdir response must still trigger exact-path cleanup for either private workspace."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    transfer_directory = PurePosixPath("/tmp/taskman-ops") / value.correlation_id
    transfer_path = transfer_directory / "taskman-host.pyz"
    invocation_directory = PurePosixPath("/run/taskman-ops") / value.correlation_id
    installed_path = invocation_directory / "taskman-host.pyz"
    directory, path, sudo = (
        (transfer_directory, transfer_path, False)
        if location == "transfer"
        else (invocation_directory, installed_path, True)
    )
    remote.add_response(
        ("mkdir", "-m", "700", "--", directory.as_posix()),
        OpsError(ExitStatus.REMOTE_PREFLIGHT, "remote", "lost creation response", False),
    )

    with pytest.raises(OpsError):
        invoke_helper(remote, helper, value)

    cleanup = {
        command: kwargs
        for command, kwargs in remote.calls
        if command in {
            ("rm", "--", path.as_posix()),
            ("rmdir", "--", directory.as_posix()),
        }
    }
    assert set(cleanup) == {
        ("rm", "--", path.as_posix()),
        ("rmdir", "--", directory.as_posix()),
    }
    assert all(
        kwargs["sudo"] is sudo
        and kwargs["sensitive"] is True
        and kwargs["timeout"] == 60
        and kwargs["stdout_limit"] == 1024
        and kwargs["stderr_limit"] == 4096
        for kwargs in cleanup.values()
    )
