"""Stable public contracts retained while the helper internals migrate."""

from __future__ import annotations

from taskman_ops.cli import build_parser
from taskman_ops.errors import ExitStatus


PUBLIC_COMMANDS = (
    "build", "provision", "deploy", "verify", "releases", "backups", "backup",
    "create-admin", "cleanup", "rollback", "restore",
)


def test_public_command_names_remain_stable() -> None:
    help_text = build_parser().format_help()
    for command in PUBLIC_COMMANDS:
        assert command in help_text


def test_every_public_exit_category_is_retained() -> None:
    assert tuple((status.name, status.value) for status in ExitStatus) == (
        ("OK", 0), ("INVALID", 2), ("LOCAL_PREREQUISITE", 3), ("SECRET", 4),
        ("REMOTE_PREFLIGHT", 5), ("BACKUP", 6), ("MIGRATION", 7), ("RELEASE", 8),
        ("READINESS", 9), ("SAFETY", 10), ("RESTORE", 11), ("LOCKED", 12),
    )
