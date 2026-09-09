from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import stat
import tarfile

import pytest

from taskman_ops.releases.artifacts import ArtifactResolution, resolve_deploy_artifact
from taskman_ops.releases.build import SourceState
from taskman_ops.cli import Invocation, dispatch
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.releases.manifests import (
    ArtifactManifest,
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    VerifiedArtifact,
    manifest_to_json,
)
from taskman_ops.releases.identifiers import build_release_id


REVISION = "a" * 40
OTHER_REVISION = "b" * 40
VERSION = "0.2.0"
OTHER_VERSION = "0.2.1"


def _manifest(
    *,
    revision: str = REVISION,
    version: str = VERSION,
    built_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
) -> ArtifactManifest:
    return ArtifactManifest(
        2,
        "taskman",
        version,
        revision,
        build_release_id(version, revision),
        built_at,
        "ubuntu26.04",
        "amd64",
        "29.0.6",
        "1.20.4",
        "22.22.1",
        BUILDER_BASE_TAG,
        BUILDER_BASE_DIGEST,
        (),
        "taskman",
    )


def _write_release_archive(path: Path) -> None:
    source = path.parent / "release-tree"
    for directory in ("bin", "lib", "releases", "erts-16.0"):
        (source / "taskman" / directory).mkdir(parents=True, exist_ok=True)
    for launcher in ("taskman", "server", "migrate", "create-admin"):
        file = source / "taskman" / "bin" / launcher
        file.write_text("#!/bin/sh\n", encoding="utf-8")
        file.chmod(0o755)
    with tarfile.open(path, "w:gz") as archive:
        archive.add(source / "taskman", arcname="taskman")


def _write_artifact(
    root: Path,
    *,
    revision: str = REVISION,
    version: str = VERSION,
    built_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    directory_name: str | None = None,
) -> VerifiedArtifact:
    manifest = _manifest(revision=revision, version=version, built_at=built_at)
    if not root.exists():
        root.mkdir(mode=0o700, parents=True)
    else:
        root.chmod(0o700)
    directory = root / (directory_name or f"{manifest.release_id}-cached")
    directory.mkdir(parents=True)
    archive = directory / f"taskman-{manifest.release_id}.tar.gz"
    _write_release_archive(archive)
    manifest_path = directory / f"taskman-{manifest.release_id}.manifest.json"
    manifest_path.write_text(manifest_to_json(manifest), encoding="utf-8")
    checksum = directory / f"taskman-{manifest.release_id}.tar.gz.sha256"
    checksum.write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="ascii",
    )
    return VerifiedArtifact(archive, manifest_path, checksum, hashlib.sha256(archive.read_bytes()).hexdigest(), manifest)


def _write_manifest_override(path: Path, manifest: ArtifactManifest, **overrides: object) -> None:
    mapping = manifest.to_mapping()
    mapping.update(overrides)
    path.write_text(json.dumps(mapping, sort_keys=True), encoding="utf-8")


def _repository(repo: Path) -> None:
    repo.mkdir()
    (repo / "mix.exs").write_text(
        'def project do\n  [app: :taskman, version: "0.2.0"]\nend\n', encoding="utf-8"
    )


def _identified_checkout(monkeypatch: pytest.MonkeyPatch, revision: str = REVISION, *, clean: bool = True) -> None:
    monkeypatch.setattr(
        "taskman_ops.releases.artifacts.read_repository_state",
        lambda _repo: SourceState(revision=revision, clean=clean),
    )


def test_implicit_resolution_reuses_verified_exact_input_without_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    cached = _write_artifact(artifact_root)
    _identified_checkout(monkeypatch)

    def unexpected_builder(_repo: Path, _root: Path) -> VerifiedArtifact:
        raise AssertionError("exact cached input should be reused")

    resolution = resolve_deploy_artifact(
        repo,
        None,
        artifact_root=artifact_root,
        builder=unexpected_builder,
    )

    assert isinstance(resolution, ArtifactResolution)
    assert resolution.source == "cached"
    assert resolution.artifact.archive == cached.archive
    assert resolution.artifact.manifest.source_revision == REVISION


def test_implicit_resolution_builds_after_cache_miss_and_shares_artifact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    _identified_checkout(monkeypatch)
    seen: list[tuple[Path, Path]] = []

    def builder(actual_repo: Path, actual_root: Path) -> VerifiedArtifact:
        seen.append((actual_repo, actual_root))
        return _write_artifact(actual_root, directory_name="built")

    resolution = resolve_deploy_artifact(repo, None, artifact_root=artifact_root, builder=builder)

    assert resolution.source == "built"
    assert seen == [(repo, artifact_root)]


@pytest.mark.parametrize(
    ("revision", "version"),
    [(OTHER_REVISION, VERSION), (REVISION, OTHER_VERSION)],
)
def test_implicit_resolution_builds_when_cached_source_inputs_do_not_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str,
    version: str,
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    _write_artifact(artifact_root, revision=revision, version=version)
    _identified_checkout(monkeypatch)
    built = _write_artifact(tmp_path / "built", directory_name="replacement")
    calls: list[tuple[Path, Path]] = []

    def builder(actual_repo: Path, actual_root: Path) -> VerifiedArtifact:
        calls.append((actual_repo, actual_root))
        return built

    resolution = resolve_deploy_artifact(repo, None, artifact_root=artifact_root, builder=builder)

    assert resolution.source == "built"
    assert resolution.artifact is built
    assert calls == [(repo, artifact_root)]


def test_invalid_cache_candidates_are_ignored_without_being_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    invalid = artifact_root / "invalid"
    invalid.mkdir(mode=0o700, parents=True)
    archive = invalid / "taskman-broken.tar.gz"
    archive.write_bytes(b"not-a-tar")
    (invalid / "taskman-broken.manifest.json").write_text(
        manifest_to_json(_manifest()), encoding="utf-8"
    )
    (invalid / "taskman-broken.tar.gz.sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="ascii",
    )
    valid = _write_artifact(artifact_root)
    _identified_checkout(monkeypatch)

    resolution = resolve_deploy_artifact(
        repo,
        None,
        artifact_root=artifact_root,
        builder=lambda *_args: pytest.fail("must not build when a valid cache entry follows corruption"),
    )

    assert resolution.source == "cached"
    assert resolution.artifact.archive == valid.archive
    assert invalid.exists()


def test_old_verified_artifacts_remain_reusable_regardless_of_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    cached = _write_artifact(artifact_root, built_at=datetime(1970, 1, 1, tzinfo=UTC))
    _identified_checkout(monkeypatch)

    resolution = resolve_deploy_artifact(
        repo,
        None,
        artifact_root=artifact_root,
        builder=lambda *_args: pytest.fail("artifact age must not force a build"),
    )

    assert resolution.source == "cached"
    assert resolution.artifact.archive == cached.archive


def test_explicit_artifact_is_authoritative_over_cached_artifact_and_checkout_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    _write_artifact(artifact_root)
    supplied_root = tmp_path / "supplied"
    supplied = _write_artifact(supplied_root, revision=OTHER_REVISION, directory_name="explicit")
    monkeypatch.setattr(
        "taskman_ops.releases.artifacts.read_repository_state",
        lambda _repo: pytest.fail("explicit artifacts must not inspect the checkout"),
    )

    resolution = resolve_deploy_artifact(repo, supplied.archive, artifact_root=artifact_root)

    assert resolution.source == "explicit"
    assert resolution.artifact.archive == supplied.archive


@pytest.mark.parametrize("state", [SourceState(revision=REVISION, clean=False), SourceState(revision=None, clean=True)])
def test_implicit_resolution_refuses_unclean_or_unidentified_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: SourceState
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    monkeypatch.setattr("taskman_ops.releases.artifacts.read_repository_state", lambda _repo: state)
    built = False

    def builder(_repo: Path, _root: Path) -> VerifiedArtifact:
        nonlocal built
        built = True
        raise AssertionError("invalid checkout must not build")

    with pytest.raises(OpsError) as raised:
        resolve_deploy_artifact(repo, None, artifact_root=tmp_path / "artifacts", builder=builder)

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE
    assert built is False


def test_nested_artifact_directories_are_not_considered_managed_cache_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    nested_root = artifact_root / "nested"
    nested_root.mkdir(mode=0o700, parents=True)
    artifact_root.chmod(0o700)
    nested = _write_artifact(nested_root)
    _identified_checkout(monkeypatch)
    built = _write_artifact(tmp_path / "built", directory_name="replacement")

    resolution = resolve_deploy_artifact(
        repo,
        None,
        artifact_root=artifact_root,
        builder=lambda *_args: built,
    )

    assert resolution.source == "built"
    assert resolution.artifact.archive == built.archive
    assert nested.archive.exists()


@pytest.mark.parametrize(
    "field_value",
    [
        {"target_os": "ubuntu24.04"},
        {"otp_version": "27.3.4.5"},
        {"builder_base_digest": "sha256:" + "0" * 64},
    ],
)
def test_verified_cache_candidates_with_pinned_identity_mismatches_trigger_a_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_value: dict[str, str],
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    cached = _write_artifact(artifact_root, directory_name="mismatched")
    _write_manifest_override(cached.manifest_path, cached.manifest, **field_value)
    _identified_checkout(monkeypatch)
    built = _write_artifact(tmp_path / "built", directory_name="replacement")

    resolution = resolve_deploy_artifact(
        repo,
        None,
        artifact_root=artifact_root,
        builder=lambda *_args: built,
    )

    assert resolution.source == "built"
    assert resolution.artifact is built
    assert cached.archive.exists()


@pytest.mark.parametrize("mode", [0o755, 0o750])
def test_implicit_resolution_rejects_a_cache_root_with_nonrestrictive_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    _write_artifact(artifact_root)
    artifact_root.chmod(mode)
    _identified_checkout(monkeypatch)

    with pytest.raises(OpsError) as raised:
        resolve_deploy_artifact(
            repo,
            None,
            artifact_root=artifact_root,
            builder=lambda *_args: pytest.fail("insecure cache root must not build"),
        )

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE


def test_implicit_resolution_rejects_a_symlinked_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    target = tmp_path / "target"
    _write_artifact(target)
    artifact_root = tmp_path / "artifacts"
    artifact_root.symlink_to(target, target_is_directory=True)
    _identified_checkout(monkeypatch)

    with pytest.raises(OpsError) as raised:
        resolve_deploy_artifact(
            repo,
            None,
            artifact_root=artifact_root,
            builder=lambda *_args: pytest.fail("symlinked cache root must not build"),
        )

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE


def test_implicit_resolution_rejects_a_cache_root_not_owned_by_the_controller_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    _write_artifact(artifact_root)
    monkeypatch.setattr("taskman_ops.releases.build.os.getuid", lambda: 2**31)
    _identified_checkout(monkeypatch)

    with pytest.raises(OpsError) as raised:
        resolve_deploy_artifact(
            repo,
            None,
            artifact_root=artifact_root,
            builder=lambda *_args: pytest.fail("foreign cache root must not build"),
        )

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE


def test_build_rejects_an_existing_insecure_artifact_root_before_exporting_source(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o755)

    def unexpected_export(*_args: object) -> None:
        pytest.fail("source export must wait for secure artifact-root validation")

    with pytest.raises(OpsError) as raised:
        from taskman_ops.releases.build import build_release

        build_release(
            repo,
            artifact_root,
            source_reader=lambda _repo: SourceState(revision=REVISION, clean=True),
            source_exporter=unexpected_export,
        )

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE
    assert stat.S_IMODE(artifact_root.stat().st_mode) == 0o755


def test_build_output_is_discoverable_by_implicit_resolution_at_the_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repository(repo)
    artifact_root = tmp_path / "shared-artifacts"
    monkeypatch.setattr("taskman_ops.releases.build.default_artifact_root", lambda: artifact_root)
    monkeypatch.setattr("taskman_ops.releases.artifacts.default_artifact_root", lambda: artifact_root)
    _identified_checkout(monkeypatch)
    built = _write_artifact(tmp_path / "built", directory_name="replacement")

    def build_for_public_command(_repo: Path, output: Path) -> VerifiedArtifact:
        cached = _write_artifact(output)
        return cached

    monkeypatch.setattr("taskman_ops.releases.build.build_release", build_for_public_command)
    build_result = dispatch(Invocation(command="build"))

    resolution = resolve_deploy_artifact(
        repo,
        None,
        builder=lambda *_args: built,
    )

    assert build_result.stage == "built"
    assert resolution.source == "cached"
    assert resolution.artifact.manifest.source_revision == REVISION
