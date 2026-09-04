from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
from queue import Queue
import select
import shutil
import subprocess
from threading import Thread

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.manifests import MigrationFingerprint
from taskman_ops.remote import CommandResult
from taskman_ops.releases.adoption import adopt_current_release, inspect_manual_current_release
from taskman_ops.releases.records import (
    ActivationRecord,
    AdoptionRecord,
    ManualAdoptionCandidate,
    ReleaseRecord,
    RemoteLifecycleStore,
)
import taskman_ops.releases.records as records_module


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


class AdoptionRemote:
    """A host-shaped Remote fake: evidence and publication stay remote."""

    def __init__(self, response: CommandResult) -> None:
        self.response = response
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((argv, kwargs))
        return self.response


class SubprocessRemote:
    """Execute the immutable remote shell transaction against a disposable host tree."""

    def __init__(self, *, command_path: Path, lock_root: Path) -> None:
        self.command_path = command_path
        self.lock_root = lock_root
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((argv, kwargs))
        translated = tuple(str(self.lock_root) if item == "/var/lock/taskman" else item for item in argv)
        completed = subprocess.run(
            ("unshare", "-Ur", "--", *translated),
            check=False,
            capture_output=True,
            env={**os.environ, "PATH": f"{self.command_path}:{os.environ['PATH']}"},
            input=kwargs["stdin"],
        )
        return CommandResult(completed.returncode, completed.stdout.decode("utf-8"))


def adopted_record(**overrides: object) -> AdoptionRecord:
    values: dict[str, object] = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "adopted_at": datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        "release_path": PurePosixPath(f"/opt/taskman/releases/{RELEASE_ID}"),
        "content_sha256": "a" * 64,
        "application_version": "0.2.0",
        "source_revision": "unknown",
        "artifact_sha256": "unknown",
        "migrations": (MigrationFingerprint("20260905000000_create_records.exs", "b" * 64),),
    }
    values.update(overrides)
    return AdoptionRecord(**values)  # type: ignore[arg-type]


def adoption_candidate(**overrides: object) -> ManualAdoptionCandidate:
    values: dict[str, object] = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "release_path": PurePosixPath(f"/opt/taskman/releases/{RELEASE_ID}"),
        "content_sha256": "a" * 64,
        "application_version": "0.2.0",
        "migrations": (MigrationFingerprint("20260905000000_create_records.exs", "b" * 64),),
    }
    values.update(overrides)
    return ManualAdoptionCandidate(**values)  # type: ignore[arg-type]


def test_manual_adoption_candidate_requires_an_exact_content_checksum() -> None:
    with pytest.raises(ValueError, match="content SHA-256"):
        adoption_candidate(content_sha256=None)


def remote_store(remote: AdoptionRemote) -> RemoteLifecycleStore:
    return RemoteLifecycleStore(
        remote,
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )


def subprocess_store(
    tmp_path: Path,
    *,
    fail_marker_link: bool,
    evidence_failure: str | None = None,
    tree_failure: str | None = None,
    selected_name: str = "manual-current",
    application_port: int = 4000,
    distribution_port: int = 6789,
    database_port: int = 5432,
) -> tuple[RemoteLifecycleStore, Path, Path]:
    """Create an OTP-shaped current release and an executable, disposable remote."""

    host_root = tmp_path / "host"
    deployment_root = host_root / "deployments"
    managed_root = host_root / "taskman"
    release_root = managed_root / "releases"
    candidate = release_root / selected_name
    server = candidate / "bin" / "server"
    app = candidate / "lib" / "taskman-0.2.0" / "ebin" / "taskman.app"
    migrations = candidate / "lib" / "taskman-0.2.0" / "priv" / "repo" / "migrations"
    server.parent.mkdir(parents=True)
    server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    server.chmod(0o700)
    app.parent.mkdir(parents=True)
    app.write_text('[{vsn,"0.2.0"}].\n', encoding="utf-8")
    if evidence_failure != "migration_root":
        migrations.mkdir(parents=True)
        (migrations / "20260905000000_create_records.exs").write_text("# migration\n", encoding="utf-8")
    (candidate / "releases").mkdir()
    if tree_failure == "symlink":
        (candidate / "unsafe-link").symlink_to(app)
    elif tree_failure == "fifo":
        os.mkfifo(candidate / "unsafe.pipe")
    elif tree_failure == "special-directory":
        (candidate / "releases").chmod(0o1755)
    elif tree_failure == "writable-file":
        app.chmod(0o666)
    elif tree_failure == "writable-directory":
        (candidate / "releases").chmod(0o777)
    selected = candidate
    managed_root.mkdir(exist_ok=True)
    (managed_root / "current").symlink_to(selected)

    caddy_config = host_root / "etc" / "caddy" / "Caddyfile"
    caddy_config.parent.mkdir(parents=True)
    caddy_proxy = (
        f"127.0.0.1:{application_port}"
        if evidence_failure != "caddy_proxy"
        else f"0.0.0.0:{application_port}"
    )
    caddy_config.write_text(f"taskman.example.com {{\n\treverse_proxy {caddy_proxy}\n}}\n", encoding="utf-8")

    command_path = tmp_path / "commands"
    command_path.mkdir()
    curl_result = "exit 1" if evidence_failure == "health" else "printf 'ready\\n'"
    curl_program = (
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"http://127.0.0.1:{application_port}/healthz"*) {curl_result} ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    (command_path / "curl").write_text(curl_program, encoding="utf-8")
    caddy_status = "1" if evidence_failure == "caddy_service" else "0"
    (command_path / "systemctl").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  "is-active --quiet taskman.service") exit 0 ;;\n'
        f'  "is-active --quiet caddy.service") exit {caddy_status} ;;\n'
        '  "show --property=MainPID --value taskman.service") printf "4242\\n" ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    application_listener = (
        f"0.0.0.0:{application_port}"
        if evidence_failure == "application_listener"
        else f"127.0.0.1:{application_port}"
    )
    distribution_listener = (
        f"0.0.0.0:{distribution_port}"
        if evidence_failure == "distribution_listener"
        else f"127.0.0.1:{distribution_port}"
    )
    database_listener = (
        f"0.0.0.0:{database_port}"
        if evidence_failure == "database_listener"
        else f"127.0.0.1:{database_port}"
    )
    epmd_listener = "LISTEN 0 4096 127.0.0.1:4369 0.0.0.0:*\\n" if evidence_failure == "epmd" else ""
    (command_path / "ss").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *":{application_port}"*) printf "LISTEN 0 4096 {application_listener} 0.0.0.0:*\\n" ;;\n'
        f'  *":{distribution_port}"*) printf "LISTEN 0 4096 {distribution_listener} 0.0.0.0:*\\n" ;;\n'
        f'  *":{database_port}"*) printf "LISTEN 0 4096 {database_listener} 0.0.0.0:*\\n" ;;\n'
        f'  *":4369"*) printf "{epmd_listener}" ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    process_executable = "/usr/bin/beam.smp" if evidence_failure == "main_pid" else f"{selected}/erts-14.2/bin/beam.smp"
    (command_path / "readlink").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"/proc/4242/exe"*) printf "%s\\n" "{process_executable}" ;;\n'
        f'  *) exec {shutil.which("readlink")} "$@" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    link_program = "#!/bin/sh\n"
    if fail_marker_link:
        link_program += 'case "$*" in *"/adoptions/adoption-"*) exit 1 ;; esac\n'
    link_program += f'exec {shutil.which("ln")} "$@"\n'
    (command_path / "ln").write_text(link_program, encoding="utf-8")
    for command in command_path.iterdir():
        command.chmod(0o700)

    remote = SubprocessRemote(command_path=command_path, lock_root=host_root / "lock")
    return (
        RemoteLifecycleStore(
            remote,  # type: ignore[arg-type]
            PurePosixPath(str(deployment_root)),
            PurePosixPath(str(managed_root)),
            PurePosixPath(str(release_root)),
            PurePosixPath(str(host_root / "backups")),
            application_port=application_port,
            distribution_port=distribution_port,
            database_port=database_port,
            caddy_config=PurePosixPath(str(caddy_config)),
        ),
        deployment_root,
        caddy_config,
    )


def write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o750)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    path.chmod(0o600)


def seed_direct_release_history(store: RemoteLifecycleStore, release_id: str) -> None:
    activated_at = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    release = ReleaseRecord(
        schema_version=1,
        release_id=release_id,
        artifact_sha256="c" * 64,
        installed_at=activated_at,
        activated_at=activated_at,
        previous_release_id=None,
        backup_id=None,
        migration_policy="no-change",
    )
    activation = ActivationRecord(
        schema_version=1,
        activation_id="activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        previous_release_id=None,
        candidate_release_id=release_id,
        activated_at=activated_at,
        backup_id=None,
        migration_policy="no-change",
    )
    write_private_json(
        Path(store.deployment_root.as_posix()) / "releases" / f"release-{release_id}.json",
        release.to_mapping(),
    )
    write_private_json(
        Path(store.deployment_root.as_posix()) / "activations" / f"{activation.activation_id}.json",
        activation.to_mapping(),
    )
    write_private_json(
        Path(store.deployment_root.as_posix()) / "manifests" / f"release-{release_id}.json",
        {
            "schema_version": 1,
            "application": "taskman",
            "application_version": "0.2.0",
            "source_revision": "a" * 40,
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
        },
    )


def raw_subprocess_snapshot(store: RemoteLifecycleStore) -> dict[str, object]:
    assert isinstance(store.remote, SubprocessRemote)
    result = store.remote.run(
        (
            "sh",
            "-ceu",
            records_module._REMOTE_SNAPSHOT_TRANSACTION,
            "taskman-lifecycle-snapshot",
            store.deployment_root.as_posix(),
            store.managed_root.as_posix(),
            store.release_root.as_posix(),
            store.backup_root.as_posix(),
            "/var/lock/taskman",
            "releases",
            "0",
            "0",
        ),
        sudo=True,
        stdin=None,
        sensitive=False,
    )
    assert result.succeeded
    return json.loads(result.stdout)


def test_adoption_requests_a_remote_exclusive_transaction_without_caller_supplied_host_evidence() -> None:
    adoption = adopted_record()
    remote = AdoptionRemote(CommandResult(0, json.dumps({"schema_version": 1, "adoption": adoption.to_mapping()})))

    actual = adopt_current_release(remote_store(remote), confirmed=True)

    assert actual == adoption
    argv, kwargs = remote.calls[0]
    assert argv[:2] == ("sh", "-ceu")
    assert "python3" not in argv
    assert kwargs["stdin"] is None
    assert kwargs["sudo"] is True


def test_manual_current_inspection_returns_exact_remote_authority_without_adopting() -> None:
    candidate = adoption_candidate()
    remote = AdoptionRemote(
        CommandResult(
            0,
            json.dumps({"schema_version": 1, "candidate": candidate.to_mapping()}),
        )
    )

    actual = inspect_manual_current_release(remote_store(remote), lock_timeout_seconds=3)

    assert actual == candidate
    assert len(remote.calls) == 1
    argv, kwargs = remote.calls[0]
    assert argv[:2] == ("sh", "-ceu")
    assert argv[-8:-4] == ("4000", "6789", "5432", "/etc/caddy/Caddyfile")
    assert argv[-4:] == ("inspect", "-", "-", "-")
    assert "taskman-lifecycle-adoption-inspect" in argv
    assert kwargs == {"sudo": True, "stdin": None, "sensitive": False}


def test_real_manual_inspection_is_nonmutating_and_binds_the_following_adoption(
    tmp_path: Path,
) -> None:
    store, deployment_root, _caddy_config = subprocess_store(
        tmp_path,
        fail_marker_link=False,
    )

    candidate = inspect_manual_current_release(store, lock_timeout_seconds=0)

    assert candidate.release_path.name == "manual-current"
    assert candidate.migrations == (
        MigrationFingerprint(
            "20260905000000_create_records.exs",
            hashlib.sha256(b"# migration\n").hexdigest(),
        ),
    )
    assert not list(deployment_root.rglob("*.json"))

    adopted = adopt_current_release(
        store,
        confirmed=True,
        expected=candidate,
        lock_timeout_seconds=0,
    )

    assert adopted.release_id == candidate.release_id
    assert adopted.release_path == candidate.release_path
    assert adopted.content_sha256 == candidate.content_sha256
    assert adopted.application_version == candidate.application_version
    assert adopted.migrations == candidate.migrations


def test_changed_manual_release_is_refused_under_lock_before_adoption_publication(
    tmp_path: Path,
) -> None:
    store, deployment_root, _caddy_config = subprocess_store(
        tmp_path,
        fail_marker_link=False,
    )
    candidate = inspect_manual_current_release(store, lock_timeout_seconds=0)
    migration = (
        Path(candidate.release_path)
        / "lib"
        / "taskman-0.2.0"
        / "priv"
        / "repo"
        / "migrations"
        / "20260905000000_create_records.exs"
    )
    migration.write_text("# changed after confirmation\n", encoding="utf-8")

    with pytest.raises(OpsError) as raised:
        adopt_current_release(
            store,
            confirmed=True,
            expected=candidate,
            lock_timeout_seconds=0,
        )

    assert raised.value.status is ExitStatus.SAFETY
    assert not list(deployment_root.rglob("*.json"))
    assert not (deployment_root / "adoptions").exists()
    assert not (deployment_root / "adoption-transactions").exists()


def test_adoption_refuses_remote_health_or_topology_evidence_before_publication() -> None:
    remote = AdoptionRemote(CommandResult(10, json.dumps({"schema_version": 1, "error": "safety", "message": "runtime health is not ready"})))

    with pytest.raises(OpsError) as raised:
        adopt_current_release(remote_store(remote), confirmed=True)

    assert raised.value.status is ExitStatus.SAFETY
    assert remote.calls[0][0][:2] == ("sh", "-ceu")


def test_adoption_refuses_a_remote_record_whose_selected_path_conflicts_with_metadata() -> None:
    adoption = adopted_record(release_path=PurePosixPath("/opt/taskman/other"))
    remote = AdoptionRemote(CommandResult(0, json.dumps({"schema_version": 1, "adoption": adoption.to_mapping()})))

    with pytest.raises(OpsError) as raised:
        adopt_current_release(remote_store(remote), confirmed=True)

    assert raised.value.status is ExitStatus.SAFETY


def test_adoption_failure_leaves_no_locally_published_baseline_for_a_retry() -> None:
    remote = AdoptionRemote(CommandResult(10, json.dumps({"schema_version": 1, "error": "safety", "message": "publication failed"})))
    store = remote_store(remote)

    with pytest.raises(OpsError):
        adopt_current_release(store, confirmed=True)

    # The controller owns no lifecycle path and cannot publish a partial
    # release/activation/adoption trio after a remote transaction failure.
    assert len(remote.calls) == 1


def test_real_adoption_transaction_hides_and_removes_all_records_when_marker_publication_fails(tmp_path: Path) -> None:
    """A failed final marker must not make staged release records authoritative."""

    store, deployment_root, _caddy_config = subprocess_store(tmp_path, fail_marker_link=True)

    with pytest.raises(OpsError) as raised:
        adopt_current_release(store, confirmed=True, lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY
    snapshot = raw_subprocess_snapshot(store)
    assert snapshot["records"] == {"releases": [], "activations": [], "backups": [], "adoptions": []}
    assert not list(deployment_root.rglob("*.json"))


def test_real_adoption_transaction_no_clobber_conflict_preserves_the_complete_baseline(tmp_path: Path) -> None:
    store, deployment_root, _caddy_config = subprocess_store(tmp_path, fail_marker_link=False)

    adopted = adopt_current_release(store, confirmed=True, lock_timeout_seconds=0)
    before, _snapshot = store.read(operation="releases", lock_timeout_seconds=0)

    with pytest.raises(OpsError) as raised:
        adopt_current_release(store, confirmed=True, lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY
    after, _snapshot = store.read(operation="releases", lock_timeout_seconds=0)
    assert after == before
    assert len(after.releases) == len(after.activations) == len(after.adoptions) == 1
    assert not list((deployment_root / "releases").glob("*.json"))
    assert not list((deployment_root / "activations").glob("*.json"))
    assert (deployment_root / "adoptions" / f"adoption-{adopted.release_id}.json").is_file()


def test_real_adoption_refuses_existing_valid_activation_history_without_publishing_a_second_genesis(
    tmp_path: Path,
) -> None:
    store, deployment_root, _caddy_config = subprocess_store(
        tmp_path,
        fail_marker_link=False,
        selected_name=RELEASE_ID,
    )
    seed_direct_release_history(store, RELEASE_ID)
    before_records, before_snapshot = store.read(operation="releases", lock_timeout_seconds=0)

    with pytest.raises(OpsError) as raised:
        adopt_current_release(store, confirmed=True, lock_timeout_seconds=0)

    after_records, after_snapshot = store.read(operation="releases", lock_timeout_seconds=0)
    assert raised.value.status is ExitStatus.SAFETY
    assert after_records == before_records
    assert after_snapshot == before_snapshot
    assert not list((deployment_root / "adoptions").glob("adoption-*.json"))
    assert not list((deployment_root / "adoption-transactions").glob("adoption-*"))


def test_real_adoption_refuses_a_derived_release_record_collision_before_publication(
    tmp_path: Path,
) -> None:
    reference_store, _deployment_root, _caddy_config = subprocess_store(
        tmp_path / "reference",
        fail_marker_link=False,
    )
    derived = adopt_current_release(reference_store, confirmed=True, lock_timeout_seconds=0)
    store, deployment_root, _caddy_config = subprocess_store(
        tmp_path / "collision",
        fail_marker_link=False,
    )
    collision = ReleaseRecord(
        schema_version=1,
        release_id=derived.release_id,
        artifact_sha256="c" * 64,
        installed_at=datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
        activated_at=None,
        previous_release_id=None,
        backup_id=None,
        migration_policy="no-change",
    )
    collision_path = deployment_root / "releases" / f"release-{derived.release_id}.json"
    write_private_json(collision_path, collision.to_mapping())
    collision_before = collision_path.read_bytes()

    with pytest.raises(OpsError) as raised:
        adopt_current_release(store, confirmed=True, lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY
    assert collision_path.read_bytes() == collision_before
    assert not list((deployment_root / "adoptions").glob("adoption-*.json"))
    assert not list((deployment_root / "adoption-transactions").glob("adoption-*"))


def test_real_exclusive_adoption_reports_its_live_holder_to_a_contending_snapshot_without_sleeps(tmp_path: Path) -> None:
    """The actual transaction writes metadata before its first blocking evidence command."""

    store, _deployment_root, _caddy_config = subprocess_store(tmp_path, fail_marker_link=False)
    assert isinstance(store.remote, SubprocessRemote)
    ready = tmp_path / "adoption-ready"
    release = tmp_path / "adoption-release"
    os.mkfifo(ready)
    os.mkfifo(release)
    ready_descriptor = os.open(ready, os.O_RDWR | os.O_NONBLOCK)
    release_descriptor = os.open(release, os.O_RDWR | os.O_NONBLOCK)
    readlink = store.remote.command_path / "readlink"
    selected = (Path(store.managed_root.as_posix()) / "current").resolve()
    readlink.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"/proc/4242/exe"*) printf "%s\\n" "' + str(selected / "erts-14.2/bin/beam.smp") + '" ;;\n'
        '  *) printf "ready\\n" > "' + str(ready) + '"; read _ < "' + str(release) + '"; exec ' + str(shutil.which("readlink")) + ' "$@" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    readlink.chmod(0o700)
    outcome: Queue[object] = Queue()

    def run_adoption() -> None:
        try:
            outcome.put(store.adopt_current_release(confirmed=True, lock_timeout_seconds=0))
        except BaseException as error:  # pragma: no cover - surfaced below
            outcome.put(error)

    thread = Thread(target=run_adoption)
    thread.start()
    try:
        readable, _writable, _exceptional = select.select([ready_descriptor], [], [], 5)
        assert readable
        assert os.read(ready_descriptor, 64) == b"ready\n"

        with pytest.raises(OpsError) as raised:
            store.read(operation="releases", lock_timeout_seconds=0)

        assert raised.value.status is ExitStatus.LOCKED
        assert "adopt" in raised.value.message
        assert "exclusive" in raised.value.message
    finally:
        os.write(release_descriptor, b"continue\n")
        os.close(ready_descriptor)
        os.close(release_descriptor)

    result = outcome.get(timeout=5)
    thread.join(timeout=5)
    if isinstance(result, BaseException):
        raise result
    assert isinstance(result, AdoptionRecord)


def test_real_adoption_collects_otp_migrations_and_proves_the_configured_runtime_topology(tmp_path: Path) -> None:
    store, _deployment_root, caddy_config = subprocess_store(
        tmp_path,
        fail_marker_link=False,
        application_port=4010,
        distribution_port=6790,
        database_port=5433,
    )

    adopted = adopt_current_release(store, confirmed=True, lock_timeout_seconds=0)
    records, snapshot = store.read(operation="releases", lock_timeout_seconds=0)

    assert Path(adopted.release_path).name == "manual-current"
    assert adopted.release_id != Path(adopted.release_path).name
    assert records.current_release_id == adopted.release_id
    assert snapshot["current_target"] == adopted.release_path.as_posix()
    assert adopted.migrations == (
        MigrationFingerprint(
            "20260905000000_create_records.exs",
            hashlib.sha256(b"# migration\n").hexdigest(),
        ),
    )
    assert adopted.source_revision == adopted.artifact_sha256 == "unknown"
    assert not (Path(adopted.release_path) / "releases" / "taskman.lifecycle.json").exists()
    assert isinstance(store.remote, SubprocessRemote)
    argv, kwargs = store.remote.calls[0]
    assert argv[-8:-4] == ("4010", "6790", "5433", str(caddy_config))
    assert argv[-4:] == ("adopt", "-", "-", "-")
    assert kwargs["stdin"] is None
    assert not list((_deployment_root / "releases").glob("*.json"))


@pytest.mark.parametrize(
    "evidence_failure",
    (
        "health",
        "application_listener",
        "distribution_listener",
        "database_listener",
        "epmd",
        "caddy_proxy",
        "caddy_service",
        "main_pid",
        "migration_root",
    ),
)
def test_real_adoption_refuses_invalid_otp_runtime_evidence_before_publication(
    tmp_path: Path,
    evidence_failure: str,
) -> None:
    store, deployment_root, _caddy_config = subprocess_store(
        tmp_path,
        fail_marker_link=False,
        evidence_failure=evidence_failure,
    )

    with pytest.raises(OpsError) as raised:
        adopt_current_release(store, confirmed=True, lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY
    assert not list(deployment_root.rglob("*.json"))


@pytest.mark.parametrize(
    "tree_failure",
    ("symlink", "fifo", "special-directory", "writable-file", "writable-directory"),
)
def test_real_adoption_refuses_an_unsafe_release_tree_before_any_publication(
    tmp_path: Path,
    tree_failure: str,
) -> None:
    store, deployment_root, _caddy_config = subprocess_store(
        tmp_path,
        fail_marker_link=False,
        tree_failure=tree_failure,
    )

    with pytest.raises(OpsError) as raised:
        adopt_current_release(store, confirmed=True, lock_timeout_seconds=0)

    assert raised.value.status is ExitStatus.SAFETY
    assert not list(deployment_root.rglob("*.json"))
