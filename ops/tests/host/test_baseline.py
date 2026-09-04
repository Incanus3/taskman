from __future__ import annotations

from tests.support.environments import environment_config
import taskman_ops.host.baseline as baseline
from taskman_ops.host.baseline import (
    BASELINE_PACKAGES,
    TASKMAN_DIRECTORIES,
    build_baseline_plan,
)


def test_baseline_plan_uses_built_in_owned_packages_account_and_directories() -> None:
    """Stable baseline state is declared once, rather than through a shell adapter."""

    plan = build_baseline_plan(environment_config())

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
    plan = build_baseline_plan(
        environment_config(install_root="/srv/taskman", backup_root="/srv/backups/taskman")
    )

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

    baseline.declare_baseline(environment_config())

    assert package_calls[0]["packages"] == list(BASELINE_PACKAGES)
    assert package_calls[0]["update"] is True
    assert package_calls[0]["cache_time"] == 3600


def test_baseline_delegates_ordinary_account_directory_file_and_service_state_to_pyinfra(monkeypatch) -> None:
    """Replacing any stable baseline state with a custom action must fail this boundary contract."""

    users: list[tuple[str, dict[str, object]]] = []
    directories: list[tuple[str, dict[str, object]]] = []
    files_written: list[tuple[str, str, dict[str, object]]] = []
    services: list[tuple[str, dict[str, object]]] = []
    from pyinfra.operations import apt, files, server

    monkeypatch.setattr(apt, "packages", lambda **_kwargs: None)
    monkeypatch.setattr(server, "user", lambda user, **kwargs: users.append((user, kwargs)))
    monkeypatch.setattr(files, "directory", lambda path, **kwargs: directories.append((path, kwargs)))
    monkeypatch.setattr(
        files,
        "put",
        lambda source, destination, **kwargs: files_written.append((source.getvalue(), destination, kwargs)),
    )
    monkeypatch.setattr(server, "service", lambda service, **kwargs: services.append((service, kwargs)))
    monkeypatch.setattr(baseline, "declare_firewall", lambda _config: None)

    plan = baseline.declare_baseline(environment_config())

    assert users == [
        (
            "taskman",
            {
                "home": "/var/lib/taskman",
                "shell": "/usr/sbin/nologin",
                "system": True,
                "create_home": True,
                "name": "Create Taskman system account",
            },
        )
    ]
    assert [(path, kwargs["user"], kwargs["group"], kwargs["mode"]) for path, kwargs in directories] == [
        (directory.path, directory.owner, directory.group, directory.mode) for directory in plan.directories
    ]
    assert [(content, destination, kwargs["user"], kwargs["group"], kwargs["mode"]) for content, destination, kwargs in files_written] == [
        (plan.unattended_updates, "/etc/apt/apt.conf.d/52taskman-unattended-upgrades", "root", "root", 0o644),
        (plan.provisioning_marker_content, plan.provisioning_marker_path, "root", "root", 0o600),
    ]
    assert services == [
        (
            "unattended-upgrades",
            {"running": True, "enabled": True, "name": "Enable unattended upgrades"},
        )
    ]
