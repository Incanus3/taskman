from __future__ import annotations

import importlib
from pathlib import Path


def test_source_order_reports_a_known_semver_downgrade_without_git_lookup(tmp_path: Path) -> None:
    """Removing numeric SemVer precedence would let an unacknowledged downgrade through."""

    source_order = importlib.import_module("taskman_ops.releases.source_order")

    result = source_order.compare_sources(
        tmp_path,
        target_version="1.9.0",
        target_revision="a" * 40,
        baseline_version="2.0.0",
        baseline_revision="a" * 40,
    )

    assert result.kind == "downgrade"
    assert result.reasons == ("version-lower",)


def test_source_order_uses_prerelease_precedence_and_ignores_build_metadata(tmp_path: Path) -> None:
    """Lexical comparison would order release candidates and build labels incorrectly."""
    source_order = importlib.import_module("taskman_ops.releases.source_order")
    result = source_order.compare_sources(tmp_path, target_version="2.0.0-rc.1+build.7", target_revision="a" * 40,
                                          baseline_version="2.0.0-rc.2+build.1", baseline_revision="a" * 40)
    assert result.kind == "downgrade"
    assert result.reasons == ("version-lower",)


def test_source_order_marks_unorderable_versions_and_missing_git_objects_unknown(tmp_path: Path) -> None:
    """Assuming lexical versions or absent objects are forward hides required acknowledgment."""
    source_order = importlib.import_module("taskman_ops.releases.source_order")
    result = source_order.compare_sources(tmp_path, target_version="version-next", target_revision="a" * 40,
                                          baseline_version="2.0.0", baseline_revision="b" * 40,
                                          git_runner=lambda _repo, _argv: 128)
    assert result.kind == "unknown"
    assert result.reasons == ("version-unorderable", "source-unavailable")


def test_source_order_detects_a_local_ancestor_and_distinguishes_no_baseline(tmp_path: Path) -> None:
    """Dropping the local ancestry check would miss an established source downgrade."""
    source_order = importlib.import_module("taskman_ops.releases.source_order")
    target = "a" * 40
    baseline = "b" * 40
    result = source_order.compare_sources(tmp_path, target_version="2.0.0", target_revision=target,
                                          baseline_version="2.0.0", baseline_revision=baseline,
                                          git_runner=lambda _repo, argv: 0 if argv[-2:] == (target, baseline) else 1)
    assert result.kind == "downgrade"
    assert result.reasons == ("source-ancestor",)
    assert source_order.compare_sources(tmp_path, target_version="2.0.0", target_revision=target,
                                        baseline_version=None, baseline_revision=None).kind == "no-baseline"


def test_source_order_reports_conflicting_ancestry_signals_as_unknown(tmp_path: Path) -> None:
    """Trusting the first successful ancestry probe converts contradictory evidence into a downgrade."""
    source_order = importlib.import_module("taskman_ops.releases.source_order")
    result = source_order.compare_sources(
        tmp_path,
        target_version="2.0.0",
        target_revision="a" * 40,
        baseline_version="2.0.0",
        baseline_revision="b" * 40,
        git_runner=lambda _repo, _argv: 0,
    )
    assert result.kind == "unknown"
    assert result.reasons == ("source-conflicting",)


def test_conflicting_ancestry_remains_unknown_even_with_a_lower_version(tmp_path: Path) -> None:
    """A contradictory source graph must not be relabeled as a certain downgrade."""
    source_order = importlib.import_module("taskman_ops.releases.source_order")
    result = source_order.compare_sources(
        tmp_path,
        target_version="1.0.0",
        target_revision="a" * 40,
        baseline_version="2.0.0",
        baseline_revision="b" * 40,
        git_runner=lambda _repo, _argv: 0,
    )
    assert result.kind == "unknown"
    assert result.reasons == ("version-lower", "source-conflicting")


def test_source_order_rejects_leading_zero_numeric_prerelease_identifiers(tmp_path: Path) -> None:
    """Accepting an invalid numeric prerelease can make unorderable release provenance look forward."""
    source_order = importlib.import_module("taskman_ops.releases.source_order")
    result = source_order.compare_sources(
        tmp_path,
        target_version="1.0.0-01",
        target_revision="a" * 40,
        baseline_version="1.0.0-1",
        baseline_revision="a" * 40,
    )
    assert result.kind == "unknown"
    assert result.reasons == ("version-unorderable",)


def test_source_order_marks_divergent_local_objects_unknown(tmp_path: Path) -> None:
    """A missing ancestry relation is uncertainty, not evidence of a forward source."""
    source_order = importlib.import_module("taskman_ops.releases.source_order")
    result = source_order.compare_sources(
        tmp_path,
        target_version="1.0.0",
        target_revision="a" * 40,
        baseline_version="1.0.0",
        baseline_revision="b" * 40,
        git_runner=lambda _repo, _argv: 1,
    )
    assert result.kind == "unknown"
    assert result.reasons == ("source-divergent",)
