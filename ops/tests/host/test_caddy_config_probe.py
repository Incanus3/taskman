"""Provisioning admission consumes the bounded native proxy authority."""
from __future__ import annotations

import pytest

from tests.support.caddy import mutation_host, installation_host, request
from tests.support.environments import environment_config
from tests.support.remotes import ScriptedRemote
from tests.host.test_facts import fact_responses, direct_dns
from taskman_ops.host.acceptance import validate_provisionable_host, ProvisioningState


@pytest.mark.parametrize('active,imported', [(True, False), (False, True)])
def test_shared_main_metadata_and_import_predicate_do_not_block_application_admission(tmp_path, monkeypatch, active, imported):
    module, root, _ = mutation_host(tmp_path, monkeypatch, active=active, imported=imported)
    main = root / 'etc/caddy/Caddyfile'
    main.chmod(0o640)
    before = main.read_bytes(), main.stat().st_mtime_ns
    authority = module.caddy(request())
    assert authority.outcome == 'succeeded'
    responses = fact_responses()
    if active:
        responses[8] = responses[-1] = module_listener_result(module)
    remote = ScriptedRemote.from_responses(responses)
    result = validate_provisionable_host(remote, environment_config(application_port=4011),
        resolver=direct_dns, caddy_authority=authority)
    assert result.state is ProvisioningState.PARTIAL
    assert result.caddy_state.value == ('active' if active else 'staged')
    assert before == (main.read_bytes(), main.stat().st_mtime_ns)
    assert not remote.responses


def module_listener_result(module):
    from taskman_ops.remote import CommandResult
    status, output, _ = module._command(('ss', '-H', '-ltnp'))
    return CommandResult(status, output.decode())


@pytest.mark.parametrize('main', [True, False])
def test_configuration_without_package_is_supported_pending_ownership(tmp_path, monkeypatch, main):
    module, _, _, _ = installation_host(tmp_path, monkeypatch, main=main)
    authority = module.caddy(request())
    result = validate_provisionable_host(ScriptedRemote.from_responses(fact_responses()),
        environment_config(application_port=4011), resolver=direct_dns, caddy_authority=authority)
    assert result.state is ProvisioningState.PARTIAL
    assert result.caddy_state.value == 'prepared'


def test_supplied_proxy_inspection_rejects_whole_state_mismatch():
    from dataclasses import replace
    from tests.support.caddy import authority_result
    from taskman_ops.errors import OpsError, ExitStatus
    authority = authority_result()
    malformed = replace(authority, state={**authority.state, "unexpected": True})
    with pytest.raises(OpsError) as raised:
        validate_provisionable_host(ScriptedRemote.from_responses(fact_responses()),
            environment_config(), resolver=direct_dns, caddy_authority=malformed)
    assert raised.value.status == ExitStatus.REMOTE_PREFLIGHT
