"""Shared controller transport for explicit helper-owned recovery policies."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage, temporary_helper_package
from ..helper_runner import HelperInvocation, invoke_helper, new_operation_id
from ..host_protocol import HostRequest, HostResult
from ..remote import Remote


SUCCESSFUL_VERIFICATION_CHECKS = (
    {"schema_version": 1, "name": "taskman-service", "status": "passed", "summary": "taskman.service is active with a positive MainPID"},
    {"schema_version": 1, "name": "release-identity", "status": "passed", "summary": "systemd MainPID executable is under the selected release"},
    {"schema_version": 1, "name": "caddy-service", "status": "passed", "summary": "caddy.service is active"},
    {"schema_version": 1, "name": "listener-topology", "status": "passed", "summary": "Taskman, distribution, and PostgreSQL listeners have the required topology"},
    {"schema_version": 1, "name": "startup-journal", "status": "passed", "summary": "recent startup journal evidence is clean"},
    {"schema_version": 1, "name": "local-readiness", "status": "passed", "summary": "loopback health endpoint returned exact ready response"},
    {"schema_version": 1, "name": "public-readiness", "status": "passed", "summary": "public HTTPS health endpoint returned exact ready response"},
    {"schema_version": 1, "name": "public-hsts", "status": "passed", "summary": "public HTTPS response includes HSTS"},
)


def helper_paths(config: EnvironmentConfig) -> dict[str, str]:
    return {
        "install_root": config.install_root.as_posix(),
        "backup_root": config.backup_root.as_posix(),
    }


def database_settings(config: EnvironmentConfig) -> dict[str, object]:
    return {
        "host": config.database_host,
        "port": config.database_port,
        "role": config.database_role,
        "name": config.database_name,
    }


def verification_settings(config: EnvironmentConfig) -> dict[str, object]:
    return {
        "application_port": config.application_port,
        "distribution_port": config.distribution_port,
        "database_port": config.database_port,
        "public_hostname": config.public_hostname,
        "public_ipv4": config.public_ipv4,
        "public_ipv6": config.public_ipv6,
        "ssh_port": config.ssh_port,
        "ssh_user": config.ssh_user,
        "readiness_timeout": config.readiness_timeout,
        "connection_timeout": config.connection_timeout,
    }


def run_request(
    remote: Remote,
    request: HostRequest,
    *,
    package: HelperPackage | None = None,
) -> HostResult:
    """Invoke one helper request and enforce exact envelope correlation."""

    manager = (
        temporary_helper_package()
        if package is None
        else nullcontext(package)
    )
    with manager as selected:
        invocation = invoke_helper(remote, selected, request)
    if not isinstance(invocation, HelperInvocation):
        raise _safety(request.operation, "host helper returned invalid invocation evidence")
    result = invocation.result
    if (
        result.protocol_version,
        result.operation,
        result.operation_id,
    ) != (
        request.protocol_version,
        request.operation,
        request.operation_id,
    ):
        raise _safety(request.operation, "host helper result does not match its request")
    if invocation.cleanup_warning is not None and invocation.cleanup_warning not in result.warnings:
        # The runner's bounded cleanup warning is transport evidence, not host
        # transaction policy. Preserve it without accepting duplicate values.
        result = HostResult(
            result.protocol_version,
            result.operation,
            result.operation_id,
            result.outcome,
            result.stage,
            result.changed_stages,
            result.lifecycle,
            result.runtime_state,
            result.verification,
            result.residue_paths,
            result.recovery_actions,
            (*result.warnings, invocation.cleanup_warning),
        )
    return result


def discover_lifecycle(
    remote: Remote,
    config: EnvironmentConfig,
) -> tuple[Mapping[str, object], tuple[str, ...]]:
    request = HostRequest(
        1,
        "discover",
        new_operation_id(),
        {},
        helper_paths(config),
        {},
    )
    result = run_request(remote, request)
    if result.stage == "lifecycle-lock":
        raise _error(
            ExitStatus.LOCKED,
            "lifecycle-lock",
            "lifecycle discovery lock is held",
            False,
            result,
        )
    if (
        result.outcome != "succeeded"
        or result.stage != "discovered"
        or not isinstance(result.lifecycle, Mapping)
    ):
        raise _safety("discovery", "host helper refused lifecycle authority")
    return result.lifecycle, result.warnings


def result_error(
    result: HostResult,
    *,
    default_status: ExitStatus,
) -> OpsError:
    if result.stage == "lifecycle-lock":
        status = ExitStatus.LOCKED
    elif result.outcome == "refused":
        status = ExitStatus.SAFETY
    else:
        status = default_status
    return _error(
        status,
        result.stage,
        f"{result.operation} helper did not complete",
        bool(result.changed_stages),
        result,
    )


def mutable(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: mutable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [mutable(item) for item in value]
    return value


def successful_verification(
    value: object,
    *,
    release_id: object,
) -> bool:
    required = {
        "schema_version", "status", "exit_status", "release_id",
        "expected_release_id", "checks", "next_action",
    }
    return bool(
        isinstance(value, Mapping)
        and set(value) == required
        and value["schema_version"] == 1
        and value["status"] == "ok"
        and value["exit_status"] == 0
        and value["release_id"] == release_id
        and value["expected_release_id"] == release_id
        and isinstance(value["checks"], tuple)
        and tuple(dict(item) for item in value["checks"] if isinstance(item, Mapping))
        == SUCCESSFUL_VERIFICATION_CHECKS
        and len(value["checks"]) == len(SUCCESSFUL_VERIFICATION_CHECKS)
        and value["next_action"] is None
    )


def _error(
    status: ExitStatus,
    stage: str,
    message: str,
    changed: bool,
    result: HostResult,
) -> OpsError:
    error = OpsError(
        status,
        stage,
        message,
        changed,
        result.recovery_actions[0]
        if result.recovery_actions
        else "inspect the bounded helper evidence before retrying",
    )
    error.lifecycle = dict(result.lifecycle)  # type: ignore[attr-defined]
    error.runtime_state = dict(result.runtime_state)  # type: ignore[attr-defined]
    error.changed_stages = tuple(result.changed_stages)  # type: ignore[attr-defined]
    error.residue_paths = tuple(result.residue_paths)  # type: ignore[attr-defined]
    error.recovery_commands = tuple(result.recovery_actions)  # type: ignore[attr-defined]
    error.warnings = tuple(result.warnings)  # type: ignore[attr-defined]
    error.verification = dict(result.verification)  # type: ignore[attr-defined]
    return error


def _safety(operation: str, message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        operation,
        message,
        False,
        "inspect helper request and lifecycle evidence before retrying",
    )


__all__ = [
    "database_settings",
    "discover_lifecycle",
    "helper_paths",
    "mutable",
    "result_error",
    "run_request",
    "successful_verification",
    "verification_settings",
]
