"""Guarded, evidence-producing database restore transaction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from uuid import uuid4

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..manifests import ArtifactManifest
from ..output import WorkflowResult
from ..releases.identifiers import validate_release_id
from ..releases.records import BackupRecord, LifecycleRecords, RemoteLifecycleStore, raise_remote_lifecycle_failure
from ..releases.remote_locking import REMOTE_LOCK_FRAMING
from ..releases.remote_snapshot import _SNAPSHOT_BODY
from ..remote import Remote
from ..services.backups import BACKUP_COMMAND
from ..verification import VerificationReport, _LOCKED_VERIFICATION_BODY
from .operational_preflight import validate_operational_preflight


_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_TOKEN_RE = re.compile(r"[0-9a-f]{32}\Z")
_SUCCESS_STAGES = ("backup", "stop", "restore", "validation", "swap", "records", "start", "verification")
_FAILURE_STAGES = frozenset({"preflight", "backup", "stop", "restore", "validation", "swap", "start", "verification", "records"})

_VALIDATE_RESTORE_DUMP = r"""set -eu
dump_path=$1
backup_root=$2
expected_size=$3
source_database_size=$4
database_port=$5
case "$dump_path" in "$backup_root"/*) ;; *) exit 2;; esac
test -f "$dump_path" && test ! -L "$dump_path"
case "$expected_size" in ''|*[!0-9]*) exit 2;; esac
test "$expected_size" -gt 0
case "$source_database_size" in ''|*[!0-9]*) exit 2;; esac
test "$source_database_size" -gt 0 && test "$source_database_size" -le 2305843009213693951
case "$database_port" in ''|*[!0-9]*) exit 2;; esac
test "$database_port" -ge 1 && test "$database_port" -le 65535
test "$(stat -c %s -- "$dump_path")" = "$expected_size"
pg_restore --list -- "$dump_path" >/dev/null 2>&1
data_directory=$(sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port "$database_port" --username postgres --dbname=postgres --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'SHOW data_directory' 2>/dev/null)
case "$data_directory" in /*) ;; *) exit 1;; esac
test -d "$data_directory" && test ! -L "$data_directory"
available_bytes=$(df -B1 --output=avail "$data_directory" 2>/dev/null | awk 'NR > 1 && $1 ~ /^[0-9]+$/ { value=$1 } END { print value }')
case "$available_bytes" in ''|*[!0-9]*) exit 1;; esac
margin=$((source_database_size / 4 + 67108864))
required=$((source_database_size + margin))
test "$available_bytes" -ge "$required"
"""


_RESTORE_SNAPSHOT = (
    r'''managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4
snapshot_token=${12}
printf '%s\n' "$snapshot_token" | grep -Eq '^[0-9a-f]{32}$' || exit 2
snapshot_file=$(mktemp "$lock_root/.restore-snapshot-$snapshot_token.XXXXXXXX") || exit 10
cleanup_restore_snapshot() { test -z "${snapshot_file:-}" || rm -f -- "$snapshot_file" || :; release_lifecycle_lock; }
trap cleanup_restore_snapshot EXIT
(
'''
    + _SNAPSHOT_BODY
    + r'''
) > "$snapshot_file" || exit 10
'''
)


_RESTORE_BODY = r'''set -eu
umask 077
safe_release_id() { printf '%s\n' "$1" | grep -Eq '^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$'; }
safe_backup_id() { printf '%s\n' "$1" | grep -Eq '^backup-[0-9a-f]{32}$'; }
safe_token() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{32}$'; }
safe_identifier() { printf '%s\n' "$1" | grep -Eq '^[a-z_][a-z0-9_]{0,62}$'; }
safe_base64() { case "$1" in *[!A-Za-z0-9+/=]*) return 1;; esac; }
safe_host() { case "$1" in 127.0.0.1|::1) return 0;; *) return 1;; esac; }
safe_port() { case "$1" in ''|*[!0-9]*) return 1;; esac; test "$1" -ge 1 && test "$1" -le 65535; }
managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; application_port=$5; public_hostname=$6; requested_backup_id=$7; dump_path=$8; dump_size=$9; current_release_id=${10}; intended_release_id=${11}; token=${12}; backup_command=${13}; database_host=${14}; database_port=${15}; database_role=${16}; database_name=${17}; backup_retention=${18}; distribution_port=${19}; caddy_config=${20}; readiness_timeout=${22}; connection_timeout=${23}; intended_path=${24}; migration_versions_b64=${25}; current_path=${26}; lifecycle_fingerprint=${27}; source_database_size=${28}
for path in "$managed_root" "$release_root" "$deployment_root" "$backup_root" "$dump_path" "$backup_command" "$caddy_config" "$intended_path" "$current_path"; do safe_path "$path" || exit 2; done
safe_backup_id "$requested_backup_id" && safe_release_id "$current_release_id" && safe_release_id "$intended_release_id" && safe_token "$token" && safe_identifier "$database_role" && safe_identifier "$database_name" && safe_host "$database_host" && safe_port "$database_port" && safe_port "$application_port" && safe_port "$distribution_port" && safe_base64 "$migration_versions_b64" || exit 2
printf '%s\n' "$lifecycle_fingerprint" | grep -Eq '^[0-9a-f]{64}$' || exit 2
case "$dump_size" in ''|*[!0-9]*) exit 2;; esac; test "$dump_size" -gt 0 || exit 2
case "$source_database_size" in ''|*[!0-9]*) exit 2;; esac; test "$source_database_size" -gt 0 && test "$source_database_size" -le 2305843009213693951 || exit 2
case "$backup_retention" in ''|*[!0-9]*) exit 2;; esac; test "$backup_retention" -gt 0 || exit 2
safe_dir "$managed_root" && safe_dir "$release_root" && safe_dir "$deployment_root" && safe_dir "$backup_root" || exit 10
case "$dump_path" in "$backup_root"/*) ;; *) exit 10;; esac
case "$intended_path" in "$release_root"/*) ;; *) exit 10;; esac
case "$current_path" in "$release_root"/*) ;; *) exit 10;; esac
temporary_database="taskman_restore_$token"; recovery_database="taskman_recovery_$token"; recovery_id="recovery-$token"
changed=false; changed_stages=; pre_restore_backup_json=null; verification_json=null; created_temporary=false; stopped=false; swap_state=before-first-rename; selection_temporary=; restore_record_temporary=; restore_recorded=false
mark_changed() { changed=true; case ",$changed_stages," in *,"$1",*) ;; *) changed_stages="${changed_stages}${changed_stages:+,}$1";; esac; }
stages_json() { test -n "$changed_stages" || { printf '[]'; return; }; old_ifs=$IFS; IFS=,; set -- $changed_stages; IFS=$old_ifs; first=1; printf '['; for stage in "$@"; do test "$first" = 1 || printf ','; printf '"%s"' "$stage"; first=0; done; printf ']'; }
admin_psql() { sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port "$database_port" --username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command "$1"; }
admin_query() { sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port "$database_port" --username postgres --dbname=postgres --tuples-only --no-align --set ON_ERROR_STOP=1 --command "$1"; }
recovery_commands() {
  recovery_database_state=$1; recovery_swap_state=$2
  command_prefix="sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port $database_port --username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command"
  inspection_prefix="sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port $database_port --username postgres --dbname=postgres --tuples-only --no-align --set ON_ERROR_STOP=1 --command"
  if test "$recovery_database_state" = unknown; then
    inspection_query="SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE datname IN ('$database_name', '$temporary_database', '$recovery_database')"
    printf '["systemctl status taskman.service","readlink -f %s/current","journalctl --no-pager --unit taskman.service --lines=100","%s \\"%s\\""]' "$managed_root" "$inspection_prefix" "$inspection_query"
    return
  fi
  if test "$recovery_database_state" = canonical-restored; then
    printf '["systemctl status taskman.service","readlink -f %s/current","journalctl --no-pager --unit taskman.service --lines=100"]' "$managed_root"
    return
  fi
  case "$recovery_swap_state" in
    restored-promoted|selection-failed) printf '["systemctl status taskman.service","readlink -f %s/current","journalctl --no-pager --unit taskman.service --lines=100","%s \\"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '\''%s'\'' AND pid <> pg_backend_pid()\\"","%s \\"DROP DATABASE %s\\"","%s \\"ALTER DATABASE %s RENAME TO %s\\""]' "$managed_root" "$command_prefix" "$database_name" "$command_prefix" "$database_name" "$command_prefix" "$recovery_database" "$database_name";;
    canonical-moved|inverse-failed) printf '["systemctl status taskman.service","readlink -f %s/current","journalctl --no-pager --unit taskman.service --lines=100","%s \\"ALTER DATABASE %s RENAME TO %s\\""]' "$managed_root" "$command_prefix" "$recovery_database" "$database_name";;
    *) printf '["systemctl status taskman.service","readlink -f %s/current","journalctl --no-pager --unit taskman.service --lines=100"]' "$managed_root";;
  esac
}
emit_failure() { status=$1; stage=$2; service=$3; selected=$4; database_state=$5; recorded=$6; printf '{"stage":"%s","backup_id":"%s","pre_restore_backup_id":%s,"current_release_id":"%s","intended_release_id":"%s","selected_release_id":"%s","recovery_id":"%s","service_state":"%s","database_state":"%s","swap_state":"%s","restore_recorded":%s,"changed":%s,"changed_stages":%s,"warnings":[],"recovery_commands":%s,"residue_paths":[],"verification":%s}\n' "$stage" "$requested_backup_id" "$pre_restore_backup_json" "$current_release_id" "$intended_release_id" "$selected" "$recovery_id" "$service" "$database_state" "$swap_state" "$recorded" "$changed" "$(stages_json)" "$(recovery_commands "$database_state" "$swap_state")" "$verification_json"; exit "$status"; }
drop_temporary() { test "$created_temporary" = true || return 0; admin_psql "DROP DATABASE \"$temporary_database\"" >/dev/null 2>&1 || return 1; created_temporary=false; }
restart_unchanged() { test "$stopped" = true || return 0; systemctl start taskman.service >/dev/null 2>&1 && systemctl is-active --quiet taskman.service || return 1; stopped=false; }
cleanup() { test -z "$selection_temporary" || rm -f -- "$selection_temporary" || :; test -z "$restore_record_temporary" || rm -f -- "$restore_record_temporary" || :; cleanup_restore_snapshot; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
fail_before_swap() { code=$1; stage=$2; service=$3; database_state=$4; drop_temporary || emit_failure 11 "$stage" unknown "$current_release_id" unknown "$restore_recorded"; if test "$service" = active; then restart_unchanged || emit_failure 11 "$stage" unknown "$current_release_id" "$database_state" "$restore_recorded"; fi; emit_failure "$code" "$stage" "$service" "$current_release_id" "$database_state" "$restore_recorded"; }
fail_after_swap() { code=$1; stage=$2; selected=$3; database_state=$4; service=unknown; if systemctl stop taskman.service >/dev/null 2>&1 && ! systemctl is-active --quiet taskman.service; then service=stopped; fi; emit_failure "$code" "$stage" "$service" "$selected" "$database_state" "$restore_recorded"; }
observe_selection() { test -L "$managed_root/current" || { printf unknown; return; }; observed=$(readlink -f -- "$managed_root/current") || { printf unknown; return; }; test "$observed" = "$current_path" && { printf '%s' "$current_release_id"; return; }; test "$observed" = "$intended_path" && { printf '%s' "$intended_release_id"; return; }; printf unknown; }
database_layout() { admin_query "SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE datname IN ('$database_name', '$temporary_database', '$recovery_database')" 2>/dev/null; }
target_complete() { test -d "$intended_path" && test ! -L "$intended_path" && test -x "$intended_path/bin/server" && test -x "$intended_path/bin/migrate" && test -d "$intended_path/releases" && test -d "$intended_path/lib" || return 1; links=$(find -P "$intended_path" -type l -print) || return 1; test -z "$links" && return 0; while IFS= read -r link; do link_target=$(readlink -f -- "$link") || return 1; case "$link_target" in "$intended_path"|"$intended_path"/*) ;; *) return 1;; esac; done <<EOF
$links
EOF
}
backup_result() { python3 - "$1" "$current_release_id" "$intended_release_id" "$backup_root" "$database_name" <<'PY'
import json, re, sys
raw, current, intended, root, database = sys.argv[1:]
try:
 value=json.loads(raw); fields={"schema_version","backup_id","created_at","size_bytes","source_database_size_bytes","database","current_release_id","candidate_release_id","reason","validated","dump_path"}
 if not isinstance(value,dict) or set(value)!=fields or value["schema_version"]!=1 or not re.fullmatch(r"backup-[0-9a-f]{32}",value["backup_id"]) or value["current_release_id"]!=current or value["candidate_release_id"]!=intended or value["reason"]!="pre-restore" or value["validated"] is not True or value["database"]!=database or type(value["size_bytes"]) is not int or value["size_bytes"]<=0 or type(value["source_database_size_bytes"]) is not int or value["source_database_size_bytes"]<=0 or not isinstance(value["dump_path"],str) or not value["dump_path"].startswith(root+"/"): raise ValueError
 print(value["backup_id"])
except (TypeError,ValueError,json.JSONDecodeError): raise SystemExit(1)
PY
}
write_restore_record() { directory="$deployment_root/restores"; record="$directory/$recovery_id.json"; if test -e "$directory" || test -L "$directory"; then safe_dir "$directory" || return 1; else install -d -o root -g root -m 750 -- "$directory" || return 1; fi; test ! -e "$record" && test ! -L "$record" || return 1; restore_record_temporary=$(mktemp "$directory/.$recovery_id.$token.XXXXXXXX") || return 1; printf '{"schema_version":1,"recovery_id":"%s","database":"%s","recovery_database":"%s","source_backup_id":"%s","pre_restore_backup_id":"%s","intended_release_id":"%s","state":"retained","created_at":"%s"}\n' "$recovery_id" "$database_name" "$recovery_database" "$requested_backup_id" "$pre_restore_backup_id" "$intended_release_id" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$restore_record_temporary" || return 1; chown root:root -- "$restore_record_temporary" && chmod 600 -- "$restore_record_temporary" && sync -f "$restore_record_temporary" || return 1; ln -- "$restore_record_temporary" "$record" || return 1; rm -f -- "$restore_record_temporary" || return 1; restore_record_temporary=; sync -f "$directory"; }
run_verification() { verification_status=0; verification_json=$(verify_candidate) || verification_status=$?; case "$verification_status" in 0|8|9) return "$verification_status";; *) verification_json=null; return 11;; esac; }
migration_schema_matches() {
  observed_versions=$(PGPASSFILE=/etc/taskman/pgpass psql --host "$database_host" --port "$database_port" --username "$database_role" --dbname "$temporary_database" --tuples-only --no-align --command "SELECT version FROM schema_migrations ORDER BY version" 2>/dev/null) || return 1
  observed_b64=$(printf '%s' "$observed_versions" | base64 -w 0) || return 1
  python3 - "$migration_versions_b64" "$observed_b64" <<'PY'
import base64, json, re, sys
try:
    expected = json.loads(base64.b64decode(sys.argv[1], validate=True))
    observed = base64.b64decode(sys.argv[2], validate=True).decode("ascii")
    if not isinstance(expected, list) or any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{14}", value) for value in expected):
        raise ValueError
    actual = [value for value in observed.splitlines() if value]
    if any(not re.fullmatch(r"[0-9]{14}", value) for value in actual) or actual != expected:
        raise ValueError
except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
target_complete || emit_failure 10 preflight unknown unknown unknown false
test -L "$managed_root/current" && selected_current=$(readlink -f -- "$managed_root/current") && test "$selected_current" = "$current_path" || emit_failure 10 preflight unknown unknown unknown false
test -f "$dump_path" && test ! -L "$dump_path" && test "$(stat -c %s -- "$dump_path")" = "$dump_size" || emit_failure 10 preflight unknown "$current_release_id" unchanged false
python3 - "$lifecycle_fingerprint" "$snapshot_file" <<'PY' || emit_failure 10 preflight unknown "$current_release_id" unchanged false
import hashlib, json, sys
try:
    with open(sys.argv[2], encoding="utf-8") as stream: snapshot = json.load(stream)
    if hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() != sys.argv[1]: raise ValueError
except (OSError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
pg_restore --list -- "$dump_path" >/dev/null 2>&1 || emit_failure 11 preflight unknown "$current_release_id" unchanged false
cluster_data_directory=$(admin_query 'SHOW data_directory' 2>/dev/null) || emit_failure 11 preflight unknown "$current_release_id" unchanged false
# PostgreSQL reports its own cluster directory through the peer-admin control
# connection. It is normally owned by ``postgres``, not the root-owned
# lifecycle controller, so do not apply ``safe_dir``'s root-ownership rule.
# It remains an absolute, non-symlink directory before it is handed to df.
safe_path "$cluster_data_directory" && test -d "$cluster_data_directory" && test ! -L "$cluster_data_directory" || emit_failure 11 preflight unknown "$current_release_id" unchanged false
available_blocks=$(df -Pk -- "$cluster_data_directory" | awk 'NR==2 {print $4}') || emit_failure 11 preflight unknown "$current_release_id" unchanged false
case "$available_blocks" in ''|*[!0-9]*) emit_failure 11 preflight unknown "$current_release_id" unchanged false;; esac
test "$available_blocks" -le 9007199254740991 || emit_failure 11 preflight unknown "$current_release_id" unchanged false
# The source allocation was measured by the backup transaction immediately
# before pg_dump. A 25% allocation margin plus 64 MiB covers WAL and index
# work without guessing from compressed dump size or the live canonical DB.
restore_margin=$((source_database_size / 4 + 67108864)); required_bytes=$((source_database_size + restore_margin)); available_bytes=$((available_blocks * 1024)); test "$available_bytes" -ge "$required_bytes" || emit_failure 11 preflight unknown "$current_release_id" unchanged false
backup_status=0; backup_output=$(TASKMAN_LIFECYCLE_LOCK_HELD="$lock_token" TASKMAN_LIFECYCLE_LOCK_FD=9 "$backup_command" --already-locked --lock-root "$lock_root" --database-host "$database_host" --database-port "$database_port" --database-role "$database_role" --database-name "$database_name" --backup-root "$backup_root" --deployment-root "$deployment_root" --managed-root "$managed_root" --release-root "$release_root" --retention "$backup_retention" --reason pre-restore --current-release "$current_release_id" --candidate-release "$intended_release_id") || backup_status=$?
test "$backup_status" = 0 || emit_failure 11 backup unknown "$current_release_id" unchanged false
pre_restore_backup_id=$(backup_result "$backup_output") || emit_failure 11 backup unknown "$current_release_id" unchanged false
pre_restore_backup_json="\"$pre_restore_backup_id\""; mark_changed backup
mark_changed stop; systemctl stop taskman.service && ! systemctl is-active --quiet taskman.service || fail_before_swap 11 stop unknown unchanged; stopped=true
mark_changed restore; admin_psql "CREATE DATABASE \"$temporary_database\" OWNER \"$database_role\"" >/dev/null 2>&1 || fail_before_swap 11 restore active unchanged; created_temporary=true
PGPASSFILE=/etc/taskman/pgpass pg_restore --exit-on-error --no-owner --no-privileges --host "$database_host" --port "$database_port" --username "$database_role" --dbname "$temporary_database" -- "$dump_path" >/dev/null 2>&1 || fail_before_swap 11 restore active unchanged
mark_changed validation; PGPASSFILE=/etc/taskman/pgpass psql --host "$database_host" --port "$database_port" --username "$database_role" --dbname "$temporary_database" --tuples-only --no-align --command "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'schema_migrations'" 2>/dev/null | grep -qx 1 || fail_before_swap 11 validation active unchanged
migration_schema_matches || fail_before_swap 11 validation active unchanged
admin_psql "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$database_name', '$temporary_database') AND pid <> pg_backend_pid()" >/dev/null 2>&1 || fail_before_swap 11 validation active unchanged
mark_changed swap; admin_psql "ALTER DATABASE \"$database_name\" RENAME TO \"$recovery_database\"" >/dev/null 2>&1 || fail_before_swap 11 swap active unchanged; swap_state=canonical-moved
test "$(database_layout)" = "$recovery_database,$temporary_database" || fail_after_swap 11 swap "$current_release_id" unknown
admin_psql "ALTER DATABASE \"$temporary_database\" RENAME TO \"$database_name\"" >/dev/null 2>&1 || { if admin_psql "ALTER DATABASE \"$recovery_database\" RENAME TO \"$database_name\"" >/dev/null 2>&1 && test "$(database_layout)" = "$database_name,$temporary_database"; then fail_before_swap 11 swap active canonical-restored; fi; swap_state=inverse-failed; fail_after_swap 11 swap "$(observe_selection)" unknown; }; created_temporary=false; swap_state=restored-promoted
test "$(database_layout)" = "$database_name,$recovery_database" || fail_after_swap 11 swap "$(observe_selection)" unknown
mark_changed records; write_restore_record || fail_after_swap 11 records "$(observe_selection)" restored-promoted; restore_recorded=true
selection_temporary="$managed_root/.restore-current-$token"; ln -s -- "$intended_path" "$selection_temporary" || { swap_state=selection-failed; fail_after_swap 11 start "$(observe_selection)" restored-promoted; }; mv -T -- "$selection_temporary" "$managed_root/current" || { swap_state=selection-failed; fail_after_swap 11 start "$(observe_selection)" restored-promoted; }; selection_temporary=
test "$(observe_selection)" = "$intended_release_id" || { swap_state=selection-failed; fail_after_swap 11 start "$(observe_selection)" restored-promoted; }
mark_changed start; systemctl start taskman.service >/dev/null 2>&1 && systemctl is-active --quiet taskman.service || fail_after_swap 11 start "$(observe_selection)" restored-promoted; stopped=false
candidate="$intended_path"
candidate_release_id="$intended_release_id"
mark_changed verification; verification_status=0; run_verification || verification_status=$?; test "$verification_status" = 0 || fail_after_swap 11 verification "$(observe_selection)" restored-promoted
printf '{"stage":"restored","backup_id":"%s","pre_restore_backup_id":"%s","current_release_id":"%s","intended_release_id":"%s","selected_release_id":"%s","recovery_id":"%s","service_state":"active","database_state":"restored-promoted","swap_state":"restored-promoted","restore_recorded":true,"changed":true,"changed_stages":%s,"warnings":[],"recovery_commands":%s,"residue_paths":[],"verification":%s}\n' "$requested_backup_id" "$pre_restore_backup_id" "$current_release_id" "$intended_release_id" "$intended_release_id" "$recovery_id" "$(stages_json)" "$(recovery_commands restored-promoted "$swap_state")" "$verification_json"
'''


RESTORE_TRANSACTION = (
    "set -eu\n"
    "lock_root=/var/lock/taskman; operation=restore; timeout_ms=${21}; owner_uid=0; lock_mode=exclusive\n"
    + REMOTE_LOCK_FRAMING
    + _LOCKED_VERIFICATION_BODY
    + "\n"
    + _RESTORE_SNAPSHOT
    + _RESTORE_BODY
)


@dataclass(frozen=True)
class RestorePlan:
    backup_id: str
    dump_path: PurePosixPath
    dump_size_bytes: int
    source_database_size_bytes: int
    current_release_id: str
    intended_release_id: str


def assess_restore(
    records: LifecycleRecords,
    current_release_id: str,
    backup_id: str,
    *,
    backup_root: PurePosixPath,
) -> RestorePlan:
    """Resolve an exact recorded backup into a restore plan, never a path."""

    if not isinstance(records, LifecycleRecords):
        raise TypeError("restore assessment requires lifecycle records")
    try:
        current = validate_release_id(current_release_id)
    except ValueError:
        raise _safety("current release identifier is invalid") from None
    if not isinstance(backup_id, str) or _BACKUP_ID_RE.fullmatch(backup_id) is None:
        raise _safety("restore requires an exact backup identifier")
    matches = [record for record in records.backups if record.backup_id == backup_id]
    if len(matches) != 1:
        raise _safety("backup identifier is not authoritative")
    backup = matches[0]
    if not backup.validated or backup.size_bytes <= 0 or backup.source_database_size_bytes <= 0 or backup.database == "":
        raise _safety("backup record is not a validated restore authority")
    if not backup.dump_path.is_relative_to(backup_root):
        raise _safety("backup record escapes the managed backup root")
    intended = backup.current_release_id
    if intended is None or intended == current:
        raise _safety("backup has no distinct intended release for guarded restore")
    if intended not in {record.release_id for record in records.releases}:
        raise _safety("backup intended release is not installed")
    return RestorePlan(backup.backup_id, backup.dump_path, backup.size_bytes, backup.source_database_size_bytes, current, intended)


def restore(
    remote: Remote,
    config: EnvironmentConfig,
    backup_id: str,
    *,
    lifecycle_store: RemoteLifecycleStore | None = None,
    lock_timeout_seconds: float = 5,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Confirm and execute one destructive restore through one host lock."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("restore requires a validated environment configuration")
    if not isinstance(lock_timeout_seconds, (int, float)) or isinstance(lock_timeout_seconds, bool) or lock_timeout_seconds < 0:
        raise ValueError("restore lock timeout must be non-negative")
    target_backup = backup_id if isinstance(backup_id, str) else "unknown"
    store = lifecycle_store or RemoteLifecycleStore(remote, config.deployment_root, config.managed_root, config.release_root, config.backup_root, application_port=config.application_port, distribution_port=config.distribution_port, database_port=config.database_port)
    if store.remote is not remote:
        raise ValueError("restore lifecycle store does not match remote")
    try:
        validate_operational_preflight(remote, config)
        records, snapshot = store.read(operation="restore", lock_timeout_seconds=lock_timeout_seconds)
        current = records.current_release_id
        if current is None:
            raise _safety("no current release is recorded")
        plan = assess_restore(records, current, backup_id, backup_root=config.backup_root)
        if snapshot.get("dump_states", {}).get(plan.dump_path.as_posix()) != "present":
            raise _safety("backup dump is stale or missing")
        current_path = _intended_release_path(records, snapshot, store, plan.current_release_id)
        intended_path = _intended_release_path(records, snapshot, store, plan.intended_release_id)
        expected_migration_versions = _intended_migration_versions(snapshot, plan.intended_release_id)
        state_fingerprint = _snapshot_fingerprint(snapshot)
        _validate_restore_dump(
            remote,
            config,
            store,
            plan.dump_path,
            plan.dump_size_bytes,
            plan.source_database_size_bytes,
        )
    except OpsError as error:
        return _failure_result(config, error, backup_id=target_backup)

    plan_mapping = {
        "environment": config.name,
        "backup_id": plan.backup_id,
        "current_release_id": plan.current_release_id,
        "intended_release_id": plan.intended_release_id,
        "dump_path": plan.dump_path.as_posix(),
        "typed_confirmation": f"{config.name} {plan.backup_id}",
        "planned_pre_restore_backup": True,
        "services_affected": ("taskman.service",),
    }
    if dry_run:
        return WorkflowResult("restore", config.name or "", False, "planned", facts=plan_mapping, next_action="review the exact restore plan and typed confirmation before a destructive run")
    confirmation = confirm or _confirm
    if not confirmation(plan_mapping):
        return WorkflowResult("restore", config.name or "", False, "confirmation-cancelled", facts=plan_mapping, next_action="review the exact backup and confirm a later restore when ready")
    try:
        evidence = run_locked_restore(remote, config, store, backup_id=plan.backup_id, dump_path=plan.dump_path, dump_size_bytes=plan.dump_size_bytes, source_database_size_bytes=plan.source_database_size_bytes, current_release_id=plan.current_release_id, intended_release_id=plan.intended_release_id, current_release_path=current_path, intended_release_path=intended_path, expected_migration_versions=expected_migration_versions, state_fingerprint=state_fingerprint, lock_timeout_seconds=lock_timeout_seconds)
    except OpsError as error:
        return _failure_result(config, error, backup_id=plan.backup_id, current_release_id=plan.current_release_id, intended_release_id=plan.intended_release_id)
    return WorkflowResult("restore", config.name or "", bool(evidence["changed"]), str(evidence["stage"]), facts=evidence, next_action="copy retained recovery evidence off-host and complete the documented browser, email, and API checks")


def _validate_restore_dump(
    remote: Remote,
    config: EnvironmentConfig,
    store: RemoteLifecycleStore,
    dump_path: PurePosixPath,
    dump_size_bytes: int,
    source_database_size_bytes: int,
) -> None:
    """Freshly validate the exact recorded dump before planning or confirmation."""

    result = remote.run(
        (
            "sh",
            "-ceu",
            _VALIDATE_RESTORE_DUMP,
            "taskman-validate-restore-dump",
            dump_path.as_posix(),
            store.backup_root.as_posix(),
            str(dump_size_bytes),
            str(source_database_size_bytes),
            str(config.database_port),
        ),
        sudo=True,
        stdin=None,
        sensitive=False,
    )
    if not result.succeeded:
        raise OpsError(
            ExitStatus.RESTORE,
            "preflight",
            "backup dump failed fresh format, path, or size validation",
            False,
            "inspect the exact recorded dump and create a freshly validated backup before retrying",
        )


def run_locked_restore(
    remote: Remote,
    config: EnvironmentConfig,
    store: RemoteLifecycleStore,
    *,
    backup_id: str,
    dump_path: PurePosixPath,
    dump_size_bytes: int,
    source_database_size_bytes: int,
    current_release_id: str,
    intended_release_id: str,
    current_release_path: PurePosixPath | None = None,
    intended_release_path: PurePosixPath | None = None,
    lock_timeout_seconds: float,
    expected_migration_versions: tuple[str, ...] = (),
    state_fingerprint: str | None = None,
    operation_token: str | None = None,
) -> dict[str, object]:
    """Execute all mutable restore boundaries in one fixed remote program."""

    if not isinstance(store, RemoteLifecycleStore) or store.remote is not remote:
        raise ValueError("restore lifecycle store does not match remote")
    if not isinstance(lock_timeout_seconds, (int, float)) or isinstance(lock_timeout_seconds, bool) or lock_timeout_seconds < 0:
        raise ValueError("restore lock timeout must be non-negative")
    if not isinstance(backup_id, str) or _BACKUP_ID_RE.fullmatch(backup_id) is None:
        raise _safety("restore requires an exact backup identifier")
    if not isinstance(dump_path, PurePosixPath) or not dump_path.is_absolute() or not dump_path.is_relative_to(store.backup_root):
        raise _safety("restore dump path is outside the managed backup root")
    if type(dump_size_bytes) is not int or dump_size_bytes <= 0:
        raise _safety("restore dump size is invalid")
    if type(source_database_size_bytes) is not int or source_database_size_bytes <= 0 or source_database_size_bytes > 2305843009213693951:
        raise _safety("restore source database size authority is invalid")
    current = validate_release_id(current_release_id)
    intended = validate_release_id(intended_release_id)
    if current == intended:
        raise _safety("restore intended release must differ from the current release")
    current_path = current_release_path or store.release_root / current
    if not isinstance(current_path, PurePosixPath) or not current_path.is_absolute() or not current_path.is_relative_to(store.release_root):
        raise _safety("restore current release path is outside the managed release root")
    intended_path = intended_release_path or store.release_root / intended
    if not isinstance(intended_path, PurePosixPath) or not intended_path.is_absolute() or not intended_path.is_relative_to(store.release_root):
        raise _safety("restore intended release path is outside the managed release root")
    if (
        not isinstance(expected_migration_versions, tuple)
        or any(not isinstance(version, str) or re.fullmatch(r"[0-9]{14}", version) is None for version in expected_migration_versions)
        or tuple(sorted(expected_migration_versions)) != expected_migration_versions
        or len(set(expected_migration_versions)) != len(expected_migration_versions)
    ):
        raise _safety("restore intended migration authority is invalid")
    if not isinstance(state_fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", state_fingerprint) is None:
        raise _safety("restore lifecycle authority fingerprint is invalid")
    token = uuid4().hex if operation_token is None else operation_token
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("restore operation token is invalid")
    result = remote.run(
        (
            "sh", "-ceu", RESTORE_TRANSACTION, "taskman-restore-transaction",
            store.managed_root.as_posix(), store.release_root.as_posix(), store.deployment_root.as_posix(), store.backup_root.as_posix(), str(config.application_port), config.public_hostname,
            backup_id, dump_path.as_posix(), str(dump_size_bytes), current, intended, token, BACKUP_COMMAND.as_posix(), config.database_host, str(config.database_port), config.database_role, config.database_name, str(config.backup_retention), str(config.distribution_port), store.caddy_config.as_posix(), str(int(lock_timeout_seconds * 1000)), str(config.readiness_timeout), str(config.connection_timeout), intended_path.as_posix(), base64.b64encode(json.dumps(expected_migration_versions, separators=(",", ":")).encode("ascii")).decode("ascii"), current_path.as_posix(), state_fingerprint, str(source_database_size_bytes),
        ),
        sudo=True,
        stdin=None,
        sensitive=False,
    )
    if result.succeeded:
        return _restore_evidence(result.stdout, backup_id=backup_id, current_release_id=current, intended_release_id=intended, expected_recovery_id=f"recovery-{token}", managed_root=store.managed_root, database_name=config.database_name, database_port=config.database_port, success=True)
    if result.returncode == ExitStatus.LOCKED:
        raise_remote_lifecycle_failure(result)
    raise _transaction_error(result.returncode, result.stdout, backup_id=backup_id, current_release_id=current, intended_release_id=intended, expected_recovery_id=f"recovery-{token}", managed_root=store.managed_root, database_name=config.database_name, database_port=config.database_port)


def _intended_release_path(records: LifecycleRecords, snapshot: Mapping[str, object], store: RemoteLifecycleStore, intended_release_id: str) -> PurePosixPath:
    adoption_paths = {record.release_id: record.release_path for record in records.adoptions}
    if intended_release_id in adoption_paths:
        return adoption_paths[intended_release_id]
    manifests = snapshot.get("manifests")
    if not isinstance(manifests, Mapping) or intended_release_id not in manifests:
        raise _safety("intended release has no immutable manifest authority")
    return store.release_root / intended_release_id


def _intended_migration_versions(snapshot: Mapping[str, object], intended_release_id: str) -> tuple[str, ...]:
    """Return the exact database versions represented by the intended artifact."""

    manifests = snapshot.get("manifests")
    if not isinstance(manifests, Mapping) or intended_release_id not in manifests:
        raise _safety("intended release has no immutable migration authority")
    try:
        manifest = ArtifactManifest.from_mapping(manifests[intended_release_id])
    except (TypeError, ValueError):
        raise _safety("intended release manifest is invalid") from None
    if manifest.release_id != intended_release_id:
        raise _safety("intended release manifest identity is contradictory")
    return tuple(fingerprint.filename.split("_", 1)[0] for fingerprint in manifest.migrations)


def _snapshot_fingerprint(snapshot: Mapping[str, object]) -> str:
    """Bind the confirmed restore to exact lifecycle, backup, and manifest authority."""

    if not isinstance(snapshot, Mapping):
        raise _safety("restore lifecycle snapshot is invalid")
    try:
        material = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise _safety("restore lifecycle snapshot is invalid") from None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _restore_evidence(
    output: str,
    *,
    backup_id: str,
    current_release_id: str,
    intended_release_id: str,
    expected_recovery_id: str,
    managed_root: PurePosixPath,
    database_name: str,
    database_port: int,
    success: bool,
) -> dict[str, object]:
    try:
        value = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        raise _safety("restore transaction returned invalid state evidence") from None
    fields = {"stage", "backup_id", "pre_restore_backup_id", "current_release_id", "intended_release_id", "selected_release_id", "recovery_id", "service_state", "database_state", "swap_state", "restore_recorded", "changed", "changed_stages", "warnings", "recovery_commands", "residue_paths", "verification"}
    if not isinstance(value, dict) or set(value) != fields or value.get("backup_id") != backup_id or value.get("current_release_id") != current_release_id or value.get("intended_release_id") != intended_release_id or value.get("recovery_id") != expected_recovery_id:
        raise _safety("restore transaction returned contradictory state evidence")
    pre_restore_backup = value["pre_restore_backup_id"]
    swap_state = value["swap_state"]
    database_state = value["database_state"]
    if not isinstance(swap_state, str) or not isinstance(database_state, str):
        raise _safety("restore transaction returned invalid state evidence")
    expected_commands = _recovery_commands(
        managed_root,
        database_port,
        database_name,
        expected_recovery_id,
        swap_state,
        database_state,
    )
    if (success and (not isinstance(pre_restore_backup, str) or _BACKUP_ID_RE.fullmatch(pre_restore_backup) is None)) or (not success and pre_restore_backup is not None and (not isinstance(pre_restore_backup, str) or _BACKUP_ID_RE.fullmatch(pre_restore_backup) is None)) or not isinstance(value["changed_stages"], list) or any(not isinstance(item, str) for item in value["changed_stages"]) or value["warnings"] != [] or value["residue_paths"] != [] or value["recovery_commands"] != expected_commands:
        raise _safety("restore transaction returned invalid state evidence")
    if success:
        try:
            verification = VerificationReport.from_mapping(value["verification"])
        except (TypeError, ValueError):
            raise _safety("restore transaction returned invalid verification evidence") from None
        if not (value["stage"] == "restored" and value["selected_release_id"] == intended_release_id and value["service_state"] == "active" and value["database_state"] == "restored-promoted" and swap_state == "restored-promoted" and value["restore_recorded"] is True and value["changed"] is True and tuple(value["changed_stages"]) == _SUCCESS_STAGES and verification.successful and verification.release_id == intended_release_id and verification.expected_release_id == intended_release_id):
            raise _safety("restore transaction returned contradictory success evidence")
    else:
        _validate_failure_evidence(value, current_release_id=current_release_id, intended_release_id=intended_release_id)
    return value


def _validate_failure_evidence(
    evidence: Mapping[str, object],
    *,
    current_release_id: str,
    intended_release_id: str,
) -> None:
    """Accept only observed rollback/swap boundaries, never inferred selection state."""

    stage = evidence["stage"]
    changed = evidence["changed"]
    changed_stages = tuple(evidence["changed_stages"])
    pre_restore_backup = evidence["pre_restore_backup_id"]
    swap_state = evidence["swap_state"]
    recorded = evidence["restore_recorded"]
    if not isinstance(stage, str) or stage not in _FAILURE_STAGES or not isinstance(swap_state, str) or type(changed) is not bool or type(recorded) is not bool or evidence["verification"] is not None:
        raise _safety("restore transaction returned invalid failure evidence")
    if stage == "preflight":
        valid = (
            changed is False
            and changed_stages == ()
            and pre_restore_backup is None
            and evidence["selected_release_id"] == "unknown"
            and evidence["service_state"] == "unknown"
            and evidence["database_state"] == "unknown"
            and swap_state == "before-first-rename"
            and recorded is False
        )
    elif stage == "backup":
        valid = (
            changed is False
            and changed_stages == ()
            and pre_restore_backup is None
            and evidence["selected_release_id"] == current_release_id
            and evidence["service_state"] == "unknown"
            and evidence["database_state"] == "unchanged"
            and swap_state == "before-first-rename"
            and recorded is False
        )
    elif stage in {"stop", "restore", "validation"}:
        prefixes = {
            "stop": ("backup", "stop"),
            "restore": ("backup", "stop", "restore"),
            "validation": ("backup", "stop", "restore", "validation"),
        }
        valid = (
            changed is True
            and changed_stages == prefixes[stage]
            and isinstance(pre_restore_backup, str)
            and evidence["selected_release_id"] == current_release_id
            and evidence["service_state"] in ({"unknown"} if stage == "stop" else {"active", "unknown"})
            and evidence["database_state"] == "unchanged"
            and swap_state == "before-first-rename"
            and recorded is False
        )
    elif stage == "swap":
        valid = (
            changed is True
            and changed_stages == ("backup", "stop", "restore", "validation", "swap")
            and isinstance(pre_restore_backup, str)
            and (
                (
                    swap_state == "canonical-moved"
                    and evidence["selected_release_id"] == current_release_id
                    and (
                        (evidence["service_state"] == "active" and evidence["database_state"] == "canonical-restored")
                        or (evidence["service_state"] in {"stopped", "unknown"} and evidence["database_state"] == "unknown")
                    )
                )
                or (
                    swap_state in {"inverse-failed", "restored-promoted"}
                    and evidence["selected_release_id"] == current_release_id
                    and evidence["service_state"] in {"stopped", "unknown"}
                    and evidence["database_state"] == "unknown"
                )
            )
            and recorded is False
        )
    else:
        base = ("backup", "stop", "restore", "validation", "swap")
        if stage == "records":
            valid = (
                changed is True
                and changed_stages == (*base, "records")
                and isinstance(pre_restore_backup, str)
                and swap_state == "restored-promoted"
                and evidence["selected_release_id"] == current_release_id
                and evidence["service_state"] in {"stopped", "unknown"}
                and evidence["database_state"] == "restored-promoted"
                and recorded is False
            )
        elif stage == "start":
            valid = (
                changed is True
                and isinstance(pre_restore_backup, str)
                and evidence["service_state"] in {"stopped", "unknown"}
                and evidence["database_state"] == "restored-promoted"
                and recorded is True
                and ((swap_state == "selection-failed" and changed_stages == (*base, "records") and evidence["selected_release_id"] == current_release_id) or (swap_state == "restored-promoted" and changed_stages == (*base, "records", "start") and evidence["selected_release_id"] == intended_release_id))
            )
        else:
            valid = (
                changed is True
                and changed_stages == (*base, "records", "start", "verification")
                and isinstance(pre_restore_backup, str)
                and swap_state == "restored-promoted"
                and evidence["selected_release_id"] == intended_release_id
                and evidence["service_state"] in {"stopped", "unknown"}
                and evidence["database_state"] == "restored-promoted"
                and recorded is True
            )
    if not valid:
        raise _safety("restore transaction returned contradictory failure evidence")


def _recovery_commands(
    managed_root: PurePosixPath,
    database_port: int,
    database_name: str,
    recovery_id: str,
    swap_state: str,
    database_state: str,
) -> list[str]:
    """Return safe, separate peer-admin commands for an observed boundary."""

    commands = [
        "systemctl status taskman.service",
        f"readlink -f {managed_root}/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
    ]
    recovery_database = f"taskman_{recovery_id.replace('-', '_')}"
    temporary_database = f"taskman_restore_{recovery_id.removeprefix('recovery-')}"
    prefix = f"sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port {database_port} --username postgres --dbname=postgres --set ON_ERROR_STOP=1 --command"
    inspection_prefix = f"sudo -u postgres -- psql --no-psqlrc --host /var/run/postgresql --port {database_port} --username postgres --dbname=postgres --tuples-only --no-align --set ON_ERROR_STOP=1 --command"
    if database_state == "unknown":
        return [
            *commands,
            f"{inspection_prefix} \"SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE datname IN ('{database_name}', '{temporary_database}', '{recovery_database}')\"",
        ]
    if database_state == "canonical-restored":
        return commands
    if swap_state in {"restored-promoted", "selection-failed"}:
        return [
            *commands,
            f'{prefix} "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = \'{database_name}\' AND pid <> pg_backend_pid()"',
            f'{prefix} "DROP DATABASE {database_name}"',
            f'{prefix} "ALTER DATABASE {recovery_database} RENAME TO {database_name}"',
        ]
    elif swap_state in {"canonical-moved", "inverse-failed"}:
        return [*commands, f'{prefix} "ALTER DATABASE {recovery_database} RENAME TO {database_name}"']
    else:
        return commands


def _transaction_error(
    returncode: int,
    output: str,
    *,
    backup_id: str,
    current_release_id: str,
    intended_release_id: str,
    expected_recovery_id: str,
    managed_root: PurePosixPath,
    database_name: str,
    database_port: int,
) -> OpsError:
    try:
        evidence = _restore_evidence(output, backup_id=backup_id, current_release_id=current_release_id, intended_release_id=intended_release_id, expected_recovery_id=expected_recovery_id, managed_root=managed_root, database_name=database_name, database_port=database_port, success=False)
    except OpsError:
        raise _safety("restore transaction failed without safe state evidence") from None
    stage = evidence["stage"]
    if not isinstance(stage, str) or stage not in _FAILURE_STAGES:
        raise _safety("restore transaction returned invalid failure evidence")
    error = OpsError(ExitStatus.RESTORE, stage, "database restore or restored-database validation failed", bool(evidence["changed"]), "use the exact recorded recovery database and the documented manual recovery commands")
    for key, value in evidence.items():
        setattr(error, key, value)
    return error


def _failure_result(config: EnvironmentConfig, error: OpsError, *, backup_id: str, current_release_id: str | None = None, intended_release_id: str | None = None) -> WorkflowResult:
    return WorkflowResult(
        "restore", config.name or "", error.changed, "safety-refused" if error.status is ExitStatus.SAFETY else f"{error.stage}-failed",
        facts={
            "backup_id": backup_id,
            "pre_restore_backup_id": getattr(error, "pre_restore_backup_id", None),
            "current_release_id": getattr(error, "current_release_id", current_release_id or "unknown"),
            "intended_release_id": getattr(error, "intended_release_id", intended_release_id or "unknown"),
            "selected_release_id": getattr(error, "selected_release_id", "unknown"),
            "recovery_id": getattr(error, "recovery_id", None),
            "service_state": getattr(error, "service_state", "unknown"),
            "database_state": getattr(error, "database_state", "unknown"),
            "swap_state": getattr(error, "swap_state", "before-first-rename"),
            "restore_recorded": getattr(error, "restore_recorded", False),
            "changed_stages": tuple(getattr(error, "changed_stages", ())),
            "recovery_commands": tuple(getattr(error, "recovery_commands", (f"systemctl status taskman.service", f"readlink -f {config.managed_root}/current", "journalctl --no-pager --unit taskman.service --lines=100"))),
            "warnings": tuple(getattr(error, "warnings", ())),
            "residue_paths": tuple(getattr(error, "residue_paths", ())),
            "verification": getattr(error, "verification", None),
        },
        next_action=error.next_action,
        exit_status=error.status,
    )


def _confirm(plan: Mapping[str, object]) -> bool:
    typed = plan["typed_confirmation"]
    return input(f"Restore Taskman backup? Type '{typed}' to continue: ").strip() == typed


def _safety(message: str) -> OpsError:
    return OpsError(ExitStatus.SAFETY, "restore", message, False, "inspect the managed backup and lifecycle records before retrying")


__all__ = ["RESTORE_TRANSACTION", "RestorePlan", "assess_restore", "restore", "run_locked_restore"]
