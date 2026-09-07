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
from .host_protocol import MAX_OUTPUT_BYTES, HostRequest, HostResult, decode_result, encode_request
from .host_protocol.identifiers import validate_correlation_id
from .remote import CommandResult, Remote, UploadReceipt


_TRANSFER_ROOT = PurePosixPath("/tmp/taskman-ops")
_INVOCATION_ROOT = PurePosixPath("/run/taskman-ops")
_HELPER_NAME = "taskman-host.pyz"
_CONTROL_TIMEOUT_SECONDS = 60
_OPERATION_TIMEOUT_SECONDS = 660
_CONTROL_STDOUT_LIMIT = 1024
_CONTROL_STDERR_LIMIT = 4096
MAX_STDERR_BYTES = 16 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL_RE = re.compile(r"[0-9]+\Z")


@dataclass(frozen=True)
class HelperInvocation:
    """The final helper result plus at most one transport cleanup warning."""

    result: HostResult
    cleanup_warning: str | None = None


def new_correlation_id() -> str:
    """Create one random transport-only correlation identifier."""

    return validate_correlation_id(f"op-{secrets.token_hex(16)}")


def invoke_helper(remote: Remote, package: HelperPackage, request: HostRequest) -> HelperInvocation:
    """Transfer, verify, execute, decode, and best-effort remove one helper.

    The runner owns only strict SSH transport and archive authority.  It never
    turns host lifecycle details, private paths, or recovery programs into a
    controller result.
    """

    _validate_inputs(package, request)
    correlation = request.correlation_id
    transfer_directory = _TRANSFER_ROOT / correlation
    transfer_path = transfer_directory / _HELPER_NAME
    invocation_directory = _INVOCATION_ROOT / correlation
    installed_path = invocation_directory / _HELPER_NAME
    transfer_created = False
    invocation_created = False
    dispatched = False
    completed = False
    cleanup_needed = False
    result: HostResult | None = None

    try:
        administrator = _administrator_identity(remote)
        _ensure_directory(remote, _TRANSFER_ROOT, administrator, sudo=False)
        _create_directory(remote, transfer_directory, administrator, sudo=False)
        transfer_created = True
        receipt = remote.put(
            package.path, transfer_path, mode=0o600, sensitive=True,
            timeout=_CONTROL_TIMEOUT_SECONDS, sudo=False,
        )
        if not isinstance(receipt, UploadReceipt):
            raise _safety_error("helper transfer receipt is invalid")
        cleanup_needed = receipt.cleanup_warning
        _assert_metadata(remote, transfer_path, administrator, "600", "regular file", sudo=False)
        _assert_checksum(remote, transfer_path, package.sha256, sudo=False)

        _ensure_directory(remote, _INVOCATION_ROOT, ("0", "0"), sudo=True)
        _create_directory(remote, invocation_directory, ("0", "0"), sudo=True)
        invocation_created = True
        _require_success(
            remote,
            ("install", "-o", "root", "-g", "root", "-m", "500", "--", transfer_path.as_posix(), installed_path.as_posix()),
            sudo=True,
            sensitive=True,
        )
        _assert_metadata(remote, installed_path, ("0", "0"), "500", "regular file", sudo=True)
        _assert_checksum(remote, installed_path, package.sha256, sudo=True)

        if not _remove_transfer(remote, transfer_directory, transfer_path, administrator):
            cleanup_needed = True
        else:
            transfer_created = False

        dispatched = True
        command = _run(
            remote,
            ("sudo", "--preserve-env=SSH_CONNECTION", "--", "python3", installed_path.as_posix()),
            sudo=False,
            stdin=encode_request(request),
            stdout_limit=MAX_OUTPUT_BYTES,
            stderr_limit=MAX_STDERR_BYTES,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )
        if not _succeeded(command):
            raise _safety_error("host helper invocation failed")
        result = _decode_result(command)
        _validate_correlation(result, package, request)
        completed = True
    except OpsError as error:
        cleanup_needed = _best_effort_cleanup(
            remote, transfer_directory, transfer_path, invocation_directory, installed_path,
            transfer_created, invocation_created,
        ) or cleanup_needed or (dispatched and not completed)
        error.helper_entry_dispatched = dispatched  # type: ignore[attr-defined]
        if cleanup_needed:
            error.warnings = ("transient helper cleanup was incomplete",)
        raise
    except Exception:
        error = _safety_error("transient helper invocation failed")
        cleanup_needed = _best_effort_cleanup(
            remote, transfer_directory, transfer_path, invocation_directory, installed_path,
            transfer_created, invocation_created,
        ) or cleanup_needed or (dispatched and not completed)
        error.helper_entry_dispatched = dispatched  # type: ignore[attr-defined]
        if cleanup_needed:
            error.warnings = ("transient helper cleanup was incomplete",)
        raise error from None

    cleanup_needed = _best_effort_cleanup(
        remote, transfer_directory, transfer_path, invocation_directory, installed_path,
        transfer_created, invocation_created,
    ) or cleanup_needed
    if result is None:
        raise _safety_error("transient helper invocation failed")
    return HelperInvocation(
        result=result,
        cleanup_warning="transient helper cleanup was incomplete" if cleanup_needed else None,
    )


def _validate_inputs(package: HelperPackage, request: HostRequest) -> None:
    if not isinstance(package, HelperPackage) or not isinstance(request, HostRequest):
        raise TypeError("helper invocation requires a package and request")
    if package.protocol_version != request.protocol_version or _SHA256_RE.fullmatch(package.sha256) is None:
        raise _safety_error("helper package verification failed")
    if package.path.is_symlink() or not package.path.is_file():
        raise _safety_error("helper package verification failed")
    try:
        digest = hashlib.sha256(package.path.read_bytes()).hexdigest()
    except OSError:
        raise _safety_error("helper package verification failed") from None
    if not secrets.compare_digest(digest, package.sha256):
        raise _safety_error("helper package verification failed")


def _administrator_identity(remote: Remote) -> tuple[str, str]:
    return (
        _decimal(_require_success(remote, ("id", "-u"), sudo=False).stdout),
        _decimal(_require_success(remote, ("id", "-g"), sudo=False).stdout),
    )


def _ensure_directory(remote: Remote, path: PurePosixPath, owner: tuple[str, str], *, sudo: bool) -> None:
    result = _run(remote, ("mkdir", "-m", "700", "--", path.as_posix()), sudo=sudo, sensitive=True)
    if not _succeeded(result):
        _assert_metadata(remote, path, owner, "700", "directory", sudo=sudo)


def _create_directory(remote: Remote, path: PurePosixPath, owner: tuple[str, str], *, sudo: bool) -> None:
    _require_success(remote, ("mkdir", "-m", "700", "--", path.as_posix()), sudo=sudo, sensitive=True)
    _assert_metadata(remote, path, owner, "700", "directory", sudo=sudo)


def _assert_metadata(
    remote: Remote, path: PurePosixPath, owner: tuple[str, str], mode: str, file_type: str, *, sudo: bool
) -> None:
    actual = _control(_require_success(remote, ("stat", "-c", "%u:%g:%a:%F", "--", path.as_posix()), sudo=sudo).stdout)
    if actual != f"{owner[0]}:{owner[1]}:{mode}:{file_type}":
        raise _safety_error("helper path authority verification failed")


def _assert_checksum(remote: Remote, path: PurePosixPath, expected: str, *, sudo: bool) -> None:
    actual = _control(_require_success(remote, ("sha256sum", "--", path.as_posix()), sudo=sudo).stdout)
    if actual != f"{expected}  {path.as_posix()}":
        raise _safety_error("helper checksum verification failed")


def _remove_transfer(remote: Remote, directory: PurePosixPath, path: PurePosixPath, owner: tuple[str, str]) -> bool:
    try:
        _assert_metadata(remote, directory, owner, "700", "directory", sudo=False)
        _assert_metadata(remote, path, owner, "600", "regular file", sudo=False)
        _require_success(remote, ("rm", "--", path.as_posix()), sudo=False, sensitive=True)
        _require_success(remote, ("rmdir", "--", directory.as_posix()), sudo=False, sensitive=True)
        return True
    except Exception:
        return False


def _best_effort_cleanup(
    remote: Remote,
    transfer_directory: PurePosixPath,
    transfer_path: PurePosixPath,
    invocation_directory: PurePosixPath,
    installed_path: PurePosixPath,
    transfer_created: bool,
    invocation_created: bool,
) -> bool:
    incomplete = False
    if invocation_created:
        incomplete = not _remove_invocation(remote, invocation_directory, installed_path) or incomplete
    if transfer_created:
        # We cannot prove the administrator identity after a transport fault;
        # ``rm``/``rmdir`` is still restricted to the derived private path.
        incomplete = not _remove_plain(remote, transfer_directory, transfer_path, sudo=False) or incomplete
    return incomplete


def _remove_invocation(remote: Remote, directory: PurePosixPath, path: PurePosixPath) -> bool:
    return _remove_plain(remote, directory, path, sudo=True)


def _remove_plain(remote: Remote, directory: PurePosixPath, path: PurePosixPath, *, sudo: bool) -> bool:
    try:
        removed = _run(remote, ("rm", "--", path.as_posix()), sudo=sudo, sensitive=True)
        directory_removed = _run(remote, ("rmdir", "--", directory.as_posix()), sudo=sudo, sensitive=True)
        return _succeeded(removed) and _succeeded(directory_removed)
    except Exception:
        return False


def _decode_result(command: CommandResult) -> HostResult:
    if not isinstance(command.stdout, str) or not isinstance(command.stderr, str):
        raise _safety_error("host helper returned invalid result")
    try:
        stdout = command.stdout.encode("utf-8", "strict")
        stderr_size = len(command.stderr.encode("utf-8", "strict"))
    except UnicodeEncodeError:
        raise _safety_error("host helper returned invalid result") from None
    if len(stdout) > MAX_OUTPUT_BYTES or stderr_size > MAX_STDERR_BYTES:
        raise _safety_error("host helper returned invalid result")
    try:
        return decode_result(stdout)
    except Exception:
        raise _safety_error("host helper returned invalid result") from None


def _validate_correlation(result: HostResult, package: HelperPackage, request: HostRequest) -> None:
    if (
        result.protocol_version != request.protocol_version
        or result.protocol_version != package.protocol_version
        or result.operation != request.operation
        or result.correlation_id != request.correlation_id
    ):
        raise _safety_error("host helper result does not match its request")


def _run(
    remote: Remote, argv: Sequence[str], *, sudo: bool, stdin: bytes | None = None,
    sensitive: bool = False, stdout_limit: int = _CONTROL_STDOUT_LIMIT,
    stderr_limit: int = _CONTROL_STDERR_LIMIT, timeout: int = _CONTROL_TIMEOUT_SECONDS,
) -> CommandResult:
    try:
        result = remote.run(argv, sudo=sudo, stdin=stdin, sensitive=sensitive, timeout=timeout,
                            stdout_limit=stdout_limit, stderr_limit=stderr_limit)
    except OpsError:
        raise
    except Exception:
        raise _safety_error("helper transport command failed") from None
    if not isinstance(result, CommandResult):
        raise _safety_error("helper transport command failed")
    return result


def _require_success(remote: Remote, argv: Sequence[str], *, sudo: bool, sensitive: bool = False) -> CommandResult:
    result = _run(remote, argv, sudo=sudo, sensitive=sensitive)
    if not _succeeded(result):
        raise _safety_error("host helper invocation failed")
    return result


def _succeeded(result: CommandResult) -> bool:
    return type(result.returncode) is int and result.returncode == 0


def _control(value: object) -> str:
    if type(value) is not str or not value.endswith("\n"):
        raise _safety_error("helper control command returned invalid output")
    return value[:-1]


def _decimal(value: object) -> str:
    result = _control(value)
    if _DECIMAL_RE.fullmatch(result) is None:
        raise _safety_error("helper administrator identity verification failed")
    return result


def _safety_error(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "helper",
        message,
        changed=False,
        next_action="inspect the transient helper transfer and invocation paths before retrying",
    )


__all__ = ["HelperInvocation", "MAX_STDERR_BYTES", "invoke_helper", "new_correlation_id"]
