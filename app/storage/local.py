"""Local-disk object storage — the default driver (M4.1, D21)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from app.storage.contract import StorageKeyError, UploadTooLarge

# Bytes copied per read in put_stream — the peak per-upload RAM cost (CR-028).
_STREAM_CHUNK = 1024 * 1024  # 1 MiB


def _safe_relative(key: str) -> Path:
    """Validate a key and return it as a safe relative path.

    Refuses absolute paths and any key with a ``..`` component, so a resolved
    path can never escape the storage root.
    """

    if not key or key.strip() == "":
        raise StorageKeyError("storage key must be non-empty")
    pure = Path(key)
    if pure.is_absolute():
        raise StorageKeyError(f"storage key must be relative: {key!r}")
    parts = pure.parts
    if ".." in parts:
        raise StorageKeyError(f"storage key must not contain '..': {key!r}")
    return pure


class LocalObjectStorage:
    """Stores objects as files under ``root``. The real, default driver."""

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        rel = _safe_relative(key)
        resolved = (self._root / rel).resolve()
        # Belt-and-braces: ensure the resolved path is inside the root.
        if os.path.commonpath([resolved, self._root]) != str(self._root):
            raise StorageKeyError(f"storage key escapes root: {key!r}")
        return resolved

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def put_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> int:
        """Stream to disk in 1 MiB chunks, capping at ``max_bytes`` (CR-028 / D28).

        Peak RAM is one chunk, not the whole file. Raises :class:`UploadTooLarge` the
        moment the running total exceeds the cap and removes the partial file, so an
        oversized upload never lands.
        """

        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with open(path, "wb") as out:
                while True:
                    chunk = stream.read(_STREAM_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise UploadTooLarge(
                            f"upload exceeds the {max_bytes}-byte limit"
                        )
                    out.write(chunk)
        except BaseException:
            # Never leave a partial/oversized object behind.
            if path.exists():
                path.unlink()
            raise
        return written

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def path_for(self, key: str) -> str | None:
        """The on-disk path of a stored object (CR-028), or ``None`` if it doesn't exist
        — so the worker can hand the model the file directly instead of re-reading bytes."""

        path = self._path(key)
        return str(path) if path.exists() else None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()
