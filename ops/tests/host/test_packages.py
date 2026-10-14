"""Package refresh decisions through the production pyinfra deploy and fake native tools."""

from __future__ import annotations

from pathlib import Path
import os
import shlex

import pytest
from pyinfra.api import Config, Inventory, State
from pyinfra.operations import apt, files, server

from taskman_ops.errors import OpsError
from taskman_ops.provisioning import ProvisioningInputs, taskman_provisioning
from taskman_ops.remote import PyinfraRemote
from taskman_ops.services.postgresql import build_postgresql_ownership_plan
from taskman_ops.services.systemd import build_systemd_plan
from tests.support.caddy import controller_plan
from tests.support.environments import environment_config
from tests.support.shell import write_shell_script
from tests.workflows.test_provision import _authority, _firewall_authority


_INSTALLED_BASELINE = (
    "ca-certificates", "curl", "gnupg", "python3-minimal", "unattended-upgrades", "ufw",
)


def _provision(tmp_path: Path, monkeypatch, *, installed, postgresql="existing", caddy="existing", fail_update=False, fail_dpkg=False):
    """Retain real dpkg facts and apt operations; replace unrelated/native privileged actions."""
    tools = tmp_path / "bin"
    tools.mkdir()
    events = tmp_path / "events"
    events.touch()
    dpkg = tmp_path / "installed"
    dpkg.write_text("".join(f"ii  {package}  1.0  all  synthetic package\n" for package in installed))
    quoted_events = shlex.quote(str(events))
    dpkg_command = 'echo "synthetic package inspection failure" >&2; exit 1' if fail_dpkg else f'cat {shlex.quote(str(dpkg))}'
    write_shell_script(tools / "dpkg", f'echo "dpkg $*" >> {quoted_events}\n{dpkg_command}')
    write_shell_script(tools / "apt-get", f'''echo "apt-get $*" >> {quoted_events}
case " $* " in
  *" update "*) exit {100 if fail_update else 0} ;;
  *" install "*) exit 0 ;;
  *) exit 99 ;;
esac''')
    # The old unconditional refresh touches a real stamp after success; keep the red run private too.
    write_shell_script(tools / "touch", 'test "$1" = /var/lib/apt/periodic/update-success-stamp')
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")

    def marker(event):
        server.shell(f"echo {shlex.quote(event)} >> {quoted_events}", name=event)

    monkeypatch.setattr(server, "user", lambda *_args, **_kwargs: marker("baseline-convergence"))
    monkeypatch.setattr(server, "service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(files, "directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(files, "put", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("taskman_ops.host.baseline.declare_firewall", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("taskman_ops.services.systemd.declare_systemd", lambda *_args: None)
    monkeypatch.setattr("taskman_ops.services.postgresql._configure_postgresql_cluster", lambda *_args, **_kwargs: marker("postgresql-configuration"))
    monkeypatch.setattr(apt, "key", lambda *_args, **_kwargs: marker("caddy-signed-key"))
    monkeypatch.setattr(apt, "repo", lambda *_args, **_kwargs: marker("caddy-signed-source"))

    config = environment_config(postgres_package_track="18")
    observation = _authority()["postgresql"]
    if postgresql == "bootstrap":
        observation = dict(branch="bootstrap", cluster=None, role="absent", database="absent", access="unproven")
    inputs = ProvisioningInputs(
        config=config, caddy_plan=controller_plan(config, branch=caddy),
        postgresql_plan=build_postgresql_ownership_plan(config, observation,
            force_postgresql_config=False),
        force_postgresql_config=False,
        firewall_authority=_firewall_authority(), systemd_plan=build_systemd_plan(config),
        runtime_environment=b"synthetic-runtime\n", pgpass=b"synthetic-pgpass\n",
        role_password_input=b"synthetic-password\n",
    )
    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host("@local")
    state.activate_host(host)
    # Force the legacy cache path to exhibit its unconditional refresh without reading native APT state.
    from pyinfra.facts.files import File
    original_get_fact = host.get_fact
    monkeypatch.setattr(host, "get_fact", lambda fact, *args, **kwargs:
        None if fact is File and kwargs.get("path") == "/var/lib/apt/periodic/update-success-stamp"
        else original_get_fact(fact, *args, **kwargs))
    remote = PyinfraRemote(host, config, inventory=inventory, state=state)
    if fail_update or fail_dpkg:
        with pytest.raises(OpsError):
            remote.run_deploy(taskman_provisioning, inputs=inputs)
    else:
        remote.run_deploy(taskman_provisioning, inputs=inputs)
    lines = events.read_text().splitlines()
    assert "dpkg -l" in lines
    return [line for line in lines if not line.startswith("dpkg ")]


@pytest.mark.parametrize("postgresql", ["existing", "bootstrap"])
def test_installed_required_packages_skip_refresh_and_install(tmp_path, monkeypatch, postgresql):
    """Unconditional update, or managing existing-cluster package tracks, breaks this contract."""
    installed = _INSTALLED_BASELINE + (("postgresql-18", "postgresql-client-18") if postgresql == "bootstrap" else ())
    events = _provision(tmp_path, monkeypatch, installed=installed, postgresql=postgresql)
    assert not any(event.startswith("apt-get ") for event in events)


@pytest.mark.parametrize("missing,postgresql", [("ufw", "existing"), ("postgresql-client-18", "bootstrap")])
def test_missing_required_package_refreshes_before_install(tmp_path, monkeypatch, missing, postgresql):
    """Checking only baseline packages misses branch prerequisites; installing before refresh is unsafe."""
    installed = tuple(package for package in (*_INSTALLED_BASELINE, "postgresql-18", "postgresql-client-18") if package != missing)
    events = _provision(tmp_path, monkeypatch, installed=installed, postgresql=postgresql)
    assert events[0] == "apt-get update"
    installs = [event for event in events if event.startswith("apt-get ") and " install " in f" {event} "]
    assert len(installs) == 1
    assert installs[0].split()[-1] == missing
    assert "--force-yes" not in installs[0] and "--allow-unauthenticated" not in installs[0]


def test_missing_caddy_branch_prerequisites_keep_signed_source_refresh(tmp_path, monkeypatch):
    """Ignoring Caddy's branch packages or removing its post-source refresh breaks authenticated installation."""
    events = _provision(tmp_path, monkeypatch, installed=_INSTALLED_BASELINE, caddy="bootstrap")
    assert events[0] == "apt-get update"
    installation = next(event for event in events if event.startswith("apt-get ") and " install " in f" {event} ")
    assert set(installation.split()[-3:]) == {"debian-keyring", "debian-archive-keyring", "apt-transport-https"}
    source = events.index("caddy-signed-source")
    assert events[source + 1] == "apt-get update"


def test_missing_caddy_package_refreshes_with_all_repository_prerequisites_installed(tmp_path, monkeypatch):
    """The specialized candidate package belongs in the branch check even though pyinfra does not install it."""
    installed = (*_INSTALLED_BASELINE, "debian-keyring", "debian-archive-keyring", "apt-transport-https")
    events = _provision(tmp_path, monkeypatch, installed=installed, caddy="bootstrap")
    assert events[0] == "apt-get update"
    assert events[events.index("caddy-signed-source") + 1] == "apt-get update"


def test_failed_required_refresh_stops_before_package_or_baseline_convergence(tmp_path, monkeypatch):
    """Continuing after a required refresh failure would silently bypass the authenticated APT boundary."""
    events = _provision(tmp_path, monkeypatch, installed=_INSTALLED_BASELINE,
                        postgresql="bootstrap", fail_update=True)
    assert events == ["apt-get update"]


def test_failed_package_inspection_stops_before_refresh_or_convergence(tmp_path, monkeypatch):
    """Unknown installed-package facts must fail rather than assume completeness or attempt installation."""
    assert _provision(tmp_path, monkeypatch, installed=_INSTALLED_BASELINE, fail_dpkg=True) == []
