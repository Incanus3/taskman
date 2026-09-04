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
from taskman_ops.services.caddy import (
    apply_caddy_install,
    build_caddy_plan,
    render_caddy_install_script,
    render_caddyfile,
)
from tests.fakes import ScriptedRemote


def config(**overrides: object) -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(**overrides))


def test_caddy_plan_uses_the_official_authenticated_repository_and_staged_validation() -> None:
    """Changing the signed repository, skipping validation, or exposing Phoenix must fail this."""

    plan = build_caddy_plan(config())

    assert plan.repository.key_url == "https://dl.cloudsmith.io/public/caddy/stable/gpg.key"
    assert plan.repository.keyring == "/usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    assert plan.repository.source == (
        "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] "
        "https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main"
    )
    assert plan.repository_packages == (
        "ca-certificates",
        "curl",
        "gnupg",
        "debian-keyring",
        "debian-archive-keyring",
        "apt-transport-https",
    )
    assert plan.packages == (
        "caddy",
    )
    assert plan.staged_validation == (
        "caddy",
        "validate",
        "--config",
        "/etc/taskman/Caddyfile.staged",
        "--adapter",
        "caddyfile",
    )


def test_caddy_renderer_installs_the_validated_public_host_in_the_minimal_loopback_topology() -> None:
    """Dropping hostname rendering or proxying to a public Phoenix listener must fail this."""

    rendered = render_caddyfile(config())

    assert rendered == "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n"


def test_caddy_renderer_uses_the_validated_application_port_instead_of_a_fixed_default() -> None:
    rendered = render_caddyfile(config(application_port=4011))

    assert rendered == "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4011\n}\n"


def test_caddy_plan_never_starts_taskman_before_a_release_transaction() -> None:
    """Adding Taskman's unit to proxy convergence would violate first-release ordering."""

    plan = build_caddy_plan(config())

    assert plan.service_enablement == ("caddy.service",)
    assert plan.forbidden_service_starts == ("taskman.service",)


def test_caddy_install_script_repairs_owned_mode_drift_and_validates_before_reload(tmp_path: Path) -> None:
    """Identical bytes with unsafe metadata must still be installed and reloaded."""

    staged = tmp_path / "Caddyfile.staged"
    destination = tmp_path / "Caddyfile"
    staged.write_text("taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n", encoding="utf-8")
    destination.write_text(staged.read_text(encoding="utf-8"), encoding="utf-8")
    destination.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "operations.log"
    _fake_executable(bin_dir / "caddy", f"printf 'validate\\n' >> {log}")
    _fake_executable(
        bin_dir / "systemctl",
        f"case \"$1\" in is-enabled|is-active) exit 0;; *) printf '%s\\n' \"$*\" >> {log};; esac",
    )

    first = subprocess.run(
        (
            "sh",
            "-ceu",
            render_caddy_install_script(
                staged.as_posix(),
                destination.as_posix(),
                owner=pwd.getpwuid(os.getuid()).pw_name,
                group=grp.getgrgid(os.getgid()).gr_name,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    second = subprocess.run(
        (
            "sh",
            "-ceu",
            render_caddy_install_script(
                staged.as_posix(),
                destination.as_posix(),
                owner=pwd.getpwuid(os.getuid()).pw_name,
                group=grp.getgrgid(os.getgid()).gr_name,
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == "changed=1\n"
    assert second.stdout == "changed=0\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644
    assert (destination.stat().st_uid, destination.stat().st_gid) == (os.getuid(), os.getgid())
    assert log.read_text(encoding="utf-8").splitlines() == ["validate", "reload caddy.service"]


def test_caddy_execution_adapter_reports_the_script_change_marker() -> None:
    """A pyinfra-facing convergence result must distinguish mutation from a no-op."""

    plan = build_caddy_plan(config())

    assert apply_caddy_install(ScriptedRemote.from_responses([CommandResult(0, "changed=1\n")]), plan).changed is True
    assert apply_caddy_install(ScriptedRemote.from_responses([CommandResult(0, "changed=0\n")]), plan).changed is False


def _fake_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
