from __future__ import annotations

from dataclasses import replace
import importlib

import pytest

from taskman_ops.host_protocol import HostRequest, HostResult, ProtocolError, encode_result, decode_result


def protocol():
    return importlib.import_module('taskman_ops.host_protocol.firewall')


def request(mode='inspect', digest=None):
    return HostRequest(3, 'firewall', 'op-0123456789abcdef0123456789abcdef',
                       {'snapshot_sha256': digest}, {}, {'mode': mode, 'ports': {
                           'ssh': 2202, 'application': 4000, 'database': 5432, 'distribution': 6789}})


def result(req=None, **changes):
    req = req or request()
    state = {'reason': 'compatible', 'changed': False, 'possibly_changed': False,
             'snapshot_sha256': 'a' * 64,
             'administrator_connection': {'source_address': '192.0.2.10', 'destination_address': '198.51.100.2'},
             'exceptions': [], 'proposed': [['ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp']], 'completed': []}
    state.update(changes)
    return HostResult.for_request(req, 'succeeded', 'firewall compatible', state)


@pytest.mark.parametrize('invalid', [True, 0, 65536, '2202'])
def test_firewall_request_rejects_non_integer_or_invalid_port(invalid):
    req = request()
    bad = replace(req, parameters={**req.parameters, 'ports': {**req.parameters['ports'], 'ssh': invalid}})
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_request(bad)


@pytest.mark.parametrize('mode,digest', [('inspect', 'a' * 64), ('converge', None), ('other', None),
                                       ('converge', 'A' * 64)])
def test_firewall_request_rejects_mismatched_mode_and_digest(mode, digest):
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_request(request(mode, digest))


@pytest.mark.parametrize('field,value', [('paths', {'root': '/opt/taskman'}),
    ('expected_state', {'snapshot_sha256': None, 'extra': None}),
    ('parameters', {'mode': 'inspect', 'ports': {}, 'extra': None})])
def test_firewall_request_rejects_extra_or_missing_fields(field, value):
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_request(replace(request(), **{field: value}))


def test_firewall_successful_inspection_roundtrips_with_closed_evidence():
    req = request()
    actual = decode_result(encode_result(result(req)))
    assert protocol().validate_firewall_result(req, actual) == actual


@pytest.mark.parametrize('changes', [ {'changed': True}, {'possibly_changed': True},
    {'completed': [['ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp']]}, {'snapshot_sha256': None},
    {'reason': 'ufw_missing'}, {'administrator_connection': None}, {'extra': None},
    {'exceptions': [{'family': 4}]}, {'changed': 1}])
def test_firewall_result_rejects_incoherent_or_unclosed_evidence(changes):
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(request(), result(**changes))


def test_firewall_result_checks_envelope_identity():
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(request(), replace(result(), correlation_id='op-' + 'f' * 32))


def test_firewall_manual_result_preserves_successful_prefix_and_possible_change():
    req = request('converge', 'a' * 64)
    actual = replace(result(req, reason='execution_failed', changed=True, possibly_changed=True,
        snapshot_sha256=None, completed=[['ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp']]), outcome='manual')
    assert protocol().validate_firewall_result(req, actual) == actual
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(req, replace(actual, outcome='retryable'))


def test_firewall_failed_inspection_has_no_partial_preview():
    actual = replace(result(reason='inspection_failed', snapshot_sha256=None), outcome='retryable')
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(request(), actual)


def test_firewall_known_change_requires_completed_commands():
    req = request('converge', 'a' * 64)
    actual = replace(result(req, reason='execution_failed', changed=True, snapshot_sha256=None), outcome='manual')
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(req, actual)


@pytest.mark.parametrize('reason', [None, {'unexpected': 'value'}, 1])
def test_invalid_reason_is_a_protocol_error(reason):
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(request(), replace(result(reason=reason), outcome='retryable'))


@pytest.mark.parametrize('field', ['direction', 'action', 'protocol', 'logtype'])
def test_non_string_rule_predicates_are_protocol_errors(field):
    exception = {'family': 4, 'direction': 'in', 'action': 'allow', 'protocol': 'tcp',
        'src': '192.0.2.0/24', 'dst': '0.0.0.0/0', 'sports': None, 'dports': ((5432, 5432),),
        'interface_in': '', 'interface_out': '', 'logtype': '', 'comment': '', 'sapp': '', 'dapp': ''}
    exception[field] = {'unexpected': 'value'}
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(request(), result(exceptions=[exception]))


def test_outgoing_or_unowned_rules_are_not_firewall_preview_exceptions():
    exception = {'family': 4, 'direction': 'out', 'action': 'allow', 'protocol': 'tcp',
        'src': '192.0.2.0/24', 'dst': '0.0.0.0/0', 'sports': None, 'dports': ((5432, 5432),),
        'interface_in': '', 'interface_out': '', 'logtype': '', 'comment': '', 'sapp': '', 'dapp': ''}
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(request(), result(exceptions=[exception]))
    exception.update(direction='in', dports=((10050, 10050),))
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(request(), result(exceptions=[exception]))


def allowed_commands(req):
    ports = req.parameters['ports']
    return tuple(('ufw', action, 'from', source, 'to', 'any', 'port', str(port), 'proto', 'tcp')
        for action, values in (('allow', (ports['ssh'], 80, 443)),
                               ('deny', (ports['application'], ports['database'], ports['distribution'], 4369)))
        for port in dict.fromkeys(values) for source in ('0.0.0.0/0', '::/0')) + (('ufw', '--force', 'enable'),)


def test_firewall_accepts_complete_fifteen_command_plan_and_partial_prefix():
    req = request('converge', 'a' * 64)
    commands = allowed_commands(req)
    actual = result(req, proposed=commands, completed=commands, changed=True)
    assert len(commands) == 15
    assert protocol().validate_firewall_result(req, actual) == actual
    partial = replace(result(req, proposed=commands, completed=commands[:2], changed=True,
        possibly_changed=True, reason='execution_failed', snapshot_sha256=None), outcome='manual')
    assert protocol().validate_firewall_result(req, partial) == partial


@pytest.mark.parametrize('commands', [
    (('ufw', 'allow', 'from', '192.0.2.0/24', 'to', 'any', 'port', '2202', 'proto', 'tcp'),),
    (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '5432', 'proto', 'tcp'),),
    (('ufw', 'reject', 'from', '::/0', 'to', 'any', 'port', '4000', 'proto', 'tcp'),),
    (('ufw', 'allow', '2202/tcp'),),
])
def test_firewall_rejects_commands_outside_fixed_family_port_action_authority(commands):
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(request(), result(proposed=commands))


@pytest.mark.parametrize('bad', ['duplicate', 'reversed-families', 'after-enable', 'over-budget', 'non-prefix'])
def test_firewall_rejects_order_duplicates_budget_and_nonprefix_completion(bad):
    req = request('converge', 'a' * 64)
    commands = allowed_commands(req)
    completed = ()
    proposed = {'duplicate': commands[:1] * 2, 'reversed-families': commands[:2][::-1],
        'after-enable': (commands[-1], commands[0]), 'over-budget': commands + commands[:1],
        'non-prefix': commands}[bad]
    if bad == 'non-prefix':
        completed = commands[1:2]
    actual = replace(result(req, proposed=proposed, completed=completed, changed=bool(completed),
        possibly_changed=True, reason='execution_failed', snapshot_sha256=None), outcome='manual')
    with pytest.raises(ProtocolError):
        protocol().validate_firewall_result(req, actual)
