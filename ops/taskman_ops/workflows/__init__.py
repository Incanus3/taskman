"""Operator-command workflows assembled from focused lifecycle capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import ExitStatus
from ..output import render_table


@dataclass(frozen=True)
class DiscoveryResult:
    """The common, versioned shape shared by human and JSON discovery."""

    records: tuple[Mapping[str, object], ...]
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> ExitStatus:
        return ExitStatus.OK

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "ok",
            "records": [dict(record) for record in self.records],
            "warnings": list(self.warnings),
        }

    def human(self) -> str:
        body = render_table(list(self.records)) if self.records else "No managed records."
        if not self.warnings:
            return body
        return "\n".join((body, "Warnings:", *(f"- {warning}" for warning in self.warnings)))


__all__ = ["DiscoveryResult"]
