"""Read-only preflight shared by existing-host mutating workflows."""

from __future__ import annotations

from collections.abc import Callable

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..host.facts import HostFacts, validate_operational_host
from ..remote import Remote


_RUNTIME_ENVIRONMENT = "/etc/taskman/taskman.env"
_PGPASS = "/etc/taskman/pgpass"
_REQUIRED_RUNTIME_KEYS = (
    "DATABASE_URL",
    "SECRET_KEY_BASE",
    "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET",
    "PHX_HOST",
    "RESEND_API_KEY",
    "MAIL_FROM",
    "PORT",
    "POOL_SIZE",
    "PHX_SERVER",
)
_RUNTIME_PREFLIGHT = r'''set -eu
path=$1
shift
test -f "$path" && test ! -L "$path"
test "$(stat -c '%U:%G:%a' -- "$path")" = root:root:600
for required do
  awk -F= -v required="$required" '
    $1 == required { count += 1; if (length($0) <= length(required) + 1) empty = 1 }
    END { exit count == 1 && !empty ? 0 : 1 }
  ' "$path"
done
command -v python3 >/dev/null 2>&1
'''
_DATABASE_PREFLIGHT = r'''set -eu
database_host=$1; database_port=$2; database_role=$3; database_name=$4; backup_root=$5; pgpass=$6
test -f "$pgpass" && test ! -L "$pgpass"
test "$(stat -c '%U:%G:%a' -- "$pgpass")" = root:root:600
export PGPASSFILE=$pgpass
psql --no-psqlrc --host "$database_host" --port "$database_port" --username "$database_role" --dbname "$database_name" --tuples-only --no-align --command 'SELECT 1' >/dev/null 2>&1
database_bytes=$(psql --no-psqlrc --host "$database_host" --port "$database_port" --username "$database_role" --dbname "$database_name" --tuples-only --no-align --command 'SELECT pg_database_size(current_database())' 2>/dev/null)
available_bytes=$(df -B1 --output=avail "$backup_root" 2>/dev/null | awk 'NR > 1 && $1 ~ /^[0-9]+$/ { value=$1 } END { print value }')
case "$database_bytes:$available_bytes" in *[!0-9:]*|*::*|:*) exit 1;; esac
test "$database_bytes" -gt 0 && test "$database_bytes" -le 900000000000000000
margin=$(( (database_bytes + 9) / 10 ))
test "$margin" -ge 67108864 || margin=67108864
required=$(( database_bytes + margin ))
test "$available_bytes" -ge "$required"
'''


HostValidator = Callable[[Remote, EnvironmentConfig], HostFacts | object]


def validate_operational_preflight(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    host_validator: HostValidator | None = None,
) -> HostFacts | object:
    """Validate host, secret-file shape, database health, and backup capacity.

    The commands intentionally emit no values. They execute before lifecycle
    planning, staging, confirmation, or any host mutation.
    """

    if not isinstance(config, EnvironmentConfig):
        raise TypeError("operational preflight requires an environment configuration")
    facts = (host_validator or validate_operational_host)(remote, config)
    runtime = remote.run(
        (
            "sh",
            "-ceu",
            _RUNTIME_PREFLIGHT,
            "taskman-runtime-preflight",
            _RUNTIME_ENVIRONMENT,
            *_REQUIRED_RUNTIME_KEYS,
        ),
        sudo=True,
        stdin=None,
        sensitive=True,
    )
    if not runtime.succeeded:
        raise _preflight(
            "runtime environment ownership, mode, required keys, or distro Python is invalid"
        )
    database = remote.run(
        (
            "sh",
            "-ceu",
            _DATABASE_PREFLIGHT,
            "taskman-database-preflight",
            config.database_host,
            str(config.database_port),
            config.database_role,
            config.database_name,
            config.backup_root.as_posix(),
            _PGPASS,
        ),
        sudo=True,
        stdin=None,
        sensitive=True,
    )
    if not database.succeeded:
        raise _preflight("managed database health or backup capacity preflight failed")
    return facts


def _preflight(message: str) -> OpsError:
    return OpsError(
        ExitStatus.REMOTE_PREFLIGHT,
        "preflight",
        message,
        changed=False,
        next_action="correct the reported existing-host prerequisite before retrying",
    )


__all__ = ["validate_operational_preflight"]
