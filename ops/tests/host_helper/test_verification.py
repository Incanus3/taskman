from taskman_ops.host_helper.legacy_result import OperationResult, project_result
from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request
from pathlib import Path
import subprocess


def test_verify_bridge_projects_only_the_verification_report() -> None:
    request = HostRequest(2, "verify", "op-0123456789abcdef0123456789abcdef", {}, {"install_root": "/opt/taskman"}, {})
    private = OperationResult(2, "verify", "op-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "succeeded", "verification", (), {}, {}, {"readiness": "passed"}, (), (), ())

    assert project_result(request, private).state == {"report": {"readiness": "passed"}}


def test_packaged_verify_redacts_invalid_runtime_input(tmp_path: Path) -> None:
    """The real entrypoint returns a bounded preflight fact, never request secrets."""

    secret = "verification-secret-must-not-escape"
    package = build_helper_package(tmp_path / "taskman-host.pyz")
    request = HostRequest(
        2,
        "verify",
        "op-0123456789abcdef0123456789abcdef",
        {"expected_release_id": None},
        {"install_root": str(tmp_path / "missing"), "backup_root": str(tmp_path / "backups")},
        {"credential_canary": secret},
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
    assert result.outcome == "retryable"
    assert result.state == {"preflight": "unsupported"}
    assert secret.encode() not in completed.stdout
