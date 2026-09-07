"""Controller translation for the final helper verification result."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage
from ..helper_runner import HelperInvocation, invoke_helper
from ..host_protocol import HostRequest
from ..output import WorkflowResult
from ..releases.identifiers import validate_release_id
from ..remote import Remote
from .helper import request as helper_request, result_error, run_request
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
    """Invoke one helper verification and consume only its public report."""

    if expected_release_id is not None:
        try:
            expected_release_id = validate_release_id(expected_release_id)
        except (TypeError, ValueError):
            raise _failure("verification requires a valid expected release") from None
    request = helper_request(
        "verify",
        config,
        expected_state={"expected_release_id": expected_release_id},
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
    result = run_request(remote, request, package=package, invoker=invoker)
    if result.outcome != "succeeded":
        _raise_host_preflight(result)
        raise result_error(result)
    try:
        report = VerificationReport.from_mapping(_mutable_mapping(result.state.get("report")))
    except (TypeError, ValueError):
        raise _failure("verification returned invalid observed state") from None
    if not report.successful:
        raise _failure("verification returned unsuccessful state")
    try:
        actual_release_id = validate_release_id(report.release_id)
    except (TypeError, ValueError):
        raise _failure("verification returned invalid observed state") from None
    if expected_release_id is not None and (
        report.expected_release_id != expected_release_id or actual_release_id != expected_release_id
    ):
        raise _failure("verification returned an unrelated release")
    return WorkflowResult(
        command="verify",
        environment=config.name,
        changed=False,
        stage="verified",
        facts={"verification": report.to_mapping()},
        warnings=tuple(result.warnings),
        next_action=report.next_action,
        exit_status=report.exit_status,
    )


def _raise_host_preflight(result) -> None:
    authority = result.state.get("preflight") if isinstance(result.state, Mapping) else None
    if authority == "preflight":
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "host-preflight",
            "required host fact collection failed",
            changed=False,
            next_action="restore SSH administrator connectivity and required sudo access before retrying",
        )
    if authority == "unsupported":
        raise OpsError(
            ExitStatus.INVALID,
            "host-preflight",
            "host does not meet the supported deployment requirements",
            changed=False,
            next_action="use a supported Ubuntu 26.04 amd64 host and correct the environment configuration",
        )


def _failure(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "verification",
        message,
        changed=False,
        next_action="inspect the observed host state and retry after resolving the reported conflict",
    )


def _mutable_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid verification report")

    def restore(item: object) -> object:
        if isinstance(item, Mapping):
            return {key: restore(value) for key, value in item.items()}
        if isinstance(item, tuple):
            return [restore(value) for value in item]
        return item

    return {key: restore(item) for key, item in value.items()}


__all__ = ["run_verify"]
