"""Zipapp-level characterization of read-only lifecycle discovery."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

import pytest

from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _request(root: Path, operation: str = "discover") -> bytes:
    return encode_request(
        HostRequest(
            protocol_version=1,
            operation=operation,
            operation_id="op-0123456789abcdef0123456789abcdef",
            expected_state={},
            paths={"install_root": str(root / "install"), "backup_root": str(root / "backups")},
            parameters={},
        )
    )


def _invoke(root: Path, operation: str = "discover"):
    package = build_helper_package(root / "taskman-host.pyz")
    completed = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=_request(root, operation),
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def _release_record() -> dict[str, object]:
    stamp = AT.isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "artifact_sha256": "a" * 64,
        "installed_at": stamp,
        "activated_at": stamp,
        "previous_release_id": None,
        "backup_id": None,
        "migration_policy": "no-change",
    }


def _activation_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "activation_id": "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "previous_release_id": None,
        "candidate_release_id": RELEASE_ID,
        "activated_at": AT.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "backup_id": None,
        "migration_policy": "no-change",
    }


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 2,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": "a" * 40,
        "release_id": RELEASE_ID,
        "built_at": AT.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "elixir_version": "1.18.3",
        "node_version": "22.22.1",
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "builder_base_tag": "ubuntu:resolute-20260811.1",
        "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
        "migrations": [],
        "top_level": "taskman",
    }


def _adoption_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "adopted_at": "2026-09-05T12:00:00Z",
        "release_path": "",
        "content_sha256": "a" * 64,
        "application_version": "0.2.0",
        "source_revision": "unknown",
        "artifact_sha256": "unknown",
        "migrations": [],
    }


def _write_record(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.parent.chmod(0o750)
    path.parent.chmod(0o750)
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o600)


@pytest.mark.parametrize(
    ("state", "expected_outcome", "expected_state"),
    (
        ("empty", "succeeded", "empty"),
        ("manual", "succeeded", "manual"),
        ("managed", "succeeded", "managed"),
        ("malformed", "refused", None),
        ("symlinked", "refused", None),
        ("permission-incompatible", "refused", None),
        ("partial", "refused", None),
    ),
)
def test_built_zipapp_characterizes_lifecycle_shapes(
    tmp_path: Path,
    state: str,
    expected_outcome: str,
    expected_state: str | None,
) -> None:
    """The helper, rather than controller Python, classifies actual host storage."""

    install = tmp_path / "install"
    releases = install / "releases"
    deployments = install / "deployments"
    backups = tmp_path / "backups"
    backups.mkdir()

    if state == "manual":
        releases.mkdir(parents=True)
        manual = releases / "operator-installed"
        manual.mkdir()
        (install / "current").symlink_to(manual)
    elif state == "managed":
        (releases / RELEASE_ID).mkdir(parents=True)
        releases.chmod(0o750)
        (releases / RELEASE_ID).chmod(0o750)
        install.mkdir(exist_ok=True)
        (install / "current").symlink_to(releases / RELEASE_ID)
        _write_record(deployments / "releases" / f"release-{RELEASE_ID}.json", _release_record())
        _write_record(deployments / "activations" / "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json", _activation_record())
        _write_record(deployments / "manifests" / f"release-{RELEASE_ID}.json", _manifest())
    elif state == "malformed":
        _write_record(deployments / "releases" / f"release-{RELEASE_ID}.json", "not-json")
    elif state == "symlinked":
        target = tmp_path / "other-records"
        target.mkdir()
        deployments.mkdir(parents=True)
        (deployments / "releases").symlink_to(target, target_is_directory=True)
    elif state == "permission-incompatible":
        directory = deployments / "releases"
        directory.mkdir(parents=True)
        directory.chmod(0o777)
    elif state == "partial":
        (releases / RELEASE_ID).mkdir(parents=True)
        _write_record(deployments / "releases" / f"release-{RELEASE_ID}.json", _release_record())

    result = _invoke(tmp_path)

    assert result.outcome == expected_outcome
    if expected_state is not None:
        assert result.lifecycle["state"] == expected_state
        assert result.stage == "discovered"
    else:
        assert result.stage == "lifecycle-records"


def test_built_zipapp_lists_records_without_trusting_caller_derived_paths(tmp_path: Path) -> None:
    """Only installation and backup roots are accepted; subpaths are helper-derived."""

    result = _invoke(tmp_path, "list_releases")

    assert result.outcome == "succeeded"
    assert result.lifecycle["records"] == ()


def test_built_zipapp_preserves_manifest_provenance_in_release_rows(tmp_path: Path) -> None:
    """Listing still exposes the established release provenance from host-local records."""

    install = tmp_path / "install"
    releases = install / "releases"
    deployments = install / "deployments"
    release_path = releases / RELEASE_ID
    release_path.mkdir(parents=True)
    releases.chmod(0o750)
    release_path.chmod(0o750)
    (install / "current").symlink_to(release_path)
    _write_record(deployments / "releases" / f"release-{RELEASE_ID}.json", _release_record())
    _write_record(deployments / "activations" / "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json", _activation_record())
    _write_record(deployments / "manifests" / f"release-{RELEASE_ID}.json", _manifest())

    result = _invoke(tmp_path, "list_releases")

    assert result.outcome == "succeeded"
    row = result.lifecycle["records"][0]
    assert row == {
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


def test_built_zipapp_reads_the_existing_atomic_adoption_bundle(tmp_path: Path) -> None:
    """Manual-adoption records retain their published bundle compatibility format."""

    install = tmp_path / "install"
    releases = install / "releases"
    release_path = releases / RELEASE_ID
    release_path.mkdir(parents=True)
    releases.chmod(0o750)
    release_path.chmod(0o750)
    (install / "current").symlink_to(release_path)
    bundle = install / "deployments" / "adoption-transactions" / f"adoption-{RELEASE_ID}"
    bundle.mkdir(parents=True)
    bundle.parent.chmod(0o750)
    bundle.chmod(0o750)
    _write_record(bundle / "release.json", _release_record() | {"artifact_sha256": None, "migration_policy": "adopted"})
    _write_record(bundle / "activation.json", _activation_record() | {"migration_policy": "adopted"})
    adoption = _adoption_record() | {"release_path": str(release_path)}
    _write_record(install / "deployments" / "adoptions" / f"adoption-{RELEASE_ID}.json", adoption)

    result = _invoke(tmp_path, "list_releases")

    assert result.outcome == "succeeded"
    row = result.lifecycle["records"][0]
    assert row["application_version"] == "0.2.0"
    assert row["source_revision"] == "unknown"
    assert row["artifact_sha256"] == "unknown"


def test_discovery_does_not_depend_on_the_controller_runtime(tmp_path: Path) -> None:
    """The zipapp owns its read-only host interpretation using only stdlib code."""

    result = _invoke(tmp_path)

    assert result.runtime_state == {}


def test_built_zipapp_lists_a_manifest_with_a_full_git_revision(tmp_path: Path) -> None:
    """The established release manifest accepts both short and full SHA-256 revisions."""

    install = tmp_path / "install"
    release_path = install / "releases" / RELEASE_ID
    release_path.mkdir(parents=True)
    release_path.parent.chmod(0o750)
    release_path.chmod(0o750)
    (install / "current").symlink_to(release_path)
    _write_record(install / "deployments" / "releases" / f"release-{RELEASE_ID}.json", _release_record())
    _write_record(install / "deployments" / "activations" / "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json", _activation_record())
    _write_record(
        install / "deployments" / "manifests" / f"release-{RELEASE_ID}.json",
        _manifest() | {"source_revision": "a" * 64},
    )

    result = _invoke(tmp_path, "list_releases")

    assert result.outcome == "succeeded"
    assert result.lifecycle["records"][0]["source_revision"] == "a" * 64


def test_built_zipapp_refuses_an_existing_symlinked_authority_root(tmp_path: Path) -> None:
    """An empty root remains unsafe when the root itself is a symlink."""

    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "backups").symlink_to(target, target_is_directory=True)

    result = _invoke(tmp_path)

    assert result.outcome == "refused"
    assert result.stage == "lifecycle-records"


def test_built_zipapp_snapshots_lifecycle_under_the_shared_lock(tmp_path: Path) -> None:
    """Read-only discovery establishes the same lifecycle lock file as mutations."""

    result = _invoke(tmp_path)

    assert result.outcome == "succeeded"
    lock = tmp_path / ".taskman-lock" / "lifecycle.lock"
    assert lock.is_file()
    assert lock.stat().st_mode & 0o777 == 0o600


def test_built_zipapp_reports_the_complete_bounded_stale_inventory(tmp_path: Path) -> None:
    """Unrelated residue is visible without becoming lifecycle authority."""

    install = tmp_path / "install"
    release_path = install / "releases" / RELEASE_ID
    release_path.mkdir(parents=True)
    release_path.parent.chmod(0o750)
    release_path.chmod(0o750)
    (install / "current").symlink_to(release_path)
    deployment = install / "deployments"
    _write_record(deployment / "releases" / f"release-{RELEASE_ID}.json", _release_record())
    _write_record(deployment / "activations" / "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json", _activation_record())
    _write_record(deployment / "manifests" / f"release-{RELEASE_ID}.json", _manifest())
    (deployment / "unexpected").mkdir()
    (deployment / "releases" / "loose").write_text("residue", encoding="utf-8")
    (deployment / "adoption-transactions" / "abandoned").mkdir(parents=True)
    (deployment / "adoption-transactions").chmod(0o750)
    (deployment / "adoption-transactions" / "abandoned").chmod(0o750)
    (deployment / "manifests" / "stray.json").write_text("residue", encoding="utf-8")
    (install / "releases" / "stray-directory").mkdir()
    (install / "releases" / "stray-file").write_text("residue", encoding="utf-8")
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "orphan.dump").write_text("residue", encoding="utf-8")

    result = _invoke(tmp_path)

    assert result.outcome == "succeeded"
    assert result.lifecycle["warnings"] == (
        "orphan backup dump: orphan.dump",
        "unexpected deployment-root entry: unexpected",
        "unrecognized deployment storage entry: releases/loose",
        "unrecognized lifecycle transaction: abandoned",
        "unrecognized manifest entry: stray.json",
        "unrecognized release directory: stray-directory",
        "unrecognized release entry: stray-file",
    )


def test_built_zipapp_reports_bounded_inventory_truncation_with_the_total_count(tmp_path: Path) -> None:
    """Inventory never materializes an unbounded directory just to trim its warnings later."""

    deployment = tmp_path / "install" / "deployments"
    deployment.mkdir(parents=True)
    deployment.chmod(0o750)
    for number in range(80):
        (deployment / f"unexpected-{number:03d}").mkdir()

    result = _invoke(tmp_path)

    assert result.outcome == "succeeded"
    assert "inventory truncated: deployment-root (80 entries; listed first 32)" in result.lifecycle["warnings"]
    assert len(result.lifecycle["warnings"]) <= 33


def test_built_zipapp_refuses_an_authoritative_record_category_over_its_stream_limit(tmp_path: Path) -> None:
    """A hostile record directory is refused before a controller sees partial lifecycle facts."""

    releases = tmp_path / "install" / "deployments" / "releases"
    releases.mkdir(parents=True)
    releases.chmod(0o750)
    for index in range(65):
        entry = releases / f"unknown-{index:02d}"
        entry.write_text("x", encoding="utf-8")
        entry.chmod(0o600)

    result = _invoke(tmp_path)

    assert result.outcome == "refused"
    assert result.stage == "lifecycle-records"
    assert result.recovery_actions == ("inspect the managed lifecycle state before retrying",)
