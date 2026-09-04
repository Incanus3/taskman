"""SOPS decryption and protected runtime-file rendering.

Plaintext secret material is intentionally short lived here: SOPS stdout is
captured in mutable buffers, parsed into a frozen model, registered with the
controller redactor, and wiped before the function returns.  The returned
``SecretConfig`` is suitable for a caller that must install a protected file,
but has no value-bearing representation or mapping protocol of its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import subprocess
from typing import Any, Callable, ClassVar
from urllib.parse import quote

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import ENVIRONMENTS_DIR, EnvironmentConfig, validate_environment_name
from .errors import ExitStatus, OpsError
from .output import register_secret


SECRETS_DIR = ENVIRONMENTS_DIR


def _secret_error(message: str = "unable to decrypt or validate deployment secrets") -> OpsError:
    return OpsError(
        status=ExitStatus.SECRET,
        stage="secrets",
        message=message,
        changed=False,
        next_action="check the external age identity and encrypted environment file",
    )


class SecretConfig(BaseModel):
    """Validated deployment credentials with deliberately non-sensitive repr."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=False,
        hide_input_in_errors=True,
    )

    database_password: str = Field(
        validation_alias=AliasChoices(
            "database_password",
            "postgres_password",
            "database_role_password",
            "db_password",
            "DATABASE_PASSWORD",
        )
    )
    secret_key_base: str = Field(validation_alias=AliasChoices("secret_key_base", "SECRET_KEY_BASE"))
    ash_authentication_token_signing_secret: str = Field(
        validation_alias=AliasChoices(
            "ash_authentication_token_signing_secret",
            "ash_signing_secret",
            "token_signing_secret",
            "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET",
        )
    )
    resend_api_key: str = Field(
        validation_alias=AliasChoices("resend_api_key", "resend_key", "RESEND_API_KEY")
    )

    _secret_fields: ClassVar[tuple[str, ...]] = (
        "database_password",
        "secret_key_base",
        "ash_authentication_token_signing_secret",
        "resend_api_key",
    )

    def __init__(self, **data: Any) -> None:
        validation_failed = False
        try:
            super().__init__(**data)
        except ValidationError:
            # Do not let Pydantic retain the input mapping in a structured
            # ValidationError.  Clear the local kwargs mapping and raise the
            # controller's fixed, secret-free boundary error after leaving
            # the ``except`` block so no exception context is attached.
            data.clear()
            validation_failed = True
        if validation_failed:
            raise _secret_error("invalid deployment secrets")

        # Directly constructed configs are just as sensitive as SOPS output.
        # Register them before handing the object to any report/exception
        # boundary; ``decrypt_secrets`` also registers parsed values earlier
        # so validation failures are covered.
        for field_name in self._secret_fields:
            register_secret(getattr(self, field_name))

    def __getattribute__(self, name: str) -> Any:
        if name == "__dict__":
            # BaseModel stores validated fields in its instance dictionary.
            # Expose only shape-preserving redaction markers to generic
            # introspection (``vars(config)`` and ``config.__dict__``), while
            # normal named field access continues to use Pydantic's storage.
            return {field_name: "[REDACTED]" for field_name in self._secret_fields}
        return super().__getattribute__(name)

    @field_validator(
        "database_password",
        "secret_key_base",
        "ash_authentication_token_signing_secret",
        "resend_api_key",
    )
    @classmethod
    def validate_secret_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value or not value.strip():
            raise ValueError("secret values must be non-empty")
        # Newlines and NUL bytes could inject an environment assignment or
        # corrupt pgpass; reject them before any protected file is rendered.
        if any(char in value for char in "\x00\r\n"):
            raise ValueError("secret values must be single-line")
        return value

    @field_validator("secret_key_base", "ash_authentication_token_signing_secret")
    @classmethod
    def validate_signing_secret_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) < 64:
            raise ValueError("signing secrets must be at least 64 bytes")
        return value

    @model_validator(mode="after")
    def validate_signing_secrets_distinct(self) -> SecretConfig:
        if self.secret_key_base == self.ash_authentication_token_signing_secret:
            raise ValueError("signing secrets must be distinct")
        return self

    @property
    def ash_signing_secret(self) -> str:
        return self.ash_authentication_token_signing_secret

    @property
    def token_signing_secret(self) -> str:
        return self.ash_authentication_token_signing_secret

    def __repr__(self) -> str:
        return "SecretConfig(<protected values>)"

    def __str__(self) -> str:
        return "SecretConfig(<protected values>)"

    def __iter__(self):
        # BaseModel's normal iterator is a mapping-like value exposure.  A
        # caller must use the named attributes explicitly when installing a
        # protected file, never ``dict(secret_config)``.
        raise TypeError("SecretConfig does not expose a mapping interface")

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise TypeError("SecretConfig does not expose a mapping interface")

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        raise TypeError("SecretConfig does not expose a mapping interface")

    def dict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise TypeError("SecretConfig does not expose a mapping interface")

    def json(self, *args: Any, **kwargs: Any) -> str:
        raise TypeError("SecretConfig does not expose a mapping interface")


def _register_strings(value: object) -> None:
    """Register all scalar strings before validation can construct errors."""

    if isinstance(value, str):
        register_secret(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _register_strings(key)
            _register_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            _register_strings(item)


def _as_buffer(value: object) -> bytearray:
    if value is None:
        return bytearray()
    if isinstance(value, bytearray):
        return value
    if isinstance(value, bytes):
        return bytearray(value)
    if isinstance(value, str):
        return bytearray(value.encode("utf-8", errors="replace"))
    if isinstance(value, memoryview):
        return bytearray(value.tobytes())
    return bytearray(str(value).encode("utf-8", errors="replace"))


def _wipe_buffer(buffer: bytearray) -> None:
    buffer[:] = b"\x00" * len(buffer)
    buffer.clear()


def _result_parts(result: object) -> tuple[int, object, object]:
    if isinstance(result, (bytes, bytearray, memoryview, str)):
        return 0, result, b""
    if isinstance(result, subprocess.CompletedProcess):
        return int(result.returncode), result.stdout, result.stderr
    if isinstance(result, Mapping):
        return int(result.get("returncode", result.get("code", 0))), result.get("stdout"), result.get("stderr")
    if isinstance(result, tuple):
        if len(result) == 3:
            return int(result[0]), result[1], result[2]
        if len(result) == 2:
            return 0, result[0], result[1]
        if len(result) == 1:
            return 0, result[0], b""
    return int(getattr(result, "returncode", getattr(result, "code", 0))), getattr(result, "stdout", b""), getattr(
        result, "stderr", b""
    )


def _call_runner(runner: object, argv: list[str]) -> object:
    if runner is None:
        return subprocess.run(argv, check=False, capture_output=True)
    if callable(runner):
        return runner(argv)
    run = getattr(runner, "run", None)
    if callable(run):
        try:
            return run(argv)
        except TypeError:
            return run(argv, capture_output=True, check=False)
    raise TypeError("runner must be callable or expose run()")


def decrypt_secrets(
    name: str,
    runner: Callable[[list[str]], object] | object | None = None,
) -> SecretConfig:
    """Decrypt one SOPS file using stdout only and return validated secrets.

    The external age identity is resolved by SOPS from its normal environment;
    this function never receives or persists a private identity itself.
    """

    safe_name = validate_environment_name(name)
    root = Path(SECRETS_DIR)
    path = root / f"{safe_name}.secrets.sops.yaml"
    if path.parent.resolve() != root.resolve() or not path.is_file():
        raise _secret_error("encrypted environment secrets are unavailable")

    argv = ["sops", "decrypt", "--output-type", "yaml", str(path)]
    result: object | None = None
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    payload: object | None = None
    try:
        try:
            result = _call_runner(runner, argv)
            returncode, stdout, stderr = _result_parts(result)
            stdout_buffer = _as_buffer(stdout)
            stderr_buffer = _as_buffer(stderr)
        except Exception:
            raise _secret_error() from None

        if returncode != 0 or not stdout_buffer:
            raise _secret_error()
        try:
            text = bytes(stdout_buffer).decode("utf-8")
            payload = yaml.safe_load(text)
            _register_strings(payload)
            if not isinstance(payload, Mapping):
                raise ValueError("SOPS output must be a mapping")
            return SecretConfig.model_validate(payload)
        except (UnicodeError, TypeError, ValueError, yaml.YAMLError, ValidationError, OpsError):
            # Never include parser details: malformed SOPS data is untrusted
            # and may contain a credential canary.
            raise _secret_error() from None
    finally:
        # Wipe mutable captures and clear fields on common result containers.
        # A CompletedProcess is only a transport object; replacing its output
        # attributes prevents an accidental caller-held result from retaining
        # plaintext after this function returns.
        _wipe_buffer(stdout_buffer)
        _wipe_buffer(stderr_buffer)
        if result is not None:
            if isinstance(result, dict):
                for attribute in ("stdout", "stderr"):
                    try:
                        raw = result.get(attribute)
                        if isinstance(raw, bytearray):
                            _wipe_buffer(raw)
                        result[attribute] = b""
                    except Exception:
                        pass
            for attribute in ("stdout", "stderr"):
                try:
                    raw = getattr(result, attribute)
                    if isinstance(raw, bytearray):
                        _wipe_buffer(raw)
                    else:
                        setattr(result, attribute, b"")
                except Exception:
                    pass
        payload = None


def _systemd_quote(value: str) -> str:
    if any(char in value for char in "\x00\r\n"):
        raise ValueError("runtime values must be single-line")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def _database_url(config: EnvironmentConfig, secrets: SecretConfig) -> str:
    host = config.database_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return (
        "ecto://"
        + quote(config.database_role, safe="")
        + ":"
        + quote(secrets.database_password, safe="")
        + "@"
        + host
        + ":"
        + str(config.database_port)
        + "/"
        + quote(config.database_name, safe="")
    )


def render_runtime_environment(config: EnvironmentConfig, secrets: SecretConfig) -> bytes:
    """Render the complete root-owned systemd EnvironmentFile in memory."""

    values: list[tuple[str, str]] = [
        ("DATABASE_URL", _database_url(config, secrets)),
        ("SECRET_KEY_BASE", secrets.secret_key_base),
        (
            "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET",
            secrets.ash_authentication_token_signing_secret,
        ),
        ("PHX_HOST", config.public_hostname),
        ("RESEND_API_KEY", secrets.resend_api_key),
        ("MAIL_FROM", config.mail_from),
        ("PORT", str(config.application_port)),
        ("POOL_SIZE", str(config.pool_size)),
        ("PHX_SERVER", "true"),
    ]
    if config.dns_cluster_query is not None:
        values.append(("DNS_CLUSTER_QUERY", config.dns_cluster_query))
    return ("\n".join(f"{key}={_systemd_quote(value)}" for key, value in values) + "\n").encode("utf-8")


def _pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("\n", "\\n")


def render_pgpass(config: EnvironmentConfig, secrets: SecretConfig) -> bytes:
    """Render one PostgreSQL ``.pgpass`` line without touching disk."""

    line = ":".join(
        (
            _pgpass_escape(config.database_host),
            str(config.database_port),
            _pgpass_escape(config.database_name),
            _pgpass_escape(config.database_role),
            _pgpass_escape(secrets.database_password),
        )
    )
    return (line + "\n").encode("utf-8")


render_pgpass_file = render_pgpass
render_runtime_pgpass = render_pgpass


__all__ = [
    "SECRETS_DIR",
    "SecretConfig",
    "decrypt_secrets",
    "render_pgpass",
    "render_pgpass_file",
    "render_runtime_pgpass",
    "render_runtime_environment",
]
