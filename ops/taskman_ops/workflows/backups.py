"""Controller translation for helper-owned read-only backup discovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage
from ..helper_runner import HelperInvocation, invoke_helper
from ..host_protocol import HostRequest
from ..host_helper.lifecycle import BackupRecord
from ..remote import Remote
from . import DiscoveryResult
from .helper import request as helper_request, result_error, run_request


HelperInvoker = Callable[[Remote, HelperPackage, HostRequest], HelperInvocation]


def list_backups(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    package: HelperPackage | None = None,
    invoker: HelperInvoker = invoke_helper,
) -> DiscoveryResult:
    """Ask the helper for backup rows without locally inspecting dump paths."""

    request = helper_request("list_backups", config)
    result = run_request(remote, request, package=package, invoker=invoker)
    if result.outcome != "succeeded":
        raise result_error(result)
    if not isinstance(result.state, Mapping) or set(result.state) - {"host_kind", "backups"}:
        raise _invalid_result()
    if result.state.get("host_kind") not in {"empty", "manual", "managed", "staged"}:
        raise _invalid_result()
    records = result.state.get("backups")
    if not isinstance(records, (list, tuple)):
        raise _invalid_result()
    parsed = tuple(_backup_row(row) for row in records)
    if len({row["backup_id"] for row in parsed}) != len(parsed):
        raise _invalid_result()
    return DiscoveryResult(parsed, tuple(result.warnings))


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


def _invalid_result() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "backup-discovery",
        "backup discovery returned invalid evidence",
        changed=False,
        next_action="resolve the managed lifecycle metadata before selecting a backup",
    )


__all__ = ["list_backups"]
