"""Controller-only parsing of helper verification evidence."""

from __future__ import annotations

from taskman_ops.errors import ExitStatus
from taskman_ops.workflows.verification_results import VerificationReport


def test_helper_verification_report_parses_the_complete_success_schema() -> None:
    """A missing controller translation boundary would reject valid helper evidence."""

    names = (
        "taskman-service",
        "release-identity",
        "caddy-service",
        "listener-topology",
        "startup-journal",
        "local-readiness",
        "public-readiness",
        "public-hsts",
    )
    report = VerificationReport.from_mapping(
        {
            "schema_version": 1,
            "status": "ok",
            "exit_status": 0,
            "release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
            "expected_release_id": "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6",
            "checks": [
                {"schema_version": 1, "name": name, "status": "passed", "summary": "passed"}
                for name in names
            ],
            "next_action": None,
        }
    )

    assert report.successful
    assert report.exit_status is ExitStatus.OK
