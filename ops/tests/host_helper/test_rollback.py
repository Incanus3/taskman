from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request
from pathlib import Path
import subprocess


def test_rollback_bridge_projects_selected_release() -> None:
    request = HostRequest(2, "rollback", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "rollback", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "records", ("select",), {"selected_release_id": "2026.9.6-feedface"}, {}, {}, (), (), ())

    assert project_result(request, private).state == {"changed": True, "selected_release_id": "2026.9.6-feedface"}


def test_packaged_rollback_refuses_incomplete_authority_before_creating_a_backup(
    tmp_path: Path,
) -> None:
    """The real rollback procedure must not fall through to mutation on bad input."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        2,
        "rollback",
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
    assert result.state["failed_boundary"] == "release"
    assert not (tmp_path / "backups").exists()
