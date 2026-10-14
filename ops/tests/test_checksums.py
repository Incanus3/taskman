"""Streaming checksums shared by artifact and host code."""

import io
from pathlib import Path

import pytest

from taskman_ops.checksums import sha256_file


@pytest.mark.parametrize("content, expected", (
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
))
def test_hashes_file_bytes(tmp_path: Path, content: bytes, expected: str) -> None:
    path = tmp_path / "data"
    path.write_bytes(content)
    assert sha256_file(path) == expected


def test_reads_in_bounded_chunks_and_closes_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    class BoundedStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 1024 * 1024
            return super().read(min(size, 1))

    stream = BoundedStream(b"abc")

    def open_binary(path: Path, mode: str):
        assert mode == "rb"
        return stream

    monkeypatch.setattr(Path, "open", open_binary)
    assert sha256_file(Path("data")) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert stream.closed


def test_missing_file_error_reaches_caller(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path / "missing")


@pytest.mark.parametrize("expiry", ("before-open", "during-read", "at-eof"))
def test_deadline_exhaustion_raises_and_closes_the_stream(monkeypatch, expiry) -> None:
    import time
    clock = [1.0 if expiry == "before-open" else 0.0]
    reads = []

    class ExpiringStream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            chunk = super().read(size)
            if expiry == "during-read" or not chunk:
                clock[0] = 1.0
            return chunk

    stream = ExpiringStream(b"abc")
    monkeypatch.setattr(time, "monotonic", lambda: clock[0], raising=False)
    monkeypatch.setattr(Path, "open", lambda _path, _mode: stream)
    with pytest.raises(TimeoutError):
        sha256_file(Path("data"), deadline=1.0)
    if expiry == "before-open":
        assert reads == []
    else:
        assert stream.closed
        assert reads == ([1048576] if expiry == "during-read" else [1048576, 1048576])


def test_unexpired_deadline_keeps_the_exact_checksum(tmp_path, monkeypatch) -> None:
    import time
    monkeypatch.setattr(time, "monotonic", lambda: 0.0, raising=False)
    path = tmp_path / "data"
    path.write_bytes(b"abc")
    assert sha256_file(path, deadline=1.0) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
