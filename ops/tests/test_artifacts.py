from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import tarfile

import pytest

from taskman_ops.artifacts import ArtifactResolution, resolve_deploy_artifact
from taskman_ops.build import SourceState
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.manifests import (
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
        "27.3.4.6",
        "1.18.3",
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


def _repository(repo: Path) -> None:
    repo.mkdir()
    (repo / "mix.exs").write_text(
        'def project do\n  [app: :taskman, version: "0.2.0"]\nend\n', encoding="utf-8"
    )


def _identified_checkout(monkeypatch: pytest.MonkeyPatch, revision: str = REVISION, *, clean: bool = True) -> None:
    monkeypatch.setattr(
        "taskman_ops.artifacts.read_repository_state",
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
    invalid.mkdir(parents=True)
    (invalid / "taskman-broken.tar.gz").write_bytes(b"not-a-tar")
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
        "taskman_ops.artifacts.read_repository_state",
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
    monkeypatch.setattr("taskman_ops.artifacts.read_repository_state", lambda _repo: state)
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
    nested_root.mkdir(parents=True)
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
