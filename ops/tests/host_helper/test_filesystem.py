"""Filesystem primitives shared by host operation procedures."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def test_sha256_file_reads_binary_content_in_bounded_chunks(tmp_path: Path) -> None:
    """A changed dump digest must be detected before publication or restore."""

    assert importlib.util.find_spec("taskman_ops.host_helper.filesystem") is not None
    from taskman_ops.host_helper.filesystem import sha256_file

    dump = tmp_path / "backup.dump"
    dump.write_bytes(b"abc")

    assert sha256_file(dump) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_fsync_directory_flushes_the_open_directory_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping the directory flush could lose an atomic link or manifest replacement."""

    assert importlib.util.find_spec("taskman_ops.host_helper.filesystem") is not None
    from taskman_ops.host_helper import filesystem

    flushed: list[int] = []

    def record_fsync(descriptor: int) -> None:
        assert Path("/proc/self/fd", str(descriptor)).resolve() == tmp_path
        flushed.append(descriptor)

    monkeypatch.setattr(filesystem.os, "fsync", record_fsync)

    filesystem.fsync_directory(tmp_path)

    assert len(flushed) == 1
