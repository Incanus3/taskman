from __future__ import annotations

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_helper.records import BackupRecord, ReleaseRecord
from taskman_ops.releases.manifests import (
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    ArtifactManifest,
)
from taskman_ops.workflows.backups import list_backups
from taskman_ops.workflows.releases import list_releases


RELEASE_ID = (
    "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
)
BACKUP_ID = "backup-cccccccccccccccccccccccccccccccc"


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43,
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@acme.tld",
        }
    )


def _release() -> dict[str, object]:
    manifest = ArtifactManifest.from_mapping(
        {
            "schema_version": 3,
            "application": "taskman",
            "application_version": "0.2.0",
            "source_revision": "a" * 40,
            "release_id": RELEASE_ID,
            "built_at": "2026-09-14T12:00:00Z",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "29.0.6",
            "elixir_version": "1.20.4",
            "node_version": "22.22.1",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "builder_base_tag": BUILDER_BASE_TAG,
            "builder_base_digest": BUILDER_BASE_DIGEST,
            "migrations": [],
            "top_level": "taskman",
            "artifact_sha256": "b" * 64,
            "source_dirty": False,
        }
    )
    return ReleaseRecord(RELEASE_ID, "a" * 40, "b" * 64, (), 2, manifest).to_mapping()


def _backup() -> dict[str, object]:
    return BackupRecord.from_mapping(
        {
            "backup_id": BACKUP_ID,
            "created_at": "2026-09-08T10:15:30Z",
            "dump_sha256": "e" * 64,
            "source_release_id": RELEASE_ID,
            "migration_versions": [20260905120000],
            "source_database_size_bytes": 128,
        }
    ).to_mapping()


def test_public_release_listing_returns_the_complete_collected_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public wrapper must not reapply the wire page limit."""
    rows = tuple(_release() for _index in range(65))
    monkeypatch.setattr(
        "taskman_ops.workflows.releases.collect_inventory",
        lambda *_args, **_kwargs: rows,
    )

    result = list_releases(object(), config())

    assert result.records == rows
    assert len(result.to_mapping()["records"]) == 65


def test_public_backup_listing_returns_validated_collected_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (_backup(),)
    monkeypatch.setattr(
        "taskman_ops.workflows.backups.collect_inventory",
        lambda *_args, **_kwargs: rows,
    )

    result = list_backups(object(), config())

    assert result.records == rows
    assert result.warnings == ()
