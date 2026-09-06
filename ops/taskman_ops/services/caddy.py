"""Caddy's declarative repository state and guarded configuration replacement."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import subprocess

from ..config import EnvironmentConfig
from ..pyinfra import conditional_convergence


_OPS_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _OPS_ROOT / "caddy" / "Caddyfile"
_RENDERER = _OPS_ROOT / "caddy" / "render-caddyfile"
_STAGED_CADDYFILE = "/etc/taskman/Caddyfile.staged"
_CADDYFILE = "/etc/caddy/Caddyfile"


@dataclass(frozen=True)
class CaddyRepository:
    key_url: str
    keyring: str
    source: str


@dataclass(frozen=True)
class CaddyPlan:
    repository: CaddyRepository
    repository_packages: tuple[str, ...]
    packages: tuple[str, ...]
    caddyfile: str


def build_caddy_plan(config: EnvironmentConfig) -> CaddyPlan:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("Caddy plan requires an environment configuration")
    return CaddyPlan(
        repository=CaddyRepository(
            key_url="https://dl.cloudsmith.io/public/caddy/stable/gpg.key",
            keyring="/usr/share/keyrings/caddy-stable-archive-keyring.gpg",
            source=(
                "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] "
                "https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main"
            ),
        ),
        repository_packages=(
            "ca-certificates",
            "curl",
            "gnupg",
            "debian-keyring",
            "debian-archive-keyring",
            "apt-transport-https",
        ),
        packages=("caddy",),
        caddyfile=render_caddyfile(config),
    )


def declare_caddy(plan: CaddyPlan) -> CaddyPlan:
    """Add built-in repository/file operations and one availability guard.

    The only custom step validates the staged configuration immediately before
    replacing the live file. An invalid replacement could take the public
    HTTPS endpoint down; package, repository, and staged-file convergence are
    ordinary pyinfra operations.
    """

    from pyinfra.operations import apt, files, systemd

    if not isinstance(plan, CaddyPlan):
        raise TypeError("Caddy declaration requires a preconfirmed Caddy plan")
    apt.packages(packages=list(plan.repository_packages), name="Install Caddy repository prerequisites")
    apt.key(src=plan.repository.key_url, dest=plan.repository.keyring, name="Install Caddy signing key")
    apt.repo(plan.repository.source, filename="caddy-stable", name="Configure Caddy repository")
    apt.packages(packages=list(plan.packages), name="Install Caddy")
    files.put(
        StringIO(plan.caddyfile),
        _STAGED_CADDYFILE,
        user="root",
        group="root",
        mode=0o644,
        add_deploy_dir=False,
        name="Stage Caddy configuration",
    )
    installation = conditional_convergence(
        name="Validate and install Caddy configuration",
        probe=render_caddy_install_probe(_STAGED_CADDYFILE, _CADDYFILE),
        script=render_caddy_install_script(_STAGED_CADDYFILE, _CADDYFILE),
    )
    systemd.service(
        "caddy",
        running=True,
        enabled=True,
        name="Enable and start Caddy",
    )
    systemd.service(
        "caddy",
        reloaded=True,
        _if=lambda: bool(getattr(installation, "did_change")()),
        name="Reload Caddy after validated replacement",
    )
    return plan


def render_caddyfile(config: EnvironmentConfig) -> str:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("Caddy renderer requires an environment configuration")
    try:
        completed = subprocess.run(
            ("sh", str(_RENDERER), config.public_hostname, str(_TEMPLATE), str(config.application_port)),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        raise RuntimeError("Caddyfile rendering is unavailable") from None
    if completed.returncode != 0:
        raise RuntimeError("Caddyfile rendering failed")
    rendered = completed.stdout
    if "taskman.example.com" in rendered or "reverse_proxy 127.0.0.1:" not in rendered:
        raise RuntimeError("Caddyfile rendering failed")
    return rendered


def render_caddy_install_probe(staged: str, destination: str) -> str:
    return (
        "set -eu; "
        f"state=$(stat --format='%U:%G:%a' {destination} 2>/dev/null || true); "
        f"if cmp -s {staged} {destination} && [ \"$state\" = root:root:644 ]; then "
        "printf 'changed=0\\n'; else printf 'changed=1\\n'; fi"
    )


def render_caddy_install_script(staged: str, destination: str) -> str:
    """Validate a staged Caddyfile before replacing public-serving bytes."""

    if not isinstance(staged, str) or not isinstance(destination, str):
        raise TypeError("Caddy install paths must be strings")
    return (
        "set -eu; changed=0; "
        f"state=$(stat --format='%U:%G:%a' {destination} 2>/dev/null || true); "
        f"if ! cmp -s {staged} {destination} || [ \"$state\" != root:root:644 ]; then "
        f"caddy validate --config {staged} --adapter caddyfile; "
        f"install -o root -g root -m 0644 {staged} {destination}; changed=1; fi; "
        "printf 'changed=%s\\n' \"$changed\""
    )


__all__ = [
    "CaddyPlan",
    "CaddyRepository",
    "build_caddy_plan",
    "declare_caddy",
    "render_caddy_install_probe",
    "render_caddy_install_script",
    "render_caddyfile",
]
