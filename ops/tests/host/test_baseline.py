from __future__ import annotations

import stat
from dataclasses import replace
from pathlib import Path

from pyinfra.api import Config, Inventory, State, deploy
from pyinfra.api.deploy import add_deploy
from pyinfra.api.operations import run_ops
from pyinfra.operations.files import ensure_mode_int
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
    assert [
        (path, kwargs["user"], kwargs["group"], int(str(ensure_mode_int(kwargs["mode"])), 8))
        for path, kwargs in directories
    ] == [
        (directory.path, directory.owner, directory.group, directory.mode) for directory in plan.directories
    ]
    assert [
        (content, destination, kwargs["user"], kwargs["group"], int(str(ensure_mode_int(kwargs["mode"])), 8))
        for content, destination, kwargs in files_written
    ] == [
        (plan.provisioning_marker_content, plan.provisioning_marker_path, "root", "root", 0o600),
        (plan.unattended_updates, "/etc/apt/apt.conf.d/52taskman-unattended-upgrades", "root", "root", 0o644),
    ]
    assert services == [
        (
            "unattended-upgrades",
            {"running": True, "enabled": True, "name": "Enable unattended upgrades"},
        )
    ]


def test_baseline_declares_the_provisioning_marker_before_other_mutations(monkeypatch) -> None:
    """A partial baseline run is marked before package/account/directory work begins."""

    events: list[tuple[str, str]] = []
    from pyinfra.operations import apt, files, server

    monkeypatch.setattr(apt, "packages", lambda **_kwargs: events.append(("packages", "")))
    monkeypatch.setattr(server, "user", lambda user, **_kwargs: events.append(("user", user)))
    monkeypatch.setattr(
        files,
        "directory",
        lambda path, **_kwargs: events.append(("directory", path)),
    )
    monkeypatch.setattr(
        files,
        "put",
        lambda _source, destination, **_kwargs: events.append(("file", destination)),
    )
    monkeypatch.setattr(server, "service", lambda service, **_kwargs: events.append(("service", service)))
    monkeypatch.setattr(baseline, "declare_firewall", lambda _config: events.append(("firewall", "")))

    plan = baseline.declare_baseline(environment_config())

    assert events[0] == ("file", plan.provisioning_marker_path)


def test_baseline_applies_domain_modes_through_actual_pyinfra_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Pyinfra receives octal-digit modes and applies the plan's intended permission bits."""

    remote_root = tmp_path / "remote"
    config = environment_config()
    from pyinfra.operations import apt, files, server

    original_put = files.put

    def remote_path(path: str) -> str:
        return (remote_root / path.lstrip("/")).as_posix()

    def put(source: object, destination: str, **kwargs: object) -> object:
        mapped_destination = (
            destination
            if destination.startswith(f"{remote_root.as_posix()}/")
            else remote_path(destination)
        )
        return original_put(
            source,
            mapped_destination,
            **{**kwargs, "user": None, "group": None},
        )

    plan = baseline.build_baseline_plan(config)
    mapped_plan = replace(
        plan,
        directories=tuple(
            replace(directory_plan, path=remote_path(directory_plan.path), owner="", group="")
            for directory_plan in plan.directories
        ),
        provisioning_marker_path=remote_path(plan.provisioning_marker_path),
    )
    monkeypatch.setattr(baseline, "build_baseline_plan", lambda _config: mapped_plan)
    monkeypatch.setattr(files, "put", put)
    monkeypatch.setattr(apt, "packages", lambda **_kwargs: None)
    monkeypatch.setattr(server, "user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(baseline, "declare_firewall", lambda _config: None)

    @deploy("Baseline mode semantics")
    def converge() -> None:
        baseline.declare_baseline(config)

    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    state.activate_host(inventory.get_host("@local"))
    add_deploy(state, converge)
    run_ops(state)

    for directory_plan in mapped_plan.directories:
        directory_path = Path(directory_plan.path)
        assert stat.S_IMODE(directory_path.stat().st_mode) == directory_plan.mode

    assert stat.S_IMODE(Path(mapped_plan.provisioning_marker_path).stat().st_mode) == 0o600
    assert stat.S_IMODE(
        Path(remote_path("/etc/apt/apt.conf.d/52taskman-unattended-upgrades")).stat().st_mode
    ) == 0o644
