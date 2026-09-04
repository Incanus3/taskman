"""Candidate activation and migration-policy contracts."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import PurePosixPath
import subprocess

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.manifests import ArtifactManifest, MigrationFingerprint
from taskman_ops.remote import CommandResult
from taskman_ops.releases.activation import _ACTIVATION_BODY, activate_release, resolve_migration_policy
from taskman_ops.releases.records import BackupRecord, RemoteLifecycleStore


CURRENT = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
CANDIDATE = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"


class ActivationRemote:
    def __init__(self, response: CommandResult) -> None:
        self.response = response
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((tuple(argv), kwargs))
        return self.response


def store(remote: ActivationRemote) -> RemoteLifecycleStore:
    return RemoteLifecycleStore(
        remote,  # type: ignore[arg-type]
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )


def backup() -> BackupRecord:
    return BackupRecord(
        1,
        "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        17,
        17,
        "taskman_prod",
        CURRENT,
        CANDIDATE,
        "pre-deploy",
        True,
        PurePosixPath("/var/backups/taskman/backup.dump"),
    )


def manifest() -> ArtifactManifest:
    return ArtifactManifest(
        1,
        "taskman",
        "0.2.0",
        "b" * 40,
        CANDIDATE,
        datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "ubuntu26.04",
        "amd64",
        "27.3.4.6",
        "1.18.3",
        "22.22.1",
        (),
        "taskman",
    )


def test_resolve_migration_policy_requires_an_explicit_operator_declaration_for_changed_migrations() -> None:
    """Defaulting changed migrations to a rollback claim must fail this."""

    current = (MigrationFingerprint("20260905000000_create_tasks.exs", "a" * 64),)
    candidate = (MigrationFingerprint("20260905000000_create_tasks.exs", "b" * 64),)

    assert resolve_migration_policy(current, current, None) == "no-change"
    with pytest.raises(OpsError) as raised:
        resolve_migration_policy(current, candidate, None)
    assert raised.value.status is ExitStatus.SAFETY
    assert resolve_migration_policy(current, candidate, "restore-required") == "restore-required"


def test_activate_release_refuses_to_select_a_candidate_without_its_exact_manifest() -> None:
    """A recordable activation must never create a lifecycle edge without manifest evidence."""

    remote = ActivationRemote(CommandResult(0, '{"activation_id":"activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","activated_at":"2026-09-05T12:00:01Z"}\n'))

    with pytest.raises(OpsError) as raised:
        activate_release(
            remote,
            store(remote),
            previous_release_id=CURRENT,
            candidate_release_id=CANDIDATE,
            backup=backup(),
            migration_policy="no-change",
            artifact_sha256="b" * 64,
        )

    assert raised.value.status is ExitStatus.SAFETY
    assert remote.calls == []


def test_activate_release_keeps_caddy_running_and_orders_stop_migrate_select_start_under_the_exclusive_transaction() -> None:
    """Starting old code after a migration or stopping Caddy must fail this."""

    response = CommandResult(
        0,
        '{"activation_id":"activation-dddddddddddddddddddddddddddddddd","activated_at":"2026-09-05T12:00:01Z",'
        f'"service_state":"active","selected_release_id":"{CANDIDATE}","database_state":"unchanged",'
        '"activation_recorded":true,"changed_stages":["stop","migration","selection","start"]}\n',
    )
    remote = ActivationRemote(response)

    activation = activate_release(
        remote,
        store(remote),
        previous_release_id=CURRENT,
        candidate_release_id=CANDIDATE,
        backup=backup(),
        migration_policy="no-change",
        operation_token="d" * 32,
        manifest=manifest(),
        artifact_sha256="b" * 64,
    )

    assert activation.previous_release_id == CURRENT
    assert activation.candidate_release_id == CANDIDATE
    assert activation.activation_id == "activation-dddddddddddddddddddddddddddddddd"


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(ExitStatus.MIGRATION, ExitStatus.MIGRATION), (ExitStatus.RELEASE, ExitStatus.RELEASE)],
)
def test_activate_release_preserves_the_failure_boundary_without_automatic_rollback(
    returncode: ExitStatus, expected: ExitStatus
) -> None:
    """Collapsing migration and activation failures into a successful retry must fail this."""

    remote = ActivationRemote(CommandResult(returncode))

    with pytest.raises(OpsError) as raised:
        activate_release(
            remote,
            store(remote),
            previous_release_id=CURRENT,
            candidate_release_id=CANDIDATE,
            backup=backup(),
            migration_policy="no-change",
            operation_token="e" * 32,
            manifest=manifest(),
            artifact_sha256="b" * 64,
        )

    assert raised.value.status is expected
    assert raised.value.changed is True


@pytest.mark.parametrize(
    ("returncode", "payload", "selected", "service", "database_changed"),
    [
        (
            ExitStatus.MIGRATION,
            '{"service_state":"stopped","selected_release_id":"' + CURRENT + '","database_state":"unknown","activation_recorded":false,"changed_stages":["stop","migration"]}\n',
            CURRENT,
            "stopped",
            True,
        ),
        (
            ExitStatus.RELEASE,
            '{"service_state":"unknown","selected_release_id":"' + CANDIDATE + '","database_state":"changed","activation_recorded":false,"changed_stages":["stop","migration","selection","start"]}\n',
            CANDIDATE,
            "unknown",
            True,
        ),
        (
            ExitStatus.RELEASE,
            '{"service_state":"unknown","selected_release_id":"' + CURRENT + '","database_state":"unchanged","activation_recorded":false,"changed_stages":["stop"]}\n',
            CURRENT,
            "unknown",
            False,
        ),
    ],
)
def test_activate_release_reports_the_remote_failure_state_without_claiming_a_rollback(
    returncode: ExitStatus,
    payload: str,
    selected: str,
    service: str,
    database_changed: bool,
) -> None:
    """Each failed activation boundary must carry observed recovery state."""

    remote = ActivationRemote(CommandResult(returncode, payload))

    with pytest.raises(OpsError) as raised:
        activate_release(
            remote,
            store(remote),
            previous_release_id=CURRENT,
            candidate_release_id=CANDIDATE,
            backup=backup(),
            migration_policy="no-change",
            manifest=manifest(),
            artifact_sha256="b" * 64,
        )

    assert raised.value.selected_release_id == selected
    assert raised.value.service_state == service
    assert raised.value.database_changed is database_changed
    assert raised.value.activation_recorded is False


def test_activate_release_does_not_guess_the_selected_release_when_a_failure_report_is_invalid() -> None:
    """An unreadable post-migration report cannot safely identify the current link."""

    remote = ActivationRemote(CommandResult(ExitStatus.RELEASE, "not-json"))

    with pytest.raises(OpsError) as raised:
        activate_release(
            remote,
            store(remote),
            previous_release_id=CURRENT,
            candidate_release_id=CANDIDATE,
            backup=backup(),
            migration_policy="no-change",
            manifest=manifest(),
            artifact_sha256="b" * 64,
        )

    assert not hasattr(raised.value, "selected_release_id")
    assert raised.value.service_state == "unknown"
    assert raised.value.database_changed is True


def test_activate_release_rejects_a_success_payload_with_a_different_activation_identifier() -> None:
    """Accepting another transaction's success record must not finalize this activation."""

    remote = ActivationRemote(
        CommandResult(0, '{"activation_id":"activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","activated_at":"2026-09-05T12:00:01Z"}\n')
    )

    with pytest.raises(OpsError) as raised:
        activate_release(
            remote,
            store(remote),
            previous_release_id=CURRENT,
            candidate_release_id=CANDIDATE,
            backup=backup(),
            migration_policy="no-change",
            operation_token="d" * 32,
            manifest=manifest(),
            artifact_sha256="b" * 64,
        )

    assert raised.value.status is ExitStatus.RELEASE
    assert raised.value.activation_recorded == "unknown"


def test_activation_shell_uses_a_valid_blocking_stop_before_migration_and_start(
    tmp_path: Path,
) -> None:
    """Changing stop to an unsupported option must make the command stub reject the transaction."""

    managed = tmp_path / "managed"
    releases = managed / "releases"
    current = releases / CURRENT
    candidate = releases / CANDIDATE
    deployment = tmp_path / "deployments"
    for directory in (deployment, deployment / "manifests", deployment / "releases", deployment / "activations"):
        directory.mkdir(parents=True, exist_ok=True)
    for release in (current, candidate):
        (release / "bin").mkdir(parents=True)
        (release / "bin" / "server").write_text("#!/bin/sh\n", encoding="utf-8")
        (release / "bin" / "migrate").write_text(
            "#!/bin/sh\n"
            "if test -e \"$TASKMAN_MIGRATION_STATE\"; then\n"
            "  printf 'idempotent-migrate\\n' >> \"$TASKMAN_EVENTS\"\n"
            "else\n"
            "  : > \"$TASKMAN_MIGRATION_STATE\"\n"
            "  printf 'migrate\\n' >> \"$TASKMAN_EVENTS\"\n"
            "fi\n",
            encoding="utf-8",
        )
        (release / "bin" / "server").chmod(0o750)
        (release / "bin" / "migrate").chmod(0o750)
        (release / ".taskman-release.json").write_text("{}\n", encoding="utf-8")
    (managed / "current").symlink_to(current)
    command_root = tmp_path / "commands"
    command_root.mkdir()
    events = tmp_path / "events"

    _stub(command_root / "systemctl", """#!/bin/sh
set -eu
case "$1" in
  stop) test "$#" = 2 || exit 64; printf 'stop %s\\n' "$2" >> "$TASKMAN_EVENTS" ;;
  is-active) test "$#" = 3 || exit 64; test "$2" = --quiet || exit 64; printf 'inactive %s\\n' "$3" >> "$TASKMAN_EVENTS"; exit 3 ;;
  start) test "$#" = 2 || exit 64; "$TASKMAN_EXEC_START_PRE"; printf 'start %s\\n' "$2" >> "$TASKMAN_EVENTS" ;;
  *) exit 64 ;;
esac
""")
    _stub(command_root / "systemd-run", """#!/bin/sh
set -eu
last=
for value in "$@"; do last=$value; done
"$last"
""")
    _stub(command_root / "stat", """#!/bin/sh
case "$*" in
  *%u:%a*) printf '0:750\\n' ;;
  *%u*) printf '0\\n' ;;
  *%a*) printf '750\\n' ;;
  *) exit 64 ;;
esac
""")
    _stub(command_root / "find", "#!/bin/sh\nexit 0\n")
    _stub(command_root / "chown", "#!/bin/sh\nexit 0\n")

    completed = subprocess.run(
        (
            "sh",
            "-ceu",
            _ACTIVATION_BODY,
            "taskman-activation-test",
            str(managed),
            str(releases),
            str(deployment),
            CURRENT,
            CANDIDATE,
            "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "activation-dddddddddddddddddddddddddddddddd",
            "d" * 32,
            "b" * 64,
            "no-change",
            "-",
        ),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{command_root}:{os.environ['PATH']}",
            "TASKMAN_EVENTS": str(events),
            "TASKMAN_MIGRATION_STATE": str(tmp_path / "migration-state"),
            # The selected `current` path is what ExecStartPre resolves at
            # service start; this invocation must be idempotent after the
            # explicit migration completed above.
            "TASKMAN_EXEC_START_PRE": str(managed / "current" / "bin" / "migrate"),
        },
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "stop taskman.service",
        "inactive taskman.service",
        "migrate",
        "idempotent-migrate",
        "start taskman.service",
    ]
    assert (managed / "current").resolve() == candidate


def _stub(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
