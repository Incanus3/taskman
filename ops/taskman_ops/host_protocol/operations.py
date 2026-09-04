"""The finite host-helper operation vocabulary.

Operation policy is deliberately not implemented here.  Later helper slices attach their
host-local policy to these names, while the shared protocol retains one exact dispatch boundary.
"""

from __future__ import annotations

from .identifiers import ProtocolError


OPERATION_NAMES = frozenset(
    {
        "backup",
        "cleanup",
        "deploy",
        "discover",
        "genesis",
        "list_backups",
        "list_releases",
        "restore",
        "rollback",
        "verify",
    }
)


def validate_operation(value: object) -> str:
    """Validate the operation name without accepting arbitrary helper commands."""

    if type(value) is not str or value not in OPERATION_NAMES:
        raise ProtocolError("invalid operation")
    return value


__all__ = [
    "OPERATION_NAMES",
    "validate_operation",
]
