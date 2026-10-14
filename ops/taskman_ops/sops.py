"""Decrypt SOPS YAML through a short-lived, protected memory channel."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import subprocess

import yaml

from .errors import OpsError
from .output import register_secret


SopsRunner = Callable[[list[str]], subprocess.CompletedProcess[bytes | bytearray]]


def register_strings(value: object) -> None:
    """Register scalar strings before validation can construct diagnostics."""

    if isinstance(value, str):
        register_secret(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            register_strings(key)
            register_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            register_strings(item)


def _as_buffer(value: object) -> bytearray:
    if value is None:
        return bytearray()
    if isinstance(value, bytearray):
        return value
    if isinstance(value, bytes):
        return bytearray(value)
    raise TypeError("SOPS captures must be binary")


def _wipe_buffer(buffer: bytearray) -> None:
    buffer[:] = b"\x00" * len(buffer)
    buffer.clear()


def decrypt_yaml(
    path: Path,
    *,
    error: Callable[[], OpsError],
    runner: SopsRunner | None = None,
    register: Callable[[object], None] | None = register_strings,
) -> Mapping:
    """Capture binary SOPS stdout, register parsed values and wipe captures.

    Credential callers register parsed strings by default; ordinary settings
    callers pass ``register=None`` to keep operator reports intact.
    The caller owns file selection, validation and stable error semantics.
    SOPS resolves its external identity through its normal environment; no
    plaintext file or private identity is created or accepted here.
    """

    argv = ["sops", "decrypt", "--output-type", "yaml", str(path)]
    result: subprocess.CompletedProcess[bytes | bytearray] | None = None
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    payload: object = None
    failed = False
    try:
        try:
            result = subprocess.run(argv, check=False, capture_output=True) if runner is None else runner(argv)
            if not isinstance(result, subprocess.CompletedProcess):
                raise TypeError("SOPS runner must return CompletedProcess")
            stdout_buffer = _as_buffer(result.stdout)
            stderr_buffer = _as_buffer(result.stderr)
            if result.returncode != 0 or not stdout_buffer:
                raise ValueError("SOPS decryption failed")
            payload = yaml.safe_load(bytes(stdout_buffer).decode("utf-8"))
            if register is not None:
                register(payload)
            if not isinstance(payload, Mapping):
                raise ValueError("SOPS output must be a mapping")
        except Exception:
            # Leave the exception handler before raising a fixed boundary
            # error, so parser/subprocess details are not retained as context.
            payload = None
            failed = True
    finally:
        _wipe_buffer(stdout_buffer)
        _wipe_buffer(stderr_buffer)
        if isinstance(result, subprocess.CompletedProcess):
            for attribute in ("stdout", "stderr"):
                raw = getattr(result, attribute)
                if isinstance(raw, bytearray):
                    _wipe_buffer(raw)
                setattr(result, attribute, b"")
    if failed:
        raise error()
    return payload
