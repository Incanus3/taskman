"""Declarative, independently callable desired state for a supported host baseline.

The capability intentionally declares stable state only.  It never inspects a
package, account, or file created by an earlier operation, so pyinfra's
prepare phase cannot make a decision from mutable same-deploy facts.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import StringIO
import shlex
from typing import Protocol

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..remote import ChangeSet, Remote


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
    """One directory whose complete ownership contract belongs to Taskman."""

    path: str
    owner: str
    group: str
    mode: int


@dataclass(frozen=True)
class ServiceAccount:
    """The unprivileged runtime account, without login or administrative access."""

    name: str
    home: str
    shell: str
    system: bool


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


@dataclass(frozen=True)
class BaselinePlan:
    """Complete immutable input to the baseline's declarative operations."""

    packages: tuple[str, ...]
    service_account: ServiceAccount
    directories: tuple[ManagedDirectory, ...]
    unattended_updates: str
    ssh_policy_changes: tuple[str, ...] = ()


class BaselineOperations(Protocol):
    """Small operation surface shared by pyinfra and focused contract tests."""

    def packages(self, packages: tuple[str, ...]) -> None: ...

    def user(self, account: ServiceAccount) -> None: ...

    def directory(self, directory: ManagedDirectory) -> None: ...

    def file(self, path: str, content: str, *, owner: str, group: str, mode: int) -> None: ...

    def service(self, name: str, *, running: bool, enabled: bool) -> None: ...


def build_baseline_plan(config: EnvironmentConfig) -> BaselinePlan:
    """Describe the baseline without connecting to or inspecting a host."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("baseline plan requires an environment configuration")
    return BaselinePlan(
        packages=BASELINE_PACKAGES,
        service_account=ServiceAccount("taskman", "/var/lib/taskman", "/usr/sbin/nologin", True),
        directories=_taskman_directories(config),
        unattended_updates=_UNATTENDED_UPDATES,
    )


def converge_baseline(config: EnvironmentConfig, *, operations: BaselineOperations | None = None) -> BaselinePlan:
    """Declare the complete repeatable host baseline through pyinfra operations.

    ``operations`` is injectable only to make the desired-state boundary
    observable in unit tests.  Production callers omit it and receive the
    pyinfra adapter.  No operation depends on a fact changed by another one.
    """

    plan = build_baseline_plan(config)
    backend = operations or _PyinfraBaselineOperations()
    backend.packages(plan.packages)
    backend.user(plan.service_account)
    for directory in plan.directories:
        backend.directory(directory)
    backend.file(
        "/etc/apt/apt.conf.d/52taskman-unattended-upgrades",
        plan.unattended_updates,
        owner="root",
        group="root",
        mode=0o644,
    )
    backend.service("unattended-upgrades", running=True, enabled=True)
    return plan


def converge_baseline_host(remote: Remote, config: EnvironmentConfig) -> ChangeSet:
    """Execute the package/user/file baseline with truthful change reporting.

    This execution-time adapter is intentionally small enough for the
    controlled Ubuntu proof and host workflows.  It uses the same immutable
    plan as the pyinfra adapter, but returns the script's explicit result so a
    no-op rerun is distinguishable from one that repaired owned state.
    Systemd activation remains a VM-only acceptance concern handled by the
    declarative pyinfra service operation above.
    """

    plan = build_baseline_plan(config)
    result = remote.run(("sh", "-c", render_baseline_convergence_script(plan)), sudo=True)
    if result.returncode == int(ExitStatus.SAFETY):
        raise OpsError(
            ExitStatus.SAFETY,
            "baseline",
            "existing Taskman service account is incompatible with the least-authority contract",
            changed=False,
            next_action="inspect the taskman account shell, home, primary group, and supplementary groups before retrying",
        )
    if not result.succeeded:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "baseline",
            "unable to converge supported host baseline",
            changed=False,
            next_action="inspect the Ubuntu package, account, and owned-file state before retrying",
        )
    changed = _changed_result(result.stdout, "baseline")
    return ChangeSet(changed=changed, operations=("baseline",))


def render_baseline_convergence_script(
    plan: BaselinePlan,
    *,
    unattended_path: str = "/etc/apt/apt.conf.d/52taskman-unattended-upgrades",
    provisioning_marker_path: str = _PROVISIONING_MARKER_PATH,
) -> str:
    """Render the conditional native adapter used by real controlled proofs."""

    if not isinstance(plan, BaselinePlan):
        raise TypeError("baseline script requires a baseline plan")
    if not isinstance(unattended_path, str) or not unattended_path.startswith("/"):
        raise ValueError("unattended-upgrades path must be absolute")
    if not isinstance(provisioning_marker_path, str) or not provisioning_marker_path.startswith("/"):
        raise ValueError("provisioning marker path must be absolute")
    packages = " ".join(shlex.quote(package) for package in plan.packages)
    package_checks = "\n".join(
        "dpkg-query --show --showformat='${db:Status-Status}' "
        f"{shlex.quote(package)} 2>/dev/null | grep -qx installed || missing=1"
        for package in plan.packages
    )
    directories = "\n".join(
        _directory_convergence(directory) for directory in plan.directories
    )
    encoded_updates = base64.b64encode(plan.unattended_updates.encode("utf-8")).decode("ascii")
    account = plan.service_account
    update_path = unattended_path
    return f"""set -eu
export DEBIAN_FRONTEND=noninteractive
changed=0
marker=$(mktemp /tmp/taskman-provisioning.XXXXXX)
printf %s {shlex.quote(_PROVISIONING_MARKER_CONTENT)} > "$marker"
marker_state=$(stat --format='%U:%G:%a' {shlex.quote(provisioning_marker_path)} 2>/dev/null || true)
if ! cmp -s "$marker" {shlex.quote(provisioning_marker_path)} || [ "$marker_state" != root:root:600 ]; then
  install -D -o root -g root -m 0600 "$marker" {shlex.quote(provisioning_marker_path)}
  changed=1
fi
rm -f "$marker"
unsafe_account() {{ echo 'incompatible taskman service account' >&2; exit {int(ExitStatus.SAFETY)}; }}
missing=0
{package_checks}
if [ \"$missing\" -eq 1 ]; then
  apt-get update >/dev/null
  apt-get install --yes --no-install-recommends {packages} >/dev/null
  changed=1
fi
if id {shlex.quote(account.name)} >/dev/null 2>&1; then
  getent group {shlex.quote(account.name)} >/dev/null || unsafe_account
  account_home=$(getent passwd {shlex.quote(account.name)} | awk -F: 'NR == 1 {{ print $6 }}')
  account_shell=$(getent passwd {shlex.quote(account.name)} | awk -F: 'NR == 1 {{ print $7 }}')
  account_primary_group=$(id -gn {shlex.quote(account.name)})
  account_groups=$(id -Gn {shlex.quote(account.name)} | tr ' ' '\\n' | LC_ALL=C sort | tr '\\n' ' ')
  [ "$account_home" = {shlex.quote(account.home)} ] || unsafe_account
  [ "$account_shell" = {shlex.quote(account.shell)} ] || unsafe_account
  [ "$account_primary_group" = {shlex.quote(account.name)} ] || unsafe_account
  [ "$account_groups" = {shlex.quote(account.name + ' ')} ] || unsafe_account
else
  if ! getent group {shlex.quote(account.name)} >/dev/null; then groupadd --system {shlex.quote(account.name)}; changed=1; fi
  useradd --system --gid {shlex.quote(account.name)} --home {shlex.quote(account.home)} --create-home --shell {shlex.quote(account.shell)} {shlex.quote(account.name)}
  changed=1
fi
{directories}
stage=$(mktemp /tmp/taskman-unattended.XXXXXX)
trap 'rm -f \"$stage\"' EXIT HUP INT TERM
printf %s {shlex.quote(encoded_updates)} | base64 --decode > \"$stage\"
state=$(stat --format='%U:%G:%a' {shlex.quote(update_path)} 2>/dev/null || true)
if ! cmp -s \"$stage\" {shlex.quote(update_path)} || [ \"$state\" != root:root:644 ]; then
  install -D -o root -g root -m 0644 \"$stage\" {shlex.quote(update_path)}
  changed=1
fi
printf 'changed=%s\\n' \"$changed\"
"""


def _directory_convergence(directory: ManagedDirectory) -> str:
    path = shlex.quote(directory.path)
    expected = f"{directory.owner}:{directory.group}:{directory.mode:o}"
    return (
        f"state=$(stat --format='%U:%G:%a' {path} 2>/dev/null || true); "
        f"if [ \"$state\" != {shlex.quote(expected)} ]; then "
        f"install -d -o {shlex.quote(directory.owner)} -g {shlex.quote(directory.group)} "
        f"-m {directory.mode:o} {path}; changed=1; fi"
    )


def _changed_result(stdout: str, stage: str) -> bool:
    values = [line.removeprefix("changed=") for line in stdout.splitlines() if line.startswith("changed=")]
    if values == ["0"]:
        return False
    if values == ["1"]:
        return True
    raise OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        stage,
        "host convergence returned an invalid change result",
        changed=False,
        next_action="inspect the host convergence command output before retrying",
    )


def _taskman_directories(config: EnvironmentConfig) -> tuple[ManagedDirectory, ...]:
    """Bind all configurable roots to the same owner/mode policy.

    The exported constant preserves the default topology contract, while this
    constructor prevents a validated non-default root from being silently
    ignored by baseline convergence.
    """

    return (
        ManagedDirectory(config.managed_root.as_posix(), "root", "root", 0o755),
        ManagedDirectory(config.release_root.as_posix(), "root", "root", 0o755),
        ManagedDirectory(config.deployment_root.as_posix(), "root", "root", 0o700),
        ManagedDirectory("/etc/taskman", "root", "taskman", 0o750),
        ManagedDirectory("/var/lib/taskman", "taskman", "taskman", 0o700),
        ManagedDirectory(config.backup_root.as_posix(), "root", "root", 0o700),
        ManagedDirectory("/var/lock/taskman", "root", "root", 0o700),
        ManagedDirectory("/usr/local/lib/taskman", "root", "root", 0o755),
    )


class _PyinfraBaselineOperations:
    """Adapter that keeps pyinfra imports out of capability consumers."""

    def packages(self, packages: tuple[str, ...]) -> None:
        from pyinfra.operations import apt

        apt.packages(packages=list(packages))

    def user(self, account: ServiceAccount) -> None:
        from pyinfra.operations import server

        server.user(
            account.name,
            home=account.home,
            shell=account.shell,
            system=account.system,
            create_home=True,
        )

    def directory(self, directory: ManagedDirectory) -> None:
        from pyinfra.operations import files

        files.directory(
            directory.path,
            user=directory.owner,
            group=directory.group,
            mode=directory.mode,
        )

    def file(self, path: str, content: str, *, owner: str, group: str, mode: int) -> None:
        from pyinfra.operations import files

        files.put(
            StringIO(content),
            path,
            user=owner,
            group=group,
            mode=mode,
            add_deploy_dir=False,
        )

    def service(self, name: str, *, running: bool, enabled: bool) -> None:
        from pyinfra.operations import server

        server.service(name, running=running, enabled=enabled)


__all__ = [
    "BASELINE_PACKAGES",
    "TASKMAN_DIRECTORIES",
    "BaselinePlan",
    "ManagedDirectory",
    "ServiceAccount",
    "build_baseline_plan",
    "converge_baseline",
    "converge_baseline_host",
    "render_baseline_convergence_script",
]
