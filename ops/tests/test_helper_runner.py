from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path, PurePosixPath
import re

import pytest

from fakes import HelperRunnerRemote
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.helper_package import HelperPackage, build_helper_package
from taskman_ops.host_protocol import (
    MAX_OUTPUT_BYTES,
    HostRequest,
    HostResult,
    encode_result,
)
from taskman_ops.remote import CommandResult


def request(operation_id: str = "op-0123456789abcdef0123456789abcdef") -> HostRequest:
    return HostRequest(
        protocol_version=1,
        operation="discover",
        operation_id=operation_id,
        expected_state={"lifecycle": "unknown"},
        paths={"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        parameters={"dry_run": False},
    )


def success_result(value: HostRequest) -> HostResult:
    return HostResult(
        protocol_version=value.protocol_version,
        operation=value.operation,
        operation_id=value.operation_id,
        outcome="succeeded",
        stage="complete",
        changed_stages=(),
        lifecycle={},
        runtime_state={},
        verification={},
        residue_paths=(),
        recovery_actions=(),
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


def test_new_operation_id_is_unique_and_uses_the_protocol_allowlist() -> None:
    """Replacing cryptographic IDs with a reusable or unsafe path token must fail here."""

    from taskman_ops.helper_runner import new_operation_id

    operation_ids = {new_operation_id() for _ in range(16)}

    assert len(operation_ids) == 16
    assert all(re.fullmatch(r"op-[0-9a-f]{32}", operation_id) for operation_id in operation_ids)


def test_invoke_helper_privately_transfers_verifies_invokes_and_removes_the_exact_paths(
    tmp_path: Path,
) -> None:
    """Dropping a transfer, ownership, checksum, argv, or cleanup guard must fail here."""

    from taskman_ops.helper_runner import MAX_STDERR_BYTES, invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)

    invocation = invoke_helper(remote, helper, value)

    transfer_directory = PurePosixPath("/tmp/taskman-ops") / value.operation_id
    transfer_path = transfer_directory / "taskman-host.pyz"
    invocation_directory = PurePosixPath("/run/taskman-ops") / value.operation_id
    installed_helper = invocation_directory / "taskman-host.pyz"
    assert invocation.result == success_result(value)
    assert invocation.cleanup_warning is None
    assert all(
        kwargs["stdout_limit"] is not None and kwargs["stderr_limit"] is not None
        for _command, kwargs in remote.calls
    )
    assert remote.uploads == [
        (
            helper.path,
            transfer_path,
            {"mode": 0o600, "sensitive": True, "timeout": 60, "sudo": False},
        )
    ]
    assert ("sha256sum", "--", transfer_path.as_posix()) in commands(remote)
    assert ("mkdir", "-m", "700", "--", "/run/taskman-ops") in commands(remote)
    assert ("mkdir", "-m", "700", "--", invocation_directory.as_posix()) in commands(remote)
    assert (
        "install",
        "-o",
        "root",
        "-g",
        "root",
        "-m",
        "500",
        "--",
        transfer_path.as_posix(),
        installed_helper.as_posix(),
    ) in commands(remote)
    assert ("sha256sum", "--", installed_helper.as_posix()) in commands(remote)
    assert ("rm", "--", transfer_path.as_posix()) in commands(remote)
    assert ("rmdir", "--", transfer_directory.as_posix()) in commands(remote)
    assert ("rm", "--", installed_helper.as_posix()) in commands(remote)
    assert ("rmdir", "--", invocation_directory.as_posix()) in commands(remote)

    helper_command = (
        "sudo",
        "--preserve-env=SSH_CONNECTION",
        "--",
        "python3",
        installed_helper.as_posix(),
    )
    helper_call = next(kwargs for command, kwargs in remote.calls if command == helper_command)
    assert helper_call == {
        "sudo": False,
        "stdin": (
            b'{"expected_state":{"lifecycle":"unknown"},"operation":"discover",'
            b'"operation_id":"op-0123456789abcdef0123456789abcdef",'
            b'"parameters":{"dry_run":false},"paths":{"backup_root":"/var/backups/taskman",'
            b'"install_root":"/opt/taskman"},"protocol_version":1}'
        ),
        "sensitive": False,
        "timeout": 660,
        "stdout_limit": MAX_OUTPUT_BYTES,
        "stderr_limit": MAX_STDERR_BYTES,
    }


def test_invoke_helper_accepts_real_line_terminated_control_command_output(tmp_path: Path) -> None:
    """Raw control-command newlines must be parsed after, not inside, Remote.run."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)

    invocation = invoke_helper(remote, helper, value)

    assert invocation.result == success_result(value)
    assert invocation.cleanup_warning is None


@pytest.mark.parametrize(
    ("control", "output"),
    [
        ("uid", "1000"),
        ("gid", "1000\n\n"),
        ("metadata", "1000:1000:700:directory"),
        ("checksum", None),
    ],
)
def test_invoke_helper_refuses_control_output_without_exactly_one_terminal_newline(
    tmp_path: Path, control: str, output: str | None
) -> None:
    """Accepting missing or repeated terminators would weaken exact authority checks."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    transfer_path = f"/tmp/taskman-ops/{value.operation_id}/taskman-host.pyz"
    argv, response = {
        "uid": (("id", "-u"), output),
        "gid": (("id", "-g"), output),
        "metadata": (("stat", "-c", "%u:%g:%a:%F", "--", "/tmp/taskman-ops"), output),
        "checksum": (("sha256sum", "--", transfer_path), f"{helper.sha256}  {transfer_path}\n\n"),
    }[control]
    assert isinstance(response, str)
    remote.add_response(argv, CommandResult(0, response))

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY


def test_invoke_helper_uses_the_request_operation_id_unchanged_for_each_private_path(
    tmp_path: Path,
) -> None:
    """Replacing frozen correlation IDs would mix helper results across invocations."""

    from taskman_ops.helper_runner import invoke_helper

    first_request = request("op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    second_request = request("op-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    helper = package(tmp_path)
    first_remote = remote_for(first_request, helper)
    second_remote = remote_for(second_request, helper)

    invoke_helper(first_remote, helper, first_request)
    invoke_helper(second_remote, helper, second_request)

    first_upload = first_remote.uploads[0][1]
    second_upload = second_remote.uploads[0][1]
    assert first_upload != second_upload
    assert first_upload.as_posix().endswith(first_request.operation_id + "/taskman-host.pyz")
    assert second_upload.as_posix().endswith(second_request.operation_id + "/taskman-host.pyz")
    assert (
        "sudo",
        "--preserve-env=SSH_CONNECTION",
        "--",
        "python3",
        f"/run/taskman-ops/{first_request.operation_id}/taskman-host.pyz",
    ) in commands(first_remote)
    assert (
        "sudo",
        "--preserve-env=SSH_CONNECTION",
        "--",
        "python3",
        f"/run/taskman-ops/{second_request.operation_id}/taskman-host.pyz",
    ) in commands(second_remote)


def test_invoke_helper_refuses_a_local_package_checksum_mismatch_before_transfer(tmp_path: Path) -> None:
    """Uploading local bytes under a false checksum would defeat both remote hash checks."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = replace(package(tmp_path), sha256="0" * 64)
    remote = remote_for(value, helper)

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert remote.uploads == []
    assert remote.calls == []


def test_invoke_helper_refuses_a_remote_transfer_checksum_mismatch_before_privilege_escalation(
    tmp_path: Path,
) -> None:
    """Installing a remote file whose bytes changed after upload must never reach root."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    transfer_path = f"/tmp/taskman-ops/{value.operation_id}/taskman-host.pyz"
    remote.add_response(("sha256sum", "--", transfer_path), CommandResult(0, f"{'0' * 64}  {transfer_path}\n"))

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert not any(command[0] == "mkdir" and command[-1].startswith("/run/") for command in commands(remote))
    assert ("rm", "--", transfer_path) in commands(remote)


def test_invoke_helper_refuses_an_existing_invocation_directory_without_removing_it(
    tmp_path: Path,
) -> None:
    """Treating an existing `/run` path as ours could erase another invocation's state."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    invocation_directory = f"/run/taskman-ops/{value.operation_id}"
    remote.add_response(("mkdir", "-m", "700", "--", invocation_directory), CommandResult(1))

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert ("rmdir", "--", invocation_directory) not in commands(remote)


@pytest.mark.parametrize(
    "bad_metadata",
    [
        "0:0:700:symbolic link",
        "1000:1000:700:directory",
        "0:0:755:directory",
    ],
)
def test_invoke_helper_refuses_an_installed_helper_directory_without_exact_root_authority(
    tmp_path: Path, bad_metadata: str
) -> None:
    """A symlink, foreign owner, or loose mode at the privileged path must block execution."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    invocation_directory = f"/run/taskman-ops/{value.operation_id}"
    remote.add_response(
        ("stat", "-c", "%u:%g:%a:%F", "--", invocation_directory),
        CommandResult(0, f"{bad_metadata}\n"),
    )

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert not any(command[:3] == ("sudo", "--preserve-env=SSH_CONNECTION", "--") for command in commands(remote))


def test_invoke_helper_refuses_an_installed_helper_checksum_change_before_execution(
    tmp_path: Path,
) -> None:
    """A post-install byte change must be caught at the root-owned execution path."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    installed_path = f"/run/taskman-ops/{value.operation_id}/taskman-host.pyz"
    remote.add_response(("sha256sum", "--", installed_path), CommandResult(0, f"{'f' * 64}  {installed_path}\n"))

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert not any(command[:3] == ("sudo", "--preserve-env=SSH_CONNECTION", "--") for command in commands(remote))
    assert ("rmdir", "--", f"/run/taskman-ops/{value.operation_id}") in commands(remote)


def test_invoke_helper_removes_an_empty_root_directory_after_installation_fails(tmp_path: Path) -> None:
    """Skipping exact empty-dir cleanup after a failed install leaves avoidable privileged residue."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    transfer_path = f"/tmp/taskman-ops/{value.operation_id}/taskman-host.pyz"
    installed_path = f"/run/taskman-ops/{value.operation_id}/taskman-host.pyz"
    remote.add_response(
        (
            "install",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "500",
            "--",
            transfer_path,
            installed_path,
        ),
        CommandResult(1),
    )
    remote.add_response(
        ("stat", "-c", "%u:%g:%a:%F", "--", installed_path),
        CommandResult(1),
    )

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.helper_entry_started is False
    assert not hasattr(raised.value, "residue_paths")
    assert ("rmdir", "--", f"/run/taskman-ops/{value.operation_id}") in commands(remote)


@pytest.mark.parametrize(
    "result_bytes",
    [
        b"x" * (MAX_OUTPUT_BYTES + 1),
        json.dumps(
            {
                "protocol_version": 1,
                "operation": "discover",
                "operation_id": "op-0123456789abcdef0123456789abcdef",
                "outcome": "succeeded",
                "stage": "complete",
                "changed_stages": [],
                "lifecycle": {},
                "runtime_state": {},
                "residue_paths": [],
                "recovery_actions": [],
                "warnings": [],
            }
        ).encode(),
    ],
)
def test_invoke_helper_rejects_oversized_or_incomplete_stdout_without_echoing_it(
    tmp_path: Path, result_bytes: bytes
) -> None:
    """Permissive helper decoding could accept a truncated result or expose host diagnostics."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    remote.helper_result = CommandResult(0, result_bytes.decode("utf-8", "surrogateescape"))

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert "x" * 32 not in str(raised.value)


def test_invoke_helper_rejects_bounded_stderr_without_echoing_a_canary(tmp_path: Path) -> None:
    """A helper diagnostic beyond its limit must not enter an operator-visible exception."""

    from taskman_ops.helper_runner import MAX_STDERR_BYTES, invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    canary = "helper-stderr-canary"
    remote.helper_result = CommandResult(0, encode_result(success_result(value)).decode(), canary * MAX_STDERR_BYTES)

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert canary not in str(raised.value)
    assert canary not in repr(raised.value)


@pytest.mark.parametrize(
    "result",
    [
        lambda value: replace(success_result(value), operation="verify"),
        lambda value: replace(
            success_result(value), operation_id="op-fedcba9876543210fedcba9876543210"
        ),
    ],
)
def test_invoke_helper_rejects_a_result_that_does_not_correlate_to_its_request(
    tmp_path: Path, result: object
) -> None:
    """Accepting a valid result for another operation or ID would cross transaction boundaries."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    remote.helper_result = CommandResult(0, encode_result(result(value)).decode())  # type: ignore[operator]

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY


def test_invoke_helper_keeps_a_primary_failure_when_completion_is_uncertain(tmp_path: Path) -> None:
    """Unknown helper completion retains both privileged invocation paths."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    remote.helper_result = CommandResult(23, "raw helper stdout", "raw helper stderr")
    invocation_directory = f"/run/taskman-ops/{value.operation_id}"
    installed_helper = f"{invocation_directory}/taskman-host.pyz"

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.message == "host helper invocation failed"
    # A nonzero remote command does not prove SSH reached sudo or that sudo
    # reached Python. The executable and directory may still be in use.
    assert raised.value.helper_entry_started is False
    assert raised.value.helper_entry_completed is False
    assert getattr(raised.value, "residue_paths") == (installed_helper, invocation_directory)
    assert ("rm", "--", installed_helper) not in commands(remote)
    assert ("rmdir", "--", invocation_directory) not in commands(remote)
    assert "raw helper" not in repr(raised.value)


@pytest.mark.parametrize("failure", ("ssh", "sudo"))
def test_invoke_helper_treats_ambiguous_ssh_and_sudo_failures_as_unproven_entry(
    tmp_path: Path, failure: str
) -> None:
    """No transport failure proves the privileged Python entrypoint ran.

    Marking entry as started before ``Remote.run`` would skip the controller's
    exact upload cleanup after an SSH failure or a sudo/nonzero refusal.
    """

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    helper_command = (
        "sudo",
        "--preserve-env=SSH_CONNECTION",
        "--",
        "python3",
        f"/run/taskman-ops/{value.operation_id}/taskman-host.pyz",
    )
    if failure == "ssh":
        remote.add_response(helper_command, OSError("ssh-canary-transport"))
    else:
        remote.add_response(helper_command, CommandResult(1, "", "sudo-canary-refusal"))

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.helper_entry_started is False
    assert "canary" not in repr(raised.value)


def test_invoke_helper_retains_the_installed_helper_after_nonzero_without_proven_completion(
    tmp_path: Path,
) -> None:
    """A nonzero runner result cannot prove the privileged helper completed.

    Treating the SSH client's exit as the helper's completion could remove an
    executable still owned by a remote process after an interrupted session.
    """

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    remote.helper_result = CommandResult(1, "", "nonzero-canary")
    invocation_directory = f"/run/taskman-ops/{value.operation_id}"
    installed_helper = f"{invocation_directory}/taskman-host.pyz"

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.helper_entry_dispatched is True
    assert raised.value.helper_entry_completed is False
    assert raised.value.residue_paths == (installed_helper, invocation_directory)
    assert raised.value.recovery_commands == (
        "inspect the transient helper invocation artifact before retrying",
    )
    assert raised.value.warnings == (
        "helper invocation completion is uncertain; installed helper artifact was retained",
    )
    assert ("rm", "--", installed_helper) not in commands(remote)
    assert ("rmdir", "--", invocation_directory) not in commands(remote)
    assert "canary" not in repr(raised.value)


def test_invoke_helper_marks_a_local_request_encoding_failure_as_pre_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request that never serializes cannot have reached host Python."""

    import taskman_ops.helper_runner as runner

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)

    def refuse_encoding(_request: object) -> bytes:
        raise OpsError(ExitStatus.SAFETY, "helper-request", "local request encoding failed")

    monkeypatch.setattr(runner, "encode_request", refuse_encoding)

    with pytest.raises(OpsError) as raised:
        runner.invoke_helper(remote, helper, value)

    assert raised.value.helper_entry_dispatched is False
    assert not any(
        command[:3] == ("sudo", "--preserve-env=SSH_CONNECTION", "--")
        for command in commands(remote)
    )


def test_invoke_helper_returns_truthful_cleanup_residue_after_a_success(tmp_path: Path) -> None:
    """Reporting a clean success after an uncertain root-dir removal would mislead recovery."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    invocation_directory = f"/run/taskman-ops/{value.operation_id}"
    remote.add_response(("rmdir", "--", invocation_directory), CommandResult(1))

    invocation = invoke_helper(remote, helper, value)

    assert invocation.result == success_result(value)
    assert invocation.cleanup_warning is not None
    assert invocation_directory in invocation.cleanup_warning


def test_invoke_helper_reports_private_upload_staging_residue_after_a_success(tmp_path: Path) -> None:
    """A `put` receipt is controller evidence and must survive an otherwise clean helper result."""

    from taskman_ops.helper_runner import invoke_helper
    from taskman_ops.remote import UploadReceipt

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    remote.put_response = UploadReceipt(
        residue_paths=("/tmp/taskman-upload-unique/payload", "/tmp/taskman-upload-unique")
    )

    invocation = invoke_helper(remote, helper, value)

    assert invocation.result == success_result(value)
    assert invocation.cleanup_warning is not None
    assert "/tmp/taskman-upload-unique/payload" in invocation.cleanup_warning
    assert "/tmp/taskman-upload-unique" in invocation.cleanup_warning


def test_invoke_helper_preserves_upload_failure_while_merging_its_private_stage_residue(
    tmp_path: Path,
) -> None:
    """Transport cleanup evidence must not overwrite the upload's primary controller failure."""

    from taskman_ops.helper_runner import invoke_helper
    from taskman_ops.remote import UploadFailure

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    primary = OpsError(ExitStatus.REMOTE_PREFLIGHT, "remote", "private remote upload failed")
    failure = UploadFailure(
        primary,
        ("/tmp/taskman-upload-unique/payload", "/tmp/taskman-upload-unique"),
    )
    remote.put_response = failure

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value is failure
    assert failure.primary_error is primary
    assert getattr(raised.value, "residue_paths") == (
        "/tmp/taskman-upload-unique/payload",
        "/tmp/taskman-upload-unique",
    )


@pytest.mark.parametrize(
    ("root", "sudo", "metadata"),
    [
        ("/tmp/taskman-ops", False, "1000:1000:755:directory"),
        ("/run/taskman-ops", True, "0:0:755:directory"),
    ],
)
def test_invoke_helper_reports_a_new_directory_when_its_post_mkdir_authority_check_fails(
    tmp_path: Path, root: str, sudo: bool, metadata: str
) -> None:
    """A successful mkdir followed by bad metadata may leave a path that the controller cannot own."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    directory = f"{root}/{value.operation_id}"
    metadata_command = ("stat", "-c", "%u:%g:%a:%F", "--", directory)
    remote.add_response(metadata_command, CommandResult(0, f"{metadata}\n"))
    remote.add_response(metadata_command, CommandResult(0, f"{metadata}\n"))

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert getattr(raised.value, "residue_paths") == (directory,)
    assert ("rmdir", "--", directory) not in commands(remote)


@pytest.mark.parametrize(("root", "sudo"), [("/tmp/taskman-ops", False), ("/run/taskman-ops", True)])
def test_invoke_helper_reports_a_new_directory_when_its_mkdir_transport_is_uncertain(
    tmp_path: Path, root: str, sudo: bool
) -> None:
    """A lost mkdir response cannot be treated as proof that no directory exists."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    directory = f"{root}/{value.operation_id}"
    mkdir_command = ("mkdir", "-m", "700", "--", directory)
    remote.add_response(mkdir_command, OSError("transport uncertainty"))
    if root == "/tmp/taskman-ops":
        remote.add_response(("id", "-u"), CommandResult(0, "1000\n"))
        remote.add_response(("id", "-u"), OSError("identity unavailable"))
    else:
        remote.add_response(
            ("stat", "-c", "%u:%g:%a:%F", "--", directory),
            CommandResult(0, "0:0:755:directory\n"),
        )

    with pytest.raises(OpsError) as raised:
        invoke_helper(remote, helper, value)

    assert raised.value.status is ExitStatus.SAFETY
    assert getattr(raised.value, "residue_paths") == (directory,)
    assert ("rmdir", "--", directory) not in commands(remote)


def test_invoke_helper_refuses_to_remove_a_root_path_when_its_final_authority_check_changes(
    tmp_path: Path,
) -> None:
    """Recursive or unchecked cleanup could remove a path swapped after the helper returned."""

    from taskman_ops.helper_runner import invoke_helper

    value = request()
    helper = package(tmp_path)
    remote = remote_for(value, helper)
    invocation_directory = f"/run/taskman-ops/{value.operation_id}"
    metadata_command = ("stat", "-c", "%u:%g:%a:%F", "--", invocation_directory)
    remote.add_response(metadata_command, CommandResult(0, "0:0:700:directory\n"))
    remote.add_response(metadata_command, CommandResult(0, "0:0:755:directory\n"))

    invocation = invoke_helper(remote, helper, value)

    assert invocation.cleanup_warning is not None
    assert invocation_directory in invocation.cleanup_warning
    assert ("rm", "--", f"{invocation_directory}/taskman-host.pyz") not in commands(remote)
    assert ("rmdir", "--", invocation_directory) not in commands(remote)
