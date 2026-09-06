"""Invoke the single helper-owned deployment transaction."""
from __future__ import annotations
from collections.abc import Callable, Mapping
import re
from ..config import EnvironmentConfig
from ..errors import OpsError
from ..helper_package import HelperPackage, temporary_helper_package
from ..helper_runner import HelperInvocation, invoke_helper, new_operation_id
from ..host_protocol import HostRequest
from ..manifests import MigrationFingerprint, VerifiedArtifact
from ..remote import Remote
from ..releases.identifiers import validate_release_id
from .helper_deploy_results import deployment_error, translate_helper_deployment_result

HelperInvoker = Callable[[Remote, HelperPackage, HostRequest], HelperInvocation]
_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required"})

def run_helper_deployment(
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
    invoker: HelperInvoker = invoke_helper,
) -> dict[str, object]:
    """Transfer a verified archive and make exactly one mutation request."""

    if not isinstance(config, EnvironmentConfig) or not isinstance(artifact, VerifiedArtifact):
        raise TypeError("helper deployment needs validated configuration and artifact")
    if migration_policy not in _POLICIES:
        raise ValueError("helper deployment requires a migration policy")
    if not isinstance(genesis, bool) or (genesis and previous_release_id is not None):
        raise ValueError("genesis deployment cannot declare a predecessor")
    if previous_release_id is not None:
        validate_release_id(previous_release_id)
    if (
        not isinstance(current_migrations, tuple)
        or not all(isinstance(item, MigrationFingerprint) for item in current_migrations)
    ):
        raise TypeError("helper deployment requires confirmed migration fingerprints")
    if tuple(item.filename for item in current_migrations) != tuple(
        sorted(item.filename for item in current_migrations)
    ) or len({item.filename for item in current_migrations}) != len(current_migrations):
        raise ValueError("helper deployment migration fingerprints are invalid")
    if manual_adoption is not None and (not isinstance(manual_adoption, Mapping) or genesis):
        raise ValueError("helper deployment manual-adoption evidence is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", artifact.sha256) is None:
        raise ValueError("helper deployment artifact checksum is invalid")

    operation_id = new_operation_id()
    upload_root = config.deployment_root / "uploads"
    upload = upload_root / f".upload-{artifact.manifest.release_id}-{operation_id}.tar.gz"
    prepared = remote.run(
        ("install", "-d", "-o", "root", "-g", "root", "-m", "700", "--", upload_root.as_posix()),
        sudo=True,
        stdin=None,
        sensitive=True,
    )
    if not prepared.succeeded:
        raise deployment_error("staging", "unable to prepare private release upload", changed=False)
    try:
        remote.put(artifact.archive, upload, mode=0o600, sensitive=True)
        request = HostRequest(
            protocol_version=1,
            operation="genesis" if genesis else "deploy",
            operation_id=operation_id,
            expected_state={
                "previous_release_id": previous_release_id,
                "current_migrations": [item.to_mapping() for item in current_migrations],
            },
            paths={
                "install_root": config.install_root.as_posix(),
                "backup_root": config.backup_root.as_posix(),
            },
            parameters={
                "candidate_release_id": artifact.manifest.release_id,
                "artifact_sha256": artifact.sha256,
                "artifact_path": upload.as_posix(),
                "manifest": artifact.manifest.to_mapping(),
                "migration_policy": migration_policy,
                "verification": _settings(config),
                "manual_adoption": dict(manual_adoption) if manual_adoption is not None else None,
            },
        )
        if package is None:
            with temporary_helper_package() as generated:
                invocation = invoker(remote, generated, request)
        else:
            invocation = invoker(remote, package, request)
    except Exception as error:
        # Only the runner's explicit pre-dispatch fact proves that Python
        # could not have read the request. SSH/sudo/nonzero/protocol failures
        # after dispatch are ambiguous and must retain this exact upload.
        if getattr(error, "helper_entry_dispatched", None) is False:
            _cleanup_uploaded_artifact(remote, upload, error)
        else:
            _retain_uncertain_uploaded_artifact(upload, error)
        raise
    return translate_helper_deployment_result(invocation, request)


def _settings(config: EnvironmentConfig) -> dict[str, object]:
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
        "database_host": config.database_host,
        "database_role": config.database_role,
        "database_name": config.database_name,
    }


def _cleanup_uploaded_artifact(remote: Remote, upload: object, error: Exception) -> None:
    """Remove only the exact controller upload without replacing its failure."""

    path = upload.as_posix()
    try:
        completed = remote.run(
            ("rm", "-f", "--", path),
            sudo=True,
            stdin=None,
            sensitive=True,
        )
        cleaned = completed.succeeded
    except Exception:
        cleaned = False
    if cleaned or not isinstance(error, OpsError):
        return
    previous_residue = tuple(getattr(error, "residue_paths", ()))
    previous_recovery = tuple(getattr(error, "recovery_commands", ()))
    previous_warnings = tuple(getattr(error, "warnings", ()))
    error.residue_paths = tuple(dict.fromkeys((*previous_residue, path)))
    error.recovery_commands = tuple(
        dict.fromkeys((*previous_recovery, "remove the uploaded deployment artifact before retrying"))
    )
    error.warnings = tuple(
        dict.fromkeys((*previous_warnings, "unable to remove uploaded deployment artifact"))
    )


def _retain_uncertain_uploaded_artifact(upload: object, error: Exception) -> None:
    """Record the controller upload when a dispatched helper may own it."""

    if not isinstance(error, OpsError):
        return
    path = upload.as_posix()
    error.residue_paths = tuple(dict.fromkeys((*tuple(getattr(error, "residue_paths", ())), path)))
    error.recovery_commands = tuple(
        dict.fromkeys(
            (*tuple(getattr(error, "recovery_commands", ())), "inspect the uploaded deployment artifact before retrying")
        )
    )
    error.warnings = tuple(
        dict.fromkeys(
            (*tuple(getattr(error, "warnings", ())), "helper entrypoint outcome is uncertain; uploaded deployment artifact was retained")
        )
    )



__all__ = ["run_helper_deployment"]
