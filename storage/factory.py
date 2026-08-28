"""Chọn Storage implementation tại composition root của ứng dụng.

API chỉ gọi ``build_storage()`` và không cần biết SQL/driver cụ thể. Vì vậy khi
Hiếu thay SQLite bằng Postgres, logic ``/chat`` và ``ai_core`` không đổi.
"""

from __future__ import annotations

import os
from pathlib import Path

from storage.base import Storage, StorageError
from storage.sqlite_store import SQLiteStore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_BACKEND_ENV = "AI_API_STORAGE_BACKEND"
SQLITE_PATH_ENV = "AI_API_SQLITE_PATH"
POSTGRES_DSN_ENV = "AI_API_POSTGRES_DSN"
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "outputs" / "h3_05" / "chat.sqlite3"


class StorageConfigurationError(StorageError):
    """Backend storage hoặc connection string chưa được cấu hình đúng."""


def build_storage(
    *,
    backend: str | None = None,
    sqlite_path: str | Path | None = None,
) -> Storage:
    """Tạo Storage theo một biến config, mặc định SQLite cho flow demo.

    TODO(Hieu/Postgres): tạo ``storage/postgres_store.py`` với class
    ``PostgresStore(Storage)`` nhận DSN ở constructor. Nhánh import bên dưới sẽ
    tự dùng implementation đó; chỉ đổi ``AI_API_STORAGE_BACKEND=postgres`` và
    đặt ``AI_API_POSTGRES_DSN``, không sửa ``api/main.py``.
    """

    selected = (backend or os.getenv(STORAGE_BACKEND_ENV, "sqlite")).strip().casefold()
    if selected == "sqlite":
        database_path = sqlite_path or os.getenv(SQLITE_PATH_ENV) or DEFAULT_SQLITE_PATH
        return SQLiteStore(database_path)

    if selected == "postgres":
        dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
        if not dsn:
            raise StorageConfigurationError(f"{POSTGRES_DSN_ENV} is required for postgres")
        try:
            # Import trễ: bản demo không cần cài driver Postgres.
            # TODO(Hieu/Postgres): file/class này là điểm duy nhất cần bổ sung.
            from storage.postgres_store import PostgresStore
        except ImportError as exc:
            raise StorageConfigurationError(
                "PostgresStore is not implemented; add storage/postgres_store.py"
            ) from exc
        implementation = PostgresStore(dsn)
        if not isinstance(implementation, Storage):
            raise StorageConfigurationError("PostgresStore must implement Storage")
        return implementation

    raise StorageConfigurationError(
        f"unsupported storage backend {selected!r}; use 'sqlite' or 'postgres'"
    )


__all__ = [
    "POSTGRES_DSN_ENV",
    "SQLITE_PATH_ENV",
    "STORAGE_BACKEND_ENV",
    "StorageConfigurationError",
    "build_storage",
]
