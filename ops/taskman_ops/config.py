"""Validated, non-secret deployment environment configuration.

The controller treats an environment file as untrusted input.  This module
keeps the accepted shape deliberately small and validates values before they
are used to construct a path, a remote command, or a runtime URL.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path, PurePosixPath
import re
from typing import Any, ClassVar

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from .errors import ExitStatus, OpsError


ENVIRONMENTS_DIR = Path(__file__).resolve().parents[1] / "environments"

ENVIRONMENT_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
POSTGRES_IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
HOST_KEY_FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}={0,1}\Z")
# Short aliases keep the validation vocabulary discoverable to later
# capabilities without introducing duplicate schema fields.
ENV_NAME_RE = ENVIRONMENT_NAME_RE
DB_IDENTIFIER_RE = POSTGRES_IDENTIFIER_RE
HOSTNAME_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+\Z"
)

_RESERVED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "broadcasthost",
    "local",
    "test",
    "invalid",
    "example",
    "host.docker.internal",
    "gateway.docker.internal",
}
_RESERVED_SUFFIXES = (
    ".localhost",
    ".local",
    ".test",
    ".invalid",
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".home.arpa",
    ".onion",
)
_UNSAFE_ROOTS = {
    PurePosixPath("/"),
    PurePosixPath("/tmp"),
    PurePosixPath("/var/tmp"),
    PurePosixPath("/home"),
    PurePosixPath("/root"),
    PurePosixPath("/etc"),
    PurePosixPath("/usr"),
    PurePosixPath("/var"),
    PurePosixPath("/opt"),
}


def _invalid(message: str, *, cause: Exception | None = None) -> OpsError:
    """Construct the stable configuration error without echoing input."""

    error = OpsError(
        status=ExitStatus.INVALID,
        stage="configuration",
        message=message,
        changed=False,
        next_action="correct the environment YAML and retry",
    )
    if cause is not None:
        error.__cause__ = None
    return error


def validate_environment_name(name: str) -> str:
    """Validate the name used to select an environment file.

    This check runs before joining the name to ``ENVIRONMENTS_DIR``.  It is
    intentionally stricter than a filesystem basename and therefore rejects
    traversal, absolute paths, separators, and shell-looking names.
    """

    if not isinstance(name, str) or ENVIRONMENT_NAME_RE.fullmatch(name) is None:
        raise _invalid("invalid environment name")
    return name


def _normalise_hostname(value: object, *, require_fqdn: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("hostname must be a string")
    hostname = value.strip().lower().rstrip(".")
    if not hostname or len(hostname) > 253 or "\n" in hostname or "\r" in hostname:
        raise ValueError("invalid hostname")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        if require_fqdn:
            raise ValueError("public hostname must be a DNS name")
        if (
            address.is_loopback
            or address.is_unspecified
            or address.is_multicast
            or address.is_link_local
            or address.is_reserved
        ):
            raise ValueError("local or reserved hostname")
        return hostname

    if hostname in _RESERVED_HOSTNAMES or hostname.endswith(_RESERVED_SUFFIXES):
        raise ValueError("local or reserved hostname")
    labels = hostname.split(".")
    if any(HOSTNAME_LABEL_RE.fullmatch(label) is None for label in labels):
        raise ValueError("invalid hostname")
    if require_fqdn and (len(labels) < 2 or not any(char.isalpha() for char in labels[-1])):
        raise ValueError("public hostname must be a DNS name")
    return hostname


def _normalise_ip(value: object, *, version: int) -> str:
    if not isinstance(value, str):
        raise ValueError("IP address must be a string")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("invalid IP address") from exc
    if address.version != version:
        raise ValueError("IP address has the wrong family")
    if (
        address.is_loopback
        or address.is_unspecified
        or address.is_multicast
        or address.is_link_local
        or address.is_reserved
    ):
        raise ValueError("IP address must not be local or reserved")
    return str(address)


def _normalise_root(value: object, *, label: str) -> PurePosixPath:
    if isinstance(value, PurePosixPath):
        raw = value.as_posix()
    elif isinstance(value, str):
        raw = value
    else:
        raise ValueError(f"{label} must be an absolute POSIX path")
    if not raw.startswith("/") or "\x00" in raw or "\\" in raw:
        raise ValueError(f"{label} must be an absolute POSIX path")
    components = raw.split("/")
    if any(component in {"", ".", ".."} for component in components[1:]):
        raise ValueError(f"{label} contains an unsafe path component")
    path = PurePosixPath(raw)
    unsafe_prefix = path.parts[1] if len(path.parts) > 1 else ""
    if (
        path in _UNSAFE_ROOTS
        or unsafe_prefix in {"tmp", "home", "root", "etc", "usr"}
        or path.parts[:3] == ("/", "var", "tmp")
        or len(path.parts) < 3
    ):
        raise ValueError(f"{label} is too broad for managed state")
    return path


def _normalise_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or POSTGRES_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is not a valid PostgreSQL identifier")
    return value


class EnvironmentConfig(BaseModel):
    """Frozen, strict configuration for one supported Taskman host."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=False,
        validate_default=True,
    )

    # ``name`` is derived from the filename by :func:`load_environment`; it
    # remains optional for callers constructing a validated config directly.
    name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("name", "environment", "environment_name"),
    )
    ssh_host: str = Field(
        validation_alias=AliasChoices("ssh_host", "ssh_hostname", "hostname", "host"),
    )
    ssh_port: StrictInt = Field(
        validation_alias=AliasChoices("ssh_port", "port", "administrator_port"),
    )
    ssh_user: str = Field(
        validation_alias=AliasChoices("ssh_user", "administrator_user", "admin_user", "user"),
    )
    host_key_fingerprint: str = Field(
        validation_alias=AliasChoices(
            "host_key_fingerprint", "pinned_host_key_fingerprint", "ssh_host_key_fingerprint"
        ),
    )
    public_hostname: str = Field(
        validation_alias=AliasChoices("public_hostname", "public_host", "taskman_hostname"),
    )
    public_ipv4: str = Field(
        validation_alias=AliasChoices("public_ipv4", "expected_public_ipv4", "public_ip"),
    )
    public_ipv6: str | None = Field(
        default=None,
        validation_alias=AliasChoices("public_ipv6", "expected_public_ipv6"),
    )
    target_os: str = Field(
        validation_alias=AliasChoices("target_os", "os", "ubuntu_release", "expected_ubuntu_release"),
    )
    architecture: str = Field(
        validation_alias=AliasChoices("architecture", "arch", "cpu_architecture"),
    )
    application_port: StrictInt = Field(
        validation_alias=AliasChoices("application_port", "app_port", "phoenix_port"),
    )
    distribution_port: StrictInt = Field(
        validation_alias=AliasChoices("distribution_port", "erlang_distribution_port", "epmd_port"),
    )
    database_name: str = Field(
        validation_alias=AliasChoices("database_name", "database", "db_name"),
    )
    database_role: str = Field(
        validation_alias=AliasChoices("database_role", "database_user", "db_role", "db_user"),
    )
    postgres_package_track: str | None = Field(
        default=None,
        validation_alias=AliasChoices("postgres_package_track", "postgres_track"),
    )
    database_host: str = "127.0.0.1"
    database_port: StrictInt = Field(
        default=5432,
        validation_alias=AliasChoices("database_port", "postgres_port", "db_port"),
    )
    managed_root: PurePosixPath = PurePosixPath("/opt/taskman")
    release_root: PurePosixPath = PurePosixPath("/opt/taskman/releases")
    deployment_root: PurePosixPath = PurePosixPath("/opt/taskman/deployments")
    backup_root: PurePosixPath = PurePosixPath("/var/backups/taskman")
    backup_schedule: str = "*-*-* 02:15:00"
    backup_retention: StrictInt = 14
    release_retention: StrictInt = 3
    readiness_timeout: StrictInt = 30
    connection_timeout: StrictInt = 10
    pool_size: StrictInt = 10
    mail_from: str = Field(validation_alias=AliasChoices("mail_from", "sender_email"))
    dns_cluster_query: str | None = None

    _path_fields: ClassVar[tuple[str, ...]] = (
        "managed_root",
        "release_root",
        "deployment_root",
        "backup_root",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_environment_name(value)

    @field_validator("ssh_host")
    @classmethod
    def validate_ssh_host(cls, value: str) -> str:
        return _normalise_hostname(value)

    @field_validator("public_hostname")
    @classmethod
    def validate_public_hostname(cls, value: str) -> str:
        return _normalise_hostname(value, require_fqdn=True)

    @field_validator("public_ipv4")
    @classmethod
    def validate_public_ipv4(cls, value: str) -> str:
        return _normalise_ip(value, version=4)

    @field_validator("public_ipv6")
    @classmethod
    def validate_public_ipv6(cls, value: str | None) -> str | None:
        return None if value is None else _normalise_ip(value, version=6)

    @field_validator("target_os")
    @classmethod
    def validate_target_os(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("target OS must be ubuntu26.04")
        normalised = value.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        if normalised not in {"ubuntu26.04", "ubuntu2604", "ubuntu26.04lts", "ubuntu2604lts"}:
            raise ValueError("unsupported target OS")
        return "ubuntu26.04"

    @field_validator("architecture")
    @classmethod
    def validate_architecture(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("unsupported architecture")
        normalised = value.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        if normalised not in {"amd64", "x8664", "x64", "em64t", "intel64"}:
            raise ValueError("unsupported architecture")
        return "amd64"

    @field_validator("ssh_user")
    @classmethod
    def validate_ssh_user(cls, value: str) -> str:
        if not isinstance(value, str) or USER_RE.fullmatch(value) is None:
            raise ValueError("invalid administrator user")
        return value

    @field_validator("host_key_fingerprint")
    @classmethod
    def validate_host_key_fingerprint(cls, value: str) -> str:
        if not isinstance(value, str) or HOST_KEY_FINGERPRINT_RE.fullmatch(value) is None:
            raise ValueError("invalid pinned host-key fingerprint")
        return value

    @field_validator("database_name")
    @classmethod
    def validate_database_name(cls, value: str) -> str:
        return _normalise_identifier(value, label="database name")

    @field_validator("database_role")
    @classmethod
    def validate_database_role(cls, value: str) -> str:
        return _normalise_identifier(value, label="database role")

    @field_validator("database_host")
    @classmethod
    def validate_database_host(cls, value: str) -> str:
        if not isinstance(value, str) or value.strip() not in {"127.0.0.1", "::1"}:
            raise ValueError("database host must remain loopback-only")
        return value.strip()

    @field_validator(*_path_fields, mode="before")
    @classmethod
    def validate_paths(cls, value: object, info: Any) -> PurePosixPath:
        return _normalise_root(value, label=info.field_name)

    @field_validator("backup_schedule")
    @classmethod
    def validate_backup_schedule(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or any(char in value for char in "\r\n\x00"):
            raise ValueError("backup schedule must be non-empty and single-line")
        return value

    @field_validator("mail_from")
    @classmethod
    def validate_mail_from(cls, value: str) -> str:
        if not isinstance(value, str) or EMAIL_RE.fullmatch(value) is None:
            raise ValueError("mail_from must be one email address")
        return value.lower()

    @field_validator("postgres_package_track")
    @classmethod
    def validate_postgres_package_track(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,2}", value) is None:
            raise ValueError("invalid PostgreSQL package track")
        return value

    @field_validator("dns_cluster_query")
    @classmethod
    def validate_dns_cluster_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(char in value for char in "\r\n\x00") or len(value) > 253:
            raise ValueError("invalid DNS cluster query")
        return value

    @field_validator("ssh_port", "application_port", "distribution_port", "database_port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator("backup_retention", "release_retention", "readiness_timeout", "connection_timeout", "pool_size")
    @classmethod
    def validate_positive(cls, value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("value must be positive")
        return value

    @model_validator(mode="after")
    def validate_cross_field_invariants(self) -> EnvironmentConfig:
        ports = {
            "ssh_port": self.ssh_port,
            "application_port": self.application_port,
            "distribution_port": self.distribution_port,
            "database_port": self.database_port,
        }
        if len(set(ports.values())) != len(ports):
            raise ValueError("service ports must not overlap")
        if any(port in {80, 443} for port in ports.values()):
            raise ValueError("public proxy ports are reserved")

        if not self.release_root.is_relative_to(self.managed_root) or self.release_root == self.managed_root:
            raise ValueError("release root must be below managed root")
        if not self.deployment_root.is_relative_to(self.managed_root) or self.deployment_root == self.managed_root:
            raise ValueError("deployment root must be below managed root")
        if self.release_root == self.deployment_root:
            raise ValueError("release and deployment roots must be distinct")

        if self.database_host == "::1" and self.public_ipv6 is None:
            # IPv6 loopback is valid on a host only when explicitly described;
            # default configs use 127.0.0.1 and do not silently switch family.
            raise ValueError("IPv6 database host requires explicit public IPv6 configuration")
        return self

    # Compatibility aliases used by future host/service capabilities.  They
    # are properties rather than duplicate model fields, so ``extra=forbid``
    # and the serialized schema remain unambiguous.
    @property
    def environment(self) -> str | None:
        return self.name

    @property
    def ssh_hostname(self) -> str:
        return self.ssh_host

    @property
    def administrator_user(self) -> str:
        return self.ssh_user

    @property
    def pinned_host_key_fingerprint(self) -> str:
        return self.host_key_fingerprint

    @property
    def expected_public_ipv4(self) -> str:
        return self.public_ipv4

    @property
    def expected_public_ipv6(self) -> str | None:
        return self.public_ipv6

    @property
    def app_port(self) -> int:
        return self.application_port

    @property
    def erlang_distribution_port(self) -> int:
        return self.distribution_port

    @property
    def database(self) -> str:
        return self.database_name

    @property
    def database_user(self) -> str:
        return self.database_role

    @property
    def target_architecture(self) -> str:
        return self.architecture

    @property
    def os_release(self) -> str:
        return self.target_os

    @property
    def managed_release_root(self) -> PurePosixPath:
        return self.release_root

    @property
    def managed_backup_root(self) -> PurePosixPath:
        return self.backup_root

    @property
    def managed_deployment_root(self) -> PurePosixPath:
        return self.deployment_root


def load_environment(name: str) -> EnvironmentConfig:
    """Load and validate ``ops/environments/<name>.yaml``.

    Validation happens before path construction and all parse/validation
    failures are translated to the controller's stable status ``2``.
    """

    safe_name = validate_environment_name(name)
    root = Path(ENVIRONMENTS_DIR)
    path = root / f"{safe_name}.yaml"
    try:
        if path.parent.resolve() != root.resolve() or not path.is_file():
            raise FileNotFoundError(path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("environment YAML must contain a mapping")
        supplied_name = payload.get("name", payload.get("environment", payload.get("environment_name")))
        if supplied_name is not None and supplied_name != safe_name:
            raise ValueError("environment name does not match filename")
        if supplied_name is None:
            payload = {"name": safe_name, **payload}
        return EnvironmentConfig.model_validate(payload)
    except OpsError:
        raise
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        # Do not chain parser details: a malformed file could contain a
        # canary or an accidental credential and must remain secret-free.
        raise _invalid("invalid environment configuration") from None


__all__ = [
    "ENVIRONMENTS_DIR",
    "ENVIRONMENT_NAME_RE",
    "POSTGRES_IDENTIFIER_RE",
    "EnvironmentConfig",
    "load_environment",
    "validate_environment_name",
]
