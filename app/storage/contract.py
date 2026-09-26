"""The ``ObjectStorage`` contract (M4.1)."""

from __future__ import annotations

from typing import BinaryIO, Protocol, runtime_checkable


class StorageKeyError(ValueError):
    """An unsafe or malformed storage key (e.g. one containing ``..``)."""


class UploadTooLarge(Exception):
    """A streamed upload exceeded ``max_bytes`` mid-write (CR-028 / D28 → 413).

    Raised by :meth:`ObjectStorage.put_stream` the moment the running byte count crosses
    the cap, so an oversized upload never fully lands in memory or on disk."""


@runtime_checkable
class ObjectStorage(Protocol):
    """Put/get/delete bytes by key. Keys are ``/``-separated logical paths.

    Implementations MUST refuse a key containing ``..`` or an absolute path
    (:class:`StorageKeyError`) so a key can never escape the storage root.
    """

    def put(self, key: str, data: bytes) -> None: ...

    def put_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> int:
        """Stream ``stream`` into storage in bounded chunks, capping at ``max_bytes``
        (CR-028 / D28). Returns the number of bytes written. Raises
        :class:`UploadTooLarge` as soon as the running total exceeds ``max_bytes`` — so
        an oversized upload is never fully buffered (the fix for reading a 5 GB file into
        RAM before the size check). Leaves no object under ``key`` on rejection.
        """
        ...

    def get(self, key: str) -> bytes: ...

    def path_for(self, key: str) -> str | None:
        """Return the on-disk path of a stored object, or ``None`` if this driver has no
        filesystem path (CR-028 / D28). The worker uses it to hand the model the file
        **directly** instead of reading the bytes back into RAM. A ``None`` return means
        the caller must fall back to :meth:`get`.
        """
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None:
        """Delete the key. Deleting a missing key is a no-op (idempotent)."""
        ...
