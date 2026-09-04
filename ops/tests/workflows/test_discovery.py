from __future__ import annotations

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.host_protocol import HostRequest, HostResult
from taskman_ops.workflows.backups import list_backups
from taskman_ops.workflows.releases import list_releases


CORRELATION = "op-0123456789abcdef0123456789abcdef"
RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
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


def _invoke(monkeypatch: pytest.MonkeyPatch, state: dict[str, object], warnings: tuple[str, ...] = ()) -> None:
    def run(_remote: object, request: HostRequest, **_kwargs: object) -> HostResult:
        return HostResult(2, request.operation, request.correlation_id, "succeeded", "state observed", state, warnings)

    monkeypatch.setattr("taskman_ops.workflows.releases.run_request", run)
    monkeypatch.setattr("taskman_ops.workflows.backups.run_request", run)


def test_discovery_result_exposes_compact_completed_release_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "release_id": RELEASE_ID,
        "source_revision": "a" * 40,
        "artifact_sha256": "d" * 64,
        "migrations": (),
    }
    _invoke(monkeypatch, {"releases": (row,), "future_fact": "ignored"}, ("unknown release entry",))

    result = list_releases(object(), config())

    assert result.records == ({**row, "migrations": []},)
    assert result.warnings == ("unknown release entry",)


def test_discovery_result_exposes_compact_completed_backup_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "backup_id": BACKUP_ID,
        "created_at": "2026-09-08T10:15:30Z",
        "dump_sha256": "e" * 64,
        "source_release_id": RELEASE_ID,
        "migration_versions": (20260905120000,),
        "source_database_size_bytes": 128,
    }
    _invoke(monkeypatch, {"backups": (row,), "future_fact": "ignored"}, ("unknown backup entry",))

    result = list_backups(object(), config())

    assert result.records == ({**row, "migration_versions": [20260905120000]},)
    assert result.warnings == ("unknown backup entry",)
