"""Ordered UFW convergence for the supported single-host topology.

Firewall rules are deliberately one ordered command operation: preserving the
administrator's active SSH port must happen before UFW is enabled.  The plan
is nevertheless immutable and derived only from validated configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..pyinfra import conditional_convergence
from ..remote import Remote


@dataclass(frozen=True)
class FirewallPlan:
    """Validated UFW commands and the mandatory external follow-up check."""

    packages: tuple[str, ...]
    commands_before_enablement: tuple[tuple[str, ...], ...]
    enablement: tuple[str, ...]
    post_convergence_checks: tuple[tuple[str, ...], ...]


class FirewallOperations(Protocol):
    """The pyinfra command operation used for order-sensitive UFW changes."""

    def packages(self, packages: tuple[str, ...]) -> None: ...

    def converge_rules(self, plan: FirewallPlan) -> None: ...


def build_firewall_plan(config: EnvironmentConfig) -> FirewallPlan:
    """Return a UFW-only plan without inspecting mutable firewall state."""

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
        enablement=("ufw", "--force", "enable"),
        post_convergence_checks=(("ssh", "fresh-connection", str(config.ssh_port)),),
    )


def _declare_firewall(config: EnvironmentConfig, *, operations: FirewallOperations | None = None) -> FirewallPlan:
    """Declare UFW convergence while retaining the SSH-before-enable ordering.

    The caller runs :func:`verify_fresh_ssh_connection` only after pyinfra has
    executed these operations.  It must use a new SSH connection, not the one
    that applied UFW.
    """

    plan = build_firewall_plan(config)
    backend = operations or _PyinfraFirewallOperations()
    backend.packages(plan.packages)
    backend.converge_rules(plan)
    return plan


def apply_firewall(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    connector: Callable[[EnvironmentConfig], object] | None = None,
) -> bool:
    """Execute inspected UFW convergence and prove strict fresh SSH access.

    The caller must use this execution-time capability after the declarative
    pyinfra operations have run.  A successful existing SSH session is not
    treated as evidence: the postcondition is a new connection obtained through
    the configured strict host-key connector.
    """

    plan = build_firewall_plan(config)
    result = remote.run(("sh", "-c", render_firewall_convergence_script(plan)), sudo=True)
    if result.returncode == int(ExitStatus.SAFETY):
        raise OpsError(
            ExitStatus.SAFETY,
            "firewall",
            "existing UFW state is unrecognized or conflicts with the supported topology",
            changed=False,
            next_action="inspect and deliberately resolve the existing UFW rules before retrying",
        )
    if not result.succeeded:
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "firewall",
            "unable to converge UFW state",
            changed=False,
            next_action="inspect UFW and administrator SSH connectivity before retrying",
        )
    changed = _changed_result(result.stdout)
    verify_fresh_ssh_connection(config, connector=connector)
    return changed


def verify_fresh_ssh_connection(
    config: EnvironmentConfig,
    *,
    connector: Callable[[EnvironmentConfig], object] | None = None,
) -> None:
    """Require a newly opened strict-host-key SSH connection to execute ``true``."""

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
    """Render an inspected, conditional UFW transaction with a change marker."""

    if not isinstance(plan, FirewallPlan):
        raise TypeError("firewall script requires a firewall plan")
    before = "\n".join(" ".join(command) for command in plan.commands_before_enablement)
    expected_rules = "\n".join(_expected_ufw_rules(plan))
    return f"""set -eu
unsafe() {{ echo 'unrecognized or conflicting UFW rule' >&2; exit {int(ExitStatus.SAFETY)}; }}
before=$(LC_ALL=C ufw status numbered)
case \"$before\" in
  'Status: inactive'*)
    saved=$(LC_ALL=C ufw show added)
    case \"$saved\" in
      '') ;;
      'Added user rules '*)
        saved_rules=$(printf '%s\\n' \"$saved\" | sed '1d' | sed '/^$/d')
        [ -z \"$saved_rules\" ] || unsafe
        ;;
      *) unsafe ;;
    esac
    {before}
    ufw --force enable
    printf 'changed=1\\n'
    ;;
  'Status: active'*)
    verbose=$(LC_ALL=C ufw status verbose)
    printf '%s\\n' \"$verbose\" | grep -Fq 'Default: deny (incoming)' || unsafe
    actual=$(printf '%s\\n' \"$before\" | sed -n -E '/^[[:space:]]*\\[[[:space:]]*[0-9]+\\][[:space:]]+/ {{ s/^[[:space:]]*\\[[[:space:]]*[0-9]+\\][[:space:]]+//; s/[[:space:]]+\\(v6\\)//g; s/[[:space:]]+/ /g; s/^ //; s/ $//; p; }}' | LC_ALL=C sort -u)
    expected=$(cat <<'TASKMAN_UFW_RULES' | LC_ALL=C sort -u
{expected_rules}
TASKMAN_UFW_RULES
)
    [ \"$actual\" = \"$expected\" ] || unsafe
    printf 'changed=0\\n'
    ;;
  *) unsafe ;;
esac
"""


def render_firewall_convergence_probe(plan: FirewallPlan) -> str:
    """Return a non-mutating UFW drift probe for truthful pyinfra reporting."""

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


def _expected_ufw_rules(plan: FirewallPlan) -> tuple[str, ...]:
    entries: list[str] = []
    for command in plan.commands_before_enablement:
        if command[0] != "ufw" or command[1] not in {"allow", "deny"}:
            continue
        action = "ALLOW" if command[1] == "allow" else "DENY"
        entries.append(f"{command[2]} {action} IN Anywhere")
    return tuple(entries)


def _changed_result(stdout: str) -> bool:
    values = [line.removeprefix("changed=") for line in stdout.splitlines() if line.startswith("changed=")]
    if values == ["0"]:
        return False
    if values == ["1"]:
        return True
    raise OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "firewall",
        "UFW convergence returned an invalid change result",
        changed=False,
        next_action="inspect the UFW convergence command output before retrying",
    )


def _fresh_ssh_error() -> OpsError:
    return OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "firewall",
        "fresh strict-host-key SSH verification failed after UFW convergence",
        changed=False,
        next_action="restore administrator SSH reachability and verify the pinned host key before retrying",
    )


class _PyinfraFirewallOperations:
    def packages(self, packages: tuple[str, ...]) -> None:
        from pyinfra.operations import apt

        apt.packages(packages=list(packages))

    def converge_rules(self, plan: FirewallPlan) -> None:
        conditional_convergence(
            probe=render_firewall_convergence_probe(plan),
            script=render_firewall_convergence_script(plan),
        )


__all__ = [
    "FirewallPlan",
    "build_firewall_plan",
    "apply_firewall",
    "render_firewall_convergence_script",
    "render_firewall_convergence_probe",
    "verify_fresh_ssh_connection",
]
