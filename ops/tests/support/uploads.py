"""Synthetic root ownership for disposable host upload consumers."""

import os
import stat

import pytest


@pytest.fixture
def upload_authority(tmp_path, monkeypatch):
    """Keep real pathname checks and leaf modes; model root-owned fixture ancestry."""
    from taskman_ops.host_helper import paths

    ancestors = {path.stat().st_ino for path in tmp_path.parents}
    metadata = paths._upload_metadata
    def synthetic_metadata(path):
        values = list(metadata(path))
        values[4:6] = [0, 0]
        if values[1] in ancestors and stat.S_ISDIR(values[0]):
            values[0] = stat.S_IFDIR | 0o755
        return os.stat_result(values)
    monkeypatch.setattr(paths, "_upload_metadata", synthetic_metadata)
    monkeypatch.setattr(paths.os, "chown", lambda *_args: None)
