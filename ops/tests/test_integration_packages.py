"""Contracts for immutable archives shared only by public integration tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import subprocess

from tests.support.integration_packages import integration_packages


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
    script = '''import json, sys
sys.path.insert(0, sys.argv[1])
from taskman_ops.host_helper import ufw_adapter
assert not any(name == 'ufw' or name.startswith('ufw.') for name in sys.modules)
print(json.dumps({'lazy': True}))
'''
    completed = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(package.path)],
                               capture_output=True, check=False)
    assert completed.returncode == 0
    assert completed.stdout == b'{"lazy": true}\n'
    assert completed.stderr == b""


def test_isolated_archive_byte_warning_stays_out_of_json(integration_packages, tmp_path):
    import json
    import sys

    package = integration_packages.materialize_helper(tmp_path / "helper.pyz")
    script = '''import json, sys, time
sys.path.insert(0, sys.argv[1])
from taskman_ops.host_helper import ufw_adapter
before = sys.stdout, sys.stderr
def warn(**kwargs):
    sys.stderr.buffer.write(b'WARN: malformed tuple\\n')
ufw_adapter._read_backend = warn
try:
    ufw_adapter.inspect_ufw(deadline=time.monotonic() + 45)
except ufw_adapter.UfwInspectionError as error:
    assert (sys.stdout, sys.stderr) == before
    print(json.dumps({'reason': error.reason}))
else:
    raise AssertionError('warning accepted')
'''
    completed = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(package.path)],
                               capture_output=True, check=False)
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
    script = '''import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from taskman_ops.host_helper import ufw_adapter
ufw_adapter._UFW_PACKAGE = Path(sys.argv[2]) / 'usr/lib/python3/dist-packages/ufw'
# Fixture-only extracted source has an unprivileged temporary parent.
ufw_adapter._trusted_path = lambda path: None
def warn(*, deadline):
    ufw_adapter._load_package(deadline=deadline)
    import ufw.util
    ufw.util.warn('malformed tuple')
ufw_adapter._read_backend = warn
try:
    ufw_adapter.inspect_ufw(deadline=time.monotonic() + 45)
except ufw_adapter.UfwInspectionError as error:
    print(json.dumps({'reason': error.reason}))
else:
    raise AssertionError('warning accepted')
'''
    completed = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(package.path), str(extracted)],
                               capture_output=True, check=False)
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"reason": "inspection_failed"}
    assert completed.stderr == b""
