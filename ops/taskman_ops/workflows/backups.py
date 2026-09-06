"""Controller translation for helper-owned read-only backup discovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage, temporary_helper_package
from ..helper_runner import HelperInvocation, invoke_helper, new_operation_id
from ..host_protocol import HostRequest
from ..host_helper.lifecycle import BackupRecord
from ..remote import Remote
from . import DiscoveryResult
from .helper_results import lifecycle_lock_error, lifecycle_records_refusal


HelperInvoker = Callable[[Remote, HelperPackage, HostRequest], HelperInvocation]


def list_backups(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    package: HelperPackage | None = None,
    invoker: HelperInvoker = invoke_helper,
) -> DiscoveryResult:
    """Ask the helper for backup rows without locally inspecting dump paths."""

    request = HostRequest(
        protocol_version=1,
        operation="list_backups",
        operation_id=new_operation_id(),
        expected_state={},
        paths={"install_root": config.install_root.as_posix(), "backup_root": config.backup_root.as_posix()},
        parameters={},
    )
    if package is None:
        with temporary_helper_package() as temporary:
            invocation = invoker(remote, temporary, request)
    else:
        invocation = invoker(remote, package, request)
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
    if not isinstance(result.lifecycle, Mapping) or set(result.lifecycle) != {"state", "records", "warnings"}:
        raise _invalid_result()
    if type(result.lifecycle["state"]) is not str or result.lifecycle["state"] not in {"empty", "manual", "managed", "staged"}:
        raise _invalid_result()
    records = result.lifecycle["records"]
    lifecycle_warnings = result.lifecycle["warnings"]
    if not isinstance(records, (list, tuple)) or not isinstance(lifecycle_warnings, (list, tuple)) or not all(type(warning) is str for warning in lifecycle_warnings):
        raise _invalid_result()
    if tuple(lifecycle_warnings) != tuple(result.warnings):
        raise _invalid_result()
    parsed = tuple(_backup_row(row) for row in records)
    if len({row["backup_id"] for row in parsed}) != len(parsed):
        raise _invalid_result()
    warnings = tuple(result.warnings)
    if invocation.cleanup_warning is not None:
        if type(invocation.cleanup_warning) is not str or not invocation.cleanup_warning:
            raise _invalid_result()
        if invocation.cleanup_warning not in warnings:
            warnings = (*warnings, invocation.cleanup_warning)
    return DiscoveryResult(parsed, warnings)


def _backup_row(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or "dump_state" not in value:
        raise _invalid_result()
    record_fields = set(value) - {"dump_state"}
    dump_state = value["dump_state"]
    if (
        record_fields not in {BackupRecord._FIELDS, BackupRecord._LEGACY_FIELDS}
        or type(dump_state) is not str
        or dump_state not in {"present", "stale"}
    ):
        raise _invalid_result()
    try:
        BackupRecord.from_mapping({key: value[key] for key in record_fields})
    except (TypeError, ValueError):
        raise _invalid_result() from None
    return dict(value)


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
        "backup-discovery",
        "backup discovery returned invalid evidence",
        changed=False,
        next_action="resolve the managed lifecycle metadata before selecting a backup",
    )


__all__ = ["list_backups"]
