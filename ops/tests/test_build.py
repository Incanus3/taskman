from __future__ import annotations

from collections.abc import Sequence
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from taskman_ops.build import CommandResult, SourceState, build_release, read_application_version
from taskman_ops.cli import Invocation, dispatch
from taskman_ops.errors import ExitStatus, OpsError


REVISION = "c" * 40
RELEASE_ID = "0.2.0-cccccccccccc-ubuntu26.04-amd64-otp27.3.4.6"


def write_release_tree(destination: Path, *, source_revision: str = REVISION) -> None:
    for directory in ("bin", "lib", "releases", "erts-16.0"):
        (destination / "taskman" / directory).mkdir(parents=True, exist_ok=True)
    for launcher in ("taskman", "server", "migrate", "create-admin"):
        path = destination / "taskman" / "bin" / launcher
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
    (destination / "toolchain.json").write_text(
        json.dumps(
            {
                "source_revision": source_revision,
                "target_os": "ubuntu26.04",
                "architecture": "amd64",
                "otp_version": "27.3.4.6",
                "elixir_version": "1.18.3",
                "node_version": "22.22.1",
            }
        ),
        encoding="utf-8",
    )


def write_repository(repo: Path) -> None:
    (repo / "priv" / "repo" / "migrations").mkdir(parents=True)
    (repo / "priv" / "repo" / "migrations" / "20260904065131_example.exs").write_text(
        "defmodule Example do end\n", encoding="utf-8"
    )
    (repo / "mix.exs").write_text(
        'def project do\n  [\n    app: :taskman,\n    version: "0.2.0"\n  ]\nend\n', encoding="utf-8"
    )


def export_source(_repo: Path, _revision: str, destination: Path) -> None:
    destination.mkdir(parents=True)
    (destination / "ops" / "builder").mkdir(parents=True)
    (destination / "ops" / "builder" / "Containerfile").write_text("FROM scratch\n", encoding="utf-8")


def test_build_refuses_dirty_or_unidentified_source_before_creating_an_artifact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repository(repo)
    output = tmp_path / "artifacts"

    for state in (SourceState(revision=REVISION, clean=False), SourceState(revision=None, clean=True)):
        with pytest.raises(OpsError) as raised:
            build_release(repo, output, source_reader=lambda _repo, state=state: state)

        assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE
        assert not output.exists()


def test_build_passes_the_exact_clean_revision_to_an_amd64_buildkit_invocation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repository(repo)
    commands: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str], _cwd: Path) -> CommandResult:
        commands.append(tuple(argv))
        destination = Path(next(value.split("=", 2)[2] for value in argv if value.startswith("type=local,dest=")))
        write_release_tree(destination)
        return CommandResult(returncode=0, stdout="", stderr="")

    artifact = build_release(
        repo,
        tmp_path / "artifacts",
        source_reader=lambda _repo: SourceState(revision=REVISION, clean=True),
        command_runner=runner,
        source_exporter=export_source,
    )

    assert len(commands) == 1
    command = commands[0]
    assert command[:5] == ("docker", "buildx", "build", "--platform", "linux/amd64")
    assert ("--build-arg", f"SOURCE_REVISION={REVISION}") == (command[5], command[6])
    assert "--output" in command
    assert artifact.manifest.release_id == RELEASE_ID
    assert artifact.archive.name == f"taskman-{RELEASE_ID}.tar.gz"
    assert stat.S_IMODE(artifact.archive.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact.manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact.checksum.stat().st_mode) == 0o600


def test_failed_build_retains_one_private_artifact_directory_for_an_exact_retry(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repository(repo)
    output = tmp_path / "artifacts"

    def failed_runner(_argv: Sequence[str], _cwd: Path) -> CommandResult:
        return CommandResult(returncode=17, stdout="", stderr="tool failed")

    with pytest.raises(OpsError) as raised:
        build_release(
            repo,
            output,
            source_reader=lambda _repo: SourceState(revision=REVISION, clean=True),
            command_runner=failed_runner,
            source_exporter=export_source,
        )

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE
    assert raised.value.next_action is not None
    retained = Path(raised.value.next_action.removeprefix("inspect retained artifact directory: "))
    assert retained.parent == output
    assert retained.name.startswith(f"{RELEASE_ID}-")
    assert stat.S_IMODE(retained.stat().st_mode) == 0o700
    assert len(list(output.iterdir())) == 1


def test_build_refuses_builder_metadata_for_a_different_source_revision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repository(repo)

    def runner(argv: Sequence[str], _cwd: Path) -> CommandResult:
        destination = Path(next(value.split("=", 2)[2] for value in argv if value.startswith("type=local,dest=")))
        write_release_tree(destination, source_revision="d" * 40)
        return CommandResult(returncode=0, stdout="", stderr="")

    with pytest.raises(OpsError) as raised:
        build_release(
            repo,
            tmp_path / "artifacts",
            source_reader=lambda _repo: SourceState(revision=REVISION, clean=True),
            command_runner=runner,
            source_exporter=export_source,
        )

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE
    assert raised.value.next_action is not None


def test_application_version_is_read_as_a_literal_without_evaluating_mix_code(tmp_path: Path) -> None:
    mix = tmp_path / "mix.exs"
    mix.write_text(
        'version = System.cmd("touch", ["must-not-run"])\nversion: "0.2.0"\n', encoding="utf-8"
    )

    assert read_application_version(mix) == "0.2.0"
    assert not (tmp_path / "must-not-run").exists()


def test_build_command_reports_the_verified_artifact_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "taskman.tar.gz"
    manifest = tmp_path / "taskman.manifest.json"
    checksum = tmp_path / "taskman.tar.gz.sha256"
    artifact = SimpleNamespace(
        manifest=SimpleNamespace(release_id=RELEASE_ID, source_revision=REVISION),
        sha256="a" * 64,
        archive=archive,
        manifest_path=manifest,
        checksum=checksum,
    )
    monkeypatch.setattr("taskman_ops.build.build_release", lambda _repo, _output: artifact)

    result = dispatch(Invocation(command="build"))

    assert result.command == "build"
    assert result.changed is True
    assert result.stage == "built"
    assert result.facts == {
        "release_id": RELEASE_ID,
        "source_revision": REVISION,
        "artifact_sha256": "a" * 64,
        "archive": str(archive),
        "manifest": str(manifest),
        "checksum": str(checksum),
    }


@pytest.mark.parametrize(
    "source",
    [
        'version: System.get_env("VERSION")\n',
        'version: "0.2.0/unsafe"\n',
        'version: "0.2"\n',
    ],
)
def test_application_version_refuses_nonliteral_or_unsafe_values(tmp_path: Path, source: str) -> None:
    mix = tmp_path / "mix.exs"
    mix.write_text(source, encoding="utf-8")

    with pytest.raises(OpsError) as raised:
        read_application_version(mix)

    assert raised.value.status is ExitStatus.LOCAL_PREREQUISITE
