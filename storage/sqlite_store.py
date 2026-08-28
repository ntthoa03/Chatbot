"""SQLite implementation TẠM của Storage cho demo H3-04.

TODO(Hieu/Postgres): thay class này bằng PostgresStorage cùng interface trong
``storage/base.py``. Không phụ thuộc ai_core để việc thay thế không đổi logic AI.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
import sqlite3
from typing import Any

from storage.base import Storage, StorageError, StorageValidationError


TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _require_tenant_id(tenant_id: object) -> str:
    """Fail-closed trước SQL; tenant thiếu/rỗng/sai không được query tất cả."""

    if not isinstance(tenant_id, str) or not TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise StorageValidationError(
            "tenant_id phải gồm chữ thường, số, '_' hoặc '-', dài 1-64 ký tự"
        )
    return tenant_id


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageValidationError(f"{field} phải là chuỗi không rỗng")
    return value.strip()


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise StorageValidationError(f"{field} phải là số nguyên >= 0")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise StorageValidationError(f"{field} phải là số nguyên >= 0") from exc
    if parsed < 0:
        raise StorageValidationError(f"{field} phải >= 0")
    return parsed


class SQLiteStore(Storage):
    """Bản lưu trữ demo tối thiểu, mọi read/write đều bắt buộc tenant_id."""

    def __init__(self, database: str | Path = "outputs/h3_04/demo.sqlite3") -> None:
        database_value = str(database)
        if database_value != ":memory:":
            path = Path(database_value)
            path.parent.mkdir(parents=True, exist_ok=True)
            database_value = str(path)
        try:
            # FastAPI/TestClient có thể tạo app và xử lý request ở hai thread khác nhau.
            # Bản demo vẫn dùng một connection và endpoint đồng bộ hóa thao tác theo lượt;
            # TODO(Hieu/Postgres): production thay bằng connection pool của Postgres.
            self._connection = sqlite3.connect(database_value, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(f"Không khởi tạo được SQLite storage: {exc}") from exc

    def upsert_tenant(self, tenant_id: str, name: str, config_version: int = 1) -> None:
        tenant_id = _require_tenant_id(tenant_id)
        name = _require_text(name, "name")
        if isinstance(config_version, bool) or not isinstance(config_version, int) or config_version < 1:
            raise StorageValidationError("config_version phải là số nguyên >= 1")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO tenants (tenant_id, name, config_version)
                VALUES (?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    name = excluded.name,
                    config_version = excluded.config_version,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (tenant_id, name, config_version),
            )

    def create_conversation(self, tenant_id: str, conversation_id: str) -> None:
        tenant_id = _require_tenant_id(tenant_id)
        conversation_id = _require_text(conversation_id, "conversation_id")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO conversations (conversation_id, tenant_id)
                VALUES (?, ?)
                ON CONFLICT(conversation_id) DO NOTHING
                """,
                (conversation_id, tenant_id),
            )
            owner = self._connection.execute(
                "SELECT tenant_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if owner is None or owner["tenant_id"] != tenant_id:
                raise StorageValidationError(
                    "conversation_id đã thuộc tenant khác; không được dùng chéo tenant"
                )

    def _require_owned_conversation(self, tenant_id: str, conversation_id: str) -> None:
        row = self._connection.execute(
            """
            SELECT 1 FROM conversations
            WHERE tenant_id = ? AND conversation_id = ?
            """,
            (tenant_id, conversation_id),
        ).fetchone()
        if row is None:
            raise StorageValidationError("Không tìm thấy conversation thuộc tenant đã truyền")

    def save_message(
        self,
        tenant_id: str,
        conversation_id: str,
        role: str,
        content: str,
        *,
        trace_id: str | None = None,
    ) -> int:
        tenant_id = _require_tenant_id(tenant_id)
        conversation_id = _require_text(conversation_id, "conversation_id")
        role = _require_text(role, "role")
        content = _require_text(content, "content")
        if role not in {"user", "assistant"}:
            raise StorageValidationError("role chỉ nhận 'user' hoặc 'assistant'")
        normalized_trace = _require_text(trace_id, "trace_id") if trace_id is not None else None
        with self._connection:
            self._require_owned_conversation(tenant_id, conversation_id)
            cursor = self._connection.execute(
                """
                INSERT INTO messages (tenant_id, conversation_id, role, content, trace_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tenant_id, conversation_id, role, content, normalized_trace),
            )
            self._touch_conversation(tenant_id, conversation_id)
        return int(cursor.lastrowid)

    def save_lead(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        name: str | None,
        phone: str | None,
        status: str = "new",
    ) -> int:
        tenant_id = _require_tenant_id(tenant_id)
        conversation_id = _require_text(conversation_id, "conversation_id")
        normalized_name = name.strip() if isinstance(name, str) and name.strip() else None
        normalized_phone = phone.strip() if isinstance(phone, str) and phone.strip() else None
        status = _require_text(status, "status")
        if normalized_name is None and normalized_phone is None:
            raise StorageValidationError("lead phải có ít nhất name hoặc phone")
        with self._connection:
            self._require_owned_conversation(tenant_id, conversation_id)
            cursor = self._connection.execute(
                """
                INSERT INTO leads (tenant_id, conversation_id, name, phone, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tenant_id, conversation_id, normalized_name, normalized_phone, status),
            )
        return int(cursor.lastrowid)

    def save_usage_event(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        trace_id: str,
        usage: Mapping[str, Any],
    ) -> int:
        tenant_id = _require_tenant_id(tenant_id)
        conversation_id = _require_text(conversation_id, "conversation_id")
        trace_id = _require_text(trace_id, "trace_id")
        if not isinstance(usage, Mapping):
            raise StorageValidationError("usage phải là mapping")
        model = _require_text(usage.get("model"), "usage.model")
        tokens_in = _non_negative_int(usage.get("tokens_in", 0), "usage.tokens_in")
        tokens_out = _non_negative_int(usage.get("tokens_out", 0), "usage.tokens_out")
        cached_tokens = _non_negative_int(
            usage.get("cached_tokens_in", 0), "usage.cached_tokens_in"
        )
        cache_write_tokens = _non_negative_int(
            usage.get("cache_write_tokens_in", 0), "usage.cache_write_tokens_in"
        )
        latency_ms = _non_negative_int(usage.get("latency_ms", 0), "usage.latency_ms")
        try:
            cost_usd = float(usage.get("cost_usd", 0.0))
        except (TypeError, ValueError) as exc:
            raise StorageValidationError("usage.cost_usd phải là số >= 0") from exc
        if cost_usd < 0:
            raise StorageValidationError("usage.cost_usd phải >= 0")
        with self._connection:
            self._require_owned_conversation(tenant_id, conversation_id)
            cursor = self._connection.execute(
                """
                INSERT INTO usage_events (
                    tenant_id, conversation_id, trace_id, model,
                    tokens_in, tokens_out, cached_tokens_in,
                    cache_write_tokens_in, cost_usd, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    conversation_id,
                    trace_id,
                    model,
                    tokens_in,
                    tokens_out,
                    cached_tokens,
                    cache_write_tokens,
                    cost_usd,
                    latency_ms,
                ),
            )
        return int(cursor.lastrowid)

    def _touch_conversation(self, tenant_id: str, conversation_id: str) -> None:
        self._connection.execute(
            """
            UPDATE conversations
            SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE tenant_id = ? AND conversation_id = ?
            """,
            (tenant_id, conversation_id),
        )

    def get_conversation(self, tenant_id: str, conversation_id: str) -> dict[str, Any] | None:
        tenant_id = _require_tenant_id(tenant_id)
        conversation_id = _require_text(conversation_id, "conversation_id")
        conversation = self._connection.execute(
            """
            SELECT conversation_id, tenant_id, status, created_at, updated_at
            FROM conversations
            WHERE tenant_id = ? AND conversation_id = ?
            """,
            (tenant_id, conversation_id),
        ).fetchone()
        if conversation is None:
            return None
        messages = self._connection.execute(
            """
            SELECT message_id, tenant_id, conversation_id, role, content, trace_id, created_at
            FROM messages
            WHERE tenant_id = ? AND conversation_id = ?
            ORDER BY message_id
            """,
            (tenant_id, conversation_id),
        ).fetchall()
        result = dict(conversation)
        result["messages"] = [dict(row) for row in messages]
        return result

    def list_leads(self, tenant_id: str, conversation_id: str | None = None) -> list[dict[str, Any]]:
        tenant_id = _require_tenant_id(tenant_id)
        parameters: list[str] = [tenant_id]
        condition = "tenant_id = ?"
        if conversation_id is not None:
            parameters.append(_require_text(conversation_id, "conversation_id"))
            condition += " AND conversation_id = ?"
        rows = self._connection.execute(
            f"""
            SELECT lead_id, tenant_id, conversation_id, name, phone, status, created_at
            FROM leads WHERE {condition} ORDER BY lead_id
            """,  # condition chỉ gồm hằng số code, mọi giá trị vẫn parameterized.
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]

    def list_usage_events(
        self, tenant_id: str, conversation_id: str | None = None
    ) -> list[dict[str, Any]]:
        tenant_id = _require_tenant_id(tenant_id)
        parameters: list[str] = [tenant_id]
        condition = "tenant_id = ?"
        if conversation_id is not None:
            parameters.append(_require_text(conversation_id, "conversation_id"))
            condition += " AND conversation_id = ?"
        rows = self._connection.execute(
            f"""
            SELECT usage_event_id, tenant_id, conversation_id, trace_id, model,
                   tokens_in, tokens_out, cached_tokens_in, cache_write_tokens_in,
                   cost_usd, latency_ms, created_at
            FROM usage_events WHERE {condition} ORDER BY usage_event_id
            """,  # condition chỉ gồm hằng số code, mọi giá trị vẫn parameterized.
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._connection.close()
