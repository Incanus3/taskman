from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import tarfile

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_helper.records import ReleaseRecord
from taskman_ops.releases.artifacts import CleanInputs, DeploymentTarget, clean_inputs_match, resolve_deploy_target
from taskman_ops.releases.build import SourceState
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import (
    APPLICATION, ARCHITECTURE, BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, ELIXIR_VERSION,
    HEX_VERSION, NODE_VERSION, OTP_VERSION, REBAR3_VERSION, SCHEMA_VERSION, TARGET_OS,
    TOP_LEVEL, ArtifactManifest, MigrationFingerprint, VerifiedArtifact, manifest_to_json, verify_artifact,
)

REVISION = "a" * 40


def _archive(path: Path, payload: str) -> None:
    root = path.parent / "tree"
    for directory in ("bin", "lib", "releases", "erts-16.0"):
        (root / TOP_LEVEL / directory).mkdir(parents=True, exist_ok=True)
    for launcher in ("taskman", "server", "migrate", "create-admin"):
        item = root / TOP_LEVEL / "bin" / launcher
        item.write_text(f"#!/bin/sh\n# {payload}\n", encoding="utf-8")
        item.chmod(0o755)
    with tarfile.open(path, "w:gz") as value:
        value.add(root / TOP_LEVEL, arcname=TOP_LEVEL)


def _artifact(root: Path, *, revision: str = REVISION, payload: str = "one", built_at: datetime | None = None) -> VerifiedArtifact:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    working = root / f"payload-{payload}.tar.gz"
    _archive(working, payload)
    digest = hashlib.sha256(working.read_bytes()).hexdigest()
    release_id = build_release_id("0.2.0", revision, artifact_sha256=digest, source_dirty=False)
    directory = root / f"{release_id}-{payload}"
    directory.mkdir()
    archive = directory / f"taskman-{release_id}.tar.gz"
    working.rename(archive)
    manifest = ArtifactManifest.from_mapping(
        {
            "schema_version": SCHEMA_VERSION, "application": APPLICATION, "application_version": "0.2.0",
            "source_revision": revision, "release_id": release_id,
            "built_at": (built_at or datetime(2026, 1, 1, tzinfo=UTC)).isoformat().replace("+00:00", "Z"),
            "target_os": TARGET_OS, "architecture": ARCHITECTURE, "otp_version": OTP_VERSION,
            "elixir_version": ELIXIR_VERSION, "node_version": NODE_VERSION, "hex_version": HEX_VERSION,
            "rebar3_version": REBAR3_VERSION, "builder_base_tag": BUILDER_BASE_TAG,
            "builder_base_digest": BUILDER_BASE_DIGEST, "migrations": [], "top_level": TOP_LEVEL,
            "artifact_sha256": digest, "source_dirty": False,
        }
    )
    manifest_path = directory / f"taskman-{release_id}.manifest.json"
    manifest_path.write_text(manifest_to_json(manifest), encoding="utf-8")
    checksum = directory / f"{archive.name}.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return verify_artifact(archive, manifest_path, checksum)


def _record(artifact: VerifiedArtifact) -> ReleaseRecord:
    return ReleaseRecord(artifact.manifest.release_id, artifact.manifest.source_revision, artifact.sha256, (), 2, artifact.manifest)


def _inputs(artifact: VerifiedArtifact) -> CleanInputs:
    manifest = artifact.manifest
    return CleanInputs(manifest.source_revision, manifest.application_version, manifest.target_os, manifest.architecture,
                       manifest.otp_version, manifest.elixir_version, manifest.node_version, manifest.hex_version,
                       manifest.rebar3_version, manifest.builder_base_tag, manifest.builder_base_digest,
                       manifest.top_level, manifest.migrations)


def _clean_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("taskman_ops.releases.artifacts.read_repository_state", lambda _repo: SourceState(REVISION, True))


def test_target_resolution_prefers_matching_selected_record_without_an_archive_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing an installed record with a fabricated archive would force an unnecessary upload."""
    artifact = _artifact(tmp_path / "artifacts")
    _clean_checkout(monkeypatch)
    target = resolve_deploy_target(tmp_path / "repo", None, installed_records=(_record(artifact),),
                                   selected_release_id=artifact.manifest.release_id, last_successful_release_id=None,
                                   clean_inputs=_inputs(artifact), artifact_root=tmp_path / "cache")
    assert isinstance(target, DeploymentTarget)
    assert target.source == "installed"
    assert target.artifact is None
    assert target.release_record == _record(artifact)


def test_target_resolution_orders_other_matching_installed_records_by_full_release_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing deterministic ordering would make equivalent host inventories choose differently."""
    first = _artifact(tmp_path / "artifacts", payload="first")
    second = _artifact(tmp_path / "artifacts", payload="second")
    _clean_checkout(monkeypatch)
    target = resolve_deploy_target(tmp_path / "repo", None, installed_records=(_record(second), _record(first)),
                                   selected_release_id=None, last_successful_release_id=None, clean_inputs=_inputs(first),
                                   artifact_root=tmp_path / "cache")
    assert target.source == "installed"
    assert target.release_id == min(first.manifest.release_id, second.manifest.release_id)


def test_target_resolution_uses_deterministically_sorted_verified_cache_after_host_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignoring cache path ordering can change a desired target when archives differ."""
    first = _artifact(tmp_path / "cache", payload="first")
    second = _artifact(tmp_path / "cache", payload="second")
    _clean_checkout(monkeypatch)
    target = resolve_deploy_target(tmp_path / "repo", None, installed_records=(), selected_release_id=None,
                                   last_successful_release_id=None, clean_inputs=_inputs(first), artifact_root=tmp_path / "cache")
    assert target.source == "cached"
    assert target.release_id == min(first.manifest.release_id, second.manifest.release_id)


def test_explicit_artifact_does_not_bypass_invalid_installed_authority(tmp_path: Path) -> None:
    """Skipping host-authority validation for explicit bytes would hide unsupported host metadata."""
    artifact = _artifact(tmp_path / "supplied")
    with pytest.raises(OpsError) as raised:
        resolve_deploy_target(tmp_path / "repo", artifact.archive, installed_records=(object(),),
                              selected_release_id=None, last_successful_release_id=None)
    assert raised.value.status is ExitStatus.SAFETY


def test_dirty_automatic_resolution_builds_before_reusing_an_exact_installed_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checking installed records before freezing dirty bytes can deploy a different checkout."""
    artifact = _artifact(tmp_path / "built")
    monkeypatch.setattr("taskman_ops.releases.artifacts.read_repository_state", lambda _repo: SourceState(REVISION, False))
    calls: list[bool] = []
    def builder(_repo: Path, _root: Path) -> VerifiedArtifact:
        calls.append(True)
        return artifact
    target = resolve_deploy_target(tmp_path / "repo", None, installed_records=(_record(artifact),),
                                   selected_release_id=artifact.manifest.release_id, last_successful_release_id=None,
                                   allow_dirty=True, artifact_root=tmp_path / "cache", builder=builder)
    assert calls == [True]
    assert target.source == "installed"


def test_clean_allow_dirty_uses_the_clean_resolution_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Treating a clean checkout as dirty loses installed/cache reuse without cause."""
    artifact = _artifact(tmp_path / "artifacts")
    _clean_checkout(monkeypatch)
    target = resolve_deploy_target(tmp_path / "repo", None, installed_records=(_record(artifact),),
                                   selected_release_id=artifact.manifest.release_id, last_successful_release_id=None,
                                   allow_dirty=True, clean_inputs=_inputs(artifact), artifact_root=tmp_path / "cache")
    assert target.source == "installed"


def test_clean_input_equality_refuses_a_changed_checkout_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Accepting changed fingerprints after discovery would confirm stale source provenance."""
    artifact = _artifact(tmp_path / "artifacts")
    _clean_checkout(monkeypatch)
    inputs = _inputs(artifact)
    monkeypatch.setattr("taskman_ops.releases.artifacts.identify_clean_inputs", lambda _repo: CleanInputs(
        **{**inputs.__dict__, "source_revision": "b" * 40}
    ))
    assert clean_inputs_match(tmp_path / "repo", inputs) is False


def test_target_resolution_requires_preidentified_clean_inputs_for_automatic_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A controller must not infer a replacement checkout identity after discovery."""
    _clean_checkout(monkeypatch)
    with pytest.raises(OpsError) as raised:
        resolve_deploy_target(tmp_path / "repo", None, installed_records=(), selected_release_id=None,
                              last_successful_release_id=None, clean_inputs=None)
    assert raised.value.status is ExitStatus.INVALID


def test_target_resolution_prefers_matching_successful_record_after_a_nonmatching_selected_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping successful history after a changed physical selection loses a valid no-upload target."""
    selected = _artifact(tmp_path / "artifacts", payload="selected", revision="b" * 40)
    successful = _artifact(tmp_path / "artifacts", payload="successful")
    _clean_checkout(monkeypatch)
    target = resolve_deploy_target(
        tmp_path / "repo", None, installed_records=(_record(selected), _record(successful)),
        selected_release_id=selected.manifest.release_id, last_successful_release_id=successful.manifest.release_id,
        clean_inputs=_inputs(successful), artifact_root=tmp_path / "cache",
    )
    assert target.source == "installed"
    assert target.release_id == successful.manifest.release_id


def test_target_resolution_does_not_match_a_record_when_any_clean_provenance_input_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source-only reuse can select an artifact built with a different builder or layout contract."""
    artifact = _artifact(tmp_path / "artifacts")
    _clean_checkout(monkeypatch)
    inputs = CleanInputs(**{**_inputs(artifact).__dict__, "builder_base_tag": "different-builder"})
    with pytest.raises(OpsError) as raised:
        resolve_deploy_target(
            tmp_path / "repo", None, installed_records=(_record(artifact),),
            selected_release_id=artifact.manifest.release_id, last_successful_release_id=None,
            clean_inputs=inputs, artifact_root=tmp_path / "cache", builder=lambda _repo, _root: artifact,
        )
    assert raised.value.status is ExitStatus.INVALID


def test_invalid_cache_is_preserved_while_a_fresh_target_is_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting rejected cache bytes destroys operator evidence and can hide a malformed artifact."""
    valid = _artifact(tmp_path / "built")
    cache = tmp_path / "cache"
    invalid = cache / "broken"
    invalid.mkdir(mode=0o700, parents=True)
    cache.chmod(0o700)
    (invalid / "taskman-broken.tar.gz").write_bytes(b"not an archive")
    (invalid / "taskman-broken.manifest.json").write_text("{}", encoding="utf-8")
    (invalid / "taskman-broken.tar.gz.sha256").write_text("0" * 64 + "  taskman-broken.tar.gz\n", encoding="ascii")
    _clean_checkout(monkeypatch)
    target = resolve_deploy_target(
        tmp_path / "repo", None, installed_records=(), selected_release_id=None, last_successful_release_id=None,
        clean_inputs=_inputs(valid), artifact_root=cache, builder=lambda _repo, _root: valid,
    )
    assert target.source == "built"
    assert invalid.exists()


@pytest.mark.parametrize(
    ("field", "changed_value"),
    (
        ("source_revision", "b" * 40),
        ("application_version", "0.2.1"),
        ("target_os", "different-target-os"),
        ("architecture", "different-architecture"),
        ("otp_version", "0.0.0"),
        ("elixir_version", "0.0.0"),
        ("node_version", "0.0.0"),
        ("hex_version", "0.0.0"),
        ("rebar3_version", "0.0.0"),
        ("builder_base_tag", "different-builder"),
        ("builder_base_digest", "sha256:" + "0" * 64),
        ("top_level", "different-top-level"),
        ("migrations", (MigrationFingerprint("20260101000000_changed.exs", "0" * 64),)),
    ),
)
def test_clean_inputs_match_rejects_each_identity_component_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, changed_value: object
) -> None:
    """Each captured source/build identity field must invalidate a stale deployment plan."""
    artifact = _artifact(tmp_path / "artifact")
    inputs = _inputs(artifact)
    changed = CleanInputs(**{**inputs.__dict__, field: changed_value})
    monkeypatch.setattr("taskman_ops.releases.artifacts.identify_clean_inputs", lambda _repo: changed)
    assert clean_inputs_match(tmp_path / "repo", inputs) is False


def test_clean_inputs_match_accepts_unchanged_inputs_and_refuses_newly_dirty_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan remains valid only while its source is clean and exactly unchanged."""
    artifact = _artifact(tmp_path / "artifact")
    inputs = _inputs(artifact)
    monkeypatch.setattr("taskman_ops.releases.artifacts.identify_clean_inputs", lambda _repo: inputs)
    assert clean_inputs_match(tmp_path / "repo", inputs) is True
    dirty = OpsError(ExitStatus.LOCAL_PREREQUISITE, "artifact", "checkout is dirty")
    monkeypatch.setattr("taskman_ops.releases.artifacts.identify_clean_inputs", lambda _repo: (_ for _ in ()).throw(dirty))
    assert clean_inputs_match(tmp_path / "repo", inputs) is False


def test_identify_clean_inputs_preserves_invalid_input_contract_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Downgrading a bounds refusal to a build prerequisite assigns the wrong operator recovery path."""
    from taskman_ops.releases.artifacts import identify_clean_inputs
    from taskman_ops.releases.build import SourceState
    monkeypatch.setattr("taskman_ops.releases.artifacts.read_repository_state", lambda _repo: SourceState(REVISION, True))
    expected = OpsError(ExitStatus.INVALID, "artifact", "too many migrations")
    monkeypatch.setattr("taskman_ops.releases.artifacts.fingerprint_migrations", lambda _path: (_ for _ in ()).throw(expected))
    with pytest.raises(OpsError) as raised:
        identify_clean_inputs(tmp_path)
    assert raised.value.status is ExitStatus.INVALID
