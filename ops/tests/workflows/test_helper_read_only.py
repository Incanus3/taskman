"""Controller translation tests for the helper-owned read-only operations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.helper_package import build_helper_package
from taskman_ops.helper_runner import HelperInvocation
from taskman_ops.host_helper.lifecycle import BackupRecord
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.backups import list_backups
from taskman_ops.workflows.releases import list_releases
from taskman_ops.workflows.verify import run_verify


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
OTHER_RELEASE_ID = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production", "ssh_host": "203.0.113.10", "ssh_port": 22, "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43, "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10", "target_os": "ubuntu26.04", "architecture": "amd64",
            "application_port": 4000, "distribution_port": 6789, "database_name": "taskman_prod",
            "database_role": "taskman", "mail_from": "no-reply@acme.tld",
        }
    )


def _result(request, *, operation: str, lifecycle: dict[str, object] | None = None, verification: dict[str, object] | None = None, cleanup_warning: str | None = None) -> HelperInvocation:
    return HelperInvocation(
        HostResult(
            protocol_version=1, operation=operation, operation_id=request.operation_id, outcome="succeeded",
            stage="discovered" if lifecycle is not None else "verified", changed_stages=(),
            lifecycle=lifecycle or {}, runtime_state={}, verification=verification or {}, residue_paths=(),
            recovery_actions=(), warnings=tuple((lifecycle or {}).get("warnings", [])),
        ),
        cleanup_warning=cleanup_warning,
    )


def _successful_verification(
    *,
    release_id: str = RELEASE_ID,
    expected_release_id: str | None = RELEASE_ID,
) -> dict[str, object]:
    checks = [
        {"schema_version": 1, "name": name, "status": "passed", "summary": "passed"}
        for name in ("taskman-service", "release-identity", "caddy-service", "listener-topology", "startup-journal", "local-readiness", "public-readiness", "public-hsts")
    ]
    return {"schema_version": 1, "status": "ok", "exit_status": 0, "release_id": release_id, "expected_release_id": expected_release_id, "checks": checks, "next_action": None}


def test_release_listing_uses_one_helper_request_and_translates_rows(tmp_path: Path) -> None:
    """The controller must not open lifecycle roots or rebuild helper policy."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    seen = []

    result = list_releases(
        object(), _config(), package=package,
        invoker=lambda _remote, _package, request: seen.append(request) or _result(request, operation="list_releases", lifecycle={"state": "empty", "records": [], "warnings": []}),
    )

    assert result.records == ()
    assert seen[0].operation == "list_releases"
    assert seen[0].paths == {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"}


def test_backup_listing_translates_helper_warnings_without_a_store(tmp_path: Path) -> None:
    """Listing consumes the result mapping already validated at the runner boundary."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    result = list_backups(
        object(), _config(), package=package,
        invoker=lambda _remote, _package, request: _result(request, operation="list_backups", lifecycle={"state": "empty", "records": [], "warnings": ["backup metadata is stale: backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}),
    )

    assert result.warnings == ("backup metadata is stale: backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",)


def test_backup_listing_accepts_the_helper_checksum_record_schema(tmp_path: Path) -> None:
    """Controller discovery must consume the helper's finalized backup record unchanged."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    identifier = "backup-" + "a" * 32
    row = {
        "schema_version": 1,
        "backup_id": identifier,
        "created_at": "2026-09-07T00:00:00Z",
        "size_bytes": 9,
        "source_database_size_bytes": 1024,
        "database": "taskman_prod",
        "current_release_id": None,
        "candidate_release_id": None,
        "reason": "scheduled",
        "validated": True,
        "dump_path": f"/var/backups/taskman/{identifier}.dump",
        "dump_sha256": "a" * 64,
        "dump_state": "present",
    }

    result = list_backups(
        object(),
        _config(),
        package=package,
        invoker=lambda _remote, _package, request: _result(
            request,
            operation="list_backups",
            lifecycle={"state": "managed", "records": [row], "warnings": []},
        ),
    )

    assert result.records == (row,)


def test_backup_listing_accepts_the_helper_legacy_record_schema(tmp_path: Path) -> None:
    """Controller discovery must retain a helper record published before checksums."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    identifier = "backup-" + "b" * 32
    row = {
        **BackupRecord(
            1,
            identifier,
            datetime(2026, 9, 7, tzinfo=UTC),
            9,
            1024,
            "taskman_prod",
            None,
            None,
            "scheduled",
            True,
            PurePosixPath(f"/var/backups/taskman/{identifier}.dump"),
        ).to_mapping(),
        "dump_state": "present",
    }

    result = list_backups(
        object(),
        _config(),
        package=package,
        invoker=lambda _remote, _package, request: _result(
            request,
            operation="list_backups",
            lifecycle={"state": "managed", "records": [row], "warnings": []},
        ),
    )

    assert result.records == (row,)


@pytest.mark.parametrize("dump_sha256", (None, "a" * 64), ids=("legacy", "current"))
def test_backup_listing_rejects_missing_dump_state_with_a_typed_error(
    tmp_path: Path,
    dump_sha256: str | None,
) -> None:
    """Malformed helper rows must not leak a raw mapping lookup error."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    identifier = "backup-" + "c" * 32
    row = BackupRecord(
        1,
        identifier,
        datetime(2026, 9, 7, tzinfo=UTC),
        9,
        1024,
        "taskman_prod",
        None,
        None,
        "scheduled",
        True,
        PurePosixPath(f"/var/backups/taskman/{identifier}.dump"),
        dump_sha256,
    ).to_mapping()

    with pytest.raises(OpsError) as raised:
        list_backups(
            object(),
            _config(),
            package=package,
            invoker=lambda _remote, _package, request: _result(
                request,
                operation="list_backups",
                lifecycle={"state": "managed", "records": [row], "warnings": []},
            ),
        )

    assert raised.value.stage == "backup-discovery"


def test_verify_translates_one_validated_helper_report_without_remote_checks(tmp_path: Path) -> None:
    """Verification report parsing is controller output translation, not host rediscovery."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    calls = []
    result = run_verify(
        object(), _config(), RELEASE_ID, package=package,
        invoker=lambda _remote, _package, request: calls.append(request) or _result(request, operation="verify", verification=_successful_verification()),
    )

    assert result.stage == "verified"
    assert result.facts["verification"]["release_id"] == RELEASE_ID
    assert calls[0].operation == "verify"
    assert calls[0].parameters["public_ipv4"] == "203.0.113.10"
    assert calls[0].parameters["ssh_port"] == 22


def test_read_only_workflows_propagate_runner_cleanup_evidence(tmp_path: Path) -> None:
    """A successful helper result still exposes conservative cleanup residue."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    cleanup = "helper invocation cleanup left residue at a private operation path"
    releases = list_releases(
        object(), _config(), package=package,
        invoker=lambda _remote, _package, request: _result(request, operation="list_releases", lifecycle={"state": "empty", "records": [], "warnings": []}, cleanup_warning=cleanup),
    )
    backups = list_backups(
        object(), _config(), package=package,
        invoker=lambda _remote, _package, request: _result(request, operation="list_backups", lifecycle={"state": "empty", "records": [], "warnings": []}, cleanup_warning=cleanup),
    )
    verification = run_verify(
        object(), _config(), RELEASE_ID, package=package,
        invoker=lambda _remote, _package, request: _result(request, operation="verify", verification=_successful_verification(), cleanup_warning=cleanup),
    )

    assert releases.warnings == (cleanup,)
    assert backups.warnings == (cleanup,)
    assert verification.warnings == (cleanup,)


def test_release_listing_rejects_an_arbitrary_helper_row(tmp_path: Path) -> None:
    """Protocol-valid JSON must still satisfy the release-list result contract."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError, match="invalid evidence"):
        list_releases(
            object(), _config(), package=package,
            invoker=lambda _remote, _package, request: _result(request, operation="list_releases", lifecycle={"state": "empty", "records": [{"anything": "goes"}], "warnings": []}),
        )


def test_verify_rejects_a_failed_report_with_a_success_stage(tmp_path: Path) -> None:
    """Outcome, stage, and report status form one invariant rather than three hints."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    failed = _successful_verification() | {
        "status": "failed",
        "exit_status": 9,
        "next_action": "inspect the fixed verification summaries and correct the reported host state before retrying",
        "checks": [
            {"schema_version": 1, "name": name, "status": "passed" if index < 7 else "failed", "summary": "failed" if index == 7 else "passed"}
            for index, name in enumerate(("taskman-service", "release-identity", "caddy-service", "listener-topology", "startup-journal", "local-readiness", "public-readiness", "public-hsts"))
        ],
    }
    with pytest.raises(OpsError, match="conflicts with its report"):
        run_verify(
            object(), _config(), RELEASE_ID, package=package,
            invoker=lambda _remote, _package, request: HelperInvocation(
                HostResult(
                    protocol_version=1, operation="verify", operation_id=request.operation_id, outcome="failed", stage="verified",
                    changed_stages=(), lifecycle={}, runtime_state={}, verification=failed, residue_paths=(), recovery_actions=(), warnings=(),
                )
            ),
        )


def test_verify_without_an_expected_release_verifies_the_actual_current_release(tmp_path: Path) -> None:
    """The public read-only command intentionally accepts the selected managed release."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    seen = []
    result = run_verify(
        object(), _config(), package=package,
        invoker=lambda _remote, _package, request: seen.append(request) or _result(
            request,
            operation="verify",
            verification=_successful_verification(expected_release_id=None),
        ),
    )

    assert result.stage == "verified"
    assert seen[0].expected_state == {"expected_release_id": None}


def test_verify_rejects_a_successful_report_for_a_different_release(tmp_path: Path) -> None:
    """Report success is useful only when it proves the requested immutable release."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError, match="invalid evidence"):
        run_verify(
            object(), _config(), RELEASE_ID, package=package,
            invoker=lambda _remote, _package, request: _result(
                request,
                operation="verify",
                verification=_successful_verification(
                    release_id=OTHER_RELEASE_ID,
                    expected_release_id=RELEASE_ID,
                ),
            ),
        )


@pytest.mark.parametrize(
    ("outcome", "stage", "runtime_state", "recovery_actions", "status", "expected_stage"),
    (
        ("failed", "host-preflight", {"host_authority": "preflight"}, ("restore SSH administrator connectivity and required sudo access before retrying",), ExitStatus.REMOTE_PREFLIGHT, "host-preflight"),
        ("failed", "host-preflight", {"host_authority": "unsupported"}, ("use a supported Ubuntu 26.04 amd64 host and correct the environment configuration",), ExitStatus.INVALID, "host-preflight"),
        ("refused", "release-selection", {}, ("inspect the managed lifecycle metadata and resolve the contradiction before retrying",), ExitStatus.SAFETY, "verification"),
        ("failed", "lifecycle-lock", {}, ("wait for the recorded lifecycle operation to finish and retry",), ExitStatus.LOCKED, "lifecycle-lock"),
    ),
)
def test_verify_translates_stable_helper_preflight_selection_and_lock_failures(
    tmp_path: Path,
    outcome: str,
    stage: str,
    runtime_state: dict[str, object],
    recovery_actions: tuple[str, ...],
    status: ExitStatus,
    expected_stage: str,
) -> None:
    """Host policy remains helper-owned while controller exit semantics stay stable."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError) as raised:
        run_verify(
            object(),
            _config(),
            RELEASE_ID,
            package=package,
            invoker=lambda _remote, _package, request: HelperInvocation(
                HostResult(
                    protocol_version=1,
                    operation="verify",
                    operation_id=request.operation_id,
                    outcome=outcome,
                    stage=stage,
                    changed_stages=(),
                    lifecycle={},
                    runtime_state=runtime_state,
                    verification={},
                    residue_paths=(),
                    recovery_actions=recovery_actions,
                    warnings=(),
                )
            ),
        )

    assert raised.value.status is status
    assert raised.value.stage == expected_stage


@pytest.mark.parametrize("plane", ("warnings", "recovery_actions"))
def test_verify_rejects_unused_helper_warning_and_recovery_planes(tmp_path: Path, plane: str) -> None:
    """Verification uses its report mapping; spare protocol planes are unsafe ambiguity."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    def invocation(request):
        values = {
            "warnings": ("unexpected helper warning",) if plane == "warnings" else (),
            "recovery_actions": ("unexpected helper recovery",) if plane == "recovery_actions" else (),
        }
        return HelperInvocation(
            HostResult(
                protocol_version=1,
                operation="verify",
                operation_id=request.operation_id,
                outcome="succeeded",
                stage="verified",
                changed_stages=(),
                lifecycle={},
                runtime_state={},
                verification=_successful_verification(),
                residue_paths=(),
                **values,
            )
        )

    with pytest.raises(OpsError, match="invalid evidence"):
        run_verify(object(), _config(), RELEASE_ID, package=package, invoker=lambda _remote, _package, request: invocation(request))


@pytest.mark.parametrize(
    "row",
    (
        {
            "release_id": RELEASE_ID,
            "status": "current",
            "application_version": "0.2.0",
            "source_revision": "a" * 40,
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "27.3.4.6",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "artifact_sha256": "a" * 64,
            "artifact_sha256_prefix": "unknown",
            "installed_at": "2026-09-05T12:00:00Z",
            "activated_at": "2026-09-05T12:00:00Z",
            "incoming_migration_policy": "no-change",
            "rollback_eligible": False,
            "rollback_reason": "target release is already current",
        },
        {
            "release_id": RELEASE_ID,
            "status": "current",
            "application_version": "0.2.0",
            "source_revision": "b" * 40,
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "27.3.4.6",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "artifact_sha256": "a" * 64,
            "artifact_sha256_prefix": "a" * 12,
            "installed_at": "2026-09-05T12:00:00Z",
            "activated_at": None,
            "incoming_migration_policy": None,
            "rollback_eligible": False,
            "rollback_reason": "target release is already current",
        },
    ),
)
def test_release_listing_rejects_checksum_and_status_metadata_that_conflicts_with_known_identity(
    tmp_path: Path,
    row: dict[str, object],
) -> None:
    """Controller row parsing enforces the helper's complete release identity contract."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError, match="invalid evidence"):
        list_releases(
            object(),
            _config(),
            package=package,
            invoker=lambda _remote, _package, request: _result(
                request,
                operation="list_releases",
                lifecycle={"state": "managed", "records": [row], "warnings": []},
            ),
        )


@pytest.mark.parametrize(
    ("listing", "operation"),
    ((list_releases, "list_releases"), (list_backups, "list_backups")),
)
def test_read_only_listing_translates_only_the_canonical_lifecycle_lock_result(
    tmp_path: Path,
    listing,
    operation: str,
) -> None:
    """A writer contention preserves its lock status and recovery contract for either listing."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    def invocation(request):
        return HelperInvocation(
            HostResult(
                protocol_version=1,
                operation=operation,
                operation_id=request.operation_id,
                outcome="failed",
                stage="lifecycle-lock",
                changed_stages=(),
                lifecycle={},
                runtime_state={
                    "lock_holder": {
                        "operation": "deploy",
                        "pid": 4242,
                        "started_at": "2026-09-06T12:00:00Z",
                        "mode": "exclusive",
                    }
                },
                verification={},
                residue_paths=(),
                recovery_actions=("wait for the recorded lifecycle operation to finish and retry",),
                warnings=(),
            )
        )

    with pytest.raises(OpsError) as raised:
        listing(object(), _config(), package=package, invoker=lambda _remote, _package, request: invocation(request))

    assert raised.value.status is ExitStatus.LOCKED
    assert raised.value.stage == "lifecycle-lock"
    assert "deploy (pid 4242" in raised.value.message


def test_read_only_listing_rejects_a_malformed_lifecycle_lock_holder_timestamp(tmp_path: Path) -> None:
    """Holder evidence is a parsed timestamp, not merely a string that looks timestamp-like."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError, match="invalid evidence"):
        list_releases(
            object(),
            _config(),
            package=package,
            invoker=lambda _remote, _package, request: HelperInvocation(
                HostResult(
                    protocol_version=1,
                    operation="list_releases",
                    operation_id=request.operation_id,
                    outcome="failed",
                    stage="lifecycle-lock",
                    changed_stages=(),
                    lifecycle={},
                    runtime_state={
                        "lock_holder": {
                            "operation": "deploy",
                            "pid": 4242,
                            "started_at": "2026-99-99T99:99:99Z",
                            "mode": "exclusive",
                        }
                    },
                    verification={},
                    residue_paths=(),
                    recovery_actions=("wait for the recorded lifecycle operation to finish and retry",),
                    warnings=(),
                )
            ),
        )


@pytest.mark.parametrize(
    ("listing", "operation"),
    ((list_releases, "list_releases"), (list_backups, "list_backups")),
)
def test_read_only_listing_rejects_an_unused_runtime_state_plane(
    tmp_path: Path,
    listing,
    operation: str,
) -> None:
    """Discovery state is lifecycle-only; ambient process facts are not result authority."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError, match="invalid evidence"):
        listing(
            object(),
            _config(),
            package=package,
            invoker=lambda _remote, _package, request: HelperInvocation(
                HostResult(
                    protocol_version=1,
                    operation=operation,
                    operation_id=request.operation_id,
                    outcome="succeeded",
                    stage="discovered",
                    changed_stages=(),
                    lifecycle={"state": "empty", "records": [], "warnings": []},
                    runtime_state={"owner_uid": 0},
                    verification={},
                    residue_paths=(),
                    recovery_actions=(),
                    warnings=(),
                )
            ),
        )


@pytest.mark.parametrize(
    ("listing", "operation"),
    ((list_releases, "list_releases"), (list_backups, "list_backups")),
)
def test_read_only_listing_rejects_forged_lifecycle_refusal_planes(
    tmp_path: Path,
    listing,
    operation: str,
) -> None:
    """A generic refusal cannot smuggle a different recovery policy to the operator."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError, match="invalid evidence"):
        listing(
            object(),
            _config(),
            package=package,
            invoker=lambda _remote, _package, request: HelperInvocation(
                HostResult(
                    protocol_version=1,
                    operation=operation,
                    operation_id=request.operation_id,
                    outcome="refused",
                    stage="lifecycle-records",
                    changed_stages=(),
                    lifecycle={},
                    runtime_state={},
                    verification={},
                    residue_paths=(),
                    recovery_actions=("ignore the managed lifecycle state",),
                    warnings=(),
                )
            ),
        )


@pytest.mark.parametrize(
    ("artifact_sha256", "artifact_sha256_prefix", "policy"),
    (
        ("a" * 64, "a" * 12, "adopted"),
        ("unknown", "unknown", "no-change"),
    ),
)
def test_release_listing_rejects_unknown_provenance_with_direct_artifact_or_policy(
    tmp_path: Path,
    artifact_sha256: str,
    artifact_sha256_prefix: str,
    policy: str,
) -> None:
    """An adopted release cannot claim direct-build checksums or a direct activation policy."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    row = {
        "release_id": RELEASE_ID,
        "status": "current",
        "application_version": "0.2.0",
        "source_revision": "unknown",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "artifact_sha256": artifact_sha256,
        "artifact_sha256_prefix": artifact_sha256_prefix,
        "installed_at": "2026-09-05T12:00:00Z",
        "activated_at": "2026-09-05T12:00:00Z",
        "incoming_migration_policy": policy,
        "rollback_eligible": False,
        "rollback_reason": "target release is already current",
    }

    with pytest.raises(OpsError, match="invalid evidence"):
        list_releases(
            object(),
            _config(),
            package=package,
            invoker=lambda _remote, _package, request: _result(
                request,
                operation="list_releases",
                lifecycle={"state": "managed", "records": [row], "warnings": []},
            ),
        )


def _provenance_row(variant: str) -> dict[str, object]:
    """Return one complete row emitted by the corresponding helper provenance path."""

    direct = {
        "release_id": RELEASE_ID,
        "status": "current",
        "application_version": "0.2.0",
        "source_revision": "a" * 40,
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "artifact_sha256": "a" * 64,
        "artifact_sha256_prefix": "a" * 12,
        "installed_at": "2026-09-05T12:00:00Z",
        "activated_at": "2026-09-05T12:00:00Z",
        "incoming_migration_policy": "no-change",
        "rollback_eligible": False,
        "rollback_reason": "target release is already current",
    }
    adopted = direct | {
        "release_id": OTHER_RELEASE_ID,
        "source_revision": "unknown",
        "hex_version": "unknown",
        "rebar3_version": "unknown",
        "artifact_sha256": "unknown",
        "artifact_sha256_prefix": "unknown",
        "incoming_migration_policy": "adopted",
    }
    return direct if variant == "direct" else adopted


@pytest.mark.parametrize(
    ("variant", "field", "forged_value"),
    (
        ("direct", "hex_version", "unknown"),
        ("direct", "rebar3_version", "unknown"),
        ("direct", "incoming_migration_policy", "adopted"),
        ("adopted", "hex_version", "2.5.1"),
        ("adopted", "rebar3_version", "3.24.0"),
        ("adopted", "application_version", "0.3.0"),
    ),
)
def test_release_listing_rejects_each_helper_impossible_provenance_mutation(
    tmp_path: Path,
    variant: str,
    field: str,
    forged_value: object,
) -> None:
    """A protocol-valid row cannot combine direct and adopted provenance facts."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    row = _provenance_row(variant) | {field: forged_value}

    with pytest.raises(OpsError, match="invalid evidence"):
        list_releases(
            object(),
            _config(),
            package=package,
            invoker=lambda _remote, _package, request: _result(
                request,
                operation="list_releases",
                lifecycle={"state": "managed", "records": [row], "warnings": []},
            ),
        )


def test_read_only_listing_rejects_an_unhashable_lifecycle_lock_mode(tmp_path: Path) -> None:
    """Malformed nested lock evidence must become a safety result, not a TypeError."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError, match="invalid evidence"):
        list_releases(
            object(),
            _config(),
            package=package,
            invoker=lambda _remote, _package, request: HelperInvocation(
                HostResult(
                    protocol_version=1,
                    operation="list_releases",
                    operation_id=request.operation_id,
                    outcome="failed",
                    stage="lifecycle-lock",
                    changed_stages=(),
                    lifecycle={},
                    runtime_state={
                        "lock_holder": {
                            "operation": "deploy",
                            "pid": 4242,
                            "started_at": "2026-09-06T12:00:00Z",
                            "mode": {},
                        }
                    },
                    verification={},
                    residue_paths=(),
                    recovery_actions=("wait for the recorded lifecycle operation to finish and retry",),
                    warnings=(),
                )
            ),
        )


def test_verify_rejects_an_unhashable_host_authority_value(tmp_path: Path) -> None:
    """Malformed nested host authority must become verification safety evidence."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    with pytest.raises(OpsError, match="invalid host preflight evidence"):
        run_verify(
            object(),
            _config(),
            RELEASE_ID,
            package=package,
            invoker=lambda _remote, _package, request: HelperInvocation(
                HostResult(
                    protocol_version=1,
                    operation="verify",
                    operation_id=request.operation_id,
                    outcome="failed",
                    stage="host-preflight",
                    changed_stages=(),
                    lifecycle={},
                    runtime_state={"host_authority": {}},
                    verification={},
                    residue_paths=(),
                    recovery_actions=(),
                    warnings=(),
                )
            ),
        )
