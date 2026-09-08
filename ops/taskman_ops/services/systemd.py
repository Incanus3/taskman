"""Systemd desired state and the protected runtime-file boundary."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO, StringIO
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


@dataclass(frozen=True)
class SystemdAsset:
    source: Path | None
    content: str | None
    destination: str
    mode: int
    binary_content: bytes | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class SystemdPlan:
    assets: tuple[SystemdAsset, ...]
    backup_environment_content: str
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
            SystemdAsset(None, render_taskman_service(config), "/etc/systemd/system/taskman.service", 0o644),
            *_backup_assets(config, backup_contract, backup_timer),
        ),
        backup_environment_content=_backup_environment(backup_contract),
        backup_environment_path="/etc/taskman/taskman-backup.env",
        enable_without_start=("taskman.service",),
        enable_and_start=("taskman-backup.timer",),
    )


def declare_systemd(inputs: ProvisioningInputs) -> SystemdPlan:
    """Add built-in unit/file, daemon-reload, and enablement operations."""

    from pyinfra.operations import files, server, systemd

    plan = build_systemd_plan(inputs.config)
    changed_unit_files: list[object] = []
    for asset in plan.assets:
        source: Path | StringIO | BytesIO
        if asset.source is not None:
            source = asset.source
        elif asset.binary_content is not None:
            source = BytesIO(asset.binary_content)
        else:
            source = StringIO(asset.content or "")
        result = files.put(
            source,
            asset.destination,
            user="root",
            group="root",
            mode=asset.mode,
            add_deploy_dir=False,
            name=f"Install {asset.destination}",
        )
        if asset.destination.startswith("/etc/systemd/system/"):
            changed_unit_files.append(result)
        _verify_installed_checksum(server, asset)
    files.put(
        StringIO(plan.backup_environment_content),
        plan.backup_environment_path,
        user="root",
        group="root",
        mode=0o600,
        add_deploy_dir=False,
        name="Install Taskman backup environment",
    )
    systemd.daemon_reload(
        name="Reload systemd daemon",
        _if=lambda: any(bool(getattr(result, "did_change")()) for result in changed_unit_files),
    )
    for service in plan.enable_without_start:
        systemd.service(service, running=None, enabled=True, name=f"Enable {service}")
    for service in plan.enable_and_start:
        systemd.service(service, running=True, enabled=True, name=f"Enable and start {service}")
    return plan


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


def render_backup_service(config: EnvironmentConfig) -> str:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("backup unit rendering requires an environment configuration")
    template = (_OPS_ROOT / "systemd" / "taskman-backup.service").read_text(encoding="utf-8")
    markers = {
        "{{TASKMAN_BACKUP_ROOT}}": config.backup_root.as_posix(),
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


def _changed_result(stdout: str) -> bool:
    markers = [line.removeprefix("changed=") for line in stdout.splitlines() if line.startswith("changed=")]
    if markers == ["0"]:
        return False
    if markers == ["1"]:
        return True
    raise OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "systemd",
        "runtime environment installation returned an invalid change result",
        changed=False,
        next_action="inspect the protected runtime environment and retry",
    )


def _backup_assets(
    config: EnvironmentConfig,
    contract: BackupServiceContract,
    timer: str,
) -> tuple[SystemdAsset, ...]:
    command, service, _timer = contract.assets
    return (
        SystemdAsset(
            command.source,
            None,
            str(command.destination),
            command.mode,
            binary_content=command.content,
            sha256=command.sha256,
        ),
        SystemdAsset(None, "", f"{config.install_root.as_posix()}/lifecycle.lock", 0o600),
        SystemdAsset(None, render_backup_service(config), str(service.destination), service.mode),
        SystemdAsset(None, timer, str(_timer.destination), _timer.mode),
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
