"""Compatibility-gated, history-preserving release rollback."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
from uuid import uuid4

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..manifests import ArtifactManifest
from ..output import WorkflowResult
from ..releases.identifiers import validate_release_id
from ..releases.records import (
    LifecycleRecords,
    RemoteLifecycleStore,
    raise_remote_lifecycle_failure,
    rollback_eligibility,
)
from ..releases.remote_locking import REMOTE_LOCK_FRAMING
from ..remote import Remote
from ..services.backups import BACKUP_COMMAND
from ..verification import VerificationReport, _LOCKED_VERIFICATION_BODY
from .operational_preflight import validate_operational_preflight


_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_ACTIVATION_ID_RE = re.compile(r"activation-[0-9a-f]{32}\Z")
_TOKEN_RE = re.compile(r"[0-9a-f]{32}\Z")
_FAILURE_STAGES = frozenset({"preflight", "backup", "stop", "selection", "start", "verification", "records"})
_CHANGED_STAGE_ORDER = ("backup", "stop", "selection", "start", "verification", "records")
_CHANGED_STAGES = frozenset(_CHANGED_STAGE_ORDER)
_SUCCESS_STAGES = _CHANGED_STAGE_ORDER


_ROLLBACK_BODY = r'''set -eu
umask 077
safe_release_id() { printf '%s\n' "$1" | grep -Eq '^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$'; }
safe_backup_id() { printf '%s\n' "$1" | grep -Eq '^backup-[0-9a-f]{32}$'; }
safe_activation_id() { printf '%s\n' "$1" | grep -Eq '^activation-[0-9a-f]{32}$'; }
safe_token() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{32}$'; }
safe_identifier() { printf '%s\n' "$1" | grep -Eq '^[a-z_][a-z0-9_]{0,62}$'; }
safe_sha256() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'; }
safe_host() { case "$1" in 127.0.0.1|::1) return 0;; *) return 1;; esac; }
safe_port() { case "$1" in ''|*[!0-9]*) return 1;; esac; test "$1" -ge 1 && test "$1" -le 65535; }
managed_root=$1; release_root=$2; deployment_root=$3; backup_root=$4; application_port=$5; public_hostname=$6; previous_release_id=$7; candidate_release_id=$8; token=$9; backup_command=${10}; database_host=${11}; database_port=${12}; database_role=${13}; database_name=${14}; backup_retention=${15}; distribution_port=${16}; caddy_config=${17}; readiness_timeout=${19}; connection_timeout=${20}; target=${21}; confirmation_fingerprint=${22}; target_kind=${23}; target_checksum=${24}
for path in "$managed_root" "$release_root" "$deployment_root" "$backup_root" "$backup_command" "$caddy_config" "$target"; do safe_path "$path" || exit 2; done
safe_release_id "$previous_release_id" && safe_release_id "$candidate_release_id" && test "$previous_release_id" != "$candidate_release_id" || exit 10
safe_token "$token" && safe_host "$database_host" && safe_port "$database_port" && safe_port "$application_port" && safe_port "$distribution_port" && safe_identifier "$database_role" && safe_identifier "$database_name" || exit 2
case "$backup_retention" in ''|*[!0-9]*) exit 2;; esac; test "$backup_retention" -gt 0 || exit 2
safe_dir "$managed_root" && safe_dir "$release_root" && safe_dir "$deployment_root" && safe_dir "$backup_root" || exit 10
case "$confirmation_fingerprint" in ''|*[!a-z0-9,:-]*) exit 2;; esac
case "$target_kind" in direct|adopted) ;; *) exit 2;; esac
case "$target_kind:$target_checksum" in direct:*) safe_sha256 "$target_checksum" || exit 2;; adopted:-) ;; *) exit 2;; esac
changed=false; changed_stages=; backup_json=null; activation_json=null; verification_json=null; selection_temporary=; record_temporary=
recovery_commands_json='["systemctl status taskman.service","readlink -f '"$managed_root"'/current","journalctl --no-pager --unit taskman.service --lines=100"]'
cleanup() { test -z "$selection_temporary" || rm -f -- "$selection_temporary" || :; test -z "$record_temporary" || rm -f -- "$record_temporary" || :; release_lifecycle_lock; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
mark_changed() { changed=true; case ",$changed_stages," in *,"$1",*) ;; *) changed_stages="${changed_stages}${changed_stages:+,}$1";; esac; }
stages_json() { test -n "$changed_stages" || { printf '[]'; return; }; old_ifs=$IFS; IFS=,; set -- $changed_stages; IFS=$old_ifs; first=1; printf '['; for stage in "$@"; do test "$first" = 1 || printf ','; printf '"%s"' "$stage"; first=0; done; printf ']'; }
failure() { status=$1; stage=$2; service=$3; selected=$4; database=$5; history=$6; recorded=$7; printf '{"stage":"%s","previous_release_id":"%s","target_release_id":"%s","selected_release_id":"%s","backup_id":%s,"activation_id":%s,"service_state":"%s","database_state":"%s","history_state":"%s","activation_recorded":%s,"changed":%s,"changed_stages":%s,"warnings":[],"recovery_commands":%s,"residue_paths":[],"verification":%s}\n' "$stage" "$previous_release_id" "$candidate_release_id" "$selected" "$backup_json" "$activation_json" "$service" "$database" "$history" "$recorded" "$changed" "$(stages_json)" "$recovery_commands_json" "$verification_json"; exit "$status"; }
case "$target" in "$release_root"/*) ;; *) failure 10 preflight unknown unknown unknown unknown false;; esac
target_tree() { test -d "$target" && test ! -L "$target" || return 1; test -z "$(find -P "$target" ! -user root -print -quit)" || return 1; test -z "$(find -P "$target" -perm /022 -print -quit)" || return 1; test -z "$(find -P "$target" -perm /7000 -print -quit)" || return 1; }
contained_links() { links=$(find -P "$target" -type l -print) || return 1; if test -n "$links"; then while IFS= read -r link; do link_target=$(readlink -f "$link") || return 1; case "$link_target" in "$target"|"$target"/*) ;; *) return 1;; esac; done <<EOF
$links
EOF
fi; }
direct_target_complete() { target_tree && test -z "$(find -P "$target" ! -type d ! -type f ! -type l -print -quit)" && test -f "$target/.taskman-release.json" && test ! -L "$target/.taskman-release.json" && test -x "$target/bin/migrate" && test -x "$target/bin/server" && test -d "$target/lib" && test -d "$target/releases" || return 1; test -z "$(find -P "$target" ! -group taskman -print -quit)" || return 1; contained_links || return 1; target_marker=$(cat -- "$target/.taskman-release.json") || return 1; test "$target_marker" = "{\"schema_version\":1,\"release_id\":\"$candidate_release_id\",\"artifact_sha256\":\"$target_checksum\"}"; }
adopted_target_complete() { target_tree && test -z "$(find -P "$target" ! -type d ! -type f -print -quit)" || return 1; test -x "$target/bin/server" && test -d "$target/lib" && test -d "$target/releases" || return 1; }
target_complete() { case "$target_kind" in direct) direct_target_complete;; adopted) adopted_target_complete;; esac; }
backup_result() { python3 - "$1" "$previous_release_id" "$candidate_release_id" "$backup_root" "$database_name" <<'PY'
import json, re, sys
raw, current, target, root, database = sys.argv[1:]
backup = re.compile(r"^backup-[0-9a-f]{32}$")
timestamp = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
try:
    value = json.loads(raw)
    fields = {"schema_version", "backup_id", "created_at", "size_bytes", "source_database_size_bytes", "database", "current_release_id", "candidate_release_id", "reason", "validated", "dump_path"}
    if not isinstance(value, dict) or set(value) != fields or value["schema_version"] != 1 or not isinstance(value["backup_id"], str) or not backup.fullmatch(value["backup_id"]) or not isinstance(value["created_at"], str) or not timestamp.fullmatch(value["created_at"]) or type(value["size_bytes"]) is not int or value["size_bytes"] <= 0 or type(value["source_database_size_bytes"]) is not int or value["source_database_size_bytes"] <= 0 or value["database"] != database or value["current_release_id"] != current or value["candidate_release_id"] != target or value["reason"] != "pre-rollback" or value["validated"] is not True or not isinstance(value["dump_path"], str) or not value["dump_path"].startswith(root + "/"): raise ValueError
    print(value["backup_id"])
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}
publish_activation() { directory="$deployment_root/activations"; name="$activation_id.json"; if test -e "$directory" || test -L "$directory"; then safe_dir "$directory" || return 1; else install -d -o root -g root -m 750 -- "$directory" || return 1; safe_dir "$directory" || return 1; fi; destination="$directory/$name"; test ! -e "$destination" && test ! -L "$destination" || return 1; record_temporary=$(mktemp "$directory/.$name.$token.XXXXXXXX") || return 1; printf '%s\n' "$activation_record" > "$record_temporary" || return 1; chown root:root -- "$record_temporary" && chmod 600 -- "$record_temporary" && sync -f "$record_temporary" || return 1; ln -- "$record_temporary" "$destination" || return 1; rm -f -- "$record_temporary" || return 2; record_temporary=; sync -f "$directory" || return 2; }
run_verification() { verification_status=0; verification_json=$(verify_candidate) || verification_status=$?; case "$verification_status" in 0|8|9) return "$verification_status";; *) verification_json=null; return 10;; esac; }
target_complete || failure 10 preflight unknown unknown unknown unknown false
backup_status=0
backup_output=$(TASKMAN_LIFECYCLE_LOCK_HELD="$lock_token" TASKMAN_LIFECYCLE_LOCK_FD=9 "$backup_command" --already-locked --lock-root "$lock_root" --database-host "$database_host" --database-port "$database_port" --database-role "$database_role" --database-name "$database_name" --backup-root "$backup_root" --deployment-root "$deployment_root" --managed-root "$managed_root" --release-root "$release_root" --retention "$backup_retention" --reason pre-rollback --current-release "$previous_release_id" --candidate-release "$candidate_release_id" --rollback-confirmation "$confirmation_fingerprint" --rollback-target-path "$target" --rollback-target-authority "$target_kind") || backup_status=$?
case "$backup_status" in 0) ;; 6) failure 6 backup unknown unknown unchanged unchanged false;; *) failure 10 preflight unknown unknown unknown unknown false;; esac
backup_id=$(backup_result "$backup_output") || failure 6 backup unknown unknown unchanged unchanged false
backup_json="\"$backup_id\""; mark_changed backup
mark_changed stop
if ! systemctl stop taskman.service || systemctl is-active --quiet taskman.service; then failure 8 stop unknown "$previous_release_id" unchanged unchanged false; fi
selection_temporary="$managed_root/.current-$token"; mark_changed selection
ln -s -- "$target" "$selection_temporary" || failure 8 selection stopped "$previous_release_id" unchanged unchanged false
mv -T -- "$selection_temporary" "$managed_root/current" || failure 8 selection stopped unknown unchanged unchanged false
selection_temporary=
mark_changed start
if ! systemctl start taskman.service; then failure 8 start unknown "$candidate_release_id" unchanged unchanged false; fi
candidate="$target"
mark_changed verification
verification_status=0; run_verification || verification_status=$?
test "$verification_status" = 0 || failure "$verification_status" verification unknown "$candidate_release_id" unchanged unchanged false
activation_id="activation-$token"; activation_json="\"$activation_id\""; activated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
activation_record=$(printf '{"schema_version":1,"activation_id":"%s","previous_release_id":"%s","candidate_release_id":"%s","activated_at":"%s","backup_id":"%s","migration_policy":"no-change"}' "$activation_id" "$previous_release_id" "$candidate_release_id" "$activated_at" "$backup_id")
mark_changed records
publication_status=0; publish_activation || publication_status=$?
case "$publication_status" in 0) ;; 1) failure 8 records active "$candidate_release_id" unchanged unchanged false;; *) failure 8 records active "$candidate_release_id" unchanged unknown '"unknown"';; esac
printf '{"stage":"rolled-back","previous_release_id":"%s","target_release_id":"%s","selected_release_id":"%s","backup_id":%s,"activation_id":%s,"service_state":"active","database_state":"unchanged","history_state":"activation-appended","activation_recorded":true,"changed":true,"changed_stages":%s,"warnings":[],"recovery_commands":%s,"residue_paths":[],"verification":%s}\n' "$previous_release_id" "$candidate_release_id" "$candidate_release_id" "$backup_json" "$activation_json" "$(stages_json)" "$recovery_commands_json" "$verification_json"
'''


ROLLBACK_TRANSACTION = (
    "set -eu\n"
    "lock_root=/var/lock/taskman; operation=rollback; timeout_ms=${18}; owner_uid=0; lock_mode=exclusive\n"
    + REMOTE_LOCK_FRAMING
    + _LOCKED_VERIFICATION_BODY
    + "\n"
    + _ROLLBACK_BODY
)


@dataclass(frozen=True)
class RollbackPlan:
    """The exact compatible history segment an operator approved."""

    current_release_id: str
    target_release_id: str
    activation_ids: tuple[str, ...]
    confirmation_fingerprint: str


def assess_rollback(
    records: LifecycleRecords,
    current_release_id: str,
    target_release_id: str,
) -> RollbackPlan:
    """Refuse every target without one complete compatible reverse path."""

    if not isinstance(records, LifecycleRecords):
        raise TypeError("rollback assessment requires lifecycle records")
    try:
        current = validate_release_id(current_release_id)
        target = validate_release_id(target_release_id)
    except ValueError:
        raise _safety("release identifier is invalid") from None
    if current == target:
        raise _safety("target release is already current")
    if target not in {record.release_id for record in records.releases}:
        raise _safety("target release is not installed")

    eligible, reason = rollback_eligibility(
        records,
        current_release_id=current,
        target_release_id=target,
    )
    if not eligible:
        raise _safety(reason or "target release is not connected to the current activation chain")

    activation_ids: list[str] = []
    activation_edges: list[str] = []
    cursor = current
    for activation in reversed(records.activations):
        if activation.candidate_release_id != cursor:
            raise _safety("activation chain is incomplete")
        activation_ids.append(activation.activation_id)
        activation_edges.append(f"{activation.activation_id}:{activation.migration_policy}")
        if activation.previous_release_id == target:
            return RollbackPlan(current, target, tuple(activation_ids), ",".join(activation_edges))
        if activation.previous_release_id is None:
            break
        cursor = activation.previous_release_id
    raise _safety("target release is not connected to the current activation chain")


def rollback(
    remote: Remote,
    config: EnvironmentConfig,
    release_id: str,
    *,
    lifecycle_store: RemoteLifecycleStore | None = None,
    lock_timeout_seconds: float = 5,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    """Confirm and execute one code-only rollback against an installed target."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("rollback requires a validated environment configuration")
    if not isinstance(lock_timeout_seconds, (int, float)) or isinstance(lock_timeout_seconds, bool) or lock_timeout_seconds < 0:
        raise ValueError("rollback lock timeout must be non-negative")
    if not isinstance(dry_run, bool):
        raise TypeError("rollback dry-run flag must be boolean")
    target = validate_release_id(release_id)
    store = lifecycle_store or RemoteLifecycleStore(
        remote,
        config.deployment_root,
        config.managed_root,
        config.release_root,
        config.backup_root,
        application_port=config.application_port,
        distribution_port=config.distribution_port,
        database_port=config.database_port,
    )
    if store.remote is not remote:
        raise ValueError("rollback lifecycle store does not match remote")

    current: str | None = None
    try:
        validate_operational_preflight(remote, config)
        records, snapshot = store.read(operation="rollback", lock_timeout_seconds=lock_timeout_seconds)
        current = records.current_release_id
        if current is None:
            raise _safety("no current release is recorded")
        plan = assess_rollback(records, current, target)
        target_path, target_kind, target_checksum = _target_authority(records, snapshot, store, target)
    except OpsError as error:
        return _failure_result(
            config,
            error,
            target_release_id=target,
            current_release_id=current,
        )

    confirmation = confirm or _confirm
    plan_mapping = {
        "current_release_id": plan.current_release_id,
        "target_release_id": plan.target_release_id,
        "activation_ids": plan.activation_ids,
        "confirmation_fingerprint": plan.confirmation_fingerprint,
        "target_release_path": target_path.as_posix(),
        "target_authority": target_kind,
        "planned_backup": True,
        "services_affected": ("taskman.service",),
    }
    if dry_run:
        return WorkflowResult(
            command="rollback",
            environment=config.name,
            changed=False,
            stage="planned",
            facts={
                "previous_release_id": plan.current_release_id,
                "target_release_id": plan.target_release_id,
                "activation_ids": plan.activation_ids,
                "confirmation_fingerprint": plan.confirmation_fingerprint,
                "target_release_path": target_path.as_posix(),
                "target_authority": target_kind,
                "backup_id": None,
                "selected_release_id": plan.current_release_id,
                "planned_backup": True,
                "services_affected": ("taskman.service",),
            },
            next_action="review the exact rollback plan and run without --dry-run only after explicit confirmation",
        )
    if not confirmation(plan_mapping):
        return WorkflowResult(
            command="rollback",
            environment=config.name,
            changed=False,
            stage="confirmation-cancelled",
            facts={
                "previous_release_id": plan.current_release_id,
                "target_release_id": plan.target_release_id,
                "activation_ids": plan.activation_ids,
                "confirmation_fingerprint": plan.confirmation_fingerprint,
                "target_release_path": target_path.as_posix(),
                "target_authority": target_kind,
                "backup_id": None,
                "selected_release_id": plan.current_release_id,
                "service_state": "unknown",
                "database_state": "unchanged",
                "history_state": "unchanged",
                "activation_recorded": False,
            },
            next_action="review the exact rollback plan and confirm a later run when ready",
        )

    try:
        evidence = run_locked_rollback(
            remote,
            config,
            store,
            current_release_id=plan.current_release_id,
            target_release_id=plan.target_release_id,
            target_release_path=target_path,
            target_authority=target_kind,
            target_checksum=target_checksum,
            confirmation_fingerprint=plan.confirmation_fingerprint,
            lock_timeout_seconds=lock_timeout_seconds,
        )
    except OpsError as error:
        return _failure_result(
            config,
            error,
            target_release_id=plan.target_release_id,
            current_release_id=plan.current_release_id,
        )
    return _result_from_evidence(config, evidence)


def run_locked_rollback(
    remote: Remote,
    config: EnvironmentConfig,
    store: RemoteLifecycleStore,
    *,
    current_release_id: str,
    target_release_id: str,
    target_release_path: PurePosixPath,
    target_authority: str,
    target_checksum: str | None,
    confirmation_fingerprint: str,
    lock_timeout_seconds: float,
    operation_token: str | None = None,
) -> dict[str, object]:
    """Run every post-confirmation rollback operation below one host lock."""

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("locked rollback requires an environment configuration")
    if not isinstance(store, RemoteLifecycleStore) or store.remote is not remote:
        raise ValueError("locked rollback lifecycle store does not match remote")
    if not isinstance(lock_timeout_seconds, (int, float)) or isinstance(lock_timeout_seconds, bool) or lock_timeout_seconds < 0:
        raise ValueError("rollback lock timeout must be non-negative")
    previous = validate_release_id(current_release_id)
    target = validate_release_id(target_release_id)
    if previous == target:
        raise _safety("target release is already current")
    if (
        not isinstance(target_release_path, PurePosixPath)
        or not target_release_path.is_absolute()
        or not target_release_path.is_relative_to(store.release_root)
        or target_release_path == store.release_root
    ):
        raise _safety("rollback target path is outside the managed release root")
    if target_authority not in {"direct", "adopted"}:
        raise ValueError("rollback target authority is invalid")
    if target_authority == "direct" and (not isinstance(target_checksum, str) or re.fullmatch(r"[0-9a-f]{64}", target_checksum) is None):
        raise _safety("direct rollback target has no authoritative artifact checksum")
    if target_authority == "adopted" and target_checksum is not None:
        raise _safety("adopted rollback target has direct-release checksum authority")
    if not isinstance(confirmation_fingerprint, str) or re.fullmatch(
        r"activation-[0-9a-f]{32}:(?:no-change|backward-compatible|restore-required|adopted)(?:,activation-[0-9a-f]{32}:(?:no-change|backward-compatible|restore-required|adopted))*",
        confirmation_fingerprint,
    ) is None:
        raise ValueError("rollback confirmation fingerprint is invalid")
    token = uuid4().hex if operation_token is None else operation_token
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("rollback operation token is invalid")

    result = remote.run(
        (
            "sh", "-ceu", ROLLBACK_TRANSACTION, "taskman-rollback-transaction",
            store.managed_root.as_posix(), store.release_root.as_posix(), store.deployment_root.as_posix(),
            store.backup_root.as_posix(), str(config.application_port), config.public_hostname, previous, target,
            token, BACKUP_COMMAND.as_posix(), config.database_host, str(config.database_port), config.database_role,
            config.database_name, str(config.backup_retention), str(config.distribution_port), store.caddy_config.as_posix(),
            str(int(lock_timeout_seconds * 1000)), str(config.readiness_timeout), str(config.connection_timeout),
            target_release_path.as_posix(), confirmation_fingerprint, target_authority, target_checksum or "-",
        ),
        sudo=True,
        stdin=None,
        sensitive=False,
    )
    if result.succeeded:
        return _rollback_evidence(
            result.stdout,
            previous_release_id=previous,
            target_release_id=target,
            expected_activation_id=f"activation-{token}",
            success=True,
        )
    if result.returncode == ExitStatus.LOCKED:
        raise_remote_lifecycle_failure(result)
    raise _transaction_error(
        result.returncode,
        result.stdout,
        previous_release_id=previous,
        target_release_id=target,
        expected_activation_id=f"activation-{token}",
    )


def _rollback_evidence(
    output: str,
    *,
    previous_release_id: str,
    target_release_id: str,
    expected_activation_id: str,
    success: bool,
    allow_unknown_verification: bool = False,
) -> dict[str, object]:
    """Accept only the bounded transaction evidence schema emitted on-host."""

    try:
        value = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        raise _safety("rollback transaction returned invalid state evidence") from None
    if not _common_evidence(value, previous_release_id, target_release_id):
        raise _safety("rollback transaction returned invalid state evidence")
    if success:
        if not _success_evidence(value, target_release_id, expected_activation_id):
            raise _safety("rollback transaction returned invalid state evidence")
    elif not _failure_evidence(
        value,
        target_release_id,
        expected_activation_id,
        allow_unknown_verification=allow_unknown_verification,
    ):
        raise _safety("rollback transaction returned invalid state evidence")
    return value


def _common_evidence(
    value: object,
    previous_release_id: str,
    target_release_id: str,
) -> bool:
    fields = {
        "stage", "previous_release_id", "target_release_id", "selected_release_id", "backup_id",
        "activation_id", "service_state", "database_state", "history_state", "activation_recorded",
        "changed", "changed_stages", "warnings", "recovery_commands", "residue_paths", "verification",
    }
    if not isinstance(value, dict) or set(value) != fields or not all(isinstance(key, str) for key in value):
        return False
    return (
        value["previous_release_id"] == previous_release_id
        and value["target_release_id"] == target_release_id
        and value["selected_release_id"] in {previous_release_id, target_release_id, "unknown"}
        and _optional_identifier(value["backup_id"], _BACKUP_ID_RE)
        and _optional_identifier(value["activation_id"], _ACTIVATION_ID_RE)
        and value["service_state"] in {"active", "stopped", "unknown"}
        and value["database_state"] in {"unchanged", "unknown"}
        and value["history_state"] in {"unchanged", "activation-appended", "unknown"}
        and value["activation_recorded"] in {True, False, "unknown"}
        and type(value["changed"]) is bool
        and _changed_stages(value["changed_stages"])
        and value["warnings"] == []
        and value["residue_paths"] == []
        and _recovery_commands(value["recovery_commands"])
    )


def _optional_identifier(value: object, pattern: re.Pattern[str]) -> bool:
    return value is None or isinstance(value, str) and pattern.fullmatch(value) is not None


def _changed_stages(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(stage, str) and stage in _CHANGED_STAGES for stage in value)
        and len(value) == len(set(value))
        and value == sorted(value, key=_CHANGED_STAGE_ORDER.index)
    )


def _recovery_commands(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and value[0] == "systemctl status taskman.service"
        and isinstance(value[1], str)
        and re.fullmatch(r"readlink -f /[A-Za-z0-9_./+-]+/current", value[1]) is not None
        and value[2] == "journalctl --no-pager --unit taskman.service --lines=100"
    )


def _verification(value: object, target_release_id: str, *, successful: bool) -> bool:
    try:
        report = VerificationReport.from_mapping(value)
    except (TypeError, ValueError):
        return False
    return (
        report.successful is successful
        and report.release_id == target_release_id
        and report.expected_release_id == target_release_id
    )


def _success_evidence(
    value: Mapping[str, object],
    target_release_id: str,
    expected_activation_id: str,
) -> bool:
    return (
        value["stage"] == "rolled-back"
        and value["selected_release_id"] == target_release_id
        and isinstance(value["backup_id"], str)
        and value["activation_id"] == expected_activation_id
        and value["service_state"] == "active"
        and value["database_state"] == "unchanged"
        and value["history_state"] == "activation-appended"
        and value["activation_recorded"] is True
        and value["changed"] is True
        and value["changed_stages"] == list(_SUCCESS_STAGES)
        and _verification(value["verification"], target_release_id, successful=True)
    )


def _failure_evidence(
    value: Mapping[str, object],
    target_release_id: str,
    expected_activation_id: str,
    *,
    allow_unknown_verification: bool,
) -> bool:
    stage = value["stage"]
    if not isinstance(stage, str) or stage not in _FAILURE_STAGES:
        return False
    stage_sequences = {
        "preflight": (),
        "backup": (),
        "stop": ("backup", "stop"),
        "selection": ("backup", "stop", "selection"),
        "start": ("backup", "stop", "selection", "start"),
        "verification": ("backup", "stop", "selection", "start", "verification"),
        "records": _SUCCESS_STAGES,
    }
    if tuple(value["changed_stages"]) != stage_sequences[stage] or value["changed"] is not bool(value["changed_stages"]):
        return False
    before_backup = stage in {"preflight", "backup"}
    before_records = stage != "records"
    allowed_database_states = {"unchanged", "unknown"} if stage in {"preflight", "backup"} else {"unchanged"}
    allowed_history_states = {"unchanged", "unknown"} if stage in {"preflight", "backup", "records"} else {"unchanged"}
    if (
        (value["backup_id"] is None) != before_backup
        or value["database_state"] not in allowed_database_states
        or value["history_state"] not in allowed_history_states
        or value["activation_recorded"] not in ({False, "unknown"} if stage == "records" else {False})
        or (value["activation_id"] is None) != before_records
        or stage == "records" and value["activation_id"] != expected_activation_id
    ):
        return False
    selected_states = {
        "preflight": {"unknown"},
        "backup": {"unknown"},
        "stop": {value["previous_release_id"]},
        "selection": {value["previous_release_id"], "unknown"},
        "start": {target_release_id},
        "verification": {target_release_id},
        "records": {target_release_id},
    }
    service_states = {
        "preflight": {"unknown"},
        "backup": {"unknown"},
        "stop": {"unknown"},
        "selection": {"stopped", "unknown"},
        "start": {"unknown"},
        "verification": {"unknown"},
        "records": {"active"},
    }
    if value["selected_release_id"] not in selected_states[stage] or value["service_state"] not in service_states[stage]:
        return False
    if stage == "verification":
        return (
            (allow_unknown_verification and value["verification"] is None)
            or _verification(value["verification"], target_release_id, successful=False)
        )
    if stage == "records":
        return _verification(value["verification"], target_release_id, successful=True)
    return value["verification"] is None


def _transaction_error(
    returncode: int,
    output: str,
    *,
    previous_release_id: str,
    target_release_id: str,
    expected_activation_id: str,
) -> OpsError:
    try:
        status = ExitStatus(returncode)
    except (TypeError, ValueError):
        raise _safety("rollback transaction failed without a recognized exit status") from None
    if status not in {ExitStatus.BACKUP, ExitStatus.RELEASE, ExitStatus.READINESS, ExitStatus.SAFETY}:
        raise _safety("rollback transaction failed without safe evidence")
    evidence = _rollback_evidence(
        output,
        previous_release_id=previous_release_id,
        target_release_id=target_release_id,
        expected_activation_id=expected_activation_id,
        success=False,
        allow_unknown_verification=status is ExitStatus.SAFETY,
    )
    stage = evidence["stage"]
    if not isinstance(stage, str):
        raise _safety("rollback transaction returned invalid stage evidence")
    actions = {
        ExitStatus.BACKUP: "inspect PostgreSQL and available backup capacity before retrying",
        ExitStatus.READINESS: "keep Taskman stopped if unhealthy and inspect the selected release and verification evidence before explicit recovery",
        ExitStatus.RELEASE: "do not start another release; inspect the selected release, service state, and fresh backup before explicit recovery",
        ExitStatus.SAFETY: "inspect the managed activation history and use restore when rollback is unsafe",
    }
    error = OpsError(status, stage, "rollback transaction did not complete", bool(evidence["changed"]), actions[status])
    for key in (
        "previous_release_id", "target_release_id", "backup_id", "activation_id", "selected_release_id", "service_state", "database_state", "history_state",
        "activation_recorded", "changed_stages", "recovery_commands", "residue_paths", "verification", "warnings",
    ):
        setattr(error, key, evidence[key])
    return error


def _target_manifest(snapshot: Mapping[str, object], target_release_id: str) -> ArtifactManifest:
    manifests = snapshot.get("manifests")
    try:
        manifest = ArtifactManifest.from_mapping(manifests.get(target_release_id) if isinstance(manifests, Mapping) else None)
    except ValueError:
        raise _safety("target release is incomplete or outside the managed release root") from None
    if manifest.release_id != target_release_id:
        raise _safety("target release is incomplete or outside the managed release root")
    return manifest


def _target_authority(
    records: LifecycleRecords,
    snapshot: Mapping[str, object],
    store: RemoteLifecycleStore,
    target_release_id: str,
) -> tuple[PurePosixPath, str, str | None]:
    """Resolve one target through the strict snapshot's direct or adopted authority."""

    adopted_paths = {record.release_id: record.release_path for record in records.adoptions}
    target_path = adopted_paths.get(target_release_id)
    if target_path is not None:
        target_kind = "adopted"
        target_checksum = None
    else:
        _target_manifest(snapshot, target_release_id)
        target_path = store.release_root / target_release_id
        matching_records = [record for record in records.releases if record.release_id == target_release_id]
        if len(matching_records) != 1 or not isinstance(matching_records[0].artifact_sha256, str) or re.fullmatch(
            r"[0-9a-f]{64}", matching_records[0].artifact_sha256
        ) is None:
            raise _safety("direct rollback target has no authoritative artifact checksum")
        target_kind = "direct"
        target_checksum = matching_records[0].artifact_sha256
    if not target_path.is_relative_to(store.release_root) or target_path == store.release_root:
        raise _safety("target release is incomplete or outside the managed release root")
    return target_path, target_kind, target_checksum


def _result_from_evidence(config: EnvironmentConfig, evidence: Mapping[str, object]) -> WorkflowResult:
    return WorkflowResult(
        command="rollback",
        environment=config.name,
        changed=bool(evidence["changed"]),
        stage=str(evidence["stage"]),
        facts=dict(evidence),
        next_action="perform the remaining browser, email, and API acceptance checks",
    )


def _failure_result(
    config: EnvironmentConfig,
    error: OpsError,
    *,
    target_release_id: str,
    current_release_id: str | None = None,
) -> WorkflowResult:
    previous = getattr(error, "previous_release_id", current_release_id) or "unknown"
    target = getattr(error, "target_release_id", target_release_id) or target_release_id
    selected = getattr(error, "selected_release_id", current_release_id) or "unknown"
    recovery_commands = tuple(
        getattr(
            error,
            "recovery_commands",
            (
                "systemctl status taskman.service",
                f"readlink -f {config.managed_root}/current",
                "journalctl --no-pager --unit taskman.service --lines=100",
            ),
        )
    )
    return WorkflowResult(
        command="rollback",
        environment=config.name,
        changed=error.changed,
        stage="safety-refused" if error.status is ExitStatus.SAFETY else f"{error.stage}-failed",
        facts={
            "previous_release_id": previous,
            "target_release_id": target,
            "backup_id": getattr(error, "backup_id", None),
            "activation_id": getattr(error, "activation_id", None),
            "selected_release_id": selected,
            "service_state": getattr(error, "service_state", "unknown"),
            "database_state": getattr(error, "database_state", "unknown"),
            "history_state": getattr(error, "history_state", "unknown"),
            "activation_recorded": getattr(error, "activation_recorded", "unknown"),
            "changed_stages": tuple(getattr(error, "changed_stages", ())),
            "recovery_commands": recovery_commands,
            "warnings": tuple(getattr(error, "warnings", ())),
            "residue_paths": tuple(getattr(error, "residue_paths", ())),
            "verification": getattr(error, "verification", None),
        },
        next_action=error.next_action,
        exit_status=error.status,
    )


def _confirm(plan: Mapping[str, object]) -> bool:
    current = plan["current_release_id"]
    target = plan["target_release_id"]
    return input(f"Roll back Taskman from {current} to {target}? Type yes to continue: ").strip().lower() == "yes"


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "rollback",
        message,
        changed=False,
        next_action="inspect the managed activation history and use restore when rollback is unsafe",
    )


__all__ = ["RollbackPlan", "assess_rollback", "rollback", "run_locked_rollback"]
