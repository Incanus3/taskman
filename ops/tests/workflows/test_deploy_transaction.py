"""The controller deployment boundary has no executable remote transaction."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fakes import HelperRunnerRemote
from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.helper_package import HelperPackage, build_helper_package
from taskman_ops.helper_runner import HelperInvocation
from taskman_ops.host_protocol import HostResult, encode_result
from taskman_ops.manifests import ArtifactManifest, BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, VerifiedArtifact
from taskman_ops.remote import CommandResult
from taskman_ops.workflows.helper_deploy import run_helper_deployment


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CANDIDATE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate({
        "name": "production", "ssh_host": "203.0.113.10", "ssh_port": 22, "ssh_user": "deployer",
        "host_key_fingerprint": "SHA256:" + "A" * 43, "public_hostname": "taskman.acme.tld",
        "public_ipv4": "203.0.113.10", "target_os": "ubuntu26.04", "architecture": "amd64",
        "application_port": 4000, "distribution_port": 6789, "database_name": "taskman_prod",
        "database_role": "taskman", "mail_from": "no-reply@acme.tld",
    })


def _artifact(tmp_path: Path) -> VerifiedArtifact:
    archive = tmp_path / "artifact.tar.gz"
    archive.write_bytes(b"archive")
    manifest = ArtifactManifest(2, "taskman", "0.2.0", "b" * 40, CANDIDATE, datetime(2026, 9, 5, tzinfo=UTC), "ubuntu26.04", "amd64", "27.3.4.6", "1.18.3", "22.22.1", BUILDER_BASE_TAG, BUILDER_BASE_DIGEST, (), "taskman")
    return VerifiedArtifact(archive, tmp_path / "manifest", tmp_path / "checksum", "c" * 64, manifest)


class _Remote:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def run(self, argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        self.calls.append(argv)
        return CommandResult(ExitStatus.OK)

    def put(self, source: Path, destination: Path, **_kwargs: object) -> None:
        self.calls.append((source, destination))


def _successful_verification() -> dict[str, object]:
    checks = (
        ("taskman-service", "taskman.service is active with a positive MainPID"),
        ("release-identity", "systemd MainPID executable is under the selected release"),
        ("caddy-service", "caddy.service is active"),
        ("listener-topology", "Taskman, distribution, and PostgreSQL listeners have the required topology"),
        ("startup-journal", "recent startup journal evidence is clean"),
        ("local-readiness", "loopback health endpoint returned exact ready response"),
        ("public-readiness", "public HTTPS health endpoint returned exact ready response"),
        ("public-hsts", "public HTTPS response includes HSTS"),
    )
    return {
        "schema_version": 1,
        "status": "ok",
        "exit_status": 0,
        "release_id": CANDIDATE,
        "expected_release_id": CANDIDATE,
        "checks": tuple(
            {"schema_version": 1, "name": name, "status": "passed", "summary": summary}
            for name, summary in checks
        ),
        "next_action": None,
    }


def _invocation(request: object, *, outcome: str = "succeeded", stage: str = "records") -> HelperInvocation:
    previous = request.expected_state["previous_release_id"]
    genesis = request.operation == "genesis"
    token = request.operation_id.removeprefix("op-")
    return HelperInvocation(
        result=HostResult(
            protocol_version=1, operation=request.operation, operation_id=request.operation_id,
            outcome=outcome, stage=stage,
            changed_stages=("staging", "backup", "migration", "selection", "start", "records") if genesis else ("staging", "backup", "stop", "migration", "selection", "start", "records"),
            lifecycle={"previous_release_id": previous, "candidate_release_id": CANDIDATE, "selected_release_id": CANDIDATE, "backup_id": "backup-" + token, "activation_id": "activation-" + token, "service_state": "active", "database_state": "unchanged", "activation_recorded": True},
            runtime_state={}, verification=_successful_verification(), residue_paths=(), recovery_actions=(), warnings=(),
        ), cleanup_warning=None,
    )


def test_one_transfer_and_one_correlated_helper_request(tmp_path: Path) -> None:
    remote = _Remote()
    seen: list[object] = []

    payload = run_helper_deployment(remote, _config(), _artifact(tmp_path), previous_release_id=CURRENT, current_migrations=(), migration_policy="no-change", package=HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1), invoker=lambda _remote, _package, request: seen.append(request) or _invocation(request))

    assert payload["stage"] == "deployed"
    assert len(seen) == 1
    assert seen[0].operation == "deploy"
    assert seen[0].expected_state == {"previous_release_id": CURRENT, "current_migrations": ()}
    assert len(remote.calls) == 2


def test_genesis_has_absent_predecessor(tmp_path: Path) -> None:
    remote = _Remote()
    seen: list[object] = []

    run_helper_deployment(remote, _config(), _artifact(tmp_path), previous_release_id=None, current_migrations=(), migration_policy="no-change", genesis=True, package=HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1), invoker=lambda _remote, _package, request: seen.append(request) or _invocation(request))

    assert seen[0].operation == "genesis"
    assert seen[0].expected_state == {"previous_release_id": None, "current_migrations": ()}


def test_helper_refusal_and_correlation_failure_are_fail_closed(tmp_path: Path) -> None:
    remote = _Remote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)
    with pytest.raises(OpsError):
        run_helper_deployment(remote, _config(), _artifact(tmp_path), previous_release_id=CURRENT, current_migrations=(), migration_policy="no-change", package=package, invoker=lambda _remote, _package, request: _invocation(request, outcome="refused", stage="deploy-preflight"))

    with pytest.raises(OpsError):
        run_helper_deployment(remote, _config(), _artifact(tmp_path), previous_release_id=CURRENT, current_migrations=(), migration_policy="no-change", package=package, invoker=lambda _remote, _package, request: HelperInvocation(result=HostResult(protocol_version=1, operation="genesis", operation_id=request.operation_id, outcome="failed", stage="deploy-preflight", changed_stages=(), lifecycle={}, runtime_state={}, verification={}, residue_paths=(), recovery_actions=(), warnings=()), cleanup_warning=None))


@pytest.mark.parametrize(
    "failure_stage", ("helper-package", "helper-install", "helper-invoke")
)
def test_controller_removes_its_uploaded_artifact_when_helper_setup_fails_before_entry(
    tmp_path: Path, failure_stage: str
) -> None:
    """Package, install, and invocation setup leave upload ownership with the controller."""

    remote = _Remote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)

    def setup_failure(_remote: object, _package: object, _request: object) -> HelperInvocation:
        error = OpsError(ExitStatus.RELEASE, failure_stage, "helper setup transport failed")
        error.helper_entry_dispatched = False
        raise error

    with pytest.raises(OpsError, match="helper setup transport failed") as raised:
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
            invoker=setup_failure,
        )

    cleanup = remote.calls[-1]
    assert cleanup[:3] == ("rm", "-f", "--")
    assert cleanup[3].startswith("/opt/taskman/deployments/uploads/.upload-")
    assert not hasattr(raised.value, "residue_paths")


def test_controller_preserves_uploaded_artifact_residue_when_pre_entry_cleanup_fails(
    tmp_path: Path,
) -> None:
    """Cleanup trouble supplements, never replaces, the setup failure."""

    class CleanupFailureRemote(_Remote):
        def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
            self.calls.append(argv)
            if argv[:2] == ("rm", "-f"):
                return CommandResult(ExitStatus.RELEASE)
            return CommandResult(ExitStatus.OK)

    remote = CleanupFailureRemote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)

    def setup_failure(_remote: object, _package: object, _request: object) -> HelperInvocation:
        error = OpsError(ExitStatus.RELEASE, "helper-install", "helper setup transport failed")
        error.helper_entry_dispatched = False
        raise error

    with pytest.raises(OpsError, match="helper setup transport failed") as raised:
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
            invoker=setup_failure,
        )

    error = raised.value
    assert error.stage == "helper-install"
    assert error.residue_paths[0].startswith("/opt/taskman/deployments/uploads/.upload-")
    assert error.recovery_commands == ("remove the uploaded deployment artifact before retrying",)
    assert error.warnings == ("unable to remove uploaded deployment artifact",)


@pytest.mark.parametrize("failure", ("ssh", "sudo", "nonzero", "uncorrelated"))
def test_controller_retains_exact_upload_after_an_ambiguous_actual_helper_runner_dispatch(
    tmp_path: Path, failure: str
) -> None:
    """Only an explicit pre-dispatch fact permits upload cleanup.

    SSH loss, sudo rejection, a nonzero helper command, and an uncorrelated
    result can all occur after Python has read the request. Removing the exact
    release artifact in those cases destroys an operation the helper may own.
    """

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    class AmbiguousInvocationRemote(HelperRunnerRemote):
        def run(self, argv: object, **kwargs: object) -> CommandResult:
            command = tuple(argv)  # type: ignore[arg-type]
            if command[:3] == ("sudo", "--preserve-env=SSH_CONNECTION", "--"):
                if failure == "ssh":
                    raise OSError("ssh-canary-transport")
                if failure in {"sudo", "nonzero"}:
                    return CommandResult(1, "", "sudo-canary-refusal")
                return CommandResult(
                    0,
                    encode_result(
                        HostResult(
                            protocol_version=1,
                            operation="deploy",
                            operation_id="op-fedcba9876543210fedcba9876543210",
                            outcome="failed",
                            stage="records",
                            changed_stages=(),
                            lifecycle={},
                            runtime_state={},
                            verification={},
                            residue_paths=(),
                            recovery_actions=(),
                            warnings=(),
                        )
                    ).decode("utf-8"),
                )
            return super().run(command, **kwargs)

    remote = AmbiguousInvocationRemote(
        checksum=package.sha256,
        helper_result=CommandResult(0, ""),
    )

    with pytest.raises(OpsError) as raised:
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
        )

    upload_cleanup = [
        command
        for command, _kwargs in remote.calls
        if command[:3] == ("rm", "-f", "--")
    ]
    assert upload_cleanup == []
    assert getattr(raised.value, "helper_entry_started") is False
    assert getattr(raised.value, "helper_entry_dispatched") is True
    assert getattr(raised.value, "helper_entry_completed") is False
    operation_id = remote.uploads[0][1].as_posix().removesuffix(".tar.gz").rsplit("-op-", 1)[1]
    invocation_directory = f"/run/taskman-ops/op-{operation_id}"
    installed_helper = f"{invocation_directory}/taskman-host.pyz"
    assert raised.value.residue_paths == (
        installed_helper,
        invocation_directory,
        remote.uploads[0][1].as_posix(),
    )
    assert raised.value.recovery_commands == (
        "inspect the transient helper invocation artifact before retrying",
        "inspect the uploaded deployment artifact before retrying",
    )
    assert ("rm", "--", installed_helper) not in [command for command, _kwargs in remote.calls]
    assert ("rmdir", "--", invocation_directory) not in [command for command, _kwargs in remote.calls]
    assert "canary" not in repr(raised.value)


@pytest.mark.parametrize(
    "transport_error",
    (
        OSError("ssh-canary-transport"),
        RuntimeError("runner-canary-transport"),
    ),
)
def test_controller_retains_invocation_and_release_artifacts_after_unknown_runner_completion(
    tmp_path: Path, transport_error: Exception
) -> None:
    """An unreturned helper command may still be reading its installed archive.

    Removing either private invocation path after a lost runner response can
    terminate or corrupt an in-flight helper. Removing the release upload has
    the same ownership problem at the controller boundary.
    """

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    class UnknownCompletionRemote(HelperRunnerRemote):
        def run(self, argv: object, **kwargs: object) -> CommandResult:
            command = tuple(argv)  # type: ignore[arg-type]
            if command[:3] == ("sudo", "--preserve-env=SSH_CONNECTION", "--"):
                raise transport_error
            return super().run(command, **kwargs)

    remote = UnknownCompletionRemote(
        checksum=package.sha256,
        helper_result=CommandResult(0, ""),
    )

    with pytest.raises(OpsError) as raised:
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
        )

    error = raised.value
    upload = remote.uploads[0][1].as_posix()
    operation_id = upload.removesuffix(".tar.gz").rsplit("-op-", 1)[1]
    invocation_directory = f"/run/taskman-ops/op-{operation_id}"
    installed_helper = f"{invocation_directory}/taskman-host.pyz"
    commands = [command for command, _kwargs in remote.calls]

    assert error.helper_entry_dispatched is True
    assert error.helper_entry_completed is False
    assert ("rm", "--", installed_helper) not in commands
    assert ("rmdir", "--", invocation_directory) not in commands
    assert not any(command[:3] == ("rm", "-f", "--") for command in commands)
    assert error.residue_paths == (installed_helper, invocation_directory, upload)
    assert error.recovery_commands == (
        "inspect the transient helper invocation artifact before retrying",
        "inspect the uploaded deployment artifact before retrying",
    )
    assert error.warnings == (
        "helper invocation completion is uncertain; installed helper artifact was retained",
        "helper entrypoint outcome is uncertain; uploaded deployment artifact was retained",
    )
    assert "canary" not in repr(error)


def test_controller_retains_uncertain_upload_after_actual_runner_transport_failure(
    tmp_path: Path,
) -> None:
    """A dispatched runner failure retains instead of deleting the upload.

    An SSH loss can follow Python request entry. Retrying a cleanup here could
    delete an artifact the helper may still be reading or preserving as state.
    """

    package = build_helper_package(tmp_path / "taskman-host.pyz")

    class UncertainCleanupRemote(HelperRunnerRemote):
        def run(self, argv: object, **kwargs: object) -> CommandResult:
            command = tuple(argv)  # type: ignore[arg-type]
            if command[:3] == ("sudo", "--preserve-env=SSH_CONNECTION", "--"):
                raise OSError("ssh-canary-transport")
            if command[:3] == ("rm", "-f", "--"):
                self.calls.append((command, kwargs))
                return CommandResult(1)
            return super().run(command, **kwargs)

    remote = UncertainCleanupRemote(
        checksum=package.sha256,
        helper_result=CommandResult(0, ""),
    )

    with pytest.raises(OpsError) as raised:
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
        )

    error = raised.value
    operation_id = remote.uploads[0][1].as_posix().removesuffix(".tar.gz").rsplit("-op-", 1)[1]
    invocation_directory = f"/run/taskman-ops/op-{operation_id}"
    installed_helper = f"{invocation_directory}/taskman-host.pyz"
    assert error.residue_paths == (
        installed_helper,
        invocation_directory,
        remote.uploads[0][1].as_posix(),
    )
    assert error.recovery_commands == (
        "inspect the transient helper invocation artifact before retrying",
        "inspect the uploaded deployment artifact before retrying",
    )
    assert error.warnings == (
        "helper invocation completion is uncertain; installed helper artifact was retained",
        "helper entrypoint outcome is uncertain; uploaded deployment artifact was retained",
    )
    assert ("rm", "--", installed_helper) not in [command for command, _kwargs in remote.calls]
    assert ("rmdir", "--", invocation_directory) not in [command for command, _kwargs in remote.calls]
    assert not any(command[:3] == ("rm", "-f", "--") for command, _kwargs in remote.calls)
    assert "canary" not in repr(error)


def test_controller_accepts_one_correlated_resumed_provisional(tmp_path: Path) -> None:
    """A fresh helper operation may finish exactly one older immutable stage."""

    remote = _Remote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)
    previous_token = "f" * 32

    def resumed(_remote: object, _package: object, request: object) -> HelperInvocation:
        valid = _invocation(request).result
        activation = "activation-" + previous_token
        return HelperInvocation(
            result=replace(
                valid,
                changed_stages=("start", "records"),
                lifecycle={
                    **valid.lifecycle,
                    "backup_id": "backup-" + previous_token,
                    "activation_id": activation,
                },
                runtime_state={"resumed_activation_id": activation},
            )
        )

    payload = run_helper_deployment(
        remote, _config(), _artifact(tmp_path), previous_release_id=CURRENT,
        current_migrations=(), migration_policy="no-change", package=package, invoker=resumed,
    )

    assert payload["activation_id"] == "activation-" + previous_token
    assert payload["changed_stages"] == ("start", "records")


@pytest.mark.parametrize("mismatched_field", ("backup_id", "activation_id"))
def test_controller_rejects_resumed_records_with_different_prior_operation_tokens(
    tmp_path: Path, mismatched_field: str
) -> None:
    """A resume must bind backup, activation, and resume evidence to one prior token.

    Checking each identifier against its own regex accepts a synthetic backup
    or activation from a different interrupted operation.
    """

    remote = _Remote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)
    prior_token = "f" * 32
    different_token = "e" * 32

    def mismatched(_remote: object, _package: object, request: object) -> HelperInvocation:
        valid = _invocation(request).result
        activation = "activation-" + (different_token if mismatched_field == "activation_id" else prior_token)
        backup = "backup-" + (different_token if mismatched_field == "backup_id" else prior_token)
        return HelperInvocation(
            replace(
                valid,
                changed_stages=("start", "records"),
                lifecycle={**valid.lifecycle, "backup_id": backup, "activation_id": activation},
                runtime_state={"resumed_activation_id": activation},
            )
        )

    with pytest.raises(OpsError, match="invalid success evidence"):
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
            invoker=mismatched,
        )


@pytest.mark.parametrize(
    ("stage", "action", "changed_stages"),
    (
        ("staging", "inspect the immutable release staging evidence before retrying", ("staging",)),
        ("backup", "preserve the validated deployment backup and inspect its lifecycle record before retrying", ("staging", "backup")),
        ("selection", "inspect the current release selection before retrying", ("staging", "backup", "selection")),
        ("records", "inspect published lifecycle records and the provisional activation before retrying", ("staging", "backup", "selection", "records")),
    ),
)
def test_controller_preserves_exact_helper_stage_recovery_actions(
    tmp_path: Path, stage: str, action: str, changed_stages: tuple[str, ...]
) -> None:
    """Helper recovery instructions remain actionable at the controller boundary.

    Replacing a helper's concrete stage action with the controller's generic
    deploy text would leave a partial backup, selection, or record state with
    no truthful next step.
    """

    remote = _Remote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)

    def failed(_remote: object, _package: object, request: object) -> HelperInvocation:
        return HelperInvocation(
            HostResult(
                protocol_version=1,
                operation=request.operation,
                operation_id=request.operation_id,
                outcome="failed",
                stage=stage,
                changed_stages=changed_stages,
                lifecycle={},
                runtime_state={},
                verification={},
                residue_paths=(),
                recovery_actions=(action,),
                warnings=("redacted helper warning",),
            )
        )

    with pytest.raises(OpsError) as raised:
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
            invoker=failed,
        )

    assert raised.value.recovery_commands == (action,)
    assert raised.value.warnings == ("redacted helper warning",)


@pytest.mark.parametrize(
    ("lifecycle_update", "changed_stages", "verification"),
    [
        ({"selected_release_id": CURRENT}, None, None),
        ({"activation_recorded": False}, None, None),
        ({"service_state": "stopped"}, None, None),
        ({"database_state": "unknown"}, None, None),
        ({}, ("staging", "migration"), None),
        ({}, None, {"schema_version": 1, "status": "failed", "exit_status": 9, "release_id": CANDIDATE, "expected_release_id": CANDIDATE, "checks": (), "next_action": "inspect"}),
    ],
)
def test_controller_rejects_impossible_succeeded_helper_evidence(
    tmp_path: Path,
    lifecycle_update: dict[str, object],
    changed_stages: tuple[str, ...] | None,
    verification: dict[str, object] | None,
) -> None:
    """A correlated success must prove the exact activation it claims.

    Dropping any strict success invariant would let a helper report deployment
    complete despite an old selection, unpublished records, stopped service,
    unknown database outcome, an impossible stage path, or failed verification.
    """

    remote = _Remote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)

    def impossible(_remote: object, _package: object, request: object) -> HelperInvocation:
        valid = _invocation(request).result
        lifecycle = {**valid.lifecycle, **lifecycle_update}
        return HelperInvocation(
            result=replace(
                valid,
                lifecycle=lifecycle,
                changed_stages=valid.changed_stages if changed_stages is None else changed_stages,
                verification=valid.verification if verification is None else verification,
            )
        )

    with pytest.raises(OpsError, match="invalid success evidence"):
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
            invoker=impossible,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: replace(result, recovery_actions=("inspect safely",)),
        lambda result: replace(result, runtime_state={"resumed_activation_id": result.lifecycle["activation_id"]}),
        lambda result: replace(
            result,
            verification={
                **result.verification,
                "checks": (*result.verification["checks"][:-1], {"schema_version": 1, "name": "public-hsts", "status": "failed", "summary": "unexpected"}),
            },
        ),
        lambda result: replace(
            result,
            verification={**result.verification, "checks": result.verification["checks"][:-1]},
        ),
        lambda result: replace(
            result,
            verification={**result.verification, "schema_version": 2},
        ),
        lambda result: replace(
            result,
            verification={
                **result.verification,
                "checks": (
                    {**result.verification["checks"][0], "unexpected": True},
                    *result.verification["checks"][1:],
                ),
            },
        ),
    ],
)
def test_controller_rejects_success_with_noncanonical_runtime_recovery_or_verification_planes(
    tmp_path: Path, mutation: object
) -> None:
    """Correlated success cannot carry a made-up resume, recovery, or check report.

    Removing strict plane validation lets a helper claim a normal activation
    resumed, attach unresolved recovery instructions, or replace the fixed
    successful verification report with arbitrary data.
    """

    remote = _Remote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)

    def invalid(_remote: object, _package: object, request: object) -> HelperInvocation:
        return HelperInvocation(mutation(_invocation(request).result))

    with pytest.raises(OpsError, match="invalid success evidence"):
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
            invoker=invalid,
        )


@pytest.mark.parametrize(
    ("runtime_state", "recovery_actions"),
    [
        ({"resumed_activation_id": "activation-" + "b" * 32}, ()),
        ({}, ("inspect safely",)),
    ],
)
def test_controller_rejects_noncanonical_noop_runtime_and_recovery_planes(
    tmp_path: Path, runtime_state: dict[str, object], recovery_actions: tuple[str, ...]
) -> None:
    """A no-op has no resume transition and no unresolved recovery action."""

    remote = _Remote()
    package = HelperPackage(tmp_path / "helper.pyz", "a" * 64, 1)

    def invalid(_remote: object, _package: object, request: object) -> HelperInvocation:
        valid = _invocation(request).result
        return HelperInvocation(
            replace(
                valid,
                outcome="no_change",
                stage="already-current",
                changed_stages=(),
                lifecycle={**valid.lifecycle, "backup_id": None},
                runtime_state=runtime_state,
                recovery_actions=recovery_actions,
            )
        )

    with pytest.raises(OpsError, match="invalid no-change evidence"):
        run_helper_deployment(
            remote,
            _config(),
            _artifact(tmp_path),
            previous_release_id=CURRENT,
            current_migrations=(),
            migration_policy="no-change",
            package=package,
            invoker=invalid,
        )
