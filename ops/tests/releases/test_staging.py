"""Secure, immutable release staging contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.manifests import ArtifactManifest, VerifiedArtifact
from taskman_ops.remote import CommandResult
from taskman_ops.releases.records import RemoteLifecycleStore
from taskman_ops.releases.staging import _STAGE_BODY, stage_release


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


class StagingRemote:
    """Records the public Remote boundary without emulating an SSH host."""

    def __init__(self, responses: list[CommandResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.uploads: list[tuple[Path, PurePosixPath, dict[str, object]]] = []

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        self.calls.append((tuple(argv), kwargs))
        return self.responses.pop(0)

    def put(self, source: Path, destination: PurePosixPath, **kwargs: object) -> None:
        self.uploads.append((source, destination, kwargs))


def artifact(tmp_path: Path) -> VerifiedArtifact:
    archive = tmp_path / "taskman.tar.gz"
    manifest_path = tmp_path / "manifest.json"
    checksum = tmp_path / "taskman.tar.gz.sha256"
    archive.write_bytes(b"release bytes")
    manifest = ArtifactManifest(
        schema_version=1,
        application="taskman",
        application_version="0.2.0",
        source_revision="a" * 40,
        release_id=RELEASE_ID,
        built_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        target_os="ubuntu26.04",
        architecture="amd64",
        otp_version="27.3.4.6",
        elixir_version="1.18.3",
        node_version="22.22.1",
        migrations=(),
        top_level="taskman",
    )
    return VerifiedArtifact(archive, manifest_path, checksum, "b" * 64, manifest)


def store(remote: StagingRemote) -> RemoteLifecycleStore:
    return RemoteLifecycleStore(
        remote,  # type: ignore[arg-type]
        PurePosixPath("/opt/taskman/deployments"),
        PurePosixPath("/opt/taskman"),
        PurePosixPath("/opt/taskman/releases"),
        PurePosixPath("/var/backups/taskman"),
    )


def test_stage_release_uploads_a_mode_0600_private_archive_and_publishes_only_the_verified_final_directory(
    tmp_path: Path,
) -> None:
    """Removing private upload protection or replacing the final tree must fail this."""

    remote = StagingRemote([CommandResult(0), CommandResult(0, '{"outcome":"staged"}\n')])

    staged = stage_release(remote, artifact(tmp_path), store(remote), operation_token="a" * 32)

    assert staged.release_id == RELEASE_ID
    assert staged.release_path == PurePosixPath(f"/opt/taskman/releases/{RELEASE_ID}")
    assert staged.resumed is False
    source, destination, upload_kwargs = remote.uploads[0]
    assert source.name == "taskman.tar.gz"
    assert destination == PurePosixPath(f"/opt/taskman/deployments/uploads/.upload-{RELEASE_ID}-{'a' * 32}.tar.gz")
    assert upload_kwargs == {"mode": 0o600, "sensitive": True}
    prepare_argv, prepare_kwargs = remote.calls[0]
    stage_argv, stage_kwargs = remote.calls[1]
    assert prepare_argv[:3] == ("sh", "-ceu", prepare_argv[2])
    assert stage_argv[:3] == ("sh", "-ceu", stage_argv[2])
    assert prepare_kwargs == {"sudo": True, "stdin": None, "sensitive": False}
    assert stage_kwargs["sudo"] is True
    assert stage_kwargs["sensitive"] is False


@pytest.mark.parametrize(
    ("returncode", "status"),
    [(ExitStatus.SAFETY, ExitStatus.SAFETY), (ExitStatus.RELEASE, ExitStatus.RELEASE)],
)
def test_stage_release_refuses_ambiguous_or_partial_existing_content_without_mutating_a_final_tree(
    tmp_path: Path, returncode: ExitStatus, status: ExitStatus
) -> None:
    """Treating a same-ID mismatch or partial directory as resumable must fail this."""

    remote = StagingRemote([CommandResult(0), CommandResult(returncode)])

    with pytest.raises(OpsError) as raised:
        stage_release(remote, artifact(tmp_path), store(remote), operation_token="b" * 32)

    assert raised.value.status is status
    assert raised.value.changed is False


def test_stage_release_reports_a_matching_complete_release_as_a_safe_resume(tmp_path: Path) -> None:
    """A retry that extracts over a verified final release must fail this."""

    remote = StagingRemote([CommandResult(0), CommandResult(0, '{"outcome":"resumed"}\n')])

    staged = stage_release(remote, artifact(tmp_path), store(remote), operation_token="c" * 32)

    assert staged.resumed is True


@pytest.mark.parametrize(
    ("checksum_matches", "unsafe_link"),
    [(True, False), (False, False), (True, True)],
)
def test_staging_shell_publishes_an_archive_without_symlinks_and_removes_its_private_upload(
    tmp_path: Path, checksum_matches: bool, unsafe_link: bool,
) -> None:
    """Both successful publication and checksum refusal must remove this private upload."""

    deployment = tmp_path / "deployments"
    releases = tmp_path / "releases"
    uploads = deployment / "uploads"
    uploads.mkdir(parents=True)
    releases.mkdir()
    token = "a" * 32
    upload = uploads / f".upload-{RELEASE_ID}-{token}.tar.gz"
    _archive(upload, unsafe_link=unsafe_link)
    upload.chmod(0o600)
    digest = sha256(upload.read_bytes()).hexdigest() if checksum_matches else "0" * 64
    commands = tmp_path / "commands"
    commands.mkdir()
    _stub(commands / "stat", """#!/bin/sh
case "$*" in
  *%u:%a*) printf '0:600\\n' ;;
  *%u*) printf '0\\n' ;;
  *%a*) printf '750\\n' ;;
  *) exit 64 ;;
esac
""")
    _stub(commands / "find", """#!/bin/sh
case "$*" in
  *'! -user root -print -quit'*|*'! -group taskman -print -quit'*) exit 0 ;;
  *) /usr/bin/find "$@" ;;
esac
""")
    _stub(commands / "chown", "#!/bin/sh\nexit 0\n")
    _stub(commands / "getent", "#!/bin/sh\nexit 0\n")

    completed = subprocess.run(
        (
            "sh",
            "-ceu",
            "release_lifecycle_lock() { :; }\n" + _STAGE_BODY,
            "taskman-staging-test",
            str(deployment),
            str(releases),
            str(uploads),
            RELEASE_ID,
            digest,
            token,
            str(upload),
        ),
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
        check=False,
    )

    accepted = checksum_matches and not unsafe_link
    assert completed.returncode == (0 if accepted else ExitStatus.SAFETY), completed.stderr
    if accepted:
        assert completed.stdout == '{"outcome":"staged"}\n'
        assert (releases / RELEASE_ID / "bin" / "server").is_file()
        assert (releases / RELEASE_ID / "bin" / "server").stat().st_mode & 0o777 == 0o750
        assert (releases / RELEASE_ID / "lib" / "release").stat().st_mode & 0o777 == 0o640
    else:
        assert not (releases / RELEASE_ID).exists()
    assert not upload.exists()


def _archive(path: Path, *, unsafe_link: bool = False) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, mode in (("taskman/bin/server", 0o750), ("taskman/lib/release", 0o640), ("taskman/releases/start", 0o640)):
            entry = tarfile.TarInfo(name)
            entry.mode = mode
            entry.size = 0
            archive.addfile(entry)
        if unsafe_link:
            link = tarfile.TarInfo("taskman/lib/escaped-link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            archive.addfile(link)


def _stub(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
