from __future__ import annotations

from dataclasses import dataclass, field
import copy
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from taskman_ops.config import EnvironmentConfig
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import redact
from taskman_ops.secrets import (
    SecretConfig,
    decrypt_secrets,
    render_pgpass,
    render_runtime_environment,
)

from tests.support.environments import valid_environment
from tests.support.secrets import no_registered_secrets_between_tests as no_registered_secrets_between_tests


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


@pytest.fixture
def encrypted_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("taskman_ops.secrets.SECRETS_DIR", tmp_path)
    path = tmp_path / "example.secrets.sops.yaml"
    path.write_text("ENC[example]", encoding="utf-8")
    return path


@pytest.mark.parametrize("shape", ["raw", "mapping", "tuple-one", "tuple-two", "tuple-three", "attributes"])
def test_sops_refuses_results_without_completed_process_contract(encrypted_secrets: Path, shape: str) -> None:
    output = yaml.safe_dump(valid_secrets()).encode()
    results = {
        "raw": output,
        "mapping": {"returncode": 0, "stdout": output, "stderr": b""},
        "tuple-one": (output,),
        "tuple-two": (output, b""),
        "tuple-three": (0, output, b""),
        "attributes": SimpleNamespace(returncode=0, stdout=output, stderr=b""),
    }

    with pytest.raises(OpsError) as raised:
        decrypt_secrets("example", lambda _argv: results[shape])

    assert raised.value.status is ExitStatus.SECRET
    assert raised.value.message == "unable to decrypt or validate deployment secrets"
    assert all(value not in repr(raised.value) for value in valid_secrets().values())


def test_sops_refuses_run_method_instead_of_callable(encrypted_secrets: Path) -> None:
    calls = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, yaml.safe_dump(valid_secrets()).encode(), b"")

    with pytest.raises(OpsError) as raised:
        decrypt_secrets("example", SimpleNamespace(run=run))

    assert raised.value.status is ExitStatus.SECRET
    assert calls == []


def test_sops_callable_type_error_is_not_retried(encrypted_secrets: Path) -> None:
    calls = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        raise TypeError("credential-canary")

    with pytest.raises(OpsError) as raised:
        decrypt_secrets("example", runner)

    assert len(calls) == 1
    assert raised.value.status is ExitStatus.SECRET
    assert "credential-canary" not in repr(raised.value)


@pytest.mark.parametrize("capture", ["stdout", "stderr"])
@pytest.mark.parametrize("invalid", ["text", "memoryview", "arbitrary"])
def test_sops_refuses_invalid_capture_types_and_clears_other_mutable_capture(
    encrypted_secrets: Path, capture: str, invalid: str
) -> None:
    class StringifiableOutput:
        def __str__(self) -> str:
            return yaml.safe_dump(valid_secrets())

    invalid_values = {
        "text": yaml.safe_dump(valid_secrets()),
        "memoryview": memoryview(yaml.safe_dump(valid_secrets()).encode()),
        "arbitrary": StringifiableOutput(),
    }
    mutable = bytearray(yaml.safe_dump(valid_secrets()).encode())
    completed = subprocess.CompletedProcess([], 0, mutable, mutable)
    setattr(completed, capture, invalid_values[invalid])

    with pytest.raises(OpsError) as raised:
        decrypt_secrets("example", lambda _argv: completed)

    assert raised.value.status is ExitStatus.SECRET
    assert raised.value.message == "unable to decrypt or validate deployment secrets"
    assert all(value not in repr(raised.value) for value in valid_secrets().values())
    assert mutable == bytearray()
    assert completed.stdout == b""
    assert completed.stderr == b""


@pytest.mark.parametrize(
    "returncode,output",
    [(1, b"plaintext-canary"), (0, None), (0, b""), (0, b"\xffplaintext-canary"),
     (0, b"not: [plaintext-canary"), (0, b"- plaintext-canary"),
     (0, b"database_password: plaintext-canary")],
)
def test_sops_refusal_clears_both_completed_captures(
    encrypted_secrets: Path, returncode: int, output: bytes | None
) -> None:
    stdout = bytearray(output) if output is not None else None
    stderr = bytearray(b"stderr-canary")
    completed = subprocess.CompletedProcess([], returncode, stdout, stderr)

    with pytest.raises(OpsError) as raised:
        decrypt_secrets("example", lambda _argv: completed)

    assert raised.value.status is ExitStatus.SECRET
    assert "canary" not in repr(raised.value)
    assert stdout is None or stdout == bytearray()
    assert stderr == bytearray()
    assert completed.stdout == b""
    assert completed.stderr == b""


def test_sops_default_captures_binary_output_without_checking_exit(
    encrypted_secrets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = valid_secrets()
    completed = subprocess.CompletedProcess([], 0, yaml.safe_dump(values).encode(), None)
    calls = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return completed

    monkeypatch.setattr("taskman_ops.secrets.subprocess.run", run)

    secrets = decrypt_secrets("example")

    assert secrets.database_password == values["database_password"]
    assert redact(values["database_password"]) == "[REDACTED]"
    assert calls == [(["sops", "decrypt", "--output-type", "yaml", str(encrypted_secrets)],
                      {"check": False, "capture_output": True})]
    assert completed.stdout == b""
    assert completed.stderr == b""


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
