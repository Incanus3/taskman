from __future__ import annotations

from tests.test_config import valid_environment
from taskman_ops.config import EnvironmentConfig
import taskman_ops.host.baseline as baseline
from taskman_ops.host.baseline import (
    BASELINE_PACKAGES,
    TASKMAN_DIRECTORIES,
    build_baseline_plan,
)


def config(**overrides: object) -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(**overrides))


def test_baseline_plan_uses_built_in_owned_packages_account_and_directories() -> None:
    """Stable baseline state is declared once, rather than through a shell adapter."""

    plan = build_baseline_plan(config())

    assert plan.packages == BASELINE_PACKAGES == (
        "ca-certificates",
        "curl",
        "gnupg",
        "python3-minimal",
        "unattended-upgrades",
        "ufw",
    )
    assert plan.directories == TASKMAN_DIRECTORIES
    assert plan.service_account.name == "taskman"
    assert plan.service_account.shell == "/usr/sbin/nologin"
    assert 'Unattended-Upgrade::Automatic-Reboot "false";' in plan.unattended_updates
    assert plan.provisioning_marker_path == "/var/lib/taskman-provisioning.state"
    assert plan.provisioning_marker_content == "taskman-provisioning-v1\n"


def test_baseline_plan_uses_validated_configured_roots() -> None:
    plan = build_baseline_plan(config(install_root="/srv/taskman", backup_root="/srv/backups/taskman"))

    assert [directory.path for directory in plan.directories[:3]] == [
        "/srv/taskman",
        "/srv/taskman/releases",
        "/srv/taskman/deployments",
    ]
    assert plan.directories[5].path == "/srv/backups/taskman"


def test_baseline_refreshes_apt_metadata_with_a_bounded_cache_before_installing_packages(monkeypatch) -> None:
    """A clean host must populate package indexes before its first install."""

    package_calls: list[dict[str, object]] = []
    from pyinfra.operations import apt, files, server

    monkeypatch.setattr(apt, "packages", lambda **kwargs: package_calls.append(kwargs))
    monkeypatch.setattr(files, "directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(files, "put", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(baseline, "declare_firewall", lambda _config: None)

    baseline.declare_baseline(config())

    assert package_calls[0]["packages"] == list(BASELINE_PACKAGES)
    assert package_calls[0]["update"] is True
    assert package_calls[0]["cache_time"] == 3600
