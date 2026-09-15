"""Public restore entry validation and preflight ordering."""

from __future__ import annotations

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.workflows.restore import restore
from tests.workflows.test_deploy import config


BACKUP = "backup-" + "a" * 32


def test_restore_specific_preflight_refuses_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight",
        lambda *_args: (_ for _ in ()).throw(
            OpsError(
                ExitStatus.REMOTE_PREFLIGHT,
                "preflight",
                "unsafe restore prerequisite",
                False,
            )
        ),
    )
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.run_request",
        lambda *_args: pytest.fail("restore discovery must wait for preflight"),
    )

    result = restore(object(), config(), BACKUP)

    assert result.exit_status is ExitStatus.REMOTE_PREFLIGHT
    assert result.stage == "preflight-failed"


def test_restore_rejects_conflicting_recovery_modes_before_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight",
        lambda *_args: pytest.fail("invalid flags must not inspect the host"),
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        restore(
            object(),
            config(),
            BACKUP,
            replace_unfinished=True,
            reapply=True,
        )


@pytest.mark.parametrize("backup_id", ("backup", "../backup", "backup-" + "a" * 31))
def test_restore_rejects_invalid_backup_identity_before_preflight(
    backup_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "taskman_ops.workflows.restore.validate_restore_preflight",
        lambda *_args: pytest.fail("invalid backup must not inspect the host"),
    )

    with pytest.raises(ValueError, match="identifier"):
        restore(object(), config(), backup_id)
