from __future__ import annotations

import os
from pathlib import Path
import subprocess

from pyinfra.api import Config, Inventory, State, deploy
from tests.support.environments import environment_config
from tests.support.shell import write_shell_script
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus
import taskman_ops.host.firewall as firewall
from taskman_ops.host.firewall import (
    build_firewall_plan,
    render_firewall_convergence_script,
    verify_fresh_ssh_connection,
)
from taskman_ops.remote import ChangeSet, CommandResult, PyinfraRemote


def test_firewall_allows_the_active_ssh_port_before_any_enablement_and_only_exposes_http_https() -> None:
    """Moving enablement above SSH access or exposing an application port must fail this."""

    plan = build_firewall_plan(environment_config())

    assert plan.packages == ("ufw",)
    assert plan.commands_before_enablement == (
        ("ufw", "allow", "2202/tcp"),
        ("ufw", "allow", "80/tcp"),
        ("ufw", "allow", "443/tcp"),
        ("ufw", "deny", "4000/tcp"),
        ("ufw", "deny", "5432/tcp"),
        ("ufw", "deny", "6789/tcp"),
        ("ufw", "deny", "4369/tcp"),
        ("ufw", "default", "deny", "incoming"),
    )


def test_firewall_does_not_export_an_ungated_mutating_pyinfra_capability() -> None:
    """Scheduling a UFW mutation without the fresh SSH gate is not public API."""

    assert "converge_firewall" not in firewall.__all__


def test_firewall_keeps_builtin_package_convergence_outside_one_lockout_safe_action(monkeypatch) -> None:
    """Replacing the direct UFW action with a generic adapter would fail this boundary test."""

    package_calls: list[dict[str, object]] = []
    activated: list[tuple[object, object]] = []
    from pyinfra.operations import apt

    monkeypatch.setattr(apt, "packages", lambda **kwargs: package_calls.append(kwargs))
    monkeypatch.setattr(
        firewall,
        "_activate_firewall_with_fresh_ssh",
        lambda plan, value, **_kwargs: activated.append((plan, value)),
        raising=False,
    )
    monkeypatch.setattr(
        firewall,
        "conditional_convergence",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("UFW must not use generic convergence")),
        raising=False,
    )

    expected = build_firewall_plan(environment_config())
    assert firewall.declare_firewall(environment_config()) == expected

    assert package_calls == [{"packages": ["ufw"], "name": "Install UFW"}]
    assert activated == [(expected, environment_config())]


def test_firewall_execution_refuses_preexisting_conflicting_or_unrecognized_rules() -> None:
    """An existing allow for Taskman's private port must not be masked by a later deny."""

    script = render_firewall_convergence_script(build_firewall_plan(environment_config()))

    assert "ufw status numbered" in script
    assert "unrecognized or conflicting UFW rule" in script
    assert "ufw allow 2202/tcp" in script
    assert script.index("ufw allow 2202/tcp") < script.index("ufw --force enable")


def test_firewall_script_executable_refuses_an_existing_allow_for_a_private_port(tmp_path: Path) -> None:
    """A later deny must not mask an earlier active UFW allow rule."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    changes = tmp_path / "changes.log"
    write_shell_script(
        bin_dir / "ufw",
        """case \"$1 ${2-}\" in
  'status numbered') printf 'Status: active\\n[ 1] 4000/tcp ALLOW IN Anywhere\\n' ;;
  'status verbose') printf 'Status: active\\nDefault: deny (incoming), allow (outgoing), disabled (routed)\\n' ;;
  *) printf '%s\\n' \"$*\" >> \"$TASKMAN_UFW_CHANGES\" ;;
esac""",
    )
    completed = subprocess.run(
        ("sh", "-ceu", render_firewall_convergence_script(build_firewall_plan(environment_config()))),
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TASKMAN_UFW_CHANGES": changes.as_posix(),
        },
    )

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert not changes.exists()


def test_firewall_script_executable_refuses_saved_rules_before_enabling_an_inactive_ufw(
    tmp_path: Path,
) -> None:
    """Inactive UFW can still retain rules that would take effect on enablement."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    changes = tmp_path / "changes.log"
    write_shell_script(
        bin_dir / "ufw",
        """case \"$1 ${2-}\" in
  'status numbered') printf 'Status: inactive\\n' ;;
  'show added') cat <<'RULES'
Added user rules (see 'ufw status' for running firewall):
ufw allow 4000/tcp
RULES
  ;;
  *) printf '%s\\n' \"$*\" >> \"$TASKMAN_UFW_CHANGES\" ;;
esac""",
    )

    completed = subprocess.run(
        ("sh", "-ceu", render_firewall_convergence_script(build_firewall_plan(environment_config()))),
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TASKMAN_UFW_CHANGES": changes.as_posix(),
        },
    )

    assert completed.returncode == int(ExitStatus.SAFETY)
    assert not changes.exists()


def test_firewall_script_executable_accepts_only_the_known_owned_rule_set_as_a_noop(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    changes = tmp_path / "changes.log"
    expected = (
        (2202, "ALLOW"),
        (80, "ALLOW"),
        (443, "ALLOW"),
        (4000, "DENY"),
        (5432, "DENY"),
        (6789, "DENY"),
        (4369, "DENY"),
    )
    rules = "\n".join(
        [
            *(
                f"[ {index}] {port}/tcp             {action} IN    Anywhere"
                for index, (port, action) in enumerate(expected, start=1)
            ),
            *(
                f"[{index + len(expected):2d}] {port}/tcp (v6)        {action} IN    Anywhere (v6)"
                for index, (port, action) in enumerate(expected, start=1)
            ),
        ]
    )
    write_shell_script(
        bin_dir / "ufw",
        f"""case \"$1 ${{2-}}\" in
  'status numbered') cat <<'RULES'
Status: active

     To                         Action      From
     --                         ------      ----
{rules}
RULES
  ;;
  'status verbose') printf 'Status: active\\nDefault: deny (incoming), allow (outgoing), disabled (routed)\\n' ;;
  *) : ;;
esac""",
    )

    completed = subprocess.run(
        ("sh", "-ceu", render_firewall_convergence_script(build_firewall_plan(environment_config()))),
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TASKMAN_UFW_CHANGES": changes.as_posix(),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""


def test_direct_firewall_operation_reports_no_change_for_the_known_owned_rule_set(monkeypatch, tmp_path: Path) -> None:
    """Yielding a direct action for an already-safe policy must fail this."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    expected = (
        (2202, "ALLOW"),
        (80, "ALLOW"),
        (443, "ALLOW"),
        (4000, "DENY"),
        (5432, "DENY"),
        (6789, "DENY"),
        (4369, "DENY"),
    )
    rules = "\n".join(
        [
            *(f"[ {index}] {port}/tcp {action} IN Anywhere" for index, (port, action) in enumerate(expected, start=1)),
            *(
                f"[{index + len(expected):2d}] {port}/tcp (v6) {action} IN Anywhere (v6)"
                for index, (port, action) in enumerate(expected, start=1)
            ),
        ]
    )
    write_shell_script(
        bin_dir / "ufw",
        f"""case \"$1 ${{2-}}\" in
  'status numbered') cat <<'RULES'
Status: active
{rules}
RULES
  ;;
  'status verbose') printf 'Status: active\\nDefault: deny (incoming), allow (outgoing), disabled (routed)\\n' ;;
  *) exit 1 ;;
esac""",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(firewall, "verify_fresh_ssh_connection", lambda _config: None)

    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host("@local")
    state.activate_host(host)
    remote = PyinfraRemote(host, environment_config(), inventory=inventory, state=state)

    @deploy("Converged direct firewall")
    def converge() -> None:
        firewall._activate_firewall_with_fresh_ssh(
            build_firewall_plan(environment_config()),
            environment_config(),
            name="Activate Taskman firewall",
            _sudo=False,
        )

    assert remote.run_deploy(converge) == ChangeSet(changed=False)


def test_firewall_boundary_requires_a_fresh_strict_connection() -> None:
    """A metadata-only SSH check must not let firewall activation succeed."""

    fresh = _FreshRemote()
    calls = 0

    def connector(_config: EnvironmentConfig) -> _FreshRemote:
        nonlocal calls
        calls += 1
        return fresh

    verify_fresh_ssh_connection(environment_config(), connector=connector)

    assert calls == 1
    assert fresh.calls == [("true",)]
    assert fresh.closed is True


class _FreshRemote:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.closed = False

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        self.calls.append(argv)
        return CommandResult(0)

    def close(self) -> None:
        self.closed = True
