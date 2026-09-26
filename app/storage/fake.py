"""In-memory object storage — TEST ONLY (M4.1).

Never selected outside tests: config validation refuses ``ACP_STORAGE_DRIVER=fake``
in production (the CR-048 trap — a store that silently discards bytes while
reporting success). It still enforces the same key rules so tests catch traversal
bugs identically to the local driver.
"""

from __future__ import annotations

from typing import BinaryIO

from app.storage.contract import UploadTooLarge
from app.storage.local import _STREAM_CHUNK, _safe_relative


class FakeObjectStorage:
    """Keeps bytes in a dict. For tests that must not touch disk."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        _safe_relative(key)  # enforce the same key discipline as the real driver
        self._data[key] = data

    def put_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> int:
        """Stream into the dict, enforcing the same incremental cap as the real driver
        (CR-028 parity). No object is stored on rejection."""

        _safe_relative(key)
        buf = bytearray()
        while True:
            chunk = stream.read(_STREAM_CHUNK)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise UploadTooLarge(f"upload exceeds the {max_bytes}-byte limit")
        self._data[key] = bytes(buf)
        return len(buf)

    def get(self, key: str) -> bytes:
        _safe_relative(key)
        return self._data[key]

    def path_for(self, key: str) -> str | None:
        """No filesystem path — the worker falls back to :meth:`get` (CR-028)."""

        return None

    def exists(self, key: str) -> bool:
        _safe_relative(key)
        return key in self._data

    def delete(self, key: str) -> None:
        _safe_relative(key)
        self._data.pop(key, None)
