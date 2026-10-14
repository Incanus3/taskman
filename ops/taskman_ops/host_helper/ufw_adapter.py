"""Lazy, trusted and read-only inspection of the system's ordinary UFW policy."""

from __future__ import annotations

import builtins
from dataclasses import asdict, dataclass
import gettext
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
from types import ModuleType

from .commands import CommandError, run_command


_UFW_PACKAGE = Path("/usr/lib/python3/dist-packages/ufw")
_OUTPUT_LIMIT = 1024 * 1024
_RULE_LIMIT = 4096
_DEFAULT_KEYS = ("default_forward_policy", "default_input_policy", "default_output_policy", "ipv6")
_MISSING = object()


@dataclass(frozen=True)
class UfwRule:
    family: int
    direction: str
    action: str
    protocol: str
    src: str
    dst: str
    sports: tuple[tuple[int, int], ...] | None
    dports: tuple[tuple[int, int], ...] | None
    interface_in: str
    interface_out: str
    logtype: str
    comment: str
    sapp: str
    dapp: str


@dataclass(frozen=True)
class UfwSnapshot:
    active: bool
    defaults: tuple[tuple[str, str], ...]
    rules: tuple[UfwRule, ...]


class UfwInspectionError(RuntimeError):
    """A bounded public reason, without native or reader diagnostics."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Capture:
    """Accept UFW's binary writes and ordinary text writes under one byte budget."""

    encoding = "utf-8"

    def __init__(self):
        self.buffer = self
        self.size = 0

    def write(self, value):
        if type(value) is str:
            encoded = value.encode("utf-8")
        elif type(value) is bytes:
            encoded = value
        else:
            raise UfwInspectionError("inspection_failed")
        self.size += len(encoded)
        if self.size > _OUTPUT_LIMIT:
            raise UfwInspectionError("inspection_failed")
        return len(value)

    def flush(self):
        pass


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise UfwInspectionError("inspection_failed")
    return remaining


def _trusted_path(path: Path) -> None:
    """Require ordinary root-owned files/directories and trusted ancestors."""

    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except OSError as error:
            raise UfwInspectionError("unsupported_state") from error
        if (info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
            raise UfwInspectionError("unsupported_state")


def _validate_namespace() -> None:
    for name, module in tuple(sys.modules.items()):
        if name != "ufw" and not name.startswith("ufw."):
            continue
        if not isinstance(module, ModuleType):
            raise UfwInspectionError("unsupported_state")
        expected = _UFW_PACKAGE / ("__init__.py" if name == "ufw" else name[4:].replace(".", "/") + ".py")
        origin = getattr(getattr(module, "__spec__", None), "origin", None)
        if getattr(module, "__file__", None) != str(expected) or origin != str(expected):
            raise UfwInspectionError("unsupported_state")
        _trusted_path(expected)
        if name == "ufw" and list(getattr(module, "__path__", ())) != [str(_UFW_PACKAGE)]:
            raise UfwInspectionError("unsupported_state")


def _load_package(*, deadline: float) -> None:
    _remaining(deadline)
    _validate_namespace()
    # Follow no package symlink when deciding whether installation is a prerequisite.
    # A visible but damaged entry must reach refusal, even when its target is absent.
    try:
        _UFW_PACKAGE.lstat()
    except FileNotFoundError as error:
        raise UfwInspectionError("ufw_missing") from error
    except OSError as error:
        raise UfwInspectionError("unsupported_state") from error
    entry = _UFW_PACKAGE / "__init__.py"
    _trusted_path(entry)
    # Import machinery may execute existing bytecode, so trust caches as well as sources.
    for member in _UFW_PACKAGE.rglob("*"):
        _trusted_path(member)
    _remaining(deadline)
    if "ufw" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "ufw", entry, submodule_search_locations=[str(_UFW_PACKAGE)]
        )
        if spec is None or spec.loader is None:
            raise UfwInspectionError("inspection_failed")
        package = importlib.util.module_from_spec(spec)
        sys.modules["ufw"] = package
        spec.loader.exec_module(package)
    _remaining(deadline)


def _read_backend(*, deadline: float) -> object:
    """The sole backend construction boundary; no capability or mutation methods."""

    _load_package(deadline=deadline)
    import ufw.backend_iptables
    import ufw.util

    _validate_namespace()
    _remaining(deadline)
    original_cmd = ufw.util.cmd

    def bounded_version(command):
        argv = tuple(command)
        if argv != ("/usr/sbin/iptables", "-V"):
            raise UfwInspectionError("unsupported_state")
        completed = run_command(
            argv, timeout_seconds=_remaining(deadline),
            env={**os.environ, "LC_ALL": "C"}, output_limit=_OUTPUT_LIMIT,
        )
        _remaining(deadline)
        if completed.returncode != 0 or completed.stderr:
            raise UfwInspectionError("inspection_failed")
        return completed.returncode, completed.stdout.decode("utf-8")

    try:
        ufw.util.cmd = bounded_version
        backend = ufw.backend_iptables.UFWBackendIptables(dryrun=True)
        _remaining(deadline)
        _validate_namespace()
        return backend
    finally:
        ufw.util.cmd = original_cmd


def _text(value: object) -> str:
    if type(value) is not str or "\x00" in value or len(value.encode("utf-8")) > _OUTPUT_LIMIT:
        raise UfwInspectionError("unsupported_state")
    return value


def _network(value: object, family: int) -> str:
    value = _text(value)
    if value == "any":
        value = "0.0.0.0/0" if family == 4 else "::/0"
    if "%" in value:
        raise UfwInspectionError("unsupported_state")
    network = ipaddress.ip_network(value, strict=False)
    if network.version != family:
        raise UfwInspectionError("unsupported_state")
    return str(network)


def _ports(value: object, protocol: str, *, saved_profile: bool):
    value = _text(value)
    if value in {"", "any"}:
        if saved_profile:
            raise UfwInspectionError("unsupported_state")
        return None
    intervals = []
    for part in value.split(","):
        if re.fullmatch(r"[0-9]+(?::[0-9]+)?", part):
            endpoints = part.split(":")
            low, high = int(endpoints[0]), int(endpoints[-1])
        else:
            if saved_profile or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", part):
                raise UfwInspectionError("unsupported_state")
            protocols = ("tcp", "udp") if protocol == "any" else (protocol,)
            resolved = {socket.getservbyname(part, candidate) for candidate in protocols}
            if len(resolved) != 1:
                raise UfwInspectionError("unsupported_state")
            low = high = resolved.pop()
        if not 1 <= low <= high <= 65535:
            raise UfwInspectionError("unsupported_state")
        intervals.append((low, high))
    # Canonicalize equivalent sets without changing the surrounding rule order.
    merged = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return tuple(merged)


def _normalize_rule(rule: object, family: int) -> UfwRule:
    if type(rule.v6) is not bool or rule.v6 != (family == 6) or rule.forward is not False:
        raise UfwInspectionError("unsupported_state")
    direction = _text(rule.direction) or "in"
    action = _text(rule.action)
    protocol = _text(rule.protocol) or "any"
    logtype = _text(rule.logtype)
    if (direction not in {"in", "out"} or action not in {"allow", "deny", "reject", "limit"}
            or protocol not in {"tcp", "udp", "any"} or logtype not in {"", "log", "log-all"}):
        raise UfwInspectionError("unsupported_state")
    interfaces = (_text(rule.interface_in), _text(rule.interface_out))
    if sum(bool(interface) for interface in interfaces) > 1 or any(
        interface and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface) for interface in interfaces
    ):
        raise UfwInspectionError("unsupported_state")
    sapp, dapp = _text(rule.sapp), _text(rule.dapp)
    if (sapp or dapp) and protocol == "any":
        raise UfwInspectionError("unsupported_state")
    encoded_comment = _text(rule.comment)
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", encoded_comment):
        raise UfwInspectionError("unsupported_state")
    # UFW silently truncates odd hex and replaces invalid UTF-8; neither is usable evidence.
    bytes.fromhex(encoded_comment).decode("utf-8")
    return UfwRule(
        family, direction, action, protocol, _network(rule.src, family), _network(rule.dst, family),
        _ports(rule.sport, protocol, saved_profile=bool(sapp)),
        _ports(rule.dport, protocol, saved_profile=bool(dapp)),
        *interfaces, logtype, _text(rule.get_comment()), sapp, dapp,
    )


def _defaults(backend: object) -> tuple[tuple[str, str], ...]:
    values = backend.defaults
    if type(values) is not dict:
        raise UfwInspectionError("unsupported_state")
    normalized = []
    for key in _DEFAULT_KEYS:
        value = _text(values[key])
        if key == "ipv6":
            if value not in {"yes", "no"}:
                raise UfwInspectionError("unsupported_state")
        else:
            value = value.upper()
            if value not in {"DROP", "ACCEPT", "REJECT"}:
                raise UfwInspectionError("unsupported_state")
        normalized.append((key, value))
    return tuple(normalized)


def _active(*, deadline: float) -> bool:
    completed = run_command(
        ("/usr/sbin/ufw", "status"), timeout_seconds=_remaining(deadline),
        env={**os.environ, "LC_ALL": "C"}, output_limit=_OUTPUT_LIMIT,
    )
    _remaining(deadline)
    if completed.returncode != 0 or completed.stderr:
        raise UfwInspectionError("inspection_failed")
    lines = completed.stdout.decode("utf-8").splitlines()
    if (not lines or lines[0] not in {"Status: active", "Status: inactive"}
            or any(line.startswith("Status:") for line in lines[1:])):
        raise UfwInspectionError("unsupported_state")
    return lines[0] == "Status: active"


def inspect_ufw(*, deadline: float) -> UfwSnapshot:
    """Return immutable persisted facts and native status, refusing all diagnostics."""

    if type(deadline) not in {int, float} or not math.isfinite(deadline):
        raise UfwInspectionError("inspection_failed")
    previous_streams = sys.stdout, sys.stderr
    previous_bytecode_flag = sys.dont_write_bytecode
    previous_translation = getattr(builtins, "_", _MISSING)
    previous_modules = {name: module for name, module in sys.modules.copy().items()
                        if name == "ufw" or name.startswith("ufw.")}
    previous_attributes = {name: vars(module).copy() for name, module in previous_modules.items()
                           if isinstance(module, ModuleType)}
    capture = _Capture()
    try:
        _remaining(deadline)
        sys.stdout = sys.stderr = capture
        sys.dont_write_bytecode = True
        builtins._ = gettext.gettext
        backend = _read_backend(deadline=deadline)
        _remaining(deadline)
        if capture.size:
            raise UfwInspectionError("inspection_failed")
        defaults = _defaults(backend)
        rules = []
        model_bytes = 0
        for family, getter in ((4, backend.get_rules_ipv4), (6, backend.get_rules_ipv6)):
            _remaining(deadline)
            saved_rules = getter()
            _remaining(deadline)
            if type(saved_rules) not in {list, tuple} or len(saved_rules) > _RULE_LIMIT:
                raise UfwInspectionError("unsupported_state")
            for saved_rule in saved_rules:
                _remaining(deadline)
                rule = _normalize_rule(saved_rule, family)
                model_bytes += len(json.dumps(asdict(rule), ensure_ascii=True).encode("utf-8"))
                if model_bytes > _OUTPUT_LIMIT:
                    raise UfwInspectionError("unsupported_state")
                rules.append(rule)
                _remaining(deadline)
        if capture.size:
            raise UfwInspectionError("inspection_failed")
        active = _active(deadline=deadline)
        _remaining(deadline)
        if capture.size:
            raise UfwInspectionError("inspection_failed")
        return UfwSnapshot(active, defaults, tuple(rules))
    except UfwInspectionError:
        raise
    except (Exception, SystemExit) as error:
        reason = "inspection_failed" if isinstance(error, (CommandError, ImportError, RuntimeError, SystemExit)) else "unsupported_state"
        raise UfwInspectionError(reason) from error
    finally:
        sys.stdout, sys.stderr = previous_streams
        sys.dont_write_bytecode = previous_bytecode_flag
        if previous_translation is _MISSING:
            if hasattr(builtins, "_"):
                del builtins._
        else:
            builtins._ = previous_translation
        for name in tuple(sys.modules):
            if name == "ufw" or name.startswith("ufw."):
                if name not in previous_modules:
                    del sys.modules[name]
        sys.modules.update(previous_modules)
        for name, attributes in previous_attributes.items():
            namespace = vars(previous_modules[name])
            namespace.clear()
            namespace.update(attributes)


__all__ = ["UfwRule", "UfwSnapshot", "UfwInspectionError", "inspect_ufw"]
