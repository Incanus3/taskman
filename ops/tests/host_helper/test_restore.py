from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request
from pathlib import Path
import subprocess


def test_restore_bridge_never_projects_recovery_actions() -> None:
    request = HostRequest(2, "restore", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "restore", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "failed", "restore", ("restore",), {"recovery_id": "recovery-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, {}, {}, ("/private",), ("recover",), ())

    result = project_result(request, private)

    assert result.outcome == "manual"
    assert result.state == {"changed": True, "failed_boundary": "restore"}


def test_packaged_restore_refuses_incomplete_authority_without_creating_state(
    tmp_path: Path,
) -> None:
    """The real restore procedure rejects an unconfirmed backup before mutation."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        2,
        "restore",
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
    assert result.state["failed_boundary"] == "restore"
    assert not (tmp_path / "install").exists()
