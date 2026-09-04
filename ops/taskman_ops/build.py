"""Build target-compatible Taskman releases from a clean identified revision."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any

from .errors import ExitStatus, OpsError
from .manifests import (
    APPLICATION,
    ARCHITECTURE,
    ELIXIR_VERSION,
    NODE_VERSION,
    OTP_VERSION,
    SCHEMA_VERSION,
    TARGET_OS,
    TOP_LEVEL,
    ArtifactManifest,
    VerifiedArtifact,
    fingerprint_migrations,
    manifest_to_json,
    sha256_file,
    verify_artifact,
)
from .releases.identifiers import build_release_id, validate_application_version, validate_source_revision


BUILDER_PLATFORM = "linux/amd64"
_PROJECT_FUNCTION_RE = re.compile(r"(?m)^[ \t]*def[ \t]+project[ \t]+do\b")
_PROJECT_VERSION_RE = re.compile(r'version\s*:\s*"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)"')
_TOOLCHAIN_FIELDS = frozenset(
    {"source_revision", "target_os", "architecture", "otp_version", "elixir_version", "node_version"}
)


@dataclass(frozen=True)
class CommandResult:
    """Captured result from the injectable local command boundary."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class SourceState:
    """The source identity that may enter the immutable builder."""

    revision: str | None
    clean: bool


CommandRunner = Callable[[Sequence[str], Path], CommandResult]
SourceReader = Callable[[Path], SourceState]
SourceExporter = Callable[[Path, str, Path], None]
Clock = Callable[[], datetime]


def _build_error(message: str, *, artifact_dir: Path | None = None) -> OpsError:
    next_action = None
    if artifact_dir is not None:
        next_action = f"inspect retained artifact directory: {artifact_dir}"
    return OpsError(
        status=ExitStatus.LOCAL_PREREQUISITE,
        stage="build",
        message=message,
        changed=False,
        next_action=next_action,
    )


def _run_command(argv: Sequence[str], cwd: Path) -> CommandResult:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return CommandResult(returncode=127, stdout="", stderr="")
    return CommandResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def read_repository_state(repo: Path) -> SourceState:
    """Require an ordinary Git worktree with no tracked or untracked changes."""

    repo = Path(repo)
    status = _run_command(("git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"), repo)
    if status.returncode != 0:
        return SourceState(revision=None, clean=False)
    revision = _run_command(("git", "-C", str(repo), "rev-parse", "--verify", "HEAD"), repo)
    candidate = revision.stdout.strip() if revision.returncode == 0 else None
    try:
        resolved = validate_source_revision(candidate) if candidate is not None else None
    except ValueError:
        resolved = None
    return SourceState(revision=resolved, clean=status.stdout == "")


def _skip_whitespace_and_comments(source: str, index: int) -> int:
    while index < len(source):
        if source[index].isspace():
            index += 1
        elif source[index] == "#":
            newline = source.find("\n", index)
            index = len(source) if newline == -1 else newline + 1
        else:
            return index
    return index


def _project_keyword_list(source: str) -> str | None:
    matches = tuple(_PROJECT_FUNCTION_RE.finditer(source))
    if len(matches) != 1:
        return None
    start = _skip_whitespace_and_comments(source, matches[0].end())
    if start == len(source) or source[start] != "[":
        return None
    index = start + 1
    depth = 1
    in_string = False
    while index < len(source):
        character = source[index]
        if in_string:
            if character == "\\":
                index += 2
                continue
            if character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character == "#":
            newline = source.find("\n", index)
            index = len(source) if newline == -1 else newline
            continue
        elif character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
        index += 1
    return None


def _project_version_values(keywords: str) -> tuple[str, ...]:
    values: list[str] = []
    index = 0
    depth = 0
    in_string = False
    while index < len(keywords):
        character = keywords[index]
        if in_string:
            if character == "\\":
                index += 2
                continue
            if character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
        elif character == "#":
            newline = keywords.find("\n", index)
            index = len(keywords) if newline == -1 else newline
            continue
        elif character in "[{(":
            depth += 1
        elif character in "]})":
            if depth == 0:
                return ()
            depth -= 1
        elif depth == 0 and (index == 0 or not (keywords[index - 1].isalnum() or keywords[index - 1] == "_")):
            match = _PROJECT_VERSION_RE.match(keywords, index)
            if match is not None:
                values.append(match.group("version"))
                index = match.end()
                continue
        index += 1
    return tuple(values)


def read_application_version(mix_file: Path) -> str:
    """Read one literal version from the ``project`` keyword list without evaluation."""

    try:
        source = mix_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise _build_error("unable to read the application version") from None
    keywords = _project_keyword_list(source)
    values = _project_version_values(keywords) if keywords is not None else ()
    if len(values) != 1:
        raise _build_error("mix.exs does not declare a literal release-safe version")
    try:
        return validate_application_version(values[0])
    except ValueError:
        raise _build_error("mix.exs does not declare a release-safe version") from None


def _source_member_parts(name: object) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise _build_error("source archive contains an unsafe member")
    parts = tuple(name.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise _build_error("source archive contains an unsafe member")
    return parts


def _export_source_from_git(repo: Path, revision: str, destination: Path) -> None:
    """Materialize only tracked bytes from the checked source object for Docker.

    Git's archive output excludes ignored workstation files such as private age
    identities and environment files.  Every member is checked before the
    archive is extracted, keeping the Docker context free of controller state.
    """

    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", revision],
            check=False,
            capture_output=True,
        )
    except OSError:
        raise _build_error("unable to export the identified source") from None
    if completed.returncode != 0:
        raise _build_error("unable to export the identified source")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:")
    except tarfile.TarError:
        raise _build_error("identified source archive is invalid") from None
    with archive:
        members = archive.getmembers()
        if not members:
            raise _build_error("identified source archive is empty")
        for member in members:
            _source_member_parts(member.name)
            if not (member.isdir() or member.isreg()) or member.isdev() or member.isfifo() or member.issym() or member.islnk():
                raise _build_error("identified source archive has an unsupported member")
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(destination, 0o700)
        try:
            archive.extractall(destination, members=members, filter="data")
        except (OSError, tarfile.TarError):
            raise _build_error("unable to materialize the identified source") from None


def _load_toolchain(path: Path, source_revision: str) -> dict[str, str]:
    try:
        parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _build_error("builder did not produce valid toolchain metadata") from None
    if not isinstance(parsed, dict) or set(parsed) != _TOOLCHAIN_FIELDS or not all(isinstance(value, str) for value in parsed.values()):
        raise _build_error("builder did not produce valid toolchain metadata")
    expected = {
        "source_revision": source_revision,
        "target_os": TARGET_OS,
        "architecture": ARCHITECTURE,
        "otp_version": OTP_VERSION,
        "elixir_version": ELIXIR_VERSION,
        "node_version": NODE_VERSION,
    }
    if parsed != expected:
        raise _build_error("builder produced an unsupported toolchain")
    return parsed


def _write_private_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def _archive_release(release_root: Path, archive_path: Path) -> None:
    if release_root.is_symlink() or not release_root.is_dir():
        raise _build_error("builder did not produce a release directory")
    try:
        with tarfile.open(archive_path, mode="w:gz", format=tarfile.PAX_FORMAT, dereference=False) as archive:
            archive.add(release_root, arcname=TOP_LEVEL, recursive=True)
        archive_path.chmod(0o600)
    except (OSError, tarfile.TarError):
        raise _build_error("unable to package the release archive") from None


def _private_artifact_root(output_dir: Path, release_id: str) -> Path:
    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        artifact_dir = Path(tempfile.mkdtemp(prefix=f"{release_id}-", dir=output_dir))
        artifact_dir.chmod(0o700)
    except OSError:
        raise _build_error("unable to create a private artifact directory") from None
    return artifact_dir


def _name_artifact_root(artifact_dir: Path, release_id: str) -> Path:
    token = artifact_dir.name.removeprefix("snapshot-")
    named = artifact_dir.with_name(f"{release_id}-{token}")
    try:
        artifact_dir.rename(named)
    except OSError:
        raise _build_error("unable to name the private artifact directory") from None
    return named


def _build_command(source_dir: Path, output_dir: Path, revision: str) -> tuple[str, ...]:
    return (
        "docker",
        "buildx",
        "build",
        "--platform",
        BUILDER_PLATFORM,
        "--build-arg",
        f"SOURCE_REVISION={revision}",
        "--file",
        str(source_dir / "ops" / "builder" / "Containerfile"),
        "--target",
        "artifact",
        "--output",
        f"type=local,dest={output_dir}",
        str(source_dir),
    )


def build_release(
    repo: Path,
    output_dir: Path,
    *,
    command_runner: CommandRunner = _run_command,
    source_reader: SourceReader = read_repository_state,
    source_exporter: SourceExporter = _export_source_from_git,
    clock: Clock = lambda: datetime.now(UTC),
) -> VerifiedArtifact:
    """Build, package, and verify an immutable artifact from one clean Git revision."""

    repo = Path(repo).resolve()
    state = source_reader(repo)
    if not state.clean or state.revision is None:
        raise _build_error("source checkout must be clean and identified")
    try:
        revision = validate_source_revision(state.revision)
    except ValueError:
        raise _build_error("source checkout must be clean and identified") from None
    artifact_dir = _private_artifact_root(Path(output_dir), "snapshot")
    source_dir = artifact_dir / "source"
    build_output = artifact_dir / "build-output"
    try:
        source_exporter(repo, revision, source_dir)
        application_version = read_application_version(source_dir / "mix.exs")
        migration_fingerprints = fingerprint_migrations(source_dir / "priv" / "repo" / "migrations")
        release_id = build_release_id(application_version, revision)
        artifact_dir = _name_artifact_root(artifact_dir, release_id)
        source_dir = artifact_dir / "source"
        build_output = artifact_dir / "build-output"
        result = command_runner(_build_command(source_dir, build_output, revision), repo)
        if result.returncode != 0:
            raise _build_error("release builder failed", artifact_dir=artifact_dir)
        toolchain = _load_toolchain(build_output / "toolchain.json", revision)
        archive = artifact_dir / f"taskman-{release_id}.tar.gz"
        _archive_release(build_output / TOP_LEVEL, archive)
        timestamp = clock()
        if timestamp.tzinfo is None:
            raise _build_error("build clock did not return UTC provenance", artifact_dir=artifact_dir)
        timestamp = timestamp.astimezone(UTC)
        manifest = ArtifactManifest.from_mapping(
            {
                "schema_version": SCHEMA_VERSION,
                "application": APPLICATION,
                "application_version": application_version,
                "source_revision": revision,
                "release_id": release_id,
                "built_at": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "target_os": toolchain["target_os"],
                "architecture": toolchain["architecture"],
                "otp_version": toolchain["otp_version"],
                "elixir_version": toolchain["elixir_version"],
                "node_version": toolchain["node_version"],
                "migrations": [fingerprint.to_mapping() for fingerprint in migration_fingerprints],
                "top_level": TOP_LEVEL,
            }
        )
        manifest_path = artifact_dir / f"taskman-{release_id}.manifest.json"
        _write_private_text(manifest_path, manifest_to_json(manifest))
        checksum = artifact_dir / f"taskman-{release_id}.tar.gz.sha256"
        _write_private_text(checksum, f"{sha256_file(archive)}  {archive.name}\n")
        verified = verify_artifact(archive, manifest_path, checksum)
    except OpsError as error:
        if error.next_action is None:
            raise _build_error(error.message, artifact_dir=artifact_dir) from None
        raise
    except Exception:
        raise _build_error("release packaging failed", artifact_dir=artifact_dir) from None
    finally:
        # Final archive and metadata make a successful build independently
        # reusable; transient source and BuildKit output do not need retention.
        if "verified" in locals():
            shutil.rmtree(source_dir, ignore_errors=True)
            shutil.rmtree(build_output, ignore_errors=True)
    return verified


__all__ = [
    "BUILDER_PLATFORM",
    "CommandResult",
    "SourceState",
    "build_release",
    "read_application_version",
    "read_repository_state",
]
