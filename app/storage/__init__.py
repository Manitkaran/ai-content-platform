"""The object-storage seam (M4.1, D21).

Uploaded media and produced artifacts go through the ``ObjectStorage`` contract.
The **local-disk driver is the default** (never a silent in-memory fake in
production — the CR-048 lesson); the fake is test-only and refused in production
by config validation.
"""

from app.storage.contract import ObjectStorage, StorageKeyError
from app.storage.registry import get_storage

__all__ = ["ObjectStorage", "StorageKeyError", "get_storage"]
