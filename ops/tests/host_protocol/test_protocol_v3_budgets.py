from __future__ import annotations

import json

import pytest

from taskman_ops.host_protocol import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    HostRequest,
    HostResult,
    ProtocolError,
    decode_request,
    decode_result,
    encode_request,
    encode_result,
)


CORRELATION = "op-0123456789abcdef0123456789abcdef"


def _request(*, parameters: dict[str, object]) -> HostRequest:
    return HostRequest(
        protocol_version=3,
        operation="deploy",
        correlation_id=CORRELATION,
        expected_state={},
        paths={"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        parameters=parameters,
    )


def _fingerprints(count: int) -> list[dict[str, str]]:
    return [
        {
            "filename": f"{20260905120000 + index:014d}_migration_{index}.exs",
            "sha256": "a" * 64,
        }
        for index in range(count)
    ]


@pytest.mark.parametrize("count", (65, 256))
def test_v3_round_trips_migration_fingerprints_only_at_exact_target_manifest_paths(count: int) -> None:
    """A generic 64-item decoder must not reject valid schema-3 target manifests."""

    request = _request(
        parameters={
            "target": {
                "kind": "upload",
                "manifest": {"migrations": _fingerprints(count)},
                "artifact_sha256": "b" * 64,
                "artifact_path": "/opt/taskman/uploads/release.tar.gz",
            }
        }
    )

    assert decode_request(encode_request(request)) == request


def test_v3_round_trips_migration_fingerprints_in_installed_embedded_manifest() -> None:
    """The installed target's nested manifest has the same explicit 256-item allowance."""

    request = _request(
        parameters={
            "target": {
                "kind": "installed",
                "release_record": {
                    "migrations": _fingerprints(256),
                    "artifact_manifest": {"migrations": _fingerprints(256)},
                },
            }
        }
    )

    assert len(decode_request(encode_request(request)).parameters["target"]["release_record"]["migrations"]) == 256  # type: ignore[index]


@pytest.mark.parametrize("migrations", (_fingerprints(257),))
def test_v3_rejects_more_than_256_target_manifest_fingerprints(migrations: list[dict[str, str]]) -> None:
    """Removing the schema cap would permit an unbounded migration plan."""

    with pytest.raises(ProtocolError):
        _request(
            parameters={
                "target": {
                    "kind": "upload",
                    "manifest": {"migrations": migrations},
                    "artifact_sha256": "b" * 64,
                    "artifact_path": "/opt/taskman/uploads/release.tar.gz",
                }
            }
        )


def test_v3_rejects_65_items_at_an_unrelated_array_path() -> None:
    """Matching a field name outside a declared schema path must not widen the wire contract."""

    with pytest.raises(ProtocolError):
        _request(parameters={"unrelated": {"migrations": list(range(65))}})


def test_v3_does_not_grant_schema_array_exceptions_to_another_operation() -> None:
    """Using a matching nested field on backup must not inherit deploy's 256-item allowance."""

    with pytest.raises(ProtocolError):
        HostRequest(
            3,
            "backup",
            CORRELATION,
            {},
            {"install_root": "/opt/taskman"},
            {"target": {"manifest": {"migrations": _fingerprints(65)}}},
        )
    with pytest.raises(ProtocolError):
        HostResult(3, "backup", CORRELATION, "succeeded", "observed", {"applied_migrations": list(range(512))}, ())


@pytest.mark.parametrize("count", (512,))
def test_v3_round_trips_observed_migration_versions_at_the_discovery_state_path(count: int) -> None:
    """A completed observation may carry the specified 512 migration versions."""

    result = HostResult(
        protocol_version=3,
        operation="discover",
        correlation_id=CORRELATION,
        outcome="succeeded",
        message="observed",
        state={"applied_migrations": list(range(count))},
        warnings=(),
    )

    assert decode_result(encode_result(result)) == result


def test_v3_rejects_513_observed_migration_versions_only_at_the_exact_state_path() -> None:
    """An observation over the schema cap is invalid rather than silently truncated."""

    with pytest.raises(ProtocolError):
        HostResult(3, "discover", CORRELATION, "succeeded", "observed", {"applied_migrations": list(range(513))}, ())


def test_v3_restore_request_accepts_512_expected_migration_versions() -> None:
    request = HostRequest(
        3,
        "restore",
        CORRELATION,
        {"applied_migrations": list(range(512))},
        {"install_root": "/opt/taskman"},
        {},
    )

    assert len(request.expected_state["applied_migrations"]) == 512


def test_v3_restore_request_accepts_exact_database_migration_arrays() -> None:
    database = {"applied_migrations": list(range(512))}
    request = HostRequest(
        3,
        "restore",
        CORRELATION,
        {
            "applied_migrations": [],
            "restore_database_state": {
                "canonical": database,
                "temporary": database,
                "retired": database,
            },
        },
        {"install_root": "/opt/taskman"},
        {},
    )

    assert len(
        request.expected_state["restore_database_state"]["temporary"]["applied_migrations"]
    ) == 512


@pytest.mark.parametrize("operation", ("deploy", "genesis", "discover"))
def test_v3_other_requests_do_not_inherit_restore_expected_database_allowances(
    operation: str,
) -> None:
    with pytest.raises(ProtocolError):
        HostRequest(
            3,
            operation,
            CORRELATION,
            {
                "restore_database_state": {
                    "canonical": {"applied_migrations": list(range(65))}
                }
            },
            {"install_root": "/opt/taskman"},
            {},
        )


def test_v3_restore_discovery_accepts_all_exact_database_migration_arrays() -> None:
    database = {
        "oid": 42,
        "owner": "taskman",
        "migration_table_present": True,
        "applied_migrations": list(range(512)),
    }
    result = HostResult(
        3,
        "discover",
        CORRELATION,
        "succeeded",
        "observed",
        {
            "applied_migrations": list(range(512)),
            "restore_database_state": {
                "canonical": database,
                "temporary": database,
                "retired": database,
            },
        },
        (),
    )

    assert len(result.state["restore_database_state"]["retired"]["applied_migrations"]) == 512


def test_v3_deploy_result_does_not_inherit_restore_database_array_allowances() -> None:
    with pytest.raises(ProtocolError):
        HostResult(
            3,
            "deploy",
            CORRELATION,
            "retryable",
            "failed",
            {
                "observations": {
                    "restore_database_state": {
                        "canonical": {"applied_migrations": list(range(65))}
                    }
                }
            },
            (),
        )


def test_v3_restore_request_does_not_inherit_deploy_target_array_allowances() -> None:
    with pytest.raises(ProtocolError):
        HostRequest(
            3,
            "restore",
            CORRELATION,
            {"applied_migrations": []},
            {"install_root": "/opt/taskman"},
            {"target": {"manifest": {"migrations": _fingerprints(65)}}},
        )


@pytest.mark.parametrize(
    ("operation", "state"),
    (
        (
            "discover",
            {"observations": {"restore_database_state": {"canonical": {"applied_migrations": list(range(65))}}}},
        ),
        (
            "restore",
            {"restore_database_state": {"canonical": {"applied_migrations": list(range(65))}}},
        ),
    ),
)
def test_v3_restore_database_allowance_is_limited_to_each_operation_exact_path(
    operation: str, state: dict[str, object]
) -> None:
    with pytest.raises(ProtocolError):
        HostResult(3, operation, CORRELATION, "retryable", "failed", state, ())


def _mapping_at_exact_size(size: int) -> dict[str, object]:
    value_count = (size + 4096 - 1) // 4096
    mapping: dict[str, object] = {"blocks": ["x" * 4096 for _ in range(value_count)]}
    encoded = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    adjustment = size - len(encoded)
    assert -4096 <= adjustment <= 0
    mapping["blocks"][0] = "x" * (4096 + adjustment)  # type: ignore[index]
    return mapping


@pytest.mark.parametrize(
    ("target", "field", "budget"),
    (
        ("upload", "manifest", 128 * 1024),
        ("installed", "release_record", 256 * 1024),
    ),
)
def test_v3_enforces_exact_aggregate_target_record_budgets(target: str, field: str, budget: int) -> None:
    """Measuring only individual fields would admit an oversized persisted artifact record."""

    record = _mapping_at_exact_size(budget)
    parameters = {"target": {"kind": target, field: record}}

    assert _request(parameters=parameters).parameters["target"][field]["blocks"][0]  # type: ignore[index]
    record["blocks"][0] += "x"  # type: ignore[index]
    with pytest.raises(ProtocolError):
        _request(parameters=parameters)


def test_v3_enforces_the_other_persisted_record_budget_at_the_page_record_path() -> None:
    """A backup/list record does not inherit the larger installed-release allowance."""

    record = _mapping_at_exact_size(64 * 1024)
    result = HostResult(
        3,
        "list_backups",
        CORRELATION,
        "succeeded",
        "observed",
        {"records": [{"id": "backup-" + "c" * 32, "record": record}]},
        (),
    )

    assert result.state["records"][0]["record"]["blocks"][0]  # type: ignore[index]
    record["blocks"][0] += "x"  # type: ignore[index]
    with pytest.raises(ProtocolError):
        HostResult(3, "list_backups", CORRELATION, "succeeded", "observed", {"records": [{"id": "backup-" + "c" * 32, "record": record}]}, ())


def test_v3_accepts_a_255_byte_migration_filename_and_rejects_256_bytes() -> None:
    """Ignoring filesystem component bounds would create an unpublishable otherwise valid artifact."""

    filename = "20260905120000_" + "a" * 236 + ".exs"
    request = _request(
        parameters={
            "target": {
                "kind": "upload",
                "manifest": {"migrations": [{"filename": filename, "sha256": "a" * 64}]},
                "artifact_sha256": "b" * 64,
                "artifact_path": "/opt/taskman/uploads/release.tar.gz",
            }
        }
    )

    assert len(filename.encode()) == 255
    assert decode_request(encode_request(request)) == request
    with pytest.raises(ProtocolError):
        _request(
            parameters={
                "target": {
                    "kind": "upload",
                    "manifest": {"migrations": [{"filename": filename + "a", "sha256": "a" * 64}]},
                    "artifact_sha256": "b" * 64,
                    "artifact_path": "/opt/taskman/uploads/release.tar.gz",
                }
            }
        )


def _request_at_exact_size(size: int) -> bytes:
    values = ["x" * 4096 for _ in range(64)]
    mapping = {
        "protocol_version": 3,
        "operation": "discover",
        "correlation_id": CORRELATION,
        "expected_state": {},
        "paths": {"install_root": "/opt/taskman"},
        "parameters": {"blocks": [{"values": list(values)} for _ in range(4)]},
    }
    encoded = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    adjustment = size - len(encoded)
    assert -4096 <= adjustment <= 0
    mapping["parameters"]["blocks"][0]["values"][0] = "x" * (4096 + adjustment)
    return json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_v3_accepts_an_exact_one_mebibyte_request_and_rejects_one_extra_byte() -> None:
    """Lower transport capture limits would reject a valid complete envelope before validation."""

    payload = _request_at_exact_size(MAX_INPUT_BYTES)

    assert len(payload) == MAX_INPUT_BYTES
    assert decode_request(payload).protocol_version == 3
    with pytest.raises(ProtocolError):
        decode_request(payload + b" ")


def test_v3_accepts_an_exact_one_mebibyte_result_and_rejects_one_extra_byte() -> None:
    """The helper result budget is aggregate and includes the envelope fields."""

    mapping = {
        "protocol_version": 3,
        "operation": "discover",
        "correlation_id": CORRELATION,
        "outcome": "succeeded",
        "message": "observed",
        "state": {"blocks": [{"values": ["x" * 4096 for _ in range(64)]} for _ in range(4)]},
        "warnings": [],
    }
    encoded = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    adjustment = MAX_OUTPUT_BYTES - len(encoded)
    assert -4096 <= adjustment <= 0
    mapping["state"]["blocks"][0]["values"][0] = "x" * (4096 + adjustment)
    payload = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    assert len(payload) == MAX_OUTPUT_BYTES
    assert decode_result(payload).protocol_version == 3
    with pytest.raises(ProtocolError):
        decode_result(payload + b" ")
