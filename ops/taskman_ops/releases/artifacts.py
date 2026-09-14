"""Resolve immutable deployment targets without contacting a host."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..errors import ExitStatus, OpsError
from ..host_helper.records import ReleaseRecord
from .build import build_release, default_artifact_root, ensure_artifact_root, read_application_version, read_repository_state
from .identifiers import validate_release_id, validate_source_revision
from .manifests import (
    ARCHITECTURE, BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, ELIXIR_VERSION, HEX_VERSION,
    NODE_VERSION, OTP_VERSION, REBAR3_VERSION, TARGET_OS, TOP_LEVEL, ArtifactManifest,
    MigrationFingerprint, VerifiedArtifact, fingerprint_migrations, verify_artifact,
)

DeploymentSource = Literal["explicit", "installed", "cached", "built"]
ArtifactBuilder = Callable[[Path, Path], VerifiedArtifact]
_CLEAN_INPUT_DRIFT_MESSAGE = "source inputs changed before the fresh build completed"


@dataclass(frozen=True)
class CleanInputs:
    """The clean source identity captured before read-only host discovery."""

    source_revision: str
    application_version: str
    target_os: str
    architecture: str
    otp_version: str
    elixir_version: str
    node_version: str
    hex_version: str
    rebar3_version: str
    builder_base_tag: str
    builder_base_digest: str
    top_level: str
    migrations: tuple[MigrationFingerprint, ...]


@dataclass(frozen=True)
class DeploymentTarget:
    """One validated archive or one validated installed release record."""

    artifact: VerifiedArtifact | None
    release_record: ReleaseRecord | None
    source: DeploymentSource

    def __post_init__(self) -> None:
        if self.source not in {"explicit", "installed", "cached", "built"}:
            raise ValueError("invalid deployment target source")
        if (self.artifact is None) == (self.release_record is None):
            raise ValueError("deployment target needs exactly one representation")
        if self.artifact is not None and not isinstance(self.artifact, VerifiedArtifact):
            raise TypeError("deployment target artifact must be verified")
        if self.release_record is not None and not isinstance(self.release_record, ReleaseRecord):
            raise TypeError("deployment target release record must be validated")
        if self.source == "installed" and self.release_record is None:
            raise ValueError("installed target needs an installed release record")
        if self.source != "installed" and self.artifact is None:
            raise ValueError("non-installed target needs an archive")

    @property
    def manifest(self) -> ArtifactManifest:
        if self.artifact is not None:
            return self.artifact.manifest
        assert self.release_record is not None
        return self.release_record.artifact_manifest

    @property
    def release_id(self) -> str:
        return self.manifest.release_id

    @property
    def artifact_sha256(self) -> str:
        return self.manifest.artifact_sha256

    @property
    def source_revision(self) -> str:
        return self.manifest.source_revision

    @property
    def source_dirty(self) -> bool:
        return self.manifest.source_dirty


def _artifact_root(value: Path | None) -> Path:
    return Path(value) if value is not None else default_artifact_root()


def _resolution_error(message: str, *, status: ExitStatus = ExitStatus.LOCAL_PREREQUISITE) -> OpsError:
    return OpsError(status, "artifact", message, changed=False, next_action="use a verified release artifact or resolve the local source inputs")


def _regular_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _candidate_triplets(root: Path) -> Iterator[tuple[Path, Path, Path]]:
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
            if archive.is_symlink() or not archive.is_file() or not archive.name.startswith("taskman-") or not archive.name.endswith(".tar.gz"):
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
            continue


def _explicit_artifact(supplied: Path) -> DeploymentTarget:
    archive = Path(supplied)
    if not archive.name.endswith(".tar.gz"):
        raise ValueError("deploy artifact must be a release archive")
    stem = archive.name[: -len(".tar.gz")]
    artifact = verify_artifact(archive, archive.with_name(f"{stem}.manifest.json"), archive.with_name(f"{archive.name}.sha256"))
    return DeploymentTarget(artifact=artifact, release_record=None, source="explicit")


def identify_clean_inputs(repo: Path) -> CleanInputs:
    """Read the exact clean source identity before discovery begins."""

    repo = Path(repo)
    state = read_repository_state(repo)
    if not state.clean or state.revision is None:
        raise _resolution_error("source checkout must be clean and identified")
    try:
        revision = validate_source_revision(state.revision)
    except ValueError:
        raise _resolution_error("source checkout has invalid release inputs") from None
    try:
        migrations = fingerprint_migrations(repo / "priv" / "repo" / "migrations")
    except OpsError as error:
        if error.status is ExitStatus.INVALID:
            raise
        raise _resolution_error("source checkout has invalid release inputs") from None
    return CleanInputs(
        revision, read_application_version(repo / "mix.exs"), TARGET_OS, ARCHITECTURE, OTP_VERSION,
        ELIXIR_VERSION, NODE_VERSION, HEX_VERSION, REBAR3_VERSION, BUILDER_BASE_TAG,
        BUILDER_BASE_DIGEST, TOP_LEVEL, migrations,
    )


def clean_inputs_match(repo: Path, clean_inputs: CleanInputs) -> bool:
    if not isinstance(clean_inputs, CleanInputs):
        raise TypeError("clean inputs must be identified inputs")
    try:
        return identify_clean_inputs(repo) == clean_inputs
    except OpsError:
        return False


def clean_inputs_drifted(error: OpsError) -> bool:
    """Recognize the one retryable mismatch raised during frozen clean resolution."""

    return (
        error.status is ExitStatus.INVALID
        and error.stage == "artifact"
        and error.message == _CLEAN_INPUT_DRIFT_MESSAGE
        and not error.changed
    )


def _matches_clean_inputs(manifest: ArtifactManifest, inputs: CleanInputs) -> bool:
    return (
        not manifest.source_dirty and manifest.source_revision == inputs.source_revision
        and manifest.application_version == inputs.application_version and manifest.target_os == inputs.target_os
        and manifest.architecture == inputs.architecture and manifest.otp_version == inputs.otp_version
        and manifest.elixir_version == inputs.elixir_version and manifest.node_version == inputs.node_version
        and manifest.hex_version == inputs.hex_version and manifest.rebar3_version == inputs.rebar3_version
        and manifest.builder_base_tag == inputs.builder_base_tag and manifest.builder_base_digest == inputs.builder_base_digest
        and manifest.top_level == inputs.top_level and manifest.migrations == inputs.migrations
    )


def _validated_records(records: Sequence[ReleaseRecord], selected: str | None, successful: str | None) -> dict[str, ReleaseRecord]:
    if not isinstance(records, (tuple, list)):
        raise _resolution_error("installed release authority is invalid", status=ExitStatus.SAFETY)
    parsed: dict[str, ReleaseRecord] = {}
    for record in records:
        if not isinstance(record, ReleaseRecord) or record.release_id in parsed:
            raise _resolution_error("installed release authority is invalid", status=ExitStatus.SAFETY)
        parsed[record.release_id] = record
    for value in (selected, successful):
        if value is None:
            continue
        try:
            release_id = validate_release_id(value)
        except ValueError:
            raise _resolution_error("installed release authority is invalid", status=ExitStatus.SAFETY) from None
        if release_id not in parsed:
            raise _resolution_error("installed release authority is incomplete", status=ExitStatus.SAFETY)
    return parsed


def _installed_target(record: ReleaseRecord) -> DeploymentTarget:
    return DeploymentTarget(artifact=None, release_record=record, source="installed")


def _first_matching_installed(records: dict[str, ReleaseRecord], identities: Sequence[str | None], inputs: CleanInputs) -> DeploymentTarget | None:
    for identity in identities:
        if identity is not None:
            record = records[identity]
            if _matches_clean_inputs(record.artifact_manifest, inputs):
                return _installed_target(record)
    for release_id in sorted(records):
        record = records[release_id]
        if _matches_clean_inputs(record.artifact_manifest, inputs):
            return _installed_target(record)
    return None


def _record_matches_artifact(record: ReleaseRecord, artifact: VerifiedArtifact) -> bool:
    manifest = artifact.manifest
    return record.release_id == manifest.release_id and record.artifact_sha256 == manifest.artifact_sha256 and record.artifact_manifest == manifest


def resolve_deploy_target(
    repo: Path, supplied: Path | None, *, installed_records: Sequence[ReleaseRecord],
    selected_release_id: str | None, last_successful_release_id: str | None,
    allow_dirty: bool = False, artifact_root: Path | None = None,
    clean_inputs: CleanInputs | None = None, builder: ArtifactBuilder = build_release,
) -> DeploymentTarget:
    """Resolve a target using validated host records only; this performs no SSH."""

    records = _validated_records(installed_records, selected_release_id, last_successful_release_id)
    if supplied is not None:
        return _explicit_artifact(Path(supplied))
    repo = Path(repo)
    state = read_repository_state(repo)
    if state.revision is None:
        raise _resolution_error("source checkout must be identified")
    if not state.clean:
        if not allow_dirty:
            raise _resolution_error("source checkout must be clean and identified")
        root = ensure_artifact_root(_artifact_root(artifact_root))
        artifact = builder(repo, root) if builder is not build_release else build_release(repo, root, allow_dirty=True)
        for record in records.values():
            if _record_matches_artifact(record, artifact):
                return _installed_target(record)
        return DeploymentTarget(artifact=artifact, release_record=None, source="built")
    if clean_inputs is None:
        raise _resolution_error("automatic clean resolution needs preidentified clean inputs", status=ExitStatus.INVALID)
    if not isinstance(clean_inputs, CleanInputs):
        raise TypeError("clean inputs must be identified inputs")
    installed = _first_matching_installed(records, (selected_release_id, last_successful_release_id), clean_inputs)
    if installed is not None:
        return installed
    root = ensure_artifact_root(_artifact_root(artifact_root))
    for candidate in sorted(_verified_candidates(root), key=lambda item: (item.manifest.release_id, str(item.archive))):
        if _matches_clean_inputs(candidate.manifest, clean_inputs):
            return DeploymentTarget(artifact=candidate, release_record=None, source="cached")
    artifact = builder(repo, root)
    if not _matches_clean_inputs(artifact.manifest, clean_inputs):
        raise _resolution_error(_CLEAN_INPUT_DRIFT_MESSAGE, status=ExitStatus.INVALID)
    return DeploymentTarget(artifact=artifact, release_record=None, source="built")


__all__ = ["CleanInputs", "DeploymentSource", "DeploymentTarget", "clean_inputs_match", "identify_clean_inputs", "resolve_deploy_target"]
