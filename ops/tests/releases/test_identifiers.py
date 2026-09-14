"""Supported digest-bearing release identity tests."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from taskman_ops.releases.identifiers import (
    build_release_id,
    managed_release_path,
    release_application_version,
    release_artifact_sha256,
    release_otp_version,
    release_source_dirty,
    release_source_revision,
    validate_release_id,
)


REVISION = "a" * 40
DIGEST = "b" * 64
CLEAN = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + DIGEST
DIRTY = CLEAN + "-dirty"


def test_build_release_id_requires_the_exact_archive_digest_and_dirty_marker() -> None:
    clean = build_release_id(
        "0.2.0", REVISION, artifact_sha256=DIGEST, source_dirty=False
    )
    dirty = build_release_id(
        "0.2.0", REVISION, artifact_sha256=DIGEST, source_dirty=True
    )

    assert clean == CLEAN
    assert dirty == DIRTY
    assert validate_release_id(clean) == clean
    assert validate_release_id(dirty) == dirty


def test_release_identity_exposes_each_supported_component() -> None:
    assert release_application_version(CLEAN) == "0.2.0"
    assert release_source_revision(CLEAN) == REVISION[:12]
    assert release_artifact_sha256(CLEAN) == DIGEST
    assert release_otp_version(CLEAN) == "29.0.6"
    assert release_source_dirty(CLEAN) is False
    assert release_source_dirty(DIRTY) is True
    assert managed_release_path(PurePosixPath("/srv/taskman/releases"), CLEAN) == PurePosixPath(
        f"/srv/taskman/releases/{CLEAN}"
    )


@pytest.mark.parametrize(
    "value",
    (
        "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6",
        "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6-" + DIGEST,
        "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "A" * 64,
        "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 63,
        CLEAN + "-dirty-extra",
        CLEAN + "/outside",
    ),
)
def test_validate_release_id_rejects_source_only_old_runtime_and_unsafe_forms(value: str) -> None:
    with pytest.raises(ValueError):
        validate_release_id(value)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"artifact_sha256": DIGEST}, "source_dirty"),
        ({"source_dirty": False}, "artifact_sha256"),
        ({"artifact_sha256": "x" * 64, "source_dirty": False}, "artifact"),
        ({"artifact_sha256": DIGEST, "source_dirty": 0}, "source_dirty"),
    ),
)
def test_build_release_id_rejects_missing_or_non_strict_identity_inputs(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        build_release_id("0.2.0", REVISION, **kwargs)  # type: ignore[arg-type]


def test_release_id_component_bound_rejects_an_oversized_application_version() -> None:
    version = "1." + ("a" * 240)

    with pytest.raises(ValueError, match="long|size|identifier|version"):
        build_release_id(version, REVISION, artifact_sha256=DIGEST, source_dirty=False)
