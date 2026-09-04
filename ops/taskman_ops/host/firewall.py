"""The lockout-sensitive UFW boundary for provisioning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pyinfra.api import operation
from pyinfra.api.command import FunctionCommand, QuoteString, StringCommand

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from .pyinfra_support import operation_sudo, probe_changed


@dataclass(frozen=True)
class FirewallPlan:
    packages: tuple[str, ...]
    commands_before_enablement: tuple[tuple[str, ...], ...]


def build_firewall_plan(config: EnvironmentConfig) -> FirewallPlan:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("firewall plan requires an environment configuration")
    return FirewallPlan(
        packages=("ufw",),
        commands_before_enablement=(
            ("ufw", "allow", f"{config.ssh_port}/tcp"),
            ("ufw", "allow", "80/tcp"),
            ("ufw", "allow", "443/tcp"),
            ("ufw", "deny", f"{config.application_port}/tcp"),
            ("ufw", "deny", f"{config.database_port}/tcp"),
            ("ufw", "deny", f"{config.distribution_port}/tcp"),
            ("ufw", "deny", "4369/tcp"),
            ("ufw", "default", "deny", "incoming"),
        ),
    )


def declare_firewall(config: EnvironmentConfig) -> FirewallPlan:
    """Declare UFW activation and its immediately ordered lockout check.

    UFW is custom because enabling a conflicting policy can sever the only
    administrative path. The operation validates existing rules, opens the
    configured SSH port before activation, then requires a fresh strict
    pinned-host-key connection. Ordinary UFW package installation stays built
    into pyinfra.
    """

    from pyinfra.operations import apt

    plan = build_firewall_plan(config)
    apt.packages(packages=list(plan.packages), name="Install UFW")
    _activate_firewall_with_fresh_ssh(
        plan,
        config,
        name="Activate Taskman firewall",
        _sudo=True,
    )
    return plan


def verify_fresh_ssh_connection(
    config: EnvironmentConfig,
    *,
    connector: Callable[[EnvironmentConfig], object] | None = None,
) -> None:
    """Require a new strict pinned-host-key SSH connection to execute ``true``."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("fresh SSH verification requires an environment configuration")
    if connector is None:
        from ..remote import connect

        connector = connect

    connection = connector(config)
    if connection is None:
        raise _fresh_ssh_error()
    try:
        run = getattr(connection, "run", None)
        if not callable(run) or not run(("true",)).succeeded:
            raise _fresh_ssh_error()
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def render_firewall_convergence_script(plan: FirewallPlan) -> str:
    if not isinstance(plan, FirewallPlan):
        raise TypeError("firewall script requires a firewall plan")
    before = "\n".join(" ".join(command) for command in plan.commands_before_enablement)
    expected_rules = "\n".join(_expected_ufw_rules(plan))
    return f"""set -eu
unsafe() {{ echo 'unrecognized or conflicting UFW rule' >&2; exit {int(ExitStatus.SAFETY)}; }}
before=$(LC_ALL=C ufw status numbered)
case "$before" in
  'Status: inactive'*)
    saved=$(LC_ALL=C ufw show added)
    case "$saved" in
      '') ;;
      'Added user rules '*)
        saved_rules=$(printf '%s\\n' "$saved" | sed '1d' | sed '/^$/d')
        case "$saved_rules" in
          ''|'(None)') ;;
          *) unsafe ;;
        esac
        ;;
      *) unsafe ;;
    esac
    {before}
    ufw --force enable
    ;;
  'Status: active'*)
    verbose=$(LC_ALL=C ufw status verbose)
    printf '%s\\n' "$verbose" | grep -Fq 'Default: deny (incoming)' || unsafe
    actual=$(printf '%s\\n' "$before" | sed -n -E '/^[[:space:]]*\\[[[:space:]]*[0-9]+\\][[:space:]]+/ {{ s/^[[:space:]]*\\[[[:space:]]*[0-9]+\\][[:space:]]+//; s/[[:space:]]+\\(v6\\)//g; s/[[:space:]]+/ /g; s/^ //; s/ $//; p; }}' | LC_ALL=C sort -u)
    expected=$(cat <<'TASKMAN_UFW_RULES' | LC_ALL=C sort -u
{expected_rules}
TASKMAN_UFW_RULES
)
    [ "$actual" = "$expected" ] || unsafe
    ;;
  *) unsafe ;;
esac
"""


@operation(is_idempotent=True)
def _activate_firewall_with_fresh_ssh(plan: FirewallPlan, config: EnvironmentConfig):
    """Protect the administrator path through ordered UFW activation and a new SSH connection."""

    from pyinfra.context import state

    if state.is_executing and not _firewall_requires_activation(plan):
        return
    yield FunctionCommand(_run_firewall_activation, (plan,), {})
    yield FunctionCommand(verify_fresh_ssh_connection, (config,), {})


def _firewall_requires_activation(plan: FirewallPlan) -> bool:
    """Read the live UFW policy immediately before deciding whether to activate it."""

    from pyinfra.context import host

    runner = getattr(host, "run_shell_command", None)
    if not callable(runner):
        raise RuntimeError("invalid pyinfra host for firewall activation")
    succeeded, output = runner(
        StringCommand(_render_firewall_change_probe(plan)),
        print_output=False,
        print_input=False,
        _sudo=operation_sudo(host),
    )
    if not succeeded:
        raise RuntimeError("firewall state inspection failed")
    return probe_changed(output, "firewall state inspection")


def _run_firewall_activation(state: object, host: object, plan: FirewallPlan) -> None:
    """Preserve a UFW safety refusal while pyinfra runs the guarded action."""

    runner = getattr(host, "run_shell_command", None)
    if not callable(runner):
        raise RuntimeError("invalid pyinfra host for firewall activation")
    script = render_firewall_convergence_script(plan)
    wrapped = StringCommand(
        "sh",
        "-c",
        QuoteString(
            f"({script}); taskman_status=$?; "
            "printf '__taskman_firewall_status=%s\\n' \"$taskman_status\"; exit 0"
        ),
    )
    succeeded, output = runner(
        wrapped,
        print_output=False,
        print_input=False,
        _sudo=operation_sudo(host),
    )
    if not succeeded:
        raise RuntimeError("firewall activation transport failed")
    status = _captured_status(output, "__taskman_firewall_status=")
    if status == 0:
        return
    failure = _firewall_refusal()
    if status == int(failure.status):
        setattr(state, "_taskman_categorized_error", failure)
        raise failure
    raise RuntimeError("firewall activation failed")


def _render_firewall_change_probe(plan: FirewallPlan) -> str:
    if not isinstance(plan, FirewallPlan):
        raise TypeError("firewall probe requires a firewall plan")
    expected_rules = "\n".join(_expected_ufw_rules(plan))
    return f"""set -eu
before=$(LC_ALL=C ufw status numbered)
case "$before" in
  'Status: active'*)
    verbose=$(LC_ALL=C ufw status verbose)
    actual=$(printf '%s\\n' "$before" | sed -n -E '/^[[:space:]]*\\[[[:space:]]*[0-9]+\\][[:space:]]+/ {{ s/^[[:space:]]*\\[[[:space:]]*[0-9]+\\][[:space:]]+//; s/[[:space:]]+\\(v6\\)//g; s/[[:space:]]+/ /g; s/^ //; s/ $//; p; }}' | LC_ALL=C sort -u)
    expected=$(cat <<'TASKMAN_UFW_RULES' | LC_ALL=C sort -u
{expected_rules}
TASKMAN_UFW_RULES
)
    if printf '%s\\n' "$verbose" | grep -Fq 'Default: deny (incoming)' && [ "$actual" = "$expected" ]; then printf 'changed=0\\n'; else printf 'changed=1\\n'; fi
    ;;
  *) printf 'changed=1\\n' ;;
esac
"""


def _captured_status(output: object, prefix: str) -> int:
    lines = getattr(output, "stdout_lines", ())
    markers = [line.removeprefix(prefix) for line in lines if line.startswith(prefix)]
    if len(markers) != 1 or not markers[0].isdecimal():
        raise RuntimeError("firewall activation returned an invalid status")
    return int(markers[0])


def _expected_ufw_rules(plan: FirewallPlan) -> tuple[str, ...]:
    entries: list[str] = []
    for command in plan.commands_before_enablement:
        if command[0] != "ufw" or command[1] not in {"allow", "deny"}:
            continue
        action = "ALLOW" if command[1] == "allow" else "DENY"
        entries.append(f"{command[2]} {action} IN Anywhere")
    return tuple(entries)


def _fresh_ssh_error() -> OpsError:
    return OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "firewall",
        "fresh strict-host-key SSH verification failed after UFW convergence",
        changed=False,
        next_action="restore administrator SSH reachability and verify the pinned host key before retrying",
    )


def _firewall_refusal() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "firewall",
        "existing UFW policy is conflicting or unrecognized and will not be replaced",
        changed=False,
        next_action="inspect the active UFW rules and resolve the contradiction before retrying",
    )


__all__ = [
    "FirewallPlan",
    "build_firewall_plan",
    "declare_firewall",
    "render_firewall_convergence_script",
    "verify_fresh_ssh_connection",
]
