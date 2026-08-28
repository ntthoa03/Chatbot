"""Interface lưu trữ độc lập với SQLite/Postgres.

TODO(Hieu/Postgres): triển khai class mới theo đúng interface này rồi thay object
được inject vào API; không đưa SQL/ORM vào ai_core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any


class StorageError(RuntimeError):
    """Lỗi thao tác storage đã được chuẩn hóa cho tầng API."""


class StorageValidationError(ValueError):
    """Dữ liệu gọi storage thiếu hoặc sai contract."""


class Storage(ABC):
    """Contract tối thiểu H3-04 mà SQLite và Postgres cùng phải giữ."""

    @abstractmethod
    def upsert_tenant(self, tenant_id: str, name: str, config_version: int = 1) -> None:
        """Tạo tenant hoặc cập nhật tên/config version của tenant đã có."""

    @abstractmethod
    def create_conversation(self, tenant_id: str, conversation_id: str) -> None:
        """Tạo hội thoại thuộc đúng tenant; gọi lại cùng ID phải an toàn."""

    @abstractmethod
    def save_message(
        self,
        tenant_id: str,
        conversation_id: str,
        role: str,
        content: str,
        *,
        trace_id: str | None = None,
    ) -> int:
        """Lưu một message user/assistant và trả message_id."""

    @abstractmethod
    def save_lead(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        name: str | None,
        phone: str | None,
        status: str = "new",
    ) -> int:
        """Lưu lead đã được flow chat xác nhận và trả lead_id."""

    @abstractmethod
    def save_usage_event(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        trace_id: str,
        usage: Mapping[str, Any],
    ) -> int:
        """Lưu token, chi phí USD, model và latency của một lượt chat."""

    @abstractmethod
    def get_conversation(self, tenant_id: str, conversation_id: str) -> dict[str, Any] | None:
        """Đọc hội thoại kèm messages; không được trả dữ liệu tenant khác."""

    @abstractmethod
    def list_leads(self, tenant_id: str, conversation_id: str | None = None) -> list[dict[str, Any]]:
        """Đọc lead đã lọc tenant và tùy chọn conversation."""

    @abstractmethod
    def list_usage_events(
        self, tenant_id: str, conversation_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Đọc usage đã lọc tenant và tùy chọn conversation."""

    @abstractmethod
    def close(self) -> None:
        """Giải phóng connection/resource của implementation."""

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
