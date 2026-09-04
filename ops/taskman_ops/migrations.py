"""Canonical migration-version sequences shared by controller and host code."""


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
