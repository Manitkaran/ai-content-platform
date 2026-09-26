"""M4.1 — object-storage seam: local default, traversal refused, fake test-only."""

from __future__ import annotations

import pytest

from app.config import ConfigError, Settings
from app.storage.contract import StorageKeyError
from app.storage.fake import FakeObjectStorage
from app.storage.local import LocalObjectStorage
from app.storage.registry import build_storage


def test_local_roundtrip(tmp_path):
    store = LocalObjectStorage(str(tmp_path / "objects"))
    store.put("media/a.mp3", b"bytes")
    assert store.exists("media/a.mp3")
    assert store.get("media/a.mp3") == b"bytes"
    store.delete("media/a.mp3")
    assert not store.exists("media/a.mp3")


def test_local_refuses_traversal(tmp_path):
    store = LocalObjectStorage(str(tmp_path / "objects"))
    with pytest.raises(StorageKeyError):
        store.put("../escape.txt", b"x")
    with pytest.raises(StorageKeyError):
        store.get("/etc/passwd")


def test_delete_missing_is_noop(tmp_path):
    store = LocalObjectStorage(str(tmp_path / "objects"))
    store.delete("media/missing.mp3")  # no raise


def test_registry_default_is_local():
    store = build_storage(Settings(env={"ACP_STORAGE_DRIVER": "local"}))
    assert isinstance(store, LocalObjectStorage)


def test_fake_selected_only_when_configured():
    # never the production default; config validation forbids fake in production
    with pytest.raises(ConfigError):
        Settings(env={"ACP_ENV": "production", "ACP_STORAGE_DRIVER": "fake"})
    store = build_storage(
        Settings(env={"ACP_ENV": "testing", "ACP_STORAGE_DRIVER": "fake"})
    )
    assert isinstance(store, FakeObjectStorage)


def test_fake_also_refuses_traversal():
    with pytest.raises(StorageKeyError):
        FakeObjectStorage().put("../x", b"1")


# --- CR-028: streaming put with an incremental size cap + path_for ------------

import io  # noqa: E402

from app.storage.contract import UploadTooLarge  # noqa: E402


def test_local_put_stream_roundtrip(tmp_path):
    store = LocalObjectStorage(str(tmp_path / "objects"))
    written = store.put_stream("media/a.wav", io.BytesIO(b"hello world"), max_bytes=100)
    assert written == 11
    assert store.get("media/a.wav") == b"hello world"


def test_fake_put_stream_roundtrip():
    store = FakeObjectStorage()
    written = store.put_stream("media/a.wav", io.BytesIO(b"hello"), max_bytes=100)
    assert written == 5
    assert store.get("media/a.wav") == b"hello"


def test_local_put_stream_rejects_oversized_early(tmp_path):
    """AC-1: the cap is enforced INCREMENTALLY — an oversized stream raises as soon as the
    running total crosses the cap (within one read chunk), not after consuming the whole
    thing, and nothing is left stored under the key.

    Uses a chunk-sized reader so we can assert it stopped ~one chunk past the cap rather
    than draining an arbitrarily large body (which is what bounds memory for real >1 MiB
    uploads)."""

    from app.storage.local import _STREAM_CHUNK

    store = LocalObjectStorage(str(tmp_path / "objects"))
    cap = 3 * _STREAM_CHUNK  # cap at 3 chunks

    class _CountingStream(io.RawIOBase):
        def __init__(self, total):
            self._left = total
            self.read_bytes = 0

        def readable(self):
            return True

        def read(self, n=-1):
            if self._left <= 0:
                return b""
            take = self._left if n < 0 else min(n, self._left)
            self._left -= take
            self.read_bytes += take
            return b"\x00" * take

    src = _CountingStream(100 * _STREAM_CHUNK)  # a 100-chunk "huge" upload
    with pytest.raises(UploadTooLarge):
        store.put_stream("media/big.wav", src, max_bytes=cap)
    # It stopped ~one chunk past the cap, NOT after draining all 100 chunks.
    assert src.read_bytes <= cap + _STREAM_CHUNK
    # The key was not left with a partial/oversized object.
    assert not store.exists("media/big.wav")


def test_fake_put_stream_rejects_oversized(tmp_path):
    store = FakeObjectStorage()
    with pytest.raises(UploadTooLarge):
        store.put_stream("media/big.wav", io.BytesIO(b"x" * 5000), max_bytes=1000)
    assert not store.exists("media/big.wav")


def test_local_path_for_returns_disk_path(tmp_path):
    store = LocalObjectStorage(str(tmp_path / "objects"))
    store.put("media/a.wav", b"data")
    p = store.path_for("media/a.wav")
    assert p is not None
    import os

    assert os.path.exists(p)
    with open(p, "rb") as fh:
        assert fh.read() == b"data"


def test_fake_path_for_is_none():
    """The fake has no filesystem path → None, so execute falls back to get()."""

    store = FakeObjectStorage()
    store.put("media/a.wav", b"data")
    assert store.path_for("media/a.wav") is None
