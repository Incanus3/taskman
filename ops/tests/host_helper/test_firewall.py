from __future__ import annotations

from dataclasses import asdict, replace
import importlib
import json
import sys

import pytest

from taskman_ops.host_helper.ufw_adapter import UfwInspectionError, UfwRule, UfwSnapshot
from taskman_ops.host_protocol import HostRequest


def policy():
    return importlib.import_module('taskman_ops.host_helper.operations.firewall')


def ports():
    return {'ssh': 2202, 'application': 4000, 'database': 5432, 'distribution': 6789}


def administrator_connection():
    return {'source_address': '192.0.2.10', 'destination_address': '198.51.100.2'}


def rule(*, family=4, direction='in', action='allow', protocol='tcp', src=None, dst=None,
         sports=None, dports=((2202, 2202),), interface_in='', interface_out='',
         logtype='', comment='', sapp='', dapp=''):
    whole = '0.0.0.0/0' if family == 4 else '::/0'
    return UfwRule(family, direction, action, protocol, src or whole, dst or whole,
                   sports, dports, interface_in, interface_out, logtype, comment, sapp, dapp)


def snapshot(rules=(), active=True):
    return UfwSnapshot(active, (('default_forward_policy', 'DROP'),
                               ('default_input_policy', 'DROP'),
                               ('default_output_policy', 'ACCEPT'), ('ipv6', 'yes')), tuple(rules))


def complete_rules():
    return tuple(rule(family=family, action=action, dports=((port, port),))
                 for family in (4, 6) for action, values in
                 (('allow', (2202, 80, 443)), ('deny', (4000, 5432, 6789, 4369))) for port in values)


def plan(original, service_ports=None, identity=None):
    return policy().plan_firewall(original, service_ports or ports(), identity or administrator_connection())


def request(mode='inspect', digest=None):
    return HostRequest(3, 'firewall', 'op-0123456789abcdef0123456789abcdef',
                       {'snapshot_sha256': digest}, {}, {'mode': mode, 'ports': ports()})


def test_restricted_database_allow_remains_before_global_fallback():
    existing = rule(src='192.0.2.0/24', dports=((5432, 5432),), comment='operator')
    original = snapshot((existing,))
    delta = plan(original)
    assert ('ufw', 'deny', 'from', '0.0.0.0/0', 'to', 'any', 'port', '5432', 'proto', 'tcp') in delta.proposed
    assert delta.exceptions == (existing,)
    assert original.rules == (existing,)


def test_equivalent_ranges_multiport_and_profile_cover_both_families():
    rules = tuple(rule(family=f, dports=((80, 80), (443, 443), (2200, 2205)), dapp='Web')
                  for f in (4, 6)) + tuple(rule(family=f, action='deny', dports=((3900, 7000),))
                                         for f in (4, 6))
    assert plan(snapshot(rules)).proposed == ()


def test_missing_family_adds_only_its_missing_ports_and_deduplicates_private():
    delta = plan(snapshot(tuple(r for r in complete_rules() if r.family == 4)),
                 {**ports(), 'distribution': 5432})
    assert delta.proposed == tuple(('ufw', action, 'from', '::/0', 'to', 'any', 'port', str(port), 'proto', 'tcp')
        for action, values in (('allow', (2202, 80, 443)), ('deny', (4000, 5432, 4369))) for port in values)


@pytest.mark.parametrize('existing', [rule(action='allow', dports=((3900, 4100),)),
    rule(action='limit', dports=((5432, 5432),)), rule(action='allow', protocol='any', dports=None),
    rule(action='deny', dports=((80, 80),), interface_in='eth0'),
    rule(action='reject', dports=((443, 443),), dst='198.51.100.2/32'),
    rule(action='limit'), rule(src='0.0.0.0/0', dports=((5432, 5432),))])
def test_all_source_conflicts_are_refused_despite_other_predicates(existing):
    with pytest.raises(UfwInspectionError, match='policy_conflict'):
        plan(snapshot((existing,)))


def test_udp_and_outgoing_rules_neither_conflict_nor_cover():
    original = snapshot((rule(protocol='udp', dports=None), rule(direction='out', dports=None)))
    assert ('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp') in plan(original).proposed
    assert ('ufw', 'deny', 'from', '0.0.0.0/0', 'to', 'any', 'port', '5432', 'proto', 'tcp') in plan(original).proposed


def test_collectively_exhaustive_private_exceptions_remain_exceptions():
    original = snapshot((rule(src='0.0.0.0/1', dports=((5432, 5432),)),
                         rule(src='128.0.0.0/1', dports=((5432, 5432),))))
    assert plan(original).exceptions == original.rules


@pytest.mark.parametrize('action', ['deny', 'reject', 'limit'])
def test_reachable_controller_block_refuses_but_earlier_definite_allow_proves_path(action):
    blocked = rule(action=action, src='192.0.2.0/24')
    with pytest.raises(UfwInspectionError, match='controller_path_blocked'):
        plan(snapshot((blocked,)))
    assert plan(snapshot((rule(src='192.0.2.10/32'), blocked))).exceptions == (
        rule(src='192.0.2.10/32'), blocked)
    assert plan(snapshot((replace(blocked, src='203.0.113.0/24'),))).proposed


@pytest.mark.parametrize('predicate', [{'sports': ((1024, 65535),)}, {'interface_in': 'eth0'}])
def test_uncertain_allow_cannot_shadow_possible_controller_block(predicate):
    original = snapshot((rule(src='192.0.2.0/24', **predicate),
                         rule(src='192.0.2.0/24', action='deny')))
    with pytest.raises(UfwInspectionError, match='controller_path_blocked'):
        plan(original)


def test_destination_mismatch_skips_block_and_hash_includes_identity_comments_order_defaults():
    original = snapshot((rule(src='192.0.2.0/24', dst='198.51.100.3/32', action='deny'),))
    digest = plan(original).snapshot_sha256
    assert digest != plan(replace(original, rules=(replace(original.rules[0], comment='note'),))).snapshot_sha256
    assert digest != plan(original, identity={**administrator_connection(), 'source_address': '192.0.2.11'}).snapshot_sha256
    assert digest == plan(original).snapshot_sha256


@pytest.mark.parametrize('changes', [{'default_input_policy': 'REJECT'}, {'ipv6': 'no'},
                                     {'default_output_policy': 'DROP'}, {'default_forward_policy': 'ACCEPT'}])
def test_incompatible_defaults_refuse_without_adjustment(changes):
    original = snapshot()
    with pytest.raises(UfwInspectionError, match='policy_conflict'):
        plan(replace(original, defaults=tuple(sorted({**dict(original.defaults), **changes}.items()))))


def test_public_private_intersection_refuses():
    with pytest.raises(UfwInspectionError, match='invalid_input'):
        plan(snapshot(), {**ports(), 'ssh': 4369})


@pytest.mark.parametrize('connection', ['', 'bad', '192.0.2.10 123 198.51.100.2 22',
                                       '192.0.2.10 0 198.51.100.2 2202',
                                       '192.0.2.10 123 ::1 2202'])
def test_invalid_ssh_connection_fails_before_reader(monkeypatch, connection):
    module = policy()
    monkeypatch.setenv('SSH_CONNECTION', connection)
    monkeypatch.setattr(module, 'inspect_ufw', lambda **kw: pytest.fail('reader called'))
    result = module.firewall(request())
    assert result.outcome == 'refused'
    assert result.state['reason'] == 'invalid_input'
    assert result.state['administrator_connection'] is None
    assert not result.state['possibly_changed']


@pytest.fixture
def native(monkeypatch, tmp_path):
    module = policy()
    monkeypatch.setenv('SSH_CONNECTION', '192.0.2.10 45123 198.51.100.2 2202')
    state_path = tmp_path / 'state.json'
    command_path = tmp_path / 'ufw'
    command_path.write_text('''#!''' + sys.executable + '''
import json, os, sys
from pathlib import Path
path = Path(os.environ['FAKE_UFW_STATE'])
data = json.loads(path.read_text())
args = sys.argv[1:]
data['commands'].append(args)
if args == ['--force', 'enable']:
    data['active'] = True
else:
    action, _, source, _, _, _, port, _, _ = args
    port = int(port)
    families = (4,) if source == '0.0.0.0/0' else (6,)
    for family in families:
        whole = '0.0.0.0/0' if family == 4 else '::/0'
        added = {'family': family, 'direction': 'in', 'action': action, 'protocol': 'tcp',
                 'src': whole, 'dst': whole, 'sports': None, 'dports': [[port, port]],
                 'interface_in': '', 'interface_out': '', 'logtype': '', 'comment': '',
                 'sapp': '', 'dapp': ''}
        matching = next((i for i, saved in enumerate(data['rules'])
            if {**saved, 'comment': ''} == added), None)
        if matching is None:
            data['rules'].append(added)
        else:
            data['rules'][matching] = added
path.write_text(json.dumps(data))
if data.get('failure') == len(data['commands']):
    sys.exit(1)
if data.get('overflow'):
    sys.stdout.write('x' * (1024 * 1024 + 1))
if data.get('aggregate_overflow'):
    sys.stdout.write('x' * (600 * 1024))
    sys.stderr.write('x' * (600 * 1024))
''')
    command_path.chmod(0o755)
    monkeypatch.setenv('FAKE_UFW_STATE', str(state_path))
    # Only native executable selection is substituted; bounded execution stays real.
    real_run = module.run_command
    monkeypatch.setattr(module, 'run_command', lambda argv, **kw: real_run((str(command_path), *argv[1:]), **kw))
    def reader(**kw):
        data = json.loads(state_path.read_text())
        saved = tuple(UfwRule(**{**r, 'sports': None if r['sports'] is None else tuple(map(tuple, r['sports'])),
                                      'dports': None if r['dports'] is None else tuple(map(tuple, r['dports']))})
                      for r in data['rules'])
        return snapshot(tuple(r for f in (4, 6) for r in saved if r.family == f), data['active'])
    monkeypatch.setattr(module, 'inspect_ufw', reader)
    def setup(original, **values):
        state_path.write_text(json.dumps({'active': original.active,
            'rules': [asdict(r) for r in original.rules],
            'commands': [], **values}))
        return plan(original).snapshot_sha256
    return module, state_path, setup


@pytest.mark.parametrize('original', [snapshot(active=False), snapshot(complete_rules(), active=False),
    snapshot(complete_rules()[:3]), snapshot(complete_rules())])
def test_native_convergence_preserves_state_and_repeated_run_is_noop(native, original):
    module, state_path, setup = native
    digest = setup(original)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'succeeded'
    assert result.state['changed'] == bool(result.state['completed'])
    data = json.loads(state_path.read_text())
    assert data['active']
    if not original.active:
        assert data['commands'][-1] == ['--force', 'enable']
    second = module.firewall(request('converge', result.state['snapshot_sha256']))
    assert second.outcome == 'succeeded'
    assert second.state['completed'] == ()
    assert not second.state['changed']
    assert json.loads(state_path.read_text())['commands'] == data['commands']


def test_snapshot_drift_refuses_before_native_dispatch(native):
    module, state_path, setup = native
    setup(snapshot())
    result = module.firewall(request('converge', '0' * 64))
    assert result.outcome == 'refused'
    assert result.state['reason'] == 'policy_conflict'
    assert json.loads(state_path.read_text())['commands'] == []


@pytest.mark.parametrize('failure', [1, 2, 15])
def test_failed_native_command_retains_partial_change_without_rollback(native, failure):
    module, state_path, setup = native
    digest = setup(snapshot(active=False), failure=failure)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'manual'
    assert result.state['reason'] == 'execution_failed'
    assert result.state['possibly_changed']
    assert result.state['changed'] == (failure > 1)
    assert len(result.state['completed']) == failure - 1
    assert len(json.loads(state_path.read_text())['commands']) == failure
    assert result.state['snapshot_sha256'] is None


def test_native_output_overflow_keeps_possible_mutation(native):
    module, state_path, setup = native
    digest = setup(snapshot(), overflow=True)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'manual'
    assert result.state['possibly_changed']
    assert not result.state['changed']


def test_reader_failure_and_unexpected_postmutation_failure_keep_evidence(native, monkeypatch):
    module, state_path, setup = native
    digest = setup(snapshot(active=False))
    reader = module.inspect_ufw
    count = 0
    def failing_reader(**kw):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('private detail')
        return reader(**kw)
    monkeypatch.setattr(module, 'inspect_ufw', failing_reader)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'manual'
    assert result.state['changed']
    assert len(result.state['completed']) == 14
    assert json.loads(state_path.read_text())['active'] is False
    assert 'private detail' not in result.message


def test_original_comment_or_duplicate_rewrite_is_postcheck_failure(native, monkeypatch):
    module, state_path, setup = native
    original = snapshot((rule(), rule()), active=False)
    digest = setup(original)
    reader = module.inspect_ufw
    count = 0
    def rewriting_reader(**kw):
        nonlocal count
        count += 1
        value = reader(**kw)
        return value if count == 1 else replace(value, rules=(replace(value.rules[0], comment='rewritten'), *value.rules[2:]))
    monkeypatch.setattr(module, 'inspect_ufw', rewriting_reader)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'manual'
    assert result.state['reason'] == 'postcheck_failed'
    assert not json.loads(state_path.read_text())['active']


def test_missing_package_is_read_only_prerequisite(monkeypatch):
    module = policy()
    monkeypatch.setenv('SSH_CONNECTION', '192.0.2.10 45123 198.51.100.2 2202')
    def missing(**kw):
        raise UfwInspectionError('ufw_missing')
    monkeypatch.setattr(module, 'inspect_ufw', missing)
    result = module.firewall(request())
    assert result.outcome == 'retryable'
    assert result.state['reason'] == 'ufw_missing'
    assert result.state['snapshot_sha256'] is None
    assert result.state['proposed'] == ()


def test_dispatch_runs_closed_firewall_operation(native):
    from taskman_ops.host_helper import __main__ as entrypoint
    module, state_path, setup = native
    setup(snapshot(active=False))
    result = entrypoint._dispatch(request())
    assert result.outcome == 'succeeded'
    assert result.state['proposed'][0] == ('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp')
    assert json.loads(state_path.read_text())['commands'] == []


def test_dispatch_reduces_invalid_result_after_converge_to_possible_mutation(monkeypatch):
    from taskman_ops.host_helper import __main__ as entrypoint
    from taskman_ops.host_protocol import HostResult, validate_firewall_result
    req = request('converge', 'a' * 64)
    monkeypatch.setitem(entrypoint._DISPATCH, 'firewall', lambda req: HostResult.for_request(req, 'succeeded', 'bad', {}))
    result = entrypoint._dispatch(req)
    assert result.outcome == 'manual'
    assert result.state['possibly_changed']
    assert validate_firewall_result(req, result) == result


def test_encoding_failure_preserves_known_command_prefix_and_possible_mutation(monkeypatch):
    from taskman_ops.host_helper import __main__ as entrypoint
    from taskman_ops.host_protocol import HostResult, decode_result, validate_firewall_result
    req = request('converge', 'a' * 64)
    state = {'reason': 'compatible', 'changed': True, 'possibly_changed': False,
             'snapshot_sha256': 'b' * 64, 'administrator_connection': administrator_connection(),
             'exceptions': (), 'proposed': (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),),
             'completed': (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),)}
    original = HostResult.for_request(req, 'succeeded', 'ok', state)
    real_encode = entrypoint.encode_result
    def broken_once(result):
        if result is original:
            raise ValueError('encoding failure')
        return real_encode(result)
    monkeypatch.setattr(entrypoint, 'encode_result', broken_once)
    output = entrypoint._encode_or_internal_failure(original, operation=req.operation,
        correlation_id=req.correlation_id, request=req)
    result = decode_result(output)
    assert result.outcome == 'manual'
    assert result.state['changed'] and result.state['possibly_changed']
    assert result.state['completed'] == (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),)
    assert validate_firewall_result(req, result) == result


@pytest.mark.parametrize('failure_reason', ['inspection_failed', 'unsupported_state'])
def test_warning_or_unsupported_reader_refuses_without_native_mutation(native, monkeypatch, failure_reason):
    module, state_path, setup = native
    setup(snapshot())
    def failed(**kwargs):
        raise UfwInspectionError(failure_reason)
    monkeypatch.setattr(module, 'inspect_ufw', failed)
    result = module.firewall(request())
    assert result.outcome == ('retryable' if failure_reason == 'inspection_failed' else 'refused')
    assert result.state['reason'] == failure_reason
    assert result.state['snapshot_sha256'] is None
    assert result.state['exceptions'] == result.state['proposed'] == ()
    assert json.loads(state_path.read_text())['commands'] == []


def test_successful_native_addition_with_warning_retains_known_change(native, monkeypatch):
    module, state_path, setup = native
    digest = setup(snapshot())
    original_run = module.run_command
    def warning(argv, **kwargs):
        completed = original_run(argv, **kwargs)
        completed.stderr = b'native warning'
        return completed
    monkeypatch.setattr(module, 'run_command', warning)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'succeeded'
    assert result.state['changed']
    assert result.state['completed'][0] == ('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp')
    assert 'warning' not in result.message


def test_deadline_reached_after_success_keeps_known_change(native, monkeypatch):
    module, state_path, setup = native
    digest = setup(snapshot())
    real_run = module.run_command
    def exhausted(argv, **kwargs):
        completed = real_run(argv, **kwargs)
        monkeypatch.setattr(module.time, 'monotonic', lambda: 10**20)
        return completed
    monkeypatch.setattr(module, 'run_command', exhausted)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'manual'
    assert result.state['changed']
    assert not result.state['possibly_changed']
    assert result.state['completed'] == (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),)


def test_final_result_overflow_after_mutation_preserves_known_commands(native, monkeypatch):
    module, state_path, setup = native
    digest = setup(snapshot())
    real_encode = module.encode_result
    def overflow(result):
        if result.outcome == 'succeeded':
            from taskman_ops.host_protocol import ProtocolError
            raise ProtocolError('result exceeds output bound')
        return real_encode(result)
    monkeypatch.setattr(module, 'encode_result', overflow)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'manual'
    assert result.state['changed']
    assert result.state['snapshot_sha256'] is None
    assert len(result.state['completed']) == 14


def test_snapshot_hash_binds_order_activation_and_default_choice():
    left, right = rule(src='203.0.113.0/24'), rule(dports=((10050, 10050),), comment='monitor')
    original = snapshot((left, right))
    assert plan(original).snapshot_sha256 != plan(snapshot((right, left))).snapshot_sha256
    assert plan(original).snapshot_sha256 != plan(replace(original, active=False)).snapshot_sha256
    defaults = dict(original.defaults)
    defaults['default_forward_policy'] = 'REJECT'
    assert plan(original).snapshot_sha256 != plan(replace(original, defaults=tuple(defaults.items()))).snapshot_sha256
    assert plan(original).snapshot_sha256 == plan(replace(original, defaults=tuple(reversed(original.defaults)))).snapshot_sha256


def test_ipv6_controller_path_has_separate_ordered_evidence():
    identity = {'source_address': '2001:db8::10', 'destination_address': '2001:db8:1::2'}
    blocked = rule(family=6, src='2001:db8::/64', action='reject')
    with pytest.raises(UfwInspectionError, match='controller_path_blocked'):
        plan(snapshot((rule(), blocked)), identity=identity)
    assert plan(snapshot((rule(family=6, src='2001:db8::10/128'), blocked)), identity=identity).proposed


def test_saved_exception_comments_profiles_interfaces_and_relative_order_survive_native_append(native):
    module, state_path, setup = native
    saved = (rule(src='203.0.113.0/24', dports=((5432, 5432),), comment='operator database'),
             rule(src='192.0.2.0/24', dports=((80, 80), (443, 443)), dapp='Web', logtype='log'),
             rule(interface_in='eth0', dports=((10050, 10050),), comment='monitor'),
             rule(family=6, src='2001:db8::/64', action='deny', dports=((443, 443),), comment='restricted'))
    original = snapshot(saved, active=False)
    digest = setup(original)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'succeeded'
    observed = module.inspect_ufw(deadline=10**20)
    assert tuple(r for r in observed.rules if r in saved) == saved
    assert result.state['exceptions'][0]['comment'] == 'operator database'
    assert result.state['exceptions'][1]['dapp'] == 'Web'
    assert json.loads(state_path.read_text())['commands'][-1] == ['--force', 'enable']


def test_combined_native_output_overflow_retains_observed_success(native):
    module, state_path, setup = native
    digest = setup(snapshot(), aggregate_overflow=True)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'manual'
    assert result.state['reason'] == 'execution_failed'
    assert result.state['changed']
    assert result.state['completed'] == (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),)
    assert len(json.loads(state_path.read_text())['commands']) == 1


def test_inspection_deadline_includes_policy_and_result_building(native, monkeypatch):
    module, state_path, setup = native
    setup(snapshot())
    original = module.plan_firewall
    def exhausted(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(module.time, 'monotonic', lambda: 10**20)
        return result
    monkeypatch.setattr(module, 'plan_firewall', exhausted)
    result = module.firewall(request())
    assert result.outcome == 'retryable'
    assert result.state['reason'] == 'inspection_failed'
    assert result.state['snapshot_sha256'] is None
    assert not json.loads(state_path.read_text())['commands']


def test_invalid_final_flag_does_not_drop_valid_completed_command_prefix(monkeypatch):
    from taskman_ops.host_helper import __main__ as entrypoint
    from taskman_ops.host_protocol import HostResult, validate_firewall_result
    req = request('converge', 'a' * 64)
    state = {'reason': 'compatible', 'changed': False, 'possibly_changed': False,
        'snapshot_sha256': 'b' * 64, 'administrator_connection': administrator_connection(),
        'exceptions': (), 'proposed': (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),),
        'completed': (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),)}
    monkeypatch.setitem(entrypoint._DISPATCH, 'firewall',
                        lambda request: HostResult.for_request(request, 'succeeded', 'invalid flags', state))
    result = entrypoint._dispatch(req)
    assert result.outcome == 'manual'
    assert result.state['possibly_changed'] and result.state['changed']
    assert result.state['completed'] == (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),)
    assert validate_firewall_result(req, result) == result


def test_uncorrelated_final_result_cannot_claim_known_firewall_completion(monkeypatch):
    from taskman_ops.host_helper import __main__ as entrypoint
    from taskman_ops.host_protocol import HostResult, validate_firewall_result
    req = request('converge', 'a' * 64)
    state = {'reason': 'compatible', 'changed': True, 'possibly_changed': False,
        'snapshot_sha256': 'b' * 64, 'administrator_connection': administrator_connection(),
        'exceptions': (), 'proposed': (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),),
        'completed': (('ufw', 'allow', 'from', '0.0.0.0/0', 'to', 'any', 'port', '2202', 'proto', 'tcp'),)}
    unrelated = HostResult(3, 'firewall', 'op-' + 'f' * 32, 'succeeded', 'other invocation', state, ())
    monkeypatch.setitem(entrypoint._DISPATCH, 'firewall', lambda request: unrelated)
    result = entrypoint._dispatch(req)
    assert result.outcome == 'manual'
    assert result.state['possibly_changed']
    assert not result.state['changed']
    assert result.state['completed'] == ()
    assert validate_firewall_result(req, result) == result


def test_oversized_inspection_result_is_an_inspection_failure(native, monkeypatch):
    module, state_path, setup = native
    setup(snapshot())
    def overflow(result):
        from taskman_ops.host_protocol import ProtocolError
        raise ProtocolError('result exceeds output bound')
    monkeypatch.setattr(module, 'encode_result', overflow)
    result = module.firewall(request())
    assert result.outcome == 'retryable'
    assert result.state['reason'] == 'inspection_failed'
    assert result.state['snapshot_sha256'] is None
    assert result.state['proposed'] == ()
    assert not json.loads(state_path.read_text())['commands']


@pytest.mark.parametrize('action', ['deny', 'reject', 'limit'])
@pytest.mark.parametrize('predicate', [{'sports': ((1024, 65535),)}, {'interface_in': 'eth0'}])
def test_uncertain_controller_block_before_definite_allow_refuses(action, predicate):
    original = snapshot((rule(src='192.0.2.0/24', action=action, **predicate),
                         rule(src='192.0.2.10/32')))
    with pytest.raises(UfwInspectionError, match='controller_path_blocked'):
        plan(original)


@pytest.mark.parametrize('existing_family', [4, 6])
@pytest.mark.parametrize('action,port', [('allow', 443), ('deny', 5432)])
def test_missing_family_convergence_preserves_existing_exact_rule_comment(native, existing_family, action, port):
    module, state_path, setup = native
    saved = rule(family=existing_family, action=action, dports=((port, port),), comment='operator metadata')
    rules = tuple(saved if r.family == existing_family and r.action == action and r.dports == saved.dports else r
        for r in complete_rules() if not (r.family != existing_family and r.action == action and r.dports == saved.dports))
    original = snapshot(rules)
    digest = setup(original)
    result = module.firewall(request('converge', digest))
    assert result.outcome == 'succeeded'
    missing_source = '::/0' if existing_family == 4 else '0.0.0.0/0'
    assert result.state['completed'] == (('ufw', action, 'from', missing_source, 'to', 'any', 'port', str(port), 'proto', 'tcp'),)
    observed = module.inspect_ufw(deadline=10**20)
    assert module._preserved(original, observed)
    assert saved in observed.rules
    assert plan(observed).proposed == ()
    before = state_path.read_bytes()
    rerun = module.firewall(request('converge', result.state['snapshot_sha256']))
    assert rerun.outcome == 'succeeded' and not rerun.state['changed']
    assert state_path.read_bytes() == before
