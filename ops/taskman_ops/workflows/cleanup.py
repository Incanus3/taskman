"""Plan-first cleanup with locked authority revalidation and truthful partial evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
from pathlib import PurePosixPath
import re
from uuid import uuid4

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..output import WorkflowResult
from ..releases.cleanup import CleanupTarget, RecoveryAuthority, StagingAuthority, cleanup_plan
from ..releases.remote_locking import REMOTE_LOCK_FRAMING
from ..releases.records import RemoteLifecycleStore
from ..releases.remote_snapshot import _SNAPSHOT_BODY
from ..remote import Remote
from .operational_preflight import validate_operational_preflight


_TOKEN_RE = re.compile(r"[0-9a-f]{32}\Z")
_TARGET_FIELDS = frozenset({"kind", "identifier", "path", "recoverable", "authority"})
_STAGING_AUTHORITY_FIELDS = frozenset({"record_path", "state"})
_RECOVERY_AUTHORITY_FIELDS = frozenset({"record_path", "source_backup_id", "pre_restore_backup_id", "intended_release_id", "recovery_database", "state"})


_CLEANUP_EXTRA_DISCOVERY_FUNCTION = r'''discover_cleanup_extras() {
deployment_root=$1
python3 - "$deployment_root" <<'PY'
import json, os, re, stat, sys
root = sys.argv[1]
recovery = re.compile(r"^recovery-[0-9a-f]{32}$")
backup = re.compile(r"^backup-[0-9a-f]{32}$")
release = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+(?:[-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$")
staging = re.compile(r"^stage-[0-9a-f]{32}$")
database = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
timestamp = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
def secure_directory(path):
    value = os.lstat(path)
    return stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode) and value.st_uid == 0 and stat.S_IMODE(value.st_mode) == 0o750
def secure_file(path):
    value = os.lstat(path)
    return stat.S_ISREG(value.st_mode) and not stat.S_ISLNK(value.st_mode) and value.st_uid == 0 and stat.S_IMODE(value.st_mode) == 0o600
try:
    restores = os.path.join(root, "restores")
    databases = []
    if os.path.lexists(restores):
        if not secure_directory(restores): raise ValueError
        for name in sorted(os.listdir(restores)):
            if not name.endswith(".json") or not recovery.fullmatch(name[:-5]): raise ValueError
            path = os.path.join(restores, name)
            if not secure_file(path): raise ValueError
            with open(path, encoding="utf-8") as stream: record = json.load(stream)
            expected = {"schema_version", "recovery_id", "database", "recovery_database", "source_backup_id", "pre_restore_backup_id", "intended_release_id", "state", "created_at"}
            identifier = name[:-5]
            if not isinstance(record, dict) or set(record) != expected or record["schema_version"] != 1 or record["recovery_id"] != identifier or not database.fullmatch(record["database"]) or record["recovery_database"] != "taskman_" + identifier.replace("-", "_") or not backup.fullmatch(record["source_backup_id"]) or not backup.fullmatch(record["pre_restore_backup_id"]) or not release.fullmatch(record["intended_release_id"]) or record["state"] != "retained" or not timestamp.fullmatch(record["created_at"]): raise ValueError
            databases.append({"kind":"database","identifier":identifier,"path":"/database/" + identifier,"recoverable":True,"authority":{"record_path":path,"source_backup_id":record["source_backup_id"],"pre_restore_backup_id":record["pre_restore_backup_id"],"intended_release_id":record["intended_release_id"],"recovery_database":record["recovery_database"],"state":record["state"]}})
    uploads = os.path.join(root, "uploads")
    completed = []
    if os.path.lexists(uploads):
        if not secure_directory(uploads): raise ValueError
        for name in sorted(os.listdir(uploads)):
            if not name.endswith(".json") or not staging.fullmatch(name[:-5]): raise ValueError
            path = os.path.join(uploads, name)
            if not secure_file(path): raise ValueError
            with open(path, encoding="utf-8") as stream: receipt = json.load(stream)
            identifier = name[:-5]
            if not isinstance(receipt, dict) or set(receipt) != {"schema_version", "staging_id", "state", "completed_at"} or receipt["schema_version"] != 1 or receipt["staging_id"] != identifier or receipt["state"] != "completed" or not timestamp.fullmatch(receipt["completed_at"]): raise ValueError
            completed.append({"kind":"staging","identifier":identifier,"path":path,"recoverable":False,"authority":{"record_path":path,"state":"completed"}})
    print(json.dumps({"schema_version":1,"retained_databases":databases,"completed_staging":completed}, separators=(",", ":")))
except (OSError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(10)
PY
}
'''

_CLEANUP_EXTRA_DISCOVERY = _CLEANUP_EXTRA_DISCOVERY_FUNCTION + r'''discover_cleanup_extras "$1"
'''

_CLEANUP_SNAPSHOT = (
    r'''snapshot_file=$(mktemp "$lock_root/.cleanup-snapshot-$token.XXXXXXXX") || exit 10
extra_snapshot_file=
cleanup_snapshots() { test -z "$snapshot_file" || rm -f -- "$snapshot_file" || :; test -z "$extra_snapshot_file" || rm -f -- "$extra_snapshot_file" || :; release_lifecycle_lock; }
trap cleanup_snapshots EXIT
(
'''
    + _SNAPSHOT_BODY
    + r'''
) > "$snapshot_file" || exit 10
'''
)

_CLEANUP_EXTRA_SNAPSHOT = _CLEANUP_EXTRA_DISCOVERY_FUNCTION + r'''if test "$extra_fingerprint" != -; then
  extra_snapshot_file=$(mktemp "$lock_root/.cleanup-extras-$token.XXXXXXXX") || exit 10
  discover_cleanup_extras "$deployment_root" > "$extra_snapshot_file" || { printf '%s\n' '{"stage":"revalidation","removed":[],"recoverability":[],"changed":false,"warnings":[]}'; exit 10; }
fi
'''

_CLEANUP_BODY = r'''python3 - "$targets_json" "$confirmation" "$state_fingerprint" "$extra_fingerprint" "$snapshot_file" "$extra_snapshot_file" "$managed_root" "$release_root" "$deployment_root" "$backup_root" "$database_port" <<'PY'
import hashlib, json, os, re, shutil, stat, subprocess, sys
raw_targets, confirmation, expected_fingerprint, expected_extra_fingerprint, snapshot_path, extra_snapshot_path, managed_root, release_root, deployment_root, backup_root, database_port = sys.argv[1:]
patterns = {"release":r"^[0-9]+[.][0-9]+[.][0-9]+(?:[-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$", "backup":r"^backup-[0-9a-f]{32}$", "staging":r"^stage-[0-9a-f]{32}$", "database":r"^recovery-[0-9a-f]{32}$"}
backup_pattern, release_pattern, database_pattern = re.compile(patterns["backup"]), re.compile(patterns["release"]), re.compile(r"^taskman_recovery_[0-9a-f]{32}$")
target_fields = {"kind","identifier","path","recoverable","authority"}; staging_authority_fields = {"record_path","state"}; recovery_authority_fields = {"record_path","source_backup_id","pre_restore_backup_id","intended_release_id","recovery_database","state"}
removed, recoverability = [], []
def emit(stage, warnings): print(json.dumps({"stage":stage,"removed":removed,"recoverability":recoverability,"changed":bool(removed),"warnings":warnings}, separators=(",", ":")))
def refusal(): emit("revalidation", []); raise SystemExit(10)
def cleanup_failure(kind): emit("cleanup", ["exact " + kind + " cleanup failed"]); raise SystemExit(10)
def valid_path(path): return isinstance(path, str) and re.fullmatch(r"/[A-Za-z0-9_./+-]+", path) is not None and not any(part in {"", ".", ".."} for part in path.split("/")[1:])
def digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
def peer_admin(command, *, tuples=False):
    argv = ["sudo","-u","postgres","--","psql","--no-psqlrc","--host","/var/run/postgresql","--port",database_port,"--username","postgres","--dbname=postgres"]
    if tuples: argv.extend(["--tuples-only","--no-align"])
    argv.extend(["--set","ON_ERROR_STOP=1","--command",command])
    return subprocess.run(tuple(argv), stdout=subprocess.PIPE if tuples else subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, check=False)
def recovery_available(target, snapshot):
    authority = target["authority"]; records = snapshot.get("records", {}); backups, releases, dumps = records.get("backups", []), records.get("releases", []), snapshot.get("dump_states", {})
    if not isinstance(backups, list) or not isinstance(releases, list) or not isinstance(dumps, dict): return False
    by_id = {item.get("backup_id"):item for item in backups if isinstance(item, dict)}
    for identifier in (authority["source_backup_id"], authority["pre_restore_backup_id"]):
        record = by_id.get(identifier)
        if not isinstance(record, dict) or record.get("validated") is not True or dumps.get(record.get("dump_path")) != "present": return False
    return any(isinstance(item, dict) and item.get("release_id") == authority["intended_release_id"] for item in releases)
try:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,31} cleanup-[0-9a-f]{32}", confirmation) is None: raise ValueError
    if expected_fingerprint != "-" and re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint) is None: raise ValueError
    if expected_extra_fingerprint != "-" and re.fullmatch(r"[0-9a-f]{64}", expected_extra_fingerprint) is None: raise ValueError
    if re.fullmatch(r"[0-9]{1,5}", database_port) is None or not 0 < int(database_port) < 65536: raise ValueError
    if any(not valid_path(root) for root in (managed_root, release_root, deployment_root, backup_root)): raise ValueError
    with open(snapshot_path, encoding="utf-8") as stream: snapshot = json.load(stream)
    if not isinstance(snapshot, dict) or (expected_fingerprint != "-" and digest(snapshot) != expected_fingerprint): raise ValueError
    extras = {"schema_version":1,"retained_databases":[],"completed_staging":[]}
    if expected_extra_fingerprint != "-":
        with open(extra_snapshot_path, encoding="utf-8") as stream: extras = json.load(stream)
        if not isinstance(extras, dict) or set(extras) != {"schema_version","retained_databases","completed_staging"} or extras.get("schema_version") != 1 or digest(extras) != expected_extra_fingerprint: raise ValueError
    targets = json.loads(raw_targets)
    if not isinstance(targets, list): raise ValueError
    checked, seen = [], set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != target_fields: raise ValueError
        kind, identifier, path, recoverable, authority = target["kind"], target["identifier"], target["path"], target["recoverable"], target["authority"]
        if kind not in patterns or not isinstance(identifier, str) or re.fullmatch(patterns[kind], identifier) is None or not valid_path(path) or type(recoverable) is not bool or (kind, identifier) in seen: raise ValueError
        if kind == "release" and path != release_root + "/" + identifier: raise ValueError
        if kind == "backup" and not path.startswith(backup_root + "/"): raise ValueError
        if kind == "staging" and (not isinstance(authority, dict) or set(authority) != staging_authority_fields or authority.get("record_path") != path or authority.get("state") != "completed" or not path.startswith(deployment_root + "/uploads/")): raise ValueError
        if kind == "database":
            expected_database = "taskman_" + identifier.replace("-", "_")
            if path != "/database/" + identifier or not isinstance(authority, dict) or set(authority) != recovery_authority_fields or authority.get("recovery_database") != expected_database or authority.get("state") != "retained" or not valid_path(authority.get("record_path")) or not authority["record_path"].startswith(deployment_root + "/restores/") or not backup_pattern.fullmatch(authority.get("source_backup_id", "")) or not backup_pattern.fullmatch(authority.get("pre_restore_backup_id", "")) or not release_pattern.fullmatch(authority.get("intended_release_id", "")) or not database_pattern.fullmatch(expected_database): raise ValueError
        seen.add((kind, identifier)); checked.append(target)
    if expected_extra_fingerprint != "-":
        authoritative = extras["completed_staging"] + extras["retained_databases"]
        if not isinstance(authoritative, list) or any(target not in authoritative for target in checked if target["kind"] in {"staging", "database"}): raise ValueError
    removing_backups = {target["identifier"] for target in checked if target["kind"] == "backup"}
    removing_releases = {target["identifier"] for target in checked if target["kind"] == "release"}
    for target in checked:
        if target["kind"] != "database": continue
        authority = target["authority"]
        if authority["source_backup_id"] in removing_backups or authority["pre_restore_backup_id"] in removing_backups or authority["intended_release_id"] in removing_releases: raise ValueError
    for target in checked:
        kind, path = target["kind"], target["path"]
        if kind == "database":
            if target["recoverable"] is not True or not recovery_available(target, snapshot): raise ValueError
            probe = peer_admin("SELECT 1 FROM pg_database WHERE datname = '" + target["authority"]["recovery_database"] + "'", tuples=True)
            if probe.returncode != 0 or probe.stdout.strip() != "1": raise ValueError
        else:
            entry = os.lstat(path)
            if stat.S_ISLNK(entry.st_mode) or (kind == "release" and not stat.S_ISDIR(entry.st_mode)) or (kind in {"backup","staging"} and not stat.S_ISREG(entry.st_mode)): raise ValueError
except (OSError, TypeError, ValueError, json.JSONDecodeError):
    refusal()
for target in checked:
    kind, path = target["kind"], target["path"]
    try:
        if kind == "release": shutil.rmtree(path)
        elif kind in {"backup","staging"}: os.unlink(path)
        else:
            database = target["authority"]["recovery_database"]
            if peer_admin("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '" + database + "' AND pid <> pg_backend_pid()").returncode != 0: cleanup_failure("database")
            if peer_admin('DROP DATABASE "' + database + '"').returncode != 0: cleanup_failure("database")
            # A successful DROP is already a material deletion. Persist it in
            # the result before removing its record, so a later record-unlink
            # failure cannot erase the operator's recovery evidence.
            removed.append(target); recoverability.append(recovery_available(target, snapshot)); os.unlink(target["authority"]["record_path"]); continue
        removed.append(target); recoverability.append(False)
    except OSError: cleanup_failure(kind)
emit("cleaned", [])
PY
'''

CLEANUP_TRANSACTION = (
    "set -eu\n"
    "managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; targets_json=$5; confirmation=$6; state_fingerprint=$7; timeout_ms=$8; token=$9; lock_root=/var/lock/taskman; operation=cleanup; owner_uid=0; lock_mode=exclusive; extra_fingerprint=${10:--}; database_port=${11:-5432}\n"
    + REMOTE_LOCK_FRAMING + _CLEANUP_SNAPSHOT + _CLEANUP_EXTRA_SNAPSHOT + _CLEANUP_BODY
)

def cleanup(remote: Remote, environment: str | EnvironmentConfig, *, targets: Sequence[CleanupTarget] = (), lifecycle_store: RemoteLifecycleStore | None = None, confirm: Callable[[Mapping[str, object]], bool] | None = None, dry_run: bool = False) -> WorkflowResult:
    """Confirm an immutable target list, then revalidate authority under one lock."""
    config = environment if isinstance(environment, EnvironmentConfig) else None
    environment_name = config.name if config is not None else environment
    if not isinstance(environment_name, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,31}", environment_name) is None: raise ValueError("cleanup environment is invalid")
    state_fingerprint: str | None = None; extra_fingerprint: str | None = None
    if config is not None:
        store = lifecycle_store or RemoteLifecycleStore(remote, config.deployment_root, config.managed_root, config.release_root, config.backup_root, application_port=config.application_port, distribution_port=config.distribution_port, database_port=config.database_port)
        if store.remote is not remote: raise ValueError("cleanup lifecycle store does not match remote")
        try:
            validate_operational_preflight(remote, config)
            records, snapshot = store.read(operation="cleanup", lock_timeout_seconds=5)
            if set(records.warnings) - {"unexpected deployment-root entry: restores", "unexpected deployment-root entry: uploads"}: raise OpsError(ExitStatus.SAFETY, "cleanup", "cleanup refuses unrecognized lifecycle storage", False, "inspect the managed lifecycle storage before retrying")
            completed_staging, retained_databases, extras = _discover_cleanup_extras(remote, config)
            targets = cleanup_plan(records, release_root=config.release_root, deployment_root=config.deployment_root, backup_root=config.backup_root, release_retention=config.release_retention, backup_retention=config.backup_retention, completed_staging=completed_staging, retained_databases=retained_databases).targets
            state_fingerprint, extra_fingerprint = _snapshot_fingerprint(snapshot), _snapshot_fingerprint(extras)
        except OpsError as error:
            return WorkflowResult("cleanup", environment_name, error.changed, "safety-refused", facts={"targets": (), "typed_confirmation": None}, next_action=error.next_action, exit_status=error.status)
    plan_targets = tuple(_target_mapping(target) for target in targets); fingerprint = _fingerprint(environment_name, plan_targets); plan = {"environment":environment_name,"targets":plan_targets,"typed_confirmation":f"{environment_name} {fingerprint}"}
    if dry_run: return WorkflowResult("cleanup", environment_name, False, "planned", facts=plan, next_action="review the exact cleanup plan before a confirmed run")
    if not (confirm or _confirm)(plan): return WorkflowResult("cleanup", environment_name, False, "confirmation-cancelled", facts=plan, next_action="review the exact cleanup plan and confirm a later run when ready")
    try:
        evidence = run_locked_cleanup(remote, environment_name, targets=plan_targets, confirmation=str(plan["typed_confirmation"]), managed_root=config.managed_root if config else None, release_root=config.release_root if config else None, deployment_root=config.deployment_root if config else None, backup_root=config.backup_root if config else None, database_port=config.database_port if config else 5432, state_fingerprint=state_fingerprint, extra_fingerprint=extra_fingerprint)
    except OpsError as error:
        return WorkflowResult("cleanup", environment_name, error.changed, f"{error.stage}-failed", facts={**plan,"removed":tuple(getattr(error,"removed", ())),"recoverability":tuple(getattr(error,"recoverability", ()))}, next_action=error.next_action, exit_status=error.status)
    return WorkflowResult("cleanup", environment_name, bool(evidence["changed"]), str(evidence["stage"]), facts={**plan,**evidence}, next_action="report retained recovery artifacts and their recoverability")

def run_locked_cleanup(remote: Remote, environment: str, *, targets: Sequence[Mapping[str, object]], confirmation: str, lock_timeout_seconds: float = 5, operation_token: str | None = None, managed_root: PurePosixPath | None = None, release_root: PurePosixPath | None = None, deployment_root: PurePosixPath | None = None, backup_root: PurePosixPath | None = None, database_port: int = 5432, state_fingerprint: str | None = None, extra_fingerprint: str | None = None) -> dict[str, object]:
    """Revalidate all targets before deletion and preserve partial evidence."""
    if not isinstance(lock_timeout_seconds, (int, float)) or isinstance(lock_timeout_seconds, bool) or lock_timeout_seconds < 0: raise ValueError("cleanup lock timeout must be non-negative")
    if not isinstance(environment, str) or not isinstance(confirmation, str): raise ValueError("cleanup confirmation is invalid")
    if type(database_port) is not int or not 0 < database_port < 65536: raise ValueError("cleanup database port is invalid")
    expected = [_strict_target_mapping(target) for target in targets]
    roots = (managed_root, release_root, deployment_root, backup_root)
    if any(root is not None and (not isinstance(root, PurePosixPath) or not root.is_absolute() or any(part in {"", ".", ".."} for part in root.parts[1:])) for root in roots): raise ValueError("cleanup managed roots are invalid")
    if any(root is None for root in roots):
        if any(root is not None for root in roots): raise ValueError("cleanup managed roots must be supplied together")
        managed_root, release_root, deployment_root, backup_root = PurePosixPath("/opt/taskman"), PurePosixPath("/opt/taskman/releases"), PurePosixPath("/opt/taskman/deployments"), PurePosixPath("/var/backups/taskman")
    fingerprint, extras = _validate_fingerprint(state_fingerprint, "cleanup lifecycle state"), _validate_fingerprint(extra_fingerprint, "cleanup extra authority")
    token = uuid4().hex if operation_token is None else operation_token
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None: raise ValueError("cleanup operation token is invalid")
    result = remote.run(("sh","-ceu",CLEANUP_TRANSACTION,"taskman-cleanup-transaction",managed_root.as_posix(),release_root.as_posix(),deployment_root.as_posix(),backup_root.as_posix(),json.dumps(expected,sort_keys=True,separators=(",",":")),confirmation,fingerprint,str(int(lock_timeout_seconds * 1000)),token,extras,str(database_port)), sudo=True, stdin=None, sensitive=False)
    if result.returncode == ExitStatus.LOCKED: raise OpsError(ExitStatus.LOCKED,"cleanup","another lifecycle operation holds the deployment lock",False,"wait for the reported lifecycle operation before retrying")
    evidence = _cleanup_evidence(result.stdout, expected, successful=result.succeeded)
    if result.succeeded: return evidence
    error = OpsError(ExitStatus.SAFETY, str(evidence["stage"]), "cleanup stopped after exact target revalidation or a reported partial deletion", bool(evidence["changed"]), "inspect the exact removed targets and retained recovery artifacts before recomputing cleanup")
    error.removed, error.recoverability = tuple(evidence["removed"]), tuple(evidence["recoverability"])  # type: ignore[attr-defined]
    raise error

def _cleanup_evidence(output: str, expected: list[dict[str, object]], *, successful: bool) -> dict[str, object]:
    try: value = json.loads(output)
    except (TypeError, json.JSONDecodeError): raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup transaction returned invalid state evidence",False,"recompute the cleanup plan") from None
    if not isinstance(value,dict) or set(value) != {"stage","removed","recoverability","changed","warnings"} or not isinstance(value["removed"],list) or not isinstance(value["recoverability"],list) or len(value["removed"]) != len(value["recoverability"]) or any(type(item) is not bool for item in value["recoverability"]) or type(value["changed"]) is not bool or not isinstance(value["warnings"],list) or any(not isinstance(item,str) for item in value["warnings"]): raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup transaction returned invalid state evidence",False,"recompute the cleanup plan")
    if value["removed"] != expected[:len(value["removed"])] or value["changed"] is not bool(value["removed"]): raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup transaction returned contradictory state evidence",False,"inspect managed artifacts before retrying")
    if successful:
        if value["stage"] != "cleaned" or value["removed"] != expected or value["warnings"] != []: raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup transaction returned contradictory success evidence",False,"inspect managed artifacts before retrying")
    elif value["stage"] == "revalidation":
        if value["removed"] != [] or value["changed"] or value["warnings"] != []: raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup revalidation returned contradictory state evidence",False,"recompute the cleanup plan")
    elif value["stage"] == "cleanup":
        if value["warnings"] == []: raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup partial failure omitted its safe reason",False,"inspect managed artifacts before retrying")
    else: raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup transaction returned an unknown failure state",False,"recompute the cleanup plan")
    return value

def _target_mapping(target: CleanupTarget) -> dict[str, object]:
    if not isinstance(target,CleanupTarget): raise ValueError("cleanup targets must be immutable CleanupTarget records")
    authority = target.authority.to_mapping() if isinstance(target.authority,(StagingAuthority,RecoveryAuthority)) else None
    return {"kind":target.kind,"identifier":target.identifier,"path":target.path.as_posix(),"recoverable":target.recoverable,"authority":authority}

def _discover_cleanup_extras(remote: Remote, config: EnvironmentConfig) -> tuple[tuple[CleanupTarget, ...], tuple[CleanupTarget, ...], Mapping[str, object]]:
    result = remote.run(("sh","-ceu",_CLEANUP_EXTRA_DISCOVERY,"taskman-cleanup-extra-discovery",config.deployment_root.as_posix()),sudo=True,stdin=None,sensitive=False)
    if not result.succeeded: raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup storage contains unrecognized or unsafe artifacts",False,"inspect managed staging and recovery records before retrying")
    try:
        value = json.loads(result.stdout)
        if not isinstance(value,dict) or set(value) != {"schema_version","retained_databases","completed_staging"} or value["schema_version"] != 1 or not isinstance(value["retained_databases"],list) or not isinstance(value["completed_staging"],list): raise ValueError
        databases = tuple(_cleanup_target_from_mapping(item,expected_kind="database") for item in value["retained_databases"]); staging = tuple(_cleanup_target_from_mapping(item,expected_kind="staging") for item in value["completed_staging"])
        if len({target.identifier for target in databases}) != len(databases) or len({target.identifier for target in staging}) != len(staging): raise ValueError
        return staging, databases, value
    except (TypeError,ValueError,json.JSONDecodeError): raise OpsError(ExitStatus.SAFETY,"cleanup","cleanup recovery inventory is invalid",False,"inspect managed recovery records before retrying") from None

def _cleanup_target_from_mapping(value: object, *, expected_kind: str) -> CleanupTarget:
    if not isinstance(value,Mapping) or set(value) != _TARGET_FIELDS: raise ValueError("cleanup extra target is invalid")
    kind, authority = value["kind"], value["authority"]
    if kind == "staging":
        if not isinstance(authority,Mapping) or set(authority) != _STAGING_AUTHORITY_FIELDS: raise ValueError("cleanup staging authority is invalid")
        parsed: StagingAuthority | RecoveryAuthority = StagingAuthority(PurePosixPath(authority["record_path"]),authority["state"])  # type: ignore[arg-type]
    elif kind == "database":
        if not isinstance(authority,Mapping) or set(authority) != _RECOVERY_AUTHORITY_FIELDS: raise ValueError("cleanup recovery authority is invalid")
        parsed = RecoveryAuthority(PurePosixPath(authority["record_path"]),authority["source_backup_id"],authority["pre_restore_backup_id"],authority["intended_release_id"],authority["recovery_database"],authority["state"])  # type: ignore[arg-type]
    else: raise ValueError("cleanup extra target kind is invalid")
    target = CleanupTarget(kind,value["identifier"],PurePosixPath(value["path"]),value["recoverable"],parsed)  # type: ignore[arg-type]
    if target.kind != expected_kind: raise ValueError("cleanup extra target has the wrong kind")
    return target

def _strict_target_mapping(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value,Mapping) or set(value) != _TARGET_FIELDS: raise ValueError("cleanup target mapping is invalid")
    try:
        if value.get("kind") in {"staging","database"}: return _target_mapping(_cleanup_target_from_mapping(value,expected_kind=value["kind"]))  # type: ignore[arg-type]
        return _target_mapping(CleanupTarget(value["kind"],value["identifier"],PurePosixPath(value["path"]),value["recoverable"],value["authority"]))  # type: ignore[arg-type]
    except (TypeError,ValueError): raise ValueError("cleanup target mapping is invalid") from None

def _fingerprint(environment: str, targets: Sequence[Mapping[str, object]]) -> str:
    return "cleanup-" + hashlib.sha256(json.dumps({"environment":environment,"targets":list(targets)},sort_keys=True,separators=(",",":")).encode()).hexdigest()[:32]
def _snapshot_fingerprint(snapshot: Mapping[str, object]) -> str:
    if not isinstance(snapshot,Mapping): raise ValueError("cleanup lifecycle snapshot is invalid")
    try: material = json.dumps(snapshot,sort_keys=True,separators=(",",":"))
    except (TypeError,ValueError): raise ValueError("cleanup lifecycle snapshot is invalid") from None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
def _validate_fingerprint(value: str | None, label: str) -> str:
    if value is None: return "-"
    if not isinstance(value,str) or re.fullmatch(r"[0-9a-f]{64}",value) is None: raise ValueError(f"{label} fingerprint is invalid")
    return value
def _confirm(plan: Mapping[str, object]) -> bool:
    typed = plan["typed_confirmation"]
    return input(f"Cleanup Taskman artifacts? Type '{typed}' to continue: ").strip() == typed

__all__ = ["CLEANUP_TRANSACTION", "cleanup", "run_locked_cleanup"]
