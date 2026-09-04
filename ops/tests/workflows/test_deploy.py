"""Ordered immutable deployment workflow contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.cli import Invocation, dispatch, parse_invocation
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.manifests import ArtifactManifest, MigrationFingerprint, VerifiedArtifact
from taskman_ops.remote import CommandResult
from taskman_ops.releases.records import ManualAdoptionCandidate
from taskman_ops.workflows.deploy import deploy, deploy_first_release, inspect_deploy_state


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CANDIDATE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.validate_operational_preflight",
        lambda *_args: object(),
    )


def config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:" + "A" * 43,
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@acme.tld",
        }
    )


def artifact(tmp_path: Path, *, release_id: str = CANDIDATE) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    archive.write_bytes(b"release")
    manifest = ArtifactManifest(
        1,
        "taskman",
        "0.2.0",
        ("b" if release_id == CANDIDATE else "a") * 40,
        release_id,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "ubuntu26.04",
        "amd64",
        "27.3.4.6",
        "1.18.3",
        "22.22.1",
        (),
        "taskman",
    )
    return VerifiedArtifact(archive, tmp_path / "manifest.json", tmp_path / "checksum", "c" * 64, manifest)


def completed_transaction_payload(
    *,
    candidate: str,
    previous: str,
    deployed: bool,
) -> dict[str, object]:
    verification = {
        "schema_version": 1,
        "status": "ok",
        "exit_status": 0,
        "release_id": candidate,
        "expected_release_id": candidate,
        "checks": [
            {"schema_version": 1, "name": name, "status": "passed", "summary": summary}
            for name, summary in (
                ("taskman-service", "taskman.service is active with a positive MainPID"),
                ("release-identity", "systemd MainPID executable is under the selected release"),
                ("caddy-service", "caddy.service is active"),
                ("listener-topology", "Taskman, distribution, and PostgreSQL listeners have the required topology"),
                ("startup-journal", "recent startup journal evidence is clean"),
                ("local-readiness", "loopback health endpoint returned exact ready response"),
                ("public-readiness", "public HTTPS health endpoint returned exact ready response"),
                ("public-hsts", "public HTTPS response includes HSTS"),
            )
        ],
        "next_action": None,
    }
    return {
        "stage": "deployed" if deployed else "already-current",
        "previous_release_id": previous,
        "candidate_release_id": candidate,
        "selected_release_id": candidate,
        "backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" if deployed else None,
        "activation_id": "activation-" + "d" * 32 if deployed else None,
        "service_state": "active",
        "database_state": "unchanged",
        "activation_recorded": True,
        "changed": deployed,
        "changed_stages": (
            ["staging", "backup", "stop", "migration", "selection", "start", "verification", "records"]
            if deployed
            else []
        ),
        "warnings": [],
        "recovery_commands": [
            "systemctl status taskman.service",
            f"readlink -f {config().managed_root}/current",
            "journalctl --no-pager --unit taskman.service --lines=100",
        ],
        "residue_paths": [],
        "verification": verification,
    }


def confirmed_deploy_options(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: str = CURRENT,
    current_sha256: str = "a" * 64,
) -> dict[str, object]:
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.inspect_deploy_state",
        lambda *_args, **_kwargs: (current, (), True, current_sha256),
    )
    return {
        "present_plan": lambda _plan: None,
        "confirm": lambda _plan: True,
    }


def test_first_release_pre_lock_upload_preparation_failure_reports_known_stopped_service(
    tmp_path: Path,
) -> None:
    """Genesis preparation cannot fabricate a running service before the lock."""

    class PreparationFailureRemote:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            self.calls.append(argv)
            return CommandResult(ExitStatus.SAFETY)

        def put(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("failed upload preparation reached artifact transfer")

    remote = PreparationFailureRemote()

    result = deploy_first_release(remote, config(), artifact(tmp_path))

    assert len(remote.calls) == 1
    assert remote.calls[0][0:2] == ("sh", "-ceu")
    assert remote.calls[0][3] == "taskman-deploy-upload-prepare"
    assert result.exit_status is ExitStatus.SAFETY
    assert result.stage == "staging-failed"
    assert result.changed is False
    assert result.facts["previous_release_id"] is None
    assert result.facts["service_state"] == "stopped"
    assert result.facts["service_state"] not in {"active", "running"}


def test_first_release_pre_lock_transport_refusal_reports_unobserved_service_as_unknown(
    tmp_path: Path,
) -> None:
    """A failure without genesis preparation evidence cannot claim service state."""

    class UncertainRemote:
        def run(self, *_args: object, **_kwargs: object) -> CommandResult:
            raise OpsError(
                ExitStatus.SAFETY,
                "remote",
                "transport refused the preparation command",
                changed=False,
            )

        def put(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("transport refusal reached artifact transfer")

    result = deploy_first_release(UncertainRemote(), config(), artifact(tmp_path))

    assert result.exit_status is ExitStatus.SAFETY
    assert result.changed is False
    assert result.facts["service_state"] == "unknown"
    assert result.facts["service_state"] not in {"active", "running"}


def test_deploy_maps_a_lock_held_already_current_result_without_invoking_legacy_per_step_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The controller must consume one transaction result, not reintroduce separate steps."""

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: completed_transaction_payload(
            candidate=CURRENT, previous=CURRENT, deployed=False
        ),
    )

    options = confirmed_deploy_options(
        monkeypatch,
        current=CURRENT,
        current_sha256="c" * 64,
    )
    result = deploy(object(), config(), artifact(tmp_path, release_id=CURRENT), **options)

    assert result.changed is False
    assert result.stage == "already-current"
    assert result.exit_status is ExitStatus.OK


def test_cli_deploy_builds_or_reuses_an_exact_artifact_then_runs_the_deployment_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI controller must route deploy through the concrete workflow."""

    verified = artifact(tmp_path)
    remote = object()
    expected = object()
    seen: list[tuple[object, EnvironmentConfig, VerifiedArtifact]] = []
    monkeypatch.setattr("taskman_ops.config.load_environment", lambda name: config() if name == "production" else None)
    monkeypatch.setattr("taskman_ops.remote.connect", lambda environment: remote)
    monkeypatch.setattr("taskman_ops.build.build_release", lambda *_args: verified)
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.deploy",
        lambda actual_remote, environment, actual_artifact, **_kwargs: seen.append((actual_remote, environment, actual_artifact)) or expected,
    )

    result = dispatch(Invocation(command="deploy", environment="production"))

    assert result is expected
    assert seen == [(remote, config(), verified)]


def test_cli_manual_current_adoption_requires_an_explicit_deploy_flag() -> None:
    """A normal deploy must never silently adopt an unrecorded current link."""

    normal = parse_invocation(["deploy", "production"])
    confirmed = parse_invocation(["deploy", "production", "--adopt-manual-current"])

    assert normal.manual_adoption_confirmed is False
    assert confirmed.manual_adoption_confirmed is True


def test_deploy_passes_only_the_explicit_manual_adoption_confirmation_into_the_locked_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execution must consume the exact manual authority and policy the operator confirmed."""

    remote = object()
    manual = ManualAdoptionCandidate(
        schema_version=1,
        release_id=CURRENT,
        release_path=PurePosixPath("/opt/taskman/releases/operator-baseline"),
        content_sha256="e" * 64,
        application_version="0.2.0",
        migrations=(),
    )
    presented: list[dict[str, object]] = []
    seen: list[dict[str, object]] = []

    class ManualStore:
        def __init__(self) -> None:
            self.remote = remote
            self.inspection_calls = 0

        def inspect_manual_current_release(
            self, *, lock_timeout_seconds: float
        ) -> ManualAdoptionCandidate:
            assert lock_timeout_seconds == 5
            self.inspection_calls += 1
            return manual

    store = ManualStore()

    def transaction(*_args: object, **kwargs: object) -> dict[str, object]:
        seen.append(dict(kwargs))
        return completed_transaction_payload(candidate=CURRENT, previous=CURRENT, deployed=False)

    monkeypatch.setattr("taskman_ops.workflows.deploy.run_locked_deployment", transaction)

    result = deploy(
        remote,
        config(),
        artifact(tmp_path, release_id=CURRENT),
        lifecycle_store=store,  # type: ignore[arg-type]
        manual_adoption_confirmed=True,
        present_plan=lambda plan: presented.append(dict(plan)),
        confirm=lambda _plan: True,
    )

    assert result.stage == "already-current"
    assert store.inspection_calls == 1
    assert presented[0]["current_release_id"] == CURRENT
    assert presented[0]["new_migrations"] is False
    assert presented[0]["migration_policy"] == "no-change"
    assert seen[0]["manual_adoption_confirmed"] is True
    assert seen[0]["expected_previous_release_id"] == CURRENT
    assert seen[0]["expected_manual_adoption"] == manual
    assert seen[0]["migration_policy"] == presented[0]["migration_policy"]


def test_manual_adoption_plan_resolves_migration_consequences_before_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown or deferred migration facts must never reach the confirmation boundary."""

    remote = object()
    migration = MigrationFingerprint("20260905000000_create_records.exs", "d" * 64)
    manual = ManualAdoptionCandidate(
        schema_version=1,
        release_id=CURRENT,
        release_path=PurePosixPath("/opt/taskman/releases/operator-baseline"),
        content_sha256="e" * 64,
        application_version="0.2.0",
        migrations=(migration,),
    )

    class ManualStore:
        def __init__(self) -> None:
            self.remote = remote

        def inspect_manual_current_release(
            self, *, lock_timeout_seconds: float
        ) -> ManualAdoptionCandidate:
            return manual

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: pytest.fail("cancelled plan started a transaction"),
    )
    confirmed: list[dict[str, object]] = []

    result = deploy(
        remote,
        config(),
        artifact(tmp_path),
        lifecycle_store=ManualStore(),  # type: ignore[arg-type]
        migration_policy="backward-compatible",
        manual_adoption_confirmed=True,
        present_plan=lambda _plan: None,
        confirm=lambda plan: confirmed.append(dict(plan)) or False,
    )

    assert result.stage == "confirmation-cancelled"
    assert confirmed[0]["current_release_id"] == CURRENT
    assert confirmed[0]["new_migrations"] is True
    assert confirmed[0]["migration_policy"] == "backward-compatible"
    assert "unknown" not in repr(confirmed[0])


def test_deploy_dry_run_preflights_and_plans_without_staging_or_changing_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowing --dry-run to reach upload, backup, or activation must fail this."""

    monkeypatch.setattr("taskman_ops.workflows.deploy.inspect_deploy_state", lambda *_args, **_kwargs: (CURRENT, (), True, "a" * 64))
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: pytest.fail("dry run started a deployment transaction"),
    )

    result = deploy(
        object(),
        config(),
        artifact(tmp_path),
        migration_policy="no-change",
        confirm=lambda _plan: pytest.fail("dry run requested confirmation"),
        present_plan=lambda _plan: pytest.fail("dry run separately presented its returned plan"),
        dry_run=True,
    )

    assert result.changed is False
    assert result.stage == "planned"
    assert result.facts["previous_release_id"] == CURRENT
    assert result.facts["candidate_release_id"] == CANDIDATE


@pytest.mark.parametrize("manual_adoption", (False, True))
def test_deploy_runs_operational_preflight_before_lifecycle_or_adoption_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manual_adoption: bool,
) -> None:
    events: list[str] = []
    remote = object()

    class Store:
        def __init__(self) -> None:
            self.remote = remote

        def read(self, **_kwargs: object) -> object:
            events.append("lifecycle")
            raise AssertionError("preflight refusal must precede lifecycle inspection")

        def inspect_manual_current_release(self, **_kwargs: object) -> object:
            events.append("adoption")
            raise AssertionError("preflight refusal must precede adoption inspection")

    def refuse(*_args: object) -> None:
        events.append("operational-preflight")
        raise OpsError(
            ExitStatus.REMOTE_PREFLIGHT,
            "preflight",
            "runtime environment metadata is invalid",
            changed=False,
        )

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.validate_operational_preflight", refuse
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: pytest.fail("preflight refusal reached staging"),
    )

    result = deploy(
        remote,
        config(),
        artifact(tmp_path),
        lifecycle_store=Store(),  # type: ignore[arg-type]
        manual_adoption_confirmed=manual_adoption,
        dry_run=True,
    )

    assert result.stage == "preflight-failed"
    assert result.changed is False
    assert events == ["operational-preflight"]


def test_deploy_presents_the_exact_redacted_plan_and_requires_confirmation_before_the_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment must not acquire its mutating transaction before operator confirmation."""

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.inspect_deploy_state",
        lambda *_args, **_kwargs: (CURRENT, (), True, "a" * 64),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: pytest.fail("cancelled deployment acquired its transaction"),
    )
    presented: list[dict[str, object]] = []
    confirmed: list[dict[str, object]] = []

    result = deploy(
        object(),
        config(),
        artifact(tmp_path),
        migration_policy="no-change",
        present_plan=lambda plan: presented.append(dict(plan)),
        confirm=lambda plan: confirmed.append(dict(plan)) or False,
    )

    assert result.stage == "confirmation-cancelled"
    assert result.changed is False
    assert presented == confirmed
    assert confirmed == [
        {
            "environment": "production",
            "ssh_destination": "deployer@203.0.113.10:22",
            "public_hostname": "taskman.acme.tld",
            "current_release_id": CURRENT,
            "candidate_release_id": CANDIDATE,
            "source_revision": "b" * 40,
            "artifact_sha256": "c" * 64,
            "new_migrations": False,
            "migration_policy": "no-change",
            "planned_backup": True,
            "services_affected": ("taskman.service",),
            "expected_maintenance_window": "brief Taskman service interruption after backup",
        }
    ]
    assert result.facts["selected_release_id"] == CURRENT
    assert result.facts["database_state"] == "unchanged"


def test_deploy_binds_the_confirmed_current_release_to_the_locked_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A current-release change after confirmation must be refused by the host transaction."""

    seen: list[object] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.inspect_deploy_state",
        lambda *_args, **_kwargs: (CURRENT, (), True, "a" * 64),
    )

    def transaction(*_args: object, **kwargs: object) -> dict[str, object]:
        seen.append(kwargs["expected_previous_release_id"])
        return completed_transaction_payload(
            candidate=CANDIDATE,
            previous=CURRENT,
            deployed=True,
        )

    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        transaction,
    )

    result = deploy(
        object(),
        config(),
        artifact(tmp_path),
        migration_policy="no-change",
        present_plan=lambda _plan: None,
        confirm=lambda _plan: True,
    )

    assert result.stage == "deployed"
    assert seen == [CURRENT]


def test_deploy_dry_run_never_allows_manual_adoption_to_mutate_lifecycle_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit future adoption still cannot make a dry run write host evidence."""

    from taskman_ops.workflows.operational_preflight import (
        validate_operational_preflight as validate_real_preflight,
    )

    class PreflightRemote:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            self.calls.append(argv)
            return CommandResult(0)

    remote = PreflightRemote()

    class ManualStore:
        def __init__(self) -> None:
            self.remote = remote
            self.inspections = 0

        def inspect_manual_current_release(
            self, *, lock_timeout_seconds: float
        ) -> ManualAdoptionCandidate:
            self.inspections += 1
            return ManualAdoptionCandidate(
                schema_version=1,
                release_id=CURRENT,
                release_path=PurePosixPath("/opt/taskman/releases/operator-baseline"),
                content_sha256="e" * 64,
                application_version="0.2.0",
                migrations=(),
            )

    store = ManualStore()
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: pytest.fail("dry run started adoption or deployment"),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.validate_operational_preflight",
        lambda actual_remote, actual_config: validate_real_preflight(
            actual_remote,
            actual_config,
            host_validator=lambda *_args: object(),
        ),
    )

    result = deploy(
        remote,
        config(),
        artifact(tmp_path),
        lifecycle_store=store,  # type: ignore[arg-type]
        migration_policy="no-change",
        manual_adoption_confirmed=True,
        dry_run=True,
    )

    assert result.stage == "planned"
    assert result.facts["previous_release_id"] == CURRENT
    assert store.inspections == 1
    assert len(remote.calls) == 2
    assert "/etc/taskman/taskman.env" in remote.calls[0]
    assert "pg_database_size" in remote.calls[1][2]


def test_deploy_state_inspection_refuses_before_calling_the_adoption_writer() -> None:
    """The read-only inspection boundary must never be an adoption backdoor."""

    remote = object()

    class ManualStore:
        def __init__(self) -> None:
            self.remote = remote
            self.adoption_calls = 0

        def read(self, **_kwargs: object) -> tuple[object, object]:
            raise OpsError(ExitStatus.SAFETY, "lifecycle", "manual current", changed=False)

        def adopt_current_release(self, **_kwargs: object) -> object:
            self.adoption_calls += 1
            raise AssertionError("dry run tried to adopt")

    store = ManualStore()

    with pytest.raises(OpsError) as raised:
        inspect_deploy_state(
            remote,
            config(),
            store,  # type: ignore[arg-type]
            lock_timeout_seconds=5,
        )

    assert raised.value.status is ExitStatus.SAFETY
    assert store.adoption_calls == 0


def test_deploy_preserves_an_unknown_activation_record_state_from_a_malformed_success_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coercing unknown lifecycle evidence to true or false would mislead recovery."""

    def malformed_success(*_args: object, **_kwargs: object) -> object:
        error = OpsError(ExitStatus.RELEASE, "activation", "invalid success", changed=True)
        error.activation_recorded = "unknown"
        raise error

    monkeypatch.setattr("taskman_ops.workflows.deploy.run_locked_deployment", malformed_success)

    options = confirmed_deploy_options(monkeypatch)
    result = deploy(object(), config(), artifact(tmp_path), migration_policy="no-change", **options)

    assert result.facts["activation_recorded"] == "unknown"


def test_deploy_maps_a_completed_lock_held_transaction_to_truthful_success_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: completed_transaction_payload(
            candidate=CANDIDATE, previous=CURRENT, deployed=True
        ),
    )

    options = confirmed_deploy_options(monkeypatch)
    result = deploy(object(), config(), artifact(tmp_path), migration_policy="no-change", **options)

    assert result.stage == "deployed"
    assert result.changed is True
    assert result.facts["previous_release_id"] == CURRENT
    assert result.facts["backup_id"] == "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_deploy_retains_the_strict_transaction_evidence_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping observed stages, residue, or recovery evidence at the controller boundary is unsafe."""

    verification = {
        "schema_version": 1,
        "status": "ok",
        "exit_status": 0,
        "release_id": CANDIDATE,
        "expected_release_id": CANDIDATE,
        "checks": [
            {"schema_version": 1, "name": name, "status": "passed", "summary": summary}
            for name, summary in (
                ("taskman-service", "taskman.service is active with a positive MainPID"),
                ("release-identity", "systemd MainPID executable is under the selected release"),
                ("caddy-service", "caddy.service is active"),
                ("listener-topology", "Taskman, distribution, and PostgreSQL listeners have the required topology"),
                ("startup-journal", "recent startup journal evidence is clean"),
                ("local-readiness", "loopback health endpoint returned exact ready response"),
                ("public-readiness", "public HTTPS health endpoint returned exact ready response"),
                ("public-hsts", "public HTTPS response includes HSTS"),
            )
        ],
        "next_action": None,
    }
    payload = {
        "stage": "deployed",
        "previous_release_id": CURRENT,
        "candidate_release_id": CANDIDATE,
        "selected_release_id": CANDIDATE,
        "backup_id": "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "activation_id": "activation-" + "d" * 32,
        "service_state": "active",
        "database_state": "unchanged",
        "activation_recorded": True,
        "changed": True,
        "changed_stages": [
            "staging",
            "backup",
            "stop",
            "migration",
            "selection",
            "start",
            "verification",
            "records",
        ],
        "warnings": ["unexpected deployment-root entry: uploads"],
        "recovery_commands": [
            "systemctl status taskman.service",
            f"readlink -f {config().managed_root}/current",
            "journalctl --no-pager --unit taskman.service --lines=100",
            (
                "stat -Lc '%U:%G %a %F %n' -- "
                f"{config().deployment_root}/uploads/.upload-foreign"
            ),
        ],
        "residue_paths": [
            f"{config().deployment_root}/uploads/.upload-foreign",
        ],
        "verification": verification,
    }
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: payload,
    )

    options = confirmed_deploy_options(monkeypatch)
    result = deploy(object(), config(), artifact(tmp_path), migration_policy="no-change", **options)

    assert result.changed is True
    assert result.warnings == ("unexpected deployment-root entry: uploads",)
    assert result.facts["changed_stages"] == tuple(payload["changed_stages"])
    assert result.facts["recovery_commands"] == tuple(payload["recovery_commands"])
    assert result.facts["residue_paths"] == tuple(payload["residue_paths"])
    assert result.facts["verification"] == verification
    assert result.facts["selected_release_id"] == CANDIDATE
    assert result.facts["service_state"] == "active"
    assert result.facts["database_state"] == "unchanged"
    assert result.facts["activation_recorded"] is True


def test_malformed_remote_transaction_state_is_a_changed_safety_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed remote envelope may follow mutation and must never be rendered as unchanged."""

    error = OpsError(ExitStatus.SAFETY, "deploy", "invalid transaction state", changed=True)
    error.selected_release_id = "unknown"
    error.database_state = "unknown"
    error.service_state = "unknown"
    error.activation_recorded = "unknown"
    error.changed_stages = ()
    error.warnings = ()
    error.recovery_commands = (
        "systemctl status taskman.service",
        f"readlink -f {config().managed_root}/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
    )
    error.residue_paths = ()
    monkeypatch.setattr(
        "taskman_ops.workflows.deploy.run_locked_deployment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    options = confirmed_deploy_options(monkeypatch)
    result = deploy(object(), config(), artifact(tmp_path), migration_policy="no-change", **options)

    assert result.exit_status is ExitStatus.SAFETY
    assert result.changed is True
    assert result.facts["selected_release_id"] == "unknown"
    assert result.facts["database_state"] == "unknown"
    assert result.facts["service_state"] == "unknown"
    assert result.facts["activation_recorded"] == "unknown"
    assert result.facts["changed_stages"] == ()
    assert result.facts["recovery_commands"] == error.recovery_commands
