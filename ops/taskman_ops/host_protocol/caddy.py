"""Closed, secret-free proxy authority and request-correlated evidence."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import ipaddress
import re

from .envelope import HostRequest, HostResult, validate_result_for_request
from .identifiers import ProtocolError

SITE_PATH = '/etc/caddy/sites-enabled/taskman.caddy'
MAIN_CONTENT = 'import /etc/caddy/sites-enabled/*.caddy\n'
MODES = frozenset({'inspect', 'guard-install', 'prepare', 'activate'})
_AUTHORITY_KEYS = frozenset({'branch', 'package_installed', 'package_version',
    'executable_sha256', 'executable_trusted', 'unit_path', 'unit_sha256', 'unit_trusted',
    'api_unit_path', 'api_unit_sha256', 'service_active', 'service_enabled', 'service_masked',
    'api_service_active', 'api_service_enabled', 'api_service_masked', 'main_pid',
    'public_listeners', 'configuration_directory', 'main', 'glob_import_present',
    'site_directory', 'site', 'site_sha256'})
_STATE_KEYS = frozenset({'exit_code', 'failed_boundary', 'mutation_state', 'authority',
    'unavailable_fields', 'receipt', 'validation', 'reload', 'recovery', 'reason'})
_BOUNDARIES = frozenset({'input', 'inspection', 'authority', 'mask', 'package', 'site',
                        'validation', 'activation', 'recovery'})
_REASONS = frozenset({'compatible', 'invalid-input', 'inspection-failed', 'unsafe-authority',
                     'unsupported-platform', 'authority-changed', 'mutation-unavailable',
                     'mask-failed', 'package-failed', 'site-failed', 'validation-failed',
                     'activation-failed', 'recovery-failed'})


def _exact(value, keys):
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ProtocolError('invalid Caddy fields')
    return value


def _digest(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _version(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9.+:~_-]{1,128}', value) is not None


def _metadata(value, *, directory=False, site=False):
    if value is None:
        return
    value = _exact(value, {'uid', 'gid', 'mode'})
    if (any(type(item) is not int or item < 0 for item in value.values())
            or value['uid'] != 0 or value['mode'] > 0o777 or value['mode'] & 0o022
            or directory and not value['mode'] & 0o111
            or site and (value['gid'] != 0 or value['mode'] != 0o644)):
        raise ProtocolError('invalid Caddy metadata')


def validate_caddy_authority(value, *, bootstrap_masks=()):
    """Validate the selected ownership snapshot without inspecting operator bytes."""
    if not isinstance(value, Mapping):
        raise ProtocolError('invalid Caddy authority')
    branch = value.get('branch')
    if branch not in {'bootstrap', 'preserve-configuration', 'existing'}:
        raise ProtocolError('invalid Caddy branch')
    value = _exact(value, _AUTHORITY_KEYS | ({'main_sha256'} if branch == 'preserve-configuration' else set()))
    for key in ('package_installed', 'executable_trusted', 'unit_trusted', 'service_active',
                'service_masked', 'api_service_active', 'api_service_masked', 'glob_import_present'):
        if type(value[key]) is not bool:
            raise ProtocolError('invalid Caddy boolean')
    installed = value['package_installed']
    if installed != (branch == 'existing') or installed != value['executable_trusted'] or installed != value['unit_trusted']:
        raise ProtocolError('inconsistent Caddy installation authority')
    if installed != _version(value['package_version']):
        raise ProtocolError('invalid Caddy package version')
    if not installed and value['package_version'] is not None:
        raise ProtocolError('invalid absent Caddy version')
    for key in ('executable_sha256', 'unit_sha256', 'api_unit_sha256'):
        if installed and not _digest(value[key]) or not installed and value[key] is not None:
            raise ProtocolError('invalid Caddy trust digest')
    for key, service in (('unit_path', 'caddy.service'), ('api_unit_path', 'caddy-api.service')):
        if installed and value[key] not in {'/lib/systemd/system/' + service, '/usr/lib/systemd/system/' + service} or not installed and value[key] is not None:
            raise ProtocolError('invalid Caddy unit path')
    for key in ('service_enabled', 'api_service_enabled'):
        if type(value[key]) is not str or value[key] not in {'enabled', 'disabled', 'masked', 'masked-runtime', 'static', 'absent'}:
            raise ProtocolError('invalid Caddy enablement')
    if value['api_service_active'] or value['api_service_enabled'] == 'enabled' or type(value['main_pid']) is not int or value['main_pid'] < 0:
        raise ProtocolError('unsupported Caddy service authority')
    if value['service_active'] != (value['main_pid'] > 0) or not installed and value['service_active']:
        raise ProtocolError('invalid Caddy process authority')
    for key in ('configuration_directory', 'site_directory'):
        _metadata(value[key], directory=True)
    _metadata(value['main'])
    _metadata(value['site'], site=True)
    if value['site'] is None and value['site_sha256'] is not None or value['site'] is not None and not _digest(value['site_sha256']):
        raise ProtocolError('invalid Caddy site digest')
    if value['main'] is None and value['glob_import_present']:
        raise ProtocolError('absent Caddy main has import')
    if value['configuration_directory'] is None and any(value[key] is not None for key in ('main', 'site_directory', 'site')):
        raise ProtocolError('invalid Caddy parent authority')
    if value['site'] is not None and value['site_directory'] is None:
        raise ProtocolError('invalid Caddy site parent')
    if branch == 'preserve-configuration':
        if value['configuration_directory'] is None or (value['main'] is None and value['main_sha256'] is not None) or (value['main'] is not None and not _digest(value['main_sha256'])):
            raise ProtocolError('invalid preserved Caddy main')
    if branch == 'bootstrap' and (value['configuration_directory'] is not None
            or value['service_masked'] != ('caddy.service' in bootstrap_masks)
            or value['api_service_masked'] != ('caddy-api.service' in bootstrap_masks)):
        raise ProtocolError('invalid Caddy bootstrap authority')
    listeners = value['public_listeners']
    if not isinstance(listeners, (list, tuple)) or len(listeners) > 64:
        raise ProtocolError('invalid Caddy listeners')
    identities = []
    for listener in listeners:
        listener = _exact(listener, {'address', 'port', 'pid'})
        address = listener['address']
        try:
            if type(address) is not str or address != '*' and str(ipaddress.ip_address(address)) != address:
                raise ValueError
        except ValueError as error:
            raise ProtocolError('invalid Caddy listener address') from error
        if (type(listener['port']) is not int or listener['port'] not in {80, 443}
                or type(listener['pid']) is not int or listener['pid'] <= 0
                or listener['pid'] != value['main_pid'] or not installed or not value['service_active']):
            raise ProtocolError('invalid Caddy listener owner')
        identities.append((listener['port'], address, listener['pid']))
    if value['service_active'] and {identity[0] for identity in identities} != {80, 443}:
        raise ProtocolError('unsupported active Caddy listeners')
    if identities != sorted(set(identities)):
        raise ProtocolError('invalid Caddy listener order')
    return value


def _receipt(value, parameters):
    value = _exact(value, {'site_path', 'site_sha256', 'site_changed', 'recovery_id',
        'original_absent', 'original_sha256', 'original_metadata', 'created_masks', 'installed_package_version'})
    if value['site_path'] != SITE_PATH or value['site_sha256'] != parameters['site_sha256']:
        raise ProtocolError('invalid Caddy receipt identity')
    for key in ('site_changed', 'original_absent'):
        if type(value[key]) is not bool:
            raise ProtocolError('invalid Caddy receipt flag')
    recovery_id = value['recovery_id']
    if recovery_id is not None and (type(recovery_id) is not str or re.fullmatch(r'[0-9a-f]{32}', recovery_id) is None):
        raise ProtocolError('invalid Caddy recovery identifier')
    if value['original_absent']:
        if value['original_sha256'] is not None or value['original_metadata'] is not None:
            raise ProtocolError('invalid original Caddy absence')
    elif not _digest(value['original_sha256']) or value['original_metadata'] is None:
        raise ProtocolError('invalid original Caddy site')
    _metadata(value['original_metadata'], site=True)
    if value['site_changed'] and recovery_id is None:
        raise ProtocolError('missing changed Caddy site recovery authority')
    masks = value['created_masks']
    if not isinstance(masks, (list, tuple)) or tuple(masks) not in {(), ('caddy.service',), ('caddy-api.service',), ('caddy.service', 'caddy-api.service')}:
        raise ProtocolError('invalid Caddy mask receipt')
    if value['installed_package_version'] is not None and not _version(value['installed_package_version']):
        raise ProtocolError('invalid Caddy installed version')
    return value


def _receipt_baseline(receipt, authority):
    if (receipt['original_absent'] != (authority['site'] is None)
            or receipt['original_sha256'] != authority['site_sha256']
            or receipt['original_metadata'] != authority['site']):
        raise ProtocolError('Caddy receipt original site mismatch')
    for name in receipt['created_masks']:
        prefix = 'api_service' if name == 'caddy-api.service' else 'service'
        if authority[prefix + '_masked'] and authority[prefix + '_enabled'] != 'masked-runtime':
            raise ProtocolError('Caddy receipt adopts original persistent mask')


def _guard_receipt(receipt):
    if receipt['site_changed'] or receipt['recovery_id'] is not None or receipt['installed_package_version'] is not None:
        raise ProtocolError('invalid Caddy guard receipt')


def validate_caddy_request(request: HostRequest) -> HostRequest:
    if request.operation != 'caddy' or request.paths:
        raise ProtocolError('invalid Caddy request')
    p = _exact(request.parameters, {'mode', 'hostname', 'application_port', 'site_content',
                                    'site_sha256', 'receipt', 'public_probe'})
    hostname, port = p['hostname'], p['application_port']
    reserved = ('localhost', 'test', 'invalid', 'example', 'example.com', 'example.net',
                'example.org', 'local', 'home.arpa', 'onion')
    if (type(hostname) is not str or len(hostname) > 253
            or re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', hostname) is None
            or not re.search(r'[a-z]', hostname.rsplit('.', 1)[-1])
            or any(hostname == suffix or hostname.endswith('.' + suffix) for suffix in reserved)
            or type(port) is not int or not 1 <= port <= 65535):
        raise ProtocolError('invalid Caddy hostname or port')
    content = f'{hostname} {{\n\treverse_proxy 127.0.0.1:{port}\n}}\n'
    if p['site_content'] != content or p['site_sha256'] != hashlib.sha256(content.encode()).hexdigest():
        raise ProtocolError('invalid deterministic Caddy site')
    mode = p['mode']
    if type(mode) is not str or mode not in MODES:
        raise ProtocolError('invalid Caddy mode')
    if mode == 'inspect':
        if request.expected_state:
            raise ProtocolError('Caddy inspection has expected authority')
    else:
        validate_caddy_authority(request.expected_state)
    if mode in {'inspect', 'guard-install'}:
        if p['receipt'] is not None:
            raise ProtocolError('unexpected Caddy receipt')
    elif p['receipt'] is not None:
        receipt = _receipt(p['receipt'], p)
        if mode == 'prepare':
            _guard_receipt(receipt)
            _receipt_baseline(receipt, request.expected_state)
            required_masks = tuple(name for name, prefix in (('caddy.service', 'service'), ('caddy-api.service', 'api_service'))
                if not request.expected_state[prefix + '_masked'] or request.expected_state[prefix + '_enabled'] == 'masked-runtime')
            if tuple(receipt['created_masks']) != required_masks:
                raise ProtocolError('incomplete persistent Caddy installation guard')
            if request.expected_state['branch'] == 'existing':
                raise ProtocolError('unexpected existing Caddy guard receipt')
    elif mode == 'activate' or request.expected_state['branch'] != 'existing':
        raise ProtocolError('missing Caddy receipt')
    if mode == 'activate':
        if p['public_probe'] not in {'ready', 'not-ready', 'not-attempted'}:
            raise ProtocolError('invalid Caddy public probe')
    elif p['public_probe'] is not None:
        raise ProtocolError('unexpected Caddy public probe')
    return request


def validate_caddy_result(request: HostRequest, result: HostResult) -> HostResult:
    validate_result_for_request(request, result)
    if request.operation != 'caddy':
        raise ProtocolError('invalid Caddy result')
    s = _exact(result.state, _STATE_KEYS)
    if type(s['exit_code']) is not int or s['exit_code'] not in {0, 2, 5, 8, 10} or (s['exit_code'] == 0) != (result.outcome == 'succeeded'):
        raise ProtocolError('invalid Caddy outcome')
    if s['failed_boundary'] is not None and s['failed_boundary'] not in _BOUNDARIES or (s['failed_boundary'] is None) != (s['exit_code'] == 0):
        raise ProtocolError('invalid Caddy boundary')
    if s['mutation_state'] not in {'unchanged', 'changed', 'unknown'} or s['reason'] not in _REASONS:
        raise ProtocolError('invalid Caddy evidence')
    if not isinstance(s['unavailable_fields'], (list, tuple)) or tuple(s['unavailable_fields']) != (() if s['authority'] is not None else ('authority',)):
        raise ProtocolError('invalid unavailable Caddy authority')
    bootstrap_masks = ()
    if request.parameters.get('mode') in {'guard-install', 'prepare'} and s['receipt'] is not None:
        receipt = _receipt(s['receipt'], request.parameters)
        if request.expected_state.get('branch') == 'bootstrap':
            bootstrap_masks = receipt['created_masks']
    if s['authority'] is not None:
        validate_caddy_authority(s['authority'], bootstrap_masks=bootstrap_masks)
    for key in ('validation', 'reload'):
        if s[key] not in {'not-attempted', 'succeeded', 'failed', 'unknown'}:
            raise ProtocolError('invalid Caddy command outcome')
    recovery = _exact(s['recovery'], {'status', 'runtime_uncertain'})
    if recovery['status'] not in {'not-needed', 'restored', 'failed', 'unknown'} or type(recovery['runtime_uncertain']) is not bool:
        raise ProtocolError('invalid Caddy recovery')
    mode = request.parameters.get('mode')
    if s['receipt'] is not None:
        receipt = _receipt(s['receipt'], request.parameters)
        if mode == 'activate' and receipt != request.parameters['receipt']:
            raise ProtocolError('Caddy activation receipt changed')
        if mode in {'guard-install', 'prepare'}:
            _receipt_baseline(receipt, request.expected_state)
        if mode == 'guard-install':
            _guard_receipt(receipt)
            if receipt['created_masks'] and s['mutation_state'] != 'changed':
                raise ProtocolError('invalid Caddy guard mutation evidence')
    if s['reload'] == 'succeeded' and s['mutation_state'] != 'changed':
        raise ProtocolError('missing confirmed Caddy reload mutation')
    if mode in {'inspect', 'guard-install'} and (s['receipt'] is not None and mode == 'inspect' or s['validation'] != 'not-attempted' or s['reload'] != 'not-attempted'):
        raise ProtocolError('unexpected Caddy inspection action')
    if mode == 'inspect' and (s['mutation_state'] != 'unchanged' or recovery != {'status': 'not-needed', 'runtime_uncertain': False}):
        raise ProtocolError('mutating Caddy inspection evidence')
    if result.outcome == 'succeeded':
        validate_caddy_request(request)
        if s['authority'] is None or s['reason'] != 'compatible':
            raise ProtocolError('missing Caddy success authority')
        if mode != 'inspect' and s['receipt'] is None:
            raise ProtocolError('missing Caddy success receipt')
        if mode == 'guard-install':
            if not s['authority']['service_masked'] or not s['authority']['api_service_masked']:
                raise ProtocolError('missing successful Caddy installation masks')
        if mode in {'prepare', 'activate'}:
            if (s['authority']['site_sha256'] != request.parameters['site_sha256']
                    or s['authority']['site'] != {'uid': 0, 'gid': 0, 'mode': 0o644}
                    or s['authority']['glob_import_present'] and s['validation'] != 'succeeded'):
                raise ProtocolError('missing Caddy prepared site proof')
        if mode == 'prepare' and s['reload'] != 'not-attempted':
            raise ProtocolError('unexpected Caddy preparation reload')
        if mode == 'prepare':
            changed_site = request.expected_state['site_sha256'] != request.parameters['site_sha256']
            if receipt['site_changed'] != changed_site or changed_site and s['mutation_state'] != 'changed':
                raise ProtocolError('invalid successful Caddy site mutation evidence')
            installed_version = receipt['installed_package_version']
            if request.expected_state['branch'] == 'existing':
                if installed_version is not None or receipt['created_masks']:
                    raise ProtocolError('unexpected existing Caddy installation evidence')
            elif installed_version != s['authority']['package_version'] or s['mutation_state'] != 'changed':
                raise ProtocolError('missing installed Caddy package evidence')
        if s['validation'] in {'failed', 'unknown'} or s['reload'] in {'failed', 'unknown'} or recovery != {'status': 'not-needed', 'runtime_uncertain': False}:
            raise ProtocolError('invalid successful Caddy command evidence')


    return result


__all__ = ['validate_caddy_request', 'validate_caddy_result', 'validate_caddy_authority']
