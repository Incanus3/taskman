"""Caddy repository, rendering, validation, and reload convergence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import subprocess
from typing import Protocol

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..pyinfra import conditional_convergence
from ..remote import ChangeSet, Remote


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
    staged_validation: tuple[str, ...]
    service_enablement: tuple[str, ...]
    forbidden_service_starts: tuple[str, ...]


class CaddyOperations(Protocol):
    """The small desired-state surface used by the native pyinfra adapter."""

    def packages(self, packages: tuple[str, ...]) -> None: ...

    def repository(self, repository: CaddyRepository) -> None: ...

    def staged_caddyfile(self, content: str) -> None: ...

    def validate_install_and_converge_service(self) -> None: ...


def build_caddy_plan(config: EnvironmentConfig) -> CaddyPlan:
    """Build a Caddy plan from validated public-host configuration only."""

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
        staged_validation=("caddy", "validate", "--config", _STAGED_CADDYFILE, "--adapter", "caddyfile"),
        service_enablement=("caddy.service",),
        forbidden_service_starts=("taskman.service",),
    )


def render_caddyfile(config: EnvironmentConfig) -> str:
    """Use the checked-in strict renderer rather than interpolating a site label."""

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


def converge_caddy(config: EnvironmentConfig, *, operations: CaddyOperations | None = None) -> CaddyPlan:
    """Declare authenticated Caddy installation and changed-file-only reload logic."""

    plan = build_caddy_plan(config)
    backend = operations or _PyinfraCaddyOperations()
    backend.packages(plan.repository_packages)
    backend.repository(plan.repository)
    backend.packages(plan.packages)
    backend.staged_caddyfile(plan.caddyfile)
    backend.validate_install_and_converge_service()
    return plan


def apply_caddy_install(remote: Remote, plan: CaddyPlan) -> ChangeSet:
    """Execute the staged Caddy/service transaction with an explicit change result."""

    if not isinstance(plan, CaddyPlan):
        raise TypeError("Caddy installation requires a plan")
    result = remote.run(("sh", "-c", render_caddy_install_script(_STAGED_CADDYFILE, _CADDYFILE)), sudo=True)
    if not result.succeeded:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "caddy",
            "unable to validate and converge Caddy",
            changed=False,
            next_action="inspect the staged Caddyfile and Caddy service state before retrying",
        )
    return ChangeSet(changed=_changed_result(result.stdout), operations=("caddy",))


class _PyinfraCaddyOperations:
    """Use pyinfra for stable files/packages and one ordered install transaction."""

    def packages(self, packages: tuple[str, ...]) -> None:
        from pyinfra.operations import apt

        apt.packages(packages=list(packages))

    def repository(self, repository: CaddyRepository) -> None:
        from pyinfra.operations import apt, files

        armored = "/etc/taskman/caddy-stable-archive-keyring.asc"
        files.download(repository.key_url, armored, user="root", group="root", mode=0o644)
        # The source list is pinned to this dedicated keyring.  De-armoring
        # compares generated bytes before replacement, and occurs before apt
        # reads the source without a prepare-time condition on a prior op.
        conditional_convergence(
            probe=render_caddy_keyring_probe(armored, repository.keyring),
            script=render_caddy_keyring_install_script(armored, repository.keyring),
        )
        apt.repo(repository.source, filename="caddy-stable")

    def staged_caddyfile(self, content: str) -> None:
        from pyinfra.operations import files

        files.put(
            StringIO(content),
            _STAGED_CADDYFILE,
            user="root",
            group="root",
            mode=0o644,
            add_deploy_dir=False,
        )

    def validate_install_and_converge_service(self) -> None:
        conditional_convergence(
            probe=render_caddy_install_probe(_STAGED_CADDYFILE, _CADDYFILE),
            script=render_caddy_install_script(_STAGED_CADDYFILE, _CADDYFILE),
        )


def render_caddy_keyring_probe(armored: str, keyring: str) -> str:
    """Return a read-only keyring byte/metadata drift probe."""

    return (
        "set -eu; staged=$(mktemp /tmp/taskman-caddy-key-probe.XXXXXX); "
        "trap 'rm -f \"$staged\"' EXIT HUP INT TERM; "
        f"gpg --batch --yes --dearmor --output \"$staged\" {armored}; "
        f"state=$(stat --format='%U:%G:%a' {keyring} 2>/dev/null || true); "
        f"if cmp -s \"$staged\" {keyring} && [ \"$state\" = root:root:644 ]; then "
        "printf 'changed=0\\n'; else printf 'changed=1\\n'; fi"
    )


def render_caddy_keyring_install_script(armored: str, keyring: str) -> str:
    """Render the keyring repair that follows a positive drift probe."""

    return (
        "set -eu; staged=$(mktemp /tmp/taskman-caddy-key.XXXXXX); "
        "trap 'rm -f \"$staged\"' EXIT HUP INT TERM; "
        f"gpg --batch --yes --dearmor --output \"$staged\" {armored}; "
        f"install -o root -g root -m 0644 \"$staged\" {keyring}; printf 'changed=1\\n'"
    )


def render_caddy_install_probe(staged: str, destination: str) -> str:
    """Return an execution-time drift probe for the owned file and service."""

    return (
        "set -eu; "
        f"state=$(stat --format='%U:%G:%a' {destination} 2>/dev/null || true); "
        f"if cmp -s {staged} {destination} && [ \"$state\" = root:root:644 ] "
        "&& systemctl is-enabled --quiet caddy.service && systemctl is-active --quiet caddy.service; then "
        "printf 'changed=0\\n'; else printf 'changed=1\\n'; fi"
    )


def render_caddy_install_script(
    staged: str,
    destination: str,
    *,
    owner: str | None = "root",
    group: str | None = "root",
) -> str:
    """Validate then repair Caddy bytes and metadata before a changed-only reload."""

    if not isinstance(staged, str) or not isinstance(destination, str):
        raise TypeError("Caddy install paths must be strings")
    if (owner is None) != (group is None):
        raise ValueError("Caddy install owner and group must be provided together")
    install_owner = "" if owner is None else f"-o {owner} -g {group} "
    expected_state = "644" if owner is None else f"{owner}:{group}:644"
    state = "$(stat --format='%a'" if owner is None else "$(stat --format='%U:%G:%a'"
    return (
        "set -eu; changed=0; "
        f"state={state} {destination} 2>/dev/null || true); "
        f"if ! cmp -s {staged} {destination} || [ \"$state\" != {expected_state} ]; then "
        f"caddy validate --config {staged} --adapter caddyfile; "
        f"install {install_owner}-m 0644 {staged} {destination}; changed=1; fi; "
        "if ! systemctl is-enabled --quiet caddy.service; then systemctl enable caddy.service; changed=1; fi; "
        "if systemctl is-active --quiet caddy.service; then "
        "if [ \"$changed\" -eq 1 ]; then systemctl reload caddy.service; fi; "
        "else systemctl start caddy.service; changed=1; fi; "
        "printf 'changed=%s\\n' \"$changed\""
    )


def _changed_result(stdout: str) -> bool:
    markers = [line.removeprefix("changed=") for line in stdout.splitlines() if line.startswith("changed=")]
    if markers == ["0"]:
        return False
    if markers == ["1"]:
        return True
    raise OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "caddy",
        "Caddy convergence returned an invalid change result",
        changed=False,
        next_action="inspect Caddy convergence output before retrying",
    )


__all__ = [
    "CaddyPlan",
    "CaddyRepository",
    "apply_caddy_install",
    "build_caddy_plan",
    "converge_caddy",
    "render_caddy_install_script",
    "render_caddy_install_probe",
    "render_caddyfile",
]
