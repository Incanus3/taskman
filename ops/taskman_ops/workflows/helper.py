"""The final controller boundary for bounded helper requests and results."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
import re
from typing import Callable

from ..config import EnvironmentConfig
from ..errors import ExitStatus, HelperTransportError, OpsError
from ..helper_package import HelperPackage, temporary_helper_package
from ..helper_runner import invoke_helper, new_correlation_id
from ..host_protocol import HostRequest, HostResult, PROTOCOL_VERSION
from ..host_protocol.envelope import merge_result_warning, validate_result_for_request
from ..host_protocol.identifiers import ProtocolError
from ..manifests import VerifiedArtifact
from ..remote import Remote, UploadReceipt
from .verification_results import VerificationReport


_MIGRATION_FILENAME_RE = re.compile(r"[0-9]{14}_[a-z0-9_]+\.exs\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


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


def request(
    operation: str,
    config: EnvironmentConfig,
    *,
    expected_state: Mapping[str, object] | None = None,
    parameters: Mapping[str, object] | None = None,
) -> HostRequest:
    """Build one final envelope; correlation is never business state."""

    return HostRequest(
        protocol_version=PROTOCOL_VERSION,
        operation=operation,
        correlation_id=new_correlation_id(),
        expected_state={} if expected_state is None else expected_state,
        paths=helper_paths(config),
        parameters={} if parameters is None else parameters,
    )


def run_request(
    remote: Remote,
    request: HostRequest,
    *,
    package: HelperPackage | None = None,
    invoker: Callable[[Remote, HelperPackage, HostRequest], HostResult] = invoke_helper,
) -> HostResult:
    """Invoke once and enforce final protocol/version/operation/correlation."""

    manager = temporary_helper_package() if package is None else nullcontext(package)
    with manager as selected:
        result = invoker(remote, selected, request)
    try:
        return validate_result_for_request(request, result)
    except ProtocolError:
        warnings = (
            ("transient helper cleanup was incomplete",)
            if isinstance(result, HostResult) and result.local_cleanup_incomplete
            else ()
        )
        raise _safety(
            request.operation,
            "host helper result does not match its request",
            warnings=warnings,
        )


def result_error(result: HostResult) -> OpsError:
    """Map a final helper outcome without reconstructing private stages."""

    if not isinstance(result, HostResult) or result.outcome == "succeeded":
        raise ValueError("result_error requires a final unsuccessful result")
    state = result.state
    boundary = state.get("failed_boundary")
    if state.get("locked") is True:
        status = ExitStatus.LOCKED
    elif result.outcome == "refused" or result.operation == "cleanup":
        status = ExitStatus.SAFETY
    elif boundary == "backup":
        status = ExitStatus.BACKUP
    elif result.operation == "backup":
        status = ExitStatus.BACKUP
    elif result.operation == "verify" or boundary == "verification":
        status = ExitStatus.READINESS
    elif boundary == "migration" and _has_observed_migrations(state):
        status = ExitStatus.MIGRATION
    elif result.operation == "restore":
        status = ExitStatus.RESTORE
    elif result.operation in {"deploy", "genesis", "rollback"}:
        status = ExitStatus.RELEASE
    else:
        status = ExitStatus.SAFETY
    error = OpsError(
        status,
        str(state.get("failed_boundary", result.operation)),
        result.message,
        changed=state.get("changed") is True,
        next_action="inspect the observed host state before retrying",
        state=state,
        warnings=result.warnings,
    )
    return error


def successful_verification(value: object, expected_release_id: str) -> dict[str, object]:
    """Validate the complete fresh readiness proof for a mutation success."""

    try:
        report = VerificationReport.from_mapping(mutable(value))
    except (TypeError, ValueError):
        raise ValueError("verification report is invalid") from None
    if (
        not report.successful
        or report.release_id != expected_release_id
        or report.expected_release_id != expected_release_id
    ):
        raise ValueError("verification report does not prove the selected release")
    return report.to_mapping()


def _has_observed_migrations(state: Mapping[str, object]) -> bool:
    value = state.get("applied_migrations")
    if not isinstance(value, (list, tuple)) or not value:
        return False
    if all(type(item) is int and item >= 0 for item in value):
        return list(value) == sorted(set(value))
    return all(
        isinstance(item, Mapping)
        and set(item) == {"filename", "sha256"}
        and type(item["filename"]) is str
        and _MIGRATION_FILENAME_RE.fullmatch(item["filename"]) is not None
        and type(item["sha256"]) is str
        and _SHA256_RE.fullmatch(item["sha256"]) is not None
        for item in value
    )


def mutable(value: object) -> object:
    """Restore frozen protocol JSON values for existing controller validators."""

    if isinstance(value, Mapping):
        return {key: mutable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [mutable(item) for item in value]
    return value


def run_deployment_request(
    remote: Remote,
    config: EnvironmentConfig,
    artifact: VerifiedArtifact,
    *,
    previous_release_id: str | None,
    applied_migrations: tuple[int, ...],
    migration_policy: str,
    genesis: bool = False,
    package: HelperPackage | None = None,
    invoker: Callable[[Remote, HelperPackage, HostRequest], HostResult] = invoke_helper,
) -> HostResult:
    """Upload one verified archive, then invoke the final deployment envelope."""

    if migration_policy not in {"no-change", "backward-compatible", "restore-required"}:
        raise ValueError("helper deployment requires a migration policy")
    correlation_id = new_correlation_id()
    upload_root = config.deployment_root / "uploads"
    upload = upload_root / f".upload-{artifact.manifest.release_id}-{correlation_id}.tar.gz"
    prepared = remote.run(
        ("install", "-d", "-o", "root", "-g", "root", "-m", "700", "--", upload_root.as_posix()),
        sudo=True,
        stdin=None,
        sensitive=True,
    )
    if not prepared.succeeded:
        raise OpsError(ExitStatus.RELEASE, "release", "unable to prepare private release upload", False)
    try:
        receipt = remote.put(artifact.archive, upload, mode=0o600, sensitive=True)
        if not isinstance(receipt, UploadReceipt):
            raise _safety("deploy", "release upload returned an invalid receipt")
        request_value = HostRequest(
            protocol_version=PROTOCOL_VERSION,
            operation="genesis" if genesis else "deploy",
            correlation_id=correlation_id,
            expected_state={
                "selected_release_id": previous_release_id,
                "applied_migrations": applied_migrations,
            },
            paths=helper_paths(config),
            parameters={
                "candidate_release_id": artifact.manifest.release_id,
                "artifact_sha256": artifact.sha256,
                "artifact_path": upload.as_posix(),
                "manifest": artifact.manifest.to_mapping(),
                "migration_policy": migration_policy,
                "credentials_path": "/etc/taskman/pgpass",
                "database": database_settings(config),
                "verification": verification_settings(config),
            },
        )
        result = run_request(remote, request_value, package=package, invoker=invoker)
        return (
            merge_result_warning(result, "transient upload cleanup was incomplete")
            if receipt.cleanup_warning
            else result
        )
    except HelperTransportError as error:
        # The uploaded archive is only removed when the runner proves helper
        # entry never started.  No residue path or recovery command crosses
        # the final result boundary.
        if error.helper_entry_dispatched is False:
            try:
                remote.run(("rm", "-f", "--", upload.as_posix()), sudo=True, sensitive=True)
            except Exception:
                pass
        raise


def _safety(operation: str, message: str, *, warnings: tuple[str, ...] = ()) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        operation,
        message,
        changed=False,
        next_action="inspect helper request and observed host state before retrying",
        warnings=warnings,
    )


__all__ = [
    "database_settings",
    "helper_paths",
    "mutable",
    "request",
    "result_error",
    "run_request",
    "successful_verification",
    "run_deployment_request",
    "verification_settings",
]
