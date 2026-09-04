"""Read-only operator workflow for deployed-host verification."""

from __future__ import annotations

from ..config import EnvironmentConfig
from ..output import WorkflowResult
from ..releases.records import RemoteLifecycleStore
from ..remote import Remote
from ..verification import Clock, PublicHTTPClient, Sleeper, verify_installation


def run_verify(
    remote: Remote,
    config: EnvironmentConfig,
    expected_release_id: str | None = None,
    *,
    lifecycle_store: RemoteLifecycleStore | None = None,
    public_client: PublicHTTPClient | None = None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    max_attempts: int | None = None,
) -> WorkflowResult:
    """Run the full read-only verification capability without convergence."""

    report = verify_installation(
        remote,
        config,
        expected_release_id,
        lifecycle_store=lifecycle_store,
        public_client=public_client,
        clock=clock,
        sleeper=sleeper,
        max_attempts=max_attempts,
    )
    return WorkflowResult(
        command="verify",
        environment=config.name,
        changed=False,
        stage="verified" if report.successful else "verification-failed",
        facts={"verification": report.to_mapping()},
        warnings=(),
        next_action=report.next_action,
        exit_status=report.exit_status,
    )


__all__ = ["run_verify"]
