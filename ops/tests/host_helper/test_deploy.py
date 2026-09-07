from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request
from pathlib import Path
import subprocess


def test_deploy_bridge_does_not_expose_the_private_operation_identifier() -> None:
    request = HostRequest(2, "deploy", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "deploy", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "deploy", ("deploy",), {"selected_release_id": "2026.9.7-deadbeef"}, {}, {}, (), (), ())

    result = project_result(request, private)

    assert result.state == {"changed": True, "selected_release_id": "2026.9.7-deadbeef"}
    assert "aaaaaaaa" not in repr(result.to_mapping())


def test_packaged_deploy_refuses_incomplete_request_before_creating_roots(
    tmp_path: Path,
) -> None:
    """Malformed deployment authority cannot reach the retained procedure."""

    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        2,
        "deploy",
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
    assert not (tmp_path / "install").exists()


def test_legacy_migration_stage_maps_to_the_release_boundary() -> None:
    """The retained procedure cannot create a final migration outcome itself."""

    request = HostRequest(
        2,
        "deploy",
        "op-0123456789abcdef0123456789abcdef",
        {},
        {"install_root": "/opt/taskman"},
        {},
    )
    private = OperationResult(
        2,
        "deploy",
        "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "failed",
        "migration",
        (),
        {},
        {},
        {},
        (),
        (),
        (),
    )
    assert project_result(request, private).state["failed_boundary"] == "release"
