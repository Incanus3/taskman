"""Exact pinned toolchains accepted for immutable Taskman releases."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeToolchain:
    """One complete build provenance tuple selected by its unique OTP version."""

    otp_version: str
    elixir_version: str
    node_version: str
    hex_version: str
    rebar3_version: str
    builder_base_tag: str
    builder_base_digest: str


CURRENT_RUNTIME = RuntimeToolchain(
    otp_version="29.1.2",
    elixir_version="1.20.5",
    node_version="22.22.1",
    hex_version="2.6.0",
    rebar3_version="3.28.0",
    builder_base_tag="ubuntu:resolute-20261024.1",
    builder_base_digest="sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c",
)
SUPPORTED_RUNTIMES = (
    RuntimeToolchain(
        otp_version="29.0.6",
        elixir_version="1.20.4",
        node_version="22.22.1",
        hex_version="2.5.1",
        rebar3_version="3.24.0",
        builder_base_tag="ubuntu:resolute-20260811.1",
        builder_base_digest="sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b",
    ),
    CURRENT_RUNTIME,
)
_RUNTIMES_BY_OTP = {runtime.otp_version: runtime for runtime in SUPPORTED_RUNTIMES}


def runtime_for_otp_version(value: str) -> RuntimeToolchain:
    """Return one allowlisted complete toolchain, never a compatible version range."""

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
