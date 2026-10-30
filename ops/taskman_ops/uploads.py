"""Controller invocation of the shared private-upload filesystem capability."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from .errors import ExitStatus, OpsError
from .host_helper import paths

if TYPE_CHECKING:
    from .remote import Remote


# Numeric statuses are the complete private contract; protected command output
# never participates in controller decisions.
_PREPARE_TIMEOUT = 60


_PREPARATION_ENTRY = """
import sys
try:
    changed = prepare_upload_root(PurePosixPath(sys.argv[1]), tuple(sys.argv[2:]))
except PathAuthorityError:
    raise SystemExit(10)
except Exception:
    raise SystemExit(1)
raise SystemExit(3 if changed else 0)
"""


def _preparation_command(install_root: PurePosixPath, destinations: tuple[str, ...]) -> tuple[str, ...]:
    source = Path(paths.__file__).read_text(encoding='utf-8')
    return ('/usr/bin/python3', '-I', '-S', '-c', source + _PREPARATION_ENTRY, str(install_root), *destinations)


def prepare_upload_root(remote: Remote, install_root: PurePosixPath, *, operation: str, destinations: tuple[str, ...] = ()) -> str:
    """Prepare the derived root and return command-local managed mutation evidence."""

    if operation not in {'deploy', 'genesis', 'prepare_genesis', 'restore'}:
        raise ValueError('invalid upload preparation operation')
    paths._upload_install_root(install_root)
    warnings: tuple[str, ...] = ()
    mutation = 'unknown'
    try:
        result = remote.run(
            _preparation_command(install_root, destinations),
            sudo=True, sensitive=True, timeout=_PREPARE_TIMEOUT,
            stdout_limit=1024, stderr_limit=4096,
        )
        code = result.returncode
    except Exception as error:
        if isinstance(error, OpsError):
            warnings = error.warnings
            if error.changed or error.state.get('mutation_state') == 'changed':
                mutation = 'changed'
        code = None
    if type(code) is int:
        if code == 0:
            return 'unchanged'
        if code == 3:
            return 'changed'
        if code == 10:
            raise OpsError(ExitStatus.SAFETY, operation, 'upload staging path is unsafe',
                           state={'mutation_state': 'unchanged'}) from None
    raise OpsError(
        ExitStatus.RESTORE if operation == 'restore' else ExitStatus.RELEASE,
        'restore' if operation == 'restore' else 'release',
        'unable to prepare private upload staging', changed=mutation != 'unchanged',
        state={'mutation_state': mutation}, warnings=warnings,
    ) from None
