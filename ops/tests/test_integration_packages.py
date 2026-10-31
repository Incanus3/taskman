"""Contracts for immutable archives shared only by public integration tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import subprocess

from tests.support.integration_packages import integration_packages


ARCHIVE_SCENARIOS = Path(__file__).resolve().parent / "support/integration_package_scenarios.py"


def test_integration_packages_materialize_private_independent_executable_copies(
    integration_packages, tmp_path: Path
) -> None:
    """Every consumer gets private bytes while retaining the validated archive identity."""

    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    first = (
        integration_packages.materialize_helper(first_directory / "helper.pyz"),
        integration_packages.materialize_scheduled_backup(first_directory / "backup.pyz"),
    )
    second = (
        integration_packages.materialize_helper(second_directory / "helper.pyz"),
        integration_packages.materialize_scheduled_backup(second_directory / "backup.pyz"),
    )

    for first_package, second_package, expected_code in zip(
        first, second, (0, 2), strict=True
    ):
        assert first_package.path != second_package.path
        assert first_package.path.read_bytes() == second_package.path.read_bytes()
        assert hashlib.sha256(first_package.path.read_bytes()).hexdigest() == first_package.sha256
        assert hashlib.sha256(second_package.path.read_bytes()).hexdigest() == second_package.sha256
        assert first_package.sha256 == second_package.sha256
        assert first_package.protocol_version == second_package.protocol_version
        assert stat.S_IMODE(first_package.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(second_package.path.stat().st_mode) == 0o600

        first_package.path.write_bytes(b"mutated private copy")
        assert second_package.path.read_bytes() != b"mutated private copy"

        completed = subprocess.run(
            ["python3", "-I", "-S", str(second_package.path)],
            input=b"",
            capture_output=True,
            check=False,
        )
        assert completed.returncode == expected_code
        assert completed.stderr == b""


def test_isolated_archive_imports_adapter_lazily_without_ufw(integration_packages, tmp_path):
    import sys

    package = integration_packages.materialize_helper(tmp_path / "helper.pyz")
    completed = subprocess.run(
        [
            sys.executable, "-I", "-S", str(ARCHIVE_SCENARIOS), "lazy_ufw_import",
            str(package.path),
        ],
        capture_output=True, check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == b'{"lazy": true}\n'
    assert completed.stderr == b""


def test_isolated_archive_byte_warning_stays_out_of_json(integration_packages, tmp_path):
    import json
    import sys

    package = integration_packages.materialize_helper(tmp_path / "helper.pyz")
    completed = subprocess.run(
        [
            sys.executable, "-I", "-S", str(ARCHIVE_SCENARIOS), "byte_warning",
            str(package.path),
        ],
        capture_output=True, check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"reason": "inspection_failed"}
    assert completed.stderr == b""


def test_isolated_archive_real_ufw_warning_stays_out_of_json(integration_packages, tmp_path):
    """Use operator-provided Ubuntu UFW source without downloads or source authentication."""
    import json
    import os
    import sys

    import pytest

    configured = os.environ.get("TASKMAN_TEST_UFW_PACKAGE")
    if not configured:
        pytest.skip("set TASKMAN_TEST_UFW_PACKAGE to a trusted extracted Ubuntu UFW root")
    extracted = Path(configured)
    for name in ("__init__.py", "backend_iptables.py", "util.py"):
        assert (extracted / "usr/lib/python3/dist-packages/ufw" / name).is_file()
    package = integration_packages.materialize_helper(tmp_path / "helper.pyz")
    completed = subprocess.run(
        [
            sys.executable, "-I", "-S", str(ARCHIVE_SCENARIOS), "real_ufw_warning",
            str(package.path), str(extracted),
        ],
        capture_output=True, check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"reason": "inspection_failed"}
    assert completed.stderr == b""


def test_isolated_archive_dispatches_postgresql_and_preserves_encoding_change(integration_packages, tmp_path):
    import json
    import sys
    import zipfile
    from taskman_ops.helper_client.package import BACKUP_ARCHIVE_MEMBERS
    package = integration_packages.materialize_helper(tmp_path / 'helper.pyz')
    with zipfile.ZipFile(package.path) as archive:
        assert {'taskman_ops/host_helper/postgresql_configuration.py',
                'taskman_ops/host_helper/operations/postgresql.py'} <= set(archive.namelist())
    assert not any('postgresql' in name for name in BACKUP_ARCHIVE_MEMBERS)
    completed = subprocess.run(
        [
            sys.executable, "-I", "-S", str(ARCHIVE_SCENARIOS), "postgresql_dispatch",
            str(package.path),
        ],
        capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == b''
    assert json.loads(completed.stdout) == {'operation': 'configure_postgresql', 'changed': True}


def test_configuration_archive_runner_loss_is_unknown_and_cleans_transient_paths(integration_packages, tmp_path):
    import pytest
    from tests.host_protocol.test_postgresql import configuration_request
    from tests.support.remotes import HelperRunnerRemote
    from taskman_ops.remote import CommandResult
    from taskman_ops.errors import OpsError
    from taskman_ops.workflows.helper import run_request
    request = configuration_request()
    package = integration_packages.materialize_helper(tmp_path / 'helper.pyz')
    installed = f'/run/taskman-ops/{request.correlation_id}/taskman-host.pyz'
    remote = HelperRunnerRemote(package.sha256, CommandResult(0, ''))
    remote.add_response(('sudo', '--preserve-env=SSH_CONNECTION', '--', 'python3', installed),
        OpsError(5, 'remote', 'synthetic dispatched loss'))
    with pytest.raises(OpsError) as caught:
        run_request(remote, request, package=package)
    assert caught.value.status == 5
    assert caught.value.changed
    assert caught.value.state['mutation_state'] == 'unknown'
    assert ('rm', '--', installed) in [command for command, _ in remote.calls]
