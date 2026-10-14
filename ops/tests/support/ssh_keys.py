"""Disposable native OpenSSH key material for local transport tests."""

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class HostKey:
    public: str
    fingerprint: str


def generate_host_key(directory: Path, name: str, algorithm: str = "ed25519") -> HostKey:
    key = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", algorithm, "-N", "", "-C", name, "-f", str(key)],
        check=True, capture_output=True,
    )
    public = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    result = subprocess.run(
        ["ssh-keygen", "-lf", str(key.with_suffix(".pub")), "-E", "sha256"],
        check=True, capture_output=True, text=True,
    )
    return HostKey(public, result.stdout.split()[1])


def write_known_hosts(home: Path, contents: str, *, hashed: bool = False) -> Path:
    directory = home / ".ssh"
    directory.mkdir(exist_ok=True)
    path = directory / "known_hosts"
    path.write_text(contents, encoding="utf-8")
    if hashed:
        subprocess.run(
            ["ssh-keygen", "-H", "-f", str(path)], check=True, capture_output=True,
        )
    return path
