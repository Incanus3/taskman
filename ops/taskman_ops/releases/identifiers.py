"""Strict release identities and validated release paths.

Release IDs are accepted from operator input in later workflows, so this
module deliberately accepts only the one target that Taskman currently
supports.  Callers validate an ID before combining it with a release root.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re

from .toolchains import CURRENT_RUNTIME, SUPPORTED_RUNTIMES, runtime_for_otp_version


APPLICATION_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")
SOURCE_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
RELEASE_ID_RE = re.compile(
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)-"
    r"(?P<revision>[0-9a-f]{12})-ubuntu26\.04-amd64-otp"
    + "(?P<otp_version>"
    + "|".join(re.escape(runtime.otp_version) for runtime in SUPPORTED_RUNTIMES)
    + r")\Z"
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


def build_release_id(
    application_version: str,
    source_revision: str,
    *,
    otp_version: str = CURRENT_RUNTIME.otp_version,
) -> str:
    """Build the stable logical release identity from audited source inputs."""

    version = validate_application_version(application_version)
    revision = validate_source_revision(source_revision)
    runtime = runtime_for_otp_version(otp_version)
    return f"{version}-{revision[:12]}-ubuntu26.04-amd64-otp{runtime.otp_version}"


def validate_release_id(value: str) -> str:
    """Validate one exact supported release identity before path use."""

    if not isinstance(value, str) or RELEASE_ID_RE.fullmatch(value) is None:
        raise ValueError("invalid release identifier")
    return value


def release_otp_version(value: str) -> str:
    """Read the exact allowlisted OTP runtime encoded in one validated ID."""

    if not isinstance(value, str):
        raise ValueError("invalid release identifier")
    match = RELEASE_ID_RE.fullmatch(value)
    if match is None:
        raise ValueError("invalid release identifier")
    return runtime_for_otp_version(match.group("otp_version")).otp_version


def release_application_version(value: str) -> str:
    """Read the validated application version without re-parsing a release ID."""

    if not isinstance(value, str):
        raise ValueError("invalid release identifier")
    match = RELEASE_ID_RE.fullmatch(value)
    if match is None:
        raise ValueError("invalid release identifier")
    return match.group("version")


def managed_release_path(root: PurePosixPath, release_id: str) -> PurePosixPath:
    """Join a strict release ID below a prevalidated POSIX release root."""

    if not isinstance(root, PurePosixPath) or not root.is_absolute():
        raise ValueError("release root must be an absolute POSIX path")
    return root / validate_release_id(release_id)


__all__ = [
    "APPLICATION_VERSION_RE",
    "RELEASE_ID_RE",
    "SOURCE_REVISION_RE",
    "build_release_id",
    "managed_release_path",
    "release_application_version",
    "release_otp_version",
    "validate_application_version",
    "validate_release_id",
    "validate_source_revision",
]
