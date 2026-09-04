from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from tests.test_config import valid_environment
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
import taskman_ops.host.firewall as firewall
from taskman_ops.host.firewall import apply_firewall, build_firewall_plan, render_firewall_convergence_script
from taskman_ops.remote import CommandResult
from tests.fakes import ScriptedRemote


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment())


def test_firewall_allows_the_active_ssh_port_before_any_enablement_and_only_exposes_http_https() -> None:
    """Moving enablement above SSH access or exposing an application port must fail this."""

    plan = build_firewall_plan(config())

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
    assert plan.enablement == ("ufw", "--force", "enable")


def test_firewall_plan_requires_a_fresh_ssh_check_after_convergence() -> None:
    """Dropping the post-firewall connection verification must fail this."""

    plan = build_firewall_plan(config())

    assert plan.post_convergence_checks == (("ssh", "fresh-connection", "2202"),)


def test_firewall_does_not_export_an_ungated_mutating_pyinfra_capability() -> None:
    """Scheduling a UFW mutation without the fresh SSH gate is not public API."""

    assert "converge_firewall" not in firewall.__all__


def test_firewall_execution_refuses_preexisting_conflicting_or_unrecognized_rules() -> None:
    """An existing allow for Taskman's private port must not be masked by a later deny."""

    script = render_firewall_convergence_script(build_firewall_plan(config()))

    assert "ufw status numbered" in script
    assert "unrecognized or conflicting UFW rule" in script
    assert "ufw allow 2202/tcp" in script
    assert script.index("ufw allow 2202/tcp") < script.index("ufw --force enable")


def test_firewall_script_executable_refuses_an_existing_allow_for_a_private_port(tmp_path: Path) -> None:
    """A later deny must not mask an earlier active UFW allow rule."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    changes = tmp_path / "changes.log"
    _fake_ufw(
        bin_dir / "ufw",
        changes,
        """case \"$1 ${2-}\" in
  'status numbered') printf 'Status: active\\n[ 1] 4000/tcp ALLOW IN Anywhere\\n' ;;
  'status verbose') printf 'Status: active\\nDefault: deny (incoming), allow (outgoing), disabled (routed)\\n' ;;
  *) printf '%s\\n' \"$*\" >> \"$TASKMAN_UFW_CHANGES\" ;;
esac""",
    )
    completed = subprocess.run(
        ("sh", "-ceu", render_firewall_convergence_script(build_firewall_plan(config()))),
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
    _fake_ufw(
        bin_dir / "ufw",
        changes,
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
        ("sh", "-ceu", render_firewall_convergence_script(build_firewall_plan(config()))),
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
    _fake_ufw(
        bin_dir / "ufw",
        changes,
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
        ("sh", "-ceu", render_firewall_convergence_script(build_firewall_plan(config()))),
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
    assert completed.stdout == "changed=0\n"


def test_firewall_execution_requires_a_fresh_connection_after_the_remote_transaction() -> None:
    """A metadata-only SSH check must not let a firewall transaction succeed."""

    remote = ScriptedRemote.from_responses([CommandResult(0, "changed=0\n")])
    fresh = _FreshRemote()
    calls = 0

    def connector(_config: EnvironmentConfig) -> _FreshRemote:
        nonlocal calls
        calls += 1
        return fresh

    changed = apply_firewall(remote, config(), connector=connector)

    assert changed is False
    assert calls == 1
    assert fresh.calls == [("true",)]
    assert fresh.closed is True


def test_firewall_refusal_prevents_the_post_execution_connection_check() -> None:
    remote = ScriptedRemote.from_responses([CommandResult(int(ExitStatus.SAFETY), stderr="unsafe firewall")])

    with pytest.raises(OpsError) as raised:
        apply_firewall(remote, config(), connector=lambda _config: pytest.fail("must not reconnect"))

    assert raised.value.status is ExitStatus.SAFETY


class _FreshRemote:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.closed = False

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        self.calls.append(argv)
        return CommandResult(0)

    def close(self) -> None:
        self.closed = True


def _fake_ufw(path: Path, changes: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
