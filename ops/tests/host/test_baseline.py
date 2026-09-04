from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from tests.test_config import valid_environment
from taskman_ops.config import EnvironmentConfig
from taskman_ops.host.baseline import (
    BASELINE_PACKAGES,
    TASKMAN_DIRECTORIES,
    build_baseline_plan,
    converge_baseline_host,
    render_baseline_convergence_script,
)
from taskman_ops.errors import ExitStatus
from taskman_ops.remote import CommandResult


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment())


def test_baseline_plan_declares_only_the_required_packages_and_no_automatic_reboot() -> None:
    """Removing a required package or enabling automated reboots must fail this."""

    plan = build_baseline_plan(config())

    assert BASELINE_PACKAGES == (
        "ca-certificates",
        "curl",
        "gnupg",
        "python3-minimal",
        "unattended-upgrades",
        "ufw",
    )
    assert plan.packages == BASELINE_PACKAGES
    assert plan.unattended_updates == (
        'APT::Periodic::Update-Package-Lists "1";\n'
        'APT::Periodic::Unattended-Upgrade "1";\n'
        'Unattended-Upgrade::Automatic-Reboot "false";\n'
    )


def test_baseline_plan_owns_only_root_or_service_owned_taskman_paths() -> None:
    """Changing a managed directory's owner or mode must fail this."""

    plan = build_baseline_plan(config())

    assert plan.directories == TASKMAN_DIRECTORIES
    assert [(entry.path, entry.owner, entry.group, entry.mode) for entry in plan.directories] == [
        ("/opt/taskman", "root", "root", 0o755),
        ("/opt/taskman/releases", "root", "root", 0o755),
        ("/opt/taskman/deployments", "root", "root", 0o700),
        ("/etc/taskman", "root", "taskman", 0o750),
        ("/var/lib/taskman", "taskman", "taskman", 0o700),
        ("/var/backups/taskman", "root", "root", 0o700),
        ("/var/lock/taskman", "root", "root", 0o700),
        ("/usr/local/lib/taskman", "root", "root", 0o755),
    ]


def test_baseline_plan_uses_each_validated_configured_root_instead_of_fixed_paths() -> None:
    plan = build_baseline_plan(
        EnvironmentConfig.model_validate(
            valid_environment(
                managed_root="/srv/taskman",
                release_root="/srv/taskman/releases",
                deployment_root="/srv/taskman/deployments",
                backup_root="/srv/taskman-backups",
            )
        )
    )

    assert [directory.path for directory in plan.directories[:3]] == [
        "/srv/taskman",
        "/srv/taskman/releases",
        "/srv/taskman/deployments",
    ]
    assert plan.directories[5].path == "/srv/taskman-backups"


def test_baseline_plan_creates_a_system_nologin_service_account_without_touching_ssh_policy() -> None:
    """Giving Taskman a login shell or expanding the plan into SSH policy must fail this."""

    plan = build_baseline_plan(config())

    assert plan.service_account.name == "taskman"
    assert plan.service_account.home == "/var/lib/taskman"
    assert plan.service_account.shell == "/usr/sbin/nologin"
    assert plan.service_account.system is True
    assert plan.ssh_policy_changes == ()


def test_baseline_script_refuses_an_existing_account_with_extra_group_authority_before_mutation(tmp_path: Path) -> None:
    """An adopted account must retain the exact no-login, taskman-only identity."""

    script = _baseline_script_for_existing_account(tmp_path)
    completed = _run_existing_account_script(script, tmp_path, groups="taskman sudo")

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert not (tmp_path / "mutations.log").exists()


def test_baseline_script_accepts_only_the_exact_existing_service_account_identity(tmp_path: Path) -> None:
    """A correct existing account is a no-op instead of being recreated."""

    script = _baseline_script_for_existing_account(tmp_path)
    completed = _run_existing_account_script(script, tmp_path, groups="taskman")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "changed=0\n"
    assert not (tmp_path / "mutations.log").exists()


def test_baseline_writes_its_recovery_marker_before_a_later_mutable_step(tmp_path: Path) -> None:
    """A package interruption must leave the anchor that permits a safe retry."""

    marker = tmp_path / "provisioning.state"
    unattended = tmp_path / "52taskman-unattended-upgrades"
    plan = replace(build_baseline_plan(config()), packages=("fixture-package",), directories=())
    script = render_baseline_convergence_script(
        plan,
        unattended_path=unattended.as_posix(),
        provisioning_marker_path=marker.as_posix(),
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_command(bin_dir / "dpkg-query", "exit 1")
    _fake_command(bin_dir / "apt-get", "exit 42")
    _fake_command(
        bin_dir / "install",
        "previous=; last=; for value do previous=$last; last=$value; done; cp \"$previous\" \"$last\"; chmod 600 \"$last\"",
    )

    completed = subprocess.run(
        ("sh", "-ceu", script),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert completed.returncode == 42
    assert marker.read_text(encoding="utf-8") == "taskman-provisioning-v1\n"
    assert marker.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(
    os.environ.get("TASKMAN_CONTROLLED_HOST_PROOF") != "1",
    reason="controlled Ubuntu container proof is opt-in",
)
def test_controlled_ubuntu_container_converges_package_user_and_file_state_twice_and_repairs_drift() -> None:
    """Exercise the module-owned host adapter, not a test-local approximation."""

    image = "ubuntu:26.04"
    name = f"taskman-host-proof-{uuid.uuid4().hex}"
    _run(("docker", "run", "--detach", "--rm", "--name", name, image, "sleep", "infinity"))
    try:
        remote = _ContainerRemote(name)
        first = converge_baseline_host(remote, config())
        second = converge_baseline_host(remote, config())
        _docker_exec(
            name,
            "printf 'drifted=1\\n' > /etc/apt/apt.conf.d/52taskman-unattended-upgrades; "
            "chmod 0600 /etc/apt/apt.conf.d/52taskman-unattended-upgrades",
        )
        repaired = converge_baseline_host(remote, config())
        state = _docker_exec(name, "stat --format='%U:%G:%a' /etc/apt/apt.conf.d/52taskman-unattended-upgrades")
        content = _docker_exec(name, "cat /etc/apt/apt.conf.d/52taskman-unattended-upgrades")
    finally:
        subprocess.run(("docker", "rm", "--force", name), check=False, capture_output=True, text=True)

    assert first.changed is True
    assert second.changed is False
    assert repaired.changed is True
    assert state.stdout.strip() == "root:root:644"
    assert content.stdout == build_baseline_plan(config()).unattended_updates


@pytest.mark.skip(reason="VM-only acceptance: systemd unit activation needs a booted Ubuntu VM")
def test_controlled_proof_explicitly_skips_systemd_acceptance() -> None:
    pass


@pytest.mark.skip(reason="VM-only acceptance: UFW needs a fresh SSH connection to a booted Ubuntu VM")
def test_controlled_proof_explicitly_skips_ufw_acceptance() -> None:
    pass


@pytest.mark.skip(reason="VM-only acceptance: ACME requires public DNS and a reachable Ubuntu VM")
def test_controlled_proof_explicitly_skips_acme_acceptance() -> None:
    pass


def _docker_exec(name: str, script: str) -> subprocess.CompletedProcess[str]:
    return _run(("docker", "exec", name, "bash", "-eu", "-c", script))


def _run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=600)


class _ContainerRemote:
    def __init__(self, name: str) -> None:
        self._name = name

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        completed = subprocess.run(
            ("docker", "exec", self._name, *argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _baseline_script_for_existing_account(tmp_path: Path) -> str:
    plan = replace(build_baseline_plan(config()), directories=())
    unattended = tmp_path / "52taskman-unattended-upgrades"
    marker = tmp_path / "provisioning.state"
    unattended.write_text(plan.unattended_updates, encoding="utf-8")
    marker.write_text("taskman-provisioning-v1\n", encoding="utf-8")
    marker.chmod(0o600)
    return render_baseline_convergence_script(
        plan,
        unattended_path=unattended.as_posix(),
        provisioning_marker_path=marker.as_posix(),
    )


def _run_existing_account_script(script: str, tmp_path: Path, *, groups: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _fake_command(bin_dir / "dpkg-query", "printf installed")
    _fake_command(
        bin_dir / "getent",
        "case \"$1\" in group) printf 'taskman:x:999:' ;; passwd) printf 'taskman:x:999:999::/var/lib/taskman:/usr/sbin/nologin\\n' ;; esac",
    )
    _fake_command(
        bin_dir / "id",
        f'''case "$1" in
  taskman) exit 0 ;;
  -gn) printf 'taskman\\n' ;;
  -Gn) printf '%s\\n' {groups!r} ;;
  *) exit 1 ;;
esac''',
    )
    _fake_command(
        bin_dir / "stat",
        "case \"$*\" in *provisioning.state*) printf 'root:root:600\\n' ;; *) printf 'root:root:644\\n' ;; esac",
    )
    for command in ("groupadd", "useradd", "install", "apt-get"):
        _fake_command(bin_dir / command, f"printf '%s\\n' '$0 $*' >> {str(tmp_path / 'mutations.log')!r}")
    return subprocess.run(
        ("sh", "-ceu", script),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )


def _fake_command(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
