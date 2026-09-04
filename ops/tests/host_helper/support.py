"""Shared baseline setup for host-helper tests."""

from pathlib import Path

from taskman_ops.host_helper.paths import ManagedPaths


def database_mapping(*, name: str = "taskman") -> dict[str, object]:
    """Describe the common local database connection with an explicit name override."""

    return {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": name}


def verification_settings() -> dict[str, object]:
    return {
        "application_port": 4000,
        "distribution_port": 6789,
        "database_port": 5432,
        "public_hostname": "taskman.example.test",
        "public_ipv4": "203.0.113.10",
        "public_ipv6": None,
        "ssh_port": 22,
        "ssh_user": "deployer",
        "readiness_timeout": 1,
        "connection_timeout": 1,
    }


def managed_paths(tmp_path: Path) -> ManagedPaths:
    """Describe isolated sibling roots without creating directories or state."""

    return ManagedPaths.from_mapping(
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")}
    )
