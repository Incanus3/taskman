from __future__ import annotations

import os
from pathlib import Path
import grp
import pwd
import stat
import subprocess

from tests.test_config import valid_environment
from taskman_ops.config import EnvironmentConfig
from taskman_ops.remote import CommandResult
from taskman_ops.remote import ChangeSet
from taskman_ops.services.systemd import (
    apply_systemd_assets,
    build_systemd_plan,
    install_runtime_environment,
    render_owned_file_repair_script,
    render_taskman_service,
)
from tests.fakes import ScriptedRemote


def config(**overrides: object) -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(**overrides))


def test_systemd_plan_validates_staged_units_and_reloads_the_daemon_only_when_owned_units_change() -> None:
    """Installing unvalidated units or reloading every rerun must fail this."""

    plan = build_systemd_plan(config(), calendar_validator=lambda _schedule: None)

    assert plan.staged_validation == (
        "systemd-analyze",
        "verify",
        "--root=managed-staging-root",
        "taskman.service",
        "taskman-backup.service",
        "taskman-backup.timer",
    )
    assert plan.reload_daemon_only_after_change is True
    assert [(asset.destination, asset.mode) for asset in plan.assets] == [
        ("/etc/systemd/system/taskman.service", 0o644),
        ("/usr/local/lib/taskman/taskman-backup", 0o750),
        ("/etc/systemd/system/taskman-backup.service", 0o644),
        ("/etc/systemd/system/taskman-backup.timer", 0o644),
    ]


def test_systemd_plan_keeps_runtime_environment_root_only_and_never_starts_taskman() -> None:
    """Relaxing environment permissions or starting the service before release activation must fail this."""

    plan = build_systemd_plan(config(), calendar_validator=lambda _schedule: None)

    assert (plan.runtime_environment.path, plan.runtime_environment.owner, plan.runtime_environment.group, plan.runtime_environment.mode) == (
        "/etc/taskman/taskman.env",
        "root",
        "root",
        0o600,
    )
    assert (plan.backup_environment.path, plan.backup_environment.owner, plan.backup_environment.group, plan.backup_environment.mode) == (
        "/etc/taskman/taskman-backup.env",
        "root",
        "root",
        0o600,
    )
    assert plan.enable_without_start == ("taskman.service",)
    assert plan.enable_and_start == ("taskman-backup.timer",)


def test_systemd_plan_renders_the_validated_managed_root_into_the_taskman_unit() -> None:
    plan = build_systemd_plan(
        config(
            managed_root="/srv/taskman",
            release_root="/srv/taskman/releases",
            deployment_root="/srv/taskman/deployments",
        ),
        calendar_validator=lambda _schedule: None,
    )
    taskman = plan.assets[0]

    assert taskman.content is not None
    assert "/srv/taskman/current/bin/server" in taskman.content
    assert "/opt/taskman" not in taskman.content


def test_rendered_exec_start_pre_verifies_against_the_selected_release_in_an_isolated_systemd_root(
    tmp_path: Path,
) -> None:
    """A bad ExecStartPre path or missing taskman account must make systemd-analyze reject the unit."""

    environment = config()
    validation_root = tmp_path / "systemd-root"
    unit_directory = validation_root / "etc" / "systemd" / "system"
    selected_bin = validation_root / environment.managed_root.as_posix().lstrip("/") / "current" / "bin"
    selected_bin.mkdir(parents=True)
    unit_directory.mkdir(parents=True)
    (validation_root / "etc" / "passwd").write_text(
        "taskman:x:1000:1000::/nonexistent:/usr/sbin/nologin\n", encoding="utf-8"
    )
    (validation_root / "etc" / "group").write_text("taskman:x:1000:\n", encoding="utf-8")
    for binary in ("migrate", "server"):
        executable = selected_bin / binary
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    for unit in ("sysinit.target", "network-online.target", "multi-user.target", "postgresql.service"):
        (unit_directory / unit).write_text("[Unit]\nDescription=validation fixture\n", encoding="utf-8")
    (unit_directory / "taskman.service").write_text(render_taskman_service(environment), encoding="utf-8")

    completed = subprocess.run(
        ("systemd-analyze", "verify", f"--root={validation_root}", "taskman.service"),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_systemd_plan_renders_configured_backup_and_deployment_roots_into_the_backup_sandbox() -> None:
    plan = build_systemd_plan(
        config(
            managed_root="/srv/taskman",
            release_root="/srv/taskman/releases",
            deployment_root="/srv/taskman/deployments",
            backup_root="/srv/backups/taskman",
        ),
        calendar_validator=lambda _schedule: None,
    )
    backup_service = next(
        asset
        for asset in plan.assets
        if asset.destination == "/etc/systemd/system/taskman-backup.service"
    )

    assert backup_service.content is not None
    assert "ReadWritePaths=/srv/backups/taskman /srv/taskman/deployments /var/lock/taskman" in backup_service.content
    assert "/var/backups/taskman" not in backup_service.content
    assert "/opt/taskman/deployments" not in backup_service.content


def test_runtime_environment_is_installed_from_sensitive_stdin_with_root_mode_600() -> None:
    """Writing runtime secrets through argv or with readable permissions must fail this."""

    remote = ScriptedRemote.from_responses([CommandResult(0, "changed=1\n")])
    content = b"SECRET_KEY_BASE=runtime-sensitive-canary\n"

    result = install_runtime_environment(remote, content)

    assert result == ChangeSet(changed=True, operations=("runtime-environment",))
    command, kwargs = remote.calls[0]
    assert command[-1] == "/etc/taskman/taskman.env"
    assert "runtime-sensitive-canary" not in " ".join(command)
    assert kwargs["stdin"] == content
    assert kwargs["sensitive"] is True


def test_systemd_owned_asset_repair_script_fixes_mode_drift_then_reports_a_noop(tmp_path: Path) -> None:
    """Same bytes with an unsafe mode must repair once and report no-op thereafter."""

    staged = tmp_path / "taskman.service.staged"
    destination = tmp_path / "taskman.service"
    staged.write_text("[Unit]\nDescription=Taskman\n", encoding="utf-8")
    destination.write_text(staged.read_text(encoding="utf-8"), encoding="utf-8")
    destination.chmod(0o600)
    script = render_owned_file_repair_script(
        staged.as_posix(),
        destination.as_posix(),
        0o644,
        owner=pwd.getpwuid(os.getuid()).pw_name,
        group=grp.getgrgid(os.getgid()).gr_name,
    )

    first = _run(script)
    second = _run(script)

    assert first.stdout == "changed=1\n"
    assert second.stdout == "changed=0\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644
    assert (destination.stat().st_uid, destination.stat().st_gid) == (os.getuid(), os.getgid())


def test_systemd_execution_adapter_reports_the_script_change_marker() -> None:
    """The staged unit capability must expose first-change versus second no-op."""

    plan = build_systemd_plan(config(), calendar_validator=lambda _schedule: None)

    assert apply_systemd_assets(ScriptedRemote.from_responses([CommandResult(0, "changed=1\n")]), plan).changed is True
    assert apply_systemd_assets(ScriptedRemote.from_responses([CommandResult(0, "changed=0\n")]), plan).changed is False


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("sh", "-ceu", script),
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
