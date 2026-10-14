"""Native-shaped socket output through a real bounded subprocess."""

from pathlib import Path
import sys


def write_ss_fixture(directory: Path, expected_args: tuple[str, ...], selected: bytes) -> Path:
    """An unfiltered query includes a large unrelated shared-process listener."""
    owners = ','.join(f'("zabbix_agentd",pid={pid},fd=4)' for pid in range(1000, 1700))
    unrelated = f'LISTEN 0 4096 *:10050 *:* users:({owners})\n'.encode()
    assert len(unrelated) > 16 * 1024
    executable = directory / 'ss-fixture'
    executable.write_text(
        f'#!{sys.executable}\n'
        'import sys\n'
        f'selected = {selected!r}\n'
        f'unrelated = {unrelated!r}\n'
        f'expected = {list(expected_args)!r}\n'
        'sys.stdout.buffer.write(selected if sys.argv[1:] == expected else unrelated + selected)\n'
    )
    executable.chmod(0o700)
    return executable
