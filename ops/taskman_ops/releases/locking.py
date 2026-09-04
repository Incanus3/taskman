"""Bounded advisory locks for host lifecycle operations.

One root-owned lock serializes every operation that can change or inspect the
lifecycle tree.  The lock file itself carries only the current exclusive
holder's safe diagnostic metadata; it never carries a command line, secret,
or arbitrary caller data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Literal
from uuid import uuid4

from ..errors import ExitStatus, OpsError


_OPERATION_RE = re.compile(r"[a-z][a-z-]{0,63}\Z")


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "lifecycle-lock",
        message,
        changed=False,
        next_action="inspect the managed lifecycle lock and resolve the unsafe state",
    )


@dataclass
class LifecycleLock:
    """One acquired lifecycle lock, released automatically by its context."""

    path: Path
    operation: str
    exclusive: bool
    pid: int
    started_at: datetime
    _descriptor: int = field(repr=False)
    _metadata_descriptor: int = field(repr=False)
    _holder_token: str = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> LifecycleLock:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> Literal[False]:
        self.release()
        return False

    def release(self) -> None:
        """Release the advisory lock before removing serialized holder data."""

        if self._released:
            return
        try:
            fcntl.flock(self._metadata_descriptor, fcntl.LOCK_EX)
            # The metadata lock prevents a contender from seeing the narrow
            # unlocked-but-not-yet-removed holder window.
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            holders = _live_holders(_read_holders(self._metadata_descriptor))
            _write_holders(
                self._metadata_descriptor,
                [holder for holder in holders if holder["token"] != self._holder_token],
            )
            fcntl.flock(self._metadata_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            os.close(self._metadata_descriptor)
            self._released = True


def lifecycle_lock(
    path: Path,
    *,
    operation: str,
    exclusive: bool,
    timeout_seconds: float,
    owner_uid: int = 0,
) -> LifecycleLock:
    """Acquire a shared or exclusive lock without unbounded waiting.

    Mutating workflows use ``exclusive=True``.  Read-only discovery uses a
    shared lock and therefore cannot see a record half-written by a mutator.
    """

    if not isinstance(operation, str) or _OPERATION_RE.fullmatch(operation) is None:
        raise ValueError("invalid lifecycle operation")
    if not isinstance(exclusive, bool):
        raise TypeError("exclusive must be boolean")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds < 0:
        raise ValueError("lock timeout must be non-negative")
    lock_path = Path(path)
    descriptor = _open_safe_lock(lock_path, owner_uid)
    try:
        metadata_descriptor = _open_safe_lock(_metadata_path(lock_path), owner_uid)
    except Exception:
        os.close(descriptor)
        raise
    requested = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            _acquire_metadata_lock(metadata_descriptor, deadline)
            try:
                fcntl.flock(descriptor, requested | fcntl.LOCK_NB)
                token = uuid4().hex
                started_at = _now()
                holders = _live_holders(_read_holders(metadata_descriptor))
                holders.append(
                    {
                        "token": token,
                        "operation": operation,
                        "pid": os.getpid(),
                        "started_at": _timestamp(started_at),
                        "mode": "exclusive" if exclusive else "shared",
                    }
                )
                _write_holders(metadata_descriptor, holders)
                fcntl.flock(metadata_descriptor, fcntl.LOCK_UN)
                return LifecycleLock(lock_path, operation, exclusive, os.getpid(), started_at, descriptor, metadata_descriptor, token)
            except BlockingIOError:
                holders = _read_holders(metadata_descriptor)
                live_holders = _live_holders(holders)
                if live_holders != holders:
                    _write_holders(metadata_descriptor, live_holders)
                fcntl.flock(metadata_descriptor, fcntl.LOCK_UN)
                if time.monotonic() >= deadline:
                    raise _locked(live_holders)
                # The wait remains bounded by the supplied deadline. Tests
                # coordinate contenders with process events instead of time.
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            except Exception:
                fcntl.flock(metadata_descriptor, fcntl.LOCK_UN)
                raise
    except Exception:
        os.close(descriptor)
        os.close(metadata_descriptor)
        raise


def _open_safe_lock(path: Path, owner_uid: int) -> int:
    _ensure_lock_directory(path.parent, owner_uid)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        try:
            descriptor = os.open(path, flags & ~os.O_CREAT, 0o600)
            created = False
        except OSError:
            raise _safety("unable to open lifecycle lock") from None
    except OSError:
        raise _safety("unable to open lifecycle lock") from None
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise _safety("lifecycle lock is not a regular file")
        if not created and (details.st_uid != owner_uid or stat.S_IMODE(details.st_mode) != 0o600):
            raise _safety("lifecycle lock ownership or permissions are unsafe")
        if created:
            os.fchown(descriptor, owner_uid, -1)
            os.fchmod(descriptor, 0o600)
        details = os.fstat(descriptor)
        if details.st_uid != owner_uid or stat.S_IMODE(details.st_mode) != 0o600:
            raise _safety("lifecycle lock ownership or permissions are unsafe")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _ensure_lock_directory(directory: Path, owner_uid: int) -> None:
    if not directory.exists() and not directory.is_symlink():
        try:
            directory.mkdir(parents=True)
            os.chown(directory, owner_uid, -1)
            os.chmod(directory, 0o750)
        except FileExistsError:
            pass
        except OSError:
            raise _safety("unable to prepare lifecycle lock directory") from None
    try:
        details = directory.lstat()
    except OSError:
        raise _safety("lifecycle lock directory is unsafe") from None
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o027
    ):
        raise _safety("lifecycle lock directory is unsafe")


def _metadata_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.meta")


def _acquire_metadata_lock(descriptor: int, deadline: float) -> None:
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise _safety("lifecycle lock metadata is contended")
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _read_holders(descriptor: int) -> list[dict[str, object]]:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 4096):
            chunks.append(chunk)
        if not chunks:
            return []
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise _safety("lifecycle lock holder metadata is invalid") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("holders"), list):
        raise _safety("lifecycle lock holder metadata is invalid")
    holders = payload["holders"]
    if not all(_valid_holder(holder) for holder in holders):
        raise _safety("lifecycle lock holder metadata is invalid")
    return holders  # type: ignore[return-value]


def _valid_holder(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"token", "operation", "pid", "started_at", "mode"}
        and isinstance(value["token"], str)
        and _OPERATION_RE.fullmatch(value["operation"]) is not None
        and type(value["pid"]) is int
        and value["pid"] > 0
        and isinstance(value["started_at"], str)
        and value["mode"] in {"shared", "exclusive"}
    )


def _live_holders(holders: list[dict[str, object]]) -> list[dict[str, object]]:
    """Drop only holders whose recorded process definitively no longer exists."""

    live: list[dict[str, object]] = []
    for holder in holders:
        pid = holder["pid"]
        assert type(pid) is int  # established by _read_holders
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            # A process we cannot inspect may still hold the advisory lock.
            pass
        except OSError as error:
            if error.errno == errno.ESRCH:
                continue
            raise _safety("unable to verify lifecycle lock holder metadata") from None
        live.append(holder)
    return live


def _write_holders(descriptor: int, holders: list[dict[str, object]]) -> None:
    encoded = (json.dumps({"schema_version": 1, "holders": holders}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
    except OSError:
        raise _safety("unable to write lifecycle lock holder metadata") from None


def _write_all(descriptor: int, data: bytes) -> None:
    pending = memoryview(data)
    while pending:
        written = os.write(descriptor, pending)
        if written <= 0:
            raise OSError("short lifecycle lock metadata write")
        pending = pending[written:]


def _locked(holders: list[dict[str, object]]) -> OpsError:
    holder = min(holders, key=lambda item: (str(item["started_at"]), int(item["pid"]), str(item["token"]))) if holders else None
    operation = holder["operation"] if holder is not None else None
    pid = holder["pid"] if holder is not None else None
    started_at = holder["started_at"] if holder is not None else None
    mode = holder["mode"] if holder is not None else None
    if (
        not isinstance(operation, str)
        or _OPERATION_RE.fullmatch(operation) is None
        or type(pid) is not int
        or pid <= 0
        or not isinstance(started_at, str)
        or mode not in {"shared", "exclusive"}
    ):
        detail = "an unknown lifecycle operation"
    else:
        detail = f"{operation} (pid {pid}, started {started_at}, {mode})"
    return OpsError(
        ExitStatus.LOCKED,
        "lifecycle-lock",
        f"lifecycle lock is held by {detail}",
        changed=False,
        next_action="wait for the recorded lifecycle operation to finish and retry",
    )


__all__ = ["LifecycleLock", "lifecycle_lock"]
