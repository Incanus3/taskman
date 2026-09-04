"""Resolve verified local release artifacts for deployment."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .build import (
    build_release,
    default_artifact_root,
    ensure_artifact_root,
    read_application_version,
    read_repository_state,
)
from ..errors import ExitStatus, OpsError
from .manifests import VerifiedArtifact, verify_artifact
from .identifiers import build_release_id, validate_source_revision


ArtifactSource = Literal["explicit", "cached", "built"]
ArtifactBuilder = Callable[[Path, Path], VerifiedArtifact]


@dataclass(frozen=True)
class ArtifactResolution:
    """The verified artifact selected for deployment and its provenance."""

    artifact: VerifiedArtifact
    source: ArtifactSource


def _artifact_root(value: Path | None) -> Path:
    if value is not None:
        return Path(value)
    return default_artifact_root()


def _resolution_error(message: str) -> OpsError:
    return OpsError(
        status=ExitStatus.LOCAL_PREREQUISITE,
        stage="artifact",
        message=message,
        changed=False,
        next_action="use a clean identified checkout or provide a verified release artifact",
    )


def _regular_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _candidate_triplets(root: Path) -> Iterator[tuple[Path, Path, Path]]:
    """Yield complete artifact triplets from direct managed directories only."""

    try:
        directories = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return
    for directory in directories:
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            entries = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError:
            continue
        for archive in entries:
            if (
                archive.is_symlink()
                or not archive.is_file()
                or not archive.name.startswith("taskman-")
                or not archive.name.endswith(".tar.gz")
            ):
                continue
            stem = archive.name[: -len(".tar.gz")]
            manifest = directory / f"{stem}.manifest.json"
            checksum = directory / f"{archive.name}.sha256"
            if _regular_file(manifest) and _regular_file(checksum):
                yield archive, manifest, checksum


def _verified_candidates(root: Path) -> Iterator[VerifiedArtifact]:
    for archive, manifest, checksum in _candidate_triplets(root):
        try:
            yield verify_artifact(archive, manifest, checksum)
        except (OpsError, OSError, ValueError):
            # A stale, incomplete, or corrupt cache entry is not evidence that
            # resolution should fail. Leave it available for inspection and
            # continue looking for another deterministic candidate.
            continue


def _explicit_artifact(supplied: Path) -> ArtifactResolution:
    archive = Path(supplied)
    if not archive.name.endswith(".tar.gz"):
        raise ValueError("deploy artifact must be a release archive")
    stem = archive.name[: -len(".tar.gz")]
    artifact = verify_artifact(
        archive,
        archive.with_name(f"{stem}.manifest.json"),
        archive.with_name(f"{archive.name}.sha256"),
    )
    return ArtifactResolution(artifact=artifact, source="explicit")


def resolve_deploy_artifact(
    repo: Path,
    supplied: Path | None,
    *,
    artifact_root: Path | None = None,
    builder: ArtifactBuilder = build_release,
) -> ArtifactResolution:
    """Select an explicit artifact, an exact local cache hit, or a fresh build.

    Explicit artifacts remain authoritative and are verified without reading
    the checkout. Implicit resolution requires a clean, identified checkout,
    then reuses only a verified candidate with the exact current source and
    application inputs. Cache entries that fail verification are ignored.
    """

    if supplied is not None:
        return _explicit_artifact(Path(supplied))

    repo = Path(repo)
    state = read_repository_state(repo)
    if not state.clean or state.revision is None:
        raise _resolution_error("source checkout must be clean and identified")
    try:
        revision = validate_source_revision(state.revision)
    except ValueError:
        raise _resolution_error("source checkout must be clean and identified") from None
    application_version = read_application_version(repo / "mix.exs")
    release_id = build_release_id(application_version, revision)
    root = ensure_artifact_root(_artifact_root(artifact_root))

    for candidate in _verified_candidates(root):
        manifest = candidate.manifest
        if (
            manifest.release_id == release_id
            and manifest.source_revision == revision
            and manifest.application_version == application_version
        ):
            return ArtifactResolution(artifact=candidate, source="cached")

    return ArtifactResolution(artifact=builder(repo, root), source="built")


__all__ = ["ArtifactResolution", "ArtifactSource", "resolve_deploy_artifact"]
