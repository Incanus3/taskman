"""Canonical migration filenames and version sequences shared by controller and host code."""

import re


MIGRATION_FILENAME_RE = re.compile(r"([0-9]{14})_[a-z0-9_]+\.exs\Z")


class MigrationOrderError(ValueError):
    """Migration versions are not sorted and unique."""


def validate_migration_versions(value: object) -> tuple[int, ...]:
    """Validate history without sorting, deduplicating, or coercing versions."""

    if not isinstance(value, (list, tuple)) or any(type(item) is not int or item < 0 for item in value):
        raise ValueError("invalid migration versions")
    versions = tuple(value)
    if versions != tuple(sorted(set(versions))):
        raise MigrationOrderError("migration versions must be sorted and unique")
    return versions


def versions_from_filenames(value: object) -> tuple[int, ...]:
    """Extract ordered versions from filenames without coercion or normalization."""

    if not isinstance(value, (list, tuple)):
        raise ValueError("invalid migration filenames")
    versions: list[int] = []
    for filename in value:
        match = MIGRATION_FILENAME_RE.fullmatch(filename) if isinstance(filename, str) else None
        if match is None:
            raise ValueError("invalid migration filenames")
        versions.append(int(match.group(1)))
    return validate_migration_versions(versions)
