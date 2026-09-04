"""Taskman unit, protected environment, and backup-timer convergence contract."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import shlex
from typing import Callable, Protocol

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..pyinfra import conditional_convergence
from ..remote import ChangeSet, CommandResult, Remote
from .backups import BackupServiceContract, backup_service_contract, render_backup_timer


_OPS_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SystemdAsset:
    source: Path | None
    content: str | None
    destination: str
    staged_destination: str
    mode: int


@dataclass(frozen=True)
class ProtectedEnvironmentFile:
    path: str
    owner: str
    group: str
    mode: int


@dataclass(frozen=True)
class SystemdPlan:
    managed_root: str
    assets: tuple[SystemdAsset, ...]
    runtime_environment: ProtectedEnvironmentFile
    backup_environment: ProtectedEnvironmentFile
    backup_environment_content: str
    staged_validation: tuple[str, ...]
    reload_daemon_only_after_change: bool
    enable_without_start: tuple[str, ...]
    enable_and_start: tuple[str, ...]


class SystemdOperations(Protocol):
    def staged_asset(self, asset: SystemdAsset) -> None: ...

    def staged_backup_environment(self, content: str, destination: str) -> None: ...

    def validate_install_reload_and_enable(self) -> None: ...


def build_systemd_plan(
    config: EnvironmentConfig,
    *,
    calendar_validator: Callable[[str], None] | None = None,
) -> SystemdPlan:
    """Build the unit and timer desired state without starting Taskman."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("systemd plan requires an environment configuration")
    backup_contract = backup_service_contract(config)
    backup_timer = render_backup_timer(config, calendar_validator=calendar_validator)
    backup_assets = _backup_assets(config, backup_contract, backup_timer)
    assets = (
        SystemdAsset(
            None,
            render_taskman_service(config),
            "/etc/systemd/system/taskman.service",
            "/etc/taskman/taskman.service.staged",
            0o644,
        ),
        *backup_assets,
    )
    return SystemdPlan(
        managed_root=config.managed_root.as_posix(),
        assets=assets,
        runtime_environment=ProtectedEnvironmentFile("/etc/taskman/taskman.env", "root", "root", 0o600),
        backup_environment=ProtectedEnvironmentFile(
            "/etc/taskman/taskman-backup.env", "root", "root", 0o600
        ),
        backup_environment_content=_backup_environment(backup_contract),
        staged_validation=(
            "systemd-analyze",
            "verify",
            "--root=managed-staging-root",
            "taskman.service",
            "taskman-backup.service",
            "taskman-backup.timer",
        ),
        reload_daemon_only_after_change=True,
        enable_without_start=("taskman.service",),
        enable_and_start=("taskman-backup.timer",),
    )


def converge_systemd(
    config: EnvironmentConfig,
    *,
    calendar_validator: Callable[[str], None] | None = None,
    operations: SystemdOperations | None = None,
) -> SystemdPlan:
    """Declare staged validation, changed-unit reload, and safe unit enablement."""

    plan = build_systemd_plan(config, calendar_validator=calendar_validator)
    backend = operations or _PyinfraSystemdOperations(plan)
    for asset in plan.assets:
        backend.staged_asset(asset)
    backend.staged_backup_environment(plan.backup_environment_content, plan.backup_environment.path)
    backend.validate_install_reload_and_enable()
    return plan


def apply_systemd_assets(remote: Remote, plan: SystemdPlan) -> ChangeSet:
    """Execute the staged unit transaction with a truthful change marker."""

    if not isinstance(plan, SystemdPlan):
        raise TypeError("systemd convergence requires a plan")
    result = remote.run(("sh", "-c", render_systemd_install_script(plan)), sudo=True)
    if not result.succeeded:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "systemd",
            "unable to validate and converge Taskman systemd assets",
            changed=False,
            next_action="inspect staged units and systemd state before retrying",
        )
    return ChangeSet(changed=_changed_result(result.stdout), operations=("systemd",))


def install_runtime_environment(remote: Remote, content: bytes) -> ChangeSet:
    """Install rendered Taskman runtime secrets through protected standard input.

    The byte comparison and exact owner/mode check make repeated installation
    a no-op when the secret file is already correct, while a private generated
    staging file avoids exposing content in a process argument or log.
    """

    return _install_protected_environment(remote, content, "/etc/taskman/taskman.env")


def render_taskman_service(config: EnvironmentConfig) -> str:
    """Render the Taskman unit with the validated managed root everywhere."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("Taskman unit rendering requires an environment configuration")
    template = (_OPS_ROOT / "systemd" / "taskman.service").read_text(encoding="utf-8")
    marker = "{{TASKMAN_MANAGED_ROOT}}"
    if template.count(marker) != 5:
        raise RuntimeError("Taskman unit template has an unexpected managed-root contract")
    return template.replace(marker, config.managed_root.as_posix())


def render_backup_service(config: EnvironmentConfig) -> str:
    """Render every configurable sandbox path in the backup unit."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("backup unit rendering requires an environment configuration")
    template = (_OPS_ROOT / "systemd" / "taskman-backup.service").read_text(encoding="utf-8")
    markers = {
        "{{TASKMAN_BACKUP_ROOT}}": config.backup_root.as_posix(),
        "{{TASKMAN_DEPLOYMENT_ROOT}}": config.deployment_root.as_posix(),
    }
    if any(template.count(marker) != 1 for marker in markers):
        raise RuntimeError("backup unit template has an unexpected managed-root contract")
    for marker, value in markers.items():
        template = template.replace(marker, value)
    return template


def render_owned_file_repair_script(
    staged: str,
    destination: str,
    mode: int,
    *,
    owner: str | None = "root",
    group: str | None = "root",
) -> str:
    """Render an executable byte-and-metadata repair transaction.

    Passing no owner/group is test-only support for an unprivileged local
    fixture; production callers retain root ownership and group explicitly.
    """

    return "set -eu; changed=0; " + _owned_file_repair_body(
        staged, destination, mode, owner=owner, group=group
    ) + " printf 'changed=%s\\n' \"$changed\""


def _owned_file_repair_body(
    staged: str,
    destination: str,
    mode: int,
    *,
    owner: str | None,
    group: str | None,
) -> str:
    if not isinstance(staged, str) or not isinstance(destination, str):
        raise TypeError("systemd asset paths must be strings")
    if (owner is None) != (group is None):
        raise ValueError("systemd asset owner and group must be provided together")
    if type(mode) is not int or not 0 <= mode <= 0o777:
        raise ValueError("systemd asset mode is invalid")
    staged_path = shlex.quote(staged)
    destination_path = shlex.quote(destination)
    if owner is None:
        state = f"$(stat --format='%a' {destination_path} 2>/dev/null || true)"
        expected = f"{mode:o}"
        install_owner = ""
    else:
        state = f"$(stat --format='%U:%G:%a' {destination_path} 2>/dev/null || true)"
        expected = f"{owner}:{group}:{mode:o}"
        install_owner = f"-o {shlex.quote(owner)} -g {shlex.quote(group)} "
    return (
        f"state={state}; if ! cmp -s {staged_path} {destination_path} || "
        f"[ \"$state\" != {shlex.quote(expected)} ]; then "
        f"install -D {install_owner}-m {mode:o} {staged_path} {destination_path}; changed=1; fi;"
    )


def _install_protected_environment(remote: Remote, content: bytes, destination: str) -> ChangeSet:
    if not isinstance(content, bytes) or not content:
        raise ValueError("runtime environment content must be non-empty bytes")
    result = remote.run(
        (
            "sh",
            "-c",
            "set -eu; changed=0; stage=$(mktemp /tmp/taskman-environment.XXXXXX); "
            "trap 'rm -f \"$stage\"' EXIT HUP INT TERM; umask 077; cat > \"$stage\"; "
            "state=$(stat --format='%U:%G:%a' \"$1\" 2>/dev/null || true); "
            "if ! cmp -s \"$stage\" \"$1\" || [ \"$state\" != root:root:600 ]; then "
            "install -o root -g root -m 0600 \"$stage\" \"$1\"; changed=1; fi; "
            "printf 'changed=%s\\n' \"$changed\"",
            "taskman-runtime-environment",
            destination,
        ),
        sudo=True,
        stdin=content,
        sensitive=True,
    )
    _require_success(result)
    changed = _changed_result(result.stdout)
    return ChangeSet(changed=changed, operations=("runtime-environment",) if changed else ())


def _require_success(result: CommandResult) -> None:
    if result.succeeded:
        return
    raise OpsError(
        ExitStatus.SECRET,
        "systemd",
        "unable to install protected runtime environment",
        changed=False,
        next_action="verify the protected runtime environment and retry",
    )


def _backup_assets(
    config: EnvironmentConfig,
    contract: BackupServiceContract,
    timer: str,
) -> tuple[SystemdAsset, ...]:
    command, service, _timer = contract.assets
    return (
        SystemdAsset(command.source, None, str(command.destination), "/etc/taskman/taskman-backup.staged", command.mode),
        SystemdAsset(
            None,
            render_backup_service(config),
            str(service.destination),
            "/etc/taskman/taskman-backup.service.staged",
            service.mode,
        ),
        SystemdAsset(None, timer, str(_timer.destination), "/etc/taskman/taskman-backup.timer.staged", _timer.mode),
    )


def _backup_environment(contract: BackupServiceContract) -> str:
    return "".join(f"{key}={value}\n" for key, value in contract.environment.items())


class _PyinfraSystemdOperations:
    """Stage all assets first, then validate and atomically install changed files."""

    def __init__(self, plan: SystemdPlan) -> None:
        self._plan = plan

    def staged_asset(self, asset: SystemdAsset) -> None:
        from pyinfra.operations import files

        source: Path | StringIO
        source = asset.source if asset.source is not None else StringIO(asset.content or "")
        files.put(
            source,
            asset.staged_destination,
            user="root",
            group="root",
            mode=asset.mode,
            add_deploy_dir=False,
        )

    def staged_backup_environment(self, content: str, destination: str) -> None:
        from pyinfra.operations import files

        files.put(
            StringIO(content),
            f"{destination}.staged",
            user="root",
            group="root",
            mode=0o600,
            add_deploy_dir=False,
        )

    def validate_install_reload_and_enable(self) -> None:
        conditional_convergence(
            probe=render_systemd_install_probe(self._plan),
            script=render_systemd_install_script(self._plan),
        )


def render_systemd_install_probe(plan: SystemdPlan) -> str:
    """Return a no-mutation probe for owned assets and required service state."""

    if not isinstance(plan, SystemdPlan):
        raise TypeError("systemd convergence probe requires a plan")
    checks = [
        _owned_file_matches(asset.staged_destination, asset.destination, asset.mode)
        for asset in plan.assets
    ]
    checks.append(
        _owned_file_matches(
            str(plan.backup_environment.path) + ".staged",
            plan.backup_environment.path,
            plan.backup_environment.mode,
        )
    )
    checks.extend(
        (
            "systemctl is-enabled --quiet taskman.service",
            "systemctl is-enabled --quiet taskman-backup.timer",
            "systemctl is-active --quiet taskman-backup.timer",
        )
    )
    return (
        "set -eu; if "
        + " && ".join(checks)
        + "; then printf 'changed=0\\n'; else printf 'changed=1\\n'; fi"
    )


def render_systemd_install_script(plan: SystemdPlan) -> str:
    """Validate, install, and conditionally activate staged owned assets."""

    if not isinstance(plan, SystemdPlan):
        raise TypeError("systemd install script requires a plan")
    validation_current = f'"$root"{shlex.quote(plan.managed_root + "/current/bin")}'
    copies = " ".join(
        f"install -D -m {asset.mode:o} {asset.staged_destination} \"$root{asset.destination}\";"
        for asset in plan.assets
    )
    installs = " ".join(
        _owned_file_repair_body(
            asset.staged_destination,
            asset.destination,
            asset.mode,
            owner="root",
            group="root",
        )
        for asset in plan.assets
    )
    return (
        "set -eu; root=$(mktemp -d); trap 'rm -rf \"$root\"' EXIT; "
        f"install -d -m 0755 \"$root/etc/systemd/system\" {validation_current} \"$root/usr/local/lib/taskman\"; "
        f"printf '#!/bin/sh\\nexit 0\\n' > {validation_current}/migrate; "
        f"cp {validation_current}/migrate {validation_current}/server; "
        f"cp {validation_current}/migrate \"$root/usr/local/lib/taskman/taskman-backup\"; "
        f"chmod 0755 {validation_current}/migrate {validation_current}/server \"$root/usr/local/lib/taskman/taskman-backup\"; "
        "for unit in sysinit.target network-online.target timers.target multi-user.target; do "
        "printf '[Unit]\\nDescription=validation fixture\\n' > \"$root/etc/systemd/system/$unit\"; done; "
        "printf '[Unit]\\nDescription=validation fixture\\n[Service]\\nType=oneshot\\nExecStart=/bin/true\\n' "
        "> \"$root/etc/systemd/system/postgresql.service\"; "
        f"{copies} "
        "systemd-analyze verify --root=\"$root\" taskman.service taskman-backup.service taskman-backup.timer; "
        "changed=0; "
        f"{installs} "
        f"{_owned_file_repair_body(str(plan.backup_environment.path) + '.staged', plan.backup_environment.path, 0o600, owner='root', group='root')} "
        "if [ \"$changed\" -eq 1 ]; then systemctl daemon-reload; fi; "
        "if ! systemctl is-enabled --quiet taskman.service; then systemctl enable taskman.service; changed=1; fi; "
        "if ! systemctl is-enabled --quiet taskman-backup.timer; then systemctl enable --now taskman-backup.timer; changed=1; "
        "elif ! systemctl is-active --quiet taskman-backup.timer; then systemctl start taskman-backup.timer; changed=1; fi; "
        "printf 'changed=%s\\n' \"$changed\""
    )


def _owned_file_matches(staged: str, destination: str, mode: int) -> str:
    state = f"$(stat --format='%U:%G:%a' {shlex.quote(destination)} 2>/dev/null || true)"
    expected = shlex.quote(f"root:root:{mode:o}")
    return (
        f"cmp -s {shlex.quote(staged)} {shlex.quote(destination)} && "
        f"[ \"{state}\" = {expected} ]"
    )


def _changed_result(stdout: str) -> bool:
    markers = [line.removeprefix("changed=") for line in stdout.splitlines() if line.startswith("changed=")]
    if markers == ["0"]:
        return False
    if markers == ["1"]:
        return True
    raise OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "systemd",
        "systemd convergence returned an invalid change result",
        changed=False,
        next_action="inspect systemd convergence output before retrying",
    )


__all__ = [
    "ProtectedEnvironmentFile",
    "SystemdAsset",
    "SystemdPlan",
    "apply_systemd_assets",
    "build_systemd_plan",
    "converge_systemd",
    "install_runtime_environment",
    "render_backup_service",
    "render_owned_file_repair_script",
    "render_systemd_install_probe",
    "render_systemd_install_script",
    "render_taskman_service",
]
