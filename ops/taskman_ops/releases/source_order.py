"""Bounded local source ordering for deploy/provision acknowledgments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Literal


_SEMVER = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_REVISION = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")

OrderKind = Literal["downgrade", "forward", "equal", "unknown", "no-baseline"]
GitRunner = Callable[[Path, tuple[str, ...]], int]


@dataclass(frozen=True)
class SourceOrder:
    """Machine-consumable classification without controller wording."""

    kind: OrderKind
    reasons: tuple[str, ...]

    @property
    def needs_acknowledgment(self) -> bool:
        return self.kind in {"downgrade", "unknown"}


def _run_git(repo: Path, argv: tuple[str, ...]) -> int:
    try:
        return subprocess.run(("git", "-C", str(repo), *argv), check=False, capture_output=True).returncode
    except OSError:
        return 128


def _version_order(target: str, baseline: str) -> tuple[Literal["downgrade", "forward", "equal", "unknown"], str | None]:
    target_match = _SEMVER.fullmatch(target)
    baseline_match = _SEMVER.fullmatch(baseline)
    if (
        target_match is None
        or baseline_match is None
        or not _valid_prerelease(target_match.group("prerelease"))
        or not _valid_prerelease(baseline_match.group("prerelease"))
    ):
        return "unknown", "version-unorderable"
    target_core = tuple(int(target_match.group(part)) for part in ("major", "minor", "patch"))
    baseline_core = tuple(int(baseline_match.group(part)) for part in ("major", "minor", "patch"))
    if target_core < baseline_core:
        return "downgrade", "version-lower"
    if target_core > baseline_core:
        return "forward", None
    return _prerelease_order(target_match.group("prerelease"), baseline_match.group("prerelease"))


def _valid_prerelease(value: str | None) -> bool:
    if value is None:
        return True
    return all(not (item.isdecimal() and len(item) > 1 and item.startswith("0")) for item in value.split("."))


def _prerelease_order(target: str | None, baseline: str | None) -> tuple[Literal["downgrade", "forward", "equal"], str | None]:
    if target == baseline:
        return "equal", None
    if target is None:
        return "forward", None
    if baseline is None:
        return "downgrade", "version-lower"
    target_items = target.split(".")
    baseline_items = baseline.split(".")
    for target_item, baseline_item in zip(target_items, baseline_items, strict=False):
        if target_item == baseline_item:
            continue
        target_numeric = target_item.isdecimal()
        baseline_numeric = baseline_item.isdecimal()
        if target_numeric and baseline_numeric:
            return ("downgrade", "version-lower") if int(target_item) < int(baseline_item) else ("forward", None)
        if target_numeric != baseline_numeric:
            return ("downgrade", "version-lower") if target_numeric else ("forward", None)
        return ("downgrade", "version-lower") if target_item < baseline_item else ("forward", None)
    return ("downgrade", "version-lower") if len(target_items) < len(baseline_items) else ("forward", None)


def _source_order(repo: Path, target: str, baseline: str, git_runner: GitRunner) -> tuple[Literal["downgrade", "forward", "equal", "unknown"], str | None]:
    if _REVISION.fullmatch(target) is None or _REVISION.fullmatch(baseline) is None:
        return "unknown", "source-unavailable"
    if target == baseline:
        return "equal", None
    target_ancestor = git_runner(repo, ("merge-base", "--is-ancestor", target, baseline))
    baseline_ancestor = git_runner(repo, ("merge-base", "--is-ancestor", baseline, target))
    if target_ancestor == 0 and baseline_ancestor == 0:
        return "unknown", "source-conflicting"
    if target_ancestor == 0:
        return "downgrade", "source-ancestor"
    if baseline_ancestor == 0:
        return "forward", None
    if target_ancestor == 1 and baseline_ancestor == 1:
        return "unknown", "source-divergent"
    return "unknown", "source-unavailable"


def compare_sources(
    repo: Path,
    *,
    target_version: str,
    target_revision: str,
    baseline_version: str | None,
    baseline_revision: str | None,
    git_runner: GitRunner = _run_git,
) -> SourceOrder:
    """Classify one target/baseline pair using only local bounded Git checks."""

    if baseline_version is None and baseline_revision is None:
        return SourceOrder("no-baseline", ())
    if baseline_version is None or baseline_revision is None:
        return SourceOrder("unknown", ("baseline-incomplete",))
    version_kind, version_reason = _version_order(target_version, baseline_version)
    source_kind, source_reason = _source_order(Path(repo), target_revision, baseline_revision, git_runner)
    reasons = tuple(reason for reason in (version_reason, source_reason) if reason is not None)
    if source_reason == "source-conflicting":
        return SourceOrder("unknown", reasons)
    if "downgrade" in {version_kind, source_kind}:
        return SourceOrder("downgrade", reasons)
    if "unknown" in {version_kind, source_kind}:
        return SourceOrder("unknown", reasons)
    if "forward" in {version_kind, source_kind}:
        return SourceOrder("forward", reasons)
    return SourceOrder("equal", reasons)


__all__ = ["OrderKind", "SourceOrder", "compare_sources"]
