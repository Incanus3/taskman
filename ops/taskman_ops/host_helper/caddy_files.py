"""Protected, atomic publication and narrowly scoped Caddy site restoration."""
from __future__ import annotations
import hashlib
import json
import os
import stat
import tempfile
import uuid
from taskman_ops.host_helper.filesystem import fsync_directory


def atomic_file(path, data, metadata):
    descriptor, temporary = tempfile.mkstemp(prefix='.taskman-caddy-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchown(stream.fileno(), metadata['uid'], metadata['gid'])
            os.fchmod(stream.fileno(), metadata['mode'])
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def safe_directory(observer, path, mode):
    observer._parents(path)
    info = observer._stat(path)
    if info is not None:
        observer._metadata(path, directory=True)
        return False
    path.mkdir(mode=mode)
    os.chown(path, 0, 0)
    fsync_directory(path.parent)
    return True


def protected_directory(observer):
    parent = observer._path('/etc/taskman')
    safe_directory(observer, parent, 0o750)
    parent = parent / 'caddy-recovery'
    safe_directory(observer, parent, 0o700)
    _protected_metadata(observer, parent, directory=True)
    identifier = uuid.uuid4().hex
    directory = parent / identifier
    directory.mkdir(mode=0o700)
    os.chown(directory, 0, 0)
    fsync_directory(parent)
    return identifier, directory


def _protected_metadata(observer, path, *, directory=False):
    info = observer._stat(path)
    if (info is None or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError('unsafe Caddy recovery evidence')


def protect_site(observer, receipt):
    identifier, directory = protected_directory(observer)
    path = observer._path(receipt['site_path'])
    record = {key: receipt[key] for key in ('site_path', 'original_absent', 'original_sha256', 'original_metadata')}
    if not receipt['original_absent']:
        data = observer._read(path, 4096)
        if hashlib.sha256(data).hexdigest() != receipt['original_sha256']:
            raise ValueError('Caddy original site changed')
        atomic_file(directory / 'site', data, {'uid': 0, 'gid': 0, 'mode': 0o600})
    atomic_file(directory / 'site.json', json.dumps(record, sort_keys=True, separators=(',', ':')).encode(),
                {'uid': 0, 'gid': 0, 'mode': 0o600})
    return identifier


def restore_site(observer, receipt):
    identifier = receipt['recovery_id']
    if identifier is None:
        raise ValueError('missing Caddy recovery evidence')
    # Identifiers/path/digests are validated by the exact protocol before entry.
    directory = observer._path('/etc/taskman/caddy-recovery') / identifier
    observer._parents(directory)
    _protected_metadata(observer, directory, directory=True)
    _protected_metadata(observer, directory / 'site.json')
    record = json.loads(observer._read(directory / 'site.json', 4096))
    if record != {key: receipt[key] for key in ('site_path', 'original_absent', 'original_sha256', 'original_metadata')}:
        raise ValueError('Caddy recovery identity mismatch')
    path = observer._path(receipt['site_path'])
    observer._parents(path)
    observer._metadata(path, site=True)
    if hashlib.sha256(observer._read(path, 4096)).hexdigest() != receipt['site_sha256']:
        raise ValueError('Caddy candidate changed before recovery')
    if receipt['original_absent']:
        path.unlink()
        fsync_directory(path.parent)
    else:
        _protected_metadata(observer, directory / 'site')
        data = observer._read(directory / 'site', 4096)
        if hashlib.sha256(data).hexdigest() != receipt['original_sha256']:
            raise ValueError('Caddy recovery checksum mismatch')
        atomic_file(path, data, receipt['original_metadata'])
        if observer._metadata(path, site=True) != receipt['original_metadata'] or observer._read(path, 4096) != data:
            raise ValueError('Caddy site restoration incomplete')
