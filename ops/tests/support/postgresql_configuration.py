"""Synthetic files shared by native PostgreSQL capability tests."""
import os
from pathlib import Path
from taskman_ops.services.postgresql import build_postgresql_plan
from tests.support.environments import environment_config


def write_postmaster_pid(data_directory: Path, *, port: int, start_time: int) -> None:
    (data_directory / "postmaster.pid").write_text(
        "\n".join(
            (
                str(os.getpid()),
                data_directory.as_posix(),
                str(start_time),
                str(port),
                "/var/run/postgresql",
                "127.0.0.1",
                "1",
                "ready ",
                "",
            )
        ),
        encoding="utf-8",
    )


def native_configuration_fixture(tmp_path: Path, *, initial_config: str):
    plan = build_postgresql_plan(environment_config())
    stage = tmp_path / "pg_hba.staged"
    destination = tmp_path / "pg_hba.conf"
    config_file = tmp_path / "postgresql.conf"
    stage.write_text(plan.hba)
    destination.write_text(plan.hba)
    destination.chmod(0o640)
    config_file.write_text(initial_config)
    config_file.chmod(0o640)
    (tmp_path / "data").mkdir()
    return {"plan": plan, "stage": stage, "destination": destination, "config_file": config_file}
