"""Safe compatibility convergence for the persistent scheduled-backup executable."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import time

from .commands import CommandError, run_command
from .lock import LifecycleLock, LifecycleLockContention, acquire_lifecycle_lock
from .paths import ManagedPaths, PathAuthorityError


_BACKUP_COMMAND = Path("/usr/local/lib/taskman/taskman-backup.pyz")
_BACKUP_TIMER = "taskman-backup.timer"
_BACKUP_SERVICE = "taskman-backup.service"


@dataclass(frozen=True)
class BackupHelperMutation:
    """Known-or-possible managed scheduler/package mutations from this convergence."""

    paused: bool = False
    replaced: bool = False
    restarted: bool = False


@dataclass(frozen=True)
class BackupHelperConvergence:
    """Successful convergence and the still-held lifecycle lock."""

    lock: LifecycleLock
    mutation: BackupHelperMutation


class BackupHelperError(RuntimeError):
    """Refusal or failure retaining every known-or-possible managed mutation."""

    def __init__(
        self,
        message: str,
        mutation: BackupHelperMutation,
        *,
        restoration_failed: bool = False,
        restoration_uncertain: bool = False,
        restoration_cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.mutation = mutation
        self.restoration_failed = restoration_failed
        self.restoration_uncertain = restoration_uncertain
        self.restoration_cause = restoration_cause


def observe_backup_timer(*, timeout_seconds: float = 60.0) -> tuple[bool, str]:
    """Read the persistent enablement and active state of the managed timer."""

    enabled = _systemd_property("UnitFileState", timeout_seconds=timeout_seconds)
    active = _systemd_property("ActiveState", timeout_seconds=timeout_seconds)
    if enabled not in {"enabled", "disabled"} or active not in {"active", "inactive"}:
        raise BackupHelperError("backup timer state is ambiguous", BackupHelperMutation())
    return enabled == "enabled", active


def stop_backup_timer(*, timeout_seconds: float = 60.0) -> None:
    run_command(("systemctl", "stop", _BACKUP_TIMER), timeout_seconds=timeout_seconds)


def start_backup_timer(*, timeout_seconds: float = 60.0) -> None:
    run_command(("systemctl", "start", _BACKUP_TIMER), timeout_seconds=timeout_seconds)


def converge_backup_helper(
    paths: ManagedPaths,
    backup_helper: Mapping[str, object],
    *,
    confirmed_checksum: str | None,
    confirmed_enabled: bool,
    revalidate: Callable[[], None],
    timeout_seconds: float,
    lock: LifecycleLock | None = None,
) -> BackupHelperConvergence:
    """Pause, quiesce, replace, and restore the compatible executable.

    The returned lock is deliberately not context-managed here: the caller owns
    the acquisition that protects its subsequent deployment consequences.
    """

    desired_sha256, upload = _input(paths, backup_helper, confirmed_checksum, confirmed_enabled, revalidate, timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    mutation = BackupHelperMutation()
    if lock is not None and not lock.held:
        raise ValueError("backup helper requires a held lifecycle lock")
    owned_lock = lock is None
    completed = False
    try:
        if lock is None:
            lock = acquire_lifecycle_lock(paths, _remaining(deadline))
        timer_enabled, timer_state = observe_backup_timer(timeout_seconds=_remaining(deadline))
        _ensure_confirmed_timer(timer_enabled, timer_state, confirmed_enabled, mutation)
        installed = _verified_executable_checksum()
        if installed != confirmed_checksum:
            raise BackupHelperError("scheduled backup executable identity changed", mutation)
        if installed == desired_sha256:
            if timer_enabled and timer_state == "inactive":
                mutation = _with_mutation(mutation, restarted=True)
                start_backup_timer(timeout_seconds=_remaining(deadline))
            completed = True
            return BackupHelperConvergence(lock, mutation)

        _validate_upload(paths, upload, desired_sha256)
        mutation = _with_mutation(mutation, paused=True)
        stop_backup_timer(timeout_seconds=_remaining(deadline))
        lock.release()
        _wait_for_backup_service(_remaining(deadline))
        lock.reacquire(paths, _remaining(deadline))
        revalidate()
        enabled_after_wait, state_after_wait = observe_backup_timer(timeout_seconds=_remaining(deadline))
        _ensure_confirmed_timer(enabled_after_wait, state_after_wait, confirmed_enabled, mutation)
        mutation = _with_mutation(mutation, replaced=True)
        _replace_executable(upload, desired_sha256)
        if enabled_after_wait:
            mutation = _with_mutation(mutation, restarted=True)
            start_backup_timer(timeout_seconds=_remaining(deadline))
        completed = True
        return BackupHelperConvergence(lock, mutation)
    except Exception as error:
        mutation, restoration_failed, restoration_uncertain, restoration_cause = _restore_enabled_timer(
            mutation,
            confirmed_checksum,
            desired_sha256,
            deadline,
        )
        message = str(error) if isinstance(error, BackupHelperError) else "scheduled backup helper did not converge"
        raise BackupHelperError(
            message,
            mutation,
            restoration_failed=restoration_failed,
            restoration_uncertain=restoration_uncertain,
            restoration_cause=restoration_cause,
        ) from error
    finally:
        if owned_lock and not completed and lock is not None:
            lock.release()


def _input(
    paths: ManagedPaths,
    value: Mapping[str, object],
    confirmed_checksum: str | None,
    confirmed_enabled: bool,
    revalidate: Callable[[], None],
    timeout_seconds: float,
) -> tuple[str, Path | None]:
    if not isinstance(paths, ManagedPaths) or not isinstance(value, Mapping) or set(value) != {"sha256", "upload_path"}:
        raise ValueError("backup helper input is invalid")
    digest = value["sha256"]
    upload = value["upload_path"]
    if type(digest) is not str or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("backup helper checksum is invalid")
    if confirmed_checksum is not None and (type(confirmed_checksum) is not str or len(confirmed_checksum) != 64):
        raise ValueError("confirmed backup checksum is invalid")
    if (
        type(confirmed_enabled) is not bool
        or not callable(revalidate)
        or type(timeout_seconds) not in {int, float}
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("backup helper convergence input is invalid")
    if upload is None:
        return digest, None
    if type(upload) is not str:
        raise ValueError("backup helper upload is invalid")
    path = PurePosixPath(upload)
    allowed = paths.deployment_root / "uploads"
    try:
        relative = path.relative_to(allowed)
    except ValueError as error:
        raise ValueError("backup helper upload is outside derived authority") from error
    if (
        not path.is_absolute()
        or path.as_posix() != upload
        or len(relative.parts) != 1
        or path.suffix != ".pyz"
        or path.name in {".", ".."}
    ):
        raise ValueError("backup helper upload is invalid")
    return digest, Path(path.as_posix())


def _ensure_confirmed_timer(enabled: bool, state: str, confirmed_enabled: bool, mutation: BackupHelperMutation) -> None:
    if enabled != confirmed_enabled:
        raise BackupHelperError("backup timer enablement changed", mutation)
    if not enabled and state == "active":
        raise BackupHelperError("backup timer is active while disabled", mutation)


def _systemd_property(name: str, *, timeout_seconds: float) -> str:
    result = run_command(("systemctl", "show", f"--property={name}", "--value", _BACKUP_TIMER), timeout_seconds=timeout_seconds, output_limit=64)
    return result.stdout.decode("ascii", "strict").strip()


def _wait_for_backup_service(timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        result = run_command(("systemctl", "show", "--property=ActiveState", "--value", _BACKUP_SERVICE), timeout_seconds=max(0.001, deadline - time.monotonic()), output_limit=64)
        if result.stdout.decode("ascii", "strict").strip() == "inactive":
            return
        if time.monotonic() >= deadline:
            raise CommandError("scheduled backup did not quiesce")
        time.sleep(min(0.05, deadline - time.monotonic()))


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CommandError("scheduled backup helper timed out")
    return remaining


def _with_mutation(
    mutation: BackupHelperMutation,
    *,
    paused: bool = False,
    replaced: bool = False,
    restarted: bool = False,
) -> BackupHelperMutation:
    return BackupHelperMutation(
        paused=mutation.paused or paused,
        replaced=mutation.replaced or replaced,
        restarted=mutation.restarted or restarted,
    )


def _restore_enabled_timer(
    mutation: BackupHelperMutation,
    confirmed_checksum: str | None,
    desired_sha256: str,
    deadline: float,
) -> tuple[BackupHelperMutation, bool, bool, Exception | None]:
    if not mutation.paused:
        return mutation, False, False, None
    try:
        enabled, state = observe_backup_timer(timeout_seconds=_remaining(deadline))
    except Exception as error:
        return mutation, False, True, error
    if not enabled or state == "active":
        return mutation, False, False, None
    try:
        if _verified_executable_checksum() not in {confirmed_checksum, desired_sha256}:
            return mutation, True, False, None
        mutation = _with_mutation(mutation, restarted=True)
        start_backup_timer(timeout_seconds=_remaining(deadline))
    except Exception as error:
        return mutation, True, False, error
    return mutation, False, False, None


def _validate_upload(paths: ManagedPaths, upload: Path | None, digest: str) -> None:
    if upload is None:
        raise ValueError("backup helper upload is required")
    paths.validate_existing(owner_uid=os.geteuid())
    _read_private_upload(upload, digest)


def _verified_executable_checksum() -> str | None:
    try:
        directory = _open_safe_directory(_BACKUP_COMMAND.parent, "backup destination")
    except FileNotFoundError:
        return None
    try:
        try:
            descriptor = os.open(
                _BACKUP_COMMAND.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return None
        try:
            return _digest_private_descriptor(descriptor, mode=0o750, label="scheduled backup executable")
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _replace_executable(upload: Path | None, digest: str) -> None:
    if upload is None:
        raise ValueError("backup helper upload is required")
    payload = _read_private_upload(upload, digest)
    try:
        directory = _open_safe_directory(_BACKUP_COMMAND.parent, "backup destination")
    except FileNotFoundError as error:
        raise ValueError("backup destination is unavailable") from error
    temporary = f".taskman-backup-{secrets.token_hex(16)}"
    try:
        _ensure_safe_destination(directory)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o750,
            dir_fd=directory,
        )
        try:
            _write_all(descriptor, payload)
            os.fchmod(descriptor, 0o750)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, _BACKUP_COMMAND.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        finally:
            os.close(directory)
    if _verified_executable_checksum() != digest:
        raise ValueError("scheduled backup executable checksum is invalid")


def _open_safe_directory(path: Path, label: str) -> int:
    if not path.is_absolute():
        raise ValueError(f"{label} is unsafe")
    try:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise ValueError(f"{label} is unsafe") from error
    try:
        components = path.parts[1:]
        for index, component in enumerate(components):
            try:
                next_descriptor = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except OSError as error:
                raise ValueError(f"{label} is unsafe") from error
            try:
                details = os.fstat(next_descriptor)
                _require_directory(details, label)
                if index == len(components) - 1:
                    _require_safe_directory(details, label)
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _require_safe_directory(details: os.stat_result, label: str) -> None:
    if (
        details.st_uid != os.geteuid()
        or details.st_mode & 0o7022
    ):
        raise ValueError(f"{label} is unsafe")


def _require_directory(details: os.stat_result, label: str) -> None:
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError(f"{label} is unsafe")


def _read_private_upload(upload: Path, digest: str) -> bytes:
    directory = _open_safe_directory(upload.parent, "backup helper upload directory")
    try:
        descriptor = os.open(upload.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            return _read_and_verify_descriptor(descriptor, digest, mode=0o600, label="backup helper upload")
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _read_and_verify_descriptor(descriptor: int, digest: str | None, *, mode: int, label: str) -> bytes:
    initial = os.fstat(descriptor)
    _require_private_regular(initial, mode=mode, label=label)
    payload = bytearray()
    while chunk := os.read(descriptor, 1024 * 1024):
        payload.extend(chunk)
    final = os.fstat(descriptor)
    _require_private_regular(final, mode=mode, label=label)
    if (initial.st_dev, initial.st_ino, initial.st_size) != (final.st_dev, final.st_ino, final.st_size):
        raise ValueError(f"{label} identity changed")
    if digest is not None and hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError(f"{label} checksum is invalid")
    return bytes(payload)


def _digest_private_descriptor(descriptor: int, *, mode: int, label: str) -> str:
    payload = _read_and_verify_descriptor(descriptor, None, mode=mode, label=label)
    return hashlib.sha256(payload).hexdigest()


def _require_private_regular(details: os.stat_result, *, mode: int, label: str) -> None:
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != mode
    ):
        raise ValueError(f"{label} is unsafe")


def _ensure_safe_destination(directory: int) -> None:
    try:
        details = os.stat(_BACKUP_COMMAND.name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    _require_private_regular(details, mode=0o750, label="backup destination")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("unable to write backup destination")
        view = view[written:]


__all__ = ["BackupHelperConvergence", "BackupHelperError", "BackupHelperMutation", "converge_backup_helper"]
