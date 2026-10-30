"""Controller mapping of the private upload capability's exit-only contract."""

import importlib
from pathlib import PurePosixPath
import subprocess

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.remote import CommandResult
from tests.support.remotes import ScriptedRemote


@pytest.mark.parametrize('operation', ['deploy', 'genesis', 'prepare_genesis', 'restore'])
@pytest.mark.parametrize('code,mutation', [(0, 'unchanged'), (3, 'changed'), (10, 'unchanged'), (1, 'unknown'), (77, 'unknown'), (True, 'unknown'), (None, 'unknown')])
def test_upload_preparation_status_mapping(operation, code, mutation):
    uploads = importlib.import_module('taskman_ops.uploads')
    response = OpsError(5, 'remote', 'private exception canary') if code is None else CommandResult(code, 'private stdout canary', 'private stderr canary')
    remote = ScriptedRemote.from_responses([response])
    if code in (0, 3) and type(code) is int:
        assert uploads.prepare_upload_root(remote, PurePosixPath('/srv/custom'), operation=operation) == mutation
    else:
        with pytest.raises(OpsError) as raised:
            uploads.prepare_upload_root(remote, PurePosixPath('/srv/custom'), operation=operation)
        error = raised.value
        if code == 10:
            assert (error.status, error.stage, error.message) == (ExitStatus.SAFETY, operation, 'upload staging path is unsafe')
        else:
            assert (error.status, error.stage, error.message) == (
                ExitStatus.RESTORE if operation == 'restore' else ExitStatus.RELEASE,
                'restore' if operation == 'restore' else 'release', 'unable to prepare private upload staging')
        assert error.state == {'mutation_state': mutation}
        assert error.changed is (mutation != 'unchanged')
        assert 'canary' not in str(error)
    command, options = remote.calls[0]
    assert command[:4] == ('/usr/bin/python3', '-I', '-S', '-c')
    assert command[5:] == ('/srv/custom',)
    assert options == dict(sudo=True, sensitive=True, timeout=60, stdout_limit=1024, stderr_limit=4096)


def test_remote_entry_executes_the_shared_source_and_rejects_unsafe_root_without_output():
    uploads = importlib.import_module('taskman_ops.uploads')
    remote = ScriptedRemote.from_responses([CommandResult(10)])
    with pytest.raises(OpsError):
        uploads.prepare_upload_root(remote, PurePosixPath('/tmp/taskman'), operation='deploy')
    command = remote.calls[0][0]
    completed = subprocess.run(command, capture_output=True, timeout=5)
    assert completed.returncode == 10
    assert completed.stdout == b''
    assert completed.stderr == b''


def test_lost_preparation_preserves_prior_error_evidence():
    uploads = importlib.import_module('taskman_ops.uploads')
    remote = ScriptedRemote.from_responses([OpsError(5, 'remote', 'private diagnostic', changed=True, warnings=('prior warning',))])
    with pytest.raises(OpsError) as raised:
        uploads.prepare_upload_root(remote, PurePosixPath('/opt/taskman'), operation='restore')
    assert raised.value.changed is True
    assert raised.value.state == {'mutation_state': 'changed'}
    assert raised.value.warnings == ('prior warning',)


@pytest.mark.parametrize('status, expected', [(0, 'unchanged'), (3, 'changed')])
def test_preparation_checks_all_destination_names(status, expected):
    uploads = importlib.import_module('taskman_ops.uploads')
    remote = ScriptedRemote.from_responses([CommandResult(status)])
    assert uploads.prepare_upload_root(remote, PurePosixPath('/opt/taskman'), operation='deploy',
        destinations=('.release.tar.gz', '.backup.pyz')) == expected
    assert remote.calls[0][0][-3:] == ('/opt/taskman', '.release.tar.gz', '.backup.pyz')
