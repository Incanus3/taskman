"""The single programmatic pyinfra deploy for stable host convergence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

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
if [ ! -e "$path" ] && [ ! -L "$path" ]; then exit 0; fi
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


@dataclass(frozen=True)
class ProvisioningInputs:
    """Validated non-secret configuration plus already-rendered secret bytes."""

    config: EnvironmentConfig
    caddy_plan: CaddyPlan
    runtime_environment: bytes
    pgpass: bytes
    role_password_input: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.config, EnvironmentConfig):
            raise TypeError("provisioning inputs require an environment configuration")
        if not isinstance(self.caddy_plan, CaddyPlan):
            raise TypeError("provisioning inputs require a preconfirmed Caddy plan")
        for field_name in ("runtime_environment", "pgpass", "role_password_input"):
            value = getattr(self, field_name)
            if not isinstance(value, bytes) or not value:
                raise ValueError(f"{field_name} must be non-empty bytes")


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
        ("/etc/taskman/taskman-backup.env", "regular file", "root", "root", "600", _sha256(plan.backup_environment_content.encode("utf-8"))),
        ("/etc/systemd/system/taskman.service", "regular file", "root", "root", "644", assets["/etc/systemd/system/taskman.service"]),
        ("/etc/systemd/system/taskman-backup.service", "regular file", "root", "root", "644", assets["/etc/systemd/system/taskman-backup.service"]),
        ("/etc/systemd/system/taskman-backup.timer", "regular file", "root", "root", "644", assets["/etc/systemd/system/taskman-backup.timer"]),
        ("/usr/local/lib/taskman/taskman-backup.pyz", "regular file", "root", "root", "750", assets["/usr/local/lib/taskman/taskman-backup.pyz"]),
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


def validate_existing_authority(remote: object, inputs: ProvisioningInputs) -> None:
    """Keep resource and secret validation as one pre-mutation capability."""

    validate_preconvergence_authority(remote, inputs)
    validate_existing_resource_authority(remote, inputs)
    validate_existing_credential_authority(remote, inputs)


def validate_preconvergence_authority(remote: object, inputs: ProvisioningInputs) -> None:
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
    if result.state != {"authority": "validated"}:
        raise OpsError(
            ExitStatus.SAFETY,
            "authority-preflight",
            "pre-convergence authority observation returned invalid evidence",
            changed=False,
            next_action="inspect the existing record and PostgreSQL authority before retrying",
        )


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
