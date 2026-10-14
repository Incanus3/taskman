"""Closed wire evidence for ordinary UFW inspection and additive convergence."""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import re

from .envelope import HostRequest, HostResult, validate_result_for_request
from .identifiers import ProtocolError


_PORT_KEYS = frozenset({'ssh', 'application', 'database', 'distribution'})
_STATE_KEYS = frozenset({'reason', 'changed', 'possibly_changed', 'snapshot_sha256',
                         'administrator_connection', 'exceptions', 'proposed', 'completed'})
_RULE_KEYS = frozenset({'family', 'direction', 'action', 'protocol', 'src', 'dst', 'sports',
                        'dports', 'interface_in', 'interface_out', 'logtype', 'comment', 'sapp', 'dapp'})
_REFUSALS = frozenset({'invalid_input', 'policy_conflict', 'unsupported_state', 'controller_path_blocked'})
_FAILURES = frozenset({'inspection_failed', 'execution_failed', 'postcheck_failed'})


def _exact(value: object, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ProtocolError('invalid firewall fields')
    return value


def _digest(value: object) -> bool:
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _port(value: object) -> bool:
    return type(value) is int and 1 <= value <= 65535


def validate_firewall_request(request: HostRequest) -> HostRequest:
    if request.operation != 'firewall' or request.paths:
        raise ProtocolError('invalid firewall request')
    expected = _exact(request.expected_state, frozenset({'snapshot_sha256'}))
    parameters = _exact(request.parameters, frozenset({'mode', 'ports'}))
    ports = _exact(parameters['ports'], _PORT_KEYS)
    if not all(_port(value) for value in ports.values()):
        raise ProtocolError('invalid firewall port')
    mode, digest = parameters['mode'], expected['snapshot_sha256']
    if not (mode == 'inspect' and digest is None or mode == 'converge' and _digest(digest)):
        raise ProtocolError('invalid firewall mode or snapshot')
    public = {ports['ssh'], 80, 443}
    private = {ports['application'], ports['database'], ports['distribution'], 4369}
    if public & private:
        raise ProtocolError('conflicting firewall ports')
    return request


def _identity(value: object) -> None:
    fields = _exact(value, frozenset({'source_address', 'destination_address'}))
    try:
        addresses = []
        for address in fields.values():
            if type(address) is not str or '%' in address:
                raise ValueError
            parsed = ipaddress.ip_address(address)
            if str(parsed) != address:
                raise ValueError
            addresses.append(parsed)
        if addresses[0].version != addresses[1].version:
            raise ValueError
    except ValueError as error:
        raise ProtocolError('invalid firewall administrator identity') from error


def _intervals(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, (tuple, list)) or not value:
        raise ProtocolError('invalid firewall ports')
    previous = -1
    for interval in value:
        if (not isinstance(interval, (tuple, list)) or len(interval) != 2
                or not all(_port(port) for port in interval)
                or not previous + 1 < interval[0] <= interval[1]):
            raise ProtocolError('invalid firewall port intervals')
        previous = interval[1]


def _rule(value: object, ports: object) -> None:
    value = _exact(value, _RULE_KEYS)
    family = value['family']
    if any(type(value[field]) is not str for field in ('direction', 'action', 'protocol', 'logtype')):
        raise ProtocolError('invalid firewall exception predicate')
    if (type(family) is not int or family not in {4, 6} or value['direction'] not in {'in', 'out'}
            or value['action'] not in {'allow', 'deny', 'reject', 'limit'}
            or value['protocol'] not in {'tcp', 'udp', 'any'}
            or value['logtype'] not in {'', 'log', 'log-all'}):
        raise ProtocolError('invalid firewall exception')
    try:
        for field in ('src', 'dst'):
            address = value[field]
            if type(address) is not str or '%' in address:
                raise ValueError
            parsed = ipaddress.ip_network(address)
            if parsed.version != family or str(parsed) != address:
                raise ValueError
        if ipaddress.ip_network(value['src']).prefixlen == 0:
            raise ValueError
    except ValueError as error:
        raise ProtocolError('invalid firewall exception address') from error
    for field in ('sports', 'dports'):
        _intervals(value[field])
    if any(type(value[field]) is not str for field in ('comment', 'sapp', 'dapp', 'interface_in', 'interface_out')):
        raise ProtocolError('invalid firewall exception text')
    interfaces = value['interface_in'], value['interface_out']
    if sum(bool(item) for item in interfaces) > 1 or any(
        item and re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', item) is None for item in interfaces
    ):
        raise ProtocolError('invalid firewall exception interface')
    if (value['direction'] != 'in' or value['protocol'] not in {'tcp', 'any'}
            or not isinstance(ports, Mapping) or set(ports) != _PORT_KEYS
            or not all(_port(port) for port in ports.values())
            or not any(value['dports'] is None or any(low <= port <= high for low, high in value['dports'])
                       for port in (*ports.values(), 80, 443, 4369))):
        raise ProtocolError('invalid firewall exception scope')
    if ((value['sapp'] or value['dapp']) and value['protocol'] == 'any'
            or value['sapp'] and value['sports'] is None or value['dapp'] and value['dports'] is None):
        raise ProtocolError('invalid firewall profile expansion')


def _commands(value: object, request: HostRequest) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 15:
        raise ProtocolError('invalid firewall commands')
    ports = request.parameters.get('ports')
    if not isinstance(ports, Mapping) or not all(_port(ports.get(key)) for key in _PORT_KEYS):
        if value:
            raise ProtocolError('invalid firewall command authority')
        return ()
    public = tuple(dict.fromkeys((ports['ssh'], 80, 443)))
    private = tuple(dict.fromkeys((ports['application'], ports['database'], ports['distribution'], 4369)))
    allowed = tuple(('ufw', action, 'from', source, 'to', 'any', 'port', str(port), 'proto', 'tcp')
                    for action, numbers in (('allow', public), ('deny', private)) for port in numbers
                    for source in ('0.0.0.0/0', '::/0')) + (('ufw', '--force', 'enable'),)
    commands = []
    last = -1
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) not in {3, 10} or not all(type(part) is str for part in item):
            raise ProtocolError('invalid firewall command')
        command = tuple(item)
        if command not in allowed or allowed.index(command) <= last:
            raise ProtocolError('invalid firewall command order')
        last = allowed.index(command)
        commands.append(command)
    return tuple(commands)


def validate_firewall_result(request: HostRequest, result: HostResult) -> HostResult:
    validate_result_for_request(request, result)
    if request.operation != 'firewall':
        raise ProtocolError('invalid firewall result operation')
    state = _exact(result.state, _STATE_KEYS)
    reason, changed, possible = state['reason'], state['changed'], state['possibly_changed']
    if type(reason) is not str or reason not in {'compatible', 'ufw_missing', *_REFUSALS, *_FAILURES}:
        raise ProtocolError('invalid firewall reason')
    if type(changed) is not bool or type(possible) is not bool:
        raise ProtocolError('invalid firewall mutation flags')
    if state['administrator_connection'] is not None:
        _identity(state['administrator_connection'])
    exceptions = state['exceptions']
    if not isinstance(exceptions, (list, tuple)) or len(exceptions) > 4096:
        raise ProtocolError('invalid firewall exceptions')
    for exception in exceptions:
        _rule(exception, request.parameters.get('ports'))
    proposed, completed = (_commands(state[key], request) for key in ('proposed', 'completed'))
    if completed != proposed[:len(completed)] or changed != bool(completed):
        raise ProtocolError('invalid firewall completion evidence')
    mode = request.parameters.get('mode')
    if result.outcome == 'succeeded':
        validate_firewall_request(request)
        if (reason != 'compatible' or not _digest(state['snapshot_sha256'])
                or state['administrator_connection'] is None or possible
                or mode == 'inspect' and (changed or completed)
                or mode == 'converge' and completed != proposed):
            raise ProtocolError('invalid firewall success evidence')
    else:
        if state['snapshot_sha256'] is not None:
            raise ProtocolError('failed firewall result has snapshot authority')
        if result.outcome == 'manual':
            if mode != 'converge' or not (changed or possible) or reason not in _FAILURES:
                raise ProtocolError('invalid firewall manual evidence')
        elif result.outcome == 'refused':
            if changed or possible or reason not in _REFUSALS:
                raise ProtocolError('invalid firewall refusal evidence')
        elif result.outcome == 'retryable':
            if changed or possible or not (reason in _FAILURES or reason == 'ufw_missing' and mode == 'inspect'):
                raise ProtocolError('invalid firewall retryable evidence')
        if mode != 'converge' and (changed or possible or exceptions or proposed or completed):
            raise ProtocolError('failed firewall inspection has partial evidence')
    return result


__all__ = ['validate_firewall_request', 'validate_firewall_result']
