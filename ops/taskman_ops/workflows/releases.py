"""Controller translation for helper-owned read-only release discovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import re

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage, temporary_helper_package
from ..helper_runner import HelperInvocation, invoke_helper, new_operation_id
from ..host_protocol import HostRequest
from ..releases.identifiers import RELEASE_ID_RE, validate_application_version, validate_release_id
from ..remote import Remote
from . import DiscoveryResult
from .helper_results import lifecycle_lock_error, lifecycle_records_refusal


HelperInvoker = Callable[[Remote, HelperPackage, HostRequest], HelperInvocation]


def list_releases(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    package: HelperPackage | None = None,
    invoker: HelperInvoker = invoke_helper,
) -> DiscoveryResult:
    """Ask the helper for release rows and translate its validated mapping."""

    request = _request("list_releases", config)
    invocation = _invoke(remote, request, package, invoker)
    result = invocation.result
    if result.stage == "lifecycle-lock":
        try:
            raise lifecycle_lock_error(result)
        except ValueError:
            raise _invalid_result() from None
    if result.outcome == "refused":
        if not lifecycle_records_refusal(result):
            raise _invalid_result()
        raise _refused()
    if result.outcome != "succeeded" or result.stage != "discovered":
        raise _invalid_result()
    if result.changed_stages or result.runtime_state or result.verification or result.residue_paths or result.recovery_actions:
        raise _invalid_result()
    return _discovery(result.lifecycle, result.warnings, invocation.cleanup_warning)


def _request(operation: str, config: EnvironmentConfig) -> HostRequest:
    return HostRequest(
        protocol_version=1,
        operation=operation,
        operation_id=new_operation_id(),
        expected_state={},
        paths={"install_root": config.install_root.as_posix(), "backup_root": config.backup_root.as_posix()},
        parameters={},
    )


def _invoke(remote: Remote, request: HostRequest, package: HelperPackage | None, invoker: HelperInvoker) -> HelperInvocation:
    if package is not None:
        return invoker(remote, package, request)
    with temporary_helper_package() as temporary:
        return invoker(remote, temporary, request)


_ROW_FIELDS = frozenset({"release_id", "status", "application_version", "source_revision", "target_os", "architecture", "otp_version", "hex_version", "rebar3_version", "artifact_sha256", "artifact_sha256_prefix", "installed_at", "activated_at", "incoming_migration_policy", "rollback_eligible", "rollback_reason"})
_REVISION_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_POLICIES = frozenset({"no-change", "backward-compatible", "restore-required", "adopted"})


def _discovery(lifecycle: Mapping[str, object], warnings: Sequence[str], cleanup_warning: str | None = None) -> DiscoveryResult:
    if not isinstance(lifecycle, Mapping) or set(lifecycle) != {"state", "records", "warnings"}:
        raise _invalid_result()
    if type(lifecycle["state"]) is not str or lifecycle["state"] not in {"empty", "manual", "managed", "staged"}:
        raise _invalid_result()
    records = lifecycle["records"]
    lifecycle_warnings = lifecycle["warnings"]
    if not isinstance(records, (list, tuple)) or not isinstance(lifecycle_warnings, (list, tuple)) or not all(type(warning) is str for warning in lifecycle_warnings):
        raise _invalid_result()
    if tuple(lifecycle_warnings) != tuple(warnings):
        raise _invalid_result()
    parsed = tuple(_release_row(row) for row in records)
    if len({row["release_id"] for row in parsed}) != len(parsed):
        raise _invalid_result()
    _validate_release_rows(lifecycle["state"], parsed)
    return DiscoveryResult(parsed, _with_cleanup_warning(tuple(warnings), cleanup_warning))


def _release_row(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _ROW_FIELDS:
        raise _invalid_result()
    try:
        release_id = validate_release_id(value["release_id"])
        application_version = validate_application_version(value["application_version"])
    except (TypeError, ValueError):
        raise _invalid_result() from None
    status = value["status"]
    source = value["source_revision"]
    checksum = value["artifact_sha256"]
    prefix = value["artifact_sha256_prefix"]
    activated = value["activated_at"]
    policy = value["incoming_migration_policy"]
    rollback = value["rollback_eligible"]
    reason = value["rollback_reason"]
    if (
        type(status) is not str
        or status not in {"current", "previous", "inactive"}
        or (source != "unknown" and (type(source) is not str or _REVISION_RE.fullmatch(source) is None))
        or value["target_os"] != "ubuntu26.04"
        or value["architecture"] != "amd64"
        or value["otp_version"] != "27.3.4.6"
        or type(value["hex_version"]) is not str
        or type(value["rebar3_version"]) is not str
        or (checksum != "unknown" and (type(checksum) is not str or _SHA256_RE.fullmatch(checksum) is None))
        or (checksum == "unknown" and prefix != "unknown")
        or (checksum != "unknown" and (type(prefix) is not str or prefix != checksum[:12]))
        or type(value["installed_at"]) is not str
        or activated is not None and type(activated) is not str
        or (policy is not None and (type(policy) is not str or policy not in _POLICIES))
        or type(rollback) is not bool
        or reason is not None and type(reason) is not str
        or rollback and reason is not None
        or not rollback and reason is None
    ):
        raise _invalid_result()
    release_match = RELEASE_ID_RE.fullmatch(release_id)
    if release_match is None or release_match["version"] != application_version:
        raise _invalid_result()
    try:
        _timestamp(value["installed_at"])
        if activated is not None:
            _timestamp(activated)
    except ValueError:
        raise _invalid_result() from None
    return dict(value)


def _validate_release_rows(state: object, rows: tuple[dict[str, object], ...]) -> None:
    """Enforce cross-row lifecycle facts without re-reading any host state."""

    if state in {"empty", "manual"} and rows:
        raise _invalid_result()
    current = [row for row in rows if row["status"] == "current"]
    if state == "managed" and len(current) != 1:
        raise _invalid_result()
    if state != "managed" and current:
        raise _invalid_result()
    if len(current) > 1 or sum(row["status"] == "previous" for row in rows) > 1:
        raise _invalid_result()
    for row in rows:
        source = row["source_revision"]
        if source != "unknown":
            expected_release = f"{row['application_version']}-{str(source)[:12]}-ubuntu26.04-amd64-otp27.3.4.6"
            if row["release_id"] != expected_release:
                raise _invalid_result()
        activated = row["activated_at"]
        policy = row["incoming_migration_policy"]
        if (activated is None) != (policy is None):
            raise _invalid_result()
        if row["status"] in {"current", "previous"} and (activated is None or policy is None):
            raise _invalid_result()
        if row["status"] == "current" and row["rollback_eligible"]:
            raise _invalid_result()
        if source == "unknown":
            if (
                row["artifact_sha256"] != "unknown"
                or row["artifact_sha256_prefix"] != "unknown"
                or row["hex_version"] != "unknown"
                or row["rebar3_version"] != "unknown"
                or policy != "adopted"
            ):
                raise _invalid_result()
        elif (
            row["artifact_sha256"] == "unknown"
            or row["artifact_sha256_prefix"] == "unknown"
            or row["hex_version"] != "2.5.1"
            or row["rebar3_version"] != "3.24.0"
            or policy == "adopted"
        ):
            raise _invalid_result()


def _timestamp(value: str) -> None:
    if not value.endswith("Z") or "T" not in value:
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid timestamp")


def _with_cleanup_warning(warnings: tuple[str, ...], cleanup_warning: str | None) -> tuple[str, ...]:
    if cleanup_warning is None:
        return warnings
    if type(cleanup_warning) is not str or not cleanup_warning:
        raise _invalid_result()
    return warnings if cleanup_warning in warnings else (*warnings, cleanup_warning)


def _refused() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "lifecycle-records",
        "managed lifecycle state was refused",
        changed=False,
        next_action="inspect the managed lifecycle state and resolve the contradiction",
    )


def _invalid_result() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "release-discovery",
        "release discovery returned invalid evidence",
        changed=False,
        next_action="resolve the managed lifecycle metadata before selecting a release",
    )


__all__ = ["list_releases"]
