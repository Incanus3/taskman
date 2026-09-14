"""Complete controller collection of byte- and count-bounded host inventories."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
import time

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..host_helper.records import BackupRecord, ReleaseRecord
from ..remote import Remote
from .helper import mutable, request as helper_request, result_error, run_request


_OPERATIONS = frozenset({"list_releases", "list_backups"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PAGE_RECORDS = 64
_MAX_DRIFT_RESTARTS = 1


def collect_inventory(
    remote: Remote,
    config: EnvironmentConfig,
    operation: str,
    *,
    deadline: float,
) -> tuple[dict[str, object], ...]:
    """Return one complete validated inventory, never a successful prefix."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("inventory collection requires a validated environment configuration")
    if type(operation) is not str or operation not in _OPERATIONS:
        raise ValueError("inventory operation is invalid")
    if type(deadline) not in {int, float}:
        raise TypeError("inventory deadline is invalid")

    restarts = 0
    while True:
        records: list[dict[str, object]] = []
        cursor: dict[str, str] | None = None
        snapshot_digest: str | None = None
        previous_id: str | None = None
        while True:
            _require_time(deadline)
            request = helper_request(
                operation,
                config,
                parameters={"cursor": cursor},
            )
            result = run_request(remote, request, deadline=deadline)
            if result.outcome != "succeeded":
                if (
                    result.outcome == "refused"
                    and result.message == "inventory-changed"
                    and restarts < _MAX_DRIFT_RESTARTS
                ):
                    restarts += 1
                    _require_time(deadline)
                    break
                raise result_error(result)

            page, digest, next_cursor = _validated_page(
                operation,
                result.state,
                expected_cursor=cursor,
                expected_digest=snapshot_digest,
                previous_id=previous_id,
            )
            if snapshot_digest is None:
                snapshot_digest = digest
            records.extend(record for _identifier, record in page)
            if page:
                previous_id = page[-1][0]
            if next_cursor is None:
                if digest != _inventory_sha256(operation, records):
                    raise _invalid(operation, "inventory digest does not match its records")
                return tuple(records)
            cursor = next_cursor
        # The host reported snapshot drift.  Discard every accumulated page
        # and restart once within the same caller-supplied command deadline.


def _validated_page(
    operation: str,
    value: object,
    *,
    expected_cursor: Mapping[str, str] | None,
    expected_digest: str | None,
    previous_id: str | None,
) -> tuple[list[tuple[str, dict[str, object]]], str, dict[str, str] | None]:
    if not isinstance(value, Mapping) or set(value) != {
        "records",
        "inventory_sha256",
        "next_cursor",
    }:
        raise _invalid(operation, "inventory page has invalid fields")
    digest = value["inventory_sha256"]
    if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
        raise _invalid(operation, "inventory page has an invalid digest")
    if expected_digest is not None and digest != expected_digest:
        raise _invalid(operation, "inventory pages have different digests")
    if expected_cursor is not None and expected_cursor["inventory_sha256"] != digest:
        raise _invalid(operation, "inventory cursor does not match its page")

    raw_records = value["records"]
    if not isinstance(raw_records, (tuple, list)) or len(raw_records) > _MAX_PAGE_RECORDS:
        raise _invalid(operation, "inventory page has an invalid record collection")
    page: list[tuple[str, dict[str, object]]] = []
    last_id = previous_id
    for raw_entry in raw_records:
        identifier, record = _validated_entry(operation, raw_entry)
        if last_id is not None and identifier <= last_id:
            raise _invalid(operation, "inventory records are not strictly ordered")
        page.append((identifier, record))
        last_id = identifier

    next_cursor = _validated_cursor(operation, value["next_cursor"])
    if next_cursor is not None:
        if not page or next_cursor != {"inventory_sha256": digest, "after_id": page[-1][0]}:
            raise _invalid(operation, "inventory continuation does not match its page")
    return page, digest, next_cursor


def _validated_entry(operation: str, value: object) -> tuple[str, dict[str, object]]:
    if not isinstance(value, Mapping) or set(value) != {"id", "record"}:
        raise _invalid(operation, "inventory record entry has invalid fields")
    identifier = value["id"]
    if type(identifier) is not str:
        raise _invalid(operation, "inventory record identifier is invalid")
    try:
        if operation == "list_releases":
            parsed = ReleaseRecord.from_mapping(mutable(value["record"]))
            actual_id = parsed.release_id
        else:
            parsed = BackupRecord.from_mapping(mutable(value["record"]))
            actual_id = parsed.backup_id
    except (TypeError, ValueError):
        raise _invalid(operation, "inventory record is invalid") from None
    if identifier != actual_id:
        raise _invalid(operation, "inventory record identity is inconsistent")
    return identifier, parsed.to_mapping()


def _validated_cursor(operation: str, value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"inventory_sha256", "after_id"}:
        raise _invalid(operation, "inventory cursor has invalid fields")
    digest = value["inventory_sha256"]
    after_id = value["after_id"]
    if (
        type(digest) is not str
        or _SHA256_RE.fullmatch(digest) is None
        or type(after_id) is not str
    ):
        raise _invalid(operation, "inventory cursor is invalid")
    return {"inventory_sha256": digest, "after_id": after_id}


def _inventory_sha256(operation: str, records: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    digest.update(b'{"operation":')
    digest.update(_canonical_ascii(operation))
    digest.update(b',"records":[')
    for index, record in enumerate(records):
        if index:
            digest.update(b",")
        identifier = (
            record["release_id"] if operation == "list_releases" else record["backup_id"]
        )
        digest.update(_canonical_ascii({"id": identifier, "record": record}))
    digest.update(b"]}")
    return digest.hexdigest()


def _canonical_ascii(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _require_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise OpsError(
            ExitStatus.SAFETY,
            "inventory",
            "inventory collection exceeded the command deadline",
            changed=False,
            next_action="retry after inventory activity has settled",
        )


def _invalid(operation: str, message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        operation,
        message,
        changed=False,
        next_action="resolve the managed inventory evidence before retrying",
    )


__all__ = ["collect_inventory"]
