"""Pure, exact eligibility planning for destructive lifecycle cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from ..errors import ExitStatus, OpsError
from .identifiers import validate_release_id
from .records import BackupRecord, LifecycleRecords


_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_STAGING_ID_RE = re.compile(r"stage-[0-9a-f]{32}\Z")
_RECOVERY_ID_RE = re.compile(r"recovery-[0-9a-f]{32}\Z")
_KINDS = frozenset({"release", "backup", "staging", "database"})


@dataclass(frozen=True)
class StagingAuthority:
    """A root-owned completion receipt for one otherwise stale staging file."""

    record_path: PurePosixPath
    state: str

    def __post_init__(self) -> None:
        _safe_authority_path(self.record_path)
        if self.state != "completed":
            raise ValueError("cleanup staging authority state is invalid")

    def to_mapping(self) -> dict[str, str]:
        return {"record_path": self.record_path.as_posix(), "state": self.state}


@dataclass(frozen=True)
class RecoveryAuthority:
    """The immutable restore-record relationship that authorizes one DB target."""

    record_path: PurePosixPath
    source_backup_id: str
    pre_restore_backup_id: str
    intended_release_id: str
    recovery_database: str
    state: str

    def __post_init__(self) -> None:
        _safe_authority_path(self.record_path)
        if _BACKUP_ID_RE.fullmatch(self.source_backup_id) is None or _BACKUP_ID_RE.fullmatch(self.pre_restore_backup_id) is None:
            raise ValueError("cleanup recovery backup authority is invalid")
        validate_release_id(self.intended_release_id)
        if not re.fullmatch(r"taskman_recovery_[0-9a-f]{32}", self.recovery_database):
            raise ValueError("cleanup recovery database authority is invalid")
        if self.state != "retained":
            raise ValueError("cleanup recovery state is invalid")

    def to_mapping(self) -> dict[str, str]:
        return {
            "record_path": self.record_path.as_posix(),
            "source_backup_id": self.source_backup_id,
            "pre_restore_backup_id": self.pre_restore_backup_id,
            "intended_release_id": self.intended_release_id,
            "recovery_database": self.recovery_database,
            "state": self.state,
        }


@dataclass(frozen=True)
class CleanupTarget:
    """One immutable, reviewed deletion target and its durable authority.

    Database targets retain a synthetic, non-filesystem ``path`` for one
    uniform reporting shape.  The on-host transaction never turns it into a
    shell path; it operates on the separately validated identifier only.
    """

    kind: str
    identifier: str
    path: PurePosixPath
    recoverable: bool
    authority: StagingAuthority | RecoveryAuthority | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError("cleanup target kind is invalid")
        if not isinstance(self.identifier, str):
            raise ValueError("cleanup target identifier is invalid")
        if not isinstance(self.path, PurePosixPath) or not self.path.is_absolute():
            raise ValueError("cleanup target path is invalid")
        if any(part in {"", ".", ".."} for part in self.path.parts[1:]) or any(
            char in self.path.as_posix() for char in "*?[]${}\\\n\r"
        ):
            raise ValueError("cleanup target path is unsafe")
        if type(self.recoverable) is not bool:
            raise ValueError("cleanup recoverability must be boolean")
        if self.kind == "release":
            validate_release_id(self.identifier)
        elif self.kind == "backup" and _BACKUP_ID_RE.fullmatch(self.identifier) is None:
            raise ValueError("cleanup backup identifier is invalid")
        elif self.kind == "staging" and _STAGING_ID_RE.fullmatch(self.identifier) is None:
            raise ValueError("cleanup staging identifier is invalid")
        elif self.kind == "database" and _RECOVERY_ID_RE.fullmatch(self.identifier) is None:
            raise ValueError("cleanup recovery identifier is invalid")
        if self.kind == "database":
            if not isinstance(self.authority, RecoveryAuthority):
                raise ValueError("cleanup database target requires restore-record authority")
            if self.authority.recovery_database != "taskman_" + self.identifier.replace("-", "_"):
                raise ValueError("cleanup recovery database authority contradicts identifier")
        elif self.kind == "staging":
            if not isinstance(self.authority, StagingAuthority) or self.authority.record_path != self.path:
                raise ValueError("cleanup staging target requires completion authority")
        elif self.authority is not None:
            raise ValueError("cleanup file target cannot carry unrelated authority")


@dataclass(frozen=True)
class CleanupPlan:
    """A deterministic, confirmation-ready collection of exact targets."""

    targets: tuple[CleanupTarget, ...]
    protected_release_ids: tuple[str, ...]
    protected_backup_ids: tuple[str, ...]


def cleanup_plan(
    records: LifecycleRecords,
    *,
    release_root: PurePosixPath,
    deployment_root: PurePosixPath,
    backup_root: PurePosixPath,
    release_retention: int,
    backup_retention: int,
    completed_staging: tuple[CleanupTarget, ...],
    retained_databases: tuple[CleanupTarget, ...],
) -> CleanupPlan:
    """Compute a conservative immutable target list from strict records.

    A lifecycle record, not a name derived from storage, is the authority for
    release and backup deletion.  The caller can add staging/database entries
    only after its host transaction proved those tool-owned entries complete.
    """

    if not isinstance(records, LifecycleRecords):
        raise TypeError("cleanup planning requires lifecycle records")
    _safe_root(release_root, "release")
    _safe_root(deployment_root, "deployment")
    _safe_root(backup_root, "backup")
    if type(release_retention) is not int or release_retention < 1:
        raise ValueError("release retention must be a positive integer")
    if type(backup_retention) is not int or backup_retention < 1:
        raise ValueError("backup retention must be a positive integer")

    releases = {record.release_id: record for record in records.releases}
    current = records.current_release_id
    if current is None or current not in releases:
        raise _safety("cleanup requires one authoritative current release")
    activations = tuple(sorted(records.activations, key=lambda value: (value.activated_at, value.activation_id)))
    if not activations or activations[-1].candidate_release_id != current:
        raise _safety("cleanup activation history is contradictory")

    previous = activations[-1].previous_release_id
    protected_releases = {current}
    if previous is not None:
        protected_releases.add(previous)
    protected_releases.update(
        value.candidate_release_id for value in activations[-release_retention:]
    )

    backups = tuple(sorted(records.backups, key=lambda value: (value.created_at, value.backup_id), reverse=True))
    protected_backups = {value.backup_id for value in backups[:backup_retention]}
    newest_predeploy: dict[tuple[str | None, str | None], BackupRecord] = {}
    for backup in backups:
        if backup.reason != "pre-deploy":
            continue
        pair = (backup.current_release_id, backup.candidate_release_id)
        newest_predeploy.setdefault(pair, backup)
    protected_backups.update(value.backup_id for value in newest_predeploy.values())

    staging_root = deployment_root / "uploads"
    staging_targets = _validated_extra_targets(completed_staging, "staging", staging_root)
    database_targets = _validated_extra_targets(retained_databases, "database", None)
    for target in database_targets:
        if not isinstance(target.authority, RecoveryAuthority):  # guarded in CleanupTarget; keeps the proof local.
            raise _safety("cleanup recovery authority is invalid")
        protected_backups.update({target.authority.source_backup_id, target.authority.pre_restore_backup_id})
        protected_releases.add(target.authority.intended_release_id)

    # Every retained backup must still have an executable code counterpart.
    # This conservatively includes restore authority, even when its activation
    # edge is too old for ordinary code-only rollback.
    for backup in backups:
        if backup.backup_id in protected_backups:
            if backup.current_release_id is not None:
                protected_releases.add(backup.current_release_id)
            if backup.candidate_release_id is not None:
                protected_releases.add(backup.candidate_release_id)

    targets: list[CleanupTarget] = []
    for release_id in sorted(releases):
        expected = release_root / release_id
        if release_id not in protected_releases:
            targets.append(CleanupTarget("release", release_id, expected, False))
    for backup in backups:
        if backup.backup_id in protected_backups:
            continue
        if not backup.dump_path.is_relative_to(backup_root):
            raise _safety("backup record escapes the managed backup root")
        targets.append(CleanupTarget("backup", backup.backup_id, backup.dump_path, False))

    targets.extend(staging_targets)
    targets.extend(database_targets)
    # Delete ordinary file artifacts first.  A retained database is always
    # last, so a later database-drop failure leaves every earlier recovery
    # artifact in place and the partial report remains maximally useful.
    targets.sort(key=lambda value: (value.recoverable, value.kind, value.identifier, value.path.as_posix()))
    if len({(value.kind, value.identifier) for value in targets}) != len(targets):
        raise _safety("cleanup target set contains duplicate authority")
    return CleanupPlan(
        targets=tuple(targets),
        protected_release_ids=tuple(sorted(protected_releases)),
        protected_backup_ids=tuple(sorted(protected_backups)),
    )


def _validated_extra_targets(
    values: tuple[CleanupTarget, ...],
    kind: str,
    root: PurePosixPath | None,
) -> tuple[CleanupTarget, ...]:
    checked: list[CleanupTarget] = []
    for target in values:
        if not isinstance(target, CleanupTarget) or target.kind != kind:
            raise _safety("cleanup target has no matching tool-owned authority")
        if root is not None and not target.path.is_relative_to(root):
            raise _safety("cleanup target escapes its managed root")
        if kind == "database":
            if target.path != PurePosixPath("/database") / target.identifier or not isinstance(target.authority, RecoveryAuthority):
                raise _safety("cleanup recovery database target is invalid")
        if kind == "staging" and not isinstance(target.authority, StagingAuthority):
            raise _safety("cleanup staging target is invalid")
        checked.append(target)
    return tuple(checked)


def _safe_root(value: PurePosixPath, label: str) -> None:
    if not isinstance(value, PurePosixPath) or not value.is_absolute() or len(value.parts) < 3:
        raise ValueError(f"{label} cleanup root is invalid")
    if any(part in {"", ".", ".."} for part in value.parts[1:]):
        raise ValueError(f"{label} cleanup root is invalid")


def _safe_authority_path(value: PurePosixPath) -> None:
    if not isinstance(value, PurePosixPath) or not value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts[1:]):
        raise ValueError("cleanup authority path is invalid")
    if any(char in {"*", "?", "[", "]", "$", "{", "}", "\\", "\n", "\r"} for char in value.as_posix()):
        raise ValueError("cleanup authority path is unsafe")


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "cleanup",
        message,
        changed=False,
        next_action="inspect the managed lifecycle records and exact storage state before retrying",
    )


__all__ = ["CleanupPlan", "CleanupTarget", "RecoveryAuthority", "StagingAuthority", "cleanup_plan"]
