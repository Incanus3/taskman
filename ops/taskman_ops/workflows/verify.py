"""Controller translation for the final helper verification result."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_client.package import HelperPackage
from ..helper_client.runner import invoke_helper
from ..host_protocol import HostRequest, HostResult
from ..output import WorkflowResult
from ..releases.identifiers import validate_release_id
from ..remote import Remote
from .helper import mutable, request as helper_request, result_error, run_request
from .verification_results import VerificationReport


HelperInvoker = Callable[[Remote, HelperPackage, HostRequest], HostResult]


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
        # A helper can complete all verification checks and still report a
        # failed release/readiness proof.  Preserve that typed report rather
        # than reducing it to a generic transport error.  Lock/refusal and
        # reportless outcomes remain ordinary helper failures.
        if result.operation != "verify" or result.outcome == "refused" or result.state.get("locked") is True:
            raise result_error(result)
        try:
            report = VerificationReport.from_mapping(mutable(result.state.get("report")))
        except (TypeError, ValueError):
            raise result_error(result)
        if report.successful:
            raise result_error(result)
        _validate_report_release(report, expected_release_id)
        return WorkflowResult(
            command="verify",
            environment=config.name,
            changed=False,
            stage="verification-failed",
            facts={"verification": report.to_mapping()},
            warnings=tuple(result.warnings),
            next_action=report.next_action,
            exit_status=report.exit_status,
        )
    try:
        report = VerificationReport.from_mapping(mutable(result.state.get("report")))
    except (TypeError, ValueError):
        raise _failure("verification returned invalid observed state") from None
    if not report.successful:
        raise _failure("verification returned unsuccessful state")
    _validate_report_release(report, expected_release_id)
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


def _validate_report_release(report: VerificationReport, expected_release_id: str | None) -> None:
    try:
        actual_release_id = validate_release_id(report.release_id)
    except (TypeError, ValueError):
        raise _failure("verification returned invalid observed state") from None
    if expected_release_id is not None and (
        report.expected_release_id != expected_release_id or actual_release_id != expected_release_id
    ):
        raise _failure("verification returned an unrelated release")


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


__all__ = ["run_verify"]
