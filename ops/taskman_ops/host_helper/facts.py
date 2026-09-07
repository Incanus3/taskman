"""Read-only lifecycle fact collection, independent of acceptance policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import stat
import time

from .lifecycle import (
    AdoptionRecord,
    LifecycleError,
    LifecycleLockContention,
    LifecycleRecords,
    LifecycleStore,
    ReleaseRecord,
    rollback_eligibility,
    validate_lifecycle_records,
)
from .paths import ManagedPaths


_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "application",
        "application_version",
        "source_revision",
        "release_id",
        "built_at",
        "target_os",
        "architecture",
        "otp_version",
        "elixir_version",
        "node_version",
        "hex_version",
        "rebar3_version",
        "builder_base_tag",
        "builder_base_digest",
        "migrations",
        "top_level",
    }
)
_MIGRATION_FIELDS = frozenset({"filename", "sha256"})
_APPLICATION_VERSION_RE = re.compile(r"[0-9][0-9A-Za-z.+-]*\Z")
_SOURCE_REVISION_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MIGRATION_FILENAME_RE = re.compile(r"[0-9]{14}_[a-z0-9_]+\.exs\Z")
_MAX_INVENTORY_WARNINGS = 64
_MAX_INVENTORY_ENTRIES = 32
_DEPLOYMENT_DIRECTORIES = frozenset(
    {"releases", "activations", "backups", "adoptions", "adoption-transactions", "manifests", "provisionals"}
)


@dataclass(frozen=True)
class ReleaseProvenance:
    """The stable release-listing fields collected from managed host evidence."""

    application_version: str
    source_revision: str
    target_os: str
    architecture: str
    otp_version: str
    hex_version: str
    rebar3_version: str


@dataclass(frozen=True)
class LifecycleFacts:
    """The coherent local lifecycle snapshot collected from one filesystem view."""

    paths: ManagedPaths
    records: LifecycleRecords
    current_target: str | None
    current_is_symlink: bool
    owner_uid: int
    provenance_by_release: Mapping[str, ReleaseProvenance]
    current_migrations: tuple[Mapping[str, object], ...]
    release_migrations: Mapping[str, tuple[Mapping[str, object], ...]]


@dataclass(frozen=True)
class LifecycleObservation:
    """Bounded filesystem evidence captured beneath one shared lock.

    An observation deliberately does not assert that separately valid record
    files form an acceptable lifecycle.  That distinction lets state-changing
    operations decide their own interrupted-transaction policy without a
    discovery read silently applying it first.
    """

    paths: ManagedPaths
    records: LifecycleRecords
    current_target: str | None
    current_is_symlink: bool
    owner_uid: int
    manifests_by_release: Mapping[str, object]


@dataclass(frozen=True)
class _InventoryEntries:
    paths: tuple[Path, ...]
    total: int


def observe_lifecycle(
    paths: ManagedPaths,
    *,
    owner_uid: int | None = None,
    deadline: float | None = None,
    operation: str = "discovery",
) -> LifecycleObservation:
    """Capture bounded, root-owned lifecycle evidence without accepting it."""

    uid = os.geteuid() if owner_uid is None else owner_uid
    paths.validate_existing(owner_uid=uid)
    store = LifecycleStore(paths, owner_uid=uid)
    with store.shared_snapshot_lock(deadline=_snapshot_deadline(deadline), operation=operation):
        return _observe_lifecycle(store)


def collect_lifecycle_facts(
    paths: ManagedPaths,
    *,
    owner_uid: int | None = None,
    deadline: float | None = None,
    operation: str = "discovery",
) -> LifecycleFacts:
    """Read one observation and apply discovery's lifecycle acceptance rules.

    This module deliberately does not decide whether a manual or staged shape
    is acceptable for a particular operation. That policy is centralized in
    :func:`classify_lifecycle` so future transactional operations can apply
    their own under-lock rules to the same facts.
    """

    uid = os.geteuid() if owner_uid is None else owner_uid
    paths.validate_existing(owner_uid=uid)
    store = LifecycleStore(paths, owner_uid=uid)
    with store.shared_snapshot_lock(deadline=_snapshot_deadline(deadline), operation=operation):
        return accept_lifecycle_observation(_observe_lifecycle(store))


def _snapshot_deadline(deadline: float | None) -> float:
    """Leave the invocation time for non-lock observation and response cleanup."""

    if deadline is None:
        return time.monotonic() + 5.0
    if type(deadline) not in {int, float}:
        raise LifecycleLockContention(None)
    return min(float(deadline), time.monotonic() + 5.0)


def _observe_lifecycle(store: LifecycleStore) -> LifecycleObservation:
    records = store.observe()
    records = replace(records, warnings=_bounded_warnings((*records.warnings, *_inventory(store, records))))
    manifests = _collect_manifests(store, records)
    current_target, current_is_symlink = _observe_current(store)
    return LifecycleObservation(store.paths, records, current_target, current_is_symlink, store.owner_uid, manifests)


def accept_lifecycle_observation(observation: LifecycleObservation) -> LifecycleFacts:
    """Apply discovery's consistency and provenance policy to raw evidence."""

    validate_lifecycle_records(observation.records, observation.paths)
    _validate_current_observation(observation)
    provenance = _collect_release_provenance(observation)
    return LifecycleFacts(
        observation.paths,
        observation.records,
        observation.current_target,
        observation.current_is_symlink,
        observation.owner_uid,
        provenance,
        _current_migrations(observation),
        _release_migrations(observation),
    )


def _observe_current(store: LifecycleStore) -> tuple[str | None, bool]:
    current = store.current_link
    if not current.exists() and not current.is_symlink():
        return None, False
    try:
        details = current.lstat()
        if not stat.S_ISLNK(details.st_mode):
            raise LifecycleError("current selection is not a symlink")
        target = current.resolve(strict=True)
        root = store.release_root.resolve(strict=True)
        target.relative_to(root)
    except LifecycleError:
        raise
    except (OSError, ValueError) as error:
        raise LifecycleError("current selection is invalid") from error
    return target.as_posix(), True


def classify_lifecycle(facts: LifecycleFacts) -> str:
    """Classify coherent facts without converting them into controller policy."""

    records = facts.records
    if records.current_release_id is not None:
        _validate_managed_current(facts)
        return "managed"
    if records.releases or records.activations or records.backups or records.adoptions:
        if facts.current_target is not None:
            raise LifecycleError("current selection exists without an activation record")
        return "staged"
    return "manual" if facts.current_target is not None else "empty"


def lifecycle_mapping(facts: LifecycleFacts, state: str) -> dict[str, object]:
    """Return bounded protocol facts in the established record schema."""

    return {
        "state": state,
        "current_target": facts.current_target,
        # The deployment planner needs the exact accepted current migration
        # set to determine a policy before it asks for confirmation.  This is
        # still helper-collected read-only authority, not a controller probe.
        "current_migrations": [dict(item) for item in facts.current_migrations],
        # Restore planning needs the accepted migration set for the release
        # recorded by a selected backup.  Publish concise rows rather than a
        # release-id-keyed map because protocol object keys are identifiers.
        "release_migrations": [
            {
                "release_id": release_id,
                "migrations": [dict(item) for item in migrations],
            }
            for release_id, migrations in facts.release_migrations.items()
        ],
        "records": {
            "releases": [record.to_mapping() for record in facts.records.releases],
            "activations": [record.to_mapping() for record in facts.records.activations],
            "backups": [record.to_mapping() for record in facts.records.backups],
            "adoptions": [record.to_mapping() for record in facts.records.adoptions],
        },
        "warnings": list(facts.records.warnings),
    }


def release_rows(facts: LifecycleFacts) -> list[dict[str, object]]:
    """Build read-only release rows from already-collected record facts."""

    current = facts.records.current_release_id
    previous = facts.records.activations[-1].previous_release_id if facts.records.activations else None
    latest = {record.candidate_release_id: record for record in facts.records.activations}
    rows: list[dict[str, object]] = []
    for record in facts.records.releases:
        activation = latest.get(record.release_id)
        provenance = facts.provenance_by_release[record.release_id]
        eligible, reason = rollback_eligibility(facts.records, current, record.release_id) if current is not None else (False, "no current release is recorded")
        rows.append(
            {
                "release_id": record.release_id,
                "status": "current" if record.release_id == current else "previous" if record.release_id == previous else "inactive",
                "application_version": provenance.application_version,
                "source_revision": provenance.source_revision,
                "target_os": provenance.target_os,
                "architecture": provenance.architecture,
                "otp_version": provenance.otp_version,
                "hex_version": provenance.hex_version,
                "rebar3_version": provenance.rebar3_version,
                "artifact_sha256": record.artifact_sha256 or "unknown",
                "artifact_sha256_prefix": record.artifact_sha256[:12] if record.artifact_sha256 else "unknown",
                "installed_at": record.installed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "activated_at": activation.activated_at.isoformat(timespec="seconds").replace("+00:00", "Z") if activation else None,
                "incoming_migration_policy": activation.migration_policy if activation else None,
                "rollback_eligible": eligible,
                "rollback_reason": reason,
            }
        )
    return sorted(rows, key=lambda row: (row["activated_at"] is not None, row["activated_at"] or "", row["release_id"]), reverse=True)


def _collect_manifests(store: LifecycleStore, records: LifecycleRecords) -> dict[str, object]:
    """Read manifest bytes as observation; do not accept their semantics yet."""

    adopted = {record.release_id for record in records.adoptions}
    values: dict[str, object] = {}
    for record in records.releases:
        if record.release_id not in adopted:
            path = store.manifest_path(record.release_id)
            # Absence is observable partial state.  An existing unsafe file
            # remains a refusal because it cannot be trusted as host fact.
            values[record.release_id] = store.read_manifest(record.release_id) if path.exists() or path.is_symlink() else None
    return values


def _collect_release_provenance(observation: LifecycleObservation) -> dict[str, ReleaseProvenance]:
    """Validate provenance only after lifecycle relationships are accepted."""

    records = observation.records
    store = LifecycleStore(observation.paths, owner_uid=observation.owner_uid)
    adoptions = {record.release_id: record for record in records.adoptions}
    result: dict[str, ReleaseProvenance] = {}
    for record in records.releases:
        adoption = adoptions.get(record.release_id)
        if adoption is not None:
            store.require_release_directory(Path(adoption.release_path.as_posix()))
            result[record.release_id] = _adoption_provenance(adoption)
        else:
            store.require_release_directory(store.release_path(record.release_id))
            result[record.release_id] = _manifest_provenance(
                observation.manifests_by_release[record.release_id],
                record,
            )
    return result


def _adoption_provenance(record: AdoptionRecord) -> ReleaseProvenance:
    return ReleaseProvenance(
        application_version=record.application_version,
        source_revision="unknown",
        target_os="ubuntu26.04",
        architecture="amd64",
        otp_version="27.3.4.6",
        hex_version="unknown",
        rebar3_version="unknown",
    )


def _manifest_provenance(value: object, release: ReleaseRecord) -> ReleaseProvenance:
    if not isinstance(value, Mapping) or set(value) != _MANIFEST_FIELDS or not all(type(key) is str for key in value):
        raise LifecycleError("installed release manifest is invalid")
    if (
        value["schema_version"] != 2
        or value["application"] != "taskman"
        or value["release_id"] != release.release_id
        or value["target_os"] != "ubuntu26.04"
        or value["architecture"] != "amd64"
        or value["otp_version"] != "27.3.4.6"
        or value["elixir_version"] != "1.18.3"
        or value["node_version"] != "22.22.1"
        or value["hex_version"] != "2.5.1"
        or value["rebar3_version"] != "3.24.0"
        or value["builder_base_tag"] != "ubuntu:resolute-20260811.1"
        or value["builder_base_digest"] != "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b"
        or value["top_level"] != "taskman"
    ):
        raise LifecycleError("installed release manifest is invalid")
    application_version = value["application_version"]
    source_revision = value["source_revision"]
    if (
        type(application_version) is not str
        or _APPLICATION_VERSION_RE.fullmatch(application_version) is None
        or type(source_revision) is not str
        or _SOURCE_REVISION_RE.fullmatch(source_revision) is None
        or release.release_id != f"{application_version}-{source_revision[:12]}-ubuntu26.04-amd64-otp27.3.4.6"
    ):
        raise LifecycleError("installed release manifest is invalid")
    _manifest_timestamp(value["built_at"])
    _manifest_migrations(value["migrations"])
    return ReleaseProvenance(
        application_version=application_version,
        source_revision=source_revision,
        target_os="ubuntu26.04",
        architecture="amd64",
        otp_version="27.3.4.6",
        hex_version="2.5.1",
        rebar3_version="3.24.0",
    )


def _manifest_timestamp(value: object) -> None:
    if type(value) is not str or not value.endswith("Z") or "T" not in value:
        raise LifecycleError("installed release manifest is invalid")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise LifecycleError("installed release manifest is invalid") from error
    if parsed.tzinfo != UTC:
        raise LifecycleError("installed release manifest is invalid")


def _manifest_migrations(value: object) -> None:
    if not isinstance(value, list):
        raise LifecycleError("installed release manifest is invalid")
    names: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _MIGRATION_FIELDS:
            raise LifecycleError("installed release manifest is invalid")
        filename, digest = item["filename"], item["sha256"]
        if (
            type(filename) is not str
            or _MIGRATION_FILENAME_RE.fullmatch(filename) is None
            or type(digest) is not str
            or _SHA256_RE.fullmatch(digest) is None
        ):
            raise LifecycleError("installed release manifest is invalid")
        names.append(filename)
    if names != sorted(names) or len(names) != len(set(names)):
        raise LifecycleError("installed release manifest is invalid")


def _current_migrations(observation: LifecycleObservation) -> tuple[Mapping[str, object], ...]:
    """Return the accepted current-release fingerprints in protocol form."""

    current = observation.records.current_release_id
    if current is None:
        return ()
    adoption = next((record for record in observation.records.adoptions if record.release_id == current), None)
    if adoption is not None:
        return tuple(dict(item) for item in adoption.migrations)
    manifest = observation.manifests_by_release.get(current)
    if not isinstance(manifest, Mapping):
        raise LifecycleError("installed release manifest is invalid")
    migrations = manifest.get("migrations")
    _manifest_migrations(migrations)
    assert isinstance(migrations, list)
    return tuple(dict(item) for item in migrations if isinstance(item, Mapping))


def _release_migrations(
    observation: LifecycleObservation,
) -> dict[str, tuple[Mapping[str, object], ...]]:
    """Return accepted migration fingerprints for every recorded release.

    A restore backup names its intended release in lifecycle history, which
    can differ from ``current``.  Keep that historical authority inside the
    helper until it is emitted as bounded discovery rows.
    """

    adoptions = {
        record.release_id: record for record in observation.records.adoptions
    }
    result: dict[str, tuple[Mapping[str, object], ...]] = {}
    for record in observation.records.releases:
        adoption = adoptions.get(record.release_id)
        if adoption is not None:
            migrations = adoption.migrations
        else:
            manifest = observation.manifests_by_release.get(record.release_id)
            if not isinstance(manifest, Mapping):
                raise LifecycleError("installed release manifest is invalid")
            migrations = manifest.get("migrations")
            _manifest_migrations(migrations)
        if not isinstance(migrations, (list, tuple)):
            raise LifecycleError("installed release manifest is invalid")
        result[record.release_id] = tuple(
            dict(item) for item in migrations if isinstance(item, Mapping)
        )
    return result


def backup_rows(facts: LifecycleFacts) -> tuple[list[dict[str, object]], list[str]]:
    """Build backup rows and stale-artifact warnings from one validated snapshot."""

    warnings = list(facts.records.warnings)
    rows: list[dict[str, object]] = []
    for record in sorted(facts.records.backups, key=lambda item: (item.created_at, item.backup_id), reverse=True):
        path = Path(record.dump_path.as_posix())
        # The record path was checked below the derived backup root by lifecycle
        # validation. Do not resolve it: a symlinked dump is stale, not authority.
        state = "present" if path.is_file() and not path.is_symlink() else "stale"
        if state == "stale":
            warnings.append(f"backup metadata is stale: {record.backup_id}")
        rows.append({**record.to_mapping(), "dump_state": state})
    return rows, sorted(warnings)


def _validate_managed_current(facts: LifecycleFacts) -> None:
    if not facts.current_is_symlink or facts.current_target is None:
        raise LifecycleError("current selection is absent despite activation records")
    selected = facts.records.current_release_id
    assert selected is not None
    adopted = {record.release_id: record.release_path.as_posix() for record in facts.records.adoptions}
    expected = adopted.get(selected, (facts.paths.release_root / selected).as_posix())
    if facts.current_target != expected:
        raise LifecycleError("current selection conflicts with activation records")


def _validate_current_observation(observation: LifecycleObservation) -> None:
    """Keep current-link relation acceptance separate from link observation."""

    selected = observation.records.current_release_id
    if selected is None:
        # A residue-free link is the supported manual-install shape.  Once
        # *any* lifecycle marker exists, though, a link without its activation
        # is incomplete authority and discovery must refuse it.
        has_lifecycle_records = any(
            (
                observation.records.releases,
                observation.records.activations,
                observation.records.backups,
                observation.records.adoptions,
            )
        )
        if has_lifecycle_records and (observation.current_target is not None or observation.current_is_symlink):
            raise LifecycleError("current selection exists without an activation record")
        return
    facts = LifecycleFacts(
        observation.paths,
        observation.records,
        observation.current_target,
        observation.current_is_symlink,
        observation.owner_uid,
        {},
        (),
        {},
    )
    _validate_managed_current(facts)


def _bounded_warnings(values: tuple[str, ...]) -> tuple[str, ...]:
    """Return one deterministic protocol-safe inventory summary."""

    return tuple(sorted(set(values)))[:_MAX_INVENTORY_WARNINGS]


def _entries(directory: Path) -> _InventoryEntries:
    """Stream a deterministic bounded sample while retaining the total count."""

    try:
        selected: list[Path] = []
        total = 0
        with os.scandir(directory) as iterator:
            for entry in iterator:
                total += 1
                candidate = Path(entry.path)
                if len(selected) < _MAX_INVENTORY_ENTRIES:
                    selected.append(candidate)
                    selected.sort(key=lambda item: item.name)
                elif entry.name < selected[-1].name:
                    selected[-1] = candidate
                    selected.sort(key=lambda item: item.name)
        return _InventoryEntries(tuple(selected), total)
    except FileNotFoundError:
        return _InventoryEntries((), 0)
    except OSError as error:
        raise LifecycleError("unable to inventory managed lifecycle storage") from error


def _inventory(store: LifecycleStore, records: LifecycleRecords) -> tuple[str, ...]:
    """Collect bounded stale-state warnings without treating residue as fact.

    The checks mirror the established snapshot inventory.  Exact recognized
    record paths remain authoritative; unrecognized files and directories are
    merely reported so an operator can investigate them without discovery
    silently erasing or adopting them.
    """

    warnings: list[str] = []
    deployment = store.deployment_root
    for entry in _inventory_entries(deployment, "deployment-root", warnings):
        if entry.name not in _DEPLOYMENT_DIRECTORIES:
            warnings.append(f"unexpected deployment-root entry: {entry.name}")

    known_adoptions = {record.release_id for record in records.adoptions}
    known_direct = {record.release_id for record in records.releases} - known_adoptions
    record_categories = {
        "releases": {f"release-{release_id}.json" for release_id in known_direct},
        "activations": {f"{record.activation_id}.json" for record in records.activations},
        "backups": {f"{record.backup_id}.json" for record in records.backups},
        "adoptions": {f"adoption-{release_id}.json" for release_id in known_adoptions},
    }
    for category, known_names in record_categories.items():
        for entry in _inventory_entries(deployment / category, category, warnings):
            if entry.name not in known_names:
                warnings.append(f"unrecognized deployment storage entry: {category}/{entry.name}")

    for entry in _inventory_entries(deployment / "adoption-transactions", "adoption-transactions", warnings):
        release_id = entry.name.removeprefix("adoption-")
        if not entry.name.startswith("adoption-") or release_id not in known_adoptions:
            warnings.append(f"unrecognized lifecycle transaction: {entry.name}")

    # A provisional stage has no committed activation marker by design.  It
    # remains visible, bounded residue until its state-changing consumer
    # confirms and commits it, so discovery never mistakes it for a release.
    for entry in _inventory_entries(deployment / "provisionals", "provisionals", warnings):
        warnings.append(f"unrecognized lifecycle transaction: {entry.name}")

    manifest_names = {f"release-{release_id}.json" for release_id in known_direct}
    for entry in _inventory_entries(deployment / "manifests", "manifests", warnings):
        if entry.name not in manifest_names:
            warnings.append(f"unrecognized manifest entry: {entry.name}")

    direct_release_paths = {store.release_path(release_id) for release_id in known_direct}
    for entry in _inventory_entries(store.release_root, "release-root", warnings):
        if entry in direct_release_paths:
            continue
        try:
            details = entry.lstat()
        except OSError as error:
            raise LifecycleError("unable to inventory managed release root") from error
        label = "unrecognized release directory" if stat.S_ISDIR(details.st_mode) and not stat.S_ISLNK(details.st_mode) else "unrecognized release entry"
        warnings.append(f"{label}: {entry.name}")

    known_dumps = {Path(record.dump_path.as_posix()) for record in records.backups}
    for entry in _inventory_entries(store.backup_root, "backup-root", warnings):
        if entry not in known_dumps:
            warnings.append(f"orphan backup dump: {entry.name}")
    return tuple(warnings)


def _inventory_entries(directory: Path, label: str, warnings: list[str]) -> tuple[Path, ...]:
    values = _entries(directory)
    if values.total > len(values.paths):
        warnings.append(
            f"inventory truncated: {label} ({values.total} entries; listed first {len(values.paths)})"
        )
    return values.paths


__all__ = [
    "LifecycleObservation",
    "LifecycleFacts",
    "ReleaseProvenance",
    "accept_lifecycle_observation",
    "backup_rows",
    "classify_lifecycle",
    "collect_lifecycle_facts",
    "lifecycle_mapping",
    "release_rows",
    "observe_lifecycle",
]
