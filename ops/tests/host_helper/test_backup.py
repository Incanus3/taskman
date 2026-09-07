from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request
from pathlib import Path
import subprocess


def test_backup_bridge_projects_backup_facts_only() -> None:
    request = HostRequest(2, "backup", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "backup", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "backup", ("backup",), {"backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "reason": "scheduled"}, {}, {}, (), (), ())

    assert project_result(request, private).state == {"changed": True, "backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "reason": "scheduled"}


def test_packaged_backup_refuses_incomplete_authority_before_running_a_dump(
    tmp_path: Path,
) -> None:
    """The real helper dispatch must refuse before it can invoke pg_dump."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        2,
        "backup",
        "op-0123456789abcdef0123456789abcdef",
        {},
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
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
    assert result.outcome == "refused"
    assert result.state == {"changed": False}
