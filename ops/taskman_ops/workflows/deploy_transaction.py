"""One lock-held remote transaction for an immutable release deployment.

The controller uploads a unique private archive before invoking this program.
Every authoritative host read and every mutable deployment operation afterwards
executes below one exclusive lifecycle lock.  The two supplied child programs
are repository-owned, base64 encoded shell sources: staging and activation
run in child shells so their traps cannot release the parent's lock.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import PurePosixPath
from uuid import uuid4

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..manifests import VerifiedArtifact
from ..remote import Remote
from ..releases.identifiers import validate_release_id
from ..releases.staging import _PREPARE_UPLOAD, _STAGE_BODY
from ..releases.activation import _ACTIVATION_BODY, _GENESIS_ACTIVATION_BODY
from ..releases.remote_locking import REMOTE_LOCK_FRAMING
from ..releases.remote_adoption import _ADOPTION_BODY
from ..releases.remote_snapshot import _SNAPSHOT_BODY
from ..releases.records import ManualAdoptionCandidate, RemoteLifecycleStore
from ..services.backups import BACKUP_COMMAND
from ..verification import VerificationReport, _LOCKED_VERIFICATION_BODY


_BODY = r'''set -eu
umask 077
safe_release_id() { printf '%s\n' "$1" | grep -Eq '^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$'; }
safe_backup_id() { printf '%s\n' "$1" | grep -Eq '^backup-[0-9a-f]{32}$'; }
safe_sha256() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'; }
safe_token() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{32}$'; }
safe_base64() { case "$1" in *[!A-Za-z0-9+/=]*) return 1;; *) return 0;; esac; }
safe_policy() { case "$1" in no-change|backward-compatible|restore-required) return 0;; *) return 1;; esac; }
failure() {
  status=$1; stage=$2; service=$3; selected=$4; database=$5; record=$6
  refresh_residue
  stages=$(changed_stages_json)
  printf '{"stage":"%s","previous_release_id":%s,"candidate_release_id":"%s","selected_release_id":"%s","backup_id":%s,"activation_id":%s,"service_state":"%s","database_state":"%s","activation_recorded":%s,"changed":%s,"changed_stages":%s,"warnings":%s,"recovery_commands":%s,"residue_paths":%s,"verification":%s}\n' "$stage" "$previous_json" "$candidate_release_id" "$selected" "$backup_json" "$activation_json" "$service" "$database" "$record" "$changed" "$stages" "$warnings_json" "$recovery_commands_json" "$residue_json" "$verification_json"
  exit "$status"
}
mark_changed() {
  changed=true
  case ",$changed_stages," in *,"$1",*) ;; *) changed_stages="${changed_stages}${changed_stages:+,}$1";; esac
}
changed_stages_json() {
  test -n "$changed_stages" || { printf '[]'; return 0; }
  previous_ifs=$IFS; IFS=,
  set -- $changed_stages
  IFS=$previous_ifs
  first=1; printf '['
  for changed_stage in "$@"; do
    test "$first" = 1 || printf ','
    printf '"%s"' "$changed_stage"; first=0
  done
  printf ']'
}
residue_paths() {
  python3 - "$upload" "$release_root" "$deployment_root" <<'PY'
import json, os, re, sys
upload, release_root, deployment_root = sys.argv[1:]
safe = re.compile(r"^/[A-Za-z0-9_./+-]+$")
paths = []
def add(path):
    if safe.fullmatch(path) and path != upload:
        paths.append(path)
def inventory(directory, predicate):
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return
    for name in names:
        if predicate(name):
            add(os.path.join(directory, name))
inventory(os.path.join(deployment_root, "uploads"), lambda _name: True)
inventory(release_root, lambda name: name.startswith(".stage-"))
inventory(os.path.join(deployment_root, "provisionals"), lambda _name: True)
for record_directory in ("manifests", "releases", "activations"):
    inventory(os.path.join(deployment_root, record_directory), lambda name: name.startswith("."))
print(json.dumps(sorted(set(paths)), separators=(",", ":")))
PY
}
recovery_commands() {
  python3 - "$managed_root" "$1" <<'PY'
import json, re, sys
managed_root, encoded = sys.argv[1:]
safe = re.compile(r"^/[A-Za-z0-9_./+-]+$")
try:
    paths = json.loads(encoded)
    if not isinstance(paths, list) or not all(isinstance(path, str) and safe.fullmatch(path) for path in paths):
        raise ValueError
    commands = [
        "systemctl status taskman.service",
        "readlink -f " + managed_root + "/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
    ]
    commands.extend("stat -Lc '%U:%G %a %F %n' -- " + path for path in paths)
    print(json.dumps(commands, separators=(",", ":")))
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
refresh_residue() {
  residue_json=$(residue_paths) || residue_json='[]'
  recovery_commands_json=$(recovery_commands "$residue_json") ||
    recovery_commands_json='["systemctl status taskman.service","journalctl --no-pager --unit taskman.service --lines=100"]'
}
emit_success() {
  result_stage=$1; result_previous=$2; result_backup=$3; result_activation=$4; result_database=$5; result_record=$6
  refresh_residue
  printf '{"stage":"%s","previous_release_id":%s,"candidate_release_id":"%s","selected_release_id":"%s","backup_id":%s,"activation_id":%s,"service_state":"active","database_state":"%s","activation_recorded":%s,"changed":%s,"changed_stages":%s,"warnings":%s,"recovery_commands":%s,"residue_paths":%s,"verification":%s}\n' "$result_stage" "$previous_json" "$candidate_release_id" "$candidate_release_id" "$result_backup" "$result_activation" "$result_database" "$result_record" "$changed" "$(changed_stages_json)" "$warnings_json" "$recovery_commands_json" "$residue_json" "$verification_json"
}
run_verification() {
  verification_status=0
  verification_json=$(verify_candidate) || verification_status=$?
  case "$verification_status" in
    0|8|9) return "$verification_status" ;;
    *) verification_json=null; return 10 ;;
  esac
}
ensure_record_dir() {
  if test -e "$1" || test -L "$1"; then safe_dir "$1" || return 1
  else install -d -o 0 -g 0 -m 750 -- "$1" || return 1; safe_dir "$1" || return 1
  fi
}
publish() {
  directory=$1; name=$2; content=$3
  ensure_record_dir "$directory" || return 1
  case "$directory" in
    "$deployment_root/manifests"|"$deployment_root/releases"|"$deployment_root/activations"|"$deployment_root/provisionals") ;;
    *) return 1 ;;
  esac
  case "$name" in ""|*[!A-Za-z0-9_.+-]*) return 1;; esac
  target="$directory/$name"
  temporary=$(mktemp --suffix=.tmp "$directory/.$name.$token.XXXXXXXX") || return 1
  safe_path "$temporary" || return 1
  case "$temporary" in "$directory/.$name.$token."????????.tmp) ;; *) return 1;; esac
  publication_temporary=$temporary
  printf '%s\n' "$content" > "$temporary" || { cleanup_publication_temporary || :; return 1; }
  chown root:root -- "$temporary" &&
    chmod 600 -- "$temporary" &&
    sync -f "$temporary" ||
    { cleanup_publication_temporary || :; return 1; }
  if test -e "$target" || test -L "$target"; then
    cmp -s "$temporary" "$target" ||
      { cleanup_publication_temporary || :; return 1; }
  else
    ln -- "$temporary" "$target" &&
      sync -f "$directory" ||
      { cleanup_publication_temporary || :; return 1; }
  fi
  cleanup_publication_temporary
}
authoritative_current() {
  # The lifecycle snapshot is an untrusted remote result even while its lock is
  # held.  Validate the complete lifecycle chain before using either an ID or
  # a selected path; a symlink basename is never lifecycle authority.
  python3 - "$release_root" "$1" "${2:-}" "$genesis_mode" <<'PY'
import base64, json, os, re, stat, sys
root, encoded, provisional_candidate, genesis_mode = sys.argv[1:]
release_re = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$")
activation_re = re.compile(r"^activation-[0-9a-f]{32}$")
backup_re = re.compile(r"^backup-[0-9a-f]{32}$")
sha_re = re.compile(r"^[0-9a-f]{64}$")
timestamp_re = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
policies = {"no-change", "backward-compatible", "restore-required", "adopted"}
def exact(value, fields):
    if not isinstance(value, dict) or set(value) != fields or not all(isinstance(key, str) for key in value): raise ValueError
    return value
def release(value):
    return isinstance(value, str) and release_re.fullmatch(value)
def timestamp(value):
    return isinstance(value, str) and timestamp_re.fullmatch(value)
def path(value):
    return isinstance(value, str) and value.startswith("/") and "//" not in value and "/../" not in value and not value.endswith("/..") and "/./" not in value and not value.endswith("/.")
def manifest(value, release_id):
    value = exact(value, {"schema_version", "application", "application_version", "source_revision", "release_id", "built_at", "target_os", "architecture", "otp_version", "elixir_version", "node_version", "hex_version", "rebar3_version", "migrations", "top_level"})
    if value["schema_version"] != 1 or value["application"] != "taskman" or not isinstance(value["application_version"], str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", value["application_version"]) or not isinstance(value["source_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["source_revision"]) or value["release_id"] != release_id or value["release_id"] != value["application_version"] + "-" + value["source_revision"][:12] + "-ubuntu26.04-amd64-otp27.3.4.6" or not timestamp(value["built_at"]) or value["target_os"] != "ubuntu26.04" or value["architecture"] != "amd64" or value["otp_version"] != "27.3.4.6" or value["elixir_version"] != "1.18.3" or value["node_version"] != "22.22.1" or value["hex_version"] != "2.5.1" or value["rebar3_version"] != "3.24.0" or value["top_level"] != "taskman" or not isinstance(value["migrations"], list): raise ValueError
    names = []
    for migration in value["migrations"]:
        migration = exact(migration, {"filename", "sha256"})
        if not isinstance(migration["filename"], str) or not re.fullmatch(r"[0-9]{14}_[a-z0-9_]+[.]exs", migration["filename"]) or not isinstance(migration["sha256"], str) or not sha_re.fullmatch(migration["sha256"]): raise ValueError
        names.append(migration["filename"])
    if names != sorted(names) or len(names) != len(set(names)): raise ValueError
    return value
def release_marker(release_id, checksum):
    marker = os.path.join(root, release_id, ".taskman-release.json")
    info = os.lstat(marker)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode): raise ValueError
    with open(marker, encoding="utf-8") as stream: value = json.load(stream)
    value = exact(value, {"schema_version", "release_id", "artifact_sha256"})
    if value["schema_version"] != 1 or value["release_id"] != release_id or value["artifact_sha256"] != checksum: raise ValueError
def warning(value):
    prefixes = ("unexpected deployment-root entry: ", "unrecognized deployment storage entry: ", "unrecognized lifecycle transaction: ", "unrecognized manifest entry: ", "unrecognized release directory: ", "unrecognized release entry: ", "orphan backup dump: ")
    return isinstance(value, str) and value.startswith(prefixes) and re.fullmatch(r"[A-Za-z0-9_: ./+-]+", value) is not None
try:
    snapshot = exact(json.loads(encoded), {"schema_version", "records", "current_target", "manifests", "dump_states", "warnings"})
    if snapshot["schema_version"] != 1 or not isinstance(snapshot["manifests"], dict) or not isinstance(snapshot["dump_states"], dict) or not isinstance(snapshot["warnings"], list) or not all(warning(item) for item in snapshot["warnings"]) or len(snapshot["warnings"]) != len(set(snapshot["warnings"])): raise ValueError
    records = exact(snapshot["records"], {"releases", "activations", "backups", "adoptions"})
    if not all(isinstance(records[name], list) for name in records): raise ValueError
    releases = {}
    for value in records["releases"]:
        value = exact(value, {"schema_version", "release_id", "artifact_sha256", "installed_at", "activated_at", "previous_release_id", "backup_id", "migration_policy"})
        if value["schema_version"] != 1 or not release(value["release_id"]) or value["artifact_sha256"] is not None and not (isinstance(value["artifact_sha256"], str) and sha_re.fullmatch(value["artifact_sha256"])) or not timestamp(value["installed_at"]) or value["activated_at"] is not None and not timestamp(value["activated_at"]) or value["previous_release_id"] is not None and not release(value["previous_release_id"]) or value["backup_id"] is not None and not (isinstance(value["backup_id"], str) and backup_re.fullmatch(value["backup_id"])) or value["migration_policy"] not in policies or value["release_id"] in releases: raise ValueError
        releases[value["release_id"]] = value
    backups = {}
    for value in records["backups"]:
        value = exact(value, {"schema_version", "backup_id", "created_at", "size_bytes", "source_database_size_bytes", "database", "current_release_id", "candidate_release_id", "reason", "validated", "dump_path"})
        if value["schema_version"] != 1 or not isinstance(value["backup_id"], str) or not backup_re.fullmatch(value["backup_id"]) or not timestamp(value["created_at"]) or type(value["size_bytes"]) is not int or value["size_bytes"] < 0 or type(value["source_database_size_bytes"]) is not int or value["source_database_size_bytes"] <= 0 or not isinstance(value["database"], str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value["database"]) or value["current_release_id"] is not None and not release(value["current_release_id"]) or value["candidate_release_id"] is not None and not release(value["candidate_release_id"]) or value["reason"] not in {"scheduled", "pre-deploy", "pre-rollback", "pre-restore"} or type(value["validated"]) is not bool or not path(value["dump_path"]) or value["backup_id"] in backups: raise ValueError
        backups[value["backup_id"]] = value
    activations = []
    activation_ids = set()
    for value in records["activations"]:
        value = exact(value, {"schema_version", "activation_id", "previous_release_id", "candidate_release_id", "activated_at", "backup_id", "migration_policy"})
        if value["schema_version"] != 1 or not isinstance(value["activation_id"], str) or not activation_re.fullmatch(value["activation_id"]) or value["activation_id"] in activation_ids or value["previous_release_id"] is not None and not release(value["previous_release_id"]) or not release(value["candidate_release_id"]) or not timestamp(value["activated_at"]) or value["backup_id"] is not None and not (isinstance(value["backup_id"], str) and backup_re.fullmatch(value["backup_id"])) or value["migration_policy"] not in policies or value["previous_release_id"] == value["candidate_release_id"]: raise ValueError
        activation_ids.add(value["activation_id"]); activations.append(value)
    adoptions = {}
    for value in records["adoptions"]:
        value = exact(value, {"schema_version", "release_id", "adopted_at", "release_path", "content_sha256", "application_version", "source_revision", "artifact_sha256", "migrations"})
        migrations = value["migrations"]
        if value["schema_version"] != 1 or not release(value["release_id"]) or value["release_id"] in adoptions or not timestamp(value["adopted_at"]) or not path(value["release_path"]) or not value["release_path"].startswith(root + "/") or not isinstance(value["content_sha256"], str) or not sha_re.fullmatch(value["content_sha256"]) or not isinstance(value["application_version"], str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", value["application_version"]) or value["source_revision"] != "unknown" or value["artifact_sha256"] != "unknown" or not isinstance(migrations, list): raise ValueError
        names = []
        for migration in migrations:
            migration = exact(migration, {"filename", "sha256"})
            if not isinstance(migration["filename"], str) or not re.fullmatch(r"[0-9]{14}_[a-z0-9_]+[.]exs", migration["filename"]) or not isinstance(migration["sha256"], str) or not sha_re.fullmatch(migration["sha256"]): raise ValueError
            names.append(migration["filename"])
        if names != sorted(names) or len(names) != len(set(names)): raise ValueError
        adoptions[value["release_id"]] = value
    if not set(adoptions).issubset(releases): raise ValueError
    # A provisional record is the only permitted interim state: its fresh
    # backup may name the staged candidate before the final release/activation
    # edge exists.  The caller still requires that exact provisional below.
    known_release_ids = set(releases)
    if release(provisional_candidate): known_release_ids.add(provisional_candidate)
    if any(item is not None and item not in known_release_ids for value in backups.values() for item in (value["current_release_id"], value["candidate_release_id"])): raise ValueError
    activations.sort(key=lambda value: (value["activated_at"], value["activation_id"]))
    previous = None; first = {}; last_time = None
    for value in activations:
        if last_time is not None and value["activated_at"] <= last_time or value["previous_release_id"] != previous or value["candidate_release_id"] not in releases or value["backup_id"] is not None and value["backup_id"] not in backups: raise ValueError
        first.setdefault(value["candidate_release_id"], value); previous = value["candidate_release_id"]; last_time = value["activated_at"]
    for release_id, value in releases.items():
        activation = first.get(release_id)
        if value["previous_release_id"] is not None and value["previous_release_id"] not in releases or value["backup_id"] is not None and value["backup_id"] not in backups: raise ValueError
        if activation is None:
            partial_candidate = release_id == provisional_candidate and snapshot["current_target"] == root + "/" + provisional_candidate
            if not partial_candidate and (value["activated_at"] is not None or value["previous_release_id"] is not None or value["backup_id"] is not None): raise ValueError
        elif value["activated_at"] != activation["activated_at"] or value["previous_release_id"] != activation["previous_release_id"] or value["backup_id"] != activation["backup_id"] or value["migration_policy"] != activation["migration_policy"]: raise ValueError
        if release_id not in adoptions:
            if not isinstance(value["artifact_sha256"], str) or not sha_re.fullmatch(value["artifact_sha256"]): raise ValueError
            release_marker(release_id, value["artifact_sha256"])
            manifest(snapshot["manifests"].get(release_id), release_id)
    if set(snapshot["manifests"]) != set(releases) - set(adoptions): raise ValueError
    for release_id, adoption in adoptions.items():
        release_record = releases[release_id]; activation = first.get(release_id)
        expected = f'{adoption["application_version"]}-{adoption["content_sha256"][:12]}-ubuntu26.04-amd64-otp27.3.4.6'
        if release_id != expected or release_record["artifact_sha256"] is not None or release_record["migration_policy"] != "adopted" or activation is None or activation["migration_policy"] != "adopted" or release_record["installed_at"] != adoption["adopted_at"] or release_record["activated_at"] != adoption["adopted_at"] or activation["activated_at"] != adoption["adopted_at"]: raise ValueError
    if genesis_mode == "1":
        if adoptions or len(releases) > 1 or len(activations) > 1: raise ValueError
        if activations and (
            activations[0]["previous_release_id"] is not None
            or activations[0]["candidate_release_id"] != provisional_candidate
        ): raise ValueError
    if not activations:
        warnings64 = base64.b64encode(json.dumps(snapshot["warnings"], separators=(",", ":")).encode()).decode()
        if snapshot["current_target"] is None:
            if releases: raise ValueError
            print("-|-|empty|" + warnings64)
            raise SystemExit(0)
        if genesis_mode != "1" or not release(provisional_candidate) or snapshot["current_target"] != root + "/" + provisional_candidate:
            raise ValueError
        if releases:
            if set(releases) != {provisional_candidate}: raise ValueError
            state = "genesis-release-partial"
        else:
            state = "genesis-provisional"
        print("-|" + root + "/" + provisional_candidate + "|" + state + "|" + warnings64)
        raise SystemExit(0)
    current = activations[-1]["candidate_release_id"]
    expected_path = adoptions.get(current, {}).get("release_path", root + "/" + current)
    selection_state = "current"
    if snapshot["current_target"] != expected_path:
        if not release(provisional_candidate) or snapshot["current_target"] != root + "/" + provisional_candidate: raise ValueError
        selection_state = "provisional"
    print(current + "|" + expected_path + "|" + selection_state + "|" + base64.b64encode(json.dumps(snapshot["warnings"], separators=(",", ":")).encode()).decode())
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
manual_adoption_snapshot() {
  # A manual baseline may be adopted only from a residue-free state with no
  # lifecycle evidence.  Malformed or merely loose records are not adoption
  # candidates and must be investigated instead.
  python3 - "$release_root" "$upload" "$1" <<'PY'
import json, os, sys
try:
    root, upload, encoded = sys.argv[1:]
    value = json.loads(encoded)
    if not isinstance(value, dict) or set(value) != {"schema_version", "records", "current_target", "manifests", "dump_states", "warnings"} or value["schema_version"] != 1: raise ValueError
    records = value["records"]
    if not isinstance(records, dict) or set(records) != {"releases", "activations", "backups", "adoptions"} or any(not isinstance(records[key], list) or records[key] for key in records): raise ValueError
    target = value["current_target"]
    if not isinstance(target, str) or not target.startswith(root + "/") or not isinstance(value["warnings"], list) or not all(isinstance(item, str) for item in value["warnings"]) or not isinstance(value["manifests"], dict) or value["manifests"] or not isinstance(value["dump_states"], dict) or value["dump_states"]: raise ValueError
    expected = {"unexpected deployment-root entry: uploads", "unrecognized release directory: " + os.path.basename(target)}
    if set(value["warnings"]) != expected or len(value["warnings"]) != len(expected): raise ValueError
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
matching_final_activation() {
  # Lifecycle records are input, not grep-able hints.  Parse exact JSON
  # schemas before treating the selected symlink as an authoritative no-op.
  python3 - "$deployment_root" "$candidate_release_id" "$archive_sha256" "$record_owner_uid" "${1:--}" <<'PY'
import json, os, re, stat, sys
root, candidate, checksum, owner_text, expected_activation = sys.argv[1:]
owner = int(owner_text)
release_re = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$")
activation_re = re.compile(r"^activation-[0-9a-f]{32}$")
backup_re = re.compile(r"^backup-[0-9a-f]{32}$")
timestamp_re = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
def secure_file(path):
    info = os.lstat(path)
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_uid == owner and stat.S_IMODE(info.st_mode) == 0o600
def records(directory):
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o750:
        raise ValueError
    return [os.path.join(directory, name) for name in os.listdir(directory)]
def load(path):
    if not secure_file(path): raise ValueError
    with open(path, encoding="utf-8") as stream: return json.load(stream)
try:
    releases = []
    for path in records(os.path.join(root, "releases")):
        value = load(path)
        if set(value) != {"schema_version","release_id","artifact_sha256","installed_at","activated_at","previous_release_id","backup_id","migration_policy"}: raise ValueError
        if value["schema_version"] != 1 or value["release_id"] != candidate or value["artifact_sha256"] != checksum: continue
        if not timestamp_re.fullmatch(value["installed_at"]) or not timestamp_re.fullmatch(value["activated_at"]): raise ValueError
        if value["previous_release_id"] is not None and not release_re.fullmatch(value["previous_release_id"]): raise ValueError
        if not isinstance(value["backup_id"], str) or not backup_re.fullmatch(value["backup_id"]): raise ValueError
        if value["migration_policy"] not in {"no-change","backward-compatible","restore-required"}: raise ValueError
        releases.append(value)
    if len(releases) != 1: raise ValueError
    matching = 0
    for path in records(os.path.join(root, "activations")):
        value = load(path)
        if set(value) != {"schema_version","activation_id","previous_release_id","candidate_release_id","activated_at","backup_id","migration_policy"}: raise ValueError
        if value["schema_version"] != 1 or not activation_re.fullmatch(value["activation_id"]): raise ValueError
        if value["candidate_release_id"] != candidate: continue
        if expected_activation != "-" and value["activation_id"] != expected_activation: raise ValueError
        if value["previous_release_id"] != releases[0]["previous_release_id"] or value["backup_id"] != releases[0]["backup_id"] or value["migration_policy"] != releases[0]["migration_policy"] or value["activated_at"] != releases[0]["activated_at"]: raise ValueError
        if not timestamp_re.fullmatch(value["activated_at"]): raise ValueError
        matching += 1
    if matching != 1: raise ValueError
except (OSError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
matching_provisional() {
  python3 - "$deployment_root" "$backup_root" "$snapshot_output" "$candidate_release_id" "$archive_sha256" "$migration_policy" "$manifest_b64" "$record_owner_uid" <<'PY'
import base64, json, os, re, stat, sys
root, backup_root, snapshot_text, candidate, checksum, policy, manifest64, owner_text = sys.argv[1:]
owner = int(owner_text)
release_re = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$")
activation_re = re.compile(r"^activation-[0-9a-f]{32}$")
backup_re = re.compile(r"^backup-[0-9a-f]{32}$")
timestamp_re = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
directory = os.path.join(root, "provisionals")
try:
    try: info = os.lstat(directory)
    except FileNotFoundError: raise SystemExit(0)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o750: raise ValueError
    expected_manifest = json.loads(base64.b64decode(manifest64, validate=True))
    found = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name); info = os.lstat(path)
        if not activation_re.fullmatch(name.removesuffix(".json")) or not name.endswith(".json") or not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o600: raise ValueError
        with open(path, encoding="utf-8") as stream: value = json.load(stream)
        if set(value) != {"schema_version","activation_id","previous_release_id","candidate_release_id","backup_id","migration_policy","artifact_sha256","installed_at","activated_at","manifest"}: raise ValueError
        if value["schema_version"] != 1 or value["activation_id"] != name.removesuffix(".json") or not activation_re.fullmatch(value["activation_id"]): raise ValueError
        if value["candidate_release_id"] != candidate or value["artifact_sha256"] != checksum or value["migration_policy"] != policy or value["manifest"] != expected_manifest: raise ValueError
        previous = value["previous_release_id"]
        if previous is not None and not release_re.fullmatch(previous) or not backup_re.fullmatch(value["backup_id"]): raise ValueError
        if not timestamp_re.fullmatch(value["installed_at"]) or value["activated_at"] != value["installed_at"]: raise ValueError
        found.append((value, path))
    if len(found) > 1: raise ValueError
    if found:
        value, path = found[0]
        snapshot = json.loads(snapshot_text)
        records = snapshot["records"]
        backup_records = [item for item in records["backups"] if isinstance(item, dict) and item.get("backup_id") == value["backup_id"]]
        if len(backup_records) != 1: raise ValueError
        backup = backup_records[0]
        if set(backup) != {"schema_version", "backup_id", "created_at", "size_bytes", "source_database_size_bytes", "database", "current_release_id", "candidate_release_id", "reason", "validated", "dump_path"} or backup["schema_version"] != 1 or backup["current_release_id"] != previous or backup["candidate_release_id"] != candidate or backup["reason"] != "pre-deploy" or backup["validated"] is not True or type(backup["size_bytes"]) is not int or backup["size_bytes"] <= 0 or type(backup["source_database_size_bytes"]) is not int or backup["source_database_size_bytes"] <= 0 or not isinstance(backup["dump_path"], str) or not backup["dump_path"].startswith(backup_root + "/") or snapshot["dump_states"].get(backup["dump_path"]) != "present": raise ValueError
        print("|".join((value["activation_id"], previous or "-", value["backup_id"], value["installed_at"], path)))
except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, base64.binascii.Error):
    raise SystemExit(2)
PY
}
matching_release_partial() {
  # A crash after the release record but before the activation edge is the
  # only final-record partial state that does not retain its provisional.
  # It is resumable only when its fresh backup and selected candidate can be
  # proved from the same strict snapshot.
  python3 - "$snapshot_output" "$previous_release_id" "$candidate_release_id" "$archive_sha256" "$migration_policy" "$backup_root" <<'PY'
import json, re, sys
snapshot_text, previous, candidate, checksum, policy, backup_root = sys.argv[1:]
expected_previous = None if previous == "-" else previous
backup_re = re.compile(r"^backup-[0-9a-f]{32}$")
timestamp_re = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
try:
    snapshot = json.loads(snapshot_text)
    records = snapshot["records"]
    release_records = [item for item in records["releases"] if item["release_id"] == candidate]
    activation_records = [item for item in records["activations"] if item["candidate_release_id"] == candidate]
    if len(release_records) != 1 or activation_records: raise ValueError
    release = release_records[0]
    expected = {"schema_version", "release_id", "artifact_sha256", "installed_at", "activated_at", "previous_release_id", "backup_id", "migration_policy"}
    if set(release) != expected or release["schema_version"] != 1 or release["artifact_sha256"] != checksum or release["previous_release_id"] != expected_previous or release["migration_policy"] != policy or release["activated_at"] != release["installed_at"] or not timestamp_re.fullmatch(release["installed_at"]) or not isinstance(release["backup_id"], str) or not backup_re.fullmatch(release["backup_id"]): raise ValueError
    backup_records = [item for item in records["backups"] if item["backup_id"] == release["backup_id"]]
    if len(backup_records) != 1: raise ValueError
    backup = backup_records[0]
    backup_fields = {"schema_version", "backup_id", "created_at", "size_bytes", "source_database_size_bytes", "database", "current_release_id", "candidate_release_id", "reason", "validated", "dump_path"}
    if set(backup) != backup_fields or backup["schema_version"] != 1 or backup["current_release_id"] != expected_previous or backup["candidate_release_id"] != candidate or backup["reason"] != "pre-deploy" or backup["validated"] is not True or type(backup["size_bytes"]) is not int or backup["size_bytes"] <= 0 or type(backup["source_database_size_bytes"]) is not int or backup["source_database_size_bytes"] <= 0 or not isinstance(backup["dump_path"], str) or not backup["dump_path"].startswith(backup_root + "/") or snapshot["dump_states"].get(backup["dump_path"]) != "present": raise ValueError
    print("|".join((release["backup_id"], release["installed_at"])))
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
candidate_marker_matches() {
  python3 - "$candidate/.taskman-release.json" "$candidate_release_id" "$archive_sha256" <<'PY'
import json, os, stat, sys
path, release_id, checksum = sys.argv[1:]
try:
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode): raise ValueError
    with open(path, encoding="utf-8") as stream: value = json.load(stream)
    if not isinstance(value, dict) or set(value) != {"schema_version", "release_id", "artifact_sha256"} or value["schema_version"] != 1 or value["release_id"] != release_id or value["artifact_sha256"] != checksum: raise ValueError
except (OSError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
backup_result() {
  python3 - "$1" "$previous_release_id" "$candidate_release_id" "$backup_root" <<'PY'
import json, re, sys
output, previous, candidate, root = sys.argv[1:]
expected_previous = None if previous == "-" else previous
backup_re = re.compile(r"^backup-[0-9a-f]{32}$")
release_re = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$")
timestamp_re = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
try:
    value = json.loads(output)
    if not isinstance(value, dict) or set(value) != {"schema_version", "backup_id", "created_at", "size_bytes", "source_database_size_bytes", "database", "current_release_id", "candidate_release_id", "reason", "validated", "dump_path"} or value["schema_version"] != 1 or not isinstance(value["backup_id"], str) or not backup_re.fullmatch(value["backup_id"]) or not isinstance(value["created_at"], str) or not timestamp_re.fullmatch(value["created_at"]) or type(value["size_bytes"]) is not int or value["size_bytes"] <= 0 or type(value["source_database_size_bytes"]) is not int or value["source_database_size_bytes"] <= 0 or not isinstance(value["database"], str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value["database"]) or value["current_release_id"] != expected_previous or value["candidate_release_id"] != candidate or value["reason"] != "pre-deploy" or value["validated"] is not True or not isinstance(value["dump_path"], str) or not value["dump_path"].startswith(root + "/") or previous != "-" and not release_re.fullmatch(previous) or not release_re.fullmatch(candidate): raise ValueError
    print(value["backup_id"])
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
activation_result() {
  python3 - "$1" "$2" "$candidate_release_id" "$genesis_mode" <<'PY'
import json, re, sys
output, expected, candidate_release_id, genesis_mode = sys.argv[1:]
try:
    value = json.loads(output)
    fields = {"activation_id", "activated_at", "service_state", "selected_release_id", "database_state", "activation_recorded", "changed_stages"}
    expected_stages = ["migration", "selection", "start"] if genesis_mode == "1" else ["stop", "migration", "selection", "start"]
    if not isinstance(value, dict) or set(value) != fields or value["activation_id"] != expected or not isinstance(value["activated_at"], str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value["activated_at"]) or value["service_state"] != "active" or value["selected_release_id"] != candidate_release_id or value["database_state"] not in {"unchanged", "changed"} or value["activation_recorded"] is not False or value["changed_stages"] != expected_stages: raise ValueError
    print("|".join((value["activated_at"], value["service_state"], value["selected_release_id"], value["database_state"], ",".join(value["changed_stages"]))))
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
activation_failure() {
  python3 - "$1" <<'PY'
import json, sys
try:
    value = json.loads(sys.argv[1])
    fields = {"service_state", "selected_release_id", "database_state", "activation_recorded", "changed_stages"}
    stages = value["changed_stages"]
    if not isinstance(value, dict) or set(value) != fields or value["service_state"] not in {"active", "stopped", "unknown"} or value["database_state"] not in {"unchanged", "changed", "unknown"} or value["activation_recorded"] is not False or not isinstance(stages, list) or not stages or any(stage not in {"stop", "migration", "selection", "start"} for stage in stages) or stages != list(dict.fromkeys(stages)): raise ValueError
    selected = value["selected_release_id"]
    if not isinstance(selected, str): raise ValueError
    print("|".join((stages[-1], value["service_state"], selected, value["database_state"], ",".join(stages))))
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
merge_activation_stages() {
  previous_ifs=$IFS; IFS=,; set -- $1; IFS=$previous_ifs
  for activation_stage in "$@"; do mark_changed "$activation_stage"; done
}
publish_final_records() {
  final_activation_id=$1; final_previous_release_id=$2; final_backup_id=$3; final_timestamp=$4; final_provisional=$5
  final_previous_json=null
  test "$final_previous_release_id" = - || final_previous_json="\"$final_previous_release_id\""
  release_record=$(printf '{"schema_version":1,"release_id":"%s","artifact_sha256":"%s","installed_at":"%s","activated_at":"%s","previous_release_id":%s,"backup_id":"%s","migration_policy":"%s"}' "$candidate_release_id" "$archive_sha256" "$final_timestamp" "$final_timestamp" "$final_previous_json" "$final_backup_id" "$migration_policy")
  activation_record=$(printf '{"schema_version":1,"activation_id":"%s","previous_release_id":%s,"candidate_release_id":"%s","activated_at":"%s","backup_id":"%s","migration_policy":"%s"}' "$final_activation_id" "$final_previous_json" "$candidate_release_id" "$final_timestamp" "$final_backup_id" "$migration_policy")
  mark_changed records
  publish "$deployment_root/manifests" "release-$candidate_release_id.json" "$candidate_manifest" || return 1
  publish "$deployment_root/releases" "release-$candidate_release_id.json" "$release_record" || return 1
  # This is deliberately the last durable edge: successful full verification
  # may never be represented as complete before its exact record is written.
  publish "$deployment_root/activations" "$final_activation_id.json" "$activation_record" || return 1
  test "$final_provisional" = - || rm -f -- "$final_provisional" || return 1
}
manifest_migrations() {
  python3 - "$1" <<'PY'
import json, re, sys
release_re = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$")
sha_re = re.compile(r"^[0-9a-f]{64}$")
timestamp_re = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
def exact(value, fields):
    if not isinstance(value, dict) or set(value) != fields: raise ValueError
    return value
def migrations(value):
    if not isinstance(value, list): raise ValueError
    names = []
    for item in value:
        item = exact(item, {"filename", "sha256"})
        if not isinstance(item["filename"], str) or not re.fullmatch(r"[0-9]{14}_[a-z0-9_]+[.]exs", item["filename"]) or not isinstance(item["sha256"], str) or not sha_re.fullmatch(item["sha256"]): raise ValueError
        names.append(item["filename"])
    if names != sorted(names) or len(names) != len(set(names)): raise ValueError
    return value
try:
    value = json.loads(sys.argv[1])
    artifact = {"schema_version", "application", "application_version", "source_revision", "release_id", "built_at", "target_os", "architecture", "otp_version", "elixir_version", "node_version", "hex_version", "rebar3_version", "migrations", "top_level"}
    adoption = {"schema_version", "release_id", "adopted_at", "release_path", "content_sha256", "application_version", "source_revision", "artifact_sha256", "migrations"}
    if isinstance(value, dict) and set(value) == artifact:
        if value["schema_version"] != 1 or value["application"] != "taskman" or not isinstance(value["application_version"], str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", value["application_version"]) or not isinstance(value["source_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["source_revision"]) or not isinstance(value["release_id"], str) or not release_re.fullmatch(value["release_id"]) or value["release_id"] != value["application_version"] + "-" + value["source_revision"][:12] + "-ubuntu26.04-amd64-otp27.3.4.6" or not isinstance(value["built_at"], str) or not timestamp_re.fullmatch(value["built_at"]) or value["target_os"] != "ubuntu26.04" or value["architecture"] != "amd64" or value["otp_version"] != "27.3.4.6" or value["elixir_version"] != "1.18.3" or value["node_version"] != "22.22.1" or value["hex_version"] != "2.5.1" or value["rebar3_version"] != "3.24.0" or value["top_level"] != "taskman": raise ValueError
    elif isinstance(value, dict) and set(value) == adoption:
        if value["schema_version"] != 1 or not isinstance(value["release_id"], str) or not release_re.fullmatch(value["release_id"]) or not isinstance(value["adopted_at"], str) or not timestamp_re.fullmatch(value["adopted_at"]) or not isinstance(value["release_path"], str) or not value["release_path"].startswith("/") or not isinstance(value["content_sha256"], str) or not sha_re.fullmatch(value["content_sha256"]) or not isinstance(value["application_version"], str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", value["application_version"]) or value["source_revision"] != "unknown" or value["artifact_sha256"] != "unknown": raise ValueError
    else:
        raise ValueError
    print(json.dumps(migrations(value["migrations"]), sort_keys=True, separators=(",", ":")))
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; application_port=$5; public_hostname=$6; previous_hint=$7; candidate_release_id=$8; archive_sha256=$9; token=${10}; migration_policy=${11}; manifest_b64=${12}; upload=${13}; backup_command=${14}; stage_b64=${15}; activation_b64=${16}; database_host=${18}; database_port=${19}; database_role=${20}; database_name=${21}; backup_retention=${22}; distribution_port=${23}; manual_adoption=${24}; caddy_config=${25}; readiness_timeout=${26:-30}; connection_timeout=${27:-10}; expected_adoption_release_id=${28:--}; expected_adoption_content_sha256=${29:--}; expected_adoption_migrations_b64=${30:--}; genesis_mode=${31:-0}
record_owner_uid=0
for path in "$managed_root" "$release_root" "$deployment_root" "$backup_root" "$upload" "$backup_command" "$caddy_config"; do safe_path "$path" || exit 2; done
case "$previous_hint" in -) ;; *) safe_release_id "$previous_hint" || exit 2;; esac
safe_release_id "$candidate_release_id" && safe_sha256 "$archive_sha256" && safe_token "$token" && safe_policy "$migration_policy" && safe_base64 "$manifest_b64" && safe_base64 "$stage_b64" && safe_base64 "$activation_b64" || exit 2
case "$application_port:$distribution_port:$database_port" in *[!0-9:]*|*::*|:*) exit 2;; esac
case "$manual_adoption" in 0|1) ;; *) exit 2;; esac
case "$genesis_mode" in 0|1) ;; *) exit 2;; esac
test "$genesis_mode" = 0 || test "$manual_adoption" = 0 || exit 2
if test "$manual_adoption" = 1; then
  safe_release_id "$expected_adoption_release_id" &&
    safe_sha256 "$expected_adoption_content_sha256" &&
    safe_base64 "$expected_adoption_migrations_b64" ||
    exit 2
else
  test "$expected_adoption_release_id:$expected_adoption_content_sha256:$expected_adoption_migrations_b64" = "-:-:-" || exit 2
fi
safe_dir "$managed_root" && safe_dir "$release_root" && safe_dir "$deployment_root" && safe_dir "$backup_root" || exit 10
candidate="$release_root/$candidate_release_id"
completed_database=changed
test "$migration_policy" != no-change || completed_database=unchanged
stage_directory=
changed=false
changed_stages=
previous_release_id=unknown
previous_json=null
backup_json=null
activation_json=null
warnings_json='[]'
verification_json=null
residue_json='[]'
recovery_commands_json='[]'
publication_temporary=
cleanup_publication_temporary() {
  test -n "$publication_temporary" || return 0
  case "$publication_temporary" in
    "$deployment_root/manifests/"*|"$deployment_root/releases/"*|"$deployment_root/activations/"*|"$deployment_root/provisionals/"*) ;;
    *) return 1 ;;
  esac
  case "${publication_temporary##*/}" in *".$token."????????.tmp) ;; *) return 1;; esac
  rm -f -- "$publication_temporary" || return 1
  publication_temporary=
}
cleanup() {
  cleanup_publication_temporary || :
  rm -f -- "$upload" || :
  test -z "$stage_directory" || rm -rf -- "$stage_directory" || :
  release_lifecycle_lock
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
if test "$manual_adoption" = 1 &&
   test "$previous_hint" != "$expected_adoption_release_id"; then
  failure 10 preflight unknown unknown unchanged false
fi
candidate_manifest=$(printf '%s' "$manifest_b64" | base64 -d) || failure 10 preflight unknown unknown unchanged false
snapshot_script=$(printf '%s' "$snapshot_b64" | base64 -d) || failure 10 preflight unknown unknown unchanged false
snapshot_status=0
snapshot_output=$(eval "$snapshot_script") || snapshot_status=$?
test "$snapshot_status" = 0 && test -n "$snapshot_output" || failure 10 preflight unknown unknown unchanged false
authoritative_status=0
authoritative=$(authoritative_current "$snapshot_output" "$candidate_release_id") || authoritative_status=$?
if test "$authoritative_status" != 0 && test "$manual_adoption" = 1; then
  manual_adoption_snapshot "$snapshot_output" || failure 10 preflight unknown unknown unchanged false
  adoption_script=$(printf '%s' "$adoption_b64" | base64 -d) || failure 10 preflight unknown unknown unchanged false
  adoption_status=0
  adoption_output=$(adoption_mode=adopt expected_release_id=$expected_adoption_release_id expected_content_sha256=$expected_adoption_content_sha256 expected_migrations_b64=$expected_adoption_migrations_b64 owner_uid=$record_owner_uid; release_lifecycle_lock() { :; }; eval "$adoption_script") || adoption_status=$?
  test "$adoption_status" = 0 && test -n "$adoption_output" || failure 10 preflight unknown unknown unchanged false
  mark_changed adoption
  snapshot_status=0
  snapshot_output=$(eval "$snapshot_script") || snapshot_status=$?
  test "$snapshot_status" = 0 && test -n "$snapshot_output" || failure 10 preflight unknown unknown unchanged false
  authoritative_status=0
  authoritative=$(authoritative_current "$snapshot_output" "$candidate_release_id") || authoritative_status=$?
fi
test "$authoritative_status" = 0 && test -n "$authoritative" || failure 10 preflight unknown unknown unchanged false
previous_ifs=$IFS; IFS='|'; set -- $authoritative; IFS=$previous_ifs
previous_release_id=$1; previous_release_path=$2; selection_state=$3; warnings_b64=$4
if test "$previous_release_id" = -; then
  test "$genesis_mode" = 1 || failure 10 preflight unknown unknown unchanged false
  test "$previous_release_path" = - || case "$selection_state" in genesis-provisional|genesis-release-partial) safe_path "$previous_release_path" ;; *) failure 10 preflight unknown unknown unchanged false;; esac
  previous_json=null
  pre_activation_service=stopped
  pre_activation_selected=unknown
else
  safe_release_id "$previous_release_id" && safe_path "$previous_release_path" || failure 10 preflight unknown unknown unchanged false
  previous_json="\"$previous_release_id\""
  pre_activation_service=active
  pre_activation_selected=$previous_release_id
fi
case "$selection_state" in
  current|provisional) test "$previous_release_id" != - || failure 10 preflight unknown unknown unchanged false ;;
  empty|genesis-provisional|genesis-release-partial) test "$genesis_mode:$previous_release_id" = "1:-" || failure 10 preflight unknown unknown unchanged false ;;
  *) failure 10 preflight unknown unknown unchanged false ;;
esac
warnings_json=$(printf '%s' "$warnings_b64" | base64 -d) || failure 10 preflight unknown unknown unchanged false
python3 - "$warnings_json" <<'PY' || failure 10 preflight unknown unknown unchanged false
import json, sys
try:
    value = json.loads(sys.argv[1])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value): raise ValueError
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
case "$previous_hint" in -) ;; *) test "$previous_hint" = "$previous_release_id" || failure 10 preflight unknown unknown unchanged false;; esac
resumable_status=0
resumable=$(matching_provisional) || resumable_status=$?
case "$resumable_status" in
  0) ;;
  2) failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false ;;
  *) failure 10 preflight unknown "$pre_activation_selected" unchanged false ;;
esac
if test "$selection_state" = provisional || test "$selection_state" = genesis-provisional; then
  if test -n "$resumable"; then
    previous_ifs=$IFS; IFS='|'; set -- $resumable; IFS=$previous_ifs
    resume_activation_id=$1; resume_previous_release_id=$2; resume_backup_id=$3; resume_timestamp=$4; resume_provisional=$5
    safe_token "${resume_activation_id#activation-}" && safe_backup_id "$resume_backup_id" && test "$resume_previous_release_id" = "$previous_release_id" || failure 10 preflight unknown "$pre_activation_selected" unchanged false
    test "$resume_previous_release_id" = - || safe_release_id "$resume_previous_release_id" || failure 10 preflight unknown "$pre_activation_selected" unchanged false
    candidate_marker_matches || failure 10 preflight unknown "$candidate_release_id" unknown false
    activation_json="\"$resume_activation_id\""
    mark_changed verification
    verification_status=0; run_verification || verification_status=$?
    test "$verification_status" = 0 || failure "$verification_status" verification active "$candidate_release_id" unknown false
    publish_final_records "$resume_activation_id" "$resume_previous_release_id" "$resume_backup_id" "$resume_timestamp" "$resume_provisional" || failure 8 records active "$candidate_release_id" "$completed_database" false
    backup_json="\"$resume_backup_id\""
    emit_success deployed "$resume_previous_release_id" "$backup_json" "\"$resume_activation_id\"" "$completed_database" true
    exit 0
  fi
  release_partial_status=0
  release_partial=$(matching_release_partial) || release_partial_status=$?
  test "$release_partial_status" = 0 && test -n "$release_partial" || failure 10 preflight unknown "$pre_activation_selected" unchanged false
  previous_ifs=$IFS; IFS='|'; set -- $release_partial; IFS=$previous_ifs
  partial_backup_id=$1; partial_timestamp=$2
  safe_backup_id "$partial_backup_id" || failure 10 preflight unknown "$pre_activation_selected" unchanged false
  candidate_marker_matches || failure 10 preflight unknown "$candidate_release_id" unknown false
  activation_json="\"activation-$token\""
  mark_changed verification
  verification_status=0; run_verification || verification_status=$?
  test "$verification_status" = 0 || failure "$verification_status" verification active "$candidate_release_id" unknown false
  publish_final_records "activation-$token" "$previous_release_id" "$partial_backup_id" "$partial_timestamp" - || failure 8 records active "$candidate_release_id" "$completed_database" false
  backup_json="\"$partial_backup_id\""
  emit_success deployed "$previous_release_id" "$backup_json" "\"activation-$token\"" "$completed_database" true
  exit 0
fi
if test "$selection_state" = genesis-release-partial; then
  release_partial_status=0
  release_partial=$(matching_release_partial) || release_partial_status=$?
  test "$release_partial_status" = 0 && test -n "$release_partial" || failure 10 preflight unknown "$pre_activation_selected" unchanged false
  previous_ifs=$IFS; IFS='|'; set -- $release_partial; IFS=$previous_ifs
  partial_backup_id=$1; partial_timestamp=$2
  safe_backup_id "$partial_backup_id" || failure 10 preflight unknown "$pre_activation_selected" unchanged false
  candidate_marker_matches || failure 10 preflight unknown "$candidate_release_id" unknown false
  activation_json="\"activation-$token\""
  mark_changed verification
  verification_status=0; run_verification || verification_status=$?
  test "$verification_status" = 0 || failure "$verification_status" verification active "$candidate_release_id" unknown false
  publish_final_records "activation-$token" - "$partial_backup_id" "$partial_timestamp" - || failure 8 records active "$candidate_release_id" "$completed_database" false
  backup_json="\"$partial_backup_id\""
  emit_success deployed - "$backup_json" "\"activation-$token\"" "$completed_database" true
  exit 0
fi
if test "$previous_release_id" = "$candidate_release_id"; then
  if test -n "$resumable"; then
    previous_ifs=$IFS; IFS='|'; set -- $resumable; IFS=$previous_ifs
    cleanup_activation_id=$1; cleanup_previous_release_id=$2; cleanup_backup_id=$3; cleanup_timestamp=$4; cleanup_provisional=$5
    safe_token "${cleanup_activation_id#activation-}" && safe_release_id "$cleanup_previous_release_id" && safe_backup_id "$cleanup_backup_id" || failure 10 preflight active "$previous_release_id" unchanged false
    candidate_marker_matches &&
      matching_final_activation "$cleanup_activation_id" || failure 10 preflight unknown "$candidate_release_id" unknown false
    activation_json="\"$cleanup_activation_id\""
    mark_changed verification
    verification_status=0; run_verification || verification_status=$?
    test "$verification_status" = 0 || failure "$verification_status" verification active "$candidate_release_id" unknown true
    mark_changed records
    rm -f -- "$cleanup_provisional" || failure 8 records active "$candidate_release_id" "$completed_database" false
    backup_json="\"$cleanup_backup_id\""
    emit_success deployed "$cleanup_previous_release_id" "$backup_json" "\"$cleanup_activation_id\"" "$completed_database" true
    exit 0
  fi
  candidate_marker_matches &&
    matching_final_activation || failure 10 preflight unknown "$previous_release_id" unchanged false
  verification_status=0; run_verification || verification_status=$?
  if test "$verification_status" != 0; then
    mark_changed verification
    failure "$verification_status" verification active "$candidate_release_id" unchanged true
  fi
  emit_success already-current "$previous_release_id" null null unchanged true
  exit 0
fi
test -z "$resumable" || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
candidate_migrations=$(manifest_migrations "$candidate_manifest")
test -n "$candidate_migrations" || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
if test "$previous_release_id" = -; then
  if test "$candidate_migrations" = '[]'; then
    test "$migration_policy" = no-change || failure 10 preflight stopped unknown unchanged false
  else
    test "$migration_policy" = restore-required || failure 10 preflight stopped unknown unchanged false
  fi
else
  if test -f "$deployment_root/manifests/release-$previous_release_id.json" && test ! -L "$deployment_root/manifests/release-$previous_release_id.json"; then
    current_manifest=$(cat "$deployment_root/manifests/release-$previous_release_id.json") || failure 10 preflight active "$previous_release_id" unchanged false
  elif test -f "$deployment_root/adoptions/adoption-$previous_release_id.json" && test ! -L "$deployment_root/adoptions/adoption-$previous_release_id.json"; then
    current_manifest=$(cat "$deployment_root/adoptions/adoption-$previous_release_id.json") || failure 10 preflight active "$previous_release_id" unchanged false
  else
    failure 10 preflight active "$previous_release_id" unchanged false
  fi
  current_migrations=$(manifest_migrations "$current_manifest")
  test -n "$current_migrations" || failure 10 preflight active "$previous_release_id" unchanged false
  if test "$current_migrations" = "$candidate_migrations"; then
    test "$migration_policy" = no-change || failure 10 preflight active "$previous_release_id" unchanged false
  else
    case "$migration_policy" in backward-compatible|restore-required) ;; *) failure 10 preflight active "$previous_release_id" unchanged false;; esac
  fi
fi
available_release=$(df -Pk "$release_root" | awk 'NR == 2 {print $4}') || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
available_backup=$(df -Pk "$backup_root" | awk 'NR == 2 {print $4}') || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
upload_size=$(stat -c %s -- "$upload") || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
expanded_archive_size=$(LC_ALL=C tar -tvzf "$upload" | awk '
  NF < 6 || $3 !~ /^[0-9]+$/ { exit 1 }
  $3 > 900000000000000000 - total { exit 1 }
  { total += $3 }
  END { print total + 0 }
') || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
database_size=$(psql --no-psqlrc --host "$database_host" --port "$database_port" --username "$database_role" --dbname "$database_name" --tuples-only --no-align --command 'SELECT pg_database_size(current_database())' 2>/dev/null) || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
case "$available_release:$available_backup:$upload_size:$expanded_archive_size:$database_size" in *[!0-9:]*|*::*|:*) failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false;; esac
test "$expanded_archive_size" -le 900000000000000000 && test "$database_size" -gt 0 && test "$database_size" -le 900000000000000000 || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
archive_margin=$(( (expanded_archive_size + 9) / 10 )); test "$archive_margin" -ge 67108864 || archive_margin=67108864
backup_margin=$(( (database_size + 9) / 10 )); test "$backup_margin" -ge 67108864 || backup_margin=67108864
release_required=$(( upload_size + expanded_archive_size + archive_margin ))
backup_required=$(( database_size + backup_margin ))
test "$release_required" -le 900000000000000000 && test "$backup_required" -le 900000000000000000 || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
test $((available_release * 1024)) -ge "$release_required" && test $((available_backup * 1024)) -ge "$backup_required" || failure 10 preflight "$pre_activation_service" "$pre_activation_selected" unchanged false
stage_script=$(printf '%s' "$stage_b64" | base64 -d) || failure 8 staging "$pre_activation_service" "$pre_activation_selected" unchanged false
stage_status=0
stage_output=$(sh -ceu "release_lifecycle_lock() { :; }
$stage_script" taskman-stage-locked "$deployment_root" "$release_root" "$deployment_root/uploads" "$candidate_release_id" "$archive_sha256" "$token" "$upload") || stage_status=$?
case "$stage_status" in
  0) ;;
  10) failure 10 staging "$pre_activation_service" "$pre_activation_selected" unchanged false ;;
  *) failure 8 staging "$pre_activation_service" "$pre_activation_selected" unchanged false ;;
esac
case "$stage_output" in '{"outcome":"staged"}'|'{"outcome":"resumed"}') ;; *) failure 8 staging "$pre_activation_service" "$pre_activation_selected" unchanged false;; esac
test "$stage_output" = '{"outcome":"resumed"}' || mark_changed staging
candidate_marker_matches || failure 10 staging "$pre_activation_service" "$pre_activation_selected" unchanged false
backup_status=0
if test "$previous_release_id" = -; then
  backup_output=$(TASKMAN_LIFECYCLE_LOCK_HELD="$lock_token" TASKMAN_LIFECYCLE_LOCK_FD=9 "$backup_command" --already-locked --lock-root "$lock_root" --database-host "$database_host" --database-port "$database_port" --database-role "$database_role" --database-name "$database_name" --backup-root "$backup_root" --deployment-root "$deployment_root" --managed-root "$managed_root" --release-root "$release_root" --retention "$backup_retention" --reason pre-deploy --candidate-release "$candidate_release_id") || backup_status=$?
else
  backup_output=$(TASKMAN_LIFECYCLE_LOCK_HELD="$lock_token" TASKMAN_LIFECYCLE_LOCK_FD=9 "$backup_command" --already-locked --lock-root "$lock_root" --database-host "$database_host" --database-port "$database_port" --database-role "$database_role" --database-name "$database_name" --backup-root "$backup_root" --deployment-root "$deployment_root" --managed-root "$managed_root" --release-root "$release_root" --retention "$backup_retention" --reason pre-deploy --current-release "$previous_release_id" --candidate-release "$candidate_release_id") || backup_status=$?
fi
case "$backup_status" in
  0) ;;
  6) failure 6 backup "$pre_activation_service" "$pre_activation_selected" unchanged false ;;
  *) failure 10 backup "$pre_activation_service" "$pre_activation_selected" unchanged false ;;
esac
backup_id=$(backup_result "$backup_output") || failure 6 backup "$pre_activation_service" "$pre_activation_selected" unchanged false
backup_json="\"$backup_id\""
mark_changed backup
activation_id="activation-$token"
if test "$previous_release_id" = -; then
  activation_script=$(printf '%s' "$genesis_activation_b64" | base64 -d) || failure 8 activation stopped unknown unknown false
else
  activation_script=$(printf '%s' "$activation_b64" | base64 -d) || failure 8 activation active "$previous_release_id" unknown false
fi
activation_status=0
activation_output=$(TASKMAN_ACTIVATION_RECORD_MODE=deferred sh -ceu "$activation_script" taskman-activate-locked "$managed_root" "$release_root" "$deployment_root" "$previous_release_id" "$candidate_release_id" "$backup_id" "$activation_id" "$token" "$archive_sha256" "$migration_policy" "$manifest_b64" "$previous_release_path") || activation_status=$?
case "$activation_status" in
  0) ;;
  7|8)
    child_failure=$(activation_failure "$activation_output") || failure 10 activation unknown unknown unknown false
    previous_ifs=$IFS; IFS='|'; set -- $child_failure; IFS=$previous_ifs
    child_stage=$1; child_service=$2; child_selected=$3; child_database=$4; child_stages=$5
    merge_activation_stages "$child_stages"
    failure "$activation_status" "$child_stage" "$child_service" "$child_selected" "$child_database" false
    ;;
  *) failure 10 activation unknown unknown unknown false ;;
esac
activation_evidence=$(activation_result "$activation_output" "$activation_id") || failure 10 activation unknown unknown unknown false
activation_json="\"$activation_id\""
previous_ifs=$IFS; IFS='|'; set -- $activation_evidence; IFS=$previous_ifs
timestamp=$1; activation_service=$2; activation_selected=$3; activation_database=$4; activation_stages=$5
merge_activation_stages "$activation_stages"
provisional=$(printf '{"schema_version":1,"activation_id":"%s","previous_release_id":%s,"candidate_release_id":"%s","backup_id":"%s","migration_policy":"%s","artifact_sha256":"%s","installed_at":"%s","activated_at":"%s","manifest":%s}' "$activation_id" "$previous_json" "$candidate_release_id" "$backup_id" "$migration_policy" "$archive_sha256" "$timestamp" "$timestamp" "$candidate_manifest")
publish "$deployment_root/provisionals" "$activation_id.json" "$provisional" || failure 8 activation unknown "$candidate_release_id" unknown false
verification_status=0; run_verification || verification_status=$?
mark_changed verification
test "$verification_status" = 0 || failure "$verification_status" verification active "$candidate_release_id" "$activation_database" false
publish_final_records "$activation_id" "$previous_release_id" "$backup_id" "$timestamp" "$deployment_root/provisionals/$activation_id.json" || failure 8 records active "$candidate_release_id" "$activation_database" false
emit_success deployed "$previous_release_id" "$backup_json" "\"$activation_id\"" "$activation_database" true
'''


DEPLOY_TRANSACTION = (
    "set -eu\n"
    "lock_root=/var/lock/taskman; operation=deploy; timeout_ms=${17}; owner_uid=0; lock_mode=exclusive\n"
    + REMOTE_LOCK_FRAMING
    + "snapshot_b64='" + base64.b64encode(_SNAPSHOT_BODY.encode("utf-8")).decode("ascii") + "'\n"
    + "adoption_b64='" + base64.b64encode(_ADOPTION_BODY.encode("utf-8")).decode("ascii") + "'\n"
    + "genesis_activation_b64='" + base64.b64encode(_GENESIS_ACTIVATION_BODY.encode("utf-8")).decode("ascii") + "'\n"
    + _LOCKED_VERIFICATION_BODY
    + "\n"
    + _BODY
)


def run_locked_deployment(
    remote: Remote,
    config: EnvironmentConfig,
    artifact: VerifiedArtifact,
    store: RemoteLifecycleStore,
    *,
    migration_policy: str,
    lock_timeout_seconds: float,
    manual_adoption_confirmed: bool = False,
    expected_previous_release_id: str | None = None,
    expected_manual_adoption: ManualAdoptionCandidate | None = None,
    genesis: bool = False,
) -> dict[str, object]:
    """Upload then run every deployment mutation below one remote lock.

    The upload path is unique and private before the transaction starts. The
    transaction installs its cleanup trap before it reads or publishes any
    mutable deployment path, so an upload failure can only leave its exact
    operation-owned private path for inspection.
    """

    if remote is not store.remote:
        raise ValueError("deployment transaction remote does not match lifecycle store")
    if not isinstance(lock_timeout_seconds, (int, float)) or isinstance(lock_timeout_seconds, bool) or lock_timeout_seconds < 0:
        raise ValueError("deployment lock timeout must be non-negative")
    if migration_policy not in {"no-change", "backward-compatible", "restore-required"}:
        raise ValueError("deployment transaction requires a validated migration policy")
    if not isinstance(manual_adoption_confirmed, bool):
        raise ValueError("manual adoption confirmation must be boolean")
    if not isinstance(genesis, bool):
        raise ValueError("deployment genesis flag must be boolean")
    if genesis and (manual_adoption_confirmed or expected_previous_release_id is not None):
        raise ValueError("genesis deployment cannot adopt or declare a previous release")
    if manual_adoption_confirmed:
        if not isinstance(expected_manual_adoption, ManualAdoptionCandidate):
            raise ValueError("manual adoption requires exact confirmed authority")
    elif expected_manual_adoption is not None:
        raise ValueError("manual adoption authority requires manual adoption confirmation")
    if expected_previous_release_id is not None:
        try:
            expected_previous_release_id = validate_release_id(expected_previous_release_id)
        except ValueError as error:
            raise ValueError("deployment transaction expected previous release is invalid") from error
    if (
        expected_manual_adoption is not None
        and expected_previous_release_id != expected_manual_adoption.release_id
    ):
        raise ValueError(
            "manual adoption previous release must match the exact confirmed authority"
        )
    release_id = artifact.manifest.release_id
    if re.fullmatch(r"[0-9a-f]{64}", artifact.sha256) is None:
        raise ValueError("deployment transaction artifact checksum is invalid")
    token = uuid4().hex
    upload_root = store.deployment_root / "uploads"
    upload = upload_root / f".upload-{release_id}-{token}.tar.gz"
    prepared = remote.run(
        (
            "sh", "-ceu", _PREPARE_UPLOAD, "taskman-deploy-upload-prepare",
            store.deployment_root.as_posix(), store.release_root.as_posix(), upload_root.as_posix(),
        ),
        sudo=True,
        stdin=None,
        sensitive=False,
    )
    if not prepared.succeeded:
        raise _transaction_error(
            prepared.returncode,
            "staging",
            "unable to prepare private deployment upload",
            genesis=genesis,
        )
    try:
        remote.put(artifact.archive, upload, mode=0o600, sensitive=True)
        manifest_b64 = base64.b64encode(
            (json.dumps(artifact.manifest.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        ).decode("ascii")
        expected_adoption_migrations_b64 = (
            base64.b64encode(
                json.dumps(
                    [
                        fingerprint.to_mapping()
                        for fingerprint in expected_manual_adoption.migrations
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).decode("ascii")
            if expected_manual_adoption is not None
            else "-"
        )
        result = remote.run(
            (
                "sh", "-ceu", DEPLOY_TRANSACTION, "taskman-deploy-transaction",
                store.managed_root.as_posix(), store.release_root.as_posix(), store.deployment_root.as_posix(),
                store.backup_root.as_posix(), str(config.application_port), config.public_hostname,
                expected_previous_release_id or "-", release_id,
                artifact.sha256, token, migration_policy, manifest_b64, upload.as_posix(), BACKUP_COMMAND.as_posix(),
                base64.b64encode(_STAGE_BODY.encode("utf-8")).decode("ascii"),
                base64.b64encode(_ACTIVATION_BODY.encode("utf-8")).decode("ascii"),
                str(int(lock_timeout_seconds * 1000)), config.database_host, str(config.database_port),
                config.database_role, config.database_name, str(config.backup_retention), str(config.distribution_port),
                "1" if manual_adoption_confirmed else "0", store.caddy_config.as_posix(),
                str(config.readiness_timeout), str(config.connection_timeout),
                (
                    expected_manual_adoption.release_id
                    if expected_manual_adoption is not None
                    else "-"
                ),
                (
                    expected_manual_adoption.content_sha256
                    if expected_manual_adoption is not None
                    else "-"
                ),
                expected_adoption_migrations_b64,
                "1" if genesis else "0",
            ),
            sudo=True,
            stdin=None,
            sensitive=False,
        )
    except Exception:
        _cleanup_upload(remote, upload)
        raise
    if not result.succeeded:
        raise _transaction_error(
            result.returncode,
            "deploy",
            "locked deployment transaction failed",
            result.stdout,
            managed_root=store.managed_root,
            candidate_release_id=release_id,
            upload=upload,
            genesis=genesis,
        )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        raise _transaction_error(
            ExitStatus.SAFETY,
            "deploy",
            "deployment transaction returned invalid state",
            managed_root=store.managed_root,
            candidate_release_id=release_id,
            upload=upload,
            genesis=genesis,
        ) from None
    return _success_payload(
        payload,
        release_id,
        store.managed_root,
        upload,
        genesis=genesis,
    )


def _cleanup_upload(remote: Remote, upload: PurePosixPath) -> None:
    try:
        remote.run(("rm", "-f", "--", upload.as_posix()), sudo=True, stdin=None, sensitive=True)
    except Exception:
        return


def _transaction_error(
    returncode: int,
    stage: str,
    message: str,
    output: str = "",
    *,
    managed_root: PurePosixPath | None = None,
    candidate_release_id: str | None = None,
    upload: PurePosixPath | None = None,
    genesis: bool = False,
) -> OpsError:
    status = (
        ExitStatus.LOCKED if returncode == ExitStatus.LOCKED else
        ExitStatus.SAFETY if returncode == ExitStatus.SAFETY else
        ExitStatus.BACKUP if returncode == ExitStatus.BACKUP else
        ExitStatus.MIGRATION if returncode == ExitStatus.MIGRATION else
        ExitStatus.READINESS if returncode == ExitStatus.READINESS else ExitStatus.RELEASE
    )
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        payload = None
    failure = _failure_payload(payload, genesis=genesis)
    lock_holder = _lock_holder(payload) if returncode == ExitStatus.LOCKED else None
    malformed_transaction = stage == "deploy" and failure is None and lock_holder is None
    if malformed_transaction:
        status = ExitStatus.SAFETY
    changed = failure["changed"] if failure is not None else malformed_transaction
    error = OpsError(
        status,
        stage,
        message,
        changed=changed if type(changed) is bool else False,
        next_action="inspect the exact selected release, backup, provisional lifecycle evidence, and database state before explicit recovery",
    )
    if failure is not None:
        error.stage = failure["stage"]
        for key, value in failure.items():
            setattr(error, key, value)
    elif lock_holder is not None:
        error.previous_release_id = "unknown"
        error.selected_release_id = "unknown"
        error.backup_id = None
        error.service_state = "unknown"
        error.database_state = "unchanged"
        error.activation_recorded = False
        error.changed_stages = ()
        error.warnings = ()
        error.residue_paths = ()
        commands = ["systemctl status taskman.service"]
        if managed_root is not None:
            commands.append(f"readlink -f {managed_root}/current")
        commands.append("journalctl --no-pager --unit taskman.service --lines=100")
        error.recovery_commands = tuple(commands)
        error.verification = None
        if candidate_release_id is not None:
            error.candidate_release_id = candidate_release_id
    elif malformed_transaction:
        error.previous_release_id = "unknown"
        error.selected_release_id = "unknown"
        error.backup_id = None
        error.service_state = "unknown"
        error.database_state = "unknown"
        error.activation_recorded = "unknown"
        error.changed_stages = ()
        error.warnings = ()
        residues = () if upload is None else (upload.as_posix(),)
        error.residue_paths = residues
        commands = ["systemctl status taskman.service"]
        if managed_root is not None:
            commands.append(f"readlink -f {managed_root}/current")
        commands.append("journalctl --no-pager --unit taskman.service --lines=100")
        commands.extend(f"stat -Lc '%U:%G %a %F %n' -- {path}" for path in residues)
        error.recovery_commands = tuple(commands)
        error.verification = None
        if candidate_release_id is not None:
            error.candidate_release_id = candidate_release_id
    return error


_RELEASE_ID_RE = re.compile(
    r"[0-9]+[.][0-9]+[.][0-9]+(?:[-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6\Z"
)
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_ACTIVATION_ID_RE = re.compile(r"activation-[0-9a-f]{32}\Z")
_FAILURE_STAGES = frozenset({
    "preflight", "staging", "backup", "stop", "migration", "selection", "start",
    "activation", "verification", "records",
})
_SERVICE_STATES = frozenset({"active", "stopped", "unknown"})
_DATABASE_STATES = frozenset({"unchanged", "changed", "unknown"})
_CHANGED_STAGE_ORDER = (
    "adoption",
    "staging",
    "backup",
    "stop",
    "migration",
    "selection",
    "start",
    "verification",
    "records",
)
_CHANGED_STAGES = frozenset(_CHANGED_STAGE_ORDER)
_PRE_ACTIVATION_STAGE_PREFIXES = (
    (),
    ("adoption",),
    ("staging",),
    ("adoption", "staging"),
)
_ACTIVATION_STAGE_SEQUENCE = ("backup", "stop", "migration", "selection", "start")
_DEPLOYED_STAGE_SEQUENCES = frozenset(
    {
        ("verification", "records"),
        *(
            prefix + _ACTIVATION_STAGE_SEQUENCE + ("verification", "records")
            for prefix in _PRE_ACTIVATION_STAGE_PREFIXES
        ),
    }
)
_GENESIS_ACTIVATION_STAGE_SEQUENCE = ("backup", "migration", "selection", "start")
_GENESIS_DEPLOYED_STAGE_SEQUENCES = frozenset(
    {
        ("verification", "records"),
        ("staging",) + _GENESIS_ACTIVATION_STAGE_SEQUENCE + ("verification", "records"),
    }
)
_WARNING_PREFIXES = (
    "unexpected deployment-root entry: ", "unrecognized deployment storage entry: ",
    "unrecognized lifecycle transaction: ", "unrecognized manifest entry: ",
    "unrecognized release directory: ", "unrecognized release entry: ", "orphan backup dump: ",
)


def _success_payload(
    payload: object,
    candidate_release_id: str,
    managed_root: PurePosixPath,
    upload: PurePosixPath,
    *,
    genesis: bool = False,
) -> dict[str, object]:
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise _transaction_error(
            ExitStatus.SAFETY, "deploy", "deployment transaction returned invalid state",
            managed_root=managed_root, candidate_release_id=candidate_release_id, upload=upload,
            genesis=genesis,
        )
    expected = {
        "stage", "previous_release_id", "candidate_release_id", "selected_release_id", "backup_id",
        "activation_id", "service_state", "database_state", "activation_recorded", "changed",
        "changed_stages", "warnings", "recovery_commands", "residue_paths", "verification",
    }
    stage = payload.get("stage")
    if stage == "already-current":
        valid = (
            set(payload) == expected
            and payload["previous_release_id"] == candidate_release_id
            and payload["candidate_release_id"] == candidate_release_id
            and payload["selected_release_id"] == candidate_release_id
            and payload["backup_id"] is None
            and payload["activation_id"] is None
            and payload["service_state"] == "active"
            and payload["database_state"] == "unchanged"
            and payload["activation_recorded"] is True
            and payload["changed"] is False
            and payload["changed_stages"] == []
        )
    elif stage == "deployed":
        valid = (
            set(payload) == expected
            and (
                payload["previous_release_id"] is None
                if genesis
                else isinstance(payload["previous_release_id"], str)
                and _RELEASE_ID_RE.fullmatch(payload["previous_release_id"]) is not None
            )
            and payload["candidate_release_id"] == candidate_release_id
            and payload["selected_release_id"] == candidate_release_id
            and isinstance(payload["backup_id"], str)
            and _BACKUP_ID_RE.fullmatch(payload["backup_id"]) is not None
            and isinstance(payload["activation_id"], str)
            and _ACTIVATION_ID_RE.fullmatch(payload["activation_id"]) is not None
            and payload["service_state"] == "active"
            and payload["database_state"] in _DATABASE_STATES
            and payload["activation_recorded"] is True
            and payload["changed"] is True
            and _changed_stages(payload["changed_stages"])
            and tuple(payload["changed_stages"]) in (
                _GENESIS_DEPLOYED_STAGE_SEQUENCES if genesis else _DEPLOYED_STAGE_SEQUENCES
            )
        )
    else:
        valid = False
    if (
        not valid
        or not _warnings(payload.get("warnings"))
        or not _residue_paths(payload.get("residue_paths"))
        or not _recovery_commands(payload.get("recovery_commands"), payload.get("residue_paths"))
        or not _verification(payload.get("verification"), candidate_release_id, successful=True)
    ):
        raise _transaction_error(
            ExitStatus.SAFETY, "deploy", "deployment transaction returned invalid state",
            managed_root=managed_root, candidate_release_id=candidate_release_id, upload=upload,
            genesis=genesis,
        )
    return payload


def _changed_stages(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item in _CHANGED_STAGES for item in value)
        and len(value) == len(set(value))
        and value == sorted(value, key=_CHANGED_STAGE_ORDER.index)
    )


def _failure_stage_sequence(stage: object, value: object, *, genesis: bool = False) -> bool:
    if not isinstance(stage, str) or not _changed_stages(value):
        return False
    sequence = tuple(value)
    if genesis:
        genesis_prefixes = ((), ("staging",))
        allowed_genesis: dict[str, frozenset[tuple[str, ...]]] = {
            "preflight": frozenset({()}),
            "staging": frozenset({()}),
            "backup": frozenset(genesis_prefixes),
            "migration": frozenset(
                prefix + ("backup", "migration") for prefix in genesis_prefixes
            ),
            "selection": frozenset(
                prefix + ("backup", "migration", "selection")
                for prefix in genesis_prefixes
            ),
            "start": frozenset(
                prefix + _GENESIS_ACTIVATION_STAGE_SEQUENCE
                for prefix in genesis_prefixes
            ),
            "activation": frozenset(
                {
                    *(prefix + ("backup",) for prefix in genesis_prefixes),
                    *(
                        prefix + _GENESIS_ACTIVATION_STAGE_SEQUENCE
                        for prefix in genesis_prefixes
                    ),
                }
            ),
            "verification": frozenset(
                {
                    ("verification",),
                    *(
                        prefix
                        + _GENESIS_ACTIVATION_STAGE_SEQUENCE
                        + ("verification",)
                        for prefix in genesis_prefixes
                    ),
                }
            ),
            "records": _GENESIS_DEPLOYED_STAGE_SEQUENCES,
        }
        return sequence in allowed_genesis.get(stage, frozenset())
    before_activation = frozenset(_PRE_ACTIVATION_STAGE_PREFIXES)
    allowed: dict[str, frozenset[tuple[str, ...]]] = {
        "preflight": frozenset({(), ("adoption",)}),
        "staging": before_activation,
        "backup": before_activation,
        "activation": frozenset(
            {
                *(
                    prefix + ("backup",)
                    for prefix in _PRE_ACTIVATION_STAGE_PREFIXES
                ),
                *(
                    prefix + _ACTIVATION_STAGE_SEQUENCE
                    for prefix in _PRE_ACTIVATION_STAGE_PREFIXES
                ),
            }
        ),
        "verification": frozenset(
            {
                ("verification",),
                *(
                    prefix + _ACTIVATION_STAGE_SEQUENCE + ("verification",)
                    for prefix in _PRE_ACTIVATION_STAGE_PREFIXES
                ),
            }
        ),
        "records": _DEPLOYED_STAGE_SEQUENCES,
    }
    activation_terminal = {
        "stop": ("backup", "stop"),
        "migration": ("backup", "stop", "migration"),
        "selection": ("backup", "stop", "migration", "selection"),
        "start": _ACTIVATION_STAGE_SEQUENCE,
    }.get(stage)
    if activation_terminal is not None:
        return sequence in {
            prefix + activation_terminal
            for prefix in _PRE_ACTIVATION_STAGE_PREFIXES
        }
    return sequence in allowed.get(stage, frozenset())


def _failure_payload(
    payload: object,
    *,
    genesis: bool = False,
) -> dict[str, object] | None:
    expected = {
        "stage", "previous_release_id", "candidate_release_id", "selected_release_id", "backup_id",
        "activation_id", "service_state", "database_state", "activation_recorded", "changed",
        "changed_stages", "warnings", "recovery_commands", "residue_paths", "verification",
    }
    if not isinstance(payload, dict) or set(payload) != expected or not all(isinstance(key, str) for key in payload):
        return None
    if (
        payload["stage"] not in _FAILURE_STAGES
        or (
            payload["previous_release_id"] is not None
            and (
                not isinstance(payload["previous_release_id"], str)
                or payload["previous_release_id"] != "unknown"
                and _RELEASE_ID_RE.fullmatch(payload["previous_release_id"]) is None
            )
        )
        or payload["previous_release_id"] is None and not genesis
        or not isinstance(payload["candidate_release_id"], str)
        or _RELEASE_ID_RE.fullmatch(payload["candidate_release_id"]) is None
        or not isinstance(payload["selected_release_id"], str)
        or payload["selected_release_id"] != "unknown" and _RELEASE_ID_RE.fullmatch(payload["selected_release_id"]) is None
        or payload["backup_id"] is not None and (not isinstance(payload["backup_id"], str) or _BACKUP_ID_RE.fullmatch(payload["backup_id"]) is None)
        or payload["activation_id"] is not None and (not isinstance(payload["activation_id"], str) or _ACTIVATION_ID_RE.fullmatch(payload["activation_id"]) is None)
        or payload["stage"] == "records" and payload["activation_id"] is None
        or payload["service_state"] not in _SERVICE_STATES
        or payload["database_state"] not in _DATABASE_STATES
        or type(payload["activation_recorded"]) is not bool
        or type(payload["changed"]) is not bool
        or not _residue_paths(payload["residue_paths"])
        or not _recovery_commands(payload["recovery_commands"], payload["residue_paths"])
        or not _warnings(payload["warnings"])
        or not _failure_stage_sequence(
            payload["stage"], payload["changed_stages"], genesis=genesis
        )
        or payload["changed"] is not bool(payload["changed_stages"])
        or not _failure_verification(
            payload["verification"],
            payload["candidate_release_id"],
            payload["stage"],
        )
    ):
        return None
    return payload


def _warnings(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(
            isinstance(item, str)
            and item.startswith(_WARNING_PREFIXES)
            and re.fullmatch(r"[A-Za-z0-9_: ./+-]+", item) is not None
            for item in value
        )
        and len(value) == len(set(value))
    )


def _residue_paths(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(path, str) and re.fullmatch(r"/[A-Za-z0-9_./+-]+", path) is not None for path in value)
        and value == sorted(set(value))
    )


def _recovery_commands(value: object, residue_paths: object) -> bool:
    if not isinstance(value, list) or not isinstance(residue_paths, list) or len(value) != 3 + len(residue_paths):
        return False
    if (
        value[0] != "systemctl status taskman.service"
        or not isinstance(value[1], str)
        or re.fullmatch(r"readlink -f /[A-Za-z0-9_./+-]+/current", value[1]) is None
        or value[2] != "journalctl --no-pager --unit taskman.service --lines=100"
    ):
        return False
    return value[3:] == [
        f"stat -Lc '%U:%G %a %F %n' -- {path}"
        for path in residue_paths
    ]


def _verification(value: object, candidate_release_id: object, *, successful: bool) -> bool:
    if value is None:
        return not successful
    try:
        report = VerificationReport.from_mapping(value)
    except (TypeError, ValueError):
        return False
    return (
        report.release_id == candidate_release_id
        and report.expected_release_id == candidate_release_id
        and (report.successful if successful else not report.successful)
    )


def _failure_verification(
    value: object,
    candidate_release_id: object,
    stage: object,
) -> bool:
    if stage == "records":
        return _verification(value, candidate_release_id, successful=True)
    if stage == "verification":
        return _verification(value, candidate_release_id, successful=False)
    return value is None


def _lock_holder(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or set(value) != {"schema_version", "holder"} or value.get("schema_version") != 1:
        return None
    holder = value.get("holder")
    if not isinstance(holder, dict) or set(holder) != {"operation", "pid", "started_at", "mode"}:
        return None
    if (
        not isinstance(holder["operation"], str)
        or re.fullmatch(r"[a-z][a-z-]{0,63}", holder["operation"]) is None
        or type(holder["pid"]) is not int
        or holder["pid"] <= 0
        or not isinstance(holder["started_at"], str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", holder["started_at"]) is None
        or holder["mode"] not in {"shared", "exclusive"}
    ):
        return None
    return holder


__all__ = ["DEPLOY_TRANSACTION", "run_locked_deployment"]
