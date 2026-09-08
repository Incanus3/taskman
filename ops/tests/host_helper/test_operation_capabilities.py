"""Focused contracts for reusable host-operation capabilities."""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path
import subprocess

import pytest

from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_protocol import HostRequest


def _capability(name: str):
    qualified = f"taskman_ops.host_helper.{name}"
    assert importlib.util.find_spec(qualified) is not None
    return importlib.import_module(qualified)


def _database() -> dict[str, object]:
    return {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}


def test_database_observation_accepts_an_empty_schema_only_for_first_release(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating an absent migration table as normal during a rollback would hide lost authority."""

    database = _capability("database")
    commands = iter((b"", b"1\n", b"20260905120000\n"))

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, next(commands), b"")

    monkeypatch.setattr(database, "run_command", run)

    assert database.observe_database_state_or_empty(_database(), Path("/etc/taskman/pgpass")) == {
        "state": "ready",
        "applied_migrations": (),
    }
    assert database.observe_database_state(_database(), Path("/etc/taskman/pgpass")) == {
        "state": "ready",
        "applied_migrations": (20260905120000,),
    }


def test_credentials_refuse_a_group_readable_database_password_file(tmp_path: Path) -> None:
    """A credential file readable by another account would expose the database password."""

    credentials = _capability("credentials")
    path = tmp_path / "pgpass"
    path.write_text("127.0.0.1:5432:*:taskman:secret\n", encoding="utf-8")
    path.chmod(0o640)

    with pytest.raises(credentials.CredentialError):
        credentials.validate_credentials(path)


def test_service_capability_targets_only_the_taskman_systemd_unit(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping a different unit would leave Taskman's release active during selection."""

    services = _capability("services")
    commands: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(services, "run_command", run)

    services.change_service("stop")

    assert commands == [("systemctl", "stop", "taskman.service")]


def test_selection_replaces_current_with_the_confirmed_release(tmp_path: Path) -> None:
    """A non-atomic or wrong-target selection could start an unintended release."""

    selection = _capability("selection")
    paths = ManagedPaths.from_mapping(
        {"install_root": (tmp_path / "install").as_posix(), "backup_root": (tmp_path / "backups").as_posix()}
    )
    current = Path(paths.local(paths.current_link))
    previous = Path(paths.local(paths.release_root / "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"))
    target = Path(paths.local(paths.release_root / "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"))
    previous.mkdir(parents=True)
    target.mkdir()
    current.symlink_to(previous)

    selection.select_current(paths, target.name)

    assert current.resolve() == target
    assert not list(current.parent.glob(".current-*.tmp"))


def test_verification_request_preserves_correlation_and_sets_only_the_target_release() -> None:
    """A stale expected release would verify the wrong process after an atomic selection."""

    requests = _capability("verification_requests")
    source = HostRequest(
        2,
        "deploy",
        "op-0123456789abcdef0123456789abcdef",
        {"selected_release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"},
        {"install_root": "/srv/taskman", "backup_root": "/var/backups/taskman"},
        {"application_port": 4000},
    )

    request = requests.verification_request(
        source,
        "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6",
        {"application_port": 4000},
    )

    assert request.operation == "verify"
    assert request.correlation_id == source.correlation_id
    assert request.expected_state == {"expected_release_id": "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"}
    assert request.paths == source.paths


def test_operations_do_not_reintroduce_a_second_host_capability_owner() -> None:
    """Recreating any of these helpers would let consequence procedures silently diverge again."""

    forbidden = {
        "_atomically_select",
        "_fsync_directory",
        "_migration_versions",
        "_observe_database",
        "_safe_credentials",
        "_service",
        "_sha256",
    }
    operation_root = Path(__file__).resolve().parents[2] / "taskman_ops" / "host_helper" / "operations"

    for name in ("backup", "deploy", "rollback", "restore"):
        tree = ast.parse((operation_root / f"{name}.py").read_text(encoding="utf-8"))
        definitions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert definitions.isdisjoint(forbidden), name
