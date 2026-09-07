import pytest
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostResult
from taskman_ops.workflows.rollback import _validate_success
from taskman_ops.workflows.verification_results import (
    CheckStatus,
    VerificationCheck,
    VerificationReport,
)


_CHECKS = (
    "taskman-service",
    "release-identity",
    "caddy-service",
    "listener-topology",
    "startup-journal",
    "local-readiness",
    "public-readiness",
    "public-hsts",
)
CURRENT = "0.2.1-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
TARGET = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


def verified(release_id: str) -> dict[str, object]:
    return VerificationReport(
        ExitStatus.OK,
        release_id,
        release_id,
        tuple(VerificationCheck(name, CheckStatus.PASSED, "passed") for name in _CHECKS),
        None,
    ).to_mapping()


def test_rollback_refuses_success_without_fresh_verification() -> None:
    request = type(
        "Request",
        (),
        {
            "expected_state": {"current_release_id": CURRENT},
            "parameters": {"target_release_id": TARGET},
        },
    )()
    result = HostResult(
        2, "rollback", "op-0123456789abcdef0123456789abcdef", "succeeded", "completed",
        {
            "changed": True,
            "previous_release_id": CURRENT,
            "target_release_id": TARGET,
            "selected_release_id": TARGET,
            "backup_id": "backup-0123456789abcdef0123456789abcdef",
            "activation_id": "activation-0123456789abcdef0123456789abcdef",
            "service_state": "active",
            "database_state": "unchanged",
            "activation_recorded": True,
        },
        (),
    )

    with pytest.raises(OpsError, match="invalid success evidence"):
        _validate_success(result, request)


def test_rollback_accepts_only_a_complete_verified_target() -> None:
    request = type(
        "Request",
        (),
        {
            "expected_state": {"current_release_id": CURRENT},
            "parameters": {"target_release_id": TARGET},
        },
    )()
    result = HostResult(
        2,
        "rollback",
        "op-0123456789abcdef0123456789abcdef",
        "succeeded",
        "completed",
        {
            "changed": True,
            "previous_release_id": CURRENT,
            "target_release_id": TARGET,
            "selected_release_id": TARGET,
            "backup_id": "backup-0123456789abcdef0123456789abcdef",
            "activation_id": "activation-0123456789abcdef0123456789abcdef",
            "service_state": "active",
            "database_state": "unchanged",
            "activation_recorded": True,
            "report": verified(TARGET),
        },
        (),
    )

    facts = _validate_success(result, request)

    assert facts["changed"] is True
    assert facts["verification"]["expected_release_id"] == TARGET
