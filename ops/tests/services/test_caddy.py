from __future__ import annotations

from tests.test_config import valid_environment
from taskman_ops.config import EnvironmentConfig
import taskman_ops.services.caddy as caddy
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository, build_caddy_plan, render_caddy_install_script, render_caddyfile


def config(**overrides: object) -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(**overrides))


def test_caddy_plan_uses_the_official_signed_repository_and_loopback_renderer() -> None:
    plan = build_caddy_plan(config())

    assert plan.repository.key_url == "https://dl.cloudsmith.io/public/caddy/stable/gpg.key"
    assert plan.repository.keyring == "/usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    assert "signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg" in plan.repository.source
    assert plan.packages == ("caddy",)
    assert render_caddyfile(config(application_port=4011)) == "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4011\n}\n"


def test_caddy_validation_precedes_replacement_of_the_live_public_configuration() -> None:
    script = render_caddy_install_script("/etc/taskman/Caddyfile.staged", "/etc/caddy/Caddyfile")

    assert script.index("caddy validate") < script.index("install -o root -g root -m 0644")
    assert "systemctl" not in script
    assert "taskman.service" not in script


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
        lambda source, destination, **_kwargs: staged.append((source.getvalue(), destination)),
    )
    monkeypatch.setattr(systemd, "service", lambda service, **kwargs: services.append((service, kwargs)))
    monkeypatch.setattr(
        caddy,
        "_validate_and_install_caddy",
        lambda staged_path, live_path, **_kwargs: validated.append((staged_path, live_path)) or ValidatedConfiguration(),
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
    assert staged == [(plan.caddyfile, "/etc/taskman/Caddyfile.staged")]
    assert validated == [("/etc/taskman/Caddyfile.staged", "/etc/caddy/Caddyfile")]
    assert services[0] == (
        "caddy",
        {"running": True, "enabled": True, "name": "Enable and start Caddy"},
    )
    assert services[1][0] == "caddy"
    assert services[1][1]["reloaded"] is True
    assert services[1][1]["name"] == "Reload Caddy after validated replacement"
    assert services[1][1]["_if"]() is True
