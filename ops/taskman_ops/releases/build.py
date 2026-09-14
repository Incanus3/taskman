"""Build target-compatible Taskman releases from a clean identified revision."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import fnmatch
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any

from ..checksums import sha256_file
from ..errors import ExitStatus, OpsError
from .manifests import (
    APPLICATION,
    ARCHITECTURE,
    BUILDER_BASE_DIGEST,
    BUILDER_BASE_TAG,
    ELIXIR_VERSION,
    HEX_VERSION,
    NODE_VERSION,
    OTP_VERSION,
    REBAR3_VERSION,
    SCHEMA_VERSION,
    TARGET_OS,
    TOP_LEVEL,
    ArtifactManifest,
    VerifiedArtifact,
    fingerprint_migrations,
    manifest_to_json,
    verify_artifact,
)
from .identifiers import build_release_id, validate_application_version, validate_source_revision


BUILDER_PLATFORM = "linux/amd64"
_PROJECT_FUNCTION_RE = re.compile(r"(?m)^[ \t]*def[ \t]+project[ \t]+do\b")
_PROJECT_VERSION_KEY_RE = re.compile(r"version\s*:")
_PROJECT_VERSION_LITERAL_RE = re.compile(r'\s*"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)"')
_TOOLCHAIN_FIELDS = frozenset(
    {
        "source_revision",
        "target_os",
        "architecture",
        "otp_version",
        "elixir_version",
        "node_version",
        "hex_version",
        "rebar3_version",
    }
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

MAX_SOURCE_MEMBERS = 16_384
MAX_SOURCE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
_SOURCE_EXCLUDED_COMPONENTS = frozenset({".git", ".beads", ".codex", ".superpowers", "__pycache__"})


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


def _source_input_error(message: str) -> OpsError:
    return OpsError(
        status=ExitStatus.INVALID,
        stage="build-input",
        message=message,
        changed=False,
        next_action="correct the source input and retry",
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


def _literal_value_ends_at_keyword_boundary(source: str, index: int) -> bool:
    index = _skip_whitespace_and_comments(source, index)
    return index == len(source) or source[index] == ","


def _project_version_values(keywords: str) -> tuple[str | None, ...]:
    values: list[str | None] = []
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
            key = _PROJECT_VERSION_KEY_RE.match(keywords, index)
            if key is not None:
                literal = _PROJECT_VERSION_LITERAL_RE.match(keywords, key.end())
                if literal is not None and _literal_value_ends_at_keyword_boundary(keywords, literal.end()):
                    values.append(literal.group("version"))
                    index = literal.end()
                else:
                    values.append(None)
                    index = key.end()
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
    version = values[0] if len(values) == 1 else None
    if version is None:
        raise _build_error("mix.exs does not declare a literal release-safe version")
    try:
        return validate_application_version(version)
    except ValueError:
        raise _build_error("mix.exs does not declare a release-safe version") from None


def _source_member_parts(name: object) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise _build_error("source archive contains an unsafe member")
    parts = tuple(name.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise _build_error("source archive contains an unsafe member")
    return parts


def _excluded_source_member(parts: tuple[str, ...]) -> bool:
    """Keep controller state and common private inputs outside a dirty snapshot."""

    if any(part in _SOURCE_EXCLUDED_COMPONENTS for part in parts):
        return True
    name = parts[-1]
    return (
        name.endswith(".agekey")
        or name in {".env", ".envrc"}
        or (len(parts) >= 3 and parts[:2] == ("ops", "environments") and fnmatch.fnmatch(name, "*.secrets.yaml"))
        or (len(parts) >= 3 and parts[:2] == ("ops", "environments") and fnmatch.fnmatch(name, "*.decrypted.yaml"))
    )


def _git_bytes(repo: Path, argv: Sequence[str]) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *argv], check=False, capture_output=True
        )
    except OSError:
        raise _build_error("unable to inspect the source checkout") from None
    if completed.returncode != 0:
        raise _build_error("unable to inspect the source checkout")
    return completed.stdout


def _nul_paths(value: bytes) -> tuple[str, ...]:
    if not value:
        return ()
    if not value.endswith(b"\0"):
        raise _build_error("source checkout returned invalid path data")
    try:
        return tuple(item.decode("utf-8", "strict") for item in value[:-1].split(b"\0"))
    except UnicodeDecodeError:
        raise _build_error("source checkout returned invalid path data") from None


def _tracked_snapshot_paths(repo: Path) -> tuple[str, ...]:
    entries = _nul_paths(_git_bytes(repo, ("ls-files", "--stage", "-z")))
    paths: list[str] = []
    for entry in entries:
        try:
            prefix, path = entry.split("\t", 1)
            mode, _object_id, stage = prefix.split(" ", 2)
        except ValueError:
            raise _build_error("source checkout returned invalid tracked-path data") from None
        if stage != "0":
            raise _source_input_error("source checkout has unresolved index entries")
        if mode == "160000":
            raise _source_input_error("source snapshot contains a submodule")
        paths.append(path)
    return tuple(paths)


def _source_file_identity(details: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mode,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _copy_dirty_source(repo: Path, revision: str, destination: Path) -> None:
    """Freeze supported checkout bytes, then prove the relevant Git view held still."""

    before_status = _git_bytes(repo, ("status", "--porcelain=v1", "-z", "--untracked-files=all"))
    before_head = _git_bytes(repo, ("rev-parse", "--verify", "HEAD")).strip()
    if before_head.decode("ascii", "ignore") != revision:
        raise _source_input_error("source revision changed before snapshot capture")

    tracked = _tracked_snapshot_paths(repo)
    untracked = _nul_paths(_git_bytes(repo, ("ls-files", "--others", "--exclude-standard", "-z")))
    selected = tuple(dict.fromkeys((*tracked, *untracked)))
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(destination, 0o700)
    total = 0
    copied = 0
    for name in selected:
        parts = _source_member_parts(name)
        if _excluded_source_member(parts):
            continue
        source = repo.joinpath(*parts)
        target = destination.joinpath(*parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            details = source.lstat()
        except FileNotFoundError:
            # A tracked deletion is an intentional part of the frozen view.
            if name in tracked:
                continue
            raise _source_input_error("source changed during snapshot capture") from None
        except OSError:
            raise _source_input_error("unable to inspect source snapshot member") from None
        if not stat.S_ISREG(details.st_mode):
            raise _source_input_error("source snapshot contains an unsupported member")
        identity = _source_file_identity(details)
        if details.st_size > MAX_SOURCE_MEMBER_BYTES:
            raise _source_input_error("source snapshot member exceeds supported size")
        copied += 1
        total += details.st_size
        if copied > MAX_SOURCE_MEMBERS or total > MAX_SOURCE_BYTES:
            raise _source_input_error("source snapshot exceeds supported bounds")
        try:
            shutil.copyfile(source, target)
            os.chmod(target, stat.S_IMODE(details.st_mode) & 0o777)
        except OSError:
            raise _source_input_error("unable to materialize source snapshot") from None
        try:
            after_copy = source.lstat()
        except OSError:
            raise _source_input_error("source changed during snapshot capture") from None
        if not stat.S_ISREG(after_copy.st_mode) or _source_file_identity(after_copy) != identity:
            raise _source_input_error("source changed during snapshot capture")

    after_status = _git_bytes(repo, ("status", "--porcelain=v1", "-z", "--untracked-files=all"))
    after_head = _git_bytes(repo, ("rev-parse", "--verify", "HEAD")).strip()
    if before_status != after_status or before_head != after_head:
        raise _source_input_error("source changed during snapshot capture")


def _export_source(repo: Path, state: SourceState, destination: Path, source_exporter: SourceExporter) -> None:
    if state.clean:
        source_exporter(repo, state.revision or "", destination)
    elif source_exporter is _export_source_from_git:
        _copy_dirty_source(repo, state.revision or "", destination)
    else:
        # Injectable exporters remain a narrow unit-test seam; production uses
        # the stable Git-aware snapshotter above.
        source_exporter(repo, state.revision or "", destination)


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
        "hex_version": HEX_VERSION,
        "rebar3_version": REBAR3_VERSION,
    }
    if parsed != expected:
        raise _build_error("builder produced an unsupported toolchain")
    return parsed


def _write_private_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def _normalized_tarinfo(member: tarfile.TarInfo) -> tarfile.TarInfo:
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    member.mtime = 0
    return member


def _archive_release(release_root: Path, archive_path: Path) -> None:
    if release_root.is_symlink() or not release_root.is_dir():
        raise _build_error("builder did not produce a release directory")
    try:
        with archive_path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT, dereference=False) as archive:
                archive.add(release_root, arcname=TOP_LEVEL, recursive=True, filter=_normalized_tarinfo)
        archive_path.chmod(0o600)
    except (OSError, tarfile.TarError):
        raise _build_error("unable to package the release archive") from None


def _private_artifact_root(output_dir: Path, release_id: str) -> Path:
    try:
        ensure_artifact_root(output_dir)
        artifact_dir = Path(tempfile.mkdtemp(prefix=f"{release_id}-", dir=output_dir))
        artifact_dir.chmod(0o700)
    except OpsError:
        raise
    except OSError:
        raise _build_error("unable to create a private artifact directory") from None
    return artifact_dir


def default_artifact_root() -> Path:
    """Return the private workstation root shared by build and deploy."""

    return Path(tempfile.gettempdir()) / f"taskman-artifacts-{os.getuid()}"


def ensure_artifact_root(path: Path) -> Path:
    """Create and validate the private root shared by build and deploy."""

    path = Path(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        pass
    except OSError:
        raise _build_error("unable to create the private artifact root") from None

    try:
        metadata = path.lstat()
    except OSError:
        raise _build_error("unable to inspect the private artifact root") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise _build_error("artifact root must be an owned private directory")
    return path


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
    allow_dirty: bool = False,
    command_runner: CommandRunner = _run_command,
    source_reader: SourceReader = read_repository_state,
    source_exporter: SourceExporter = _export_source_from_git,
    clock: Clock = lambda: datetime.now(UTC),
) -> VerifiedArtifact:
    """Build one clean source export or a private frozen dirty snapshot."""

    repo = Path(repo).resolve()
    state = source_reader(repo)
    if state.revision is None or (not state.clean and not allow_dirty):
        raise _build_error("source checkout must be clean and identified")
    try:
        revision = validate_source_revision(state.revision)
    except ValueError:
        raise _build_error("source checkout must be clean and identified") from None
    artifact_dir = _private_artifact_root(Path(output_dir), "snapshot")
    source_dir = artifact_dir / "source"
    build_output = artifact_dir / "build-output"
    try:
        _export_source(repo, state, source_dir, source_exporter)
        application_version = read_application_version(source_dir / "mix.exs")
        migration_fingerprints = fingerprint_migrations(source_dir / "priv" / "repo" / "migrations")
        result = command_runner(_build_command(source_dir, build_output, revision), repo)
        if result.returncode != 0:
            raise _build_error("release builder failed", artifact_dir=artifact_dir)
        toolchain = _load_toolchain(build_output / "toolchain.json", revision)
        archive = artifact_dir / "taskman.tar.gz"
        _archive_release(build_output / TOP_LEVEL, archive)
        archive_sha256 = sha256_file(archive)
        release_id = build_release_id(
            application_version,
            revision,
            artifact_sha256=archive_sha256,
            source_dirty=not state.clean,
        )
        artifact_dir = _name_artifact_root(artifact_dir, release_id)
        source_dir = artifact_dir / "source"
        build_output = artifact_dir / "build-output"
        archive = artifact_dir / f"taskman-{release_id}.tar.gz"
        try:
            (artifact_dir / "taskman.tar.gz").rename(archive)
        except OSError:
            raise _build_error("unable to name the release archive", artifact_dir=artifact_dir) from None
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
                "builder_base_tag": BUILDER_BASE_TAG,
                "builder_base_digest": BUILDER_BASE_DIGEST,
                "hex_version": toolchain["hex_version"],
                "rebar3_version": toolchain["rebar3_version"],
                "migrations": [fingerprint.to_mapping() for fingerprint in migration_fingerprints],
                "top_level": TOP_LEVEL,
                "artifact_sha256": archive_sha256,
                "source_dirty": not state.clean,
            }
        )
        # This validates the full installed representation before the local
        # artifact becomes a reusable cache entry.
        from ..host_helper.records import ReleaseRecord

        ReleaseRecord(
            release_id,
            revision,
            archive_sha256,
            tuple(item.to_mapping() for item in migration_fingerprints),
            2,
            manifest,
        )
        manifest_path = artifact_dir / f"taskman-{release_id}.manifest.json"
        _write_private_text(manifest_path, manifest_to_json(manifest))
        checksum = artifact_dir / f"taskman-{release_id}.tar.gz.sha256"
        _write_private_text(checksum, f"{archive_sha256}  {archive.name}\n")
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
    "MAX_SOURCE_BYTES",
    "MAX_SOURCE_MEMBER_BYTES",
    "MAX_SOURCE_MEMBERS",
    "SourceState",
    "build_release",
    "default_artifact_root",
    "ensure_artifact_root",
    "read_application_version",
    "read_repository_state",
]
