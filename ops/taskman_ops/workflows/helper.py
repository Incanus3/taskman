"""The final controller boundary for bounded helper requests and results."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import re
from pathlib import Path
import tempfile
from typing import Callable

from ..config import EnvironmentConfig
from ..errors import ExitStatus, HelperTransportError, OpsError
from ..helper_client.package import HelperPackage, temporary_helper_package
from ..helper_client.runner import invoke_helper, new_correlation_id
from ..host_protocol import (
    MUTATION_OPERATIONS,
    MUTATION_STATES,
    HostRequest,
    HostResult,
    PROTOCOL_VERSION,
    unavailable_observations,
    validate_cleanup_completion,
    validate_mutation_state,
    validate_verification_report,
)
from ..host_protocol.envelope import merge_result_warning, validate_result_for_request
from ..host_protocol.identifiers import ProtocolError
from ..releases.identifiers import validate_release_id
from ..releases.artifacts import DeploymentTarget
from ..remote import Remote, UploadReceipt
from ..services.backups import scheduled_backup_helper


_MIGRATION_FILENAME_RE = re.compile(r"[0-9]{14}_[a-z0-9_]+\.exs\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SELECTION_ID_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")
_PGPASS = "/etc/taskman/pgpass"
_DISCOVERY_MODES = frozenset({"strict", "deploy", "provision", "restore"})


@contextmanager
def temporary_scheduled_backup_helper_package():
    """Materialize the exact persistent scheduler package for one upload."""

    with tempfile.TemporaryDirectory(prefix="taskman-scheduled-backup-refresh-") as directory:
        yield scheduled_backup_helper(Path(directory) / "taskman-backup.pyz")


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


def discovery_request(
    config: EnvironmentConfig,
    *,
    mode: str = "strict",
    backup_id: str | None = None,
) -> HostRequest:
    """Build credential-safe live host and database discovery authority."""

    if type(mode) is not str or mode not in _DISCOVERY_MODES:
        raise ValueError("discovery mode is invalid")
    if mode == "restore":
        if type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None:
            raise ValueError("restore discovery requires a valid backup identifier")
    elif backup_id is not None:
        raise ValueError("backup identifier is valid only for restore discovery")

    parameters: dict[str, object] = {
        "credentials_path": _PGPASS,
        "database": database_settings(config),
        "mode": mode,
    }
    if backup_id is not None:
        parameters["backup_id"] = backup_id

    return request(
        "discover",
        config,
        parameters=parameters,
    )


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
    invoker: Callable[..., HostResult] = invoke_helper,
    prior_mutation_state: str = "unchanged",
    completed_targets: tuple[Mapping[str, object], ...] = (),
    deadline: float | None = None,
) -> HostResult:
    """Invoke once and enforce final protocol/version/operation/correlation."""

    aggregate_mutation_state(prior_mutation_state)
    manager = temporary_helper_package() if package is None else nullcontext(package)
    try:
        with manager as selected:
            if deadline is None:
                result = invoker(remote, selected, request)
            else:
                result = invoker(remote, selected, request, deadline=deadline)
    except HelperTransportError as error:
        if (
            error.helper_entry_dispatched and _mutating_request(request)
        ) or prior_mutation_state != "unchanged":
            raise _mutation_transport_error(
                request,
                error,
                prior_mutation_state=prior_mutation_state,
                completed_targets=completed_targets,
            ) from None
        raise

    try:
        result = validate_result_for_request(request, result)
    except ProtocolError:
        warnings = (
            ("transient helper cleanup was incomplete",)
            if isinstance(result, HostResult) and result.local_cleanup_incomplete
            else ()
        )
        if _mutating_request(request):
            raise _mutation_protocol_error(
                request,
                warnings=warnings,
                prior_mutation_state=prior_mutation_state,
                completed_targets=completed_targets,
            ) from None
        raise _safety(
            request.operation,
            "host helper result does not match its request",
            warnings=warnings,
        )
    if _mutation_result_required(request, result):
        try:
            validated = validate_mutation_state(result.operation, result.outcome, result.state)
            if request.operation == "cleanup":
                validate_cleanup_completion(request, result.outcome, validated)
        except ProtocolError:
            if _mutating_request(request):
                raise _mutation_protocol_error(
                    request,
                    warnings=result.warnings,
                    prior_mutation_state=prior_mutation_state,
                    completed_targets=completed_targets,
                ) from None
            raise _safety(
                request.operation,
                "host helper returned invalid mutation evidence",
                warnings=result.warnings,
            ) from None
        result = replace(result, state=validated)
    return result


def result_error(
    result: HostResult,
    *,
    starting_state: Mapping[str, object] | None = None,
    prior_mutation_state: str = "unchanged",
    completed_targets: tuple[Mapping[str, object], ...] = (),
) -> OpsError:
    """Map a final helper outcome without reconstructing private stages."""

    if not isinstance(result, HostResult) or result.outcome == "succeeded":
        raise ValueError("result_error requires a final unsuccessful result")
    if result.operation in MUTATION_OPERATIONS:
        state = mutation_result_facts(
            result,
            starting_state=starting_state,
            prior_mutation_state=prior_mutation_state,
            completed_targets=completed_targets,
        )
        status = ExitStatus(int(state["exit_code"]))
        return OpsError(
            status,
            str(state["failed_boundary"]),
            result.message,
            state["mutation_state"] != "unchanged",
            next_action="inspect the observed host state before retrying",
            state=state,
            warnings=result.warnings,
        )

    state = result.state
    boundary = state.get("failed_boundary")
    if state.get("invalid_request") is True:
        status = ExitStatus.INVALID
    elif state.get("locked") is True:
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


def aggregate_mutation_state(*states: str) -> str:
    """Combine command-local mutation evidence without weakening proof."""

    if any(type(state) is not str or state not in MUTATION_STATES for state in states):
        raise ValueError("invalid mutation state")
    if "changed" in states:
        return "changed"
    if "unknown" in states:
        return "unknown"
    return "unchanged"


def mutation_result_facts(
    result: HostResult,
    *,
    starting_state: Mapping[str, object] | None = None,
    prior_mutation_state: str = "unchanged",
    completed_targets: tuple[Mapping[str, object], ...] = (),
) -> dict[str, object]:
    """Map validated invocation evidence into exact command-level public facts."""

    if not isinstance(result, HostResult) or result.operation not in MUTATION_OPERATIONS:
        raise ValueError("mutation facts require a mutation helper result")
    try:
        state = validate_mutation_state(result.operation, result.outcome, result.state)
    except ProtocolError:
        raise ValueError("helper mutation evidence is invalid") from None
    aggregate = aggregate_mutation_state(prior_mutation_state, str(state["mutation_state"]))
    facts: dict[str, object] = {
        "starting_state": None if starting_state is None else mutable(starting_state),
        "mutation_state": aggregate,
        "exit_code": state["exit_code"],
        "failed_boundary": state["failed_boundary"],
        "observations": mutable(state["observations"]),
        "unavailable_fields": list(state["unavailable_fields"]),
        "inspection_error": state["inspection_error"],
        "report": mutable(state["report"]),
    }
    if result.operation in {"deploy", "genesis"}:
        facts.update(
            desired_release_id=state["desired_release_id"],
            backup_id=state["backup_id"],
        )
    elif result.operation == "restore":
        facts.update(
            desired_release_id=state["desired_release_id"],
            backup_id=state["backup_id"],
            pre_restore_backup_id=state["pre_restore_backup_id"],
        )
    else:
        facts["completed_targets"] = _merge_completed_targets(
            completed_targets,
            state["completed_targets"],
        )
    return facts


def successful_verification(value: object, expected_release_id: str) -> dict[str, object]:
    """Validate the complete fresh readiness proof for a mutation success."""

    try:
        report = validate_verification_report(mutable(value))
    except ProtocolError:
        raise ValueError("verification report is invalid") from None
    if (
        report["exit_status"] != ExitStatus.OK
        or report["release_id"] != expected_release_id
        or report["expected_release_id"] != expected_release_id
    ):
        raise ValueError("verification report does not prove the selected release")
    return report


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


def merge_warnings(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Combine warnings in first-occurrence order without repeating messages."""

    return tuple(dict.fromkeys(item for group in groups for item in group))


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
    target: DeploymentTarget,
    *,
    expected_state: Mapping[str, object],
    migration_policy: str,
    backup_helper: Mapping[str, object],
    prune_backup_ids: tuple[str, ...],
    backup_helper_package: HelperPackage | None = None,
    genesis: bool = False,
    package: HelperPackage | None = None,
    invoker: Callable[[Remote, HelperPackage, HostRequest], HostResult] = invoke_helper,
) -> HostResult:
    """Dispatch one exact confirmed deploy/genesis request.

    Consent belongs exclusively to the public controller.  The helper receives
    only the material state that the confirmed plan bound, plus a target and
    the scheduler package facts it must revalidate under its lifecycle lock.
    """

    if (
        not isinstance(target, DeploymentTarget)
        or migration_policy not in {"no-change", "backward-compatible", "restore-required"}
        or not _valid_deployment_expected_state(expected_state)
        or not isinstance(backup_helper, Mapping)
        or set(backup_helper) != {"sha256", "upload_path"}
        or not isinstance(prune_backup_ids, tuple)
        or prune_backup_ids != tuple(sorted(set(prune_backup_ids)))
        or any(type(item) is not str or _BACKUP_ID_RE.fullmatch(item) is None for item in prune_backup_ids)
    ):
        raise ValueError("helper deployment requires a migration policy")
    correlation_id = new_correlation_id()
    upload_root = config.deployment_root / "uploads"
    artifact = target.artifact
    upload: Path | None = None
    scheduler_upload: Path | None = None
    upload_cleanup_warning = False
    helper_value = dict(backup_helper)
    if artifact is not None:
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
        receipt = UploadReceipt()
        if artifact is not None:
            assert upload is not None
            receipt = remote.put(artifact.archive, upload, mode=0o600, sensitive=True)
            if not isinstance(receipt, UploadReceipt):
                raise _safety("deploy", "release upload returned an invalid receipt")
            upload_cleanup_warning = receipt.cleanup_warning
            target_value: dict[str, object] = {
                "kind": "upload",
                "manifest": artifact.manifest.to_mapping(),
                "artifact_sha256": artifact.sha256,
                "artifact_path": upload.as_posix(),
            }
        else:
            assert target.release_record is not None
            target_value = {
                "kind": "installed",
                "release_record": target.release_record.to_mapping(),
            }
        if helper_value["upload_path"] is not None:
            if backup_helper_package is None:
                raise ValueError("scheduler upload requires its verified package")
            if helper_value["sha256"] != backup_helper_package.sha256:
                raise ValueError("scheduler package checksum does not match its payload")
            scheduler_upload = upload_root / f".backup-helper-{correlation_id}.pyz"
            scheduler_receipt = remote.put(
                backup_helper_package.path, scheduler_upload, mode=0o600, sensitive=True
            )
            if not isinstance(scheduler_receipt, UploadReceipt):
                raise _safety("deploy", "scheduler helper upload returned an invalid receipt")
            upload_cleanup_warning = upload_cleanup_warning or scheduler_receipt.cleanup_warning
            helper_value["upload_path"] = scheduler_upload.as_posix()
        request_value = HostRequest(
            protocol_version=PROTOCOL_VERSION,
            operation="genesis" if genesis else "deploy",
            correlation_id=correlation_id,
            expected_state=expected_state,
            paths=helper_paths(config),
            parameters={
                "target": target_value,
                "migration_policy": migration_policy,
                "credentials_path": "/etc/taskman/pgpass",
                "database": database_settings(config),
                "verification": verification_settings(config),
                "backup_helper": helper_value,
                "prune_backup_ids": list(prune_backup_ids),
            },
        )
        result = run_request(remote, request_value, package=package, invoker=invoker)
        upload_cleanup_warning = _remove_finalized_uploads(
            remote, (upload, scheduler_upload)
        ) or upload_cleanup_warning
        return (
            merge_result_warning(result, "transient upload cleanup was incomplete")
            if upload_cleanup_warning
            else result
        )
    except HelperTransportError as error:
        # The uploaded archive is only removed when the runner proves helper
        # entry never started.  No residue path or recovery command crosses
        # the final result boundary.
        if error.helper_entry_dispatched is False:
            _remove_finalized_uploads(remote, (upload, scheduler_upload))
        raise


def run_restore_request(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    request: HostRequest,
    backup_helper_package: HelperPackage | None = None,
    package: HelperPackage | None = None,
    invoker: Callable[[Remote, HelperPackage, HostRequest], HostResult] = invoke_helper,
    prior_mutation_state: str = "unchanged",
) -> HostResult:
    """Upload the confirmed scheduled helper and dispatch one exact restore request."""

    if (
        not isinstance(request, HostRequest)
        or request.operation != "restore"
        or request.paths != helper_paths(config)
        or set(request.parameters)
        != {
            "backup_id",
            "credentials_path",
            "database",
            "verification",
            "backup_helper",
            "prune_backup_ids",
            "replace_unfinished",
            "reapply",
        }
    ):
        raise ValueError("restore helper request is invalid")
    helper = request.parameters["backup_helper"]
    if not isinstance(helper, Mapping) or set(helper) != {"sha256", "upload_path"}:
        raise ValueError("restore backup helper authority is invalid")
    helper_value = dict(helper)
    marker = helper_value["upload_path"]
    if marker not in {None, "pending-controller-upload"}:
        raise ValueError("restore backup helper upload marker is invalid")
    scheduler_upload: Path | None = None
    upload_cleanup_warning = False
    try:
        if marker is not None:
            if backup_helper_package is None or helper_value["sha256"] != backup_helper_package.sha256:
                raise ValueError("scheduler package checksum does not match its payload")
            scheduler_upload = (
                config.deployment_root / "uploads" / f".backup-helper-{request.correlation_id}.pyz"
            )
            receipt = remote.put(
                backup_helper_package.path,
                scheduler_upload,
                mode=0o600,
                sensitive=True,
            )
            if not isinstance(receipt, UploadReceipt):
                raise _safety("restore", "scheduler helper upload returned an invalid receipt")
            upload_cleanup_warning = receipt.cleanup_warning
            helper_value["upload_path"] = scheduler_upload.as_posix()
        dispatched = replace(
            request,
            parameters={**request.parameters, "backup_helper": helper_value},
        )
        result = run_request(
            remote,
            dispatched,
            package=package,
            invoker=invoker,
            prior_mutation_state=prior_mutation_state,
        )
        upload_cleanup_warning = _remove_finalized_uploads(remote, (scheduler_upload,)) or upload_cleanup_warning
        return (
            merge_result_warning(result, "transient upload cleanup was incomplete")
            if upload_cleanup_warning
            else result
        )
    except HelperTransportError as error:
        if error.helper_entry_dispatched is False and scheduler_upload is not None:
            try:
                remote.run(
                    ("rm", "-f", "--", scheduler_upload.as_posix()),
                    sudo=True,
                    sensitive=True,
                )
            except Exception:
                pass
        raise


def _remove_finalized_uploads(remote: Remote, uploads: tuple[Path | None, ...]) -> bool:
    """Remove controller-staged bytes after a final helper result without changing it."""

    incomplete = False
    for upload in uploads:
        if upload is None:
            continue
        try:
            removed = remote.run(("rm", "-f", "--", upload.as_posix()), sudo=True, sensitive=True)
            if not getattr(removed, "succeeded", False):
                incomplete = True
        except Exception:
            incomplete = True
    return incomplete


def _valid_deployment_expected_state(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "selected_release_id",
        "last_successful_selection_id",
        "applied_migrations",
        "backup_protection_sha256",
        "scheduled_backup_sha256",
        "backup_timer_enabled",
        "downgrade_baseline_sha256",
    }:
        return False
    try:
        if value["selected_release_id"] is not None:
            validate_release_id(value["selected_release_id"])
        selection = value["last_successful_selection_id"]
        if selection is not None and (type(selection) is not str or _SELECTION_ID_RE.fullmatch(selection) is None):
            return False
        migrations = value["applied_migrations"]
        if (
            not isinstance(migrations, (list, tuple))
            or any(type(item) is not int or item < 0 for item in migrations)
            or tuple(migrations) != tuple(sorted(set(migrations)))
            or type(value["backup_timer_enabled"]) is not bool
        ):
            return False
        for key in ("backup_protection_sha256", "downgrade_baseline_sha256"):
            if type(value[key]) is not str or _SHA256_RE.fullmatch(value[key]) is None:
                return False
        scheduler = value["scheduled_backup_sha256"]
        return scheduler is None or (type(scheduler) is str and _SHA256_RE.fullmatch(scheduler) is not None)
    except (TypeError, ValueError):
        return False


def _mutation_result_required(request_value: HostRequest, result: HostResult) -> bool:
    if request_value.operation not in MUTATION_OPERATIONS:
        return False
    if request_value.operation != "cleanup":
        return True
    return result.outcome != "succeeded" or request_value.parameters.get("action") == "execute"


def _mutating_request(request_value: HostRequest) -> bool:
    if request_value.operation in {"deploy", "genesis", "restore"}:
        return True
    return (
        request_value.operation == "cleanup"
        and request_value.parameters.get("action") == "execute"
    )


def _mutation_protocol_error(
    request_value: HostRequest,
    *,
    warnings: tuple[str, ...],
    prior_mutation_state: str,
    completed_targets: tuple[Mapping[str, object], ...],
) -> OpsError:
    facts = _unavailable_mutation_facts(
        request_value,
        prior_mutation_state=prior_mutation_state,
        dispatch_state="unknown",
        completed_targets=completed_targets,
    )
    return OpsError(
        ExitStatus.SAFETY,
        "helper",
        "host helper returned invalid mutation evidence",
        changed=facts["mutation_state"] != "unchanged",
        next_action="inspect the observed host state before retrying",
        state=facts,
        warnings=warnings,
    )


def _mutation_transport_error(
    request_value: HostRequest,
    error: HelperTransportError,
    *,
    prior_mutation_state: str,
    completed_targets: tuple[Mapping[str, object], ...],
) -> HelperTransportError:
    facts = _unavailable_mutation_facts(
        request_value,
        prior_mutation_state=prior_mutation_state,
        dispatch_state="unknown" if error.helper_entry_dispatched and _mutating_request(request_value) else "unchanged",
        completed_targets=completed_targets,
    )
    mapped = OpsError(
        error.status,
        error.stage,
        error.message,
        changed=facts["mutation_state"] != "unchanged",
        next_action=error.next_action,
        state=facts,
        warnings=error.warnings,
    )
    return HelperTransportError(
        mapped,
        helper_entry_dispatched=error.helper_entry_dispatched,
    )


def _unavailable_mutation_facts(
    request_value: HostRequest,
    *,
    prior_mutation_state: str,
    dispatch_state: str,
    completed_targets: tuple[Mapping[str, object], ...],
) -> dict[str, object]:
    observations, unavailable = unavailable_observations(request_value.operation)
    facts: dict[str, object] = {
        "starting_state": mutable(request_value.expected_state),
        "mutation_state": aggregate_mutation_state(prior_mutation_state, dispatch_state),
        "observations": observations,
        "unavailable_fields": unavailable,
        "failed_boundary": "helper",
        "inspection_error": None,
        "report": None,
    }
    if request_value.operation in {"deploy", "genesis"}:
        facts.update(desired_release_id=_request_target_release(request_value), backup_id=None)
    elif request_value.operation == "restore":
        facts.update(
            desired_release_id=None,
            backup_id=_request_backup_id(request_value),
            pre_restore_backup_id=None,
        )
    elif request_value.operation == "cleanup":
        facts["completed_targets"] = _merge_completed_targets(completed_targets, ())
    return facts


def _request_target_release(request_value: HostRequest) -> str | None:
    target = request_value.parameters.get("target")
    if not isinstance(target, Mapping):
        return None
    target_kind = target.get("kind")
    if target_kind == "upload":
        manifest = target.get("manifest")
        release_id = manifest.get("release_id") if isinstance(manifest, Mapping) else None
    elif target_kind == "installed":
        record = target.get("release_record")
        release_id = record.get("release_id") if isinstance(record, Mapping) else None
    else:
        return None
    if type(release_id) is not str:
        return None
    try:
        return validate_release_id(release_id)
    except ValueError:
        return None


def _request_backup_id(request_value: HostRequest) -> str | None:
    backup_id = request_value.parameters.get("backup_id")
    return (
        backup_id
        if type(backup_id) is str and _BACKUP_ID_RE.fullmatch(backup_id) is not None
        else None
    )


def _merge_completed_targets(
    earlier: object,
    current: object,
) -> list[dict[str, object]]:
    if not isinstance(earlier, (list, tuple)) or not isinstance(current, (list, tuple)):
        raise ValueError("completed cleanup targets must be collections")
    combined: dict[tuple[str, str, str], dict[str, object]] = {}
    for item in (*earlier, *current):
        if not isinstance(item, Mapping) or set(item) != {"kind", "identifier", "path"}:
            raise ValueError("completed cleanup target is invalid")
        identity = tuple(item[key] for key in ("kind", "identifier", "path"))
        if not all(type(value) is str for value in identity):
            raise ValueError("completed cleanup target is invalid")
        combined[identity] = dict(item)
    return [combined[identity] for identity in sorted(combined)]


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
    "aggregate_mutation_state",
    "database_settings",
    "discovery_request",
    "helper_paths",
    "merge_warnings",
    "mutable",
    "mutation_result_facts",
    "request",
    "result_error",
    "run_request",
    "successful_verification",
    "run_deployment_request",
    "verification_settings",
]
