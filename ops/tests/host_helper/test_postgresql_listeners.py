"""Selected PostgreSQL socket identity through bounded native observation."""

import os
import shutil
import socket

import pytest

from taskman_ops.host_helper import postgresql as module
from taskman_ops.host_helper.commands import CommandError, run_command
from tests.support.listeners import write_ss_fixture


@pytest.mark.parametrize('oversized_selected_output', [False, True])
def test_selected_port_query_excludes_large_unrelated_process_output(tmp_path, monkeypatch, oversized_selected_output):
    query = ('ss', '-H', '-ltnp', 'sport = :5439')
    selected = (
        b'LISTEN 0 244 127.0.0.1:5439 0.0.0.0:* users:(("postgres",pid=321,fd=8))\n'
        b'LISTEN 0 244 [::1]:5439 [::]:* users:(("postgres",pid=321,fd=9))\n'
    )
    if oversized_selected_output:
        selected += b' ' * (16 * 1024)
    executable = write_ss_fixture(tmp_path, query[1:], selected)
    queries = []

    def command(argv, **kwargs):
        queries.append(argv)
        return run_command((str(executable), *argv[1:]), **kwargs)

    monkeypatch.setattr(module, 'run_command', command)
    if oversized_selected_output:
        with pytest.raises(CommandError, match='output exceeds'):
            module._listeners(5439, 321, '127.0.0.1')
    else:
        module._listeners(5439, 321, '127.0.0.1')
        module._listeners(5439, 321, '::1')
    assert queries == [query] * (1 if oversized_selected_output else 2)


@pytest.mark.parametrize('output', [
    '',
    'malformed listener',
    'LISTEN 0 244 127.0.0.1:5439 0.0.0.0:* users:(("foreign",pid=321,fd=8))',
    'LISTEN 0 244 127.0.0.1:5439 0.0.0.0:* users:(("postgres",pid=322,fd=8))',
    'LISTEN 0 244 0.0.0.0:5439 0.0.0.0:* users:(("postgres",pid=321,fd=8))',
    'LISTEN 0 244 [::1]:5439 [::]:* users:(("postgres",pid=321,fd=8))',
    'LISTEN 0 244 127.0.0.1:5439 0.0.0.0:* users:(("postgres",pid=321,fd=8),("foreign",pid=9,fd=9))',
])
def test_selected_listener_still_requires_complete_loopback_process_identity(monkeypatch, output):
    monkeypatch.setattr(module, '_command', lambda argv: output)
    with pytest.raises(module.PostgresqlObservationError) as error:
        module._listeners(5439, 321, '127.0.0.1')
    assert error.value.exit_code == 10


def test_absent_selected_listener_is_empty_but_a_foreign_socket_is_not_absence(monkeypatch):
    monkeypatch.setattr(module, '_command', lambda argv: '')
    module._listeners(5439)
    monkeypatch.setattr(module, '_command', lambda argv: 'LISTEN 0 244 127.0.0.1:5439 *:* users:(("postgres",pid=321,fd=8))')
    with pytest.raises(module.PostgresqlObservationError):
        module._listeners(5439)


@pytest.mark.skipif(shutil.which('ss') is None, reason='native ss is unavailable')
def test_native_selected_port_filter_retains_real_socket_owner_and_refuses_foreign_process():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        output = module._command(('ss', '-H', '-ltnp', f'sport = :{port}'))
        assert f'127.0.0.1:{port}' in output
        assert f'pid={os.getpid()},' in output
        assert all(int(line.split()[3].rsplit(':', 1)[1]) == port for line in output.splitlines())
        with pytest.raises(module.PostgresqlObservationError) as error:
            module._listeners(port, os.getpid(), '127.0.0.1')
        assert error.value.exit_code == 10
