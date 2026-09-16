from __future__ import annotations

from copy import deepcopy

import pytest

from taskman_ops.host_protocol import HostResult, ProtocolError, validate_mutation_state


RELEASE = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
OTHER_RELEASE = "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp29.0.6-" + "d" * 64
SELECTION = "selection-" + "e" * 64 + ".json"
BACKUP = "backup-" + "a" * 32
OTHER_BACKUP = "backup-" + "b" * 32
CORRELATION = "op-0123456789abcdef0123456789abcdef"
CHECK_NAMES = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)


def report(*, successful: bool) -> dict[str, object]:
    checks = [
        {
            "schema_version": 1,
            "name": name,
            "status": "passed",
            "summary": "passed",
        }
        for name in CHECK_NAMES
    ]
    if not successful:
        checks[-1]["status"] = "failed"
    return {
        "schema_version": 1,
        "status": "ok" if successful else "failed",
        "exit_status": 0 if successful else 9,
        "release_id": RELEASE,
        "expected_release_id": RELEASE,
        "checks": checks,
        "next_action": (
            None
            if successful
            else "inspect the fixed verification summaries and correct the reported host state before retrying"
        ),
    }


def observations(*, restore: bool = False) -> dict[str, object]:
    value: dict[str, object] = {
        "selected_release_id": RELEASE,
        "last_successful_selection_id": SELECTION,
        "applied_migrations": [20260905120000],
        "protected_backup_ids": [BACKUP],
        "backup_protection_sha256": "1" * 64,
        "restore_target_sha256": None,
        "database_state": "ready",
        "service_state": "running",
        "scheduled_backup_sha256": "2" * 64,
        "backup_timer_enabled": True,
        "backup_timer_state": "active",
    }
    if restore:
        value["restore_database_state"] = {
            "canonical": {
                "oid": 42,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": [20260905120000],
            },
            "temporary": None,
            "retired": None,
        }
    return value


def deploy_state(
    *,
    mutation_state: str = "changed",
    exit_code: int = 0,
    failed_boundary: str | None = None,
    verification_report: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "mutation_state": mutation_state,
        "exit_code": exit_code,
        "failed_boundary": failed_boundary,
        "observations": observations(),
        "unavailable_fields": [],
        "inspection_error": None,
        "report": report(successful=True) if verification_report is None and exit_code == 0 else verification_report,
        "desired_release_id": RELEASE,
        "backup_id": BACKUP,
    }


def test_deploy_success_requires_exact_complete_passing_authority() -> None:
    """Dropping a completion field must make an otherwise plausible success untrusted."""

    state = deploy_state()

    assert validate_mutation_state("deploy", "succeeded", state) == state
    for field in tuple(state):
        malformed = deepcopy(state)
        del malformed[field]
        with pytest.raises(ProtocolError):
            validate_mutation_state("deploy", "succeeded", malformed)

    malformed = {**state, "unexpected": False}
    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", "succeeded", malformed)


def test_failed_verification_retains_the_attempted_report() -> None:
    """Replacing an available failed report with null must fail verification-result validation."""

    state = deploy_state(
        exit_code=9,
        failed_boundary="verification",
        verification_report=report(successful=False),
    )

    assert validate_mutation_state("deploy", "retryable", state)["report"] == report(
        successful=False
    )
    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", "retryable", {**state, "report": None})


def test_lifecycle_verification_failure_retains_exit_eight_report() -> None:
    """A failed lifecycle check uses the release boundary, not readiness exit 9."""

    lifecycle_report = report(successful=True)
    lifecycle_report.update(
        status="failed",
        exit_status=8,
        checks=[
            {**check, "status": "failed" if index == 0 else "passed"}
            for index, check in enumerate(lifecycle_report["checks"][:5])
        ],
        next_action="inspect the fixed verification summaries and correct the reported host state before retrying",
    )
    state = deploy_state(
        exit_code=8,
        failed_boundary="verification",
        verification_report=lifecycle_report,
    )

    assert validate_mutation_state("deploy", "retryable", state)["report"] == lifecycle_report


def test_passing_report_survives_a_later_history_failure() -> None:
    """A history error after readiness must not erase the earlier passing proof."""

    state = deploy_state(
        exit_code=8,
        failed_boundary="history",
        verification_report=report(successful=True),
    )

    validated = validate_mutation_state("deploy", "retryable", state)

    assert validated["report"] == report(successful=True)
    assert validated["failed_boundary"] == "history"


def test_failed_report_cannot_masquerade_as_a_later_history_failure() -> None:
    state = deploy_state(
        exit_code=8,
        failed_boundary="history",
        verification_report=report(successful=False),
    )

    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", "retryable", state)


@pytest.mark.parametrize(
    ("outcome", "exit_code", "boundary"),
    (
        ("succeeded", 9, None),
        ("retryable", 0, "verification"),
        ("refused", 12, "lock"),
        ("retryable", 2, "input"),
        ("retryable", 9, "history"),
    ),
)
def test_outcome_exit_status_and_failure_boundary_must_agree(
    outcome: str, exit_code: int, boundary: str | None
) -> None:
    """A contradictory envelope must not be reinterpreted into a public status."""

    state = deploy_state(
        exit_code=exit_code,
        failed_boundary=boundary,
        verification_report=report(successful=False) if boundary == "verification" else None,
    )

    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", outcome, state)


def test_operation_category_controls_shared_failure_boundary_exit_code() -> None:
    """Selection/service/history failures retain release lifecycle exit 8."""

    restore = {
        **deploy_state(
            mutation_state="unknown",
            exit_code=8,
            failed_boundary="selection",
            verification_report=None,
        ),
        "observations": observations(restore=True),
        "pre_restore_backup_id": None,
    }
    assert validate_mutation_state("restore", "retryable", restore)["exit_code"] == 8

    deploy = deploy_state(
        mutation_state="unknown",
        exit_code=11,
        failed_boundary="selection",
        verification_report=None,
    )
    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", "retryable", deploy)


@pytest.mark.parametrize("operation", ("deploy", "genesis"))
@pytest.mark.parametrize("mutation_state", ("changed", "unknown", "unchanged"))
def test_deployment_inspection_failure_retains_discovery_exit_five(operation: str, mutation_state: str) -> None:
    state = deploy_state(mutation_state=mutation_state, exit_code=5, failed_boundary="inspection")

    exact = validate_mutation_state(operation, "retryable", state)
    assert exact["exit_code"] == 5
    assert exact["mutation_state"] == mutation_state


@pytest.mark.parametrize("operation", ("deploy", "genesis"))
@pytest.mark.parametrize("boundary", ("selection", "service", "history"))
def test_release_lifecycle_failures_cannot_use_discovery_exit_five(operation: str, boundary: str) -> None:
    state = deploy_state(exit_code=5, failed_boundary=boundary)

    with pytest.raises(ProtocolError):
        validate_mutation_state(operation, "retryable", state)


def test_verification_command_failure_can_report_unavailable_report() -> None:
    """A lost verification reply must not fabricate check results."""

    restore = {
        **deploy_state(
            mutation_state="unknown",
            exit_code=9,
            failed_boundary="verification",
            verification_report=None,
        ),
        "observations": observations(restore=True),
        "pre_restore_backup_id": OTHER_BACKUP,
    }

    assert validate_mutation_state("restore", "retryable", restore)["report"] is None


def test_cleanup_delete_failure_keeps_cleanup_status_with_retryable_outcome() -> None:
    state = cleanup_state(completed_targets=[])
    state.update(exit_code=10, failed_boundary="cleanup", mutation_state="unknown")

    assert validate_mutation_state("cleanup", "retryable", state)["exit_code"] == 10


def test_unavailable_markers_distinguish_unknown_from_proved_absence() -> None:
    """Stale or contradictory final values must not masquerade as unavailable observations."""

    state = deploy_state(
        mutation_state="unknown",
        exit_code=8,
        failed_boundary="service",
        verification_report=None,
    )
    state["backup_id"] = None
    state["observations"] = {
        key: (
            "unknown"
            if key in {"database_state", "service_state", "backup_timer_state"}
            else None
        )
        for key in observations()
    }
    state["unavailable_fields"] = sorted(state["observations"])
    state["inspection_error"] = "inspection-failed"

    assert validate_mutation_state("deploy", "retryable", state) == state

    stale = deepcopy(state)
    stale["observations"]["selected_release_id"] = RELEASE
    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", "retryable", stale)

    unmarked = deepcopy(state)
    unmarked["unavailable_fields"].remove("service_state")
    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", "retryable", unmarked)

    empty_not_unknown = deepcopy(state)
    empty_not_unknown["observations"]["applied_migrations"] = []
    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", "retryable", empty_not_unknown)


def test_proved_absent_selection_does_not_require_an_unavailable_marker() -> None:
    """A null current observed under the lock must remain distinct from failed inspection."""

    state = deploy_state(
        mutation_state="unchanged",
        exit_code=10,
        failed_boundary="authority",
        verification_report=None,
    )
    state["desired_release_id"] = None
    state["backup_id"] = None
    state["observations"]["selected_release_id"] = None
    state["observations"]["last_successful_selection_id"] = None
    state["observations"]["restore_target_sha256"] = None
    state["observations"]["scheduled_backup_sha256"] = None

    assert validate_mutation_state("genesis", "refused", state) == state


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("desired_release_id", OTHER_BACKUP),
        ("backup_id", RELEASE),
    ),
)
def test_deploy_operation_identifiers_use_their_exact_domains(field: str, value: str) -> None:
    """Swapping release and backup identifier classes must be rejected."""

    with pytest.raises(ProtocolError):
        validate_mutation_state("deploy", "succeeded", {**deploy_state(), field: value})


def test_restore_validates_exact_database_observation_conditions() -> None:
    """A migration table absence cannot carry an invented empty migration array."""

    state = {
        **deploy_state(),
        "observations": observations(restore=True),
        "backup_id": BACKUP,
        "pre_restore_backup_id": OTHER_BACKUP,
    }

    assert validate_mutation_state("restore", "succeeded", state) == state

    malformed = deepcopy(state)
    malformed["observations"]["restore_database_state"]["canonical"] = {
        "oid": 42,
        "owner": "taskman",
        "migration_table_present": False,
        "applied_migrations": [],
    }
    with pytest.raises(ProtocolError):
        validate_mutation_state("restore", "succeeded", malformed)


def test_restore_keeps_independent_common_observations_when_database_identity_is_unavailable() -> None:
    state = {
        **deploy_state(
            mutation_state="unknown",
            exit_code=11,
            failed_boundary="inspection",
            verification_report=None,
        ),
        "observations": observations(restore=True),
        "pre_restore_backup_id": None,
    }
    state["observations"]["restore_database_state"] = None
    state["unavailable_fields"] = ["restore_database_state"]
    state["inspection_error"] = "unsafe-observation"

    validated = validate_mutation_state("restore", "retryable", state)

    assert validated["observations"]["applied_migrations"] == [20260905120000]
    assert validated["observations"]["restore_database_state"] is None


def cleanup_state(*, completed_targets: list[dict[str, object]]) -> dict[str, object]:
    return {
        "mutation_state": "changed" if completed_targets else "unchanged",
        "exit_code": 0,
        "failed_boundary": None,
        "observations": {
            "selected_release_id": RELEASE,
            "last_successful_selection_id": SELECTION,
            "backup_protection_sha256": "1" * 64,
            "restore_target_sha256": None,
        },
        "unavailable_fields": [],
        "inspection_error": None,
        "report": None,
        "completed_targets": completed_targets,
    }


def test_cleanup_completions_are_exact_sorted_unique_targets() -> None:
    """A result must not imply completion through duplicate or out-of-order target evidence."""

    targets = [
        {"kind": "backup", "identifier": BACKUP, "path": f"/var/backups/taskman/{BACKUP}.dump"},
        {"kind": "release", "identifier": RELEASE, "path": f"/opt/taskman/releases/{RELEASE}"},
    ]
    state = cleanup_state(completed_targets=targets)

    assert validate_mutation_state("cleanup", "succeeded", state) == state
    for malformed_targets in ([targets[1], targets[0]], [targets[0], targets[0]]):
        with pytest.raises(ProtocolError):
            validate_mutation_state(
                "cleanup",
                "succeeded",
                cleanup_state(completed_targets=malformed_targets),
            )


def test_cleanup_target_accepts_exact_1024_byte_path_and_255_byte_component() -> None:
    identifier = "e" * 255
    path = "/" + "/".join(("a" * 255, "b" * 255, "c" * 255, identifier))
    state = cleanup_state(
        completed_targets=[{"kind": "temporary", "identifier": identifier, "path": path}]
    )

    assert len(path.encode("utf-8")) == 1024
    assert validate_mutation_state("cleanup", "succeeded", state)["completed_targets"]


@pytest.mark.parametrize(
    ("identifier", "path"),
    (
        ("e" * 256, "/tmp/" + "e" * 256),
        (
            "e" * 204,
            "/" + "/".join(("a" * 204, "b" * 204, "c" * 204, "d" * 204, "e" * 204)),
        ),
    ),
)
def test_cleanup_target_rejects_oversized_component_or_path(identifier: str, path: str) -> None:
    assert len(identifier.encode("utf-8")) == (256 if len(identifier) == 256 else 204)
    with pytest.raises(ProtocolError):
        validate_mutation_state(
            "cleanup",
            "succeeded",
            cleanup_state(
                completed_targets=[
                    {"kind": "temporary", "identifier": identifier, "path": path}
                ]
            ),
        )


def test_protocol_collection_limits_apply_to_exact_mutation_paths() -> None:
    """Supported 512-version results must fit without relaxing unrelated recovery arrays."""

    state = deploy_state()
    state["observations"]["applied_migrations"] = list(range(512))
    result = HostResult(3, "deploy", CORRELATION, "succeeded", "done", state, ())

    assert len(result.state["observations"]["applied_migrations"]) == 512
    assert len(validate_mutation_state("deploy", "succeeded", result.state)["observations"]["applied_migrations"]) == 512

    too_many = deploy_state()
    too_many["observations"]["applied_migrations"] = list(range(513))
    with pytest.raises(ProtocolError):
        HostResult(3, "deploy", CORRELATION, "succeeded", "done", too_many, ())

    too_many_protections = deploy_state()
    too_many_protections["observations"]["protected_backup_ids"] = [
        f"backup-{index:032x}" for index in range(65)
    ]
    with pytest.raises(ProtocolError):
        HostResult(3, "deploy", CORRELATION, "succeeded", "done", too_many_protections, ())


def test_restore_database_observations_receive_only_their_exact_512_version_allowance() -> None:
    """A restore database page must not be clipped at 64 or relax a sibling array."""

    state = {
        **deploy_state(),
        "observations": observations(restore=True),
        "backup_id": BACKUP,
        "pre_restore_backup_id": OTHER_BACKUP,
    }
    versions = list(range(512))
    state["observations"]["applied_migrations"] = versions
    state["observations"]["restore_database_state"]["canonical"]["applied_migrations"] = versions

    result = HostResult(3, "restore", CORRELATION, "succeeded", "done", state, ())

    assert len(result.state["observations"]["restore_database_state"]["canonical"]["applied_migrations"]) == 512

    too_many = deepcopy(state)
    versions = list(range(513))
    too_many["observations"]["applied_migrations"] = versions
    too_many["observations"]["restore_database_state"]["canonical"]["applied_migrations"] = versions
    with pytest.raises(ProtocolError):
        HostResult(3, "restore", CORRELATION, "succeeded", "done", too_many, ())

    unrelated = deepcopy(state)
    unrelated["observations"]["restore_database_state"]["canonical"]["unrelated"] = list(range(65))
    with pytest.raises(ProtocolError):
        HostResult(3, "restore", CORRELATION, "succeeded", "done", unrelated, ())
