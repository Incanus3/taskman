"""Private retained copies of the two files selected native convergence modifies."""

from collections.abc import Mapping
import errno
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import uuid

from taskman_ops.host_protocol import HostResult, ProtocolError
from taskman_ops.host_protocol.postgresql import (
    POSTGRESQL_RECOVERY_ROOT, native_path, reduce_postgresql_recovery_evidence,
    validate_postgresql_recovery_request, validate_postgresql_recovery_result,
)
from taskman_ops.host_helper.filesystem import fsync_directory

_LIMIT = 1024 * 1024


class PostgreSQLRecoveryError(RuntimeError):
    def __init__(self, exit_code=5, *, mutation_state="unchanged", recovery_directory=None, cluster=None):
        super().__init__("PostgreSQL configuration recovery capture failed")
        self.exit_code = exit_code
        self.mutation_state = mutation_state
        self.recovery_directory = recovery_directory
        self.cluster = cluster


def _safe_parent(path, *, native=False):
    # Resolve neither native sources nor recovery parents through symbolic links.
    if path.resolve() != path:
        raise PostgreSQLRecoveryError(10)
    info = path.lstat()
    owners = {0}
    if native:
        try:
            owners.add(pwd.getpwnam("postgres").pw_uid)
        except KeyError:
            pass
    # The baseline ancestor is root:taskman 0750. A read/traverse group
    # grants no replacement authority; private recovery directories stay 0700.
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners or mode & 0o022 or
            mode & ~0o777):
        raise PostgreSQLRecoveryError(10)


def _original(path):
    path = Path(native_path(str(path)))
    if path.resolve() != path:
        raise PostgreSQLRecoveryError(10)
    _safe_parent(path.parent, native=True)
    info = path.lstat()
    try:
        account = pwd.getpwnam("postgres")
        owners, groups = {0, account.pw_uid}, {0, account.pw_gid}
    except KeyError:
        owners = groups = {0}
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_gid not in groups or
            stat.S_IMODE(info.st_mode) & 0o022 or stat.S_IMODE(info.st_mode) & ~0o777 or info.st_size > _LIMIT):
        raise PostgreSQLRecoveryError(10)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        # A substituted link or removed source is source drift, not evidence
        # that the initially inspected native file was captured.
        raise PostgreSQLRecoveryError(10 if error.errno in {errno.ELOOP, errno.ENOENT} else 5) from None
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if _source_identity(opened) != _source_identity(info):
            raise PostgreSQLRecoveryError(10)
        content = stream.read(_LIMIT + 1)
        ended = os.fstat(stream.fileno())
        try:
            final = path.lstat()
        except OSError:
            raise PostgreSQLRecoveryError(10) from None
        if (_source_identity(ended) != _source_identity(info) or
                _source_identity(final) != _source_identity(info) or len(content) != info.st_size):
            raise PostgreSQLRecoveryError(10)
    if len(content) > _LIMIT:
        raise PostgreSQLRecoveryError(10)
    metadata = {"native_path": str(path), "uid": info.st_uid, "gid": info.st_gid,
                "mode": stat.S_IMODE(info.st_mode), "sha256": hashlib.sha256(content).hexdigest()}
    return content, metadata


def _source_identity(info):
    """Bind ordinary metadata and identity; reading may legitimately change atime."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def capture_postgresql_config(cluster: Mapping[str, object], *, root: Path) -> Path:
    """Capture bounded safe originals; retain every created file on failure."""
    mutation, saved = "unchanged", None
    try:
        if os.geteuid() != 0:
            raise PostgreSQLRecoveryError(10)
        if (not isinstance(root, Path) or not root.is_absolute() or root.resolve() != root or
                not isinstance(cluster, Mapping) or
                set(cluster) != {"version", "name", "port", "data_directory", "config_file", "hba_file"}):
            raise PostgreSQLRecoveryError(10)
        originals = {name: _original(cluster[field]) for name, field in
                     (("postgresql.conf", "config_file"), ("pg_hba.conf", "hba_file"))}
        _safe_parent(root.parent)
        if root.exists():
            info = root.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or
                    stat.S_IMODE(info.st_mode) != 0o700):
                raise PostgreSQLRecoveryError(10)
        else:
            mutation = "unknown"
            root.mkdir(mode=0o700)
            mutation = "changed"
            root.chmod(0o700)
            fsync_directory(root.parent)
        candidate = root / str(uuid.uuid4())
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            raise PostgreSQLRecoveryError(10) from None
        saved, mutation = candidate, "changed"
        saved.chmod(0o700)
        fsync_directory(root)
        for name, (content, _) in originals.items():
            _write_private(saved / name, content)
        metadata = {"cluster": dict(cluster), "files": {name: data for name, (_, data) in originals.items()}}
        _write_private(saved / "metadata.json", json.dumps(metadata, sort_keys=True).encode() + b"\n")
        fsync_directory(saved)
        return saved
    except (OSError, ValueError, PostgreSQLRecoveryError) as error:
        code = error.exit_code if isinstance(error, PostgreSQLRecoveryError) else 5
        raise PostgreSQLRecoveryError(code, mutation_state=mutation,
            recovery_directory=None if saved is None else str(saved),
            cluster=None if saved is None else dict(cluster)) from None


def postgresql_recovery_failure_result(request, result=None, *, exit_code=8):
    state = reduce_postgresql_recovery_evidence(request, result)
    state.update(exit_code=exit_code, failed_boundary="postgresql")
    return HostResult.for_request(request, "refused" if exit_code == 10 else "retryable",
        "PostgreSQL configuration recovery capture failed", state,
        () if result is None or not isinstance(result, HostResult) else result.warnings)


def postgresql_config_recovery(request):
    """Capture only; selected native mutation belongs to the existing service."""
    try:
        validate_postgresql_recovery_request(request)
    except ProtocolError:
        result = postgresql_recovery_failure_result(request, exit_code=10)
        return HostResult.for_request(request, "refused", result.message,
            {**dict(result.state), "mutation_state": "unchanged"})
    try:
        saved = capture_postgresql_config(request.parameters["cluster"], root=Path(POSTGRESQL_RECOVERY_ROOT))
        result = HostResult.for_request(request, "succeeded", "PostgreSQL configuration recovery copies retained",
            {"mutation_state": "changed", "recovery_directory": str(saved), "cluster": dict(request.parameters["cluster"])})
    except PostgreSQLRecoveryError as error:
        result = HostResult.for_request(request, "refused" if error.exit_code == 10 else "retryable",
            "PostgreSQL configuration recovery capture failed", {
                "mutation_state": error.mutation_state, "recovery_directory": error.recovery_directory,
                "cluster": error.cluster, "exit_code": error.exit_code, "failed_boundary": "postgresql"})
    return validate_postgresql_recovery_result(request, result)
