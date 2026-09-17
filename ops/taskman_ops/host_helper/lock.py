"""The single host lifecycle lock used by mutating helper capabilities."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import time
from collections.abc import Iterator

from .paths import ManagedPaths, PathAuthorityError


class LifecycleLockContention(TimeoutError):
    """Raised when the lifecycle lock remains held at the caller deadline."""


class LifecycleLock:
    """One acquired lifecycle-lock descriptor with an explicit owner."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    @property
    def held(self) -> bool:
        return self._descriptor >= 0

    def release(self) -> None:
        """Release exactly this acquisition, once."""

        if self._descriptor < 0:
            return
        descriptor, self._descriptor = self._descriptor, -1
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def reacquire(self, paths: ManagedPaths, timeout_seconds: float) -> None:
        """Replace this released acquisition without changing its explicit owner."""

        if self.held:
            raise RuntimeError("lifecycle lock is already held")
        replacement = acquire_lifecycle_lock(paths, timeout_seconds)
        self._descriptor, replacement._descriptor = replacement._descriptor, -1

    def __enter__(self) -> "LifecycleLock":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.release()


def _safe_directory(path: Path, owner_uid: int) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o755, parents=True)
            os.chmod(path, 0o755)
        except FileExistsError:
            pass
        except OSError as error:
            raise LifecycleLockContention("unable to prepare lifecycle lock directory") from error
        try:
            details = path.lstat()
        except OSError as error:
            raise LifecycleLockContention("unable to inspect lifecycle lock directory") from error
    except OSError as error:
        raise LifecycleLockContention("unable to inspect lifecycle lock directory") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
    ):
        raise LifecycleLockContention("lifecycle lock directory is unsafe")


@contextmanager
def lifecycle_lock(paths: ManagedPaths, timeout_seconds: float) -> Iterator[LifecycleLock]:
    """Acquire the one exclusive lifecycle lock for a bounded duration.

    The lock file is derived from ``install_root`` and contains no operation
    identity or mutable recovery metadata.  Its existence is not itself host
    state; the advisory descriptor lock is the authority.
    """

    if not isinstance(paths, ManagedPaths):
        raise TypeError("lifecycle lock needs managed paths")
    if type(timeout_seconds) not in {int, float} or timeout_seconds < 0:
        raise ValueError("invalid lifecycle lock timeout")
    lock = acquire_lifecycle_lock(paths, timeout_seconds)
    try:
        yield lock
    finally:
        lock.release()


def acquire_lifecycle_lock(paths: ManagedPaths, timeout_seconds: float) -> LifecycleLock:
    """Acquire and return an explicitly owned lifecycle lock."""

    owner_uid = os.geteuid()
    try:
        paths.validate_existing(owner_uid=owner_uid)
    except PathAuthorityError as error:
        raise LifecycleLockContention(str(error)) from error
    install_root = Path(paths.local(paths.install_root))
    _safe_directory(install_root, owner_uid)
    lock_path = Path(paths.local(paths.lifecycle_lock_path))
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as error:
        raise LifecycleLockContention("unable to open lifecycle lock") from error
    try:
        details = lock_path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_uid != owner_uid
            or details.st_mode & 0o7077
        ):
            raise LifecycleLockContention("lifecycle lock file is unsafe")
        deadline = time.monotonic() + float(timeout_seconds)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LifecycleLockContention("lifecycle lock is held")
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        return LifecycleLock(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


__all__ = ["LifecycleLock", "LifecycleLockContention", "acquire_lifecycle_lock", "lifecycle_lock"]
