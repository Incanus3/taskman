"""Controller translation for completed release manifests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..helper_package import HelperPackage
from ..helper_runner import invoke_helper
from ..host_helper.records import ReleaseRecord
from ..host_protocol import HostRequest, HostResult
from ..remote import Remote
from . import DiscoveryResult
from .helper import request as helper_request, result_error, run_request


HelperInvoker = Callable[[Remote, HelperPackage, HostRequest], HostResult]


def list_releases(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    package: HelperPackage | None = None,
    invoker: HelperInvoker = invoke_helper,
) -> DiscoveryResult:
    """Ask the helper for completed release rows and validate only that contract."""

    request = helper_request("list_releases", config)
    result = run_request(remote, request, package=package, invoker=invoker)
    if result.outcome != "succeeded":
        raise result_error(result)
    return _discovery(result.state, result.warnings)


def _discovery(state: Mapping[str, object], warnings: Sequence[str]) -> DiscoveryResult:
    if not isinstance(state, Mapping) or "releases" not in state:
        raise _invalid_result()
    records = state["releases"]
    if not isinstance(records, (list, tuple)):
        raise _invalid_result()
    parsed = tuple(_release_row(row) for row in records)
    if len({row["release_id"] for row in parsed}) != len(parsed):
        raise _invalid_result()
    return DiscoveryResult(parsed, tuple(warnings))


def _release_row(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid_result()
    try:
        return ReleaseRecord.from_mapping(value).to_mapping()
    except (TypeError, ValueError):
        raise _invalid_result() from None


def _invalid_result() -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "release-discovery",
        "release discovery returned invalid evidence",
        changed=False,
        next_action="resolve the managed release metadata before selecting a release",
    )


__all__ = ["list_releases"]
