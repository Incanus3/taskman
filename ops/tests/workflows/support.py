"""Shared artifact and verification setup for workflow tests."""

from datetime import UTC, datetime
from pathlib import Path

from taskman_ops.releases.manifests import (
    ArtifactManifest,
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    ELIXIR_VERSION,
    HEX_VERSION,
    MigrationFingerprint,
    NODE_VERSION,
    OTP_VERSION,
    REBAR3_VERSION,
    VerifiedArtifact,
)
from taskman_ops.releases.identifiers import build_release_id


_ARTIFACT_SHA256 = "c" * 64
CANDIDATE = build_release_id("0.2.0", "b" * 40, artifact_sha256=_ARTIFACT_SHA256, source_dirty=False, otp_version=OTP_VERSION)

_CHECKS = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)


def successful_verification_report(release_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "ok",
        "exit_status": 0,
        "release_id": release_id,
        "expected_release_id": release_id,
        "checks": [
            {
                "schema_version": 1,
                "name": name,
                "status": "passed",
                "summary": "passed",
            }
            for name in _CHECKS
        ],
        "next_action": None,
    }


def deployment_artifact(
    tmp_path: Path,
    *,
    migrations: tuple[MigrationFingerprint, ...] = (),
) -> VerifiedArtifact:
    # This models an already-validated artifact; the archive bytes are only a stub.
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"release")
    manifest = ArtifactManifest(
        3,
        "taskman",
        "0.2.0",
        "b" * 40,
        CANDIDATE,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "ubuntu26.04",
        "amd64",
        OTP_VERSION,
        ELIXIR_VERSION,
        NODE_VERSION,
        BUILDER_BASE_TAG,
        BUILDER_BASE_DIGEST,
        migrations,
        "taskman",
        HEX_VERSION,
        REBAR3_VERSION,
        _ARTIFACT_SHA256,
        False,
    )
    return VerifiedArtifact(
        archive,
        tmp_path / "manifest.json",
        tmp_path / "checksum",
        _ARTIFACT_SHA256,
        manifest,
    )
