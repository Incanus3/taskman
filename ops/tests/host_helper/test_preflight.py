from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
import stat
from types import SimpleNamespace

import pytest

from taskman_ops.host_helper.operations import preflight as preflight_module
from taskman_ops.host_protocol import HostRequest


CORRELATION = "op-0123456789abcdef0123456789abcdef"


def _request(tmp_path: Path, *, mode: str = "inspection") -> HostRequest:
    return HostRequest(
        3,
        "restore_preflight",
        CORRELATION,
        {},
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        {
            "mode": mode,
            "credentials_path": "/etc/taskman/pgpass",
            "database": {
                "host": "127.0.0.1", "port": 5432,
                "role": "taskman", "name": "taskman_prod",
            },
        },
    )


def test_restore_preflight_returns_only_the_closed_inspection_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[object, object]] = []
    monkeypatch.setattr(preflight_module, "validate_credentials", lambda path: observed.append(("credentials", path)))
    monkeypatch.setattr(
        preflight_module,
        "observe_restore_preflight",
        lambda database, credentials, mode: observed.append((database, mode)) or {"mode": "inspection"},
    )

    result = preflight_module.restore_preflight(_request(tmp_path))

    assert result.outcome == "succeeded"
    assert result.state == {"mode": "inspection"}
    assert observed[0] == ("credentials", Path("/etc/taskman/pgpass"))
    assert observed[1][1] == "inspection"


def test_restore_preflight_rejects_extra_parameters_without_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def observe(*_args: object) -> object:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(preflight_module, "observe_restore_preflight", observe)
    original = _request(tmp_path)
    request = HostRequest(
        original.protocol_version, original.operation, original.correlation_id,
        original.expected_state, original.paths, {**original.parameters, "extra": True},
    )

    result = preflight_module.restore_preflight(request)

    assert result.outcome == "refused"
    assert result.state == {}
    assert called is False


def test_capacity_preflight_returns_positive_exact_role_sizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight_module, "validate_credentials", lambda _path: None)
    monkeypatch.setattr(
        preflight_module,
        "observe_restore_preflight",
        lambda *_args: {
            "mode": "capacity",
            "database_available_bytes": 123,
            "database_size_bytes": {"canonical": 10, "temporary": None, "retired": 20},
        },
    )

    result = preflight_module.restore_preflight(_request(tmp_path, mode="capacity"))

    assert result.outcome == "succeeded"
    assert result.state["database_size_bytes"] == {
        "canonical": 10, "temporary": None, "retired": 20,
    }


def test_native_capacity_uses_postgres_data_directory_and_exact_database_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter((b"1\n", b"/srv/postgresql/data\n", b"taskman_prod\t10\ntaskman_prod__restore_old\t20\n"))
    calls: list[tuple[object, ...]] = []

    def query(database, command, *, variables=()):
        calls.append((database, command, variables))
        return next(outputs)

    class Stats:
        f_bavail = 100
        f_frsize = 4096

    monkeypatch.setattr(preflight_module, "_admin_query", query)
    monkeypatch.setattr(preflight_module.os, "statvfs", lambda path: calls.append((path,)) or Stats())
    database = {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman_prod"}

    state = preflight_module.observe_restore_preflight(
        database, Path("/etc/taskman/pgpass"), "capacity"
    )

    assert state["database_available_bytes"] == 409600
    assert state["database_size_bytes"] == {
        "canonical": 10, "temporary": None, "retired": 20,
    }
    assert ("/srv/postgresql/data",) in calls


def test_native_database_query_uses_the_parser_fixed_tab_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(preflight_module, "run_command", run)
    database = {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman_prod"}

    preflight_module._admin_query(database, "SELECT 1")

    assert "--field-separator=\t" in calls[0]


def test_pgpass_private_entry_refuses_mismatched_connection_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight_module, "_PGPASS_PATH", tmp_path / "pgpass")
    called = False

    def authenticate(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(preflight_module, "authenticate_pgpass", authenticate)
    status = preflight_module.provision_pgpass_authority(
        ("127.0.0.1", "5432", "taskman", "taskman_prod"),
        BytesIO(b"127.0.0.1:5432:other:taskman:secret\n"),
    )

    assert status == 10
    assert called is False
    assert list(tmp_path.iterdir()) == []


def test_pgpass_private_entry_validates_retained_file_before_using_pgpassfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"127.0.0.1:5432:taskman_prod:taskman:secret\n"
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    class RetainedPgpass:
        contents = raw
        mode = 0o600

        def lstat(self):
            return SimpleNamespace(
                st_mode=stat.S_IFREG | self.mode,
                st_uid=0,
                st_gid=0,
            )

        def read_bytes(self):
            return self.contents

        def as_posix(self):
            return "/etc/taskman/pgpass"

    retained = RetainedPgpass()

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(preflight_module, "_PGPASS_PATH", retained)
    monkeypatch.setattr(preflight_module, "run_command", run)
    arguments = ("127.0.0.1", "5432", "taskman", "taskman_prod")

    assert preflight_module.provision_pgpass_authority(arguments, BytesIO(raw)) == 0
    assert calls[0][1]["env"] == {"PGPASSFILE": "/etc/taskman/pgpass"}
    assert calls[0][0] == (
        "psql", "--no-psqlrc", "--quiet", "--host", "127.0.0.1",
        "--port", "5432", "--username", "taskman", "--dbname", "taskman_prod",
        "--no-password", "--command", "SELECT 1", "--output", "/dev/null",
    )

    retained.contents = b"different\n"
    assert preflight_module.provision_pgpass_authority(arguments, BytesIO(raw)) == 10
    retained.contents = raw
    retained.mode = 0o640
    assert preflight_module.provision_pgpass_authority(arguments, BytesIO(raw)) == 10
    assert len(calls) == 1
