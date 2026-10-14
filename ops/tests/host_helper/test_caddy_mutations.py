"""Real proxy filesystem transitions, with native commands isolated at their boundary."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path

import pytest
from tests.support.caddy import host, request, mutation_host, installation_host
from taskman_ops.host_helper.__main__ import _dispatch



def test_prepare_existing_site_defers_reload_and_preserves_main_and_siblings(tmp_path, monkeypatch):
    module, root, calls = mutation_host(tmp_path, monkeypatch)
    main = root / 'etc/caddy/Caddyfile'
    before = (main.read_bytes(), main.stat().st_mtime_ns)
    authority = module.caddy(request()).state['authority']
    result = _dispatch(request('prepare', authority))
    assert result.outcome == 'succeeded', result.to_mapping()
    assert result.state['receipt']['site_changed'] is True
    assert result.state['mutation_state'] == 'changed'
    assert result.state['validation'] == 'succeeded'
    assert result.state['reload'] == 'not-attempted'
    assert (root / 'etc/caddy/sites-enabled/taskman.caddy').read_bytes() == request().parameters['site_content'].encode()
    assert (main.read_bytes(), main.stat().st_mtime_ns) == before
    assert (root / 'etc/caddy/sites-enabled').stat().st_mode & 0o777 == 0o750
    assert (root / 'etc/caddy/sites-enabled/other.caddy').read_bytes() == b'operator sibling\n'
    assert len(calls) == 2
    assert all(argv[:4] == ('sudo', '-n', '-u', 'caddy') for argv, _ in calls)


@pytest.mark.parametrize('original', [None, b'old site\n'])
def test_invalid_candidate_restores_only_site_and_keeps_changed_evidence(tmp_path, monkeypatch, original):
    module, root, calls = mutation_host(tmp_path, monkeypatch, original=original)
    authority = module.caddy(request()).state['authority']
    def native(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 2:
            raise RuntimeError('private native error')
        return b''
    monkeypatch.setattr(module, '_native', native)
    result = _dispatch(request('prepare', authority))
    assert result.state['exit_code'] == 8
    assert result.state['failed_boundary'] == 'validation'
    assert result.state['mutation_state'] == 'changed'
    assert result.state['recovery']['status'] == 'restored'
    path = root / 'etc/caddy/sites-enabled/taskman.caddy'
    assert path.read_bytes() == original if original is not None else not path.exists()
    assert (root / 'etc/caddy/sites-enabled/other.caddy').read_bytes() == b'operator sibling\n'
    evidence = root / 'etc/taskman/caddy-recovery' / result.state['receipt']['recovery_id']
    assert evidence.is_dir()
    assert evidence.stat().st_mode & 0o777 == 0o700
    assert 'private' not in str(result.to_mapping())


def test_invalid_original_refuses_before_site_publication(tmp_path, monkeypatch):
    module, root, _ = mutation_host(tmp_path, monkeypatch)
    authority = module.caddy(request()).state['authority']
    monkeypatch.setattr(module, '_native', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('private')))
    result = _dispatch(request('prepare', authority))
    assert result.state['exit_code'] == 8
    assert result.state['mutation_state'] == 'unchanged'
    assert not (root / 'etc/caddy/sites-enabled/taskman.caddy').exists()


def test_no_import_site_publication_never_activates_existing_inactive_caddy(tmp_path, monkeypatch):
    module, root, calls = mutation_host(tmp_path, monkeypatch, active=False, imported=False)
    authority = module.caddy(request()).state['authority']
    prepared = _dispatch(request('prepare', authority))
    assert prepared.outcome == 'succeeded', prepared.to_mapping()
    activated = _dispatch(request('activate', prepared.state['authority'], receipt=prepared.state['receipt'], public_probe='not-attempted'))
    assert activated.outcome == 'succeeded'
    assert activated.state['reload'] == 'not-attempted'
    assert calls == []


def test_converged_site_keeps_mtime_and_ready_activation_is_validated_noop(tmp_path, monkeypatch):
    original = request().parameters['site_content'].encode()
    module, root, calls = mutation_host(tmp_path, monkeypatch, original=original)
    authority = module.caddy(request()).state['authority']
    path = root / 'etc/caddy/sites-enabled/taskman.caddy'
    mtime = path.stat().st_mtime_ns
    prepared = _dispatch(request('prepare', authority))
    assert prepared.outcome == 'succeeded', prepared.to_mapping()
    assert prepared.state['mutation_state'] == 'unchanged'
    activated = _dispatch(request('activate', prepared.state['authority'], receipt=prepared.state['receipt'], public_probe='ready'))
    assert activated.outcome == 'succeeded'
    assert activated.state['reload'] == 'not-attempted'
    assert activated.state['validation'] == 'succeeded'
    assert path.stat().st_mtime_ns == mtime
    assert len(calls) == 2



@pytest.mark.parametrize(('preserved','masked','main'), [(True,False,True),(True,True,False),(False,False,False)])
def test_guard_masks_before_authenticated_install_and_preserves_configuration(tmp_path, monkeypatch, preserved, masked, main):
    module,root,state,calls=installation_host(tmp_path,monkeypatch,preserved=preserved,masked=masked,main=main)
    before = module.caddy(request()).state['authority']
    original_main=(root/'etc/caddy/Caddyfile').read_bytes() if main else None
    guarded=_dispatch(request('guard-install',before))
    assert guarded.outcome=='succeeded',guarded.to_mapping()
    assert module.caddy(request()).outcome!='succeeded' if not preserved else True
    assert list(guarded.state['receipt']['created_masks']) == (['caddy-api.service'] if masked else ['caddy.service','caddy-api.service'])
    prepared=_dispatch(request('prepare',before,receipt=guarded.state['receipt']))
    assert prepared.outcome=='succeeded',dict(prepared.state)
    assert prepared.state['receipt']['installed_package_version']=='2.11.7'
    assert prepared.state['authority']['service_active'] is (not preserved)
    assert prepared.state['authority']['service_masked'] is preserved
    if main:assert (root/'etc/caddy/Caddyfile').read_bytes()==original_main
    if not preserved:assert (root/'etc/caddy/Caddyfile').read_bytes()==b'import /etc/caddy/sites-enabled/*.caddy\n'
    install=next(i for i,(argv,_) in enumerate(calls) if argv[:2]==('apt-get','install'))
    assert all(argv[:2]==('systemctl','mask') for argv,_ in calls[:len(guarded.state['receipt']['created_masks'])])
    assert any(argv[:2]==('dpkg-deb','--show') for argv,_ in calls[:install])
    assert calls[install][0][-1]=='caddy=2.11.7'
    assert '-o' in calls[install][0] and 'Dpkg::Options::=--force-confold' in calls[install][0]


@pytest.mark.parametrize('failure',['package','preservation'])
def test_install_failure_retains_masks_protected_main_and_no_site(tmp_path,monkeypatch,failure):
    module,root,state,calls=installation_host(tmp_path,monkeypatch)
    authority=module.caddy(request()).state['authority']
    guarded=_dispatch(request('guard-install',authority))
    assert guarded.outcome=='succeeded',guarded.to_mapping()
    state['failure']=failure
    prepared=_dispatch(request('prepare',authority,receipt=guarded.state['receipt']))
    assert prepared.state['exit_code']==8,prepared.to_mapping()
    assert not (root/'etc/caddy/sites-enabled/taskman.caddy').exists()
    assert (root/'etc/systemd/system/caddy.service').is_symlink()
    assert (root/'etc/systemd/system/caddy-api.service').is_symlink()
    if state['installed']:
        assert prepared.state['mutation_state'] in {'changed','unknown'}
        protected=list((root/'etc/taskman/caddy-recovery').glob('*/main'))
        assert len(protected)==1
        assert protected[0].read_bytes()==b'import sites-enabled/*.caddy\n'


@pytest.mark.parametrize('failure', ['reload-rejection', 'lost-reply', 'restoration', 'restored-validation', 'inactive'])
def test_activation_failure_keeps_primary_error_and_runtime_uncertainty(tmp_path,monkeypatch,failure):
    module,root,calls=mutation_host(tmp_path,monkeypatch,original=b'old site\n')
    authority=module.caddy(request()).state['authority']
    prepared=_dispatch(request('prepare',authority))
    assert prepared.outcome=='succeeded'
    from taskman_ops.host_helper.commands import CommandTimeout,CommandError
    from taskman_ops.host_helper import caddy_files
    def native(argv,**kwargs):
        calls.append((argv,kwargs))
        if argv[:2]==('systemctl','reload'):
            if failure=='restoration':
                (root/'etc/caddy/sites-enabled/taskman.caddy').write_bytes(b'concurrent site')
            raise CommandTimeout('private transport') if failure=='lost-reply' else CommandError('native rejection')
        if failure=='restored-validation' and (root/'etc/caddy/sites-enabled/taskman.caddy').read_bytes()==b'old site\n':
            raise CommandError('private restored validation')
        return b''
    monkeypatch.setattr(module,'_native',native)
    if failure=='inactive':
        from taskman_ops.host_helper.caddy_execution import CaddyExecution
        original=CaddyExecution.observe
        def observe(self):
            a=original(self)
            if self.reload!='not-attempted':
                a={**a,'service_active':False,'main_pid':0,'public_listeners':[]}
            return a
        monkeypatch.setattr(CaddyExecution,'observe',observe)
    result=_dispatch(request('activate',prepared.state['authority'],receipt=prepared.state['receipt'],public_probe='not-ready'))
    assert result.state['exit_code']==8
    assert result.state['failed_boundary']=='activation'
    assert result.state['reason']=='activation-failed'
    assert result.state['mutation_state'] in {'unknown','changed'}
    assert result.state['recovery']['runtime_uncertain'] is True
    assert result.state['recovery']['status']==('failed' if failure in {'restoration','restored-validation'} else 'unknown' if failure=='inactive' else 'restored')
    assert len([argv for argv,_ in calls if argv[:2]==('systemctl','reload')])==1
    assert 'private' not in str(result.to_mapping())


def test_helper_encoding_failure_retains_validated_mutation_and_receipt(tmp_path,monkeypatch):
    module,root,calls=mutation_host(tmp_path,monkeypatch)
    authority=module.caddy(request()).state['authority']
    req=request('prepare',authority)
    prepared=_dispatch(req)
    from taskman_ops.host_helper import __main__ as entry
    from taskman_ops.host_protocol import decode_result
    encode=entry.encode_result
    count=0
    def first_failure(result):
        nonlocal count
        count+=1
        if count==1:raise ValueError('private encoding')
        return encode(result)
    monkeypatch.setattr(entry,'encode_result',first_failure)
    result=decode_result(entry._encode_or_internal_failure(prepared,operation='caddy',correlation_id=req.correlation_id,request=req))
    assert result.state['exit_code']!=0
    assert result.state['mutation_state']=='changed'
    assert result.state['receipt']==prepared.state['receipt']
    assert count==2


def test_partial_publication_failure_retains_site_recovery_authority(tmp_path,monkeypatch):
    module,root,calls=mutation_host(tmp_path,monkeypatch,original=b'old site\n')
    authority=module.caddy(request()).state['authority']
    from taskman_ops.host_helper import caddy_files
    atomic=caddy_files.atomic_file
    def fail_after_replace(path,*args,**kwargs):
        atomic(path,*args,**kwargs)
        if path==root/'etc/caddy/sites-enabled/taskman.caddy' and path.read_bytes()!=b'old site\n':
            raise OSError('directory fsync failed')
    monkeypatch.setattr(caddy_files,'atomic_file',fail_after_replace)
    result=_dispatch(request('prepare',authority))
    assert result.state['exit_code']==8
    assert result.state['recovery']['status']=='restored'
    assert (root/'etc/caddy/sites-enabled/taskman.caddy').read_bytes()==b'old site\n'


def test_package_candidate_download_overflow_refuses_before_install(tmp_path,monkeypatch):
    module,root,state,calls=installation_host(tmp_path,monkeypatch)
    authority=module.caddy(request()).state['authority']
    guard=_dispatch(request('guard-install',authority))
    native=module._native
    from taskman_ops.host_helper.commands import CommandError
    def overflow(argv,**kwargs):
        if argv[0]=='sh':raise CommandError('output exceeds bound')
        return native(argv,**kwargs)
    monkeypatch.setattr(module,'_native',overflow)
    result=_dispatch(request('prepare',authority,receipt=guard.state['receipt']))
    assert result.state['exit_code']==8
    assert not state['installed']
    assert (root/'etc/systemd/system/caddy.service').is_symlink()


def test_exhausted_prepare_deadline_refuses_before_package_install(tmp_path,monkeypatch):
    module,root,state,calls=installation_host(tmp_path,monkeypatch)
    authority=module.caddy(request()).state['authority']
    guard=_dispatch(request('guard-install',authority))
    from taskman_ops.host_helper import caddy_package
    inspect=caddy_package.inspect_candidate
    def expire(observer, directory):
        version=inspect(observer, directory)
        observer._DEADLINE.set(0)
        return version
    monkeypatch.setattr(caddy_package,'inspect_candidate',expire)
    result=_dispatch(request('prepare',authority,receipt=guard.state['receipt']))
    assert result.state['exit_code']!=0
    assert not state['installed']


def test_install_consumes_inspected_package_without_second_download(tmp_path,monkeypatch):
    module,root,state,calls=installation_host(tmp_path,monkeypatch)
    native=module._native
    extracted=[]
    def consume(argv,**kwargs):
        if argv[:2]==('dpkg-deb','--show'):
            extracted.append(Path(argv[-1]))
        if argv[:2]==('apt-get','install'):
            cache=Path(next(arg.split('=',1)[1] for arg in argv if arg.startswith('Dir::Cache::archives=')))
            assert list(cache.glob('*.deb'))==extracted
            assert extracted[0].read_bytes()==b'authenticated package fixture'
        if argv[0] == 'sh':
            assert argv[-1] == 'caddy=2.11.7'
            assert 'APT::Get::AllowUnauthenticated=false' in argv
            assert 'Acquire::AllowInsecureRepositories=false' in argv
            assert 'Acquire::AllowDowngradeToInsecureRepositories=false' in argv
        return native(argv,**kwargs)
    monkeypatch.setattr(module,'_native',consume)
    authority=module.caddy(request()).state['authority']
    guard=_dispatch(request('guard-install',authority))
    result=_dispatch(request('prepare',authority,receipt=guard.state['receipt']))
    assert result.outcome=='succeeded'
    install=next(argv for argv,_ in calls if argv[:2]==('apt-get','install'))
    assert len([argv for argv, _ in calls if argv[0] == 'sh']) == 1
    assert '--no-download' in install
    assert '--no-install-recommends' in install
    assert '--no-remove' in install
    assert 'APT::Get::AllowUnauthenticated=false' in install
    assert 'Acquire::AllowInsecureRepositories=false' in install
    assert 'Acquire::AllowDowngradeToInsecureRepositories=false' in install
    assert any(arg.startswith('Dir::Cache::archives=') for arg in install)


def test_inspect_shares_45_second_deadline_and_clears_context(tmp_path,monkeypatch):
    module,root,command=host(tmp_path,monkeypatch)
    started=[]
    original=module._platform
    def platform():
        remaining=module._remaining(1000)
        assert 44 < remaining <= 45
        started.append(remaining)
        original()
        module._DEADLINE.set(0)
    monkeypatch.setattr(module,'_platform',platform)
    result=module.caddy(request())
    assert result.state['exit_code']==5
    assert result.state['authority'] is None
    assert module._DEADLINE.get() is None
    assert len(started)==1


def test_native_output_overflow_is_suppressed_and_bounded(capsys):
    from taskman_ops.host_helper.operations import caddy as module
    from taskman_ops.host_helper.commands import CommandError
    with pytest.raises(CommandError):
        module._native(('python3','-c','import sys;sys.stderr.write("PRIVATE"*4000)'),timeout_seconds=3)
    output=capsys.readouterr()
    assert output.out==output.err==''


def test_reload_failure_with_unchanged_site_still_reports_runtime_uncertainty(tmp_path,monkeypatch):
    module,root,calls=mutation_host(tmp_path,monkeypatch,original=request().parameters['site_content'].encode())
    authority=module.caddy(request()).state['authority']
    prepared=module.caddy(request('prepare',authority))
    from taskman_ops.host_helper.commands import CommandError
    def rejection(argv,**kwargs):
        if argv[:2]==('systemctl','reload'):raise CommandError('command exited unsuccessfully')
        return b''
    monkeypatch.setattr(module,'_native',rejection)
    result=_dispatch(request('activate',prepared.state['authority'],receipt=prepared.state['receipt'],public_probe='not-ready'))
    assert result.state['exit_code']==8
    assert result.state['reload']=='failed'
    assert result.state['recovery']['status']=='not-needed'
    assert result.state['recovery']['runtime_uncertain'] is True


def test_preservation_unsafe_metadata_is_install_failure_before_site(tmp_path,monkeypatch):
    module,root,state,calls=installation_host(tmp_path,monkeypatch)
    authority=module.caddy(request()).state['authority']
    guard=module.caddy(request('guard-install',authority))
    native=module._native
    def unsafe(argv,**kwargs):
        value=native(argv,**kwargs)
        if argv[:2]==('apt-get','install'):(root/'etc/caddy/Caddyfile').chmod(0o666)
        return value
    monkeypatch.setattr(module,'_native',unsafe)
    result=_dispatch(request('prepare',authority,receipt=guard.state['receipt']))
    assert result.state['exit_code']==8
    assert result.state['failed_boundary']=='package'
    assert result.state['authority'] is None
    assert not (root/'etc/caddy/sites-enabled/taskman.caddy').exists()


def test_runtime_mask_is_retained_and_missing_persistent_guard_is_created(tmp_path,monkeypatch):
    module,root,state,calls=installation_host(tmp_path,monkeypatch,masked=True)
    directory=root/'run/systemd/system';directory.mkdir(parents=True)
    (root/'etc/systemd/system/caddy.service').rename(directory/'caddy.service')
    command=module._command
    def runtime(argv):
        status,out,err=command(argv)
        if argv[:3]==('systemctl','show','caddy.service') and not (root/'etc/systemd/system/caddy.service').is_symlink():
            out=out.replace(b'LoadState=not-found',b'LoadState=masked').replace(b'UnitFileState=\n',b'UnitFileState=masked-runtime\n').replace(b'FragmentPath=\n',b'FragmentPath=/dev/null\n')
        return status,out,err
    monkeypatch.setattr(module,'_command',runtime)
    authority=module.caddy(request()).state['authority']
    assert authority is not None
    guard=_dispatch(request('guard-install',authority))
    assert guard.outcome=='succeeded'
    assert tuple(guard.state['receipt']['created_masks'])==('caddy.service','caddy-api.service')
    assert (root/'etc/systemd/system/caddy.service').is_symlink()
    assert (directory/'caddy.service').is_symlink()


def test_install_keeps_root_caddy_main_and_configuration_directory_metadata(tmp_path,monkeypatch):
    module,root,state,calls=installation_host(tmp_path,monkeypatch)
    main=root/'etc/caddy/Caddyfile';directory=root/'etc/caddy'
    main.chmod(0o640);directory.chmod(0o750)
    main.write_bytes(b'import sites-enabled/*.caddy\n# operator configuration\n')
    original=(main.read_bytes(),main.stat().st_mtime_ns)
    authority=module.caddy(request()).state['authority']
    guard=_dispatch(request('guard-install',authority))
    result=_dispatch(request('prepare',authority,receipt=guard.state['receipt']))
    assert result.outcome=='succeeded'
    assert result.state['authority']['main']=={'uid':0,'gid':998,'mode':0o640}
    assert result.state['authority']['configuration_directory']=={'uid':0,'gid':0,'mode':0o750}
    assert (main.read_bytes(),main.stat().st_mtime_ns)==original


def test_isolated_packaged_prepare_runs_filesystem_observer_and_mutation_path(tmp_path,monkeypatch):
    import subprocess
    import sys
    from taskman_ops.helper_client.package import build_helper_package
    from taskman_ops.host_protocol import encode_request,decode_result,validate_caddy_result
    module,root,command=host(tmp_path,monkeypatch,active=False)
    authority=module.caddy(request()).state['authority']
    package=build_helper_package(tmp_path/'caddy.pyz')
    script=r'''import sys,os,hashlib,pwd,subprocess
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv.pop(1))
from taskman_ops.host_helper.operations import caddy as proxy
proxy._ROOT=root=Path(sys.argv.pop(1))
native_stat=proxy._stat
def root_stat(path):
    info=native_stat(path)
    if info is None:return None
    fields=list(info);fields[4]=0;fields[5]=998 if path==root/'etc/caddy/Caddyfile' else 0
    return os.stat_result(fields)
proxy._stat=root_stat
os.fchown=lambda *args:None
os.chown=lambda *args:None
pwd.getpwnam=lambda name:SimpleNamespace(pw_uid=998,pw_gid=998)
os.getgrouplist=lambda *args:[998]
def run(argv,**kwargs):
    assert 0 < kwargs['timeout_seconds'] <= 30
    assert kwargs['output_limit']==16384
    if argv[0]=='sudo':
        # Native validator may emit configuration diagnostics; helper suppresses them.
        return subprocess.CompletedProcess(argv,0,b'private configuration',b'private diagnostics')
    native=tuple(argv[4:])
    status=0;out=b'';err=b''
    if native[:2]==('dpkg-query','--show'):out=b'installed\t2.10.2\n'
    elif native[:2]==('dpkg-query','--listfiles'):out=b'/usr/bin/caddy\n/usr/lib/systemd/system/caddy.service\n/usr/lib/systemd/system/caddy-api.service\n'
    elif native[:2]==('dpkg-query','--control-show'):
        out=b''.join(f'{hashlib.md5(p.read_bytes()).hexdigest()}  {p.relative_to(root)}\n'.encode() for p in (root/'usr/bin/caddy',root/'usr/lib/systemd/system/caddy.service',root/'usr/lib/systemd/system/caddy-api.service'))
    elif native[:2]==('systemctl','show'):
        name=native[2]
        fields=dict(LoadState='loaded',ActiveState='inactive',UnitFileState='enabled' if name=='caddy.service' else 'disabled',FragmentPath='/usr/lib/systemd/system/'+name,DropInPaths='',MainPID='0')
        out=''.join(f'{k}={v}\n' for k,v in fields.items()).encode()
    elif native[0]=='ss':pass
    elif native==('uname','-m'):out=b'x86_64\n'
    elif native[0]=='grep':
        p=subprocess.run(native,capture_output=True);status,out,err=p.returncode,p.stdout,p.stderr
    else:raise AssertionError('unexpected native operation')
    return subprocess.CompletedProcess(argv,0,out+b'\nTASKMAN_STATUS='+str(status).encode()+b'\n',err)
proxy.run_command=run
from taskman_ops.host_helper.__main__ import main
raise SystemExit(main())
'''
    req=request('prepare',authority)
    completed=subprocess.run((sys.executable,'-I','-S','-c',script,str(package.path),str(root)),input=encode_request(req),capture_output=True)
    assert completed.returncode==0
    assert completed.stderr==b''
    result=decode_result(completed.stdout)
    assert validate_caddy_result(req,result).outcome=='succeeded',result.to_mapping()
    assert result.state['receipt']['site_changed'] is True
    assert (root/'etc/caddy/sites-enabled/taskman.caddy').read_bytes()==req.parameters['site_content'].encode()
    assert b'private' not in completed.stdout


def test_bootstrap_start_lost_reply_preserves_runtime_uncertainty(tmp_path,monkeypatch):
    module,root,state,calls=installation_host(tmp_path,monkeypatch,preserved=False,main=False)
    authority=module.caddy(request()).state['authority']
    guard=module.caddy(request('guard-install',authority))
    native=module._native
    from taskman_ops.host_helper.commands import CommandTimeout
    def lost(argv,**kwargs):
        result=native(argv,**kwargs)
        if argv[:2]==('systemctl','start'):raise CommandTimeout('start reply lost')
        return result
    monkeypatch.setattr(module,'_native',lost)
    result=_dispatch(request('prepare',authority,receipt=guard.state['receipt']))
    assert result.state['exit_code']==8
    assert result.state['failed_boundary']=='activation'
    assert result.state['mutation_state']=='changed'
    assert result.state['recovery']['runtime_uncertain'] is True
    assert len([argv for argv,_ in calls if argv[:2]==('systemctl','start')])==1


@pytest.mark.parametrize(('version', 'default', 'priority', 'preserved'), [
    ('2.12.0', b'# new upstream default\n:8080 { respond "welcome" }\n', 700, False),
    ('2.13.1-1', b'# another valid upstream default\n', 990, True),
])
def test_authenticated_future_candidate_uses_its_default_and_preserves_operator_main(
        tmp_path, monkeypatch, version, default, priority, preserved):
    # A permanent release/hash/default restriction would refuse these valid candidates.
    module, root, state, calls = installation_host(tmp_path, monkeypatch, preserved=preserved,
        main=preserved, version=version, default=default, priority=priority)
    authority = module.caddy(request()).state['authority']
    before = (root/'etc/caddy/Caddyfile').read_bytes() if preserved else None
    guard = module.caddy(request('guard-install', authority))
    result = module.caddy(request('prepare', authority, receipt=guard.state['receipt']))
    assert result.outcome == 'succeeded', result.to_mapping()
    assert result.state['receipt']['installed_package_version'] == version
    assert result.state['authority']['service_active'] is (not preserved)
    assert (root/'etc/caddy/Caddyfile').read_bytes() == (before if preserved else b'import /etc/caddy/sites-enabled/*.caddy\n')
    assert any(argv[:2] == ('dpkg-deb', '--extract') for argv, _ in calls) is (not preserved)


@pytest.mark.parametrize('failure', ['name', 'version', 'architecture', 'malformed', 'origin', 'authentication'])
def test_candidate_identity_origin_or_native_authentication_failure_never_installs(tmp_path, monkeypatch, failure):
    module, root, state, calls = installation_host(tmp_path, monkeypatch)
    native = module._native
    def refused(argv, **kwargs):
        if argv[:2] == ('dpkg-deb', '--show'):
            return {'name': b'other\t2.11.7\tamd64\n', 'version': b'caddy\t2.12.0\tamd64\n',
                    'architecture': b'caddy\t2.11.7\tarm64\n', 'malformed': b'caddy\t2.11.7\n'}.get(failure, native(argv, **kwargs))
        if failure == 'origin' and argv[:2] == ('apt-cache', 'policy'):
            return native(argv, **kwargs).replace(b'dl.cloudsmith.io', b'foreign.example')
        if failure == 'authentication' and argv[0] == 'sh':
            raise RuntimeError('native APT authentication rejected')
        return native(argv, **kwargs)
    monkeypatch.setattr(module, '_native', refused)
    authority = module.caddy(request()).state['authority']
    guard = module.caddy(request('guard-install', authority))
    result = module.caddy(request('prepare', authority, receipt=guard.state['receipt']))
    assert result.state['exit_code'] == 8
    assert result.state['failed_boundary'] == 'package'
    assert not state['installed']
    assert not (root/'etc/caddy/sites-enabled/taskman.caddy').exists()
    assert (root/'etc/systemd/system/caddy.service').is_symlink()
    assert 'native APT' not in str(result.to_mapping())


def test_cached_archive_drift_before_install_refuses_without_package_mutation(tmp_path, monkeypatch):
    # Missing the once-only boundary comparison would install changed bytes.
    module, root, state, calls = installation_host(tmp_path, monkeypatch)
    from taskman_ops.host_helper import caddy_package
    inspect = caddy_package.inspect_candidate
    def drift(observer, directory):
        selection = inspect(observer, directory)
        next(directory.glob('*.deb')).write_bytes(b'ordinary cache clash')
        return selection
    monkeypatch.setattr(caddy_package, 'inspect_candidate', drift)
    authority = module.caddy(request()).state['authority']
    guard = module.caddy(request('guard-install', authority))
    result = module.caddy(request('prepare', authority, receipt=guard.state['receipt']))
    assert result.state['exit_code'] == 8
    assert not state['installed']
    assert not any(argv[:2] == ('apt-get', 'install') for argv, _ in calls)


@pytest.mark.parametrize('mismatch', ['bytes', 'mode'])
def test_bootstrap_installed_default_mismatch_keeps_masks_and_original_default(tmp_path, monkeypatch, mismatch):
    # Replacing a default without comparison would overwrite an ordinary package clash.
    module, root, state, calls = installation_host(tmp_path, monkeypatch, preserved=False, main=False)
    native = module._native
    def mismatched(argv, **kwargs):
        result = native(argv, **kwargs)
        if argv[:2] == ('apt-get', 'install'):
            path = root/'etc/caddy/Caddyfile'
            if mismatch == 'bytes': path.write_bytes(b'package changed its default\n')
            else: path.chmod(0o640)
        return result
    monkeypatch.setattr(module, '_native', mismatched)
    authority = module.caddy(request()).state['authority']
    guard = module.caddy(request('guard-install', authority))
    result = module.caddy(request('prepare', authority, receipt=guard.state['receipt']))
    assert result.state['exit_code'] == 8
    assert result.state['failed_boundary'] == 'package'
    assert result.state['mutation_state'] == 'changed'
    assert state['installed'] and not state['active']
    assert (root/'etc/systemd/system/caddy.service').is_symlink()
    assert (root/'etc/caddy/Caddyfile').read_bytes() != b'import /etc/caddy/sites-enabled/*.caddy\n'


@pytest.mark.parametrize('failure', ['missing', 'symlink', 'mode', 'owner', 'oversized', 'native'])
def test_unsafe_or_unavailable_extracted_default_refuses_before_install(tmp_path, monkeypatch, failure):
    module, root, state, calls = installation_host(tmp_path, monkeypatch, preserved=False, main=False)
    native, observed_stat = module._native, module._stat
    def extraction(argv, **kwargs):
        if failure == 'native' and argv[:2] == ('dpkg-deb', '--extract'):
            raise RuntimeError('private extraction error')
        result = native(argv, **kwargs)
        if argv[:2] == ('dpkg-deb', '--extract'):
            path = Path(argv[3])/'etc/caddy/Caddyfile'
            if failure == 'missing': path.unlink()
            elif failure == 'symlink':
                path.rename(path.with_name('target'))
                path.symlink_to('target')
            elif failure == 'mode': path.chmod(0o640)
            elif failure == 'oversized': path.write_bytes(b'x' * 65537)
        return result
    def owner(path):
        info = observed_stat(path)
        if info is not None and root not in path.parents and path.name == 'Caddyfile':
            fields = list(info); fields[4] = 1000
            return os.stat_result(fields)
        return info
    if failure == 'owner': monkeypatch.setattr(module, '_stat', owner)
    monkeypatch.setattr(module, '_native', extraction)
    authority = module.caddy(request()).state['authority']
    guard = module.caddy(request('guard-install', authority))
    result = module.caddy(request('prepare', authority, receipt=guard.state['receipt']))
    assert result.state['exit_code'] == 8
    assert result.state['failed_boundary'] == 'package'
    assert not state['installed'] and not state['active']
    assert not (root/'etc/caddy').exists()
    assert (root/'etc/systemd/system/caddy.service').is_symlink()
    assert 'private extraction' not in str(result.to_mapping())


def test_native_package_identity_and_extraction_admit_future_defaults_and_dependencies(
        tmp_path, monkeypatch):
    # Native tools must read identity/defaults without a per-release control/script contract.
    import shutil, subprocess
    version, default = '2.13.1-1', b'# native archive default B\n'
    executable = shutil.which('dpkg-deb')
    if executable is None: pytest.skip('native dpkg-deb unavailable')
    payload = tmp_path/'package'
    (payload/'DEBIAN').mkdir(parents=True)
    (payload/'DEBIAN/control').write_text(
        f'Package: caddy\nVersion: {version}\nArchitecture: amd64\nMaintainer: Fixture <fixture@example.invalid>\n'
        'Description: Native identity and default fixture\nDepends: libc6\nRecommends: curl\n')
    script = payload/'DEBIAN/postinst'
    script.write_text('#!/bin/sh\nexit 0\n'); script.chmod(0o755)
    main = payload/'etc/caddy/Caddyfile'
    main.parent.mkdir(parents=True); main.write_bytes(default); main.chmod(0o644)
    archive = tmp_path/'candidate.deb'
    built = subprocess.run((executable, '--root-owner-group', '--build', str(payload), str(archive)),
                           capture_output=True, timeout=10)
    assert built.returncode == 0, built.stderr
    module, root, state, calls = installation_host(tmp_path, monkeypatch, preserved=False,
        main=False, version=version, default=default, priority=900)
    native = module._native
    def native_package(argv, **kwargs):
        if argv[0] == 'dpkg-deb':
            calls.append((argv, kwargs))
            result = subprocess.run(argv, capture_output=True, timeout=kwargs['timeout_seconds'])
            assert result.returncode == 0, result.stderr
            return result.stdout
        result = native(argv, **kwargs)
        if argv[0] == 'sh': shutil.copyfile(archive, next(Path(argv[4]).glob('*.deb')))
        return result
    monkeypatch.setattr(module, '_native', native_package)
    authority = module.caddy(request()).state['authority']
    guard = module.caddy(request('guard-install', authority))
    result = module.caddy(request('prepare', authority, receipt=guard.state['receipt']))
    assert result.outcome == 'succeeded', result.to_mapping()
    assert result.state['receipt']['installed_package_version'] == version
    assert (root/'etc/caddy/Caddyfile').read_bytes() == b'import /etc/caddy/sites-enabled/*.caddy\n'
    assert state['active']


@pytest.mark.parametrize('size', [0, 100*1024*1024+1])
def test_archive_bounds_refuse_before_control_extraction(tmp_path,monkeypatch,size):
    module,root,state,calls=installation_host(tmp_path,monkeypatch)
    authority=module.caddy(request()).state['authority'];guard=module.caddy(request('guard-install',authority))
    native=module._native
    def wrong_size(argv,**kwargs):
        result=native(argv,**kwargs)
        if argv[0]=='sh':
            with (Path(argv[4])/'caddy_2.11.7_amd64.deb').open('wb') as stream:stream.truncate(size)
        return result
    monkeypatch.setattr(module,'_native',wrong_size)
    result=module.caddy(request('prepare',authority,receipt=guard.state['receipt']))
    assert result.state['exit_code']==8
    assert not any(argv[:2] in {('apt-get','install'),('dpkg-deb','--show')} for argv,_ in calls)
