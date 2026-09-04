from __future__ import annotations

from dataclasses import dataclass, field
import copy
from pathlib import Path
import subprocess

import pytest
import yaml
from pydantic import ValidationError

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import clear_secrets, redact
from taskman_ops.secrets import (
    SecretConfig,
    decrypt_secrets,
    render_pgpass,
    render_runtime_environment,
)

from test_config import valid_environment


@pytest.fixture(autouse=True)
def no_registered_secrets_between_tests() -> None:
    clear_secrets()
    yield
    clear_secrets()


def valid_secrets(**overrides: object) -> dict[str, str]:
    values = {
        "database_password": "db password: canary \\ with quote",
        "secret_key_base": "secret-key-base-canary-" + "a" * 64,
        "ash_authentication_token_signing_secret": "ash-token-canary-" + "b" * 64,
        "resend_api_key": "re_canary_resend_key",
    }
    values.update(overrides)
    return values


def test_secret_config_requires_all_values_and_hides_them_from_repr_and_mappings() -> None:
    values = valid_secrets()
    secrets = SecretConfig.model_validate(values)

    assert secrets.database_password == values["database_password"]
    assert secrets.secret_key_base != secrets.ash_authentication_token_signing_secret
    assert len(secrets.secret_key_base.encode()) >= 64
    assert len(secrets.ash_authentication_token_signing_secret.encode()) >= 64
    rendered = repr(secrets) + str(secrets)
    assert all(value not in rendered for value in values.values())
    assert all(value not in repr(vars(secrets)) for value in values.values())
    assert all(value not in repr(secrets.__dict__) for value in values.values())
    with pytest.raises(TypeError):
        iter(secrets)
    with pytest.raises(TypeError):
        secrets.model_dump()


def test_secret_equality_is_meaningful_and_copy_surfaces_are_disabled() -> None:
    values = valid_secrets()
    secrets = SecretConfig.model_validate(values)
    equivalent = SecretConfig.model_validate(values)
    distinct = SecretConfig.model_validate({**values, "resend_api_key": "other-resend-key"})

    assert secrets == equivalent
    assert secrets != distinct
    with pytest.raises(TypeError):
        secrets.model_copy()
    with pytest.raises(TypeError):
        copy.copy(secrets)
    with pytest.raises(TypeError):
        copy.deepcopy(secrets)


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_password": ""},
        {"secret_key_base": "too-short"},
        {"ash_authentication_token_signing_secret": "too-short"},
        {"secret_key_base": "same", "ash_authentication_token_signing_secret": "same"},
        {"unknown": "value"},
    ],
)
def test_secret_validation_rejects_missing_short_duplicate_or_unknown_values(overrides: dict[str, str]) -> None:
    with pytest.raises(OpsError) as raised:
        SecretConfig.model_validate({**valid_secrets(), **overrides})

    assert raised.value.status is ExitStatus.SECRET
    assert all(value not in repr(raised.value) for value in valid_secrets().values())
    assert all(value not in repr(raised.value.as_dict()) for value in valid_secrets().values())


@dataclass
class CapturedRunner:
    stdout: bytearray
    stderr: bytearray = field(default_factory=bytearray)
    returncode: int = 0

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.argv = argv
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


def environment_for_secrets() -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(valid_environment(name="example"))


def test_decrypt_secrets_uses_sops_stdout_only_registers_values_and_clears_buffers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = valid_secrets()
    output = bytearray(yaml.safe_dump(values).encode())
    runner = CapturedRunner(output, bytearray(b"stderr must not be parsed"))
    monkeypatch.setattr("taskman_ops.secrets.SECRETS_DIR", tmp_path)
    (tmp_path / "example.secrets.sops.yaml").write_text("ENC[example]", encoding="utf-8")

    secrets = decrypt_secrets("example", runner)

    assert runner.argv == [
        "sops",
        "decrypt",
        "--output-type",
        "yaml",
        str(tmp_path / "example.secrets.sops.yaml"),
    ]
    assert "--output" not in runner.argv
    assert runner.stdout == bytearray()
    assert runner.stderr == bytearray()
    assert redact(values["database_password"]) == "[REDACTED]"
    assert secrets.resend_api_key == values["resend_api_key"]


def test_malformed_sops_output_maps_to_secret_status_four(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("taskman_ops.secrets.SECRETS_DIR", tmp_path)
    (tmp_path / "example.secrets.sops.yaml").write_text("ENC[example]", encoding="utf-8")
    runner = CapturedRunner(bytearray(b"not: [yaml"), bytearray(b"password canary"))

    with pytest.raises(OpsError) as raised:
        decrypt_secrets("example", runner)

    assert raised.value.status is ExitStatus.SECRET
    assert "password canary" not in repr(raised.value)


@pytest.mark.parametrize("returncode", [1, 2])
def test_sops_command_failure_maps_to_secret_status_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    monkeypatch.setattr("taskman_ops.secrets.SECRETS_DIR", tmp_path)
    (tmp_path / "example.secrets.sops.yaml").write_text("ENC[example]", encoding="utf-8")
    runner = CapturedRunner(bytearray(b""), bytearray(b"db canary"), returncode)

    with pytest.raises(OpsError) as raised:
        decrypt_secrets("example", runner)

    assert raised.value.status is ExitStatus.SECRET


def test_runtime_and_pgpass_rendering_is_quoted_in_memory_only() -> None:
    config = environment_for_secrets()
    secrets = SecretConfig.model_validate(valid_secrets())

    runtime = render_runtime_environment(config, secrets)
    pgpass = render_pgpass(config, secrets)

    assert isinstance(runtime, bytes)
    assert b'PHX_HOST="taskman.acme.tld"' in runtime
    assert b'PORT="4000"' in runtime
    assert b'DATABASE_URL="ecto://taskman:db%20password%3A%20canary%20%5C%20with%20quote@127.0.0.1:5432/taskman_prod"' in runtime
    assert b'RESEND_API_KEY="re_canary_resend_key"' in runtime
    assert pgpass.endswith(b"\n")
    assert b"127.0.0.1:5432:taskman_prod:taskman:db password\\: canary \\\\ with quote\n" == pgpass
    assert redact(runtime) == b"[REDACTED]"
    assert redact(pgpass) == b"[REDACTED]"


def test_pgpass_escapes_ipv6_host_without_uri_brackets() -> None:
    config = environment_for_secrets().model_copy(update={"database_host": "::1"})
    secrets = SecretConfig.model_validate(valid_secrets())

    pgpass = render_pgpass(config, secrets)

    assert pgpass.startswith(b"\\:\\:1:5432:taskman_prod:taskman:")
    assert b"[" not in pgpass
