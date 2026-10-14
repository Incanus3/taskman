from __future__ import annotations

import os
from pathlib import Path

from pyinfra.api import Config, Inventory, State, deploy

from tests.support.environments import environment_config
from taskman_ops.remote import ChangeSet, PyinfraRemote
import taskman_ops.services.caddy as caddy
from taskman_ops.services.caddy import render_caddyfile


def test_site_renderer_keeps_loopback_only_upstream():
    assert render_caddyfile(environment_config(application_port=4011)) == (
        "taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4011\n}\n"
    )


def test_ownership_plan_freezes_site_branch_and_manual_activation():
    import hashlib
    import json
    from tests.support.caddy import authority_result
    result = authority_result(branch='existing', imported=True)
    plan = caddy.build_caddy_ownership_plan(environment_config(), result)
    assert plan.branch == 'existing'
    assert plan.site_content == 'taskman.acme.tld {\n\treverse_proxy 127.0.0.1:4000\n}\n'
    assert plan.site_sha256 == hashlib.sha256(plan.site_content.encode()).hexdigest()
    assert plan.main_content is None
    assert json.loads(plan.authority_json)['branch'] == 'existing'
    assert 'manual' in caddy.caddy_ownership_preview(plan)['activation']


def test_plan_confirms_narrow_authority_and_preserved_main_bytes(tmp_path, monkeypatch):
    from tests.support.caddy import host, request
    from taskman_ops.errors import OpsError
    import pytest
    import hashlib
    config = environment_config(application_port=4011)
    module, root, _ = host(tmp_path, monkeypatch)
    first = module.caddy(request())
    plan = caddy.build_caddy_ownership_plan(config, first)
    main = root / 'etc/caddy/Caddyfile'
    main.write_text('import sites-enabled/*.caddy\n# operator changes another site\n')
    assert caddy.validate_caddy_plan_authority(plan, module.caddy(request())).outcome == 'succeeded'
    main.write_text('# missing import\n')
    with pytest.raises(OpsError) as error:
        caddy.validate_caddy_plan_authority(plan, module.caddy(request()))
    assert error.value.status == 10


def test_preserved_main_and_site_change_require_new_preview(tmp_path, monkeypatch):
    from tests.support.caddy import host, request
    from taskman_ops.errors import OpsError
    import pytest
    module, root, _ = host(tmp_path, monkeypatch, installed=False, active=False)
    config = environment_config(application_port=4011)
    plan = caddy.build_caddy_ownership_plan(config, module.caddy(request()))
    main = root / 'etc/caddy/Caddyfile'
    main.write_text('import sites-enabled/*.caddy\n# same predicate different bytes\n')
    with pytest.raises(OpsError) as error:
        caddy.validate_caddy_plan_authority(plan, module.caddy(request()))
    assert error.value.status == 10


def test_bootstrap_main_template_is_separate():
    assert (Path(__file__).resolve().parents[2] / 'caddy/Caddyfile.main').read_bytes() == (
        b'import /etc/caddy/sites-enabled/*.caddy\n'
    )


def test_inspection_uses_allowlisted_helper_and_rejects_malformed_reply(tmp_path, monkeypatch):
    from tests.support.caddy import host
    from taskman_ops.host_protocol import HostResult
    from taskman_ops.errors import OpsError
    import pytest
    module, _, _ = host(tmp_path, monkeypatch)
    remote = object()
    def invoke(actual_remote, package, request):
        assert actual_remote is remote
        assert package.path.is_file()
        assert request.paths == {}
        assert request.expected_state == {}
        return module.caddy(request)
    monkeypatch.setattr(caddy, 'invoke_helper', invoke)
    assert caddy.inspect_caddy(remote, environment_config()).state['authority']['branch'] == 'existing'
    monkeypatch.setattr(caddy, 'invoke_helper', lambda _, package, request:
        HostResult.for_request(request, 'succeeded', 'missing evidence', {}))
    with pytest.raises(OpsError) as error:
        caddy.inspect_caddy(remote, environment_config())
    assert error.value.status == 5


def test_confirmed_ownership_refuses_site_service_socket_trust_and_metadata_drift():
    from tests.support.caddy import authority_result
    from taskman_ops.host_protocol import HostResult
    from taskman_ops.errors import OpsError
    import pytest
    initial = authority_result(branch='existing', active=True, imported=True)
    plan = caddy.build_caddy_ownership_plan(environment_config(application_port=4011), initial)
    for changes in (
        {'executable_sha256': '0' * 64}, {'unit_sha256': '0' * 64},
        {'service_enabled': 'disabled'}, {'service_masked': True},
        {'main': {'uid': 0, 'gid': 998, 'mode': 0o644}},
        {'site_directory': {'uid': 0, 'gid': 0, 'mode': 0o755}},
        {'site_directory': {'uid': 0, 'gid': 0, 'mode': 0o755},
         'site': {'uid': 0, 'gid': 0, 'mode': 0o644}, 'site_sha256': '0' * 64},
        {'public_listeners': [{'address': '127.0.0.1', 'port': 80, 'pid': 123},
                              {'address': '::', 'port': 443, 'pid': 123}]},
        {'service_active': False, 'main_pid': 0, 'public_listeners': []},
    ):
        mapping = initial.to_mapping()
        mapping['state']['authority'].update(changes)
        with pytest.raises(OpsError) as error:
            caddy.validate_caddy_plan_authority(plan, HostResult.from_mapping(mapping))
        assert error.value.status == 10


def test_preserved_main_absence_and_metadata_are_confirmation_prerequisites(tmp_path, monkeypatch):
    from tests.support.caddy import host, request
    from taskman_ops.errors import OpsError
    import pytest
    module, root, _ = host(tmp_path, monkeypatch, installed=False, active=False, main=False)
    config = environment_config(application_port=4011)
    plan = caddy.build_caddy_ownership_plan(config, module.caddy(request()))
    (root / 'etc/caddy/Caddyfile').write_text('# package or operator created main\n')
    with pytest.raises(OpsError) as error:
        caddy.validate_caddy_plan_authority(plan, module.caddy(request()))
    assert error.value.status == 10


def test_ownership_sources_existing_plan_queues_no_global_actions():
    from tests.support.caddy import authority_result
    plan=caddy.build_caddy_ownership_plan(environment_config(application_port=4011),
        authority_result(branch='existing', active=True, imported=True))
    inventory=Inventory((["@local"],{}));state=State(inventory,Config(PARALLEL=1),check_for_changes=False)
    local=inventory.get_host('@local');state.activate_host(local)
    remote=PyinfraRemote(local,environment_config(application_port=4011),inventory=inventory,state=state)
    @deploy('Caddy source convergence')
    def sources():
        caddy.declare_caddy_sources(plan)
    changes=remote.run_deploy(sources)
    assert changes==ChangeSet(changed=False)
    assert state.get_op_order()==[]


def test_ownership_invocation_preserves_prepared_receipt_for_deferred_activation(tmp_path,monkeypatch):
    from tests.support.caddy import mutation_host
    from tests.support.caddy import request
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    plan=caddy.build_caddy_ownership_plan(environment_config(application_port=4011),module.caddy(request()))
    def invoke(remote,package,request):
        assert package.path.is_file()
        from taskman_ops.host_helper.__main__ import _dispatch
        return _dispatch(request)
    monkeypatch.setattr(caddy,'invoke_helper',invoke)
    prepared=caddy.prepare_caddy(object(),plan)
    assert prepared.state['receipt']['site_changed'] is True
    activated=caddy.activate_caddy(object(),plan,prepared,public_probe='not-ready')
    assert activated.state['reload']=='succeeded'
    assert activated.state['mutation_state']=='changed'
    assert [argv for argv,_ in calls if argv[0]=='systemctl']==[('systemctl','reload','caddy.service')]


def test_ownership_transport_loss_is_unknown_and_keeps_prior_receipt(tmp_path,monkeypatch):
    import pytest
    from tests.support.caddy import mutation_host
    from tests.support.caddy import request
    from taskman_ops.errors import OpsError
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    plan=caddy.build_caddy_ownership_plan(environment_config(application_port=4011),module.caddy(request()))
    monkeypatch.setattr(caddy,'invoke_helper',lambda remote,package,req: module.caddy(req))
    prepared=caddy.prepare_caddy(object(),plan)
    def lost(*args,**kwargs):raise RuntimeError('private transport')
    monkeypatch.setattr(caddy,'invoke_helper',lost)
    with pytest.raises(OpsError) as error:
        caddy.activate_caddy(object(),plan,prepared,public_probe='not-ready')
    assert error.value.changed
    assert error.value.state['mutation_state']=='unknown'
    assert error.value.state['caddy_receipt']==prepared.to_mapping()['state']['receipt']
    assert error.value.state['reload']=='unknown'
    assert error.value.state['recovery']['runtime_uncertain'] is True
    assert 'inspect' in str(error.value).lower()
    assert 'private' not in str(error.value)


def test_activation_loss_before_dispatch_retains_receipt_without_runtime_claim(tmp_path,monkeypatch):
    import pytest
    from tests.support.caddy import mutation_host,request
    from taskman_ops.errors import OpsError
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    plan=caddy.build_caddy_ownership_plan(environment_config(application_port=4011),module.caddy(request()))
    monkeypatch.setattr(caddy,'invoke_helper',lambda remote,package,req: module.caddy(req))
    prepared=caddy.prepare_caddy(object(),plan)
    def before_dispatch(*args,**kwargs):
        error=RuntimeError('private pre-entry transport');error.helper_entry_dispatched=False;raise error
    monkeypatch.setattr(caddy,'invoke_helper',before_dispatch)
    with pytest.raises(OpsError) as error:caddy.activate_caddy(object(),plan,prepared,public_probe='not-ready')
    assert error.value.changed is False
    assert error.value.state['mutation_state']=='unchanged'
    assert error.value.state['reload']=='not-attempted'
    assert error.value.state['recovery']['runtime_uncertain'] is False
    assert error.value.state['caddy_receipt']==prepared.to_mapping()['state']['receipt']
    assert all(argv[:2]!=('systemctl','reload') for argv,_ in calls)


def test_bootstrap_preparation_lost_reply_retains_possible_start_and_guard(tmp_path,monkeypatch):
    import pytest
    from tests.support.caddy import installation_host,request
    from taskman_ops.errors import OpsError
    module,root,state,calls=installation_host(tmp_path,monkeypatch,preserved=False,main=False)
    plan=caddy.build_caddy_ownership_plan(environment_config(application_port=4011),module.caddy(request()))
    monkeypatch.setattr(caddy,'invoke_helper',lambda remote,package,req: module.caddy(req))
    guard=caddy.guard_caddy_install(object(),plan)
    def lost(remote,package,req):
        assert module.caddy(req).outcome=='succeeded'
        raise RuntimeError('private reply loss after bootstrap start')
    monkeypatch.setattr(caddy,'invoke_helper',lost)
    with pytest.raises(OpsError) as error:caddy.prepare_caddy(object(),plan,guard)
    assert state['active'] is True
    assert error.value.state['mutation_state']=='unknown'
    assert error.value.state['activation']=='unknown'
    assert error.value.state['recovery']['runtime_uncertain'] is True
    assert error.value.state['caddy_receipt']==guard.to_mapping()['state']['receipt']
    assert error.value.state['caddy_authority'] is None
    assert 'reload' not in error.value.state
    assert 'inspect' in str(error.value).lower()


def test_existing_preparation_transport_loss_does_not_claim_runtime_activation(tmp_path,monkeypatch):
    import pytest
    from tests.support.caddy import mutation_host,request
    from taskman_ops.errors import OpsError
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    plan=caddy.build_caddy_ownership_plan(environment_config(application_port=4011),module.caddy(request()))
    def lost(*args):raise RuntimeError('private transport')
    monkeypatch.setattr(caddy,'invoke_helper',lost)
    with pytest.raises(OpsError) as error:caddy.prepare_caddy(object(),plan)
    assert error.value.state['mutation_state']=='unknown'
    assert 'reload' not in error.value.state and 'activation' not in error.value.state


def test_ownership_package_failure_before_dispatch_proves_unchanged(tmp_path,monkeypatch):
    import pytest
    from contextlib import contextmanager
    from tests.support.caddy import host,request
    from taskman_ops.errors import OpsError
    module,root,command=host(tmp_path,monkeypatch)
    plan=caddy.build_caddy_ownership_plan(environment_config(application_port=4011),module.caddy(request()))
    @contextmanager
    def failed_package():
        raise RuntimeError('local archive failure')
        yield
    monkeypatch.setattr(caddy,'temporary_helper_package',failed_package)
    with pytest.raises(OpsError) as error:caddy.prepare_caddy(object(),plan)
    assert error.value.changed is False
    assert error.value.state['mutation_state']=='unchanged'
