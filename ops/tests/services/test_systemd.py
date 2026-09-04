from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat
import subprocess

import pytest
from pyinfra.api import Config, Inventory, State, deploy
from pyinfra.api.deploy import add_deploy
from pyinfra.api.operations import run_ops
from pyinfra.operations.files import ensure_mode_int

from tests.support.remotes import LocalProtectedRemote, ScriptedRemote
from tests.support.environments import environment_config
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.provisioning import ProvisioningInputs
from taskman_ops.remote import ChangeSet, CommandResult
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository
import taskman_ops.services.systemd as taskman_systemd
from taskman_ops.services.systemd import build_systemd_plan, install_runtime_environment, render_taskman_service


def test_systemd_plan_uses_builtin_non_secret_assets_and_never_starts_taskman() -> None:
    plan = build_systemd_plan(
        environment_config(install_root="/srv/taskman"), calendar_validator=lambda _schedule: None
    )

    assert [(asset.destination, asset.mode) for asset in plan.assets] == [
        ("/etc/systemd/system/taskman.service", 0o644),
        ("/usr/local/lib/taskman/taskman-backup.pyz", 0o750),
        ("/srv/taskman/lifecycle.lock", 0o600),
        ("/etc/systemd/system/taskman-backup.service", 0o644),
        ("/etc/systemd/system/taskman-backup.timer", 0o644),
    ]
    assert plan.enable_without_start == ("taskman.service",)
    assert plan.enable_and_start == ("taskman-backup.timer",)
    service = render_taskman_service(environment_config(install_root="/srv/taskman"))
    assert set(service.splitlines()) >= {
        "User=taskman",
        "Group=taskman",
        "EnvironmentFile=/etc/taskman/taskman.env",
        "Environment=RELEASE_TMP=/var/lib/taskman",
        "ExecStartPre=/srv/taskman/current/bin/migrate",
        "ExecStart=/srv/taskman/current/bin/server",
        "KillSignal=SIGTERM",
        "Restart=on-failure",
    }


@pytest.mark.parametrize(
    "install_root",
    ["/opt/taskman", "/srv/taskman$(printf${IFS}CANARY)", "/srv/taskman;:"],
)
def test_rendered_taskman_unit_validates_with_supported_install_roots(
    tmp_path: Path,
    install_root: str,
) -> None:
    """Native systemd must retain supported roots in executable and path directives."""

    unit_root = tmp_path / "unit-root"
    units = unit_root / "etc" / "systemd" / "system"
    release_bin = unit_root / install_root.lstrip("/") / "current" / "bin"
    units.mkdir(parents=True)
    release_bin.mkdir(parents=True)
    for executable in ("migrate", "server"):
        path = release_bin / executable
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o750)

    config = environment_config(install_root=install_root)
    (units / "taskman.service").write_text(render_taskman_service(config), encoding="utf-8")
    for unit in ("sysinit.target", "network-online.target", "postgresql.service"):
        fixture = "[Unit]\nDescription=fixture\n"
        if unit == "postgresql.service":
            fixture += f"[Service]\nType=oneshot\nExecStart={install_root}/current/bin/server\n"
        (units / unit).write_text(fixture, encoding="utf-8")

    completed = subprocess.run(
        ["systemd-analyze", "verify", f"--root={unit_root}", "taskman.service"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""


def test_runtime_environment_uses_a_non_sensitive_exit_status_receipt() -> None:
    """A sensitive transport that suppresses streams still reports a changed write."""

    remote = ScriptedRemote.from_responses([CommandResult(3)])
    content = b"SECRET_KEY_BASE=runtime-sensitive-canary\n"

    result = install_runtime_environment(remote, content)

    assert result == ChangeSet(changed=True, operations=("Install protected runtime environment",))
    command, kwargs = remote.calls[0]
    assert "runtime-sensitive-canary" not in " ".join(command)
    assert kwargs["stdin"] == content
    assert kwargs["sensitive"] is True


def test_runtime_environment_performs_actual_idempotent_and_mode_repairing_writes(tmp_path: Path) -> None:
    """Protected writes change on first install and mode drift, but not on a rerun."""

    destination = tmp_path / "taskman.env"
    remote = LocalProtectedRemote(destination, tmp_path)
    content = b"SECRET_KEY_BASE=runtime-sensitive-canary\n"

    first = install_runtime_environment(remote, content)
    second = install_runtime_environment(remote, content)
    destination.chmod(0o644)
    repaired = install_runtime_environment(remote, content)

    assert first == ChangeSet(changed=True, operations=("Install protected runtime environment",))
    assert second == ChangeSet(changed=False, operations=())
    assert repaired == ChangeSet(changed=True, operations=("Install protected runtime environment",))
    assert destination.read_bytes() == content
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert remote.results == [(3, b"", b""), (0, b"", b""), (3, b"", b"")]


def test_runtime_environment_normalizes_actual_remote_write_errors(tmp_path: Path) -> None:
    """A protected shell failure is generic, non-success, and stream-free."""

    destination = tmp_path / "missing" / "taskman.env"
    remote = LocalProtectedRemote(destination, tmp_path)

    with pytest.raises(OpsError) as raised:
        install_runtime_environment(remote, b"SECRET_KEY_BASE=runtime-sensitive-canary\n")

    assert raised.value.status is ExitStatus.SECRET
    assert raised.value.changed is True
    assert remote.results == [(1, b"", b"")]


@pytest.mark.parametrize("failure", ("cleanup", "signal"))
def test_runtime_environment_marks_possible_mutation_for_protected_failures(
    tmp_path: Path, *, failure: str
) -> None:
    """Cleanup and signal failures after replacement retain conservative change evidence."""

    destination = tmp_path / "taskman.env"
    remote = LocalProtectedRemote(destination, tmp_path, failure=failure)
    content = b"SECRET_KEY_BASE=runtime-sensitive-canary\n"

    with pytest.raises(OpsError) as raised:
        install_runtime_environment(remote, content)

    assert raised.value.status is ExitStatus.SECRET
    assert raised.value.changed is True
    assert destination.read_bytes() == content
    assert remote.results == [(1, b"", b"")]


def test_runtime_environment_preserves_a_structured_transport_error(tmp_path: Path) -> None:
    """A transport error stays intact while recording possible protected mutation."""

    error = OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "transport",
        "protected transport failed",
        changed=False,
        next_action="retry the protected operation",
        state={"boundary": "runtime"},
        warnings=("transport warning",),
    )
    remote = ScriptedRemote.from_responses([error])

    with pytest.raises(OpsError) as raised:
        install_runtime_environment(remote, b"SECRET_KEY_BASE=runtime-sensitive-canary\n")

    assert raised.value is error
    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.stage == "transport"
    assert raised.value.message == "protected transport failed"
    assert raised.value.next_action == "retry the protected operation"
    assert raised.value.state == {"boundary": "runtime"}
    assert raised.value.warnings == ("transport warning",)
    assert raised.value.changed is True


def test_daemon_reload_is_skipped_when_no_unit_file_changed(monkeypatch) -> None:
    """A converged rerun must not execute pyinfra's non-idempotent reload."""

    @dataclass
    class Result:
        changed: bool

        def did_change(self) -> bool:
            return self.changed

    results = [Result(False), Result(False), Result(False), Result(False), Result(False), Result(False)]
    reload_kwargs: dict[str, object] = {}
    from pyinfra.operations import files, server, systemd

    monkeypatch.setattr(files, "put", lambda *_args, **_kwargs: results.pop(0))
    monkeypatch.setattr(server, "shell", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(systemd, "service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(systemd, "daemon_reload", lambda **kwargs: reload_kwargs.update(kwargs))

    taskman_systemd.declare_systemd(
        ProvisioningInputs(
            config=environment_config(),
            caddy_plan=CaddyPlan(
                CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
                (),
                ("caddy",),
                "taskman.acme.tld {}\n",
            ),
            runtime_environment=b"RUNTIME=value\n",
            pgpass=b"pgpass\n",
            role_password_input=b"role-password-input\n",
        )
    )

    assert reload_kwargs["_if"]() is False


def test_systemd_uses_builtin_file_reload_enablement_and_service_convergence(monkeypatch) -> None:
    """Replacing a unit, daemon reload, or enablement with a custom shell action must fail this."""

    @dataclass
    class Result:
        def did_change(self) -> bool:
            return False

    puts: list[tuple[str, str, dict[str, object]]] = []
    reloads: list[dict[str, object]] = []
    services: list[tuple[str, dict[str, object]]] = []
    from pyinfra.operations import files, server, systemd

    monkeypatch.setattr(
        files,
        "put",
        lambda _source, destination, **kwargs: puts.append(("", destination, kwargs)) or Result(),
    )
    monkeypatch.setattr(systemd, "daemon_reload", lambda **kwargs: reloads.append(kwargs))
    monkeypatch.setattr(systemd, "service", lambda service, **kwargs: services.append((service, kwargs)))
    checksum_checks: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(
        server,
        "shell",
        lambda commands, **kwargs: checksum_checks.append((commands, kwargs)),
    )
    inputs = ProvisioningInputs(
        config=environment_config(),
        caddy_plan=CaddyPlan(
            CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
            (),
            ("caddy",),
            "taskman.acme.tld {}\n",
        ),
        runtime_environment=b"RUNTIME=value\n",
        pgpass=b"pgpass\n",
        role_password_input=b"role-password-input\n",
    )

    plan = taskman_systemd.declare_systemd(inputs)

    assert [
        (destination, int(str(ensure_mode_int(kwargs["mode"])), 8))
        for _content, destination, kwargs in puts
    ] == [
        *((asset.destination, asset.mode) for asset in plan.assets),
        (plan.backup_environment_path, 0o600),
    ]
    assert reloads[0]["name"] == "Reload systemd daemon"
    assert reloads[0]["_if"]() is False
    assert services == [
        ("taskman.service", {"running": None, "enabled": True, "name": "Enable taskman.service"}),
        (
            "taskman-backup.timer",
            {"running": True, "enabled": True, "name": "Enable and start taskman-backup.timer"},
        ),
    ]
    backup_asset = next(asset for asset in plan.assets if asset.destination.endswith("taskman-backup.pyz"))
    assert backup_asset.sha256 is not None
    backup_put = next(entry for entry in puts if entry[1] == backup_asset.destination)
    assert {**backup_put[2], "mode": int(str(ensure_mode_int(backup_put[2]["mode"])), 8)} == {
        "user": "root",
        "group": "root",
        "mode": backup_asset.mode,
        "add_deploy_dir": False,
        "name": f"Install {backup_asset.destination}",
    }
    assert len(checksum_checks) == 1
    commands, kwargs = checksum_checks[0]
    assert backup_asset.destination in commands
    assert backup_asset.sha256 in commands
    assert kwargs == {"name": f"Verify checksum for {backup_asset.destination}", "_sudo": True}


def test_systemd_applies_asset_modes_through_actual_pyinfra_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Pyinfra applies each systemd and backup asset with its intended permission bits."""

    remote_root = tmp_path / "remote"
    config = environment_config()
    from pyinfra.operations import files, server, systemd

    original_put = files.put

    def remote_path(path: str) -> str:
        return (remote_root / path.lstrip("/")).as_posix()

    def put(source: object, destination: str, **kwargs: object) -> object:
        return original_put(
            source,
            remote_path(destination),
            **{**kwargs, "user": None, "group": None},
        )

    monkeypatch.setattr(files, "put", put)
    monkeypatch.setattr(server, "shell", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(systemd, "daemon_reload", lambda **_kwargs: None)
    monkeypatch.setattr(systemd, "service", lambda *_args, **_kwargs: None)
    inputs = ProvisioningInputs(
        config=config,
        caddy_plan=CaddyPlan(
            CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
            (),
            ("caddy",),
            "taskman.acme.tld {}\n",
        ),
        runtime_environment=b"RUNTIME=value\n",
        pgpass=b"pgpass\n",
        role_password_input=b"role-password-input\n",
    )

    @deploy("Systemd mode semantics")
    def converge() -> None:
        taskman_systemd.declare_systemd(inputs)

    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    state.activate_host(inventory.get_host("@local"))
    add_deploy(state, converge)
    run_ops(state)

    plan = build_systemd_plan(config)
    for asset in plan.assets:
        asset_path = Path(remote_path(asset.destination))
        assert stat.S_IMODE(asset_path.stat().st_mode) == asset.mode

    environment_path = Path(remote_path(plan.backup_environment_path))
    assert stat.S_IMODE(environment_path.stat().st_mode) == 0o600
