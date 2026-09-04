"""Strict release identities and managed release paths.

Release IDs are accepted from operator input in later workflows, so this
module deliberately accepts only the one target that Taskman currently
supports.  Callers validate an ID before combining it with a managed root.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re


APPLICATION_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")
SOURCE_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
RELEASE_ID_RE = re.compile(
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)-"
    r"(?P<revision>[0-9a-f]{12})-ubuntu26\.04-amd64-otp27\.3\.4\.6\Z"
)


def validate_application_version(value: str) -> str:
    """Return a release-safe literal application version."""

    if not isinstance(value, str) or APPLICATION_VERSION_RE.fullmatch(value) is None:
        raise ValueError("invalid application version")
    return value


def validate_source_revision(value: str) -> str:
    """Return a full lowercase Git object ID, never a path-like alias."""

    if not isinstance(value, str) or SOURCE_REVISION_RE.fullmatch(value) is None:
        raise ValueError("invalid source revision")
    return value


def build_release_id(application_version: str, source_revision: str) -> str:
    """Build the stable logical release identity from audited source inputs."""

    version = validate_application_version(application_version)
    revision = validate_source_revision(source_revision)
    return f"{version}-{revision[:12]}-ubuntu26.04-amd64-otp27.3.4.6"


def validate_release_id(value: str) -> str:
    """Validate one exact supported release identity before path use."""

    if not isinstance(value, str) or RELEASE_ID_RE.fullmatch(value) is None:
        raise ValueError("invalid release identifier")
    return value


def managed_release_path(root: PurePosixPath, release_id: str) -> PurePosixPath:
    """Join a strict release ID below a prevalidated managed POSIX root."""

    if not isinstance(root, PurePosixPath) or not root.is_absolute():
        raise ValueError("managed release root must be an absolute POSIX path")
    return root / validate_release_id(release_id)


__all__ = [
    "APPLICATION_VERSION_RE",
    "RELEASE_ID_RE",
    "SOURCE_REVISION_RE",
    "build_release_id",
    "managed_release_path",
    "validate_application_version",
    "validate_release_id",
    "validate_source_revision",
]
