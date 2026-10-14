from __future__ import annotations

import ast
from dataclasses import replace
import inspect
from pathlib import Path, PurePosixPath
import re
import textwrap

import pytest

from tests.support.remotes import HelperRunnerRemote
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.helper_client import runner as helper_runner_module
from taskman_ops.helper_client.package import HelperPackage, build_helper_package
from taskman_ops.host_protocol import (
    HostRequest,
    HostResult,
    MAX_COLLECTION_ITEMS,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    encode_result,
)
from taskman_ops.remote import CommandResult


def request(correlation_id: str = "op-0123456789abcdef0123456789abcdef") -> HostRequest:
    return HostRequest(
        protocol_version=3,
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
    from taskman_ops.helper_client.runner import new_correlation_id

    values = {new_correlation_id() for _ in range(16)}

    assert len(values) == 16
    assert all(re.fullmatch(r"op-[0-9a-f]{32}", value) for value in values)


def test_firewall_helper_interpreter_is_isolated(tmp_path: Path) -> None:
    value = replace(request(), operation="firewall")
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    helper_runner_module.invoke_helper(remote, helper, value)
    installed = f"/run/taskman-ops/{value.correlation_id}/taskman-host.pyz"
    assert ("sudo", "--preserve-env=SSH_CONNECTION", "--", "/usr/bin/python3", "-I", "-S", installed) in commands(remote)


def test_helper_runner_has_one_created_path_cleanup_tracker() -> None:
    """Reintroducing parallel cleanup flags would split exact-path cleanup authority."""

    tree = ast.parse(textwrap.dedent(inspect.getsource(helper_runner_module.invoke_helper)))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert not {"transfer_created", "invocation_created", "completed", "cleanup_needed"} & names


def test_invoke_helper_keeps_correlation_transport_only(tmp_path: Path) -> None:
    from taskman_ops.helper_client.runner import MAX_STDERR_BYTES, invoke_helper

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


@pytest.mark.parametrize("database_state", ["ready", "absent"])
def test_sensitive_pgpass_entry_retains_only_status_and_cleanup_warning(tmp_path: Path, database_state: str) -> None:
    from taskman_ops.helper_client.runner import invoke_sensitive_pgpass_authority

    helper = package(tmp_path)
    value = request()
    remote = remote_for(value, helper)
    installed = PurePosixPath("/run/taskman-ops") / value.correlation_id / "taskman-host.pyz"
    remote.add_response(
        (
            "sudo", "--preserve-env=SSH_CONNECTION", "--", "python3", installed.as_posix(),
            "provision-pgpass-authority", "127.0.0.1", "5432", "taskman", "taskman_prod",
            database_state, "authenticate",
        ),
        CommandResult(0, "discarded secret output", "discarded secret error"),
    )
    remote.add_response(("rm", "--", installed.as_posix()), CommandResult(1))

    receipt = invoke_sensitive_pgpass_authority(
        remote, helper, correlation_id=value.correlation_id,
        host="127.0.0.1", port=5432, role="taskman", database="taskman_prod",
        pgpass=b"127.0.0.1:5432:taskman_prod:taskman:secret\n",
        database_state=database_state,
    )

    assert receipt.exit_status == 0
    assert receipt.warnings == ("transient helper cleanup was incomplete",)
    assert receipt.local_cleanup_incomplete is True
    private_call = next(
        kwargs for command, kwargs in remote.calls
        if "provision-pgpass-authority" in command
    )
    assert private_call["sensitive"] is True
    assert private_call["stdout_limit"] > 0
    assert private_call["stderr_limit"] > 0


def test_sensitive_pgpass_entry_refuses_oversized_secret_before_remote_dispatch(
    tmp_path: Path,
) -> None:
    from taskman_ops.helper_client.runner import invoke_sensitive_pgpass_authority

    helper = package(tmp_path)
    remote = remote_for(request(), helper)
    secret = b"secret-canary-" + (b"x" * MAX_INPUT_BYTES)

    with pytest.raises(TypeError, match="inputs are invalid") as raised:
        invoke_sensitive_pgpass_authority(
            remote,
            helper,
            correlation_id=request().correlation_id,
            host="127.0.0.1",
            port=5432,
            role="taskman",
            database="taskman_prod",
            pgpass=secret,
            database_state="ready",
        )

    assert remote.calls == []
    assert remote.uploads == []
    assert b"secret-canary" not in str(raised.value).encode()



def test_invoke_helper_caps_each_remote_command_to_the_shared_remaining_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskman_ops.helper_client.runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    monkeypatch.setattr(helper_runner_module.time, "monotonic", lambda: 100.0)

    invoke_helper(remote, helper, value, deadline=107.9)

    assert all(kwargs["timeout"] <= 7 for _command, kwargs in remote.calls)
    assert remote.uploads[0][2]["timeout"] <= 7


def test_invoke_helper_returns_one_result_with_one_cleanup_warning(tmp_path: Path) -> None:
    """Returning a wrapper or duplicate warning would split the final result boundary."""

    from taskman_ops.helper_client.runner import invoke_helper

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

    from taskman_ops.helper_client.runner import invoke_helper

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
    from taskman_ops.helper_client.runner import invoke_helper

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

    from taskman_ops.helper_client.runner import invoke_helper

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

    from taskman_ops.helper_client.runner import invoke_helper

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


@pytest.mark.parametrize("status", [0, 5])
def test_sensitive_runner_tracks_probe_cleanup_before_archive(tmp_path, status):
    helper = package(tmp_path)
    remote = remote_for(request(), helper)
    installed = f"/run/taskman-ops/{request().correlation_id}/taskman-host.pyz"
    probe = f"/run/taskman-ops/{request().correlation_id}/pgpass.probe"
    private = ("sudo", "--preserve-env=SSH_CONNECTION", "--", "python3", installed,
               "provision-pgpass-authority", "127.0.0.1", "5432", "taskman", "taskman_prod", "ready", "authenticate")
    remote.add_response(private, CommandResult(status, "suppressed", "suppressed"))
    receipt = helper_runner_module.invoke_sensitive_pgpass_authority(remote, helper,
        correlation_id=request().correlation_id, host="127.0.0.1", port=5432, role="taskman",
        database="taskman_prod", pgpass=b"127.0.0.1:5432:taskman_prod:taskman:secret\n", database_state="ready")
    assert receipt.exit_status == status
    assert commands(remote).index(("rm", "-f", "--", probe)) < commands(remote).index(("rm", "--", installed)) < commands(remote).index(("rmdir", "--", str(PurePosixPath(installed).parent)))
    for command, kwargs in remote.calls:
        assert "secret" not in repr(command)
        if command == private or probe in command:
            assert kwargs["sensitive"] is True
            assert kwargs.get("env") is None


@pytest.mark.parametrize("cleanup_lost", [False, True])
def test_sensitive_timeout_attempts_probe_cleanup_and_preserves_uncertainty(tmp_path, cleanup_lost):
    helper = package(tmp_path)
    installed = f"/run/taskman-ops/{request().correlation_id}/taskman-host.pyz"
    probe = f"/run/taskman-ops/{request().correlation_id}/pgpass.probe"
    class InterruptedRemote(HelperRunnerRemote):
        def run(self, argv, **kwargs):
            if "provision-pgpass-authority" in argv:
                self.calls.append((tuple(argv), kwargs))
                raise TimeoutError("helper killed before finally")
            if probe in argv and cleanup_lost:
                self.calls.append((tuple(argv), kwargs))
                raise ConnectionError("transport lost")
            return super().run(argv, **kwargs)
    remote = InterruptedRemote(checksum=helper.sha256, helper_result=CommandResult(0))
    with pytest.raises(OpsError) as raised:
        helper_runner_module.invoke_sensitive_pgpass_authority(remote, helper,
            correlation_id=request().correlation_id, host="127.0.0.1", port=5432, role="taskman",
            database="taskman_prod", pgpass=b"127.0.0.1:5432:taskman_prod:taskman:secret\n", database_state="ready")
    assert ("rm", "-f", "--", probe) in commands(remote)
    assert ("rm", "--", installed) in commands(remote)
    assert raised.value.warnings == ("transient helper cleanup was incomplete",)
    assert raised.value.changed is False
    assert "secret" not in str(raised.value)


def test_runner_removes_real_probe_file_after_helper_process_is_killed(tmp_path):
    """The remote cleanup owns the passfile when helper finally cannot run."""
    import subprocess
    import sys
    helper = package(tmp_path)
    probe = tmp_path / "pgpass.probe"
    derived_probe = f"/run/taskman-ops/{request().correlation_id}/pgpass.probe"
    script = '''import sys, signal
from pathlib import Path
sys.argv[0] = sys.argv.pop(1)
sys.path.insert(0, sys.argv[0])
from taskman_ops.host_helper.operations import preflight
preflight._PGPASS_PATH = Path(sys.argv.pop(1))
def blocked(*args):
    print("probe-created", flush=True)
    signal.pause()
preflight.prove_scram_access = blocked
raise SystemExit(preflight.provision_pgpass_authority(tuple(sys.argv[1:]), sys.stdin.buffer))
'''
    class KilledEntryRemote(HelperRunnerRemote):
        def run(self, argv, **kwargs):
            if "provision-pgpass-authority" in argv:
                self.calls.append((tuple(argv), kwargs))
                entry = argv.index("provision-pgpass-authority")
                child = subprocess.Popen([sys.executable, "-I", "-S", "-c", script,
                    str(helper.path), str(tmp_path / "missing"), *argv[entry + 1:]],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    child.communicate(kwargs["stdin"], timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    stdout, stderr = child.communicate()
                    assert stdout == b"probe-created\n" and stderr == b""
                    assert probe.is_file()
                    raise TimeoutError("helper process killed before cleanup")
                pytest.fail("helper should have waited in the application proof")
            if derived_probe in argv:
                assert kwargs["sensitive"] is True
                completed = subprocess.run(("rm", "-f", "--", str(probe)), capture_output=True, check=False)
                self.calls.append((tuple(argv), kwargs))
                return CommandResult(completed.returncode)
            return super().run(argv, **kwargs)
    remote = KilledEntryRemote(checksum=helper.sha256, helper_result=CommandResult(0))
    with pytest.raises(OpsError):
        helper_runner_module.invoke_sensitive_pgpass_authority(remote, helper,
            correlation_id=request().correlation_id, host="127.0.0.1", port=5432, role="taskman",
            database="taskman_prod", pgpass=b"127.0.0.1:5432:taskman_prod:taskman:secret\n", database_state="ready")
    assert not probe.exists()
