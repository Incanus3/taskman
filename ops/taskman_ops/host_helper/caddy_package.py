"""Authenticated upstream APT selection, scoped to one installation invocation."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import stat

_ARCHIVE_LIMIT = 100 * 1024 * 1024
_ORIGIN = 'https://dl.cloudsmith.io/public/caddy/stable/deb/debian'


@dataclass(frozen=True)
class Candidate:
    version: str
    path: Path
    sha256: str


def archive_sha256(observer, path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _ARCHIVE_LIMIT:
        raise ValueError('unsafe Caddy candidate archive')
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while True:
            observer._remaining(3)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _ARCHIVE_LIMIT:
                raise ValueError('oversized Caddy candidate archive')
            digest.update(chunk)
    if size != info.st_size:
        raise ValueError('changed Caddy candidate archive')
    return digest.hexdigest()


def candidate_version(policy):
    text = policy.decode('ascii')
    match = re.search(r'^  Candidate: ([A-Za-z0-9.+:~_-]{1,128})$', text, re.M)
    if match is None:
        raise ValueError('unavailable Caddy candidate')
    version = match[1]
    block = re.search(r'^\s{2,5}(?:\*\*\* )?' + re.escape(version) + r' [0-9]+\n((?: {8}[^\n]+\n)+)', text, re.M)
    if block is None:
        raise ValueError('unavailable Caddy candidate origin')
    sources = [line.strip().split() for line in block[1].splitlines()]
    if (len(sources) != 1 or len(sources[0]) != 5 or not sources[0][0].isdigit()
            or sources[0][1:] != [_ORIGIN, 'any-version/main', 'amd64', 'Packages']):
        raise ValueError('unsupported Caddy candidate origin')
    return version


def inspect_candidate(observer, directory):
    version = candidate_version(observer._native(('apt-cache', 'policy', 'caddy'), timeout_seconds=3))
    # Native APT authenticates the exact candidate using operator-managed indexes.
    observer._native(('sh', '-c', 'cd -- "$1" && shift && exec "$@"', 'taskman-caddy-download', str(directory),
        'apt-get', '-o', 'APT::Get::AllowUnauthenticated=false', '-o', 'Acquire::AllowInsecureRepositories=false',
        '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false', 'download', 'caddy=' + version), timeout_seconds=300)
    packages = list(directory.iterdir())
    if len(packages) != 1 or packages[0].suffix != '.deb':
        raise ValueError('unavailable Caddy candidate package')
    path = packages[0]
    digest = archive_sha256(observer, path)
    identity = observer._native(('dpkg-deb', '--show', '--showformat=${Package}\t${Version}\t${Architecture}\n',
                                str(path)), timeout_seconds=3)
    if identity != f'caddy\t{version}\tamd64\n'.encode('ascii'):
        raise ValueError('Caddy candidate identity mismatch')
    return Candidate(version, path, digest)


def bootstrap_default(observer, candidate, directory):
    staging = directory / 'default'
    observer._native(('dpkg-deb', '--extract', str(candidate.path), str(staging)), timeout_seconds=30)
    path = staging / 'etc/caddy/Caddyfile'
    info = observer._stat(path)
    if (info is None or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o644 or info.st_size > 65536):
        raise ValueError('unsafe Caddy package default')
    return observer._read(path, 65536)
