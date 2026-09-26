"""Config-selected storage registry (M4.1).

Local-disk is the default. The fake is only reachable when explicitly configured,
and config validation forbids it in production.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, StorageDriver, get_settings
from app.storage.contract import ObjectStorage


def build_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_driver == StorageDriver.local:
        from app.storage.local import LocalObjectStorage

        return LocalObjectStorage(root=f"{settings.data_dir}/objects")

    if settings.storage_driver == StorageDriver.fake:
        from app.storage.fake import FakeObjectStorage

        return FakeObjectStorage()

    raise ValueError(f"Unknown storage driver: {settings.storage_driver!r}")


@lru_cache
def get_storage() -> ObjectStorage:
    return build_storage(get_settings())


def reset_storage_cache() -> None:
    get_storage.cache_clear()
