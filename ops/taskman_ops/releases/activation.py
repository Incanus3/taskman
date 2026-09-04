"""Fail-closed migration and atomic release-selection transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import base64
import json
import re
from typing import Literal
from uuid import uuid4

from ..errors import ExitStatus, OpsError
from ..manifests import ArtifactManifest, MigrationFingerprint
from ..remote import Remote
from .identifiers import validate_release_id
from .remote_locking import REMOTE_LOCK_FRAMING
from .records import BackupRecord, RemoteLifecycleStore

MigrationPolicy = Literal["no-change", "backward-compatible", "restore-required"]
_TOKEN_RE = re.compile(r"[0-9a-f]{32}\Z")
_POLICIES = frozenset({"backward-compatible", "restore-required"})


@dataclass(frozen=True)
class ActivationResult:
    """The selected activation edge after Taskman was started successfully."""

    activation_id: str
    previous_release_id: str
    candidate_release_id: str
    activated_at: datetime
    backup_id: str
    migration_policy: MigrationPolicy


_ACTIVATION_BODY = r'''set -eu
umask 077
safe_path() { case "$1" in /*) ;; *) return 1;; esac; case "$1" in *[!A-Za-z0-9_./+-]*|*..*) return 1;; esac; }
safe_release_id() { printf '%s\n' "$1" | grep -Eq '^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$'; }
safe_backup_id() { printf '%s\n' "$1" | grep -Eq '^backup-[0-9a-f]{32}$'; }
safe_activation_id() { printf '%s\n' "$1" | grep -Eq '^activation-[0-9a-f]{32}$'; }
safe_token() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{32}$'; }
safe_sha256() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'; }
safe_policy() { case "$1" in no-change|backward-compatible|restore-required) return 0;; *) return 1;; esac; }
safe_base64() { case "$1" in -) return 0;; *[!A-Za-z0-9+/=]*) return 1;; *) return 0;; esac; }
safe_dir() {
  test -d "$1" && test ! -L "$1" || return 1
  test "$(stat -c %u -- "$1")" = 0 || return 1
  mode=$(stat -c %a -- "$1") || return 1
  case "$mode" in [0-7][2367][0-7]|[0-7][0-7][2367]) return 1;; esac
}
release_complete() {
  test -d "$candidate" && test ! -L "$candidate" || return 1
  test -f "$candidate/.taskman-release.json" && test ! -L "$candidate/.taskman-release.json" || return 1
  test -x "$candidate/bin/migrate" && test -x "$candidate/bin/server" || return 1
  test -z "$(find -P "$candidate" ! -user root -print -quit)" || return 1
  test -z "$(find -P "$candidate" ! -group taskman -print -quit)" || return 1
  links=$(find -P "$candidate" -type l -print) || return 1
  if test -n "$links"; then
    while IFS= read -r link; do
      target=$(readlink -f "$link") || return 1
      case "$target" in "$candidate"|"$candidate"/*) ;; *) return 1;; esac
    done <<EOF
$links
EOF
  fi
  test -z "$(find -P "$candidate" -perm /022 -print -quit)" || return 1
}
failure() {
  failure_status=$1; failure_service=$2; failure_selected=$3; failure_database=$4
  printf '{"service_state":"%s","selected_release_id":"%s","database_state":"%s","activation_recorded":false,"changed_stages":%s}\n' "$failure_service" "$failure_selected" "$failure_database" "$(activation_stages_json)"
  exit "$failure_status"
}
mark_activation_stage() {
  activation_stages="${activation_stages}${activation_stages:+,}$1"
}
activation_stages_json() {
  test -n "$activation_stages" || { printf '[]'; return; }
  previous_ifs=$IFS; IFS=,; set -- $activation_stages; IFS=$previous_ifs
  first=1; printf '['
  for activation_stage in "$@"; do
    test "$first" = 1 || printf ','
    printf '"%s"' "$activation_stage"; first=0
  done
  printf ']'
}
ensure_record_dir() {
  if test -e "$1" || test -L "$1"; then safe_dir "$1" || return 1
  else install -d -o root -g root -m 750 -- "$1" || return 1; safe_dir "$1" || return 1
  fi
}
write_record() {
  record_directory=$1; record_name=$2; record_content=$3
  ensure_record_dir "$record_directory" || return 1
  record_target="$record_directory/$record_name"
  test ! -e "$record_target" && test ! -L "$record_target" || return 1
  record_temporary=$(mktemp "$record_directory/.$record_name.XXXXXXXX") || return 1
  printf '%s\n' "$record_content" > "$record_temporary" || return 1
  chown root:root -- "$record_temporary" && chmod 600 -- "$record_temporary" && sync -f "$record_temporary" || return 1
  ln -- "$record_temporary" "$record_target" && sync -f "$record_directory" || return 1
  rm -f -- "$record_temporary"
}
write_manifest() {
  test "$manifest_b64" = - && return 0
  ensure_record_dir "$deployment_root/manifests" || return 1
  manifest_target="$deployment_root/manifests/release-$candidate_release_id.json"
  test ! -e "$manifest_target" && test ! -L "$manifest_target" || return 1
  manifest_temporary=$(mktemp "$deployment_root/manifests/.release-$candidate_release_id.XXXXXXXX") || return 1
  printf '%s' "$manifest_b64" | base64 -d > "$manifest_temporary" || return 1
  chown root:root -- "$manifest_temporary" && chmod 600 -- "$manifest_temporary" && sync -f "$manifest_temporary" || return 1
  ln -- "$manifest_temporary" "$manifest_target" && sync -f "$deployment_root/manifests" || return 1
  rm -f -- "$manifest_temporary"
}
managed_root=$1; release_root=$2; deployment_root=$3; previous_release_id=$4; candidate_release_id=$5; backup_id=$6; activation_id=$7; token=$8; artifact_sha256=$9; migration_policy=${10}; manifest_b64=${11}; previous_release_path=${12:-"$release_root/$previous_release_id"}
safe_path "$managed_root" && safe_path "$release_root" || exit 2
safe_path "$deployment_root" && safe_path "$previous_release_path" || exit 2
safe_release_id "$previous_release_id" && safe_release_id "$candidate_release_id" && safe_backup_id "$backup_id" && safe_activation_id "$activation_id" && safe_token "$token" && safe_sha256 "$artifact_sha256" && safe_policy "$migration_policy" && safe_base64 "$manifest_b64" || exit 2
test "$previous_release_id" != "$candidate_release_id" || exit 10
safe_dir "$managed_root" && safe_dir "$release_root" || exit 10
candidate="$release_root/$candidate_release_id"
activation_stages=
release_complete || exit 10
test -L "$managed_root/current" || exit 10
selected=$(readlink -f -- "$managed_root/current") || exit 10
case "$previous_release_path" in "$release_root"/*) ;; *) exit 10;; esac
test "$selected" = "$previous_release_path" || exit 10
mark_activation_stage stop
if ! systemctl stop taskman.service; then failure 8 unknown "$previous_release_id" unchanged; fi
if systemctl is-active --quiet taskman.service; then failure 8 unknown "$previous_release_id" unchanged; fi
mark_activation_stage migration
if ! systemd-run --wait --quiet --collect --property=User=taskman --property=Group=taskman --property=EnvironmentFile=/etc/taskman/taskman.env "$candidate/bin/migrate"; then
  # The previous selection remains intact, but migrations may have committed.
  failure 7 stopped "$previous_release_id" unknown
fi
selection="$managed_root/.current-$token"
mark_activation_stage selection
ln -s -- "$candidate" "$selection" || failure 8 stopped "$previous_release_id" unknown
mv -T -- "$selection" "$managed_root/current" || failure 8 stopped "$previous_release_id" unknown
mark_activation_stage start
database_state=changed
test "$migration_policy" != no-change || database_state=unchanged
if ! systemctl start taskman.service; then failure 8 unknown "$candidate_release_id" "$database_state"; fi
activated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
release_record=$(printf '{"schema_version":1,"release_id":"%s","artifact_sha256":"%s","installed_at":"%s","activated_at":"%s","previous_release_id":"%s","backup_id":"%s","migration_policy":"%s"}' "$candidate_release_id" "$artifact_sha256" "$activated_at" "$activated_at" "$previous_release_id" "$backup_id" "$migration_policy")
activation_record=$(printf '{"schema_version":1,"activation_id":"%s","previous_release_id":"%s","candidate_release_id":"%s","activated_at":"%s","backup_id":"%s","migration_policy":"%s"}' "$activation_id" "$previous_release_id" "$candidate_release_id" "$activated_at" "$backup_id" "$migration_policy")
case "${TASKMAN_ACTIVATION_RECORD_MODE:-final}" in
  final)
    if ! ensure_record_dir "$deployment_root" || ! write_manifest || ! write_record "$deployment_root/releases" "release-$candidate_release_id.json" "$release_record" || ! write_record "$deployment_root/activations" "$activation_id.json" "$activation_record"; then
      failure 8 unknown "$candidate_release_id" changed
    fi
    ;;
  deferred) : ;;
  *) exit 2 ;;
esac
printf '{"activation_id":"%s","activated_at":"%s","service_state":"active","selected_release_id":"%s","database_state":"%s","activation_recorded":%s,"changed_stages":%s}\n' "$activation_id" "$activated_at" "$candidate_release_id" "$database_state" "$([ "${TASKMAN_ACTIVATION_RECORD_MODE:-final}" = final ] && printf true || printf false)" "$(activation_stages_json)"
'''


_GENESIS_ACTIVATION_BODY = r'''set -eu
umask 077
safe_path() { case "$1" in /*) ;; *) return 1;; esac; case "$1" in *[!A-Za-z0-9_./+-]*|*..*) return 1;; esac; }
safe_release_id() { printf '%s\n' "$1" | grep -Eq '^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$'; }
safe_backup_id() { printf '%s\n' "$1" | grep -Eq '^backup-[0-9a-f]{32}$'; }
safe_activation_id() { printf '%s\n' "$1" | grep -Eq '^activation-[0-9a-f]{32}$'; }
safe_token() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{32}$'; }
safe_sha256() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'; }
safe_policy() { case "$1" in no-change|restore-required) return 0;; *) return 1;; esac; }
safe_base64() { case "$1" in *[!A-Za-z0-9+/=]*) return 1;; *) return 0;; esac; }
safe_dir() {
  test -d "$1" && test ! -L "$1" || return 1
  test "$(stat -c %u -- "$1")" = 0 || return 1
  mode=$(stat -c %a -- "$1") || return 1
  case "$mode" in [0-7][2367][0-7]|[0-7][0-7][2367]) return 1;; esac
}
release_complete() {
  test -d "$candidate" && test ! -L "$candidate" || return 1
  test -f "$candidate/.taskman-release.json" && test ! -L "$candidate/.taskman-release.json" || return 1
  test -x "$candidate/bin/migrate" && test -x "$candidate/bin/server" || return 1
  test -z "$(find -P "$candidate" ! -user root -print -quit)" || return 1
  test -z "$(find -P "$candidate" ! -group taskman -print -quit)" || return 1
  links=$(find -P "$candidate" -type l -print) || return 1
  if test -n "$links"; then
    while IFS= read -r link; do
      target=$(readlink -f "$link") || return 1
      case "$target" in "$candidate"|"$candidate"/*) ;; *) return 1;; esac
    done <<EOF
$links
EOF
  fi
  test -z "$(find -P "$candidate" -perm /022 -print -quit)" || return 1
}
failure() {
  failure_status=$1; failure_service=$2; failure_selected=$3; failure_database=$4
  printf '{"service_state":"%s","selected_release_id":"%s","database_state":"%s","activation_recorded":false,"changed_stages":%s}\n' "$failure_service" "$failure_selected" "$failure_database" "$(activation_stages_json)"
  exit "$failure_status"
}
mark_activation_stage() { activation_stages="${activation_stages}${activation_stages:+,}$1"; }
activation_stages_json() {
  test -n "$activation_stages" || { printf '[]'; return; }
  previous_ifs=$IFS; IFS=,; set -- $activation_stages; IFS=$previous_ifs
  first=1; printf '['
  for activation_stage in "$@"; do
    test "$first" = 1 || printf ','
    printf '"%s"' "$activation_stage"; first=0
  done
  printf ']'
}
managed_root=$1; release_root=$2; deployment_root=$3; previous_release_id=$4; candidate_release_id=$5; backup_id=$6; activation_id=$7; token=$8; artifact_sha256=$9; migration_policy=${10}; manifest_b64=${11}
safe_path "$managed_root" && safe_path "$release_root" && safe_path "$deployment_root" || exit 2
test "$previous_release_id" = - || exit 2
safe_release_id "$candidate_release_id" && safe_backup_id "$backup_id" && safe_activation_id "$activation_id" && safe_token "$token" && safe_sha256 "$artifact_sha256" && safe_policy "$migration_policy" && safe_base64 "$manifest_b64" || exit 2
safe_dir "$managed_root" && safe_dir "$release_root" || exit 10
candidate="$release_root/$candidate_release_id"
activation_stages=
release_complete || exit 10
test ! -e "$managed_root/current" && test ! -L "$managed_root/current" || exit 10
mark_activation_stage migration
if ! systemd-run --wait --quiet --collect --property=User=taskman --property=Group=taskman --property=EnvironmentFile=/etc/taskman/taskman.env "$candidate/bin/migrate"; then
  failure 7 stopped unknown unknown
fi
selection="$managed_root/.current-$token"
mark_activation_stage selection
ln -s -- "$candidate" "$selection" || failure 8 stopped unknown unknown
mv -T -- "$selection" "$managed_root/current" || failure 8 stopped unknown unknown
mark_activation_stage start
database_state=changed
test "$migration_policy" != no-change || database_state=unchanged
if ! systemctl start taskman.service; then failure 8 unknown "$candidate_release_id" "$database_state"; fi
activated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"activation_id":"%s","activated_at":"%s","service_state":"active","selected_release_id":"%s","database_state":"%s","activation_recorded":false,"changed_stages":%s}\n' "$activation_id" "$activated_at" "$candidate_release_id" "$database_state" "$(activation_stages_json)"
'''


_ACTIVATION_TRANSACTION = (
    "set -eu\n"
    "lock_root=/var/lock/taskman; operation=deploy; timeout_ms=5000; owner_uid=0; lock_mode=exclusive\n"
    + REMOTE_LOCK_FRAMING
    + _ACTIVATION_BODY
)


def resolve_migration_policy(
    current: tuple[MigrationFingerprint, ...],
    candidate: tuple[MigrationFingerprint, ...],
    declared: str | None,
) -> MigrationPolicy:
    """Return the only policy safe to report for these two migration sets."""

    if not isinstance(current, tuple) or not isinstance(candidate, tuple):
        raise TypeError("migration fingerprints must be immutable tuples")
    if current == candidate:
        if declared not in {None, "no-change"}:
            raise _safety("an unchanged migration set cannot declare a changed-migration policy")
        return "no-change"
    if declared not in _POLICIES:
        raise _safety("changed migrations require an explicit backward-compatible or restore-required declaration")
    return declared  # type: ignore[return-value]


def activate_release(
    remote: Remote,
    store: RemoteLifecycleStore,
    *,
    previous_release_id: str,
    candidate_release_id: str,
    backup: BackupRecord,
    migration_policy: MigrationPolicy,
    operation_token: str | None = None,
    manifest: ArtifactManifest | None = None,
    artifact_sha256: str | None = None,
) -> ActivationResult:
    """Stop, migrate, select, and start one candidate without any rollback.

    Database effects are deliberately never reversed or hidden. A migration
    failure preserves the old selection with Taskman stopped; later failures
    preserve the observed new selection rather than restarting old code.
    """

    if remote is not store.remote:
        raise ValueError("activation remote does not match lifecycle store")
    previous = validate_release_id(previous_release_id)
    candidate = validate_release_id(candidate_release_id)
    if previous == candidate:
        raise _safety("candidate release is already current")
    if not isinstance(backup, BackupRecord) or not backup.validated:
        raise _safety("activation requires a validated pre-deploy backup")
    if backup.reason != "pre-deploy" or backup.current_release_id != previous or backup.candidate_release_id != candidate:
        raise _safety("pre-deploy backup does not match the activation edge")
    if migration_policy not in {"no-change", "backward-compatible", "restore-required"}:
        raise _safety("invalid deployment migration policy")
    if not isinstance(manifest, ArtifactManifest) or manifest.release_id != candidate:
        raise _safety("activation requires the exact candidate manifest")
    if artifact_sha256 is None or re.fullmatch(r"[0-9a-f]{64}", artifact_sha256) is None:
        raise _safety("candidate artifact checksum is invalid")

    token = _token(operation_token)
    expected_activation_id = f"activation-{token}"
    manifest_b64 = base64.b64encode(
        (json.dumps(manifest.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).decode("ascii")
    result = remote.run(
        (
            "sh", "-ceu", _ACTIVATION_TRANSACTION, "taskman-activate-release",
            store.managed_root.as_posix(), store.release_root.as_posix(), store.deployment_root.as_posix(),
            previous, candidate, backup.backup_id, expected_activation_id, token, artifact_sha256,
            migration_policy, manifest_b64,
        ),
        sudo=True,
        stdin=None,
        sensitive=False,
    )
    if result.returncode == ExitStatus.MIGRATION:
        raise _failure_from_result(
            result.stdout,
            ExitStatus.MIGRATION,
            "migration",
            "candidate migration failed; current remains on the previous release and Taskman is stopped",
            previous,
            backup.backup_id,
        )
    if result.returncode == ExitStatus.SAFETY:
        raise _safety("activation preconditions or immutable release state are unsafe")
    if result.returncode != ExitStatus.OK:
        raise _failure_from_result(
            result.stdout,
            ExitStatus.RELEASE,
            "activation",
            "activation or service start failed; no automatic rollback was attempted",
            None,
            backup.backup_id,
        )
    activation = _activation_result(
        result.stdout,
        expected_activation_id,
        previous,
        candidate,
        backup.backup_id,
        migration_policy,
    )
    return activation


def _activation_result(
    output: str,
    expected_activation_id: str,
    previous: str,
    candidate: str,
    backup_id: str,
    migration_policy: MigrationPolicy,
) -> ActivationResult:
    try:
        value = json.loads(output)
        if not isinstance(value, dict) or set(value) != {
            "activation_id",
            "activated_at",
            "service_state",
            "selected_release_id",
            "database_state",
            "activation_recorded",
            "changed_stages",
        }:
            raise ValueError
        activation_id = value["activation_id"]
        timestamp = value["activated_at"]
        if activation_id != expected_activation_id:
            raise ValueError
        if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
            raise ValueError
        activated_at = datetime.fromisoformat(f"{timestamp[:-1]}+00:00")
        if activated_at.tzinfo != UTC:
            raise ValueError
        if (
            value["service_state"] != "active"
            or value["selected_release_id"] != candidate
            or value["database_state"] != ("unchanged" if migration_policy == "no-change" else "changed")
            or value["activation_recorded"] is not True
            or value["changed_stages"] != ["stop", "migration", "selection", "start"]
        ):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError):
        raise _failure(
            ExitStatus.RELEASE,
            "activation",
            "activation returned an invalid lifecycle record",
            None,
            backup_id,
            activation_recorded="unknown",
        ) from None
    return ActivationResult(activation_id, previous, candidate, activated_at, backup_id, migration_policy)


def _token(value: str | None) -> str:
    token = uuid4().hex if value is None else value
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("activation token must be a lowercase UUID hex value")
    return token


def _failure(
    status: ExitStatus,
    stage: str,
    message: str,
    selected_release_id: str | None,
    backup_id: str,
    *,
    activation_recorded: bool | Literal["unknown"] = False,
) -> OpsError:
    error = OpsError(
        status,
        stage,
        message,
        changed=True,
        next_action="keep Taskman stopped, inspect the selected release and database migration state, then choose explicit recovery",
    )
    if selected_release_id is not None:
        error.selected_release_id = selected_release_id
    error.backup_id = backup_id
    error.database_changed = True
    error.activation_recorded = activation_recorded
    error.service_state = "stopped" if status is ExitStatus.MIGRATION else "unknown"
    return error


def _failure_from_result(
    output: str,
    status: ExitStatus,
    stage: str,
    message: str,
    default_selected_release_id: str | None,
    backup_id: str,
) -> OpsError:
    """Attach only validated fixed state facts emitted by the remote script."""

    error = _failure(status, stage, message, default_selected_release_id, backup_id)
    try:
        value = json.loads(output)
        if not isinstance(value, dict) or set(value) != {
            "service_state",
            "selected_release_id",
            "database_state",
            "activation_recorded",
            "changed_stages",
        }:
            raise ValueError
        selected = validate_release_id(value["selected_release_id"])
        if value["service_state"] not in {"stopped", "unknown"}:
            raise ValueError
        if value["database_state"] not in {"unchanged", "changed", "unknown"}:
            raise ValueError
        if value["activation_recorded"] is not False:
            raise ValueError
        if (
            not isinstance(value["changed_stages"], list)
            or any(stage not in {"stop", "migration", "selection", "start"} for stage in value["changed_stages"])
            or value["changed_stages"] != list(dict.fromkeys(value["changed_stages"]))
        ):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError):
        return error
    error.selected_release_id = selected
    error.service_state = value["service_state"]
    error.database_changed = value["database_state"] != "unchanged"
    error.changed_stages = tuple(value["changed_stages"])
    return error


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "activation",
        message,
        changed=False,
        next_action="inspect the managed lifecycle records and immutable release state before retrying",
    )


__all__ = ["ActivationResult", "MigrationPolicy", "activate_release", "resolve_migration_policy"]
