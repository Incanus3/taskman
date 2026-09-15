"""Exact runtime pairs accepted for immutable Taskman releases."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeToolchain:
    """The coupled OTP and Elixir versions encoded in a release identity."""

    otp_version: str
    elixir_version: str


CURRENT_RUNTIME = RuntimeToolchain(otp_version="29.0.6", elixir_version="1.20.4")
SUPPORTED_RUNTIMES = (CURRENT_RUNTIME,)
_RUNTIMES_BY_OTP = {runtime.otp_version: runtime for runtime in SUPPORTED_RUNTIMES}


def runtime_for_otp_version(value: str) -> RuntimeToolchain:
    """Return one allowlisted runtime pair, never a compatible version range."""

    if not isinstance(value, str):
        raise ValueError("unsupported OTP runtime")
    try:
        return _RUNTIMES_BY_OTP[value]
    except KeyError as error:
        raise ValueError("unsupported OTP runtime") from error


__all__ = [
    "CURRENT_RUNTIME",
    "RuntimeToolchain",
    "SUPPORTED_RUNTIMES",
    "runtime_for_otp_version",
]
