"""The final controller boundary for bounded helper requests and results."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import replace
import re
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
    validate_mutation_state,
)
from ..host_protocol.envelope import merge_result_warning, validate_result_for_request
from ..host_protocol.identifiers import ProtocolError
from ..releases.identifiers import validate_release_id
from ..releases.manifests import VerifiedArtifact
from ..remote import Remote, UploadReceipt
from .verification_results import VerificationReport


_MIGRATION_FILENAME_RE = re.compile(r"[0-9]{14}_[a-z0-9_]+\.exs\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_PGPASS = "/etc/taskman/pgpass"
_DISCOVERY_MODES = frozenset({"strict", "deploy", "provision", "restore"})


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
                _validate_cleanup_completion(request, result.outcome, validated)
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
    release_id = request_value.parameters.get("candidate_release_id")
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


def _validate_cleanup_completion(
    request_value: HostRequest,
    outcome: str,
    state: Mapping[str, object],
) -> None:
    raw_targets = request_value.parameters.get("targets")
    if not isinstance(raw_targets, (list, tuple)):
        raise ProtocolError("cleanup request targets are invalid")
    requested = {
        tuple(item.get(key) for key in ("kind", "identifier", "path"))
        for item in raw_targets
        if isinstance(item, Mapping)
    }
    if len(requested) != len(raw_targets):
        raise ProtocolError("cleanup request targets are invalid")
    completed = {
        tuple(item[key] for key in ("kind", "identifier", "path"))
        for item in state["completed_targets"]  # type: ignore[union-attr]
    }
    action = request_value.parameters.get("action")
    if action == "inspect" and (
        completed or state["mutation_state"] != "unchanged"
    ):
        raise ProtocolError("cleanup inspection cannot claim mutation completion")
    if not completed <= requested:
        raise ProtocolError("cleanup completion exceeds confirmed targets")
    if outcome == "succeeded" and completed != requested:
        raise ProtocolError("cleanup success does not account for its batch")


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
