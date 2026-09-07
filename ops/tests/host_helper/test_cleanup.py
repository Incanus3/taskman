from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request
from pathlib import Path
import subprocess


def test_cleanup_bridge_projects_the_confirmed_target_facts() -> None:
    request = HostRequest(2, "cleanup", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "cleanup", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "cleanup", (), {"targets": (), "removed": (), "recoverability": ()}, {}, {}, (), (), ())

    assert project_result(request, private).state == {"changed": False, "targets": (), "removed": (), "recoverability": ()}


def test_packaged_cleanup_refuses_an_unconfirmed_deletion_request(
    tmp_path: Path,
) -> None:
    """The real deletion procedure must reject empty authority before paths exist."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        2,
        "cleanup",
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
    assert not (tmp_path / "install").exists()
    assert not (tmp_path / "backups").exists()
