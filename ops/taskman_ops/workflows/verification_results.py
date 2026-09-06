"""Controller-side parsing of the helper's secret-free verification result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from ..errors import ExitStatus
from ..releases.identifiers import validate_release_id


_CHECK_NAMES = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)
_LIFECYCLE_CHECK_NAMES = frozenset(_CHECK_NAMES[:5])
_READINESS_CHECK_NAMES = frozenset(_CHECK_NAMES[5:])
_FAILED_NEXT_ACTION = (
    "inspect the fixed verification summaries and correct the reported host state before retrying"
)


class CheckStatus(str, Enum):
    """One check's stable, reportable outcome."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class VerificationCheck:
    """A secret-free, versioned individual verification fact."""

    schema_version: ClassVar[int] = 1
    name: str
    status: CheckStatus
    summary: str

    def __post_init__(self) -> None:
        if self.name not in _CHECK_NAMES:
            raise ValueError("invalid verification check name")
        if not isinstance(self.status, CheckStatus):
            raise TypeError("verification check status must be typed")
        if not isinstance(self.summary, str) or not self.summary:
            raise ValueError("verification check summary must be a non-empty string")

    def to_mapping(self) -> dict[str, str | int]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "status": self.status.value,
            "summary": self.summary,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "VerificationCheck":
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "name", "status", "summary"}
            or value.get("schema_version") != cls.schema_version
        ):
            raise ValueError("verification check must use the exact schema")
        try:
            status = CheckStatus(value["status"])
        except (TypeError, ValueError):
            raise ValueError("verification check status is invalid") from None
        return cls(value["name"], status, value["summary"])  # type: ignore[arg-type]


@dataclass(frozen=True)
class VerificationReport:
    """The complete, secret-free outcome of one helper verification."""

    schema_version: ClassVar[int] = 1
    exit_status: ExitStatus
    release_id: str | None
    expected_release_id: str | None
    checks: tuple[VerificationCheck, ...]
    next_action: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.exit_status, ExitStatus):
            raise TypeError("verification report exit status must be typed")
        for release_id in (self.release_id, self.expected_release_id):
            if release_id is not None:
                validate_release_id(release_id)
        if not isinstance(self.checks, tuple) or not all(
            isinstance(check, VerificationCheck) for check in self.checks
        ):
            raise TypeError("verification report checks must be typed")
        names = tuple(check.name for check in self.checks)
        if not names or names != _CHECK_NAMES[: len(names)]:
            raise ValueError("verification report checks must be a non-empty ordered unique prefix")
        if any(check.status is CheckStatus.SKIPPED for check in self.checks):
            raise ValueError("verification report checks must not skip required evidence")
        if self.exit_status is ExitStatus.OK:
            if names != _CHECK_NAMES or any(
                check.status is not CheckStatus.PASSED for check in self.checks
            ):
                raise ValueError("successful report requires the complete required check set to pass")
            if self.next_action is not None:
                raise ValueError("successful report must not include a next action")
            return
        if self.exit_status not in {ExitStatus.RELEASE, ExitStatus.READINESS}:
            raise ValueError("verification report status must be release, readiness, or ok")
        if self.next_action != _FAILED_NEXT_ACTION:
            raise ValueError("failed report requires the fixed next action")
        failed_names = {
            check.name for check in self.checks if check.status is CheckStatus.FAILED
        }
        if not failed_names:
            raise ValueError("failed report requires a failed check")
        if self.exit_status is ExitStatus.RELEASE:
            if len(self.checks) > len(_LIFECYCLE_CHECK_NAMES) or not failed_names <= _LIFECYCLE_CHECK_NAMES:
                raise ValueError("release status must match a lifecycle failure")
        elif (
            len(self.checks) < len(_LIFECYCLE_CHECK_NAMES) + 1
            or any(
                check.status is not CheckStatus.PASSED
                for check in self.checks[: len(_LIFECYCLE_CHECK_NAMES)]
            )
            or not failed_names <= _READINESS_CHECK_NAMES
        ):
            raise ValueError("readiness status must match a readiness failure")

    @property
    def successful(self) -> bool:
        return self.exit_status is ExitStatus.OK

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": "ok" if self.successful else "failed",
            "exit_status": self.exit_status.value,
            "release_id": self.release_id,
            "expected_release_id": self.expected_release_id,
            "checks": [check.to_mapping() for check in self.checks],
            "next_action": self.next_action,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "VerificationReport":
        expected = {
            "schema_version",
            "status",
            "exit_status",
            "release_id",
            "expected_release_id",
            "checks",
            "next_action",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != expected
            or value.get("schema_version") != cls.schema_version
        ):
            raise ValueError("verification report must use the exact schema")
        if type(value.get("exit_status")) is not int:
            raise TypeError("verification report exit status must be an integer")
        try:
            exit_status = ExitStatus(value["exit_status"])
        except (TypeError, ValueError):
            raise ValueError("verification report exit status is invalid") from None
        expected_status = "ok" if exit_status is ExitStatus.OK else "failed"
        if value.get("status") != expected_status:
            raise ValueError("verification report status conflicts with its exit status")
        checks_value = value.get("checks")
        if not isinstance(checks_value, list):
            raise TypeError("verification report checks must be a list")
        checks = tuple(VerificationCheck.from_mapping(check) for check in checks_value)
        return cls(
            exit_status,
            value.get("release_id"),  # type: ignore[arg-type]
            value.get("expected_release_id"),  # type: ignore[arg-type]
            checks,
            value.get("next_action"),  # type: ignore[arg-type]
        )


__all__ = ["CheckStatus", "VerificationCheck", "VerificationReport"]
