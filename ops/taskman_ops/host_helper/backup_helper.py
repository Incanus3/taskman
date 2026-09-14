"""Safe compatibility convergence for the persistent scheduled-backup executable."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

from .commands import CommandError, run_command
from .filesystem import fsync_directory
from .lock import LifecycleLock, LifecycleLockContention, acquire_lifecycle_lock
from .paths import ManagedPaths, PathAuthorityError


_BACKUP_COMMAND = Path("/usr/local/lib/taskman/taskman-backup.pyz")
_BACKUP_TIMER = "taskman-backup.timer"
_BACKUP_SERVICE = "taskman-backup.service"


@dataclass(frozen=True)
class BackupHelperMutation:
    """Known managed scheduler/package mutations from this convergence."""

    paused: bool = False
    replaced: bool = False
    restarted: bool = False


@dataclass(frozen=True)
class BackupHelperConvergence:
    """Successful convergence and the still-held lifecycle lock."""

    lock: LifecycleLock
    mutation: BackupHelperMutation


class BackupHelperError(RuntimeError):
    """Refusal or failure retaining every known managed mutation."""

    def __init__(self, message: str, mutation: BackupHelperMutation, *, restoration_failed: bool = False) -> None:
        super().__init__(message)
        self.mutation = mutation
        self.restoration_failed = restoration_failed


def observe_backup_timer() -> tuple[bool, str]:
    """Read the persistent enablement and active state of the managed timer."""

    enabled = _systemd_property("UnitFileState")
    active = _systemd_property("ActiveState")
    if enabled not in {"enabled", "disabled"} or active not in {"active", "inactive"}:
        raise BackupHelperError("backup timer state is ambiguous", BackupHelperMutation())
    return enabled == "enabled", active


def stop_backup_timer() -> None:
    run_command(("systemctl", "stop", _BACKUP_TIMER), timeout_seconds=60.0)


def start_backup_timer() -> None:
    run_command(("systemctl", "start", _BACKUP_TIMER), timeout_seconds=60.0)


def converge_backup_helper(
    paths: ManagedPaths,
    backup_helper: Mapping[str, object],
    *,
    confirmed_checksum: str | None,
    confirmed_enabled: bool,
    revalidate: Callable[[], None],
    timeout_seconds: float,
) -> BackupHelperConvergence:
    """Pause, quiesce, replace, and restore the compatible executable.

    The returned lock is deliberately not context-managed here: the caller owns
    the acquisition that protects its subsequent deployment consequences.
    """

    desired_sha256, upload = _input(paths, backup_helper, confirmed_checksum, confirmed_enabled, revalidate, timeout_seconds)
    mutation = BackupHelperMutation()
    lock: LifecycleLock | None = None
    timer_was_enabled = False
    try:
        lock = acquire_lifecycle_lock(paths, timeout_seconds)
        revalidate()
        timer_was_enabled, timer_state = observe_backup_timer()
        _ensure_confirmed_timer(timer_was_enabled, timer_state, confirmed_enabled, mutation)
        installed = _verified_executable_checksum()
        if installed != confirmed_checksum:
            raise BackupHelperError("scheduled backup executable identity changed", mutation)
        if installed == desired_sha256:
            if timer_was_enabled and timer_state == "inactive":
                start_backup_timer()
                mutation = BackupHelperMutation(restarted=True)
            return BackupHelperConvergence(lock, mutation)

        _validate_upload(paths, upload, desired_sha256)
        stop_backup_timer()
        mutation = BackupHelperMutation(paused=True)
        lock.release()
        lock = None
        _wait_for_backup_service(timeout_seconds)
        lock = acquire_lifecycle_lock(paths, timeout_seconds)
        revalidate()
        enabled_after_wait, state_after_wait = observe_backup_timer()
        _ensure_confirmed_timer(enabled_after_wait, state_after_wait, confirmed_enabled, mutation)
        _replace_executable(upload, desired_sha256)
        mutation = BackupHelperMutation(paused=True, replaced=True)
        if enabled_after_wait:
            start_backup_timer()
            mutation = BackupHelperMutation(paused=True, replaced=True, restarted=True)
        return BackupHelperConvergence(lock, mutation)
    except (CommandError, LifecycleLockContention, OSError, PathAuthorityError, ValueError) as error:
        restoration_failed = False
        if timer_was_enabled and mutation.paused and _verified_executable_checksum() in {confirmed_checksum, desired_sha256}:
            try:
                start_backup_timer()
                mutation = BackupHelperMutation(mutation.paused, mutation.replaced, True)
            except (CommandError, OSError):
                restoration_failed = True
        if lock is not None:
            lock.release()
        raise BackupHelperError("scheduled backup helper did not converge", mutation, restoration_failed=restoration_failed) from error
    except BackupHelperError:
        if lock is not None:
            lock.release()
        raise


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
    if type(confirmed_enabled) is not bool or not callable(revalidate) or type(timeout_seconds) not in {int, float} or timeout_seconds <= 0:
        raise ValueError("backup helper convergence input is invalid")
    if upload is None:
        return digest, None
    if type(upload) is not str:
        raise ValueError("backup helper upload is invalid")
    path = Path(upload)
    allowed = Path(paths.local(paths.deployment_root / "uploads"))
    try:
        path.relative_to(allowed)
    except ValueError as error:
        raise ValueError("backup helper upload is outside derived authority") from error
    return digest, path


def _ensure_confirmed_timer(enabled: bool, state: str, confirmed_enabled: bool, mutation: BackupHelperMutation) -> None:
    if enabled != confirmed_enabled:
        raise BackupHelperError("backup timer enablement changed", mutation)
    if not enabled and state == "active":
        raise BackupHelperError("backup timer is active while disabled", mutation)


def _systemd_property(name: str) -> str:
    result = run_command(("systemctl", "show", f"--property={name}", "--value", _BACKUP_TIMER), timeout_seconds=60.0, output_limit=64)
    return result.stdout.decode("ascii", "strict").strip()


def _wait_for_backup_service(timeout_seconds: float) -> None:
    import time

    deadline = time.monotonic() + timeout_seconds
    while True:
        result = run_command(("systemctl", "show", "--property=ActiveState", "--value", _BACKUP_SERVICE), timeout_seconds=max(0.001, deadline - time.monotonic()), output_limit=64)
        if result.stdout.decode("ascii", "strict").strip() == "inactive":
            return
        if time.monotonic() >= deadline:
            raise CommandError("scheduled backup did not quiesce")
        time.sleep(min(0.05, deadline - time.monotonic()))


def _validate_upload(paths: ManagedPaths, upload: Path | None, digest: str) -> None:
    if upload is None:
        raise ValueError("backup helper upload is required")
    details = upload.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid():
        raise ValueError("backup helper upload is unsafe")
    if hashlib.sha256(upload.read_bytes()).hexdigest() != digest:
        raise ValueError("backup helper upload checksum is invalid")
    paths.validate_existing(owner_uid=os.geteuid())


def _verified_executable_checksum() -> str | None:
    try:
        details = _BACKUP_COMMAND.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o750:
        raise ValueError("scheduled backup executable is unsafe")
    return hashlib.sha256(_BACKUP_COMMAND.read_bytes()).hexdigest()


def _replace_executable(upload: Path | None, digest: str) -> None:
    if upload is None:
        raise ValueError("backup helper upload is required")
    _BACKUP_COMMAND.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".taskman-backup-", dir=_BACKUP_COMMAND.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(upload.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o750)
        os.replace(temporary, _BACKUP_COMMAND)
        fsync_directory(_BACKUP_COMMAND.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if _verified_executable_checksum() != digest:
        raise ValueError("scheduled backup executable checksum is invalid")


__all__ = ["BackupHelperConvergence", "BackupHelperError", "BackupHelperMutation", "converge_backup_helper"]
