"""Read-only release discovery based only on validated lifecycle metadata."""

from __future__ import annotations

from collections.abc import Mapping

from ..errors import ExitStatus, OpsError
from ..manifests import ARCHITECTURE, OTP_VERSION, TARGET_OS, ArtifactManifest
from ..releases.records import LifecycleRecords, RemoteLifecycleStore, rollback_eligibility
from . import DiscoveryResult


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "release-discovery",
        message,
        changed=False,
        next_action="resolve the managed lifecycle metadata before selecting a release",
    )


def list_releases(
    store: RemoteLifecycleStore,
    *,
    lock_timeout_seconds: float = 5,
) -> DiscoveryResult:
    """List release state from one remote, shared-lock host snapshot."""

    if not isinstance(store, RemoteLifecycleStore):
        raise TypeError("release discovery requires a remote lifecycle store")
    records, snapshot = store.read(operation="releases", lock_timeout_seconds=lock_timeout_seconds)
    manifest_by_release = _validate_manifests(records, snapshot["manifests"])
    activation_by_release = _latest_activations(records)
    current = records.current_release_id
    previous = records.activations[-1].previous_release_id if records.activations else None
    adoptions = {record.release_id: record for record in records.adoptions}
    rows: list[dict[str, object]] = []
    for release in records.releases:
        activation = activation_by_release.get(release.release_id)
        adoption = adoptions.get(release.release_id)
        manifest = manifest_by_release.get(release.release_id)
        if adoption is not None:
            provenance: dict[str, object] = {
                "application_version": adoption.application_version,
                "source_revision": "unknown",
                "target_os": TARGET_OS,
                "architecture": ARCHITECTURE,
                "otp_version": OTP_VERSION,
                "hex_version": "unknown",
                "rebar3_version": "unknown",
                "artifact_sha256": "unknown",
                "artifact_sha256_prefix": "unknown",
            }
        elif manifest is not None:
            checksum = release.artifact_sha256
            provenance = {
                "application_version": manifest.application_version,
                "source_revision": manifest.source_revision,
                "target_os": manifest.target_os,
                "architecture": manifest.architecture,
                "otp_version": manifest.otp_version,
                "hex_version": manifest.hex_version,
                "rebar3_version": manifest.rebar3_version,
                "artifact_sha256": checksum,
                "artifact_sha256_prefix": checksum[:12] if checksum is not None else "unknown",
            }
        else:  # pragma: no cover - _validate_manifests rejects this branch
            raise _safety("installed release manifest is unavailable")
        eligible, reason = _rollback_fields(records, current, release.release_id)
        rows.append(
            {
                "release_id": release.release_id,
                "status": _status(release.release_id, current, previous),
                **provenance,
                "installed_at": release.installed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "activated_at": (
                    activation.activated_at.isoformat(timespec="seconds").replace("+00:00", "Z")
                    if activation is not None
                    else None
                ),
                "incoming_migration_policy": activation.migration_policy if activation is not None else None,
                "rollback_eligible": eligible,
                "rollback_reason": reason,
            }
        )
    rows.sort(key=lambda row: (row["activated_at"] is not None, row["activated_at"] or "", row["release_id"]), reverse=True)
    return DiscoveryResult(tuple(rows), records.warnings)


def _validate_manifests(
    records: LifecycleRecords,
    manifests: object,
) -> dict[str, ArtifactManifest]:
    if not isinstance(manifests, Mapping) or not all(isinstance(key, str) for key in manifests):
        raise _safety("remote installed manifests are invalid")
    result: dict[str, ArtifactManifest] = {}
    adopted = {record.release_id for record in records.adoptions}
    for release in records.releases:
        if release.release_id in adopted:
            continue
        raw_manifest = manifests.get(release.release_id)
        try:
            manifest = ArtifactManifest.from_mapping(raw_manifest)
        except ValueError:
            raise _safety("installed release manifest is unavailable or inconsistent")
        if manifest.release_id != release.release_id:
            raise _safety("installed release manifest is unavailable or inconsistent")
        result[release.release_id] = manifest
    for release_id, raw_manifest in manifests.items():
        try:
            manifest = ArtifactManifest.from_mapping(raw_manifest)
        except ValueError:
            raise _safety("remote installed manifests are invalid") from None
        if release_id not in {record.release_id for record in records.releases} or manifest.release_id != release_id:
            raise _safety("installed manifest has no matching lifecycle record")
    return result


def _latest_activations(records: LifecycleRecords) -> dict[str, object]:
    latest: dict[str, object] = {}
    for activation in records.activations:
        latest[activation.candidate_release_id] = activation
    return latest


def _status(release_id: str, current: str | None, previous: str | None) -> str:
    if release_id == current:
        return "current"
    if release_id == previous:
        return "previous"
    return "inactive"


def _rollback_fields(records: LifecycleRecords, current: str | None, target: str) -> tuple[bool, str | None]:
    if current is None:
        return False, "no current release is recorded"
    return rollback_eligibility(records, current_release_id=current, target_release_id=target)


__all__ = ["list_releases"]
