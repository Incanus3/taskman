"""Contract tests for the guarded database-restore workflow."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess

import pytest

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.remote import CommandResult
from taskman_ops.releases.records import ActivationRecord, BackupRecord, LifecycleRecords, ReleaseRecord, RemoteLifecycleStore
from taskman_ops.releases.remote_snapshot import REMOTE_SNAPSHOT_TRANSACTION
from taskman_ops.verification import CheckStatus, VerificationCheck, VerificationReport
from taskman_ops.workflows.restore import (
    RESTORE_TRANSACTION,
    _recovery_commands,
    assess_restore,
    restore,
    run_locked_restore,
)


RELEASE_A = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"
RELEASE_B = "0.2.0-bbbbbbbbbbbb-ubuntu26.04-amd64-otp27.3.4.6"
BACKUP_A = "backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FIRST = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
SECOND = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _valid_operational_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_operational_preflight",
        lambda *_args: object(),
    )


def _config() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(
        {
            "name": "production",
            "ssh_host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "deployer",
            "host_key_fingerprint": "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "public_hostname": "taskman.acme.tld",
            "public_ipv4": "203.0.113.10",
            "target_os": "ubuntu26.04",
            "architecture": "amd64",
            "application_port": 4000,
            "distribution_port": 6789,
            "database_name": "taskman_prod",
            "database_role": "taskman",
            "mail_from": "no-reply@example.test",
        }
    )


def _records(*, backup_id: str = BACKUP_A, dump_path: PurePosixPath | None = None) -> LifecycleRecords:
    return LifecycleRecords(
        releases=(
            ReleaseRecord(1, RELEASE_A, "a" * 64, FIRST, FIRST, None, None, "no-change"),
            ReleaseRecord(1, RELEASE_B, "b" * 64, SECOND, SECOND, RELEASE_A, None, "no-change"),
        ),
        activations=(
            ActivationRecord(1, "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, RELEASE_A, FIRST, None, "no-change"),
            ActivationRecord(1, "activation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", RELEASE_A, RELEASE_B, SECOND, None, "no-change"),
        ),
        backups=(
            BackupRecord(
                1,
                backup_id,
                FIRST,
                2048,
                1_048_576,
                "taskman_prod",
                RELEASE_A,
                RELEASE_B,
                "pre-deploy",
                True,
                dump_path or PurePosixPath("/var/backups/taskman/backup-a.dump"),
            ),
        ),
        adoptions=(),
        warnings=(),
    )


def _manifest(release_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "application": "taskman",
        "application_version": "0.2.0",
        "source_revision": release_id.split("-", 2)[1][0] * 40,
        "release_id": release_id,
        "built_at": "2026-09-05T09:00:00Z",
        "target_os": "ubuntu26.04",
        "architecture": "amd64",
        "otp_version": "27.3.4.6",
        "elixir_version": "1.18.3",
        "node_version": "22.22.1",
        "hex_version": "2.5.1",
        "rebar3_version": "3.24.0",
        "migrations": [],
        "top_level": "taskman",
    }


class _SnapshotRemote:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((argv, kwargs))
        return CommandResult(0, json.dumps(self.snapshot))


def _store() -> tuple[_SnapshotRemote, RemoteLifecycleStore]:
    records = _records()
    remote = _SnapshotRemote(
        {
            "schema_version": 1,
            "records": {
                "releases": [record.to_mapping() for record in records.releases],
                "activations": [record.to_mapping() for record in records.activations],
                "backups": [record.to_mapping() for record in records.backups],
                "adoptions": [],
            },
            "current_target": f"/opt/taskman/releases/{RELEASE_B}",
            "manifests": {RELEASE_A: _manifest(RELEASE_A), RELEASE_B: _manifest(RELEASE_B)},
            "dump_states": {"/var/backups/taskman/backup-a.dump": "present"},
            "warnings": [],
        }
    )
    return remote, RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )


def test_assess_restore_accepts_only_the_exact_authoritative_backup_id() -> None:
    """Substituting a path or a matching prefix would restore an unreviewed dump."""

    records = _records()
    plan = assess_restore(records, RELEASE_B, BACKUP_A, backup_root=PurePosixPath("/var/backups/taskman"))

    assert plan.backup_id == BACKUP_A
    assert plan.dump_path == PurePosixPath("/var/backups/taskman/backup-a.dump")
    assert plan.intended_release_id == RELEASE_A
    assert plan.current_release_id == RELEASE_B
    for unsafe in ("backup-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "/var/backups/taskman/backup-a.dump", "*"):
        with pytest.raises(OpsError) as raised:
            assess_restore(records, RELEASE_B, unsafe, backup_root=PurePosixPath("/var/backups/taskman"))
        assert raised.value.status is ExitStatus.SAFETY


def test_assess_restore_refuses_stale_dump_or_an_uninstalled_intended_release() -> None:
    """A record is not restore authority after its dump or intended release disappears."""

    records = _records(dump_path=PurePosixPath("/tmp/backup-a.dump"))

    with pytest.raises(OpsError) as raised:
        assess_restore(records, RELEASE_B, BACKUP_A, backup_root=PurePosixPath("/var/backups/taskman"))

    assert raised.value.status is ExitStatus.SAFETY


def _write_executable(path: Path, contents: str) -> None:
    # Keep the fixture definitions compact while materialising real POSIX
    # scripts; command behavior, not a source-string assertion, is exercised.
    path.write_text(contents.encode("utf-8").decode("unicode_escape"), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _transaction_fixture(
    tmp_path: Path,
    *,
    expected_migration_filenames: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], dict[str, str], Path, Path, Path]:
    """Build a disposable command/filesystem harness for the real shell transaction."""

    commands = tmp_path / "commands"
    commands.mkdir()
    command_log = tmp_path / "commands.log"
    managed = tmp_path / "managed"
    releases = managed / "releases"
    deployment = tmp_path / "deployments"
    backups = tmp_path / "backups"
    locks = tmp_path / "locks"
    for directory in (managed, releases, deployment, backups, locks):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    for release_id in (RELEASE_A, RELEASE_B):
        release = releases / release_id
        (release / "bin").mkdir(parents=True)
        (release / "lib").mkdir()
        (release / "releases").mkdir()
        _write_executable(release / "bin" / "server", "#!/bin/sh\nexit 0\n")
        _write_executable(release / "bin" / "migrate", "#!/bin/sh\\nexit 0\\n")
    (managed / "current").symlink_to(releases / RELEASE_B)
    dump = backups / "backup-a.dump"
    dump.write_bytes(b"x" * 2048)

    _write_executable(commands / "pg_restore", "#!/bin/sh\\nprintf 'pg_restore %s\\\\n' \"$*\" >> \"$COMMAND_LOG\"\\ncase \"$*\" in *--list*) test \"${FAIL_LIST:-0}\" != 1; exit $?;; esac\\ntest \"${FAIL_RESTORE:-0}\" != 1\\n")
    _write_executable(commands / "psql", "#!/bin/sh\\nprintf 'psql %s\\\\n' \"$*\" >> \"$COMMAND_LOG\"\\ncase \"$*\" in *information_schema.tables*) printf '1\\\\n';; *'SHOW data_directory'*) printf '%s\\\\n' \"$PGDATA\";; *'pg_database_size'*) printf '%s\\\\n' \"${CURRENT_DATABASE_BYTES:-1048576}\";; *'CREATE DATABASE \"taskman_restore_'*) printf 'taskman_prod,taskman_restore_dddddddddddddddddddddddddddddddd' > \"$DATABASE_LAYOUT\";; *'DROP DATABASE \"taskman_restore_'*) printf taskman_prod > \"$DATABASE_LAYOUT\";; *'ALTER DATABASE \"taskman_prod\" RENAME TO \"taskman_recovery_'*) printf 'taskman_recovery_dddddddddddddddddddddddddddddddd,taskman_restore_dddddddddddddddddddddddddddddddd' > \"$DATABASE_LAYOUT\";; *'ALTER DATABASE \"taskman_restore_'*' RENAME TO \"taskman_prod\"'*) test \"${FAIL_SECOND_RENAME:-0}\" != 1 || exit 1; printf 'taskman_prod,taskman_recovery_dddddddddddddddddddddddddddddddd' > \"$DATABASE_LAYOUT\";; *'ALTER DATABASE \"taskman_recovery_'*) test \"${FAIL_INVERSE:-0}\" != 1 || exit 1; printf 'taskman_prod,taskman_restore_dddddddddddddddddddddddddddddddd' > \"$DATABASE_LAYOUT\";; *string_agg*) layout=$(cat \"$DATABASE_LAYOUT\"); if test \"${FAIL_AFTER_FIRST_LAYOUT:-0}\" = 1 && test \"$layout\" = 'taskman_recovery_dddddddddddddddddddddddddddddddd,taskman_restore_dddddddddddddddddddddddddddddddd'; then printf unexpected-layout; elif test \"${FAIL_AFTER_SECOND_LAYOUT:-0}\" = 1 && test \"$layout\" = 'taskman_prod,taskman_recovery_dddddddddddddddddddddddddddddddd'; then printf unexpected-layout; else printf '%s\\\\n' \"$layout\"; fi;; esac\\nexit 0\\n")
    backup_json = '{"schema_version":1,"backup_id":"backup-cccccccccccccccccccccccccccccccc","created_at":"2026-09-05T12:00:00Z","size_bytes":2048,"source_database_size_bytes":1048576,"database":"taskman_prod","current_release_id":"' + RELEASE_B + '","candidate_release_id":"' + RELEASE_A + '","reason":"pre-restore","validated":true,"dump_path":"' + str(backups / "pre-restore.dump") + '"}'
    _write_executable(commands / "taskman-backup", "#!/bin/sh\\nprintf 'backup %s\\\\n' \"$*\" >> \"$COMMAND_LOG\"\\nprintf '%s\\\\n' '" + backup_json + "'\\n")
    _write_executable(commands / "systemctl", "#!/bin/sh\\nprintf 'systemctl %s\\\\n' \"$*\" >> \"$COMMAND_LOG\"\\ncase \"$1\" in stop) test \"${FAIL_STOP:-0}\" != 1 || exit 1; printf stopped > \"$SERVICE_STATE\";; start) test \"${FAIL_START:-0}\" != 1 || exit 1; printf active > \"$SERVICE_STATE\";; is-active) test \"$(cat \"$SERVICE_STATE\" 2>/dev/null || printf active)\" = active || exit 3;; show) printf 'active\\\\n123\\\\n';; esac\\n")
    _write_executable(commands / "sudo", "#!/bin/sh\\nprintf 'sudo %s\\\\n' \"$*\" >> \"$COMMAND_LOG\"\\ntest \"$1\" = -u && test \"$2\" = postgres && test \"$3\" = -- || exit 97\\nshift 3\\nexec \"$@\"\\n")
    _write_executable(commands / "mv", "#!/bin/sh\\nprintf 'mv %s\\\\n' \"$*\" >> \"$COMMAND_LOG\"\\ntest \"${FAIL_SELECTION:-0}\" != 1 || exit 1\\nexec /bin/mv \"$@\"\\n")
    _write_executable(commands / "systemd-run", "#!/bin/sh\\nprintf 'systemd-run %s\\\\n' \"$*\" >> \"$COMMAND_LOG\"\\n")
    _write_executable(commands / "readlink", "#!/bin/sh\\ncase \"$*\" in *'/current') exec /usr/bin/readlink \"$@\";; esac\\nprintf '%s/bin/server\\\\n' \"$INTENDED_PATH\"\\n")
    _write_executable(commands / "ss", "#!/bin/sh\\nprintf 'LISTEN 0 4096 127.0.0.1:4000 0.0.0.0:*\\\\nLISTEN 0 4096 127.0.0.1:6789 0.0.0.0:*\\\\nLISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*\\\\n'\\n")
    _write_executable(commands / "journalctl", "#!/bin/sh\\nprintf 'started successfully\\\\n'\\n")
    _write_executable(commands / "curl", "#!/bin/sh\\ncase \"$*\" in *https://*) printf 'HTTP/1.1 200 OK\\\\r\\\\nCache-Control: no-store\\\\r\\\\nStrict-Transport-Security: max-age=31536000\\\\r\\\\n\\\\r\\\\nready';; *) printf 'HTTP/1.1 200 OK\\\\r\\\\nCache-Control: no-store\\\\r\\\\n\\\\r\\\\nready';; esac\\n")
    _write_executable(commands / "df", "#!/bin/sh\\nprintf 'df %s\\\\n' \"$*\" >> \"$COMMAND_LOG\"\\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\\\n/dev/fake 1 1 %s 1%% /\\\\n' \"${DF_AVAILABLE_BLOCKS:-999999999}\"\\n")
    (tmp_path / "service-state").write_text("active", encoding="utf-8")
    (tmp_path / "database-layout").write_text("taskman_prod", encoding="utf-8")
    pgdata = tmp_path / "postgres-data"
    pgdata.mkdir()

    transaction = RESTORE_TRANSACTION.replace("lock_root=/var/lock/taskman; operation=restore; timeout_ms=${21}; owner_uid=0;", f"lock_root={locks}; operation=restore; timeout_ms=${{21}}; owner_uid={os.getuid()};", 1).replace("chown root:root", f"chown {os.getuid()}:{os.getgid()}").replace("install -d -o root -g root", f"install -d -o {os.getuid()} -g {os.getgid()}")
    migration_versions = base64.b64encode(json.dumps(expected_migration_filenames).encode("utf-8")).decode("ascii")
    snapshot = subprocess.run(
        (
            "sh", "-ceu", REMOTE_SNAPSHOT_TRANSACTION.replace("owner_uid=$8", f"owner_uid={os.getuid()}", 1), "taskman-restore-snapshot",
            str(deployment), str(managed), str(releases), str(backups), str(locks), "restore", "5000", str(os.getuid()),
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert snapshot.returncode == 0, snapshot.stderr
    lifecycle_fingerprint = hashlib.sha256(
        json.dumps(json.loads(snapshot.stdout), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    argv = (
        "sh", "-ceu", transaction, "taskman-restore-transaction", str(managed), str(releases), str(deployment), str(backups), "4000", "taskman.acme.tld", BACKUP_A, str(dump), "2048", RELEASE_B, RELEASE_A, "d" * 32, str(commands / "taskman-backup"), "127.0.0.1", "5432", "taskman", "taskman_prod", "14", "6789", str(tmp_path / "Caddyfile"), "5000", "2", "2", str(releases / RELEASE_A), migration_versions, str(releases / RELEASE_B), lifecycle_fingerprint, "1048576",
    )
    environment = {"PATH": f"{commands}:{os.environ['PATH']}", "COMMAND_LOG": str(command_log), "SERVICE_STATE": str(tmp_path / "service-state"), "INTENDED_PATH": str(releases / RELEASE_A), "PGDATA": str(pgdata), "DATABASE_LAYOUT": str(tmp_path / "database-layout")}
    return argv, environment, managed, deployment, command_log


def test_restore_transaction_executes_the_validated_swap_and_records_recovery_database(tmp_path: Path) -> None:
    """The real transaction restores, validates, renames, selects, verifies, and records recovery evidence."""

    argv, environment, managed, deployment, command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **environment}, check=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "restored"
    assert evidence["recovery_id"] == "recovery-" + "d" * 32
    assert evidence["restore_recorded"] is True
    assert evidence["recovery_commands"][-3:] == [
        "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 "
        "--username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command "
        '\"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = \'taskman_prod\' '
        "AND pid <> pg_backend_pid()\"",
        "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 "
        "--username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command \"DROP DATABASE taskman_prod\"",
        "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 "
        "--username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command "
        "\"ALTER DATABASE taskman_recovery_dddddddddddddddddddddddddddddddd RENAME TO taskman_prod\"",
    ]
    assert (deployment / "restores" / ("recovery-" + "d" * 32 + ".json")).is_file()
    assert managed.joinpath("current").resolve() == managed / "releases" / RELEASE_A
    log = command_log.read_text(encoding="utf-8")
    assert "pg_restore --list" in log
    assert 'ALTER DATABASE "taskman_prod" RENAME TO "taskman_recovery_' in log
    assert 'ALTER DATABASE "taskman_restore_' in log


def test_restore_recovery_commands_use_distinct_postgresql_administration_calls(tmp_path: Path) -> None:
    """Recovery never sends DROP DATABASE in a multi-statement psql transaction."""

    commands = _recovery_commands(
        PurePosixPath("/opt/taskman"),
        5432,
        "taskman_prod",
        "recovery-" + "d" * 32,
        "restored-promoted",
        "restored-promoted",
    )
    layout = tmp_path / "database-layout"
    layout.write_text(
        "taskman_prod,taskman_recovery_dddddddddddddddddddddddddddddddd",
        encoding="utf-8",
    )
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    _write_executable(
        bin_directory / "sudo",
        "#!/bin/sh\ntest \"$1,$2,$3\" = '-u,postgres,--' || exit 97\nshift 3\nexec \"$@\"\n",
    )
    _write_executable(
        bin_directory / "psql",
        "#!/bin/sh\ncommand=\nwhile test \"$#\" -gt 0; do if test \"$1\" = --command; then command=$2; shift 2; else shift; fi; done\ncase \"$command\" in *'DROP DATABASE'*';'*) exit 91;; esac\ncase \"$command\" in *'SELECT pg_terminate_backend'*) : ;; 'DROP DATABASE taskman_prod') printf taskman_recovery_dddddddddddddddddddddddddddddddd > \"$DATABASE_LAYOUT\" ;; 'ALTER DATABASE taskman_recovery_dddddddddddddddddddddddddddddddd RENAME TO taskman_prod') test \"$(cat \"$DATABASE_LAYOUT\")\" = taskman_recovery_dddddddddddddddddddddddddddddddd || exit 92; printf taskman_prod > \"$DATABASE_LAYOUT\" ;; *) exit 93 ;; esac\n",
    )
    environment = {**os.environ, "PATH": f"{bin_directory}:{os.environ['PATH']}", "DATABASE_LAYOUT": str(layout)}

    terminated = subprocess.run(commands[3], shell=True, text=True, capture_output=True, env=environment, check=False)
    assert terminated.returncode == 0, terminated.stderr
    assert layout.read_text(encoding="utf-8") == "taskman_prod,taskman_recovery_dddddddddddddddddddddddddddddddd"
    dropped = subprocess.run(commands[4], shell=True, text=True, capture_output=True, env=environment, check=False)
    assert dropped.returncode == 0, dropped.stderr
    assert layout.read_text(encoding="utf-8") == "taskman_recovery_dddddddddddddddddddddddddddddddd"
    renamed = subprocess.run(commands[5], shell=True, text=True, capture_output=True, env=environment, check=False)
    assert renamed.returncode == 0, renamed.stderr
    assert layout.read_text(encoding="utf-8") == "taskman_prod"


def test_restore_recovery_commands_withhold_mutations_after_canonical_state_is_restored() -> None:
    """A successful inverse leaves no retained recovery database that can be renamed."""

    commands = _recovery_commands(
        PurePosixPath("/opt/taskman"),
        5432,
        "taskman_prod",
        "recovery-" + "d" * 32,
        "canonical-moved",
        "canonical-restored",
    )

    assert commands == [
        "systemctl status taskman.service",
        "readlink -f /opt/taskman/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
    ]


def test_restore_transaction_uses_postgres_peer_admin_for_cluster_mutation_and_data_capacity(tmp_path: Path) -> None:
    """A NOCREATEDB application role must never receive cluster-admin DDL."""

    argv, environment, _managed, _deployment, command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **environment}, check=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    log = command_log.read_text(encoding="utf-8")
    admin_prefix = "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres"
    assert f'{admin_prefix} --set ON_ERROR_STOP=1 --command CREATE DATABASE "taskman_restore_' in log
    assert f'{admin_prefix} --set ON_ERROR_STOP=1 --command ALTER DATABASE "taskman_prod"' in log
    assert f'{admin_prefix} --set ON_ERROR_STOP=1 --command SELECT pg_terminate_backend' in log
    assert "--username taskman --dbname postgres --set ON_ERROR_STOP=1 --command CREATE DATABASE" not in log
    assert "--username taskman --dbname postgres --set ON_ERROR_STOP=1 --command ALTER DATABASE" not in log
    assert "--username taskman --dbname postgres --set ON_ERROR_STOP=1 --command SELECT pg_terminate_backend" not in log
    assert f"{admin_prefix} --tuples-only --no-align --set ON_ERROR_STOP=1 --command SHOW data_directory" in log
    assert "pg_database_size('taskman_prod')" not in log
    assert "df -Pk -- " + environment["PGDATA"] in log


def test_restore_transaction_uses_recorded_source_allocation_not_the_live_database_size(tmp_path: Path) -> None:
    """Restore capacity derives from the backup's durable source allocation authority."""

    argv, environment, _managed, _deployment, command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(
        (*argv[:-1], str(2 * 1024 * 1024)),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            **environment,
            "CURRENT_DATABASE_BYTES": str(512 * 1024 * 1024),
            "DF_AVAILABLE_BLOCKS": "70000",
        },
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "pg_database_size('taskman_prod')" not in command_log.read_text(encoding="utf-8")


def test_restore_transaction_refuses_lifecycle_fingerprint_drift_before_database_actions(tmp_path: Path) -> None:
    """Confirmation expires when backup, lifecycle, or manifest authority changes before locking."""

    argv, environment, _managed, _deployment, command_log = _transaction_fixture(tmp_path)
    stale = (*argv[:-2], "0" * 64, argv[-1])
    completed = subprocess.run(stale, text=True, capture_output=True, env={**os.environ, **environment}, check=False)

    assert completed.returncode == ExitStatus.SAFETY
    assert json.loads(completed.stdout)["stage"] == "preflight"
    assert not command_log.exists() or "systemctl stop taskman.service" not in command_log.read_text(encoding="utf-8")


def test_restore_transaction_revalidates_dump_contents_under_lock_before_any_mutation(tmp_path: Path) -> None:
    """A dump replaced after confirmation cannot reach backup or service stop."""

    argv, environment, _managed, _deployment, command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        env={**os.environ, **environment, "FAIL_LIST": "1"},
        check=False,
    )

    assert completed.returncode == ExitStatus.RESTORE
    assert json.loads(completed.stdout)["stage"] == "preflight"
    log = command_log.read_text(encoding="utf-8")
    assert "pg_restore --list" in log
    assert "backup " not in log
    assert "systemctl stop taskman.service" not in log


def test_restore_transaction_reports_unknown_service_after_an_unconfirmed_stop_failure(tmp_path: Path) -> None:
    """A failed stop cannot be represented as an active, unchanged service."""

    argv, environment, _managed, _deployment, _command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **environment, "FAIL_STOP": "1"}, check=False)

    assert completed.returncode == ExitStatus.RESTORE
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "stop"
    assert evidence["service_state"] == "unknown"
    assert evidence["swap_state"] == "before-first-rename"


def test_restore_transaction_reports_inverse_failure_without_claiming_a_promoted_database(tmp_path: Path) -> None:
    """When the exact inverse fails, evidence retains uncertainty rather than inventing a restored state."""

    argv, environment, managed, deployment, _command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        env={**os.environ, **environment, "FAIL_SECOND_RENAME": "1", "FAIL_INVERSE": "1"},
        check=False,
    )

    assert completed.returncode == ExitStatus.RESTORE
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "swap"
    assert evidence["swap_state"] == "inverse-failed"
    assert evidence["database_state"] == "unknown"
    assert evidence["selected_release_id"] == RELEASE_B
    assert managed.joinpath("current").resolve() == managed / "releases" / RELEASE_B


@pytest.mark.parametrize("layout_boundary", ["canonical-moved", "restored-promoted"])
def test_restore_transaction_records_observed_layout_uncertainty_after_each_rename(
    tmp_path: Path, layout_boundary: str
) -> None:
    """Each post-rename layout mismatch retains its actual swap boundary and uncertainty."""

    argv, environment, _managed, _deployment, _command_log = _transaction_fixture(tmp_path)
    failure = "FAIL_AFTER_FIRST_LAYOUT" if layout_boundary == "canonical-moved" else "FAIL_AFTER_SECOND_LAYOUT"
    completed = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        env={**os.environ, **environment, failure: "1"},
        check=False,
    )

    assert completed.returncode == ExitStatus.RESTORE
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "swap"
    assert evidence["swap_state"] == layout_boundary
    assert evidence["database_state"] == "unknown"
    assert evidence["service_state"] == "stopped"
    assert evidence["selected_release_id"] == RELEASE_B
    assert evidence["restore_recorded"] is False
    assert evidence["recovery_commands"][-1] == (
        "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 "
        "--username postgres --dbname=postgres --tuples-only --no-align --set ON_ERROR_STOP=1 --command "
        "\"SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE datname IN "
        "('taskman_prod', 'taskman_restore_dddddddddddddddddddddddddddddddd', "
        "'taskman_recovery_dddddddddddddddddddddddddddddddd')\""
    )
    assert all(
        mutation not in command
        for command in evidence["recovery_commands"]
        for mutation in ("DROP DATABASE", "ALTER DATABASE", "pg_terminate_backend")
    )


def test_restore_transaction_records_a_selection_failure_only_after_observing_the_old_selection(tmp_path: Path) -> None:
    """A failed symlink replacement cannot claim that the intended release was selected."""

    argv, environment, managed, deployment, _command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **environment, "FAIL_SELECTION": "1"}, check=False)

    assert completed.returncode == ExitStatus.RESTORE
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "start"
    assert evidence["swap_state"] == "selection-failed"
    assert evidence["selected_release_id"] == RELEASE_B
    assert evidence["database_state"] == "restored-promoted"
    assert evidence["restore_recorded"] is True
    assert any("DROP DATABASE taskman_prod" in command for command in evidence["recovery_commands"])
    assert managed.joinpath("current").resolve() == managed / "releases" / RELEASE_B
    assert deployment.joinpath("restores", "recovery-" + "d" * 32 + ".json").is_file()


def test_restore_transaction_removes_only_its_temporary_database_and_restarts_before_swap_failure(tmp_path: Path) -> None:
    """A failure before canonical rename preserves the old selection and restarts it safely."""

    argv, environment, managed, deployment, command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **environment, "FAIL_RESTORE": "1"}, check=False)

    assert completed.returncode == ExitStatus.RESTORE
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "restore"
    assert evidence["database_state"] == "unchanged"
    assert evidence["service_state"] == "active"
    assert managed.joinpath("current").resolve() == managed / "releases" / RELEASE_B
    assert not (deployment / "restores").exists()
    log = command_log.read_text(encoding="utf-8")
    assert 'DROP DATABASE "taskman_restore_' in log
    assert "systemctl start taskman.service" in log


def test_restore_transaction_refuses_temporary_schema_migrations_that_do_not_match_the_intended_release(
    tmp_path: Path,
) -> None:
    """A restored dump missing an intended migration must never reach the name swap."""

    argv, environment, managed, deployment, command_log = _transaction_fixture(
        tmp_path,
        expected_migration_filenames=("20260905100000_create_tasks.exs",),
    )
    completed = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **environment}, check=False)

    assert completed.returncode == ExitStatus.RESTORE
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "validation"
    assert evidence["database_state"] == "unchanged"
    assert managed.joinpath("current").resolve() == managed / "releases" / RELEASE_B
    assert not (deployment / "restores").exists()
    log = command_log.read_text(encoding="utf-8")
    assert 'DROP DATABASE "taskman_restore_' in log


def test_restore_transaction_attempts_only_the_exact_inverse_after_a_partial_database_rename(
    tmp_path: Path,
) -> None:
    """A failed second rename returns only the retained old database to its canonical name."""

    argv, environment, managed, deployment, command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **environment, "FAIL_SECOND_RENAME": "1"}, check=False)

    assert completed.returncode == ExitStatus.RESTORE
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "swap"
    assert evidence["database_state"] == "canonical-restored"
    assert evidence["service_state"] == "active"
    assert managed.joinpath("current").resolve() == managed / "releases" / RELEASE_B
    assert not (deployment / "restores").exists()
    log = command_log.read_text(encoding="utf-8")
    rename_commands = [
        line
        for line in log.splitlines()
        if line.startswith("sudo -u postgres -- psql ") and "--command ALTER DATABASE" in line
    ]
    assert len(rename_commands) == 3
    assert 'ALTER DATABASE "taskman_recovery_' in log
    assert 'DROP DATABASE "taskman_restore_' in log
    assert all(
        mutation not in command
        for command in evidence["recovery_commands"]
        for mutation in ("DROP DATABASE", "ALTER DATABASE", "pg_terminate_backend")
    )
    layout = Path(environment["DATABASE_LAYOUT"])
    recovered_layout = layout.read_text(encoding="utf-8")
    for command in evidence["recovery_commands"]:
        recovery = subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            env={**os.environ, **environment},
            check=False,
        )
        assert recovery.returncode == 0, recovery.stderr
    assert layout.read_text(encoding="utf-8") == recovered_layout


def test_restore_transaction_keeps_both_databases_and_stops_after_a_post_swap_failure(tmp_path: Path) -> None:
    """Once canonical state changed, restore records no success and never restarts an uncertain service."""

    argv, environment, managed, deployment, command_log = _transaction_fixture(tmp_path)
    completed = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **environment, "FAIL_START": "1"}, check=False)

    assert completed.returncode == ExitStatus.RESTORE
    evidence = json.loads(completed.stdout)
    assert evidence["stage"] == "start"
    assert evidence["database_state"] == "restored-promoted"
    assert evidence["service_state"] == "stopped"
    assert evidence["restore_recorded"] is True
    assert managed.joinpath("current").resolve() == managed / "releases" / RELEASE_A
    assert (deployment / "restores" / ("recovery-" + "d" * 32 + ".json")).is_file()
    log = command_log.read_text(encoding="utf-8")
    assert log.count("systemctl stop taskman.service") == 2
    assert "systemctl start taskman.service" in log


def test_restore_requires_typed_environment_and_exact_backup_before_the_exclusive_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary confirmation cannot authorize a destructive database replacement."""

    remote, store = _store()
    confirmed: list[dict[str, object]] = []
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.run_locked_restore",
        lambda *_args, **_kwargs: pytest.fail("cancelled restore acquired the exclusive transaction"),
    )

    result = restore(
        remote,
        _config(),
        BACKUP_A,
        lifecycle_store=store,
        confirm=lambda plan: confirmed.append(dict(plan)) or False,
    )

    assert result.stage == "confirmation-cancelled"
    assert result.changed is False
    assert confirmed == [
        {
            "environment": "production",
            "backup_id": BACKUP_A,
            "current_release_id": RELEASE_B,
            "intended_release_id": RELEASE_A,
            "dump_path": "/var/backups/taskman/backup-a.dump",
            "typed_confirmation": "production " + BACKUP_A,
            "planned_pre_restore_backup": True,
            "services_affected": ("taskman.service",),
        }
    ]
    assert len(remote.calls) == 2
    dump_validation, validation_kwargs = remote.calls[1]
    assert "pg_restore --list" in dump_validation[2]
    assert dump_validation[-3:] == (
        "2048",
        "1048576",
        "5432",
    )
    assert dump_validation[-5:-3] == (
        "/var/backups/taskman/backup-a.dump",
        "/var/backups/taskman",
    )
    assert "SHOW data_directory" in dump_validation[2]
    assert validation_kwargs == {"sudo": True, "stdin": None, "sensitive": False}


@pytest.mark.parametrize("dry_run", [False, True])
def test_restore_freshly_rejects_corrupt_dump_before_confirmation_or_mutation(
    monkeypatch: pytest.MonkeyPatch,
    dry_run: bool,
) -> None:
    """Both planning and destructive runs validate exact dump contents immediately."""

    remote, store = _store()
    original_run = remote.run

    def fail_dump_validation(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        if "pg_restore --list" in argv[2]:
            remote.calls.append((argv, kwargs))
            return CommandResult(1, "")
        return original_run(argv, **kwargs)

    remote.run = fail_dump_validation  # type: ignore[method-assign]
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.run_locked_restore",
        lambda *_args, **_kwargs: pytest.fail("corrupt dump reached exclusive transaction"),
    )

    result = restore(
        remote,
        _config(),
        BACKUP_A,
        lifecycle_store=store,
        confirm=lambda _plan: pytest.fail("corrupt dump reached confirmation"),
        dry_run=dry_run,
    )

    assert result.stage == "preflight-failed"
    assert result.changed is False
    assert result.exit_status is ExitStatus.RESTORE
    assert len(remote.calls) == 2


def _verification(release_id: str, *, successful: bool = True) -> dict[str, object]:
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
    return VerificationReport(
        ExitStatus.OK if successful else ExitStatus.READINESS,
        release_id,
        release_id,
        tuple(
            VerificationCheck(name, CheckStatus.PASSED, f"{name} passed")
            for name in names
        ),
        None if successful else "inspect the fixed verification summaries and correct the reported host state before retrying",
    ).to_mapping()


def _success_payload() -> dict[str, object]:
    return {
        "stage": "restored",
        "backup_id": BACKUP_A,
        "pre_restore_backup_id": "backup-cccccccccccccccccccccccccccccccc",
        "current_release_id": RELEASE_B,
        "intended_release_id": RELEASE_A,
        "selected_release_id": RELEASE_A,
        "recovery_id": "recovery-dddddddddddddddddddddddddddddddd",
        "service_state": "active",
        "database_state": "restored-promoted",
        "swap_state": "restored-promoted",
        "restore_recorded": True,
        "changed": True,
        "changed_stages": ["backup", "stop", "restore", "validation", "swap", "records", "start", "verification"],
        "warnings": [],
        "recovery_commands": [
            "systemctl status taskman.service",
            "readlink -f /opt/taskman/current",
            "journalctl --no-pager --unit taskman.service --lines=100",
            "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'taskman_prod' AND pid <> pg_backend_pid()\"",
            "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command \"DROP DATABASE taskman_prod\"",
            "sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port 5432 --username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command \"ALTER DATABASE taskman_recovery_dddddddddddddddddddddddddddddddd RENAME TO taskman_prod\"",
        ],
        "residue_paths": [],
        "verification": _verification(RELEASE_A),
    }


def test_locked_restore_runs_one_fixed_exclusive_transaction_and_rejects_forged_success() -> None:
    """A caller must not compose restore steps or accept a result for another dump/release."""

    payload = _success_payload()

    class _TransactionRemote:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
            self.calls.append((argv, kwargs))
            return CommandResult(0, json.dumps(payload))

    remote = _TransactionRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    evidence = run_locked_restore(
        remote,
        _config(),
        store,
        backup_id=BACKUP_A,
        dump_path=PurePosixPath("/var/backups/taskman/backup-a.dump"),
        dump_size_bytes=2048,
        source_database_size_bytes=1_048_576,
        current_release_id=RELEASE_B,
        intended_release_id=RELEASE_A,
        state_fingerprint="a" * 64,
        lock_timeout_seconds=5,
        operation_token="d" * 32,
    )

    assert evidence == payload
    assert len(remote.calls) == 1
    argv, kwargs = remote.calls[0]
    assert argv[:2] == ("sh", "-ceu")
    assert "pg_restore --list" in argv[2]
    assert "ALTER DATABASE" in argv[2]
    assert "--already-locked" in argv[2]
    assert kwargs == {"sudo": True, "stdin": None, "sensitive": False}

    payload["backup_id"] = "backup-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    with pytest.raises(OpsError) as raised:
        run_locked_restore(
            remote,
            _config(),
            store,
            backup_id=BACKUP_A,
            dump_path=PurePosixPath("/var/backups/taskman/backup-a.dump"),
            dump_size_bytes=2048,
            source_database_size_bytes=1_048_576,
            current_release_id=RELEASE_B,
            intended_release_id=RELEASE_A,
            state_fingerprint="a" * 64,
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )
    assert raised.value.status is ExitStatus.SAFETY


def test_locked_restore_accepts_only_a_stopped_post_swap_failure_boundary() -> None:
    """Controller evidence must not turn an uncertain post-swap host into a runnable service."""

    payload = {
        **_success_payload(),
        "stage": "start",
        "service_state": "stopped",
        "database_state": "restored-promoted",
        "restore_recorded": True,
        "changed_stages": ["backup", "stop", "restore", "validation", "swap", "records", "start"],
        "verification": None,
    }

    class _FailureRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(ExitStatus.RESTORE, json.dumps(payload))

    remote = _FailureRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    with pytest.raises(OpsError) as raised:
        run_locked_restore(
            remote,
            _config(),
            store,
            backup_id=BACKUP_A,
            dump_path=PurePosixPath("/var/backups/taskman/backup-a.dump"),
            dump_size_bytes=2048,
            source_database_size_bytes=1_048_576,
            current_release_id=RELEASE_B,
            intended_release_id=RELEASE_A,
            state_fingerprint="a" * 64,
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )
    assert raised.value.status is ExitStatus.RESTORE

    payload["service_state"] = "active"
    with pytest.raises(OpsError) as raised:
        run_locked_restore(
            remote,
            _config(),
            store,
            backup_id=BACKUP_A,
            dump_path=PurePosixPath("/var/backups/taskman/backup-a.dump"),
            dump_size_bytes=2048,
            source_database_size_bytes=1_048_576,
            current_release_id=RELEASE_B,
            intended_release_id=RELEASE_A,
            state_fingerprint="a" * 64,
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )
    assert raised.value.status is ExitStatus.SAFETY


@pytest.mark.parametrize("swap_state", ["canonical-moved", "restored-promoted"])
def test_locked_restore_preserves_observed_layout_uncertainty_boundaries(swap_state: str) -> None:
    """Observed post-rename uncertainty is an actionable restore failure, not generic safety fallback."""

    payload = {
        **_success_payload(),
        "stage": "swap",
        "selected_release_id": RELEASE_B,
        "service_state": "stopped",
        "database_state": "unknown",
        "swap_state": swap_state,
        "restore_recorded": False,
        "changed_stages": ["backup", "stop", "restore", "validation", "swap"],
        "verification": None,
    }
    payload["recovery_commands"] = _recovery_commands(
        PurePosixPath("/opt/taskman"),
        5432,
        "taskman_prod",
        "recovery-" + "d" * 32,
        swap_state,
        "unknown",
    )

    class _UncertainLayoutRemote:
        def run(self, _argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
            return CommandResult(ExitStatus.RESTORE, json.dumps(payload))

    remote = _UncertainLayoutRemote()
    store = RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )

    with pytest.raises(OpsError) as raised:
        run_locked_restore(
            remote,
            _config(),
            store,
            backup_id=BACKUP_A,
            dump_path=PurePosixPath("/var/backups/taskman/backup-a.dump"),
            dump_size_bytes=2048,
            source_database_size_bytes=1_048_576,
            current_release_id=RELEASE_B,
            intended_release_id=RELEASE_A,
            state_fingerprint="a" * 64,
            lock_timeout_seconds=5,
            operation_token="d" * 32,
        )

    assert raised.value.status is ExitStatus.RESTORE
    assert raised.value.swap_state == swap_state
    assert raised.value.database_state == "unknown"
