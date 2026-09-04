"""Private upload and immutable publication of one verified release archive."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
from typing import TYPE_CHECKING
from uuid import uuid4

from ..errors import ExitStatus, OpsError
from ..manifests import VerifiedArtifact
from ..remote import Remote
from .identifiers import managed_release_path, validate_release_id
from .remote_locking import REMOTE_LOCK_FRAMING
from .records import RemoteLifecycleStore

if TYPE_CHECKING:
    from ..remote import CommandResult


_TOKEN_RE = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class StagedRelease:
    """A complete immutable release directory, newly published or resumed."""

    release_id: str
    sha256: str
    release_path: PurePosixPath
    resumed: bool


_PREPARE_UPLOAD = r'''set -eu
umask 077
safe_path() { case "$1" in /*) ;; *) return 1;; esac; case "$1" in *[!A-Za-z0-9_./+-]*|*..*) return 1;; esac; }
safe_dir() {
  test -d "$1" && test ! -L "$1" || return 1
  test "$(stat -c %u -- "$1")" = 0 || return 1
  mode=$(stat -c %a -- "$1") || return 1
  case "$mode" in [0-7][2367][0-7]|[0-7][0-7][2367]) return 1;; esac
}
ensure_dir() {
  if test -e "$1" || test -L "$1"; then safe_dir "$1" || exit 10
  else install -d -o root -g root -m 750 -- "$1" || exit 8; safe_dir "$1" || exit 10
  fi
}
deployment_root=$1; release_root=$2; upload_root=$3
safe_path "$deployment_root" && safe_path "$release_root" && safe_path "$upload_root" || exit 2
safe_dir "$deployment_root" || exit 10
safe_dir "$release_root" || exit 10
ensure_dir "$upload_root"
'''


# This command receives no caller-controlled shell fragments. Every path,
# identifier, digest, and token is independently restricted before it reaches
# the argv construction below. It removes only its generated upload/staging
# paths; it never deletes or modifies a final release directory.
_STAGE_BODY = r'''set -eu
umask 077
safe_path() { case "$1" in /*) ;; *) return 1;; esac; case "$1" in *[!A-Za-z0-9_./+-]*|*..*) return 1;; esac; }
safe_release_id() { printf '%s\n' "$1" | grep -Eq '^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$'; }
safe_token() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{32}$'; }
safe_sha256() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'; }
safe_dir() {
  test -d "$1" && test ! -L "$1" || return 1
  test "$(stat -c %u -- "$1")" = 0 || return 1
  mode=$(stat -c %a -- "$1") || return 1
  case "$mode" in [0-7][2367][0-7]|[0-7][0-7][2367]) return 1;; esac
}
safe_private_upload() {
  test -f "$1" && test ! -L "$1" || return 1
  test "$(stat -c %u:%a -- "$1")" = 0:600
}
release_complete() {
  test -d "$1" && test ! -L "$1" || return 1
  test -f "$1/.taskman-release.json" && test ! -L "$1/.taskman-release.json" || return 1
  test "$(stat -c %u -- "$1")" = 0 || return 1
  test -x "$1/bin/server" && test -d "$1/lib" && test -d "$1/releases" || return 1
  test -z "$(find -P "$1" ! -user root -print -quit)" || return 1
  test -z "$(find -P "$1" ! -group taskman -print -quit)" || return 1
  links=$(find -P "$1" -type l -print) || return 1
  if test -n "$links"; then
    while IFS= read -r link; do
      target=$(readlink -f "$link") || return 1
      case "$target" in "$1"|"$1"/*) ;; *) return 1;; esac
    done <<EOF
$links
EOF
  fi
  test -z "$(find -P "$1" -perm /022 -print -quit)" || return 1
  test -z "$(find -P "$1" -perm /7000 -print -quit)" || return 1
  grep -Fqx "{\"schema_version\":1,\"release_id\":\"$release_id\",\"artifact_sha256\":\"$archive_sha256\"}" "$1/.taskman-release.json"
}
deployment_root=$1; release_root=$2; upload_root=$3; release_id=$4; archive_sha256=$5; token=$6; upload=$7
safe_path "$deployment_root" && safe_path "$release_root" && safe_path "$upload_root" && safe_path "$upload" || exit 2
safe_release_id "$release_id" && safe_sha256 "$archive_sha256" && safe_token "$token" || exit 2
safe_dir "$deployment_root" && safe_dir "$release_root" && safe_dir "$upload_root" || exit 10
expected_upload="$upload_root/.upload-$release_id-$token.tar.gz"
test "$upload" = "$expected_upload" || exit 10
safe_private_upload "$upload" || exit 10
stage=
published=0
cleanup() {
  rm -f -- "$upload" || :
  if test "$published" = 0 && test -n "$stage"; then rm -rf -- "$stage" || :; fi
  release_lifecycle_lock
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
remote_sha256=$(sha256sum -- "$upload" | awk '{print $1}') || exit 8
test "$remote_sha256" = "$archive_sha256" || exit 10
final="$release_root/$release_id"
if test -e "$final" || test -L "$final"; then
  release_complete "$final" || exit 10
  rm -f -- "$upload" || exit 8
  test ! -e "$upload" && test ! -L "$upload" || exit 8
  printf '%s\n' '{"outcome":"resumed"}'
  exit 0
fi
stage=$(mktemp -d "$release_root/.stage-$release_id-$token.XXXXXXXX") || exit 8
members=$(tar -tzf "$upload") || exit 8
printf '%s\n' "$members" | while IFS= read -r member; do
  case "$member" in taskman|taskman/*) ;; *) exit 10;; esac
  case "$member" in /*|*'..'*) exit 10;; esac
done
tar -xzf "$upload" -C "$stage" --no-same-owner --no-same-permissions || exit 8
content="$stage/taskman"
test -d "$content" && test ! -L "$content" || exit 10
test -x "$content/bin/server" && test -d "$content/lib" && test -d "$content/releases" || exit 10
test -z "$(find -P "$content" ! -type d ! -type f ! -type l -print -quit)" || exit 10
links=$(find -P "$content" -type l -print) || exit 10
if test -n "$links"; then
  while IFS= read -r link; do
    target=$(readlink -f "$link") || exit 10
    case "$target" in "$content"|"$content"/*) ;; *) exit 10;; esac
  done <<EOF
$links
EOF
fi
test -z "$(find -P "$content" -perm /022 -print -quit)" || exit 10
test -z "$(find -P "$content" -perm /7000 -print -quit)" || exit 10
getent group taskman >/dev/null 2>&1 || exit 10
find -P "$content" -exec chown -h root:taskman -- {} + || exit 8
find -P "$content" -type d -exec chown root:taskman -- {} + -exec chmod 750 -- {} + || exit 8
find -P "$content" -type f -perm /111 -exec chown root:taskman -- {} + -exec chmod 750 -- {} + || exit 8
find -P "$content" -type f ! -perm /111 -exec chown root:taskman -- {} + -exec chmod 640 -- {} + || exit 8
printf '{"schema_version":1,"release_id":"%s","artifact_sha256":"%s"}\n' "$release_id" "$archive_sha256" > "$content/.taskman-release.json" || exit 8
chown root:taskman -- "$content/.taskman-release.json" && chmod 640 -- "$content/.taskman-release.json" || exit 8
sync -f "$content/.taskman-release.json" || exit 8
sync -f "$content" || exit 8
mv -T -n -- "$content" "$final" || exit 10
published=1
rmdir -- "$stage" || exit 8
rm -f -- "$upload" || exit 8
test ! -e "$upload" && test ! -L "$upload" || exit 8
printf '%s\n' '{"outcome":"staged"}'
'''


_STAGE_TRANSACTION = (
    "set -eu\n"
    "lock_root=/var/lock/taskman; operation=deploy; timeout_ms=5000; owner_uid=0; lock_mode=exclusive\n"
    + REMOTE_LOCK_FRAMING
    + _STAGE_BODY
)


def stage_release(
    remote: Remote,
    artifact: VerifiedArtifact,
    store: RemoteLifecycleStore,
    *,
    operation_token: str | None = None,
) -> StagedRelease:
    """Upload, independently inspect, and publish one immutable release.

    A final release is either absent or proved complete by its private marker.
    Neither the retry path nor an error path edits that final tree.
    """

    if not isinstance(artifact, VerifiedArtifact):
        raise TypeError("release staging requires a verified artifact")
    if remote is not store.remote:
        raise ValueError("release staging remote does not match lifecycle store")
    release_id = validate_release_id(artifact.manifest.release_id)
    if artifact.manifest.release_id != release_id or not re.fullmatch(r"[0-9a-f]{64}", artifact.sha256):
        raise _safety("verified artifact identity is invalid")
    release_path = managed_release_path(store.release_root, release_id)
    token = _token(operation_token)
    upload_root = store.deployment_root / "uploads"
    upload_path = upload_root / f".upload-{release_id}-{token}.tar.gz"

    _require_success(
        remote.run(
            ("sh", "-ceu", _PREPARE_UPLOAD, "taskman-stage-prepare", store.deployment_root.as_posix(), store.release_root.as_posix(), upload_root.as_posix()),
            sudo=True,
            stdin=None,
            sensitive=False,
        ),
        "unable to prepare private release staging",
    )
    try:
        remote.put(artifact.archive, upload_path, mode=0o600, sensitive=True)
        result = remote.run(
            (
                "sh", "-ceu", _STAGE_TRANSACTION, "taskman-stage-release",
                store.deployment_root.as_posix(), store.release_root.as_posix(), upload_root.as_posix(),
                release_id, artifact.sha256, token, upload_path.as_posix(),
            ),
            sudo=True,
            stdin=None,
            sensitive=False,
        )
    except OpsError:
        _cleanup_private_upload(remote, upload_path)
        raise
    except Exception:
        _cleanup_private_upload(remote, upload_path)
        raise _release_error("private release upload failed") from None

    if result.returncode == ExitStatus.SAFETY:
        raise _safety("release staging found ambiguous, incomplete, or conflicting immutable content")
    if result.returncode != ExitStatus.OK:
        raise _release_error("release staging failed; the final release tree was left unchanged")
    try:
        response = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        raise _release_error("release staging returned an invalid result") from None
    if not isinstance(response, dict) or set(response) != {"outcome"} or response["outcome"] not in {"staged", "resumed"}:
        raise _release_error("release staging returned an invalid result")
    return StagedRelease(release_id, artifact.sha256, release_path, response["outcome"] == "resumed")


def _token(value: str | None) -> str:
    token = uuid4().hex if value is None else value
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("release staging token must be a lowercase UUID hex value")
    return token


def _cleanup_private_upload(remote: Remote, upload_path: PurePosixPath) -> None:
    """Best-effort removal of one exact operation-owned private file."""

    try:
        remote.run(("rm", "-f", "--", upload_path.as_posix()), sudo=True, stdin=None, sensitive=True)
    except Exception:
        # The primary error remains authoritative; its recovery action tells
        # the operator to inspect a possible private residue.
        return


def _require_success(result: CommandResult, message: str) -> None:
    if result.returncode == ExitStatus.SAFETY:
        raise _safety(message)
    if result.returncode != ExitStatus.OK:
        raise _release_error(message)


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "staging",
        message,
        changed=False,
        next_action="inspect the exact managed release and private staging paths before retrying",
    )


def _release_error(message: str) -> OpsError:
    return OpsError(
        ExitStatus.RELEASE,
        "staging",
        message,
        changed=False,
        next_action="inspect the private staging residue and immutable release state before retrying",
    )


__all__ = ["StagedRelease", "stage_release"]
