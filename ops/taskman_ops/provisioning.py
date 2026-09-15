"""The single programmatic pyinfra deploy for stable host convergence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re

from pyinfra.api import deploy

from .config import EnvironmentConfig
from .errors import ExitStatus, OpsError
from .remote import ChangeSet, PyinfraRemote
from .services.caddy import CaddyPlan
from .services.postgresql import (
    build_postgresql_plan,
    converge_database,
)
from .services.systemd import build_systemd_plan, install_runtime_environment


_RUNTIME_REUSE_SCRIPT = r'''set -eu
path=$1
if [ ! -e "$path" ] && [ ! -L "$path" ]; then exit 0; fi
test -f "$path" && test ! -L "$path"
test "$(stat --format='%U:%G:%a' -- "$path")" = root:root:600
cmp -s - "$path"
'''
_PGPASS_REUSE_SCRIPT = r'''set -eu
path=$1 host=$2 port=$3 role=$4 database=$5
if [ ! -e "$path" ] && [ ! -L "$path" ]; then
  # A missing managed pgpass is the one supported partial-install boundary.
  # libpq rejects a pipe as PGPASSFILE. Parse exactly the rendered line and
  # give psql only its password environment variable; no passfile is written.
  exec timeout 60s /usr/bin/python3 -c '
import os
import shutil
import sys

def refuse():
    raise SystemExit(1)

def parse(raw):
    if not raw.endswith(b"\n") or raw[:-1].find(b"\n") >= 0 or b"\r" in raw:
        refuse()
    fields = []
    field = bytearray()
    escaped = False
    for byte in raw[:-1]:
        if escaped:
            if byte not in (58, 92):
                refuse()
            field.append(byte)
            escaped = False
        elif byte == 92:
            escaped = True
        elif byte == 58:
            fields.append(bytes(field))
            field.clear()
        else:
            field.append(byte)
    if escaped:
        refuse()
    fields.append(bytes(field))
    if len(fields) != 5 or not fields[4]:
        refuse()
    return fields

fields = parse(sys.stdin.buffer.read())
expected = [item.encode("utf-8") for item in sys.argv[1:]]
if len(expected) != 4 or fields[:4] != expected:
    refuse()
try:
    password = fields[4].decode("utf-8")
except UnicodeDecodeError:
    refuse()
psql = shutil.which("psql")
if psql is None:
    refuse()
os.execve(psql, [psql, "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--no-password", "--host", sys.argv[1], "--port", sys.argv[2], "--username", sys.argv[4], "--dbname", sys.argv[3], "--command", "SELECT 1"], {"PGPASSWORD": password})
' "$host" "$port" "$database" "$role" >/dev/null 2>&1
fi
test -f "$path" && test ! -L "$path"
test "$(stat --format='%U:%G:%a' -- "$path")" = root:root:600
cmp -s - "$path"
export PGPASSFILE=$path
psql --no-psqlrc --set=ON_ERROR_STOP=1 --no-password --host "$host" --port "$port" \
  --username "$role" --dbname "$database" --command 'SELECT 1' >/dev/null 2>&1
'''
_RESOURCE_REUSE_SCRIPT = r'''set -eu
check() {
  path=$1 kind=$2 owner=$3 group=$4 mode=$5 digest=$6
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then return 0; fi
  test ! -L "$path"
  test "$(stat --format='%F:%U:%G:%a' -- "$path")" = "$kind:$owner:$group:$mode"
  if [ -n "$digest" ]; then test "$(sha256sum -- "$path" | awk '{print $1}')" = "$digest"; fi
}
while [ "$#" -gt 0 ]; do
  check "$1" "$2" "$3" "$4" "$5" "$6"
  shift 6
done
'''

_SCHEDULER_RESOURCE_PATHS = frozenset(
    {
        "/usr/local/lib/taskman/taskman-backup.pyz",
        "/etc/systemd/system/taskman-backup.service",
        "/etc/systemd/system/taskman-backup.timer",
        "/etc/taskman/taskman-backup.env",
    }
)
_SCHEDULER_RESOURCE_NAMES = {
    "helper": "/usr/local/lib/taskman/taskman-backup.pyz",
    "service": "/etc/systemd/system/taskman-backup.service",
    "timer": "/etc/systemd/system/taskman-backup.timer",
    "environment": "/etc/taskman/taskman-backup.env",
}


@dataclass(frozen=True)
class ProvisioningInputs:
    """Validated non-secret configuration plus already-rendered secret bytes."""

    config: EnvironmentConfig
    caddy_plan: CaddyPlan
    runtime_environment: bytes
    pgpass: bytes
    role_password_input: bytes
    # Only resources that were observed absent before confirmation may be
    # created by the generic pyinfra boundary.  Scheduler replacement remains
    # the locked genesis helper's responsibility.
    # Compatibility callers which have not performed the provision admission
    # retain the historic all-create declaration.  The public provision path
    # always replaces this with its confirmed create-only delta.
    scheduler_create: frozenset[str] = _SCHEDULER_RESOURCE_PATHS

    def __post_init__(self) -> None:
        if not isinstance(self.config, EnvironmentConfig):
            raise TypeError("provisioning inputs require an environment configuration")
        if not isinstance(self.caddy_plan, CaddyPlan):
            raise TypeError("provisioning inputs require a preconfirmed Caddy plan")
        for field_name in ("runtime_environment", "pgpass", "role_password_input"):
            value = getattr(self, field_name)
            if not isinstance(value, bytes) or not value:
                raise ValueError(f"{field_name} must be non-empty bytes")
        if not isinstance(self.scheduler_create, frozenset) or not self.scheduler_create.issubset(
            _SCHEDULER_RESOURCE_PATHS
        ):
            raise ValueError("scheduler creation authority is invalid")


@deploy("Converge Taskman host")
def taskman_provisioning(*, inputs: ProvisioningInputs) -> None:
    """Declare exactly one host deploy without prepare-time mutable-fact branches."""

    from .host.baseline import declare_baseline
    from .services.caddy import declare_caddy
    from .services.postgresql import declare_postgresql
    from .services.systemd import declare_systemd

    declare_baseline(inputs.config)
    declare_caddy(inputs.caddy_plan)
    declare_postgresql(inputs.config)
    declare_systemd(inputs)


def converge_provisioning(remote: PyinfraRemote, inputs: ProvisioningInputs) -> ChangeSet:
    """Execute stable pyinfra convergence, then the three secret/database guards.

    The remaining imperative work has concrete material-risk ownership:
    PostgreSQL validates the selected cluster before HBA installation and the
    candidate before restart, then validates database authority and connectivity;
    runtime and pgpass bytes never enter pyinfra's command or logging path.
    """

    if not isinstance(remote, PyinfraRemote):
        raise TypeError("provisioning convergence requires a connected PyinfraRemote")
    if not isinstance(inputs, ProvisioningInputs):
        raise TypeError("provisioning convergence requires ProvisioningInputs")

    deploy_changes = remote.run_deploy(taskman_provisioning, inputs=inputs)
    try:
        database_changes = converge_database(
            remote,
            build_postgresql_plan(inputs.config),
            role_password_input=inputs.role_password_input,
            pgpass=inputs.pgpass,
        )
    except OpsError as error:
        error.changed = error.changed or deploy_changes.changed
        raise
    try:
        runtime_changes = install_runtime_environment(remote, inputs.runtime_environment)
    except OpsError as error:
        error.changed = error.changed or deploy_changes.changed or database_changes.changed
        raise
    operations = tuple(dict.fromkeys((*deploy_changes.operations, *database_changes.operations, *runtime_changes.operations)))
    return ChangeSet(changed=bool(operations), operations=operations)


def validate_existing_credential_authority(remote: object, inputs: ProvisioningInputs) -> None:
    """Refuse conflicting existing secret authority before pyinfra can write.

    An absent file is a supported partial-installation boundary.  Once either
    protected file exists it is authority: it must be a root-owned, non-link
    regular file with exact bytes matching the just-decrypted input.  The
    database password file additionally proves that those retained bytes can
    authenticate to the configured role and database.  ``cmp`` consumes the
    protected stdin directly, so this read-only check never creates a staging
    file or exposes secret values in argv, output, or logs.
    """

    if not isinstance(inputs, ProvisioningInputs):
        raise TypeError("credential authority requires provisioning inputs")
    runner = getattr(remote, "run", None)
    if not callable(runner):
        raise TypeError("credential authority requires a remote command runner")
    config = inputs.config
    checks = (
        (_RUNTIME_REUSE_SCRIPT, ("/etc/taskman/taskman.env",), inputs.runtime_environment),
        (
            _PGPASS_REUSE_SCRIPT,
            (
                "/etc/taskman/pgpass",
                config.database_host,
                str(config.database_port),
                config.database_role,
                config.database_name,
            ),
            inputs.pgpass,
        ),
    )
    for script, arguments, content in checks:
        result = runner(
            ("sh", "-ceu", script, "taskman-credential-authority", *arguments),
            sudo=True,
            stdin=content,
            sensitive=True,
        )
        if getattr(result, "succeeded", False):
            continue
        raise OpsError(
            ExitStatus.SAFETY,
            "credential-preflight",
            "existing protected credential authority is incompatible and will not be replaced",
            changed=False,
            next_action="inspect the existing credential and database authority before retrying",
        )


def validate_existing_resource_authority(remote: object, inputs: ProvisioningInputs) -> None:
    """Validate every already-present stable convergence resource without repair.

    The pyinfra declarations may create an absent prerequisite, but they must
    not use their mode/owner convergence as an adoption mechanism.  This
    admission is deliberately read-only: existing directories, account-home
    state, lock, helper, unit files, and secret-parent records must have their
    documented no-link metadata before a declaration can touch them.  Exact
    unit and helper bytes are bound to the same rendered systemd plan that
    convergence will install.
    """

    if not isinstance(inputs, ProvisioningInputs):
        raise TypeError("resource authority requires provisioning inputs")
    runner = getattr(remote, "run", None)
    if not callable(runner):
        raise TypeError("resource authority requires a remote command runner")

    plan = build_systemd_plan(inputs.config)
    assets = {asset.destination: _systemd_asset_sha256(asset) for asset in plan.assets}
    config = inputs.config
    roots = (
        (config.install_root.as_posix(), "directory", "root", "root", "755", ""),
        (config.release_root.as_posix(), "directory", "root", "root", "755", ""),
        (config.deployment_root.as_posix(), "directory", "root", "root", "700", ""),
        (f"{config.deployment_root}/selections", "directory", "root", "root", "750", ""),
        (f"{config.deployment_root}/backup-protections", "directory", "root", "root", "750", ""),
        (config.backup_root.as_posix(), "directory", "root", "root", "700", ""),
        ("/etc/taskman", "directory", "root", "taskman", "750", ""),
        ("/var/lib/taskman", "directory", "taskman", "taskman", "700", ""),
        ("/var/lock/taskman", "directory", "root", "root", "700", ""),
        ("/usr/local/lib/taskman", "directory", "root", "root", "755", ""),
        (f"{config.install_root}/lifecycle.lock", "regular file", "root", "root", "600", ""),
        # The executable can be an earlier supported helper and is refreshed
        # only under the Task 5 lifecycle lock.  Units and environment are
        # configuration, not versioned helper bytes: admit them only when
        # they exactly match the rendered supported assets.
        ("/etc/taskman/taskman-backup.env", "regular file", "root", "root", "600", _sha256(plan.backup_environment_content.encode("utf-8"))),
        ("/etc/systemd/system/taskman.service", "regular file", "root", "root", "644", assets["/etc/systemd/system/taskman.service"]),
        ("/etc/systemd/system/taskman-backup.service", "regular file", "root", "root", "644", assets["/etc/systemd/system/taskman-backup.service"]),
        ("/etc/systemd/system/taskman-backup.timer", "regular file", "root", "root", "644", assets["/etc/systemd/system/taskman-backup.timer"]),
        ("/usr/local/lib/taskman/taskman-backup.pyz", "regular file", "root", "root", "750", ""),
    )
    argv = ("sh", "-ceu", _RESOURCE_REUSE_SCRIPT, "taskman-resource-authority", *(value for row in roots for value in row))
    result = runner(argv, sudo=True)
    if getattr(result, "succeeded", False):
        return
    raise OpsError(
        ExitStatus.SAFETY,
        "resource-preflight",
        "existing managed resource authority is incompatible and will not be replaced",
        changed=False,
        next_action="inspect the existing managed resource authority before retrying",
    )


def validate_existing_authority(remote: object, inputs: ProvisioningInputs) -> Mapping[str, object]:
    """Keep resource and secret validation as one pre-mutation capability."""

    authority = validate_preconvergence_authority(remote, inputs)
    validate_existing_resource_authority(remote, inputs)
    validate_existing_credential_authority(remote, inputs)
    # Narrow legacy capability tests may replace the observer with a receipt.
    # Production's closed observer cannot return ``None``.
    if authority is None:
        return {}
    return {**authority, "scheduler_create": _scheduler_create_authority(authority)}


def _scheduler_create_authority(authority: Mapping[str, object]) -> tuple[str, ...]:
    """Translate the closed helper presence projection into pyinfra deltas.

    This is intentionally a create-only projection.  A present scheduler file
    can be old but supported; adopting it by rewriting it in pyinfra would
    bypass the lifecycle-lock pause/wait/refresh protocol.
    """

    resources = authority.get("scheduler_resources")
    if not isinstance(resources, Mapping) or set(resources) != set(_SCHEDULER_RESOURCE_NAMES):
        raise OpsError(
            ExitStatus.SAFETY,
            "resource-preflight",
            "pre-convergence scheduler resource authority is incomplete",
            changed=False,
            next_action="inspect the existing scheduler resources before retrying",
        )
    if not all(type(value) is bool for value in resources.values()):
        raise OpsError(
            ExitStatus.SAFETY,
            "resource-preflight",
            "pre-convergence scheduler resource authority is invalid",
            changed=False,
            next_action="inspect the existing scheduler resources before retrying",
        )
    return tuple(
        sorted(path for name, path in _SCHEDULER_RESOURCE_NAMES.items() if not resources[name])
    )


def validate_preconvergence_authority(remote: object, inputs: ProvisioningInputs) -> Mapping[str, object]:
    """Validate record and PostgreSQL authority through the read-only helper."""

    if not isinstance(inputs, ProvisioningInputs):
        raise TypeError("preconvergence authority requires provisioning inputs")
    from .workflows.helper import request, result_error, run_request

    plan = build_postgresql_plan(inputs.config)
    authority_request = request(
        "provision_authority",
        inputs.config,
        parameters={
            "database": {
                "host": inputs.config.database_host,
                "port": inputs.config.database_port,
                "role": inputs.config.database_role,
                "name": inputs.config.database_name,
            },
            "postgres_package_track": plan.package_track,
        },
    )
    result = run_request(remote, authority_request)
    if result.outcome != "succeeded":
        raise result_error(result)
    state = result.state
    # This is the exact bounded projection returned by
    # ``discover.provision_authority``.  Keep this schema deliberately closed:
    # accepting arbitrary helper keys would turn an incomplete privileged
    # observation into authority the controller never reviewed.
    required = {
        "authority",
        "initial_database_empty",
        "selected_release_id",
        "last_successful_selection_id",
        "last_successful_selection",
        "previous_successful_selection",
        "applied_migrations",
        "service_state",
        "database_state",
        "backup_protections",
        "independently_held_backup_ids",
        "backup_protection_sha256",
        "scheduled_backup_sha256",
        "backup_timer_enabled",
        "backup_timer_state",
        "downgrade_baseline_sha256",
        "installed_release_count",
        "installed_release_sha256",
        "scheduler_resources",
    }
    if not isinstance(state, Mapping) or set(state) != required or state["authority"] != "validated":
        raise OpsError(
            ExitStatus.SAFETY,
            "authority-preflight",
            "pre-convergence authority observation returned invalid evidence",
            changed=False,
            next_action="inspect the existing record and PostgreSQL authority before retrying",
        )
    try:
        if state["selected_release_id"] is not None and not isinstance(state["selected_release_id"], str):
            raise ValueError
        if state["last_successful_selection_id"] is not None and not isinstance(state["last_successful_selection_id"], str):
            raise ValueError
        if not isinstance(state["applied_migrations"], tuple) or any(type(item) is not int for item in state["applied_migrations"]):
            raise ValueError
        if type(state["initial_database_empty"]) is not bool:
            raise ValueError
        if state["service_state"] not in {"running", "stopped", "unknown"} or state["database_state"] not in {"ready", "absent"}:
            raise ValueError
        if not isinstance(state["backup_protections"], tuple) or not isinstance(state["independently_held_backup_ids"], tuple):
            raise ValueError
        if type(state["installed_release_count"]) is not int or state["installed_release_count"] < 0:
            raise ValueError
        for key in ("backup_protection_sha256", "scheduled_backup_sha256", "downgrade_baseline_sha256", "installed_release_sha256"):
            value = state[key]
            if value is not None and (type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None):
                raise ValueError
        if type(state["backup_timer_enabled"]) is not bool or state["backup_timer_state"] not in {"active", "inactive"}:
            raise ValueError
        resources = state["scheduler_resources"]
        if (
            not isinstance(resources, Mapping)
            or set(resources) != set(_SCHEDULER_RESOURCE_NAMES)
            or not all(type(value) is bool for value in resources.values())
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise OpsError(
            ExitStatus.SAFETY, "authority-preflight",
            "pre-convergence authority observation returned invalid evidence",
            changed=False,
            next_action="inspect the existing record and PostgreSQL authority before retrying",
        ) from None
    return dict(state)


def _systemd_asset_sha256(asset: object) -> str:
    content = getattr(asset, "binary_content", None)
    if content is None:
        text = getattr(asset, "content", None)
        if text is not None:
            content = text.encode("utf-8")
        else:
            source = getattr(asset, "source", None)
            content = source.read_bytes() if source is not None else None
    if not isinstance(content, bytes):
        raise TypeError("systemd asset has no exact bytes")
    return _sha256(content)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


__all__ = [
    "ProvisioningInputs",
    "converge_provisioning",
    "taskman_provisioning",
    "validate_existing_authority",
    "validate_preconvergence_authority",
    "validate_existing_credential_authority",
    "validate_existing_resource_authority",
]
