"""Root-owned lifecycle storage and policy validation."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from uuid import uuid4

from .lifecycle_records import (
    SCHEMA_VERSION,
    ActivationRecord,
    AdoptionRecord,
    BackupRecord,
    LifecycleError,
    LifecycleRecords,
    ManualAdoptionCandidate,
    ReleaseRecord,
    StagedRelease,
    _activation,
    _backup_id,
    _format_timestamp,
    _migrations,
    _path,
    _release,
    _sha256,
    _timestamp,
    _under,
    _VERSION_RE,
)
from .paths import ManagedPaths


MAX_RECORD_BYTES = 64 * 1024
_MAX_RECORD_ENTRIES = 64


@dataclass(frozen=True)
class LifecycleWriteEffect:
    """The durable publication fact returned by one no-replace record write."""

    published: bool


class LifecycleWriteFailure(LifecycleError):
    """A record-write failure that retains publication and exact residue facts."""

    def __init__(
        self,
        message: str,
        effect: LifecycleWriteEffect,
        residue_paths: tuple[Path, ...] = (),
    ) -> None:
        if not isinstance(effect, LifecycleWriteEffect):
            raise TypeError("lifecycle write failure requires an effect")
        if not isinstance(residue_paths, tuple) or not all(isinstance(path, Path) for path in residue_paths):
            raise TypeError("lifecycle write failure residue paths are invalid")
        self.effect = effect
        self.residue_paths = residue_paths
        super().__init__(message)


@dataclass(frozen=True)
class LifecycleFinalizationEffect:
    """The exact durable finalization prefix and provisional-retirement fact.

    Finalization publishes three independent no-replace records.  A failure
    after any one of them cannot truthfully be represented by the boolean
    effect of only the last write, so this record carries the complete prefix
    that a locked retry is allowed to validate and finish.
    """

    published_paths: tuple[Path, ...]
    provisional_path: Path | None
    residue_paths: tuple[Path, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.published_paths)


class LifecycleFinalizationFailure(LifecycleError):
    """A finalization error that retains its durable multi-record effects."""

    def __init__(self, message: str, effect: LifecycleFinalizationEffect) -> None:
        if not isinstance(effect, LifecycleFinalizationEffect):
            raise TypeError("lifecycle finalization failure requires an effect")
        self.effect = effect
        super().__init__(message)


@dataclass(frozen=True)
class LifecycleLockHolder:
    """The bounded, non-sensitive holder shape shared with shell transactions."""

    operation: str
    pid: int
    started_at: str
    mode: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "pid": self.pid,
            "started_at": self.started_at,
            "mode": self.mode,
        }


class LifecycleLockContention(LifecycleError):
    """A canonical lifecycle lock was still held at the caller deadline."""

    def __init__(self, holder: LifecycleLockHolder | None) -> None:
        self.holder = holder
        if holder is None:
            detail = "an unknown lifecycle operation"
        else:
            detail = f"{holder.operation} (pid {holder.pid}, started {holder.started_at}, {holder.mode})"
        super().__init__(f"lifecycle lock is held by {detail}")


class LifecycleStore:
    """Root-owned storage under paths derived by :class:`ManagedPaths`."""

    def __init__(self, paths: ManagedPaths, *, owner_uid: int | None = None) -> None:
        if not isinstance(paths, ManagedPaths): raise TypeError("lifecycle store needs managed paths")
        self.paths, self.owner_uid = paths, os.geteuid() if owner_uid is None else owner_uid
        if type(self.owner_uid) is not int or self.owner_uid < 0: raise ValueError("invalid lifecycle owner")

    @property
    def deployment_root(self) -> Path: return self.paths.local(self.paths.deployment_root)
    @property
    def release_root(self) -> Path: return self.paths.local(self.paths.release_root)
    @property
    def backup_root(self) -> Path: return self.paths.local(self.paths.backup_root)
    @property
    def current_link(self) -> Path: return self.paths.local(self.paths.current_link)

    def _directory(self, category: str) -> Path: return self.deployment_root / category

    @property
    def _lock_root(self) -> Path:
        """Return the single writer-compatible lifecycle lock authority.

        The helper is invoked through ``sudo`` in production, so root always
        uses the established lock shared with the remaining state-changing
        transactions.  A private sibling lock makes the unprivileged isolated
        zipapp characterization harness executable without weakening the
        production authority.
        """

        if self.owner_uid == 0:
            return Path("/var/lock/taskman")
        return self.paths.local(self.paths.install_root).parent / ".taskman-lock"

    @contextmanager
    def shared_snapshot_lock(
        self,
        *,
        deadline: float | None = None,
        operation: str = "discovery",
    ):
        """Hold the canonical shared lock without waiting beyond ``deadline``."""

        with self._snapshot_lock(fcntl.LOCK_SH, deadline=deadline, operation=operation):
            yield

    @contextmanager
    def exclusive_lifecycle_lock(
        self,
        *,
        deadline: float | None = None,
        operation: str = "lifecycle-write",
    ):
        """Serialize a lifecycle mutation with the same canonical lock."""

        with self._snapshot_lock(fcntl.LOCK_EX, deadline=deadline, operation=operation):
            yield

    @contextmanager
    def _snapshot_lock(self, mode: int, *, deadline: float | None, operation: str):
        """Acquire the shell-compatible lock and bounded holder metadata atomically.

        The remaining state-changing shell programs own ``lifecycle.lock`` and
        its pipe-delimited ``.meta`` sidecar.  The helper deliberately uses
        that same conservative schema so a read cannot wait forever or become
        invisible to a later exclusive transaction.
        """

        if mode not in {fcntl.LOCK_SH, fcntl.LOCK_EX}:
            raise ValueError("invalid lifecycle lock mode")
        if re.fullmatch(r"[a-z][a-z-]{0,63}", operation) is None:
            raise ValueError("invalid lifecycle lock operation")
        if deadline is None:
            deadline = time.monotonic() + 5.0
        if type(deadline) not in {int, float} or not time.monotonic() < float(deadline):
            raise LifecycleLockContention(None)

        root = self._lock_root
        self._ensure_lock_root(root)
        descriptor = self._open_lock(root / "lifecycle.lock")
        metadata_descriptor: int | None = None
        token: str | None = None
        try:
            metadata_descriptor = self._open_lock(root / "lifecycle.lock.meta")
            holder = LifecycleLockHolder(
                operation=operation,
                pid=os.getpid(),
                started_at=_format_timestamp(datetime.now(UTC).replace(microsecond=0), "lock holder start time"),
                mode="shared" if mode == fcntl.LOCK_SH else "exclusive",
            )
            token = uuid4().hex
            requested = mode | fcntl.LOCK_NB
            while True:
                if not _acquire_metadata_lock(metadata_descriptor, float(deadline)):
                    raise LifecycleLockContention(None)
                try:
                    holders = _live_lock_holders(_read_lock_holders(metadata_descriptor))
                    _write_lock_holders(metadata_descriptor, holders)
                    try:
                        fcntl.flock(descriptor, requested)
                    except BlockingIOError:
                        contender = _representative_lock_holder(holders)
                    else:
                        _write_lock_holders(metadata_descriptor, [*holders, (token, holder)])
                        fcntl.flock(metadata_descriptor, fcntl.LOCK_UN)
                        try:
                            yield
                        finally:
                            self._release_snapshot_lock(
                                descriptor,
                                metadata_descriptor,
                                token,
                                deadline=float(deadline),
                            )
                            descriptor = -1
                            metadata_descriptor = None
                        return
                except Exception:
                    if metadata_descriptor is not None:
                        fcntl.flock(metadata_descriptor, fcntl.LOCK_UN)
                    raise
                else:
                    fcntl.flock(metadata_descriptor, fcntl.LOCK_UN)
                if time.monotonic() >= float(deadline):
                    raise LifecycleLockContention(contender)
                time.sleep(min(0.01, max(0.0, float(deadline) - time.monotonic())))
        except LifecycleError:
            raise
        except OSError as error:
            raise LifecycleError("unable to acquire lifecycle snapshot lock") from error
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            if metadata_descriptor is not None:
                os.close(metadata_descriptor)

    @staticmethod
    def _release_snapshot_lock(
        descriptor: int,
        metadata_descriptor: int,
        token: str,
        *,
        deadline: float,
    ) -> None:
        """Release without letting a contended metadata sidecar exceed the deadline.

        The primary flock is released first.  If the sidecar is briefly busy,
        the token is conservative stale evidence and the next acquisition will
        prune it once this short-lived helper process exits; cleanup never
        holds the primary lock or the runner beyond its fixed budget.
        """

        metadata_held = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            if not _acquire_metadata_lock(metadata_descriptor, deadline):
                return
            metadata_held = True
            holders = _live_lock_holders(_read_lock_holders(metadata_descriptor))
            _write_lock_holders(
                metadata_descriptor,
                [item for item in holders if item[0] != token],
            )
        finally:
            try:
                if metadata_held:
                    fcntl.flock(metadata_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(metadata_descriptor)
                os.close(descriptor)

    def inspect_manual_current(self) -> ManualAdoptionCandidate:
        """Inspect a residue-free manual current release under the shared lock."""

        with self.shared_snapshot_lock(operation="adoption-inspect"):
            return self.inspect_manual_current_locked()

    def inspect_manual_current_locked(self) -> ManualAdoptionCandidate:
        """Reobserve a manual current release while the caller owns the lock.

        Deployment uses this before publishing adoption records, so migration
        policy can be checked against the exact reobserved fingerprints before
        any lifecycle mutation is made.
        """

        self.paths.validate_existing(owner_uid=self.owner_uid)
        records = self.read(validate_current=False)
        if records.releases or records.activations or records.backups or records.adoptions:
            raise LifecycleError("manual adoption requires an empty lifecycle")
        return self._manual_candidate()

    def adopt_manual_current(self, expected: ManualAdoptionCandidate) -> AdoptionRecord:
        """Revalidate one confirmed manual candidate and publish its atomic marker."""

        if not isinstance(expected, ManualAdoptionCandidate):
            raise TypeError("manual adoption requires a candidate")
        with self.exclusive_lifecycle_lock(operation="adoption-write"):
            return self.adopt_manual_current_locked(expected)

    def adopt_manual_current_locked(self, expected: ManualAdoptionCandidate) -> AdoptionRecord:
        """Adopt a revalidated manual release while a transaction holds the lock."""

        if not isinstance(expected, ManualAdoptionCandidate):
            raise TypeError("manual adoption requires a candidate")
        # The lock closes the check-to-publish window.  Revalidate every
        # derived root *after* acquiring it, rather than trusting the earlier
        # inspection step or following a newly swapped symlink.
        observed = self.inspect_manual_current_locked()
        if observed != expected:
            raise LifecycleError("manual adoption confirmation no longer matches host authority")
        # A complete bundle without its final marker can be resumed, but only
        # after the same empty-lifecycle and manual-tree observation required
        # for first publication is repeated under this lock.
        resumed = self._resume_markerless_adoption(observed)
        if resumed is not None:
            return resumed
        adopted_at = datetime.now(UTC).replace(microsecond=0)
        adoption = AdoptionRecord(
            SCHEMA_VERSION,
            observed.release_id,
            adopted_at,
            observed.release_path,
            observed.content_sha256,
            observed.application_version,
            "unknown",
            "unknown",
            observed.migrations,
        )
        release = ReleaseRecord(
            SCHEMA_VERSION,
            observed.release_id,
            None,
            adopted_at,
            adopted_at,
            None,
            None,
            "adopted",
        )
        activation = ActivationRecord(
            SCHEMA_VERSION,
            f"activation-{observed.content_sha256[:32]}",
            None,
            observed.release_id,
            adopted_at,
            None,
            "adopted",
        )
        self._write_adoption_bundle(adoption, release, activation)
        return adoption

    def _resume_markerless_adoption(self, expected: ManualAdoptionCandidate) -> AdoptionRecord | None:
        """Publish a missing marker only for a complete, exact adoption bundle.

        ``release.json`` and ``activation.json`` are durable before the marker
        by design.  A crash in that narrow interval is recoverable only when
        every bundled fact still exactly proves the re-observed manual tree.
        Partial or contradictory residue stays an explicit refusal.
        """

        transactions = self._directory("adoption-transactions")
        markers = self._directory("adoptions")
        bundle = transactions / f"adoption-{expected.release_id}"
        marker = markers / f"adoption-{expected.release_id}.json"
        bundle_exists = bundle.exists() or bundle.is_symlink()
        marker_exists = marker.exists() or marker.is_symlink()
        if not bundle_exists:
            return None
        if marker_exists:
            raise LifecycleError("manual adoption record already exists")
        _safe_directory(transactions, self.owner_uid)
        _safe_directory(bundle, self.owner_uid)
        try:
            release = _read_bundle_record(bundle / "release.json", ReleaseRecord, self.owner_uid)
            activation = _read_bundle_record(bundle / "activation.json", ActivationRecord, self.owner_uid)
        except LifecycleError as error:
            raise LifecycleError(f"incomplete manual adoption transaction: {bundle.name}") from error
        if not isinstance(release, ReleaseRecord) or not isinstance(activation, ActivationRecord):
            raise LifecycleError(f"incomplete manual adoption transaction: {bundle.name}")
        adopted_at = release.installed_at
        adoption = AdoptionRecord(
            SCHEMA_VERSION,
            expected.release_id,
            adopted_at,
            expected.release_path,
            expected.content_sha256,
            expected.application_version,
            "unknown",
            "unknown",
            expected.migrations,
        )
        expected_activation_id = f"activation-{expected.content_sha256[:32]}"
        if (
            release.to_mapping()
            != ReleaseRecord(
                SCHEMA_VERSION,
                expected.release_id,
                None,
                adopted_at,
                adopted_at,
                None,
                None,
                "adopted",
            ).to_mapping()
            or activation.to_mapping()
            != ActivationRecord(
                SCHEMA_VERSION,
                expected_activation_id,
                None,
                expected.release_id,
                adopted_at,
                None,
                "adopted",
            ).to_mapping()
        ):
            raise LifecycleError("markerless manual adoption does not match host authority")
        self._ensure_directory(markers)
        self._write(marker, adoption.to_mapping())
        return adoption

    def _manual_candidate(self) -> ManualAdoptionCandidate:
        current = self.current_link
        try:
            details = current.lstat()
            if not stat.S_ISLNK(details.st_mode):
                raise LifecycleError("manual adoption requires a current symlink")
            selected = current.resolve(strict=True)
            root = self.release_root.resolve(strict=True)
            selected.relative_to(root)
        except LifecycleError:
            raise
        except (OSError, ValueError) as error:
            raise LifecycleError("manual adoption current selection is invalid") from error
        _safe_directory(self.release_root, self.owner_uid)
        _safe_directory(selected, self.owner_uid)
        server = selected / "bin" / "server"
        if not _owned_regular_executable(server, self.owner_uid):
            raise LifecycleError("manual release server is invalid")
        application_version, migrations = _manual_application(selected, self.owner_uid)
        content = _tree_digest(selected, self.owner_uid)
        return ManualAdoptionCandidate(
            SCHEMA_VERSION,
            f"{application_version}-{content[:12]}-ubuntu26.04-amd64-otp27.3.4.6",
            PurePosixPath(selected.as_posix()),
            content,
            application_version,
            migrations,
        )

    def _write_adoption_bundle(
        self,
        adoption: AdoptionRecord,
        release: ReleaseRecord,
        activation: ActivationRecord,
    ) -> None:
        transactions = self._directory("adoption-transactions")
        markers = self._directory("adoptions")
        self._ensure_directory(transactions)
        self._ensure_directory(markers)
        bundle = transactions / f"adoption-{adoption.release_id}"
        marker = markers / f"adoption-{adoption.release_id}.json"
        if bundle.exists() or bundle.is_symlink() or marker.exists() or marker.is_symlink():
            raise LifecycleError("manual adoption record already exists")
        try:
            bundle.mkdir(mode=0o750)
            os.chmod(bundle, 0o750)
            os.chown(bundle, self.owner_uid, -1)
            _safe_directory(bundle, self.owner_uid)
            self._write_private_new(bundle / "release.json", release.to_mapping())
            self._write_private_new(bundle / "activation.json", activation.to_mapping())
            self._fsync_directory(bundle)
            self._fsync_directory(transactions)
            self._write(marker, adoption.to_mapping())
        except OSError as error:
            raise LifecycleError("unable to publish manual adoption") from error

    def _write_private_new(self, path: Path, payload: Mapping[str, object]) -> None:
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise LifecycleError("lifecycle record is oversized")
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, self.owner_uid, -1)
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        except OSError as error:
            raise LifecycleError("unable to write manual adoption bundle") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def stage_immutable(self, staged: StagedRelease) -> None:
        """Publish one provisional edge without permitting an in-place retry edit."""

        if not isinstance(staged, StagedRelease):
            raise TypeError("immutable staging requires a staged release")
        with self.exclusive_lifecycle_lock(operation="release-stage"):
            self.paths.validate_existing(owner_uid=self.owner_uid)
            self._validate_staged_candidate(staged)
            # A provisional record is meaningful only alongside the immutable
            # candidate it names.  The later transaction may resume it, but
            # it never overwrites this recorded authority.
            self._write(self._directory("provisionals") / f"{staged.activation_id}.json", staged.to_mapping())

    def stage_immutable_locked(self, staged: StagedRelease) -> None:
        """Publish immutable staging while the caller holds this store's lock.

        A transaction must not release and reacquire the lifecycle lock between
        observing its confirmed predecessor and recording the provisional edge.
        The public locking wrapper above remains appropriate for standalone
        callers such as manual recovery; the deploy helper uses this focused
        lock-held primitive.
        """

        if not isinstance(staged, StagedRelease):
            raise TypeError("immutable staging requires a staged release")
        self.paths.validate_existing(owner_uid=self.owner_uid)
        self._validate_staged_candidate(staged)
        self._write(
            self._directory("provisionals") / f"{staged.activation_id}.json",
            staged.to_mapping(),
        )

    def finalize_staged_locked(
        self,
        staged: StagedRelease,
        release: ReleaseRecord,
        activation: ActivationRecord,
    ) -> LifecycleFinalizationEffect:
        """Publish final lifecycle records after an already-verified activation.

        A retry may validate and retain an exact prefix that this method
        previously published, but it can never replace it.  Contradictory or
        non-prefix records remain a safety refusal.  Every error exposes the
        known durable records and whether the provisional still needs
        retirement, so callers do not convert partial publication to an
        unchanged records stage.
        """

        if not all(isinstance(value, expected) for value, expected in ((staged, StagedRelease), (release, ReleaseRecord), (activation, ActivationRecord))):
            raise TypeError("staged finalization requires lifecycle records")
        if (
            release.release_id != staged.candidate_release_id
            or activation.activation_id != staged.activation_id
            or activation.candidate_release_id != staged.candidate_release_id
            or activation.previous_release_id != staged.previous_release_id
            or activation.backup_id != staged.backup_id
        ):
            raise LifecycleError("staged finalization does not match immutable authority")
        self._validate_staged_candidate(staged)
        manifest_path = self.manifest_path(staged.candidate_release_id)
        release_path = self._record_path("releases", release.release_id)
        activation_path = self._record_path("activations", activation.activation_id)
        provisional = self._directory("provisionals") / f"{staged.activation_id}.json"

        paths = (manifest_path, release_path, activation_path)
        present = tuple(self._finalization_record_matches(path, expected) for path, expected in (
            (manifest_path, staged.manifest),
            (release_path, release),
            (activation_path, activation),
        ))
        if any(
            not present[index] and any(present[index + 1 :])
            for index in range(len(present))
        ):
            raise LifecycleError("staged finalization record prefix is contradictory")

        provisional_exists = provisional.exists() or provisional.is_symlink()
        if provisional_exists:
            self._require_matching_provisional(provisional, staged)
        elif present != (True, True, True):
            raise LifecycleError("immutable staging record is absent before finalization")

        effect = LifecycleFinalizationEffect(
            tuple(path for path, exists in zip(paths, present, strict=True) if exists),
            provisional if provisional_exists else None,
        )
        for path, payload, exists in zip(
            paths,
            (staged.manifest, release.to_mapping(), activation.to_mapping()),
            present,
            strict=True,
        ):
            if exists:
                continue
            try:
                self._write(path, payload)
            except LifecycleWriteFailure as error:
                if error.effect.published:
                    effect = LifecycleFinalizationEffect(
                        (*effect.published_paths, path), effect.provisional_path, error.residue_paths
                    )
                elif error.residue_paths:
                    effect = LifecycleFinalizationEffect(
                        effect.published_paths, effect.provisional_path, error.residue_paths
                    )
                raise LifecycleFinalizationFailure(
                    "unable to publish verified lifecycle records", effect
                ) from error
            except LifecycleError as error:
                raise LifecycleFinalizationFailure(
                    "unable to publish verified lifecycle records", effect
                ) from error
            effect = LifecycleFinalizationEffect(
                (*effect.published_paths, path), effect.provisional_path, effect.residue_paths
            )

        if effect.provisional_path is None:
            return effect
        try:
            effect.provisional_path.unlink()
        except OSError as error:
            raise LifecycleFinalizationFailure(
                "unable to retire immutable staging record", effect
            ) from error
        effect = LifecycleFinalizationEffect(effect.published_paths, None, effect.residue_paths)
        try:
            self._fsync_directory(provisional.parent)
        except OSError as error:
            raise LifecycleFinalizationFailure(
                "unable to retire immutable staging record", effect
            ) from error
        return effect

    def _finalization_record_matches(
        self,
        path: Path,
        expected: Mapping[str, object] | ReleaseRecord | ActivationRecord,
    ) -> bool:
        """Return whether one optional final record exists and is exact.

        Presence checks remain no-follow checks.  An existing record that is
        unsafe or differs from the immutable provisional is not an interrupted
        prefix this transaction may adopt.
        """

        if not path.exists() and not path.is_symlink():
            return False
        _safe_file(path, self.owner_uid)
        if isinstance(expected, ReleaseRecord):
            observed = ReleaseRecord.from_mapping(
                _read_json(path, self.owner_uid, "release record")
            )
            matches = observed == expected
        elif isinstance(expected, ActivationRecord):
            observed = ActivationRecord.from_mapping(
                _read_json(path, self.owner_uid, "activation record")
            )
            matches = observed == expected
        else:
            observed = _read_json(path, self.owner_uid, "installed manifest")
            matches = isinstance(observed, Mapping) and dict(observed) == dict(expected)
        if not matches:
            raise LifecycleError("staged finalization record does not match immutable authority")
        return True

    def _require_matching_provisional(self, path: Path, staged: StagedRelease) -> None:
        """Require the retained provisional still encodes this exact authority."""

        _safe_file(path, self.owner_uid)
        observed = StagedRelease.from_mapping(
            _read_json(path, self.owner_uid, "immutable staging record")
        )
        if observed != staged:
            raise LifecycleError("immutable staging record does not match finalization authority")

    def resume_immutable(self, activation_id: str, candidate_release_id: str, artifact_sha256: str) -> StagedRelease:
        """Return a resumable provisional only when all confirmed authority matches."""

        with self.exclusive_lifecycle_lock(operation="release-resume"):
            return self.resume_immutable_locked(activation_id, candidate_release_id, artifact_sha256)

    def resume_immutable_locked(
        self, activation_id: str, candidate_release_id: str, artifact_sha256: str
    ) -> StagedRelease:
        """Read a provisional while a caller already owns the lifecycle lock."""

        _activation(activation_id)
        _release(candidate_release_id)
        _sha256(artifact_sha256, "staged artifact checksum")
        self.paths.validate_existing(owner_uid=self.owner_uid)
        directory = self._directory("provisionals")
        _safe_directory(directory, self.owner_uid)
        path = directory / f"{activation_id}.json"
        _safe_file(path, self.owner_uid)
        staged = StagedRelease.from_mapping(_read_json(path, self.owner_uid, "immutable staging record"))
        if (staged.activation_id, staged.candidate_release_id, staged.artifact_sha256) != (
            activation_id,
            candidate_release_id,
            artifact_sha256,
        ):
            raise LifecycleError("immutable staging confirmation no longer matches host authority")
        self._validate_staged_candidate(staged)
        return staged

    def resume_matching_immutable_locked(
        self, candidate_release_id: str, artifact_sha256: str
    ) -> StagedRelease | None:
        """Find exactly one prior provisional for a fresh-operation rerun.

        The caller already owns the lifecycle lock.  Matching only immutable
        candidate identity and archive checksum is deliberately not enough to
        resume: this method returns the one durable candidate and the caller
        must independently revalidate predecessor, policy, manifest, backup,
        and current selection before mutation.  Multiple matches are unsafe.
        """

        _release(candidate_release_id)
        _sha256(artifact_sha256, "staged artifact checksum")
        self.paths.validate_existing(owner_uid=self.owner_uid)
        directory = self._directory("provisionals")
        if not directory.exists() and not directory.is_symlink():
            return None
        _safe_directory(directory, self.owner_uid)
        entries = self._bounded_category_entries(directory, "provisionals")
        matches: list[StagedRelease] = []
        for path in entries:
            name = path.name
            if not name.endswith(".json"):
                raise LifecycleError("invalid immutable staging record")
            activation_id = name.removesuffix(".json")
            _activation(activation_id)
            _safe_file(path, self.owner_uid)
            staged = StagedRelease.from_mapping(
                _read_json(path, self.owner_uid, "immutable staging record")
            )
            if (
                staged.candidate_release_id == candidate_release_id
                and staged.artifact_sha256 == artifact_sha256
            ):
                self._validate_staged_candidate(staged)
                matches.append(staged)
        if len(matches) > 1:
            raise LifecycleError("immutable staging resume is ambiguous")
        return matches[0] if matches else None

    def _validate_staged_candidate(self, staged: StagedRelease) -> None:
        """Bind a provisional record to its immutable published tree and marker."""

        candidate = self.release_path(staged.candidate_release_id)
        try:
            self.require_release_directory(candidate)
        except LifecycleError as error:
            raise LifecycleError("immutable staging candidate is absent or unsafe") from error
        marker = candidate / ".taskman-release.json"
        try:
            _safe_release_marker(marker, self.owner_uid)
            marker_value = _read_release_marker(marker, self.owner_uid)
        except LifecycleError as error:
            raise LifecycleError("immutable staging candidate marker is absent or unsafe") from error
        if marker_value != {
            "schema_version": SCHEMA_VERSION,
            "release_id": staged.candidate_release_id,
            "artifact_sha256": staged.artifact_sha256,
        }:
            raise LifecycleError("immutable staging candidate marker does not match confirmed checksum")
        _validate_staged_manifest(staged.manifest, staged.candidate_release_id)

    def _ensure_lock_root(self, root: Path) -> None:
        try:
            if root.exists() or root.is_symlink():
                _safe_directory(root, self.owner_uid)
                return
            root.mkdir(parents=True, mode=0o700)
            os.chmod(root, 0o700)
            os.chown(root, self.owner_uid, -1)
            _safe_directory(root, self.owner_uid)
        except OSError as error:
            raise LifecycleError("unable to prepare lifecycle snapshot lock") from error

    def _open_lock(self, path: Path) -> int:
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            except FileNotFoundError:
                descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.fchmod(descriptor, 0o600)
                os.fchown(descriptor, self.owner_uid, -1)
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != self.owner_uid
                or stat.S_IMODE(details.st_mode) != 0o600
            ):
                raise LifecycleError("lifecycle snapshot lock is unsafe")
            return descriptor
        except LifecycleError:
            raise
        except OSError as error:
            raise LifecycleError("lifecycle snapshot lock is unsafe") from error
        finally:
            # Ownership of a verified descriptor transfers to the caller.
            if descriptor is not None:
                try:
                    details = os.fstat(descriptor)
                except OSError:
                    details = None
                if details is None or (
                    not stat.S_ISREG(details.st_mode)
                    or details.st_uid != self.owner_uid
                    or stat.S_IMODE(details.st_mode) != 0o600
                ):
                    os.close(descriptor)

    def release_path(self, release_id: str) -> Path:
        return self.release_root / _release(release_id)

    def manifest_path(self, release_id: str) -> Path:
        return self._directory("manifests") / f"release-{_release(release_id)}.json"

    def require_release_directory(self, path: Path) -> None:
        """Require an existing, owned release directory below the derived root."""

        try:
            path.relative_to(self.release_root)
        except ValueError as error:
            raise LifecycleError("release path is outside the managed release root") from error
        _safe_directory(self.release_root, self.owner_uid)
        _safe_directory(path, self.owner_uid)

    def read_manifest(self, release_id: str) -> object:
        """Read one exact root-owned installed manifest without following links."""

        directory = self._directory("manifests")
        _safe_directory(directory, self.owner_uid)
        path = self.manifest_path(release_id)
        _safe_file(path, self.owner_uid)
        return _read_json(path, self.owner_uid, "installed manifest")

    def _record_path(self, category: str, identifier: str) -> Path:
        if category == "releases":
            name = f"release-{_release(identifier)}.json"
        elif category == "activations":
            name = f"{_activation(identifier)}.json"
        elif category == "backups":
            name = f"{_backup_id(identifier)}.json"
        elif category == "adoptions":
            name = f"adoption-{_release(identifier)}.json"
        else:
            raise LifecycleError("unknown lifecycle record category")
        return self._directory(category) / name

    def write_release(self, record: ReleaseRecord) -> None: self._write(self._record_path("releases", record.release_id), record.to_mapping())
    def write_activation(self, record: ActivationRecord) -> None: self._write(self._record_path("activations", record.activation_id), record.to_mapping())
    def write_backup(self, record: BackupRecord) -> None:
        if not _under(record.dump_path, self.paths.backup_root): raise LifecycleError("backup dump path is outside the managed backup root")
        self._write(self._record_path("backups", record.backup_id), record.to_mapping())
    def write_adoption(self, record: AdoptionRecord) -> None:
        if not _under(record.release_path, self.paths.release_root): raise LifecycleError("adoption path is outside the managed release root")
        self._write(self._record_path("adoptions", record.release_id), record.to_mapping())

    def _write(self, path: Path, payload: Mapping[str, object]) -> LifecycleWriteEffect:
        """Publish a no-replace record and surface link-before-fsync effects.

        A caller receives ``published=True`` after a normal return.  If the
        final hard link succeeded but directory fsync failed, the raised
        :class:`LifecycleWriteFailure` carries the same durable-effect fact
        instead of hiding a record that a later recovery must inspect.
        """

        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise LifecycleError("lifecycle record is oversized")
        self._ensure_directory(path.parent)
        if path.exists() or path.is_symlink(): raise LifecycleError("lifecycle record identifier already exists")
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        descriptor: int | None = None
        effect = LifecycleWriteEffect(published=False)
        failure: LifecycleWriteFailure | None = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.fchmod(descriptor, 0o600); os.fchown(descriptor, self.owner_uid, -1)
            _write_all(descriptor, encoded); os.fsync(descriptor)
            os.close(descriptor); descriptor = None
            os.link(temporary, path, follow_symlinks=False)
            effect = LifecycleWriteEffect(published=True)
            self._fsync_directory(path.parent)
        except LifecycleWriteFailure as error:
            failure = error
        except LifecycleError:
            raise
        except OSError as error:
            failure = LifecycleWriteFailure("unable to atomically write lifecycle record", effect)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                if failure is None:
                    failure = LifecycleWriteFailure(
                        "unable to remove lifecycle record temporary residue",
                        effect,
                        (temporary,),
                    )
                else:
                    failure.residue_paths = tuple(dict.fromkeys((*failure.residue_paths, temporary)))
        if failure is not None:
            raise failure
        return effect

    def _ensure_directory(self, directory: Path) -> None:
        if directory.exists() or directory.is_symlink():
            _safe_directory(directory, self.owner_uid); return
        root = self.deployment_root
        try:
            if root.exists() or root.is_symlink(): _safe_directory(root, self.owner_uid)
            else:
                root.mkdir(parents=True); os.chmod(root, 0o750); os.chown(root, self.owner_uid, -1); _safe_directory(root, self.owner_uid)
            directory.mkdir(); os.chmod(directory, 0o750); os.chown(directory, self.owner_uid, -1); _safe_directory(directory, self.owner_uid)
        except OSError as error: raise LifecycleError("unable to prepare lifecycle record directory") from error

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

    def observe(self) -> LifecycleRecords:
        """Parse bounded record files without accepting their relationships.

        A caller holding :meth:`shared_snapshot_lock` can use this to observe
        an interrupted transaction faithfully.  Relationship and current-link
        acceptance belongs to the operation that consumes the observation.
        """
        if self.deployment_root.exists() or self.deployment_root.is_symlink():
            _safe_directory(self.deployment_root, self.owner_uid)
        for category in (
            "releases",
            "activations",
            "backups",
            "adoptions",
            "adoption-transactions",
            "manifests",
            "provisionals",
        ):
            directory = self._directory(category)
            if directory.exists() or directory.is_symlink():
                _safe_directory(directory, self.owner_uid)
        releases, warnings = self._read_category("releases", ReleaseRecord, "release-")
        activations, activation_warnings = self._read_category("activations", ActivationRecord, "")
        backups, backup_warnings = self._read_category("backups", BackupRecord, "")
        adoptions, adoption_warnings = self._read_category("adoptions", AdoptionRecord, "adoption-")
        adopted_releases, adopted_activations = self._read_adoption_bundles(adoptions)
        records = LifecycleRecords(tuple(sorted((*releases, *adopted_releases), key=lambda item: item.release_id)), tuple(sorted((*activations, *adopted_activations), key=lambda item: (item.activated_at, item.activation_id))), tuple(sorted(backups, key=lambda item: (item.created_at, item.backup_id))), tuple(sorted(adoptions, key=lambda item: item.release_id)), tuple(sorted((*warnings, *activation_warnings, *backup_warnings, *adoption_warnings))))
        return records

    def read(self, *, validate_current: bool = True) -> LifecycleRecords:
        """Return lifecycle records after the established consistency checks."""

        records = self.observe()
        validate_lifecycle_records(records, self.paths)
        if validate_current:
            _validate_current(records, self)
        return records

    def _read_category(self, category: str, parser: type[ReleaseRecord] | type[ActivationRecord] | type[BackupRecord] | type[AdoptionRecord], prefix: str):
        directory = self._directory(category)
        if not directory.exists() and not directory.is_symlink(): return [], []
        _safe_directory(directory, self.owner_uid)
        records, warnings = [], []
        for entry in self._bounded_category_entries(directory, category):
            identifier = _record_identifier(category, entry.name, prefix)
            if identifier is None:
                warnings.append(f"unrecognized deployment storage entry: {category}/{entry.name}"); continue
            _safe_file(entry, self.owner_uid)
            try:
                parsed = parser.from_mapping(_read_json(entry, self.owner_uid, "lifecycle record"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, LifecycleError) as error:
                raise LifecycleError("invalid lifecycle record") from error
            if _record_id(parsed) != identifier: raise LifecycleError("lifecycle record filename does not match metadata")
            records.append(parsed)
        return records, warnings

    @staticmethod
    def _bounded_category_entries(directory: Path, category: str) -> tuple[Path, ...]:
        """Read a fixed page before ordering record evidence deterministically.

        Sorting a whole authority directory needs an unbounded list. Refuse
        on the first entry beyond the fixed page instead, then sort only that
        bounded page for stable warning and record order.
        """

        entries: list[Path] = []
        try:
            with os.scandir(directory) as scanner:
                for item in scanner:
                    if len(entries) >= _MAX_RECORD_ENTRIES:
                        raise LifecycleError(
                            f"lifecycle {category} inventory exceeds limit: "
                            f"at least {_MAX_RECORD_ENTRIES + 1} entries; limit {_MAX_RECORD_ENTRIES}"
                        )
                    entries.append(Path(item.path))
        except LifecycleError:
            raise
        except OSError as error:
            raise LifecycleError("unable to inspect lifecycle record directory") from error
        return tuple(sorted(entries, key=lambda item: item.name))

    def _read_adoption_bundles(
        self,
        adoptions: list[AdoptionRecord],
    ) -> tuple[list[ReleaseRecord], list[ActivationRecord]]:
        if not adoptions:
            return [], []
        root = self._directory("adoption-transactions")
        _safe_directory(root, self.owner_uid)
        releases: list[ReleaseRecord] = []
        activations: list[ActivationRecord] = []
        for adoption in adoptions:
            bundle = root / f"adoption-{adoption.release_id}"
            _safe_directory(bundle, self.owner_uid)
            release = _read_bundle_record(bundle / "release.json", ReleaseRecord, self.owner_uid)
            activation = _read_bundle_record(bundle / "activation.json", ActivationRecord, self.owner_uid)
            # Marker/bundle agreement is lifecycle acceptance, not parsing:
            # raw observations must be able to describe an interrupted or
            # contradictory adoption for the consuming operation to refuse.
            releases.append(release)
            activations.append(activation)
        return releases, activations


_LOCK_METADATA_LINE = re.compile(
    r"([0-9a-f]{32})\|([a-z][a-z-]{0,63})\|([1-9][0-9]*)\|([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)\|(shared|exclusive)\Z"
)
_MAX_LOCK_HOLDERS = 64


def _acquire_metadata_lock(descriptor: int, deadline: float) -> bool:
    """Acquire metadata without making a read wait past the invocation budget."""

    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _read_lock_holders(descriptor: int) -> list[tuple[str, LifecycleLockHolder]]:
    """Parse the exact shell sidecar, retaining only demonstrably live holders."""

    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        payload = bytearray()
        while len(payload) <= MAX_RECORD_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_RECORD_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
    except OSError as error:
        raise LifecycleError("unable to read lifecycle lock holder metadata") from error
    if len(payload) > MAX_RECORD_BYTES:
        raise LifecycleError("lifecycle lock holder metadata is oversized")
    if not payload:
        return []
    try:
        lines = payload.decode("ascii", "strict").splitlines()
    except UnicodeDecodeError as error:
        raise LifecycleError("lifecycle lock holder metadata is invalid") from error
    if len(lines) > _MAX_LOCK_HOLDERS:
        raise LifecycleError("lifecycle lock holder metadata is invalid")
    result: list[tuple[str, LifecycleLockHolder]] = []
    for line in lines:
        match = _LOCK_METADATA_LINE.fullmatch(line)
        if match is None:
            raise LifecycleError("lifecycle lock holder metadata is invalid")
        token, operation, pid, started_at, mode = match.groups()
        try:
            _timestamp(started_at, "lock holder start time")
        except LifecycleError as error:
            raise LifecycleError("lifecycle lock holder metadata is invalid") from error
        result.append((token, LifecycleLockHolder(operation, int(pid), started_at, mode)))
    return result


def _live_lock_holders(
    holders: list[tuple[str, LifecycleLockHolder]],
) -> list[tuple[str, LifecycleLockHolder]]:
    result: list[tuple[str, LifecycleLockHolder]] = []
    for token, holder in holders:
        try:
            os.kill(holder.pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        except OSError as error:
            if error.errno == errno.ESRCH:
                continue
            raise LifecycleError("unable to verify lifecycle lock holder metadata") from error
        result.append((token, holder))
    return result


def _write_lock_holders(
    descriptor: int,
    holders: list[tuple[str, LifecycleLockHolder]],
) -> None:
    if len(holders) > _MAX_LOCK_HOLDERS:
        raise LifecycleError("lifecycle lock holder metadata is invalid")
    encoded = "".join(
        f"{token}|{holder.operation}|{holder.pid}|{holder.started_at}|{holder.mode}\n"
        for token, holder in holders
    ).encode("ascii")
    if len(encoded) > MAX_RECORD_BYTES:
        raise LifecycleError("lifecycle lock holder metadata is oversized")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
    except OSError as error:
        raise LifecycleError("unable to write lifecycle lock holder metadata") from error


def _representative_lock_holder(
    holders: list[tuple[str, LifecycleLockHolder]],
) -> LifecycleLockHolder | None:
    if not holders:
        return None
    return min((holder for _token, holder in holders), key=lambda value: (value.started_at, value.pid, value.operation, value.mode))


def _record_identifier(category: str, name: str, prefix: str) -> str | None:
    if not name.startswith(prefix) or not name.endswith(".json"): return None
    value = name[len(prefix):-5]
    try:
        if category in {"releases", "adoptions"}: return _release(value)
        if category == "activations": return _activation(value)
        return _backup_id(value)
    except LifecycleError: return None


def _record_id(record: object) -> str:
    if isinstance(record, ReleaseRecord) or isinstance(record, AdoptionRecord): return record.release_id
    if isinstance(record, ActivationRecord): return record.activation_id
    if isinstance(record, BackupRecord): return record.backup_id
    raise TypeError("unknown record")


def _read_bundle_record(
    path: Path,
    parser: type[ReleaseRecord] | type[ActivationRecord],
    owner_uid: int,
) -> ReleaseRecord | ActivationRecord:
    _safe_file(path, owner_uid)
    try:
        return parser.from_mapping(_read_json(path, owner_uid, "adoption bundle record"))
    except LifecycleError as error:
        raise LifecycleError("invalid adoption bundle record") from error


def _safe_directory(path: Path, owner_uid: int) -> None:
    try: details = path.lstat()
    except OSError as error: raise LifecycleError("unable to inspect lifecycle record directory") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != owner_uid or details.st_mode & 0o7022:
        raise LifecycleError("lifecycle record directory is unsafe")


def _safe_file(path: Path, owner_uid: int) -> None:
    try: details = path.lstat()
    except OSError as error: raise LifecycleError("unable to inspect lifecycle record") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_uid != owner_uid or stat.S_IMODE(details.st_mode) != 0o600:
        raise LifecycleError("lifecycle record is unsafe")


def _safe_release_marker(path: Path, owner_uid: int) -> None:
    """Validate the immutable release marker without following its path."""

    try:
        details = path.lstat()
    except OSError as error:
        raise LifecycleError("release marker is unreadable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
    ):
        raise LifecycleError("release marker is unsafe")


def _read_release_marker(path: Path, owner_uid: int) -> object:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != owner_uid
            or details.st_mode & 0o7022
            or details.st_size > MAX_RECORD_BYTES
        ):
            raise LifecycleError("release marker is unsafe")
        payload = bytearray()
        while len(payload) <= MAX_RECORD_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_RECORD_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > MAX_RECORD_BYTES:
            raise LifecycleError("release marker is oversized")
        return json.loads(bytes(payload).decode("utf-8"))
    except LifecycleError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LifecycleError("invalid release marker") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


_STAGED_MANIFEST_FIELDS = frozenset(
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


def _validate_staged_manifest(value: object, release_id: str) -> None:
    """Require the exact immutable artifact-manifest identity before staging."""

    if not isinstance(value, Mapping) or set(value) != _STAGED_MANIFEST_FIELDS:
        raise LifecycleError("immutable staging manifest is invalid")
    if (
        value.get("schema_version") != 2
        or value.get("application") != "taskman"
        or value.get("release_id") != release_id
        or value.get("target_os") != "ubuntu26.04"
        or value.get("architecture") != "amd64"
        or value.get("otp_version") != "27.3.4.6"
        or value.get("elixir_version") != "1.18.3"
        or value.get("node_version") != "22.22.1"
        or value.get("hex_version") != "2.5.1"
        or value.get("rebar3_version") != "3.24.0"
        or value.get("builder_base_tag") != "ubuntu:resolute-20260811.1"
        or value.get("builder_base_digest") != "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b"
        or value.get("top_level") != "taskman"
    ):
        raise LifecycleError("immutable staging manifest is invalid")
    application_version = value.get("application_version")
    source_revision = value.get("source_revision")
    if (
        type(application_version) is not str
        or _VERSION_RE.fullmatch(application_version) is None
        or type(source_revision) is not str
        or re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", source_revision) is None
        or release_id != f"{application_version}-{source_revision[:12]}-ubuntu26.04-amd64-otp27.3.4.6"
    ):
        raise LifecycleError("immutable staging manifest is invalid")
    _timestamp(value.get("built_at"), "immutable staging manifest time")
    _migrations(value.get("migrations"))


def _owned_regular_executable(path: Path, owner_uid: int) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(details.st_mode)
        and not stat.S_ISLNK(details.st_mode)
        and details.st_uid == owner_uid
        and not details.st_mode & 0o7022
        and not details.st_mode & 0o7000
        and bool(details.st_mode & 0o100)
    )


def _manual_application(selected: Path, owner_uid: int) -> tuple[str, tuple[Mapping[str, object], ...]]:
    library = selected / "lib"
    try:
        _safe_directory(library, owner_uid)
        matches = tuple(sorted(library.glob("taskman-*/ebin/taskman.app")))
    except OSError as error:
        raise LifecycleError("manual release application metadata is invalid") from error
    if len(matches) != 1:
        raise LifecycleError("manual release application metadata is invalid")
    app = matches[0]
    _safe_file_tree_entry(app, owner_uid)
    match = re.search(r'vsn,"([0-9][0-9A-Za-z.+-]*)"', _read_tree_text(app))
    if match is None:
        raise LifecycleError("manual release application version is invalid")
    version = match.group(1)
    migrations_root = library / f"taskman-{version}" / "priv" / "repo" / "migrations"
    _safe_directory(migrations_root, owner_uid)
    migrations: list[Mapping[str, object]] = []
    for migration in sorted(migrations_root.iterdir(), key=lambda entry: entry.name):
        _safe_file_tree_entry(migration, owner_uid)
        if re.fullmatch(r"[0-9]{14}_[a-z0-9_]+\.exs", migration.name) is None:
            raise LifecycleError("manual release migration is invalid")
        migrations.append({"filename": migration.name, "sha256": hashlib.sha256(migration.read_bytes()).hexdigest()})
    return version, _migrations(migrations)


def _read_tree_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise LifecycleError("manual release application metadata is unreadable") from error


def _safe_file_tree_entry(path: Path, owner_uid: int) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise LifecycleError("manual release entry is unreadable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != owner_uid
        or details.st_mode & 0o7022
        or details.st_mode & 0o7000
    ):
        raise LifecycleError("manual release entry is unsafe")


def _tree_digest(root: Path, owner_uid: int) -> str:
    entries: list[tuple[str, os.stat_result]] = []
    for directory, names, files in os.walk(root, followlinks=False):
        path = Path(directory)
        try:
            details = path.lstat()
        except OSError as error:
            raise LifecycleError("manual release tree is unreadable") from error
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != owner_uid or details.st_mode & 0o7022 or details.st_mode & 0o7000:
            raise LifecycleError("manual release tree is unsafe")
        relative = path.relative_to(root).as_posix()
        entries.append((f"d:{relative}", details))
        for name in sorted((*names, *files)):
            entry = path / name
            try:
                entry_details = entry.lstat()
            except OSError as error:
                raise LifecycleError("manual release tree is unreadable") from error
            if stat.S_ISLNK(entry_details.st_mode) or entry_details.st_uid != owner_uid or entry_details.st_mode & 0o7022 or entry_details.st_mode & 0o7000:
                raise LifecycleError("manual release tree is unsafe")
            if stat.S_ISDIR(entry_details.st_mode):
                continue
            if not stat.S_ISREG(entry_details.st_mode):
                raise LifecycleError("manual release tree is unsafe")
            entries.append((f"f:{entry.relative_to(root).as_posix()}", entry_details))
    if len(entries) > 4_096:
        raise LifecycleError("manual release tree is too large to inspect")
    digest = hashlib.sha256()
    for identifier, details in sorted(entries):
        digest.update(f"{identifier}|{stat.S_IMODE(details.st_mode):o}|{details.st_uid}\0".encode("utf-8"))
        if identifier.startswith("f:"):
            relative = identifier[2:]
            try:
                with (root / relative).open("rb") as stream:
                    while chunk := stream.read(8192):
                        digest.update(chunk)
            except OSError as error:
                raise LifecycleError("manual release tree is unreadable") from error
    return digest.hexdigest()


def _read_json(path: Path, owner_uid: int, label: str) -> object:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != owner_uid
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_size > MAX_RECORD_BYTES
        ):
            raise LifecycleError(f"{label} is unsafe")
        payload = bytearray()
        while len(payload) <= MAX_RECORD_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_RECORD_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > MAX_RECORD_BYTES:
            raise LifecycleError(f"{label} is oversized")
        return json.loads(bytes(payload).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LifecycleError(f"invalid {label}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    pending = memoryview(payload)
    while pending:
        written = os.write(descriptor, pending)
        if written <= 0: raise OSError("short lifecycle record write")
        pending = pending[written:]


def validate_lifecycle_records(records: LifecycleRecords, paths: ManagedPaths) -> None:
    releases = {record.release_id: record for record in records.releases}
    backups = {record.backup_id for record in records.backups}
    if len(releases) != len(records.releases) or len(backups) != len(records.backups) or len({record.activation_id for record in records.activations}) != len(records.activations) or len({record.release_id for record in records.adoptions}) != len(records.adoptions): raise LifecycleError("duplicate lifecycle identifiers")
    if not {record.release_id for record in records.adoptions}.issubset(releases): raise LifecycleError("adopted release has no release record")
    for backup in records.backups:
        if not _under(backup.dump_path, paths.backup_root) or not {item for item in (backup.current_release_id, backup.candidate_release_id) if item is not None}.issubset(releases): raise LifecycleError("backup record contradicts lifecycle state")
    previous, last_time, first = None, None, {}
    for activation in records.activations:
        if (last_time is not None and activation.activated_at <= last_time) or activation.previous_release_id != previous or activation.candidate_release_id not in releases or (activation.backup_id is not None and activation.backup_id not in backups): raise LifecycleError("activation chain is broken")
        first.setdefault(activation.candidate_release_id, activation); previous, last_time = activation.candidate_release_id, activation.activated_at
    for release in records.releases:
        activation = first.get(release.release_id)
        if release.previous_release_id is not None and release.previous_release_id not in releases or release.backup_id is not None and release.backup_id not in backups: raise LifecycleError("release references unknown lifecycle state")
        if activation is None:
            if release.activated_at is not None or release.previous_release_id is not None or release.backup_id is not None: raise LifecycleError("release activation fields contradict history")
        elif (release.activated_at, release.previous_release_id, release.backup_id, release.migration_policy) != (activation.activated_at, activation.previous_release_id, activation.backup_id, activation.migration_policy): raise LifecycleError("release activation fields contradict history")
    for adoption in records.adoptions:
        release, activation = releases[adoption.release_id], first.get(adoption.release_id)
        if adoption.release_id != f"{adoption.application_version}-{adoption.content_sha256[:12]}-ubuntu26.04-amd64-otp27.3.4.6" or not _under(adoption.release_path, paths.release_root) or release.artifact_sha256 is not None or release.migration_policy != "adopted" or activation is None or activation.migration_policy != "adopted" or any(item != adoption.adopted_at for item in (release.installed_at, release.activated_at, activation.activated_at)): raise LifecycleError("adoption record conflicts with lifecycle state")


def _validate_current(records: LifecycleRecords, store: LifecycleStore) -> None:
    current, selected = store.current_link, records.current_release_id
    if selected is None:
        if current.exists() or current.is_symlink():
            raise LifecycleError("current selection exists without an activation record")
        return
    try:
        details = current.lstat()
        if not stat.S_ISLNK(details.st_mode): raise LifecycleError("current selection is not a symlink")
        target = current.resolve(strict=True); root = store.release_root.resolve(strict=True)
        adoption_paths = {record.release_id: Path(record.release_path.as_posix()) for record in records.adoptions}
        expected = adoption_paths.get(selected, store.release_root / selected).resolve(strict=True)
        target.relative_to(root)
    except LifecycleError: raise
    except (OSError, ValueError) as error: raise LifecycleError("current selection is invalid") from error
    if target != expected: raise LifecycleError("current selection conflicts with activation records")


def rollback_eligibility(records: LifecycleRecords, current_release_id: str, target_release_id: str) -> tuple[bool, str | None]:
    try: _release(current_release_id); _release(target_release_id)
    except LifecycleError: return False, "release identifier is invalid"
    if current_release_id == target_release_id: return False, "target release is already current"
    if records.current_release_id != current_release_id: return False, "current release does not match activation records"
    cursor = current_release_id
    for activation in reversed(records.activations):
        if activation.candidate_release_id != cursor: return False, "activation chain is incomplete"
        if activation.migration_policy == "restore-required": return False, f"activation {activation.activation_id} requires database restore"
        if activation.previous_release_id == target_release_id: return True, None
        if activation.previous_release_id is None: break
        cursor = activation.previous_release_id
    return False, "target release is not connected to the current activation chain"


__all__ = ["ActivationRecord", "AdoptionRecord", "BackupRecord", "LifecycleError", "LifecycleFinalizationEffect", "LifecycleFinalizationFailure", "LifecycleLockContention", "LifecycleLockHolder", "LifecycleRecords", "LifecycleStore", "LifecycleWriteEffect", "LifecycleWriteFailure", "ManualAdoptionCandidate", "ReleaseRecord", "SCHEMA_VERSION", "StagedRelease", "rollback_eligibility", "validate_lifecycle_records"]
