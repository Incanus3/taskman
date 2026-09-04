"""Caddy's declarative repository state and guarded configuration replacement."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import subprocess

from pyinfra.api import operation
from pyinfra.api.command import QuoteString, StringCommand

from ..config import EnvironmentConfig
from ..host.pyinfra_support import operation_sudo


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
    package = apt.packages(packages=list(plan.packages), name="Install Caddy")
    staged_configuration = files.put(
        StringIO(plan.caddyfile),
        _STAGED_CADDYFILE,
        user="root",
        group="root",
        mode="644",
        add_deploy_dir=False,
        name="Stage Caddy configuration",
    )
    installation = _validate_and_install_caddy(
        _STAGED_CADDYFILE,
        _CADDYFILE,
        package,
        staged_configuration,
        name="Validate staged Caddy configuration",
        _sudo=True,
    )
    live_configuration = files.put(
        StringIO(plan.caddyfile),
        _CADDYFILE,
        user="root",
        group="root",
        mode="644",
        add_deploy_dir=False,
        _if=lambda: installation.did_change(),
        name="Install validated Caddy configuration",
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
        _if=lambda: live_configuration.did_change(),
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


@operation(is_idempotent=True)
def _validate_and_install_caddy(
    staged: str,
    destination: str,
    package: object,
    staged_configuration: object,
):
    """Validate only when the guarded live configuration may need work."""

    from pyinfra.context import state

    if state.is_executing and not _caddy_validation_required(
        staged,
        destination,
        package,
        staged_configuration,
    ):
        return
    yield StringCommand("caddy", "validate", "--config", QuoteString(staged), "--adapter", "caddyfile")


def _caddy_validation_required(
    staged: str,
    destination: str,
    package: object,
    staged_configuration: object,
) -> bool:
    """Inspect only the live file boundary immediately before validation."""

    if _did_change(package) or _did_change(staged_configuration):
        return True

    from pyinfra.context import host

    runner = getattr(host, "run_shell_command", None)
    if not callable(runner):
        raise RuntimeError("invalid pyinfra host for Caddy validation")
    script = (
        "set -eu; "
        f"state=$(stat --format='%U:%G:%a' {destination} 2>/dev/null || true); "
        f"if cmp -s {staged} {destination} && [ \"$state\" = root:root:644 ]; then "
        "printf 'changed=0\\n'; else printf 'changed=1\\n'; fi"
    )
    succeeded, output = runner(
        StringCommand("sh", "-c", QuoteString(script)),
        print_output=False,
        print_input=False,
        _sudo=operation_sudo(host),
    )
    if not succeeded:
        raise RuntimeError("Caddy live configuration inspection failed")
    markers = [
        line.removeprefix("changed=")
        for line in output.stdout_lines
        if line.startswith("changed=")
    ]
    if markers == ["0"]:
        return False
    if markers == ["1"]:
        return True
    raise RuntimeError("Caddy live configuration inspection returned an invalid change result")


def _did_change(result: object) -> bool:
    changed = getattr(result, "did_change", None)
    return bool(changed()) if callable(changed) else False


__all__ = [
    "CaddyPlan",
    "CaddyRepository",
    "build_caddy_plan",
    "declare_caddy",
    "render_caddyfile",
]
