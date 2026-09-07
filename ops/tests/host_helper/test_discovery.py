from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

import pytest

from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request


def _release_id(index: int) -> str:
    return f"0.2.0-{index:012x}-ubuntu26.04-amd64-otp27.3.4.6"


def _migration(index: int) -> dict[str, str]:
    return {
        "filename": f"20260905{index:06d}_create_tasks.exs",
        "sha256": f"{index:064x}",
    }


def _discovery_result(release_count: int, migrations_per_release: int) -> OperationResult:
    return OperationResult(
        2,
        "discover",
        "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "succeeded",
        "discovered",
        (),
        {
            "state": "managed",
            "records": {"releases": [], "backups": [], "activations": [], "adoptions": []},
            "release_migrations": [
                {
                    "release_id": _release_id(release),
                    "migrations": [_migration(migration) for migration in range(migrations_per_release)],
                }
                for release in range(release_count)
            ],
        },
        {},
        {},
        (),
        (),
        (),
    )


def _discovery_request() -> HostRequest:
    return HostRequest(
        2,
        "discover",
        "op-0123456789abcdef0123456789abcdef",
        {},
        {"install_root": "/opt/taskman"},
        {},
    )


def test_discovery_bridge_flattens_selected_public_facts() -> None:
    request = HostRequest(2, "discover", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "discover", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "discovered", (), {"state": "empty", "records": {"releases": [], "backups": [], "activations": []}}, {}, {}, (), (), ())

    assert project_result(request, private).state == {"host_kind": "empty", "releases": (), "backups": (), "activations": ()}


def test_discovery_bridge_projects_historical_migration_authority() -> None:
    """Restore consumes only the release/migration rows selected from discovery."""

    release_id = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    request = HostRequest(
        2,
        "discover",
        "op-0123456789abcdef0123456789abcdef",
        {},
        {"install_root": "/opt/taskman"},
        {},
    )
    private = OperationResult(
        2,
        "discover",
        "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "succeeded",
        "discovered",
        (),
        {
            "state": "managed",
            "records": {"releases": [], "backups": [], "activations": [], "adoptions": []},
            "release_migrations": [
                {
                    "release_id": release_id,
                    "migrations": [
                        {
                            "filename": "20260905120000_create_tasks.exs",
                            "sha256": "e" * 64,
                        }
                    ],
                }
            ],
        },
        {},
        {},
        (),
        (),
        (),
    )

    result = project_result(request, private)

    assert result.state["release_migrations"] == (
        {
            "release_id": release_id,
            "migrations": (
                {
                    "filename": "20260905120000_create_tasks.exs",
                    "sha256": "e" * 64,
                },
            ),
        },
    )


@pytest.mark.parametrize(
    ("release_count", "migrations_per_release"),
    ((65, 1), (1, 65)),
)
def test_discovery_bridge_refuses_history_that_exceeds_collection_bounds(
    release_count: int,
    migrations_per_release: int,
) -> None:
    """A complete but unrepresentable history must not crash helper encoding."""

    result = project_result(
        _discovery_request(),
        _discovery_result(release_count, migrations_per_release),
    )

    assert result.outcome == "refused"
    assert result.message == "helper discovery history exceeds protocol bounds"
    assert result.state == {"history": "unavailable"}


def test_discovery_bridge_refuses_history_that_exceeds_output_bytes() -> None:
    """Nested valid rows also need a bounded final encoded result."""

    result = project_result(
        _discovery_request(),
        _discovery_result(32, 64),
    )

    assert result.outcome == "refused"
    assert result.message == "helper discovery history exceeds protocol bounds"
    assert result.state == {"history": "unavailable"}


def test_packaged_discovery_projects_historical_migrations_from_real_manifest(
    tmp_path: Path,
) -> None:
    """Restore authority comes from accepted host manifests, not a synthetic map."""

    release_id = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
    stamp = datetime(2026, 9, 5, 12, 0, tzinfo=UTC).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    install = tmp_path / "install"
    release = install / "releases" / release_id
    release.mkdir(parents=True)
    release.parent.chmod(0o750)
    release.chmod(0o750)
    (install / "current").symlink_to(release)

    def write_record(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.parent.chmod(0o750)
        path.parent.chmod(0o750)
        path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        path.chmod(0o600)

    write_record(
        install / "deployments" / "releases" / f"release-{release_id}.json",
        {
            "schema_version": 1,
            "release_id": release_id,
            "artifact_sha256": "a" * 64,
            "installed_at": stamp,
            "activated_at": stamp,
            "previous_release_id": None,
            "backup_id": None,
            "migration_policy": "no-change",
        },
    )
    write_record(
        install / "deployments" / "activations" / "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json",
        {
            "schema_version": 1,
            "activation_id": "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "previous_release_id": None,
            "candidate_release_id": release_id,
            "activated_at": stamp,
            "backup_id": None,
            "migration_policy": "no-change",
        },
    )
    migration = {"filename": "20260905120000_create_tasks.exs", "sha256": "e" * 64}
    write_record(
        install / "deployments" / "manifests" / f"release-{release_id}.json",
        {
            "schema_version": 2,
            "application": "taskman",
            "application_version": "0.2.0",
            "source_revision": "a" * 40,
            "release_id": release_id,
            "built_at": stamp,
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "otp_version": "27.3.4.6",
            "elixir_version": "1.18.3",
            "node_version": "22.22.1",
            "hex_version": "2.5.1",
            "rebar3_version": "3.24.0",
            "builder_base_tag": "ubuntu:resolute-20260811.1",
            "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
            "migrations": [migration],
            "top_level": "taskman",
        },
    )
    (tmp_path / "backups").mkdir()
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        2,
        "discover",
        "op-0123456789abcdef0123456789abcdef",
        {},
        {"install_root": install.as_posix(), "backup_root": (tmp_path / "backups").as_posix()},
        {},
    )

    completed = subprocess.run(
        ["python3", "-I", str(package.path)],
        input=encode_request(request),
        capture_output=True,
        check=False,
    )

    result = decode_result(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert result.outcome == "succeeded"
    assert result.state["release_migrations"] == (
        {"release_id": release_id, "migrations": (migration,)},
    )
