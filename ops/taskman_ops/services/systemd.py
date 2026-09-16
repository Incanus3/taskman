"""Systemd desired state and the protected runtime-file boundary."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import shlex
from typing import TYPE_CHECKING, Callable

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..remote import ChangeSet, CommandResult, Remote
from .backups import BackupServiceContract, backup_service_contract, render_backup_timer

if TYPE_CHECKING:
    from ..provisioning import ProvisioningInputs


_OPS_ROOT = Path(__file__).resolve().parents[2]
_PROTECTED_WRITE_CHANGED = 3


@dataclass(frozen=True)
class SystemdAsset:
    content: bytes
    destination: str
    mode: int
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes):
            raise TypeError("systemd asset content must be exact bytes")


@dataclass(frozen=True)
class SystemdPlan:
    assets: tuple[SystemdAsset, ...]
    backup_environment_content: bytes
    backup_environment_path: str
    enable_without_start: tuple[str, ...]
    enable_and_start: tuple[str, ...]


def build_systemd_plan(
    config: EnvironmentConfig,
    *,
    calendar_validator: Callable[[str], None] | None = None,
) -> SystemdPlan:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("systemd plan requires an environment configuration")
    backup_contract = backup_service_contract(config)
    backup_timer = render_backup_timer(config, calendar_validator=calendar_validator)
    return SystemdPlan(
        assets=(
            SystemdAsset(
                render_taskman_service(config).encode("utf-8"),
                "/etc/systemd/system/taskman.service",
                0o644,
            ),
            *_backup_assets(config, backup_contract, backup_timer),
        ),
        backup_environment_content=_backup_environment(backup_contract).encode("utf-8"),
        backup_environment_path="/etc/taskman/taskman-backup.env",
        enable_without_start=("taskman.service",),
        enable_and_start=("taskman-backup.timer",),
    )


def declare_systemd(inputs: ProvisioningInputs) -> SystemdPlan:
    """Add built-in unit/file, daemon-reload, and enablement operations."""

    from pyinfra.operations import files, server, systemd

    plan = inputs.systemd_plan
    scheduler_create = inputs.scheduler_create
    changed_unit_files: list[object] = []
    for asset in plan.assets:
        if _scheduler_resource(asset.destination) and asset.destination not in scheduler_create:
            # A pre-existing scheduler is never a generic convergence target.
            # Its controlled pause/wait/checksum refresh belongs to genesis.
            continue
        result = files.put(
            BytesIO(asset.content),
            asset.destination,
            user="root",
            group="root",
            mode=format(asset.mode, "o"),
            add_deploy_dir=False,
            name=f"Install {asset.destination}",
        )
        if asset.destination.startswith("/etc/systemd/system/"):
            changed_unit_files.append(result)
        _verify_installed_checksum(server, asset)
    if plan.backup_environment_path in scheduler_create:
        files.put(
            BytesIO(plan.backup_environment_content),
            plan.backup_environment_path,
            user="root",
            group="root",
            mode="600",
            add_deploy_dir=False,
            name="Install Taskman backup environment",
        )
    systemd.daemon_reload(
        name="Reload systemd daemon",
        _if=lambda: any(bool(getattr(result, "did_change")()) for result in changed_unit_files),
    )
    for service in plan.enable_without_start:
        systemd.service(service, running=None, enabled=True, name=f"Enable {service}")
    create_all_scheduler_resources = scheduler_create == frozenset(
        {
            "/usr/local/lib/taskman/taskman-backup.pyz",
            "/etc/systemd/system/taskman-backup.service",
            "/etc/systemd/system/taskman-backup.timer",
            "/etc/taskman/taskman-backup.env",
        }
    )
    for service in plan.enable_and_start:
        timer_path = "/etc/systemd/system/taskman-backup.timer"
        # A partially present scheduler may contain an earlier compatible
        # helper.  Generic convergence may lay down its missing timer, but
        # must not start it before genesis owns the lifecycle-locked pause/wait/refresh
        # sequence.  Only an entirely absent scheduler has no old executable
        # that this operation could accidentally launch.
        if service != "taskman-backup.timer" or (
            timer_path in scheduler_create and create_all_scheduler_resources
        ):
            systemd.service(service, running=True, enabled=True, name=f"Enable and start {service}")
        elif timer_path in scheduler_create:
            # A newly created timer beside an older scheduler must be enabled,
            # but not started until genesis has refreshed the executable while
            # holding the lifecycle lock.
            systemd.service(service, running=None, enabled=True, name=f"Enable {service} without starting")
    return plan


def _scheduler_resource(destination: str) -> bool:
    return destination in {
        "/usr/local/lib/taskman/taskman-backup.pyz",
        "/etc/systemd/system/taskman-backup.service",
        "/etc/systemd/system/taskman-backup.timer",
    }


def install_runtime_environment(remote: Remote, content: bytes) -> ChangeSet:
    """Install runtime secrets using protected standard input, not pyinfra logs."""

    return _install_protected_environment(remote, content, "/etc/taskman/taskman.env")


def render_taskman_service(config: EnvironmentConfig) -> str:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("Taskman unit rendering requires an environment configuration")
    template = (_OPS_ROOT / "systemd" / "taskman.service").read_text(encoding="utf-8")
    marker = "{{TASKMAN_INSTALL_ROOT}}"
    if template.count(marker) != 5:
        raise RuntimeError("Taskman unit template has an unexpected install-root contract")
    return template.replace(marker, config.install_root.as_posix())


def _quote_systemd_path_list_word(path: str) -> str:
    """Quote a prevalidated path-list token; root validation already rejects backslashes."""

    if "'" not in path and '"' not in path:
        return path
    escaped = path.replace('"', '\\"')
    return f'"{escaped}"'


def render_backup_service(config: EnvironmentConfig) -> str:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("backup unit rendering requires an environment configuration")
    template = (_OPS_ROOT / "systemd" / "taskman-backup.service").read_text(encoding="utf-8")
    markers = {
        "{{TASKMAN_BACKUP_ROOT}}": _quote_systemd_path_list_word(config.backup_root.as_posix()),
        "{{TASKMAN_INSTALL_ROOT}}": config.install_root.as_posix(),
    }
    if any(template.count(marker) != 1 for marker in markers):
        raise RuntimeError("backup unit template has an unexpected install-root contract")
    for marker, value in markers.items():
        template = template.replace(marker, value)
    return template


def _install_protected_environment(remote: Remote, content: bytes, destination: str) -> ChangeSet:
    if not isinstance(content, bytes) or not content:
        raise ValueError("runtime environment content must be non-empty bytes")
    try:
        result = remote.run(
            (
                "sh",
                "-c",
                "exec >/dev/null 2>&1; set -eu; changed=0; stage=; "
                "finish() { status=$?; trap - EXIT HUP INT TERM; cleanup_status=0; "
                "if [ -n \"$stage\" ]; then rm -f -- \"$stage\" || cleanup_status=1; fi; "
                "if [ \"$status\" -ne 0 ] || [ \"$cleanup_status\" -ne 0 ]; then exit 1; fi; "
                f"if [ \"$changed\" -eq 1 ]; then exit {_PROTECTED_WRITE_CHANGED}; fi; exit 0; }}; "
                "trap finish EXIT; trap 'exit 1' HUP INT TERM; umask 077; "
                "stage=$(mktemp /tmp/taskman-environment.XXXXXX); cat > \"$stage\"; "
                "state=$(stat --format='%U:%G:%a' \"$1\" 2>/dev/null || true); "
                "if ! cmp -s \"$stage\" \"$1\" || [ \"$state\" != root:root:600 ]; then "
                "install -o root -g root -m 0600 \"$stage\" \"$1\"; changed=1; fi",
                "taskman-runtime-environment",
                destination,
            ),
            sudo=True,
            stdin=content,
            sensitive=True,
        )
    except OpsError as error:
        error.changed = True
        raise
    changed = _changed_result(result)
    return ChangeSet(changed=changed, operations=("Install protected runtime environment",) if changed else ())


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


def _changed_result(result: CommandResult) -> bool:
    if result.returncode == _PROTECTED_WRITE_CHANGED:
        return True
    if result.returncode == 0:
        return False
    try:
        _require_success(result)
    except OpsError as error:
        error.changed = True
        raise
    return False


def _backup_assets(
    config: EnvironmentConfig,
    contract: BackupServiceContract,
    timer: str,
) -> tuple[SystemdAsset, ...]:
    command, service, _timer = contract.assets
    return (
        SystemdAsset(
            command.content,
            str(command.destination),
            command.mode,
            sha256=command.sha256,
        ),
        SystemdAsset(b"", f"{config.install_root.as_posix()}/lifecycle.lock", 0o600),
        SystemdAsset(render_backup_service(config).encode("utf-8"), str(service.destination), service.mode),
        SystemdAsset(timer.encode("utf-8"), str(_timer.destination), _timer.mode),
    )


def _verify_installed_checksum(server: object, asset: SystemdAsset) -> None:
    """Declare an on-host checksum check for the immutable scheduled zipapp."""

    if asset.sha256 is None:
        return
    if re.fullmatch(r"[0-9a-f]{64}", asset.sha256) is None:
        raise ValueError("systemd asset checksum is invalid")
    destination = shlex.quote(asset.destination)
    command = f"test \"$(sha256sum -- {destination} | awk '{{print $1}}')\" = {asset.sha256}"
    server.shell(  # type: ignore[attr-defined]
        commands=command,
        name=f"Verify checksum for {asset.destination}",
        _sudo=True,
    )


def _backup_environment(contract: BackupServiceContract) -> str:
    return "".join(f"{key}={value}\n" for key, value in contract.environment.items())


__all__ = [
    "SystemdAsset",
    "SystemdPlan",
    "build_systemd_plan",
    "declare_systemd",
    "install_runtime_environment",
    "render_backup_service",
    "render_taskman_service",
]
