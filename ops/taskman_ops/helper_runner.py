"""One strict controller boundary for transient host-helper execution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import secrets
from typing import Sequence

from .errors import ExitStatus, OpsError
from .helper_package import HelperPackage
from .host_protocol import (
    MAX_OUTPUT_BYTES,
    HostRequest,
    HostResult,
    decode_result,
    encode_request,
)
from .host_protocol.identifiers import validate_operation_id
from .remote import CommandResult, Remote, UploadReceipt


_TRANSFER_ROOT = PurePosixPath("/tmp/taskman-ops")
_INVOCATION_ROOT = PurePosixPath("/run/taskman-ops")
_HELPER_NAME = "taskman-host.pyz"
_TRANSFER_DIRECTORY_MODE = "700"
_TRANSFER_FILE_MODE = "600"
_INVOCATION_DIRECTORY_MODE = "700"
_INSTALLED_HELPER_MODE = "500"
_CONTROL_TIMEOUT_SECONDS = 60
# The helper's transaction deadline is 600 seconds. The runner leaves an
# additional transport/encoding window, so a bounded helper timeout produces
# its own structured state/residue evidence before SSH may terminate it.
_OPERATION_TIMEOUT_SECONDS = 660
_METADATA_LIMIT = 128
_CHECKSUM_LIMIT = 160
_IDENTITY_LIMIT = 32
_CONTROL_STDOUT_LIMIT = 1024
_CONTROL_STDERR_LIMIT = 4096
MAX_STDERR_BYTES = 16 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL_RE = re.compile(r"[0-9]+\Z")


@dataclass(frozen=True)
class HelperInvocation:
    """The validated helper result plus any conservative cleanup evidence."""

    result: HostResult
    cleanup_warning: str | None = None


@dataclass
class _DirectoryCreation:
    """What one selected operation directory may have done on the host."""

    possibly_created: bool = False
    authority_proven: bool = False


def new_operation_id() -> str:
    """Create the random, allowlisted correlation ID required for a new request."""

    return validate_operation_id(f"op-{secrets.token_hex(16)}")


def invoke_helper(
    remote: Remote,
    package: HelperPackage,
    request: HostRequest,
) -> HelperInvocation:
    """Transfer, verify, invoke, decode, and conservatively remove one helper.

    The helper owns host lifecycle policy. This boundary deliberately checks
    only the local package, privileged-file authority, bounded protocol, and
    request/result correlation needed before its result reaches a workflow.
    """

    _validate_inputs(package, request)
    operation_id = request.operation_id
    transfer_directory = _TRANSFER_ROOT / operation_id
    transfer_path = transfer_directory / _HELPER_NAME
    invocation_directory = _INVOCATION_ROOT / operation_id
    installed_helper = invocation_directory / _HELPER_NAME
    transfer_creation = _DirectoryCreation()
    invocation_creation = _DirectoryCreation()
    upload_residue_paths: list[str] = []
    primary_error: OpsError | None = None
    invocation: HelperInvocation | None = None
    helper_entry_started = False
    helper_entry_dispatched = False
    helper_entry_completed = False

    try:
        administrator = _administrator_identity(remote)
        _ensure_directory(remote, _TRANSFER_ROOT, owner=administrator, sudo=False)
        _create_new_directory(
            remote,
            transfer_directory,
            owner=administrator,
            sudo=False,
            creation=transfer_creation,
        )

        try:
            receipt = remote.put(
                package.path,
                transfer_path,
                mode=0o600,
                sensitive=True,
                timeout=_CONTROL_TIMEOUT_SECONDS,
                sudo=False,
            )
            upload_residue_paths.extend(_upload_residue_paths(receipt))
        except OpsError as error:
            upload_residue_paths.extend(_error_residue_paths(error))
            raise
        _assert_metadata(
            remote,
            transfer_directory,
            owner=administrator,
            mode=_TRANSFER_DIRECTORY_MODE,
            file_type="directory",
            sudo=False,
        )
        _assert_metadata(
            remote,
            transfer_path,
            owner=administrator,
            mode=_TRANSFER_FILE_MODE,
            file_type="regular file",
            sudo=False,
        )
        _assert_checksum(remote, transfer_path, package.sha256, sudo=False)

        _ensure_directory(remote, _INVOCATION_ROOT, owner=("0", "0"), sudo=True)
        _create_new_directory(
            remote,
            invocation_directory,
            owner=("0", "0"),
            sudo=True,
            creation=invocation_creation,
        )
        _require_success(
            remote,
            (
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                _INSTALLED_HELPER_MODE,
                "--",
                transfer_path.as_posix(),
                installed_helper.as_posix(),
            ),
            sudo=True,
            sensitive=True,
        )
        _assert_metadata(
            remote,
            installed_helper,
            owner=("0", "0"),
            mode=_INSTALLED_HELPER_MODE,
            file_type="regular file",
            sudo=True,
        )
        _assert_checksum(remote, installed_helper, package.sha256, sudo=True)

        if not _cleanup_transfer(remote, transfer_directory, transfer_path, administrator):
            raise _safety_error("unable to remove the verified helper transfer")
        transfer_creation.possibly_created = False

        # Dispatching an SSH command is not proof that SSH reached sudo or
        # that sudo reached Python. Keep controller ownership of the exact
        # release upload until a bounded, correlated protocol result proves
        # entry. The privileged helper archive can be cleaned only after the
        # dispatched command itself is known to have completed.
        request_payload = encode_request(request)
        helper_entry_dispatched = True
        completed = _run(
            remote,
            # ``sudo`` does not provide SSH connection metadata itself.
            # Preserve OpenSSH's real pre-sudo session fact through this
            # fixed boundary for the root helper's host authority check.
            ("sudo", "--preserve-env=SSH_CONNECTION", "--", "python3", installed_helper.as_posix()),
            sudo=False,
            stdin=request_payload,
            sensitive=False,
            stdout_limit=MAX_OUTPUT_BYTES,
            stderr_limit=MAX_STDERR_BYTES,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )
        if not _succeeded(completed):
            raise _safety_error("host helper invocation failed")
        result = _decode_helper_result(completed)
        _validate_result_correlation(result, package, request)
        # Only an exact correlated protocol result proves the root helper
        # completed. A runner return code or malformed/uncorrelated payload
        # may follow a transport break while its remote process still owns the
        # installed archive.
        helper_entry_completed = True
        helper_entry_started = True
        invocation = HelperInvocation(result=result)
    except OpsError as error:
        error.helper_entry_started = helper_entry_started
        error.helper_entry_dispatched = helper_entry_dispatched
        error.helper_entry_completed = helper_entry_completed
        primary_error = error
    except Exception:
        primary_error = _safety_error("transient helper invocation failed")
        primary_error.helper_entry_started = helper_entry_started
        primary_error.helper_entry_dispatched = helper_entry_dispatched
        primary_error.helper_entry_completed = helper_entry_completed

    residue_paths = list(upload_residue_paths)
    if transfer_creation.possibly_created and not _cleanup_transfer(
        remote, transfer_directory, transfer_path, _safe_identity(remote)
    ):
        residue_paths.append(transfer_directory.as_posix())
    invocation_completion_unknown = helper_entry_dispatched and not helper_entry_completed
    if invocation_creation.possibly_created:
        if invocation_completion_unknown:
            # The archive passed root ownership checks before dispatch. An
            # unreturned command may still be reading it, so neither the
            # exact file nor its private directory is safe to remove.
            residue_paths.extend((installed_helper.as_posix(), invocation_directory.as_posix()))
        elif not _cleanup_invocation(remote, invocation_directory, installed_helper):
            residue_paths.append(invocation_directory.as_posix())
    residue_paths = _unique_residue_paths(residue_paths)

    if primary_error is not None:
        if residue_paths:
            primary_error.residue_paths = tuple(residue_paths)
        if invocation_completion_unknown:
            _retain_uncertain_invocation_artifact(primary_error)
        raise primary_error

    if invocation is None:
        raise _safety_error("transient helper invocation failed")
    if residue_paths:
        return HelperInvocation(
            result=invocation.result,
            cleanup_warning=_cleanup_warning(residue_paths),
        )
    return invocation


def _retain_uncertain_invocation_artifact(error: OpsError) -> None:
    """Add stable recovery evidence without replacing an invocation failure."""

    previous_recovery = tuple(getattr(error, "recovery_commands", ()))
    previous_warnings = tuple(getattr(error, "warnings", ()))
    error.recovery_commands = tuple(
        dict.fromkeys(
            (*previous_recovery, "inspect the transient helper invocation artifact before retrying")
        )
    )
    error.warnings = tuple(
        dict.fromkeys(
            (
                *previous_warnings,
                "helper invocation completion is uncertain; installed helper artifact was retained",
            )
        )
    )


def _validate_inputs(package: HelperPackage, request: HostRequest) -> None:
    if not isinstance(package, HelperPackage) or not isinstance(request, HostRequest):
        raise TypeError("helper invocation requires a package and request")
    if package.protocol_version != request.protocol_version:
        raise _safety_error("helper package protocol does not match its request")
    if _SHA256_RE.fullmatch(package.sha256) is None:
        raise _safety_error("helper package verification failed")
    if package.path.is_symlink() or not package.path.is_file():
        raise _safety_error("helper package verification failed")
    try:
        digest = _local_sha256(package.path)
    except OSError:
        raise _safety_error("helper package verification failed") from None
    if not secrets.compare_digest(digest, package.sha256):
        raise _safety_error("helper package verification failed")


def _local_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _administrator_identity(remote: Remote) -> tuple[str, str]:
    uid = _decimal_output(
        _require_success(
            remote,
            ("id", "-u"),
            sudo=False,
            stdout_limit=_IDENTITY_LIMIT,
            stderr_limit=_CONTROL_STDERR_LIMIT,
        ).stdout
    )
    gid = _decimal_output(
        _require_success(
            remote,
            ("id", "-g"),
            sudo=False,
            stdout_limit=_IDENTITY_LIMIT,
            stderr_limit=_CONTROL_STDERR_LIMIT,
        ).stdout
    )
    return uid, gid


def _safe_identity(remote: Remote) -> tuple[str, str] | None:
    try:
        return _administrator_identity(remote)
    except Exception:
        return None


def _ensure_directory(
    remote: Remote,
    path: PurePosixPath,
    *,
    owner: tuple[str, str],
    sudo: bool,
) -> None:
    created = _run(
        remote,
        ("mkdir", "-m", _TRANSFER_DIRECTORY_MODE, "--", path.as_posix()),
        sudo=sudo,
        sensitive=True,
    )
    if not _succeeded(created):
        _assert_metadata(
            remote,
            path,
            owner=owner,
            mode=_TRANSFER_DIRECTORY_MODE,
            file_type="directory",
            sudo=sudo,
        )
        return
    _assert_metadata(
        remote,
        path,
        owner=owner,
        mode=_TRANSFER_DIRECTORY_MODE,
        file_type="directory",
        sudo=sudo,
    )


def _create_new_directory(
    remote: Remote,
    path: PurePosixPath,
    *,
    owner: tuple[str, str],
    sudo: bool,
    creation: _DirectoryCreation,
) -> None:
    creation.possibly_created = True
    created = _run(
        remote,
        ("mkdir", "-m", _TRANSFER_DIRECTORY_MODE, "--", path.as_posix()),
        sudo=sudo,
        sensitive=True,
    )
    if not _succeeded(created):
        # A definite nonzero mkdir result is a clean refusal: mkdir could not
        # have created its leaf while reporting failure.
        creation.possibly_created = False
        raise _safety_error("host helper invocation failed")
    _assert_metadata(
        remote,
        path,
        owner=owner,
        mode=_TRANSFER_DIRECTORY_MODE,
        file_type="directory",
        sudo=sudo,
    )
    creation.authority_proven = True


def _assert_checksum(remote: Remote, path: PurePosixPath, expected: str, *, sudo: bool) -> None:
    output = _require_success(
        remote,
        ("sha256sum", "--", path.as_posix()),
        sudo=sudo,
        stdout_limit=_CHECKSUM_LIMIT,
        stderr_limit=_CONTROL_STDERR_LIMIT,
    ).stdout
    actual = f"{expected}  {path.as_posix()}"
    payload = _control_payload(output)
    if payload is None or not secrets.compare_digest(payload, actual):
        raise _safety_error("helper checksum verification failed")


def _assert_metadata(
    remote: Remote,
    path: PurePosixPath,
    *,
    owner: tuple[str, str],
    mode: str,
    file_type: str,
    sudo: bool,
) -> None:
    output = _require_success(
        remote,
        ("stat", "-c", "%u:%g:%a:%F", "--", path.as_posix()),
        sudo=sudo,
        stdout_limit=_METADATA_LIMIT,
        stderr_limit=_CONTROL_STDERR_LIMIT,
    ).stdout
    expected = f"{owner[0]}:{owner[1]}:{mode}:{file_type}"
    payload = _control_payload(output)
    if payload is None or not secrets.compare_digest(payload, expected):
        raise _safety_error("helper path authority verification failed")


def _cleanup_transfer(
    remote: Remote,
    directory: PurePosixPath,
    path: PurePosixPath,
    owner: tuple[str, str] | None,
) -> bool:
    if owner is None:
        return False
    try:
        _assert_metadata(
            remote,
            directory,
            owner=owner,
            mode=_TRANSFER_DIRECTORY_MODE,
            file_type="directory",
            sudo=False,
        )
        try:
            _assert_metadata(
                remote,
                path,
                owner=owner,
                mode=_TRANSFER_FILE_MODE,
                file_type="regular file",
                sudo=False,
            )
        except OpsError:
            _require_success(
                remote,
                ("rmdir", "--", directory.as_posix()),
                sudo=False,
                sensitive=True,
            )
            return True
        _require_success(
            remote,
            ("rm", "--", path.as_posix()),
            sudo=False,
            sensitive=True,
        )
        _require_success(
            remote,
            ("rmdir", "--", directory.as_posix()),
            sudo=False,
            sensitive=True,
        )
    except Exception:
        return False
    return True


def _cleanup_invocation(
    remote: Remote,
    directory: PurePosixPath,
    path: PurePosixPath,
) -> bool:
    try:
        _assert_metadata(
            remote,
            directory,
            owner=("0", "0"),
            mode=_INVOCATION_DIRECTORY_MODE,
            file_type="directory",
            sudo=True,
        )
        try:
            _assert_metadata(
                remote,
                path,
                owner=("0", "0"),
                mode=_INSTALLED_HELPER_MODE,
                file_type="regular file",
                sudo=True,
            )
        except OpsError:
            # ``rmdir`` cannot follow a child or remove a non-empty directory,
            # so it is the only exact cleanup safe when installation failed
            # before a root-owned helper file can be proven.
            _require_success(
                remote,
                ("rmdir", "--", directory.as_posix()),
                sudo=True,
                sensitive=True,
            )
            return True
        _require_success(
            remote,
            ("rm", "--", path.as_posix()),
            sudo=True,
            sensitive=True,
        )
        _require_success(
            remote,
            ("rmdir", "--", directory.as_posix()),
            sudo=True,
            sensitive=True,
        )
    except Exception:
        return False
    return True


def _decode_helper_result(completed: CommandResult) -> HostResult:
    if not isinstance(completed.stdout, str) or not isinstance(completed.stderr, str):
        raise _safety_error("host helper returned invalid result")
    try:
        stdout = completed.stdout.encode("utf-8", "strict")
        stderr_size = len(completed.stderr.encode("utf-8", "strict"))
    except UnicodeEncodeError:
        raise _safety_error("host helper returned invalid result") from None
    if len(stdout) > MAX_OUTPUT_BYTES or stderr_size > MAX_STDERR_BYTES:
        raise _safety_error("host helper returned invalid result")
    try:
        return decode_result(stdout)
    except Exception:
        raise _safety_error("host helper returned invalid result") from None


def _validate_result_correlation(
    result: HostResult,
    package: HelperPackage,
    request: HostRequest,
) -> None:
    if (
        result.protocol_version != request.protocol_version
        or result.protocol_version != package.protocol_version
        or result.operation != request.operation
        or result.operation_id != request.operation_id
    ):
        raise _safety_error("host helper result does not match its request")


def _run(
    remote: Remote,
    argv: Sequence[str],
    *,
    sudo: bool,
    stdin: bytes | None = None,
    sensitive: bool = False,
    stdout_limit: int | None = None,
    stderr_limit: int | None = None,
    timeout: int = _CONTROL_TIMEOUT_SECONDS,
) -> CommandResult:
    effective_stdout_limit = _CONTROL_STDOUT_LIMIT if stdout_limit is None else stdout_limit
    effective_stderr_limit = _CONTROL_STDERR_LIMIT if stderr_limit is None else stderr_limit
    try:
        result = remote.run(
            argv,
            sudo=sudo,
            stdin=stdin,
            sensitive=sensitive,
            timeout=timeout,
            stdout_limit=effective_stdout_limit,
            stderr_limit=effective_stderr_limit,
        )
    except OpsError:
        raise
    except Exception:
        raise _safety_error("helper transport command failed") from None
    if not isinstance(result, CommandResult):
        raise _safety_error("helper transport command failed")
    return result


def _require_success(
    remote: Remote,
    argv: Sequence[str],
    *,
    sudo: bool,
    stdin: bytes | None = None,
    sensitive: bool = False,
    stdout_limit: int | None = None,
    stderr_limit: int | None = None,
    timeout: int = _CONTROL_TIMEOUT_SECONDS,
) -> CommandResult:
    result = _run(
        remote,
        argv,
        sudo=sudo,
        stdin=stdin,
        sensitive=sensitive,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
        timeout=timeout,
    )
    if not _succeeded(result):
        raise _safety_error("host helper invocation failed")
    return result


def _succeeded(result: CommandResult) -> bool:
    return type(result.returncode) is int and result.returncode == 0


def _decimal_output(value: object) -> str:
    payload = _control_payload(value)
    if payload is None or _DECIMAL_RE.fullmatch(payload) is None:
        raise _safety_error("helper administrator identity verification failed")
    return payload


def _control_payload(value: object) -> str | None:
    """Remove exactly one required ASCII line terminator from a control command."""

    if type(value) is not str or not value.endswith("\n"):
        return None
    return value[:-1]


def _cleanup_warning(residue_paths: Sequence[str]) -> str:
    return (
        "transient helper cleanup may have left "
        + ", ".join(residue_paths)
        + "; inspect each exact path and remove it only after verifying root ownership, mode, and type"
    )


def _upload_residue_paths(receipt: object) -> tuple[str, ...]:
    if not isinstance(receipt, UploadReceipt):
        raise _safety_error("helper transfer receipt is invalid")
    return _checked_residue_paths(receipt.residue_paths)


def _error_residue_paths(error: OpsError) -> tuple[str, ...]:
    return _checked_residue_paths(getattr(error, "residue_paths", ()))


def _checked_residue_paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(
        type(path) is not str or not path.startswith("/") for path in value
    ):
        raise _safety_error("helper transfer residue evidence is invalid")
    return value


def _unique_residue_paths(paths: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(paths))


def _safety_error(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "helper",
        message,
        changed=False,
        next_action="inspect the transient helper transfer and invocation paths before retrying",
    )


__all__ = ["HelperInvocation", "MAX_STDERR_BYTES", "invoke_helper", "new_operation_id"]
