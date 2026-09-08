"""Controller translation for completed backup manifests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage
from ..helper_runner import invoke_helper
from ..host_helper.records import BackupRecord
from ..host_protocol import HostRequest, HostResult
from ..remote import Remote
from . import DiscoveryResult
from .helper import request as helper_request, result_error, run_request


HelperInvoker = Callable[[Remote, HelperPackage, HostRequest], HostResult]


def list_backups(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    package: HelperPackage | None = None,
    invoker: HelperInvoker = invoke_helper,
) -> DiscoveryResult:
    """Ask the helper for validated backup rows without re-reading dump paths."""

    request = helper_request("list_backups", config)
    result = run_request(remote, request, package=package, invoker=invoker)
    if result.outcome != "succeeded":
        raise result_error(result)
    return _discovery(result.state, result.warnings)


def _discovery(state: Mapping[str, object], warnings: Sequence[str]) -> DiscoveryResult:
    if not isinstance(state, Mapping) or "backups" not in state:
        raise _invalid_result()
    records = state["backups"]
    if not isinstance(records, (list, tuple)):
        raise _invalid_result()
    parsed = tuple(_backup_row(row) for row in records)
    if len({row["backup_id"] for row in parsed}) != len(parsed):
        raise _invalid_result()
    return DiscoveryResult(parsed, tuple(warnings))


def _backup_row(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid_result()
    try:
        return BackupRecord.from_mapping(value).to_mapping()
    except (TypeError, ValueError):
        raise _invalid_result() from None


def _invalid_result() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "backup-discovery",
        "backup discovery returned invalid evidence",
        changed=False,
        next_action="resolve the managed backup metadata before selecting a backup",
    )


__all__ = ["list_backups"]
