from __future__ import annotations

from dataclasses import dataclass

from tests.fakes import ScriptedRemote
from tests.test_config import valid_environment
from taskman_ops.config import EnvironmentConfig
from taskman_ops.provisioning import ProvisioningInputs
from taskman_ops.remote import ChangeSet, CommandResult
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository
import taskman_ops.services.systemd as taskman_systemd
from taskman_ops.services.systemd import build_systemd_plan, install_runtime_environment, render_taskman_service


def config(**overrides: object) -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(**overrides))


def test_systemd_plan_uses_builtin_non_secret_assets_and_never_starts_taskman() -> None:
    plan = build_systemd_plan(config(install_root="/srv/taskman"), calendar_validator=lambda _schedule: None)

    assert [(asset.destination, asset.mode) for asset in plan.assets] == [
        ("/etc/systemd/system/taskman.service", 0o644),
        ("/usr/local/lib/taskman/taskman-backup", 0o750),
        ("/etc/systemd/system/taskman-backup.service", 0o644),
        ("/etc/systemd/system/taskman-backup.timer", 0o644),
    ]
    assert plan.enable_without_start == ("taskman.service",)
    assert plan.enable_and_start == ("taskman-backup.timer",)
    assert "/srv/taskman/current/bin/server" in render_taskman_service(config(install_root="/srv/taskman"))


def test_runtime_environment_stays_on_the_private_stdin_boundary() -> None:
    remote = ScriptedRemote.from_responses([CommandResult(0, "changed=1\n")])
    content = b"SECRET_KEY_BASE=runtime-sensitive-canary\n"

    result = install_runtime_environment(remote, content)

    assert result == ChangeSet(changed=True, operations=("Install protected runtime environment",))
    command, kwargs = remote.calls[0]
    assert "runtime-sensitive-canary" not in " ".join(command)
    assert kwargs["stdin"] == content
    assert kwargs["sensitive"] is True


def test_daemon_reload_is_skipped_when_no_unit_file_changed(monkeypatch) -> None:
    """A converged rerun must not execute pyinfra's non-idempotent reload."""

    @dataclass
    class Result:
        changed: bool

        def did_change(self) -> bool:
            return self.changed

    results = [Result(False), Result(False), Result(False), Result(False), Result(False)]
    reload_kwargs: dict[str, object] = {}
    from pyinfra.operations import files, systemd

    monkeypatch.setattr(files, "put", lambda *_args, **_kwargs: results.pop(0))
    monkeypatch.setattr(systemd, "service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(systemd, "daemon_reload", lambda **kwargs: reload_kwargs.update(kwargs))

    taskman_systemd.declare_systemd(
        ProvisioningInputs(
            config=config(),
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
