"""Preserve ordinary UFW rules while appending bounded port protection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import ipaddress
import json
import os
import time

from taskman_ops.host_protocol import (
    HostRequest, HostResult, ProtocolError, encode_result,
    validate_firewall_request, validate_firewall_result,
)
from taskman_ops.host_helper.commands import run_command
from taskman_ops.host_helper.ufw_adapter import UfwInspectionError, UfwRule, UfwSnapshot, inspect_ufw


_DEADLINE_SECONDS = 45
_OUTPUT_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class FirewallDelta:
    snapshot_sha256: str
    proposed: tuple[tuple[str, ...], ...]
    exceptions: tuple[UfwRule, ...]


def _owned_ports(ports: Mapping[str, int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if (not isinstance(ports, Mapping) or set(ports) != {'ssh', 'application', 'database', 'distribution'}
            or any(type(port) is not int or not 1 <= port <= 65535 for port in ports.values())):
        raise UfwInspectionError('invalid_input')
    public = tuple(dict.fromkeys((ports['ssh'], 80, 443)))
    private = tuple(dict.fromkeys((ports['application'], ports['database'], ports['distribution'], 4369)))
    if set(public) & set(private):
        raise UfwInspectionError('invalid_input')
    return public, private


def _identity(connection: Mapping[str, str]) -> dict[str, str]:
    try:
        if not isinstance(connection, Mapping) or set(connection) != {'source_address', 'destination_address'}:
            raise ValueError
        addresses = {}
        for key, value in connection.items():
            if type(value) is not str or '%' in value:
                raise ValueError
            addresses[key] = ipaddress.ip_address(value)
        if addresses['source_address'].version != addresses['destination_address'].version:
            raise ValueError
        return {key: str(value) for key, value in addresses.items()}
    except ValueError as error:
        raise UfwInspectionError('invalid_input') from error


def _administrator_connection(ssh_port: int) -> dict[str, str]:
    fields = os.environ.get('SSH_CONNECTION', '').split()
    if (len(fields) != 4 or not fields[1].isascii() or not fields[1].isdecimal()
            or not fields[3].isascii() or not fields[3].isdecimal()
            or not 1 <= int(fields[1]) <= 65535 or int(fields[3]) != ssh_port):
        raise UfwInspectionError('invalid_input')
    return _identity({'source_address': fields[0], 'destination_address': fields[2]})


def _contains(intervals: tuple[tuple[int, int], ...] | None, port: int) -> bool:
    return intervals is None or any(low <= port <= high for low, high in intervals)


def _tcp_in(rule: UfwRule) -> bool:
    return rule.direction == 'in' and rule.protocol in {'tcp', 'any'}


def _all(network: str) -> bool:
    return ipaddress.ip_network(network, strict=False).prefixlen == 0


def _covers(rule: UfwRule, family: int, action: str, port: int) -> bool:
    return (rule.family == family and _tcp_in(rule) and rule.action == action
            and _all(rule.src) and _all(rule.dst) and rule.sports is None
            and not rule.interface_in and not rule.interface_out and _contains(rule.dports, port))


def _controller_path(rules: tuple[UfwRule, ...], ssh_port: int, identity: Mapping[str, str]) -> None:
    source = ipaddress.ip_address(identity['source_address'])
    destination = ipaddress.ip_address(identity['destination_address'])
    for rule in rules:
        if (rule.family != source.version or not _tcp_in(rule) or not _contains(rule.dports, ssh_port)
                or source not in ipaddress.ip_network(rule.src, strict=False)
                or destination not in ipaddress.ip_network(rule.dst, strict=False)):
            continue
        if rule.action != 'allow':
            raise UfwInspectionError('controller_path_blocked')
        if rule.sports is None and not rule.interface_in and not rule.interface_out:
            return
    # The prospective unrestricted SSH fallback is appended after the saved rules.
    # Reaching it without a possible blocking rule establishes the future path.


def plan_firewall(snapshot: UfwSnapshot, ports: Mapping[str, int],
                  administrator_connection: Mapping[str, str]) -> FirewallDelta:
    public, private = _owned_ports(ports)
    identity = _identity(administrator_connection)
    defaults = dict(snapshot.defaults)
    if (defaults.get('default_input_policy') != 'DROP'
            or defaults.get('default_output_policy') != 'ACCEPT'
            or defaults.get('default_forward_policy') not in {'DROP', 'REJECT'}
            or defaults.get('ipv6') != 'yes'):
        raise UfwInspectionError('policy_conflict')
    exceptions = []
    for rule in snapshot.rules:
        if not _tcp_in(rule):
            continue
        owned_overlap = any(_contains(rule.dports, port) for port in (*public, *private))
        if not _all(rule.src):
            if owned_overlap:
                exceptions.append(rule)
            continue
        if (rule.action in {'allow', 'limit'} and any(_contains(rule.dports, port) for port in private)
                or rule.action in {'deny', 'reject', 'limit'} and any(_contains(rule.dports, port) for port in public)):
            raise UfwInspectionError('policy_conflict')
    _controller_path(snapshot.rules, ports['ssh'], identity)
    proposed = tuple(('ufw', action, 'from', source, 'to', 'any', 'port', str(port), 'proto', 'tcp')
                     for action, numbers in (('allow', public), ('deny', private)) for port in numbers
                     for family, source in ((4, '0.0.0.0/0'), (6, '::/0'))
                     if not any(_covers(rule, family, action, port) for rule in snapshot.rules))
    if not snapshot.active:
        proposed += (('ufw', '--force', 'enable'),)
    if len(exceptions) > 4096:
        raise UfwInspectionError('unsupported_state')
    canonical = {'active': snapshot.active, 'defaults': sorted(snapshot.defaults),
                 'rules': [asdict(rule) for rule in snapshot.rules], 'administrator_connection': identity}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=True).encode('utf-8')).hexdigest()
    return FirewallDelta(digest, proposed, tuple(exceptions))


def _preserved(original: UfwSnapshot, observed: UfwSnapshot) -> bool:
    if dict(original.defaults) != dict(observed.defaults):
        return False
    for family in (4, 6):
        remaining = iter(rule for rule in observed.rules if rule.family == family)
        for rule in (rule for rule in original.rules if rule.family == family):
            if not any(candidate == rule for candidate in remaining):
                return False
    return True


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('firewall deadline exceeded')
    return remaining


def _state() -> dict[str, object]:
    return {'reason': 'inspection_failed', 'changed': False, 'possibly_changed': False,
            'snapshot_sha256': None, 'administrator_connection': None,
            'exceptions': (), 'proposed': (), 'completed': ()}


def _failure(request: HostRequest, state: dict[str, object], reason: str) -> HostResult:
    state.update(reason=reason, snapshot_sha256=None, exceptions=())
    if state['changed'] or state['possibly_changed']:
        outcome = 'manual'
        if reason not in {'inspection_failed', 'execution_failed', 'postcheck_failed'}:
            state['reason'] = 'postcheck_failed'
    elif reason in {'invalid_input', 'policy_conflict', 'unsupported_state', 'controller_path_blocked'}:
        outcome = 'refused'
    else:
        outcome = 'retryable'
    if request.parameters.get('mode') != 'converge':
        state.update(proposed=(), completed=())
    return HostResult.for_request(request, outcome, 'firewall ' + str(state['reason']), state)


def firewall_failure_result(request: HostRequest, result: HostResult | None = None) -> HostResult:
    """Reduce invalid/lost helper evidence without claiming dispatched work unchanged."""

    state = _state()
    if request.parameters.get('mode') == 'converge':
        state['possibly_changed'] = True
        if (isinstance(result, HostResult) and result.protocol_version == request.protocol_version
                and result.operation == request.operation and result.correlation_id == request.correlation_id):
            candidate = dict(state)
            candidate.update(proposed=result.state.get('proposed', ()),
                             completed=result.state.get('completed', ()),
                             changed=bool(result.state.get('completed', ())))
            try:
                reduced = _failure(request, candidate, 'postcheck_failed')
                validate_firewall_result(request, reduced)
            except (ProtocolError, TypeError, ValueError):
                pass
            else:
                return reduced
    return _failure(request, state, 'inspection_failed')


def firewall(request: HostRequest) -> HostResult:
    """Inspect or converge under one deadline, retaining each native command boundary."""

    state = _state()
    deadline = time.monotonic() + _DEADLINE_SECONDS
    boundary = 'input'
    try:
        validate_firewall_request(request)
        ports = request.parameters['ports']
        identity = _administrator_connection(ports['ssh'])
        state['administrator_connection'] = identity
        boundary = 'inspection'
        original = inspect_ufw(deadline=deadline)
        _remaining(deadline)
        delta = plan_firewall(original, ports, identity)
        _remaining(deadline)
        if request.parameters['mode'] == 'converge':
            if request.expected_state['snapshot_sha256'] != delta.snapshot_sha256:
                raise UfwInspectionError('policy_conflict')
            state.update(proposed=delta.proposed, exceptions=tuple(asdict(rule) for rule in delta.exceptions))
            for command in delta.proposed:
                if command == ('ufw', '--force', 'enable'):
                    boundary = 'postcheck'
                    saved = inspect_ufw(deadline=deadline)
                    saved_delta = plan_firewall(saved, ports, identity)
                    if (not _preserved(original, saved) or saved.active
                            or saved_delta.proposed != (('ufw', '--force', 'enable'),)):
                        raise UfwInspectionError('postcheck_failed')
                boundary = 'execution'
                remaining = _remaining(deadline)
                state['possibly_changed'] = True
                completed = run_command(('/usr/sbin/ufw', *command[1:]),
                                        env={**os.environ, 'LC_ALL': 'C'}, timeout_seconds=remaining,
                                        output_limit=_OUTPUT_LIMIT)
                if completed.returncode != 0:
                    raise RuntimeError('native command failed')
                state['completed'] = (*state['completed'], command)
                state['changed'] = True
                state['possibly_changed'] = False
                if len(completed.stdout) + len(completed.stderr) > _OUTPUT_LIMIT:
                    raise RuntimeError('native output exceeds bound')
                _remaining(deadline)
            boundary = 'postcheck'
            final = inspect_ufw(deadline=deadline)
            _remaining(deadline)
            final_delta = plan_firewall(final, ports, identity)
            if not final.active or final_delta.proposed or not _preserved(original, final):
                raise UfwInspectionError('postcheck_failed')
            digest = final_delta.snapshot_sha256
        else:
            state.update(proposed=delta.proposed, exceptions=tuple(asdict(rule) for rule in delta.exceptions))
            digest = delta.snapshot_sha256
        state.update(reason='compatible', snapshot_sha256=digest)
        result = HostResult.for_request(request, 'succeeded', 'firewall compatible', state)
        validate_firewall_result(request, result)
        encode_result(result)  # Reject oversized evidence while command-boundary state is available.
        _remaining(deadline)
        return result
    except ProtocolError:
        reason = {'input': 'invalid_input', 'inspection': 'inspection_failed',
                  'execution': 'execution_failed', 'postcheck': 'postcheck_failed'}[boundary]
    except UfwInspectionError as error:
        reason = error.reason
        if boundary == 'postcheck':
            reason = 'postcheck_failed'
        elif reason == 'ufw_missing' and request.parameters.get('mode') != 'inspect':
            reason = 'inspection_failed'
    except (Exception, SystemExit):
        reason = {'input': 'invalid_input', 'inspection': 'inspection_failed', 'execution': 'execution_failed',
                  'postcheck': 'postcheck_failed'}[boundary]
    return _failure(request, state, reason)


__all__ = ['FirewallDelta', 'plan_firewall', 'firewall']
