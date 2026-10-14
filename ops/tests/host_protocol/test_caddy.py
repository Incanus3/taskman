from __future__ import annotations

import hashlib

import pytest

from taskman_ops.host_protocol import HostResult, ProtocolError
from tests.support.caddy import request


def test_request_accepts_only_deterministic_site():
    from taskman_ops.host_protocol import validate_caddy_request
    assert validate_caddy_request(request()).operation == 'caddy'
    for updates in ({'site_content': 'private arbitrary config'}, {'site_sha256': '0' * 64},
                    {'hostname': 'a; touch /tmp/b'}, {'application_port': True},
                    {'mode': 'arbitrary'}, {'receipt': {}}, {'public_probe': 'ready'}, {'extra': None}):
        with pytest.raises(ProtocolError):
            validate_caddy_request(request(**updates))


def test_result_rejects_missing_unknown_and_contradictory_evidence():
    from taskman_ops.host_protocol import validate_caddy_result
    req = request()
    state = dict(exit_code=5, failed_boundary='inspection', mutation_state='unchanged',
                 authority=None, unavailable_fields=['authority'], receipt=None,
                 validation='not-attempted', reload='not-attempted',
                 recovery={'status': 'not-needed', 'runtime_uncertain': False}, reason='inspection-failed')
    assert validate_caddy_result(req, HostResult.for_request(req, 'retryable', 'inspection failed', state))
    for updates in ({'exit_code': 0}, {'unavailable_fields': []}, {'extra': None},
                    {'mutation_state': 'changed'}, {'reload': 'succeeded'}, {'reason': 'private diagnostics'}):
        with pytest.raises(ProtocolError):
            validate_caddy_result(req, HostResult.for_request(req, 'retryable', 'failed', {**state, **updates}))


def test_unknown_result_types_are_protocol_refusals():
    from taskman_ops.host_protocol import validate_caddy_result
    req = request()
    state = dict(exit_code=5, failed_boundary='inspection', mutation_state='unchanged',
                 authority=None, unavailable_fields=['authority'], receipt=None,
                 validation='not-attempted', reload='not-attempted',
                 recovery={'status': 'not-needed', 'runtime_uncertain': False}, reason='inspection-failed')
    for updates in ({'failed_boundary': []}, {'reason': []}, {'mutation_state': []},
                    {'validation': []}, {'recovery': {'status': [], 'runtime_uncertain': False}}):
        with pytest.raises(ProtocolError):
            validate_caddy_result(req, HostResult.for_request(req, 'retryable', 'failed', {**state, **updates}))


@pytest.mark.parametrize('hostname', ['example.com', 'service.example.net', 'service.example.org', 'service.localhost'])
def test_request_rejects_renderer_reserved_hostnames(hostname):
    from taskman_ops.host_protocol import validate_caddy_request
    content = f'{hostname} {{\n\treverse_proxy 127.0.0.1:4011\n}}\n'
    with pytest.raises(ProtocolError):
        validate_caddy_request(request(hostname=hostname, site_content=content,
                                      site_sha256=hashlib.sha256(content.encode()).hexdigest()))


def test_success_result_rejects_enabled_api_service_and_preserves_masks(tmp_path, monkeypatch):
    from tests.support.caddy import host, request as inspect_request
    from taskman_ops.host_protocol import validate_caddy_result
    module, _, _ = host(tmp_path, monkeypatch)
    req = inspect_request()
    result = module.caddy(req)
    assert validate_caddy_result(req, result)
    mapping = result.to_mapping()
    mapping['state']['authority']['api_service_enabled'] = 'enabled'
    with pytest.raises(ProtocolError):
        validate_caddy_result(req, HostResult.from_mapping(mapping))
    mapping['state']['authority'].update(api_service_enabled='masked', api_service_masked=True)
    assert validate_caddy_result(req, HostResult.from_mapping(mapping))


@pytest.mark.parametrize('receipt_updates', [
    {'site_changed': True},
    {'recovery_id': '1' * 32},
    {'installed_package_version': '2.10.2'},
    {'created_masks': ['caddy.service']},
])
def test_guard_result_rejects_site_package_recovery_and_unchanged_mutation_claims(tmp_path, monkeypatch, receipt_updates):
    from tests.support.caddy import host, request as caddy_request
    from taskman_ops.host_protocol import validate_caddy_request, validate_caddy_result
    from taskman_ops.host_protocol.caddy import SITE_PATH
    module, _, _ = host(tmp_path, monkeypatch, installed=False, active=False)
    observed = module.caddy(caddy_request())
    authority = observed.state['authority']
    req = validate_caddy_request(caddy_request('guard-install', authority))
    receipt = dict(site_path=SITE_PATH, site_sha256=req.parameters['site_sha256'],
                   site_changed=False, recovery_id=None, original_absent=True,
                   original_sha256=None, original_metadata=None, created_masks=[],
                   installed_package_version=None)
    mapping = observed.to_mapping()
    mapping['state'].update(receipt=receipt, exit_code=5, failed_boundary='mask', reason='mask-failed')
    mapping['outcome'] = 'retryable'
    assert validate_caddy_result(req, HostResult.from_mapping(mapping))
    receipt.update(receipt_updates)
    with pytest.raises(ProtocolError):
        validate_caddy_result(req, HostResult.from_mapping(mapping))
    if receipt_updates == {'created_masks': ['caddy.service']}:
        mapping['state']['mutation_state'] = 'changed'
        mapping['state']['authority'].update(service_masked=True, service_enabled='masked')
        assert validate_caddy_result(req, HostResult.from_mapping(mapping))


def test_prepare_guard_receipt_cannot_adopt_original_masks_or_unrelated_site(tmp_path,monkeypatch):
    from tests.support.caddy import installation_host
    from tests.support.caddy import request as proxy_request
    from taskman_ops.host_protocol import validate_caddy_request
    module,root,state,calls=installation_host(tmp_path,monkeypatch,masked=True)
    authority=module.caddy(proxy_request()).state['authority']
    guard=module.caddy(proxy_request('guard-install',authority))
    for changes in ({'created_masks':['caddy.service','caddy-api.service']},
                    {'original_absent':False,'original_sha256':'0'*64,'original_metadata':{'uid':0,'gid':0,'mode':0o644}}):
        receipt={**guard.to_mapping()['state']['receipt'],**changes}
        with pytest.raises(ProtocolError):
            validate_caddy_request(proxy_request('prepare',authority,receipt=receipt))


def test_bootstrap_mask_allowance_is_bound_to_the_closed_guard_receipt(tmp_path,monkeypatch):
    from tests.support.caddy import installation_host
    from tests.support.caddy import request as proxy_request
    from taskman_ops.host_protocol import validate_caddy_request, validate_caddy_result
    module,root,state,calls=installation_host(tmp_path,monkeypatch,preserved=False,main=False)
    authority=module.caddy(proxy_request()).state['authority']
    req=proxy_request('guard-install',authority)
    guard=module.caddy(req)
    assert validate_caddy_result(req,guard)
    # The same intermediate authority cannot be a fresh preview or expected baseline.
    with pytest.raises(ProtocolError):
        validate_caddy_request(proxy_request('guard-install',guard.state['authority']))
    mapping=guard.to_mapping();mapping['state']['receipt']['created_masks']=['caddy.service']
    with pytest.raises(ProtocolError):
        validate_caddy_result(req,HostResult.from_mapping(mapping))


def test_activation_receipt_and_success_must_prove_prepared_site(tmp_path,monkeypatch):
    from tests.support.caddy import mutation_host
    from tests.support.caddy import request as proxy_request
    from taskman_ops.host_protocol import validate_caddy_result
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    authority=module.caddy(proxy_request()).state['authority']
    req=proxy_request('prepare',authority)
    prepared=module.caddy(req)
    mapping=prepared.to_mapping();mapping['state']['receipt']['recovery_id']=None
    with pytest.raises(ProtocolError):validate_caddy_result(req,HostResult.from_mapping(mapping))
    mapping=prepared.to_mapping();mapping['state']['authority']['site_sha256']='0'*64
    with pytest.raises(ProtocolError):validate_caddy_result(req,HostResult.from_mapping(mapping))


def test_prepare_requires_receipt_for_both_persistent_installation_guards(tmp_path,monkeypatch):
    from tests.support.caddy import installation_host,request as proxy_request
    from taskman_ops.host_protocol import validate_caddy_request
    module,root,state,calls=installation_host(tmp_path,monkeypatch)
    authority=module.caddy(proxy_request()).state['authority']
    guarded=module.caddy(proxy_request('guard-install',authority))
    receipt=guarded.to_mapping()['state']['receipt'];receipt['created_masks']=['caddy.service']
    with pytest.raises(ProtocolError):validate_caddy_request(proxy_request('prepare',authority,receipt=receipt))


@pytest.mark.parametrize('change',[{'mutation_state':'unchanged'}, {'reload':'failed'}, {'receipt_changed':False}])
def test_prepared_success_cannot_erase_site_mutation_or_claim_failed_reload(tmp_path,monkeypatch,change):
    from tests.support.caddy import mutation_host,request as proxy_request
    from taskman_ops.host_protocol import validate_caddy_result
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    authority=module.caddy(proxy_request()).state['authority']
    req=proxy_request('prepare',authority);result=module.caddy(req)
    mapping=result.to_mapping()
    if 'receipt_changed' in change:mapping['state']['receipt']['site_changed']=False
    else:mapping['state'].update(change)
    with pytest.raises(ProtocolError):validate_caddy_result(req,HostResult.from_mapping(mapping))


@pytest.mark.parametrize('failure', [False, True])
def test_activation_result_cannot_replace_prepared_recovery_receipt(tmp_path,monkeypatch,failure):
    from taskman_ops.host_protocol import validate_caddy_result
    from tests.support.caddy import mutation_host,request as proxy_request
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    authority=module.caddy(proxy_request()).state['authority']
    prepared=module.caddy(proxy_request('prepare',authority))
    req=proxy_request('activate',prepared.state['authority'],receipt=prepared.state['receipt'],public_probe='not-ready')
    mapping=module.caddy(req).to_mapping()
    if failure:
        mapping.update(outcome='retryable')
        mapping['state'].update(exit_code=8,failed_boundary='activation',reason='activation-failed',reload='unknown',
                                recovery={'status':'unknown','runtime_uncertain':True})
    mapping['state']['receipt'].update(site_changed=False,recovery_id=None)
    with pytest.raises(ProtocolError):validate_caddy_result(req,HostResult.from_mapping(mapping))


@pytest.mark.parametrize('mutation', ['unchanged', 'unknown'])
def test_successful_activation_reload_requires_confirmed_mutation(tmp_path,monkeypatch,mutation):
    from taskman_ops.host_protocol import validate_caddy_result
    from tests.support.caddy import mutation_host,request as proxy_request
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    prepared=module.caddy(proxy_request('prepare',module.caddy(proxy_request()).state['authority']))
    req=proxy_request('activate',prepared.state['authority'],receipt=prepared.state['receipt'],public_probe='not-ready')
    mapping=module.caddy(req).to_mapping();mapping['state']['mutation_state']=mutation
    with pytest.raises(ProtocolError):validate_caddy_result(req,HostResult.from_mapping(mapping))
