"""Child-process scenarios for importing only the supplied integration archive."""

import sys


def lazy_ufw_import() -> None:
    import json
    import sys

    from taskman_ops.host_helper import ufw_adapter

    assert not any(name == 'ufw' or name.startswith('ufw.') for name in sys.modules)
    print(json.dumps({'lazy': True}))


def byte_warning() -> None:
    import json
    import sys
    import time

    from taskman_ops.host_helper import ufw_adapter

    before = sys.stdout, sys.stderr

    def warn(**kwargs):
        sys.stderr.buffer.write(b'WARN: malformed tuple\n')

    ufw_adapter._read_backend = warn
    try:
        ufw_adapter.inspect_ufw(deadline=time.monotonic() + 45)
    except ufw_adapter.UfwInspectionError as error:
        assert (sys.stdout, sys.stderr) == before
        print(json.dumps({'reason': error.reason}))
    else:
        raise AssertionError('warning accepted')


def real_ufw_warning(trusted_ufw_root: str) -> None:
    import json
    import sys
    import time

    from pathlib import Path
    from taskman_ops.host_helper import ufw_adapter

    ufw_adapter._UFW_PACKAGE = Path(trusted_ufw_root) / 'usr/lib/python3/dist-packages/ufw'
    # Fixture-only extracted source has an unprivileged temporary parent.
    ufw_adapter._trusted_path = lambda path: None

    def warn(*, deadline):
        ufw_adapter._load_package(deadline=deadline)
        import ufw.util

        ufw.util.warn('malformed tuple')

    ufw_adapter._read_backend = warn
    try:
        ufw_adapter.inspect_ufw(deadline=time.monotonic() + 45)
    except ufw_adapter.UfwInspectionError as error:
        print(json.dumps({'reason': error.reason}))
    else:
        raise AssertionError('warning accepted')


def postgresql_dispatch() -> None:
    import json
    import sys

    from contextlib import nullcontext
    from taskman_ops.host_helper import __main__ as entry
    from taskman_ops.host_helper.operations import postgresql as adapter
    from taskman_ops.host_protocol import HostRequest, decode_result, ProtocolError

    request = HostRequest(
        3,
        'configure_postgresql',
        'op-' + '1' * 32,
        {},
        {'install_root': '/srv/taskman', 'backup_root': '/srv/taskman-backups'},
        {
            'package_track': '18',
            'cluster': None,
            'database': {
                'host': '127.0.0.1', 'port': 5432, 'role': 'taskman', 'name': 'taskman',
            },
            'hba_sha256': 'a' * 64,
        },
    )
    adapter.lifecycle_lock = lambda *a, **k: nullcontext()
    adapter.configure_cluster = lambda *a, **k: True
    result = entry._dispatch(request)
    assert result.outcome == 'succeeded' and dict(result.state) == {'changed': True}
    encode = entry.encode_result
    calls = []

    def fail_once(value):
        calls.append(True)
        if len(calls) == 1:
            raise ProtocolError('synthetic encoding failure')
        return encode(value)

    entry.encode_result = fail_once
    result = decode_result(
        entry._encode_or_internal_failure(
            result,
            request=request,
            operation=request.operation,
            correlation_id=request.correlation_id,
        )
    )
    assert len(calls) == 2
    assert result.state['mutation_state'] == 'changed'
    assert result.state['reason'] == 'internal_failure'
    print(json.dumps({'operation': result.operation, 'changed': result.state['changed']}))


def main() -> None:
    scenarios = {
        "lazy_ufw_import": lazy_ufw_import,
        "byte_warning": byte_warning,
        "real_ufw_warning": real_ufw_warning,
        "postgresql_dispatch": postgresql_dispatch,
    }
    scenario = scenarios[sys.argv[1]]
    # -I -S keeps the checkout and installed packages out of the import path.
    sys.path.insert(0, sys.argv[2])
    scenario(*sys.argv[3:])


if __name__ == "__main__":
    main()
