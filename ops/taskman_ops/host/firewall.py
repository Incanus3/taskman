"""Bind ordinary UFW helper authority to provisioning and fresh SSH verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pyinfra.api import operation
from pyinfra.api.command import FunctionCommand

from ..config import EnvironmentConfig
from ..errors import ExitStatus, HelperTransportError, OpsError
from ..helper_client.package import temporary_helper_package
from ..helper_client.runner import invoke_helper, new_correlation_id
from ..host_protocol import (
    PROTOCOL_VERSION, HostRequest, HostResult, ProtocolError,
    validate_firewall_request, validate_firewall_result,
)
from ..remote import PyinfraRemote, merge_change_warnings


@dataclass(frozen=True)
class FirewallPlan:
    packages: tuple[str, ...]
    proposed: tuple[tuple[str, ...], ...]
    authority: HostResult


def _request(config: EnvironmentConfig, *, mode: str = "inspect", digest: str | None = None,
             correlation_id: str | None = None) -> HostRequest:
    if not isinstance(config, EnvironmentConfig):
        raise _error(ExitStatus.INVALID, "invalid_input")
    request = HostRequest(
        protocol_version=PROTOCOL_VERSION, operation="firewall",
        correlation_id=correlation_id or new_correlation_id(), paths={},
        expected_state={"snapshot_sha256": digest},
        parameters={"mode": mode, "ports": {
            "ssh": config.ssh_port, "application": config.application_port,
            "database": config.database_port, "distribution": config.distribution_port,
        }},
    )
    try:
        return validate_firewall_request(request)
    except (ProtocolError, TypeError, ValueError):
        raise _error(ExitStatus.INVALID, "invalid_input") from None


def _invoke(remote: object, request: HostRequest) -> HostResult:
    convergence = request.parameters["mode"] == "converge"
    try:
        with temporary_helper_package() as package:
            result = invoke_helper(remote, package, request)
    except OpsError as error:
        possible = convergence and isinstance(error, HelperTransportError) and error.helper_entry_dispatched
        raise _error(ExitStatus.REMOTE_PREFLIGHT, "inspection_failed",
                     changed=error.changed or possible, warnings=error.warnings) from None
    except Exception:
        # Packaging and pre-dispatch validation failures are local/read-only.
        # The runner wraps every dispatched transport failure with its boundary.
        raise _error(ExitStatus.REMOTE_PREFLIGHT, "inspection_failed") from None
    try:
        validate_firewall_result(request, result)
    except (ProtocolError, TypeError, ValueError, AttributeError):
        # Untrusted flags cannot establish unchanged work after dispatch.
        warnings = (("transient helper cleanup was incomplete",)
                    if isinstance(result, HostResult) and result.local_cleanup_incomplete else ())
        raise _error(ExitStatus.REMOTE_PREFLIGHT, "inspection_failed",
                     changed=convergence, warnings=warnings) from None
    if result.outcome != "succeeded" and not (
        not convergence and result.state["reason"] == "ufw_missing"
    ):
        reason = result.state["reason"]
        status = (ExitStatus.INVALID if reason == "invalid_input" else
                  ExitStatus.SAFETY if result.outcome == "refused" else ExitStatus.REMOTE_PREFLIGHT)
        raise _error(status, reason,
                     changed=result.state["changed"] or result.state["possibly_changed"],
                     warnings=result.warnings)
    return result


def inspect_firewall(remote: object, config: EnvironmentConfig) -> HostResult:
    """Inspect through the same protected transient archive used by convergence."""
    return _invoke(remote, _request(config))


def validate_firewall_authority(config: EnvironmentConfig, result: HostResult | None) -> HostResult:
    """Require a closed read-only result before admitting a provisioning plan."""
    if not isinstance(result, HostResult):
        raise _error(ExitStatus.SAFETY, "missing firewall authority")
    request = _request(config, correlation_id=result.correlation_id)
    try:
        validate_firewall_result(request, result)
    except (ProtocolError, TypeError, ValueError):
        raise _error(ExitStatus.REMOTE_PREFLIGHT, "invalid firewall authority") from None
    if result.outcome != "succeeded" and result.state["reason"] != "ufw_missing":
        status = ExitStatus.INVALID if result.state["reason"] == "invalid_input" else (
            ExitStatus.SAFETY if result.outcome == "refused" else ExitStatus.REMOTE_PREFLIGHT)
        raise _error(status, result.state["reason"], warnings=result.warnings)
    if result.state["administrator_connection"] is None:
        raise _error(ExitStatus.REMOTE_PREFLIGHT, "missing administrator identity")
    return result


def _empty_delta(config: EnvironmentConfig, identity: object):
    """Reuse helper-owned policy to preview the maximum installation additions."""
    from ..host_helper.operations.firewall import plan_firewall
    from ..host_helper.ufw_adapter import UfwSnapshot

    snapshot = UfwSnapshot(False, (
        ("default_forward_policy", "DROP"), ("default_input_policy", "DROP"),
        ("default_output_policy", "ACCEPT"), ("ipv6", "yes"),
    ), ())
    return plan_firewall(snapshot, _request(config).parameters["ports"], identity)


def firewall_preview(config: EnvironmentConfig, confirmed: HostResult) -> dict[str, object]:
    authority = validate_firewall_authority(config, confirmed)
    missing = authority.state["reason"] == "ufw_missing"
    proposed = (_empty_delta(config, authority.state["administrator_connection"]).proposed
                if missing else authority.state["proposed"])
    return {
        "ownership_assumption": "ordinary UFW-managed rules; no custom hooks or independent filtering",
        "package_installation": missing,
        "post_install_gate": ("preserve compatible saved policy; actual coverage may reduce these maximum fallback additions"
                              if missing else None),
        "administrator_connection": dict(authority.state["administrator_connection"]),
        "snapshot_sha256": authority.state["snapshot_sha256"],
        "proposed": proposed,
        "exceptions": authority.state["exceptions"],
        "defaults": "preserve incoming DROP, outgoing ACCEPT, forwarded DROP/REJECT, IPV6=yes",
        "activation": ("activate preserved compatible saved rules if UFW is inactive" if missing else
                       "enable UFW and make all preserved saved rules effective"
                       if ("ufw", "--force", "enable") in proposed else "already active"),
    }


def _bind_refreshed(config: EnvironmentConfig, confirmed: HostResult, refreshed: HostResult) -> HostResult:
    expected = firewall_preview(config, confirmed)
    observed = firewall_preview(config, refreshed)
    if confirmed.state["reason"] == "ufw_missing":
        compatible = (refreshed.outcome == "succeeded"
                      and observed["administrator_connection"] == expected["administrator_connection"])
    else:
        compatible = observed == expected
    if not compatible:
        raise _error(ExitStatus.SAFETY, "firewall authority changed; review a new preview")
    return refreshed


def declare_firewall(config: EnvironmentConfig, *, confirmed: HostResult) -> FirewallPlan:
    """Keep native package installation before one helper-driven guarded action."""
    from pyinfra.operations import apt

    authority = validate_firewall_authority(config, confirmed)
    preview = firewall_preview(config, authority)
    plan = FirewallPlan(("ufw",), preview["proposed"], authority)
    apt.packages(packages=list(plan.packages), name="Install UFW")
    _activate_firewall_with_fresh_ssh(config, authority, name="Converge Taskman firewall", _sudo=True)
    return plan


def _current_authority(state: object, host: object, config: EnvironmentConfig,
                       confirmed: HostResult) -> tuple[object, HostResult]:
    remote = PyinfraRemote(host, config, state=state)
    try:
        result = inspect_firewall(remote, config)
        _record_warnings(state, result.warnings)
        return remote, _bind_refreshed(config, confirmed, result)
    except OpsError as error:
        setattr(state, "_taskman_categorized_error", error)
        raise


@operation(is_idempotent=True)
def _activate_firewall_with_fresh_ssh(config: EnvironmentConfig, confirmed: HostResult):
    from pyinfra.context import host, state

    if state.is_executing:
        _, refreshed = _current_authority(state, host, config, confirmed)
        if not refreshed.state["proposed"]:
            return
    yield FunctionCommand(_run_firewall_convergence, (config, confirmed), {})


def _run_firewall_convergence(state: object, host: object, config: EnvironmentConfig,
                              confirmed: HostResult) -> None:
    remote, authority = _current_authority(state, host, config, confirmed)
    try:
        result = _invoke(remote, _request(config, mode="converge",
                                         digest=authority.state["snapshot_sha256"]))
        _record_warnings(state, result.warnings)
        if result.state["changed"]:
            try:
                verify_fresh_ssh_connection(config)
            except Exception:
                raise _error(ExitStatus.REMOTE_PREFLIGHT, "fresh SSH verification failed",
                             changed=True, warnings=result.warnings) from None
    except OpsError as error:
        setattr(state, "_taskman_categorized_error", error)
        raise


def _record_warnings(state: object, warnings: tuple[str, ...]) -> None:
    setattr(state, "_taskman_warnings",
            merge_change_warnings(getattr(state, "_taskman_warnings", ()), warnings))


def verify_fresh_ssh_connection(config: EnvironmentConfig, *,
                               connector: Callable[[EnvironmentConfig], object] | None = None) -> None:
    """Require a new strict pinned-host-key SSH connection to execute ``true``."""
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("fresh SSH verification requires an environment configuration")
    if connector is None:
        from ..remote import connect
        connector = connect
    connection = connector(config)
    if connection is None:
        raise _error(ExitStatus.REMOTE_PREFLIGHT, "fresh SSH verification failed")
    try:
        run = getattr(connection, "run", None)
        if not callable(run) or not run(("true",)).succeeded:
            raise _error(ExitStatus.REMOTE_PREFLIGHT, "fresh SSH verification failed")
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def _error(status: ExitStatus, reason: str, *, changed: bool = False,
           warnings: tuple[str, ...] = ()) -> OpsError:
    return OpsError(status, "firewall", f"firewall: {reason}", changed,
        next_action="inspect UFW and administrator SSH access; use console recovery and verify the pinned host key if unreachable, then rerun for a new preview",
        warnings=warnings)


__all__ = ["FirewallPlan", "declare_firewall", "inspect_firewall", "firewall_preview",
           "validate_firewall_authority", "verify_fresh_ssh_connection"]
