from __future__ import annotations

import hashlib
import json

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_helper.records import BackupRecord
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.inventory import collect_inventory
from tests.support.environments import environment_config


BACKUP_A = "backup-00000000000000000000000000000001"
BACKUP_B = "backup-00000000000000000000000000000002"
RELEASE = (
    "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
)


def _record(backup_id: str) -> dict[str, object]:
    return BackupRecord.from_mapping(
        {
            "backup_id": backup_id,
            "created_at": "2026-09-14T12:00:00Z",
            "dump_sha256": "c" * 64,
            "source_release_id": RELEASE,
            "migration_versions": [20260914000000],
            "source_database_size_bytes": 1024,
        }
    ).to_mapping()


def _digest(entries: list[dict[str, object]]) -> str:
    payload = json.dumps(
        {"operation": "list_backups", "records": entries},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _result(request: HostRequest, state: dict[str, object], *, outcome: str = "succeeded", message: str = "observed") -> HostResult:
    return HostResult(3, request.operation, request.correlation_id, outcome, message, state, ())


def test_collect_inventory_returns_complete_records_only_after_all_pages_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning after the first page would silently cap a public listing."""
    entries = [
        {"id": BACKUP_A, "record": _record(BACKUP_A)},
        {"id": BACKUP_B, "record": _record(BACKUP_B)},
    ]
    digest = _digest(entries)
    requests: list[HostRequest] = []

    def run(_remote: object, request: HostRequest, *, deadline: float) -> HostResult:
        requests.append(request)
        if len(requests) == 1:
            return _result(
                request,
                {
                    "records": (entries[0],),
                    "inventory_sha256": digest,
                    "next_cursor": {"inventory_sha256": digest, "after_id": BACKUP_A},
                },
            )
        return _result(
            request,
            {"records": (entries[1],), "inventory_sha256": digest, "next_cursor": None},
        )

    monkeypatch.setattr("taskman_ops.workflows.inventory.run_request", run)

    records = collect_inventory(
        object(), environment_config(), "list_backups", deadline=10**12
    )

    assert records == (entries[0]["record"], entries[1]["record"])
    assert requests[0].parameters == {"cursor": None}
    assert requests[1].parameters == {
        "cursor": {"inventory_sha256": digest, "after_id": BACKUP_A}
    }


def test_collect_inventory_never_returns_partial_records_after_a_later_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed second page must not expose the validated first page as success."""
    first = {"id": BACKUP_A, "record": _record(BACKUP_A)}
    digest = _digest([first])
    calls = 0

    def run(_remote: object, request: HostRequest, *, deadline: float) -> HostResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _result(
                request,
                {
                    "records": (first,),
                    "inventory_sha256": digest,
                    "next_cursor": {"inventory_sha256": digest, "after_id": BACKUP_A},
                },
            )
        return _result(request, {}, outcome="retryable", message="unavailable")

    monkeypatch.setattr("taskman_ops.workflows.inventory.run_request", run)

    with pytest.raises(OpsError):
        collect_inventory(object(), environment_config(), "list_backups", deadline=10**12)


@pytest.mark.parametrize(
    "state",
    [
        {"records": (), "inventory_sha256": "bad", "next_cursor": None},
        {
            "records": (
                {"id": BACKUP_B, "record": _record(BACKUP_B)},
                {"id": BACKUP_A, "record": _record(BACKUP_A)},
            ),
            "inventory_sha256": "d" * 64,
            "next_cursor": None,
        },
        {
            "records": ({"id": BACKUP_A, "record": _record(BACKUP_A)},),
            "inventory_sha256": "d" * 64,
            "next_cursor": {"inventory_sha256": "e" * 64, "after_id": BACKUP_A},
        },
    ],
)
def test_collect_inventory_rejects_malformed_digest_order_or_cursor(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, object]
) -> None:
    """Malformed page evidence must not become a public inventory."""
    monkeypatch.setattr(
        "taskman_ops.workflows.inventory.run_request",
        lambda _remote, request, *, deadline: _result(request, state),
    )

    with pytest.raises(OpsError) as caught:
        collect_inventory(object(), environment_config(), "list_backups", deadline=10**12)

    assert caught.value.status is ExitStatus.SAFETY


def test_collect_inventory_restarts_once_for_drift_then_reports_continuing_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbounded restart loop could outlive the command deadline under churn."""
    calls = 0

    def run(_remote: object, request: HostRequest, *, deadline: float) -> HostResult:
        nonlocal calls
        calls += 1
        return _result(request, {}, outcome="refused", message="inventory-changed")

    monkeypatch.setattr("taskman_ops.workflows.inventory.run_request", run)

    with pytest.raises(OpsError, match="inventory"):
        collect_inventory(object(), environment_config(), "list_backups", deadline=10**12)

    assert calls == 2


def test_collect_inventory_restarts_only_for_the_exact_drift_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A coincidental message on another outcome must not trigger a restart."""
    calls = 0

    def run(_remote: object, request: HostRequest, *, deadline: float) -> HostResult:
        nonlocal calls
        calls += 1
        return _result(request, {}, outcome="retryable", message="inventory-changed")

    monkeypatch.setattr("taskman_ops.workflows.inventory.run_request", run)

    with pytest.raises(OpsError):
        collect_inventory(object(), environment_config(), "list_backups", deadline=10**12)

    assert calls == 1


def test_collect_inventory_refuses_before_dispatch_after_command_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting another page after deadline would create an unbounded command."""
    monkeypatch.setattr("taskman_ops.workflows.inventory.time.monotonic", lambda: 5.0)
    monkeypatch.setattr(
        "taskman_ops.workflows.inventory.run_request",
        lambda *_args: pytest.fail("expired enumeration must not dispatch"),
    )

    with pytest.raises(OpsError, match="deadline"):
        collect_inventory(object(), environment_config(), "list_backups", deadline=4.0)


def test_collect_inventory_passes_the_shared_absolute_deadline_to_every_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_deadlines: list[float] = []
    entry = {"id": BACKUP_A, "record": _record(BACKUP_A)}
    digest = _digest([entry])

    def run(_remote: object, request: HostRequest, *, deadline: float) -> HostResult:
        seen_deadlines.append(deadline)
        return _result(
            request,
            {"records": (entry,), "inventory_sha256": digest, "next_cursor": None},
        )

    monkeypatch.setattr("taskman_ops.workflows.inventory.run_request", run)
    monkeypatch.setattr("taskman_ops.workflows.inventory.time.monotonic", lambda: 100.0)

    collect_inventory(object(), environment_config(), "list_backups", deadline=123.5)

    assert seen_deadlines == [123.5]
