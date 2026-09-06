"""Controller translation for helper-owned read-only verification."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage, temporary_helper_package
from ..helper_runner import HelperInvocation, invoke_helper, new_operation_id
from ..host_protocol import HostRequest
from ..output import WorkflowResult
from ..releases.identifiers import validate_release_id
from ..remote import Remote
from .helper_results import lifecycle_lock_error
from .verification_results import VerificationReport


HelperInvoker = Callable[[Remote, HelperPackage, HostRequest], HelperInvocation]


def run_verify(
    remote: Remote,
    config: EnvironmentConfig,
    expected_release_id: str | None = None,
    *,
    package: HelperPackage | None = None,
    invoker: HelperInvoker = invoke_helper,
) -> WorkflowResult:
    """Invoke one helper verification and translate its exact report mapping."""

    if expected_release_id is not None:
        try:
            expected_release_id = validate_release_id(expected_release_id)
        except (TypeError, ValueError):
            raise _failure("verification requires a valid expected release") from None
    expected_state = {"expected_release_id": expected_release_id}
    request = HostRequest(
        protocol_version=1,
        operation="verify",
        operation_id=new_operation_id(),
        expected_state=expected_state,
        paths={"install_root": config.install_root.as_posix(), "backup_root": config.backup_root.as_posix()},
        parameters={
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
        },
    )
    if package is None:
        with temporary_helper_package() as temporary:
            invocation = invoker(remote, temporary, request)
    else:
        invocation = invoker(remote, package, request)
    result = invocation.result
    if result.stage == "host-preflight":
        _raise_host_preflight(result)
    if result.stage == "release-selection":
        _raise_release_selection(result)
    if result.stage == "lifecycle-lock":
        _raise_lock_contention(result)
    if result.outcome == "refused":
        raise _failure("verification was refused")
    if result.outcome not in {"succeeded", "failed"} or result.stage not in {"verified", "verification"}:
        raise _failure("verification returned invalid evidence")
    try:
        report = VerificationReport.from_mapping(_mutable_mapping(result.verification))
    except (TypeError, ValueError):
        raise _failure("verification returned invalid evidence") from None
    if (result.outcome, result.stage) != (("succeeded", "verified") if report.successful else ("failed", "verification")):
        raise _failure("verification result conflicts with its report")
    try:
        actual_release_id = validate_release_id(report.release_id)
    except (TypeError, ValueError):
        raise _failure("verification returned invalid evidence") from None
    expected_matches = (
        report.expected_release_id is None
        if expected_release_id is None
        else report.expected_release_id == expected_release_id and actual_release_id == expected_release_id
    )
    if (
        result.changed_stages
        or result.lifecycle
        or result.runtime_state
        or result.residue_paths
        or result.warnings
        or result.recovery_actions
        or not expected_matches
    ):
        raise _failure("verification returned invalid evidence")
    warnings: tuple[str, ...] = ()
    if invocation.cleanup_warning is not None:
        if type(invocation.cleanup_warning) is not str or not invocation.cleanup_warning:
            raise _failure("verification returned invalid cleanup evidence")
        warnings = (invocation.cleanup_warning,)
    return WorkflowResult(
        command="verify",
        environment=config.name,
        changed=False,
        stage="verified" if report.successful else "verification-failed",
        facts={"verification": report.to_mapping()},
        warnings=warnings,
        next_action=report.next_action,
        exit_status=report.exit_status,
    )


def _failure(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "verification",
        message,
        changed=False,
        next_action="inspect the managed lifecycle state and retry after resolving the reported conflict",
    )


def _raise_host_preflight(result) -> None:
    authority = result.runtime_state.get("host_authority") if isinstance(result.runtime_state, Mapping) else None
    if (
        result.outcome != "failed"
        or result.changed_stages
        or result.lifecycle
        or result.verification
        or result.residue_paths
        or result.warnings
        or set(result.runtime_state) != {"host_authority"}
        or type(authority) is not str
        or authority not in {"preflight", "unsupported"}
        or result.recovery_actions
        != (
            "restore SSH administrator connectivity and required sudo access before retrying"
            if authority == "preflight"
            else "use a supported Ubuntu 26.04 amd64 host and correct the environment configuration",
        )
    ):
        raise _failure("verification returned invalid host preflight evidence")
    if authority == "preflight":
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "host-preflight",
            "required host fact collection failed",
            changed=False,
            next_action=result.recovery_actions[0],
        )
    raise OpsError(
        ExitStatus.INVALID,
        "host-preflight",
        "host does not meet the supported deployment requirements",
        changed=False,
        next_action=result.recovery_actions[0],
    )


def _raise_release_selection(result) -> None:
    if (
        result.outcome != "refused"
        or result.changed_stages
        or result.lifecycle
        or result.runtime_state
        or result.verification
        or result.residue_paths
        or result.warnings
        or result.recovery_actions
        != ("inspect the managed lifecycle metadata and resolve the contradiction before retrying",)
    ):
        raise _failure("verification returned invalid release-selection evidence")
    raise OpsError(
        ExitStatus.SAFETY,
        "verification",
        "managed release selection was refused",
        changed=False,
        next_action=result.recovery_actions[0],
    )


def _raise_lock_contention(result) -> None:
    try:
        error = lifecycle_lock_error(result)
    except ValueError:
        raise _failure("verification returned invalid lifecycle-lock evidence")
    raise error


def _mutable_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Restore JSON lists frozen by the shared protocol before report parsing."""

    def restore(item: object) -> object:
        if isinstance(item, Mapping):
            return {key: restore(value) for key, value in item.items()}
        if isinstance(item, tuple):
            return [restore(value) for value in item]
        return item

    return {key: restore(item) for key, item in value.items()}


__all__ = ["run_verify"]
