"""The finite host-helper operation vocabulary.

Operation policy is deliberately not implemented here.  Later helper slices attach their
host-local policy to these names, while the shared protocol retains one exact dispatch boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .identifiers import ProtocolError


@dataclass(frozen=True)
class OperationSpec:
    """One declared operation and whether it may mutate host state."""

    name: str
    mutating: bool


_OPERATION_SPECS = (
    OperationSpec("backup", True),
    OperationSpec("cleanup", True),
    OperationSpec("deploy", True),
    OperationSpec("discover", False),
    OperationSpec("genesis", True),
    OperationSpec("list_backups", False),
    OperationSpec("list_releases", False),
    OperationSpec("restore", True),
    OperationSpec("rollback", True),
    OperationSpec("verify", False),
)

OPERATION_NAMES = tuple(spec.name for spec in _OPERATION_SPECS)
OPERATION_SPECS: Mapping[str, OperationSpec] = MappingProxyType(
    {spec.name: spec for spec in _OPERATION_SPECS}
)


def operation_spec(value: object) -> OperationSpec:
    """Return the exact operation declaration or reject an unknown dispatch target."""

    if type(value) is not str:
        raise ProtocolError("invalid operation")
    try:
        return OPERATION_SPECS[value]
    except KeyError as error:
        raise ProtocolError("invalid operation") from error


def validate_operation(value: object) -> str:
    """Validate the operation name without accepting arbitrary helper commands."""

    return operation_spec(value).name


__all__ = [
    "OPERATION_NAMES",
    "OPERATION_SPECS",
    "OperationSpec",
    "operation_spec",
    "validate_operation",
]
