from __future__ import annotations

import os
from pathlib import Path

from pyinfra.api import Config, Inventory, State, deploy

from tests.test_config import valid_environment
from taskman_ops.config import EnvironmentConfig
from taskman_ops.remote import ChangeSet, PyinfraRemote
import taskman_ops.services.caddy as caddy
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository, build_caddy_plan, render_caddyfile


def config(**overrides: object) -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(**overrides))


def test_caddy_plan_uses_the_official_signed_repository_and_loopback_renderer() -> None:
    plan = build_caddy_plan(config())

    assert plan.repository.key_url == "https://dl.cloudsmith.io/public/caddy/stable/gpg.key"
    assert plan.repository.keyring == "/usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    assert "signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg" in plan.repository.source
    assert plan.packages == ("caddy",)
    assert render_caddyfile(config(application_port=4011)) == "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4011\n}\n"


def test_declare_caddy_stages_the_preconfirmed_plan_without_rendering_again(monkeypatch) -> None:
    """Stable repository/file/service state stays built in around the validation boundary.

    Routing validation through the generic conditional-convergence adapter would
    make this test fail: the direct Caddy action is the only custom step.
    """

    plan = CaddyPlan(
        repository=CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
        repository_packages=("curl",),
        packages=("caddy",),
        caddyfile="taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n",
    )
    staged: list[tuple[str, str]] = []
    validated: list[tuple[str, str]] = []
    packages: list[dict[str, object]] = []
    keys: list[dict[str, object]] = []
    repositories: list[tuple[str, dict[str, object]]] = []
    services: list[tuple[str, dict[str, object]]] = []
    operations: list[str] = []

    class ValidatedConfiguration:
        def did_change(self) -> bool:
            return True

    from pyinfra.operations import apt, files, systemd

    monkeypatch.setattr(apt, "packages", lambda **kwargs: packages.append(kwargs))
    monkeypatch.setattr(apt, "key", lambda **kwargs: keys.append(kwargs))
    monkeypatch.setattr(apt, "repo", lambda source, **kwargs: repositories.append((source, kwargs)))
    monkeypatch.setattr(
        files,
        "put",
        lambda source, destination, **_kwargs: staged.append((source.getvalue(), destination))
        or operations.append(destination)
        or ValidatedConfiguration(),
    )
    monkeypatch.setattr(systemd, "service", lambda service, **kwargs: services.append((service, kwargs)))
    monkeypatch.setattr(
        caddy,
        "_validate_and_install_caddy",
        lambda staged_path, *_args, **_kwargs: validated.append((staged_path, "/etc/caddy/Caddyfile"))
        or operations.append("validate")
        or ValidatedConfiguration(),
        raising=False,
    )
    monkeypatch.setattr(
        caddy,
        "conditional_convergence",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Caddy must not use generic convergence")),
        raising=False,
    )
    monkeypatch.setattr(
        caddy,
        "build_caddy_plan",
        lambda _config: (_ for _ in ()).throw(AssertionError("Caddy must not render after confirmation")),
    )

    caddy.declare_caddy(plan)

    assert packages == [
        {"packages": ["curl"], "name": "Install Caddy repository prerequisites"},
        {"packages": ["caddy"], "name": "Install Caddy"},
    ]
    assert keys == [
        {
            "src": "https://example.test/key",
            "dest": "/keyring",
            "name": "Install Caddy signing key",
        }
    ]
    assert repositories == [
        (
            "deb https://example.test stable",
            {"filename": "caddy-stable", "name": "Configure Caddy repository"},
        )
    ]
    assert staged == [
        (plan.caddyfile, "/etc/taskman/Caddyfile.staged"),
        (plan.caddyfile, "/etc/caddy/Caddyfile"),
    ]
    assert validated == [("/etc/taskman/Caddyfile.staged", "/etc/caddy/Caddyfile")]
    assert operations == ["/etc/taskman/Caddyfile.staged", "validate", "/etc/caddy/Caddyfile"]
    assert services[0] == (
        "caddy",
        {"running": True, "enabled": True, "name": "Enable and start Caddy"},
    )
    assert services[1][0] == "caddy"
    assert services[1][1]["reloaded"] is True
    assert services[1][1]["name"] == "Reload Caddy after validated replacement"
    assert services[1][1]["_if"]() is True


def test_caddy_uses_builtin_live_file_change_for_reload_after_custom_validation(monkeypatch) -> None:
    """A validator result must not force reload when pyinfra's live file is unchanged."""

    class Result:
        def __init__(self, changed: bool) -> None:
            self.changed = changed

        def did_change(self) -> bool:
            return self.changed

    file_results = iter((Result(True), Result(False)))
    puts: list[str] = []
    services: list[tuple[str, dict[str, object]]] = []
    from pyinfra.operations import apt, files, systemd

    monkeypatch.setattr(apt, "packages", lambda **_kwargs: Result(False))
    monkeypatch.setattr(apt, "key", lambda **_kwargs: Result(False))
    monkeypatch.setattr(apt, "repo", lambda *_args, **_kwargs: Result(False))
    monkeypatch.setattr(
        files,
        "put",
        lambda _source, destination, **_kwargs: puts.append(destination) or next(file_results),
    )
    monkeypatch.setattr(caddy, "_validate_and_install_caddy", lambda *_args, **_kwargs: Result(True))
    monkeypatch.setattr(systemd, "service", lambda service, **kwargs: services.append((service, kwargs)))
    plan = CaddyPlan(
        CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
        (),
        ("caddy",),
        "taskman.acme.tld {\n}\n",
    )

    caddy.declare_caddy(plan)

    assert puts == ["/etc/taskman/Caddyfile.staged", "/etc/caddy/Caddyfile"]
    assert services[-1][1]["_if"]() is False


def test_declare_caddy_programmatic_replay_reports_no_change_when_converged(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The real Caddy declaration must preserve pyinfra's converged result."""

    staged_path = tmp_path / "Caddyfile.staged"
    live_path = tmp_path / "Caddyfile"
    monkeypatch.setattr(caddy, "_STAGED_CADDYFILE", staged_path.as_posix())
    monkeypatch.setattr(caddy, "_CADDYFILE", live_path.as_posix())

    class Result:
        def did_change(self) -> bool:
            return False

    from pyinfra.operations import apt, systemd

    monkeypatch.setattr(apt, "packages", lambda **_kwargs: Result())
    monkeypatch.setattr(apt, "key", lambda **_kwargs: Result())
    monkeypatch.setattr(apt, "repo", lambda *_args, **_kwargs: Result())
    monkeypatch.setattr(systemd, "service", lambda *_args, **_kwargs: Result())

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "caddy",
        "#!/bin/sh\nexit 0\n",
    )
    _write_executable(
        bin_dir / "sudo",
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -u|-g|-C) shift 2 ;;\n"
        "    -*) shift ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        "exec \"$@\"\n",
    )
    _write_executable(bin_dir / "chown", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "stat",
        "#!/bin/sh\n"
        "/usr/bin/stat \"$@\" | sed -E -e 's/^[^:]+:[^:]+:/root:root:/' -e 's/^user=[^ ]+ group=[^ ]+/user=root group=root/'\n",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    plan = CaddyPlan(
        CaddyRepository("https://example.test/key", "/keyring", "deb https://example.test stable"),
        (),
        ("caddy",),
        "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n",
    )

    def run_once() -> ChangeSet:
        inventory = Inventory((["@local"], {}))
        state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
        host = inventory.get_host("@local")
        state.activate_host(host)
        remote = PyinfraRemote(
            host,
            config(),
            inventory=inventory,
            state=state,
        )

        @deploy("Converge Caddy")
        def converge() -> None:
            caddy.declare_caddy(plan)

        return remote.run_deploy(converge)

    first = run_once()
    second = run_once()

    assert first.changed is True
    assert second == ChangeSet(changed=False)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
