"""Storage boundary tạm cho H3-04; production sẽ thay implementation SQLite."""

from storage.base import Storage, StorageError, StorageValidationError
from storage.factory import StorageConfigurationError, build_storage
from storage.sqlite_store import SQLiteStore

__all__ = [
    "SQLiteStore",
    "Storage",
    "StorageConfigurationError",
    "StorageError",
    "StorageValidationError",
    "build_storage",
]
