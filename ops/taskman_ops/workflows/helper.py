"""The final controller boundary for bounded helper requests and results."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from typing import Callable

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage, temporary_helper_package
from ..helper_runner import HelperInvocation, invoke_helper, new_correlation_id
from ..host_protocol import HostRequest, HostResult, PROTOCOL_VERSION
from ..manifests import MigrationFingerprint, VerifiedArtifact
from ..remote import Remote, UploadReceipt


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
    invoker: Callable[[Remote, HelperPackage, HostRequest], HelperInvocation] = invoke_helper,
) -> HostResult:
    """Invoke once and enforce final protocol/version/operation/correlation."""

    manager = temporary_helper_package() if package is None else nullcontext(package)
    with manager as selected:
        invocation = invoker(remote, selected, request)
    if not isinstance(invocation, HelperInvocation):
        raise _safety(request.operation, "host helper returned invalid invocation")
    result = invocation.result
    if (
        not isinstance(result, HostResult)
        or result.protocol_version != request.protocol_version
        or result.operation != request.operation
        or result.correlation_id != request.correlation_id
    ):
        raise _safety(request.operation, "host helper result does not match its request")
    if invocation.cleanup_warning is None:
        return result
    if invocation.cleanup_warning in result.warnings:
        return result
    return HostResult(
        protocol_version=result.protocol_version,
        operation=result.operation,
        correlation_id=result.correlation_id,
        outcome=result.outcome,
        message=result.message,
        state=result.state,
        warnings=(*result.warnings, invocation.cleanup_warning),
    )


def result_error(result: HostResult) -> OpsError:
    """Map a final helper outcome without reconstructing private stages."""

    if not isinstance(result, HostResult) or result.outcome == "succeeded":
        raise ValueError("result_error requires a final unsuccessful result")
    state = result.state
    if state.get("locked") is True:
        status = ExitStatus.LOCKED
    elif result.outcome == "refused" or result.operation == "cleanup":
        status = ExitStatus.SAFETY
    elif result.operation == "backup":
        status = ExitStatus.BACKUP
    elif result.operation == "restore":
        status = ExitStatus.RESTORE
    elif result.operation == "verify" or state.get("failed_boundary") == "verification":
        status = ExitStatus.READINESS
    elif state.get("failed_boundary") == "migration":
        status = ExitStatus.MIGRATION
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
    )
    error.state = dict(state)  # type: ignore[attr-defined]
    error.warnings = tuple(result.warnings)  # type: ignore[attr-defined]
    return error


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
    current_migrations: tuple[MigrationFingerprint, ...],
    migration_policy: str,
    genesis: bool = False,
    manual_adoption: Mapping[str, object] | None = None,
    package: HelperPackage | None = None,
    invoker: Callable[[Remote, HelperPackage, HostRequest], HelperInvocation] = invoke_helper,
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
                "previous_release_id": previous_release_id,
                "current_migrations": [item.to_mapping() for item in current_migrations],
            },
            paths=helper_paths(config),
            parameters={
                "candidate_release_id": artifact.manifest.release_id,
                "artifact_sha256": artifact.sha256,
                "artifact_path": upload.as_posix(),
                "manifest": artifact.manifest.to_mapping(),
                "migration_policy": migration_policy,
                "verification": {
                    **verification_settings(config),
                    "database_host": config.database_host,
                    "database_role": config.database_role,
                    "database_name": config.database_name,
                },
                "manual_adoption": dict(manual_adoption) if manual_adoption is not None else None,
            },
        )
        result = run_request(remote, request_value, package=package, invoker=invoker)
        if not receipt.cleanup_warning or "transient upload cleanup was incomplete" in result.warnings:
            return result
        return HostResult(
            protocol_version=result.protocol_version,
            operation=result.operation,
            correlation_id=result.correlation_id,
            outcome=result.outcome,
            message=result.message,
            state=result.state,
            warnings=(*result.warnings, "transient upload cleanup was incomplete"),
        )
    except Exception as error:
        # The uploaded archive is only removed when the runner proves helper
        # entry never started.  No residue path or recovery command crosses
        # the final result boundary.
        if getattr(error, "helper_entry_dispatched", None) is False:
            try:
                remote.run(("rm", "-f", "--", upload.as_posix()), sudo=True, sensitive=True)
            except Exception:
                pass
        raise


def _safety(operation: str, message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        operation,
        message,
        changed=False,
        next_action="inspect helper request and observed host state before retrying",
    )


__all__ = [
    "database_settings",
    "helper_paths",
    "mutable",
    "request",
    "result_error",
    "run_request",
    "run_deployment_request",
    "verification_settings",
]
