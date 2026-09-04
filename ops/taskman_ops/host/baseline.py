"""Declarative desired state for the Taskman host baseline."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

from ..config import EnvironmentConfig
from .firewall import declare_firewall


BASELINE_PACKAGES: tuple[str, ...] = (
    "ca-certificates",
    "curl",
    "gnupg",
    "python3-minimal",
    "unattended-upgrades",
    "ufw",
)
_UNATTENDED_UPDATES = (
    'APT::Periodic::Update-Package-Lists "1";\n'
    'APT::Periodic::Unattended-Upgrade "1";\n'
    'Unattended-Upgrade::Automatic-Reboot "false";\n'
)
_PROVISIONING_MARKER_PATH = "/var/lib/taskman-provisioning.state"
_PROVISIONING_MARKER_CONTENT = "taskman-provisioning-v1\n"


@dataclass(frozen=True)
class ManagedDirectory:
    path: str
    owner: str
    group: str
    mode: int


@dataclass(frozen=True)
class ServiceAccount:
    name: str
    home: str
    shell: str
    system: bool


@dataclass(frozen=True)
class BaselinePlan:
    packages: tuple[str, ...]
    service_account: ServiceAccount
    directories: tuple[ManagedDirectory, ...]
    unattended_updates: str
    provisioning_marker_path: str
    provisioning_marker_content: str


TASKMAN_DIRECTORIES: tuple[ManagedDirectory, ...] = (
    ManagedDirectory("/opt/taskman", "root", "root", 0o755),
    ManagedDirectory("/opt/taskman/releases", "root", "root", 0o755),
    ManagedDirectory("/opt/taskman/deployments", "root", "root", 0o700),
    ManagedDirectory("/etc/taskman", "root", "taskman", 0o750),
    ManagedDirectory("/var/lib/taskman", "taskman", "taskman", 0o700),
    ManagedDirectory("/var/backups/taskman", "root", "root", 0o700),
    ManagedDirectory("/var/lock/taskman", "root", "root", 0o700),
    ManagedDirectory("/usr/local/lib/taskman", "root", "root", 0o755),
)


def build_baseline_plan(config: EnvironmentConfig) -> BaselinePlan:
    """Describe the stable baseline without inspecting a mutable host."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("baseline plan requires an environment configuration")
    return BaselinePlan(
        packages=BASELINE_PACKAGES,
        service_account=ServiceAccount("taskman", "/var/lib/taskman", "/usr/sbin/nologin", True),
        directories=_taskman_directories(config),
        unattended_updates=_UNATTENDED_UPDATES,
        provisioning_marker_path=_PROVISIONING_MARKER_PATH,
        provisioning_marker_content=_PROVISIONING_MARKER_CONTENT,
    )


def declare_baseline(config: EnvironmentConfig) -> BaselinePlan:
    """Add built-in package, account, directory, and update operations.

    All fact and diff decisions remain in pyinfra's execution lifecycle.  This
    function contains no host-state branch that could become stale after an
    earlier operation in the same deploy.
    """

    from pyinfra.operations import apt, files, server

    plan = build_baseline_plan(config)
    apt.packages(
        packages=list(plan.packages),
        update=True,
        cache_time=3600,
        name="Install Taskman baseline packages",
    )
    server.user(
        plan.service_account.name,
        home=plan.service_account.home,
        shell=plan.service_account.shell,
        system=plan.service_account.system,
        create_home=True,
        name="Create Taskman system account",
    )
    for directory in plan.directories:
        files.directory(
            directory.path,
            user=directory.owner,
            group=directory.group,
            mode=directory.mode,
            name=f"Create {directory.path}",
        )
    files.put(
        StringIO(plan.unattended_updates),
        "/etc/apt/apt.conf.d/52taskman-unattended-upgrades",
        user="root",
        group="root",
        mode=0o644,
        add_deploy_dir=False,
        name="Configure unattended upgrades",
    )
    files.put(
        StringIO(plan.provisioning_marker_content),
        plan.provisioning_marker_path,
        user="root",
        group="root",
        mode=0o600,
        add_deploy_dir=False,
        name="Install Taskman provisioning marker",
    )
    server.service(
        "unattended-upgrades",
        running=True,
        enabled=True,
        name="Enable unattended upgrades",
    )
    declare_firewall(config)
    return plan


def _taskman_directories(config: EnvironmentConfig) -> tuple[ManagedDirectory, ...]:
    return (
        ManagedDirectory(config.install_root.as_posix(), "root", "root", 0o755),
        ManagedDirectory(config.release_root.as_posix(), "root", "root", 0o755),
        ManagedDirectory(config.deployment_root.as_posix(), "root", "root", 0o700),
        ManagedDirectory("/etc/taskman", "root", "taskman", 0o750),
        ManagedDirectory("/var/lib/taskman", "taskman", "taskman", 0o700),
        ManagedDirectory(config.backup_root.as_posix(), "root", "root", 0o700),
        ManagedDirectory("/var/lock/taskman", "root", "root", 0o700),
        ManagedDirectory("/usr/local/lib/taskman", "root", "root", 0o755),
    )


__all__ = [
    "BASELINE_PACKAGES",
    "TASKMAN_DIRECTORIES",
    "BaselinePlan",
    "ManagedDirectory",
    "ServiceAccount",
    "build_baseline_plan",
    "declare_baseline",
]
