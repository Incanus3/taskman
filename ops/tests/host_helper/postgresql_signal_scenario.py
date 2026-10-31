"""Disposable process scenarios for PostgreSQL signal handling tests."""

from __future__ import annotations

import hashlib
import os
import signal
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from taskman_ops.host_helper import postgresql_configuration as capability
from taskman_ops.host_helper.commands import CommandCancelled, run_command
from taskman_ops.host_helper.operations import postgresql as adapter
from taskman_ops.host_protocol import HostRequest
from tests.host_helper.test_postgresql_configuration import (
    CONFIG,
    DATA,
    HBA,
    STAGE,
    NativeCommands,
)
from tests.support.postgresql_configuration import native_configuration_fixture


def wait_for_command_child(ready_marker: Path) -> int:
    """Write the command PID before waiting so the parent can synchronize and clean up."""

    ready_marker.write_text(str(os.getpid()))
    time.sleep(60)
    return 0


def run_signal_scenario(root: Path) -> int:
    """Exercise interrupted PostgreSQL configuration and exact HBA restoration."""

    files = native_configuration_fixture(root, initial_config="listen_addresses = '127.0.0.1'\n")
    original = b"# synthetic original policy\n"
    files["destination"].write_bytes(original)
    files["destination"].chmod(0o604)
    before = files["destination"].stat()
    commands = NativeCommands(files)
    commands.write_pid()
    paths = {
        DATA: root / "data",
        CONFIG: files["config_file"],
        HBA: files["destination"],
        STAGE: files["stage"],
    }
    capability._local_path = lambda path: paths.get(str(path), Path(path))
    capability._postgres_identity = lambda: (os.getuid(), os.getgid())
    adapter.lifecycle_lock = lambda *a, **k: nullcontext()
    previous = {
        number: signal.getsignal(number)
        for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    }
    cleaned = []
    event = []

    def parser(count):
        if count == 1:
            return ""
        try:
            run_command(
                (
                    sys.executable,
                    "-m",
                    "tests.host_helper.postgresql_signal_scenario",
                    "--wait-command",
                    str(root / "ready"),
                ),
                timeout_seconds=30,
                cancellation=event[0],
                output_limit=1024,
            )
        except CommandCancelled:
            pid = int((root / "ready").read_text())
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                cleaned.append(True)
            else:
                raise AssertionError("child not reaped")
            raise

    commands.parser = parser

    def native(argv, **kwargs):
        if not event:
            event.append(kwargs["cancellation"])
        return commands(argv, **kwargs)

    capability.run_command = native
    replace = os.replace
    restores = []

    def restoring(source, destination):
        if Path(source).read_bytes() == original:
            assert cleaned == [True]
            restores.append(True)
            for number in previous:
                os.kill(os.getpid(), number)
        return replace(source, destination)

    capability.os.replace = restoring
    parameters = {
        "package_track": "16",
        "cluster": None,
        "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
        "hba_sha256": hashlib.sha256(files["stage"].read_bytes()).hexdigest(),
    }
    request = HostRequest(
        3,
        "configure_postgresql",
        "op-" + "1" * 32,
        {},
        {"install_root": "/srv/taskman", "backup_root": "/srv/taskman-backups"},
        parameters,
    )
    result = adapter.configure_postgresql(request)
    after = files["destination"].stat()
    assert files["destination"].read_bytes() == original
    assert (before.st_uid, before.st_gid, before.st_mode) == (after.st_uid, after.st_gid, after.st_mode)
    assert restores == [True]
    assert not files["destination"].with_name("pg_hba.conf.taskman-backup").exists()
    assert all(signal.getsignal(number) == handler for number, handler in previous.items())
    assert result.state["reason"] == "command_failed"
    assert result.state["mutation_state"] == "changed"
    print("restored after reaping", flush=True)
    return 0


def main() -> int:
    arguments = sys.argv[1:]
    if len(arguments) == 2 and arguments[0] == "--wait-command":
        return wait_for_command_child(Path(arguments[1]))
    if len(arguments) == 1:
        return run_signal_scenario(Path(arguments[0]))
    raise SystemExit("expected a scenario directory or --wait-command marker path")


if __name__ == "__main__":
    raise SystemExit(main())
