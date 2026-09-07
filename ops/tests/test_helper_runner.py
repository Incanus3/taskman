from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath
import re

import pytest

from fakes import HelperRunnerRemote
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.helper_package import HelperPackage, build_helper_package
from taskman_ops.host_protocol import HostRequest, HostResult, MAX_OUTPUT_BYTES, encode_result
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


def test_invoke_helper_keeps_correlation_transport_only_and_validates_result(tmp_path: Path) -> None:
    from taskman_ops.helper_runner import MAX_STDERR_BYTES, invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)

    invocation = invoke_helper(remote, helper, value)

    transfer = PurePosixPath("/tmp/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    installed = PurePosixPath("/run/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    assert invocation.result == success_result(value)
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


def test_invoke_helper_refuses_an_unrelated_final_result(tmp_path: Path) -> None:
    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    unrelated = success_result(request("op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
    remote.helper_result = CommandResult(0, encode_result(unrelated).decode("utf-8"))

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY


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
