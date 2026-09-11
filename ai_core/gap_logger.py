"""Ghi nhận lỗ hổng tri thức mà không phụ thuộc database hay HTTP.

Mỗi gap luôn được append vào JSONL để các lượt chạy CLI/eval không mất dữ
liệu.  Tầng composition (ví dụ API) có thể dùng ``capture_knowledge_gaps`` để
nhận cùng sự kiện và lưu vào storage production sau khi lượt chat hoàn tất.
"""

from __future__ import annotations

import json
import os
import threading
import unicodedata
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Literal

from ai_core.trace import redact_sensitive_data


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KNOWLEDGE_GAP_PATH = PROJECT_ROOT / "outputs" / "knowledge_gaps.jsonl"
KNOWLEDGE_GAP_SCHEMA_VERSION = "knowledge-gap.v1"
KnowledgeGapReason = Literal[
    "below_threshold",
    "no_match",
    "fallback_response",
    "retrieval_error",
]

_WRITE_LOCK = threading.Lock()
_CAPTURED_GAPS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "captured_knowledge_gaps",
    default=None,
)

_MISSING_KNOWLEDGE_PHRASES = (
    "chua co du du lieu",
    "khong co du du lieu",
    "chua co thong tin",
    "khong co thong tin",
    "khong tim thay thong tin",
    "chua the xac nhan",
)


def reply_indicates_missing_knowledge(reply: str) -> bool:
    """Nhận diện bot tự nói thiếu thông tin, kể cả khi không dùng fallback mẫu."""

    decomposed = unicodedata.normalize("NFD", str(reply).casefold())
    normalized = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")
    normalized = " ".join(normalized.split())
    return any(phrase in normalized for phrase in _MISSING_KNOWLEDGE_PHRASES)


def knowledge_gap_path() -> Path:
    configured = os.getenv("AI_CORE_KNOWLEDGE_GAP_PATH")
    if not configured:
        return DEFAULT_KNOWLEDGE_GAP_PATH
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


@contextmanager
def capture_knowledge_gaps() -> Iterator[list[dict[str, Any]]]:
    """Thu các gap của đúng context hiện tại để adapter lưu vào database."""

    captured: list[dict[str, Any]] = []
    token = _CAPTURED_GAPS.set(captured)
    try:
        yield captured
    finally:
        _CAPTURED_GAPS.reset(token)


def log_knowledge_gap(
    *,
    question: str,
    tenant_id: str,
    conversation_id: str,
    trace_id: str,
    top_score: float | None,
    threshold: float,
    reason: KnowledgeGapReason,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Ghi một gap; lỗi file không được làm hỏng câu trả lời cho khách."""

    # Chỉ nội dung tự do do khách nhập mới cần che PII. UUID chứa nhiều chữ số và
    # dấu gạch nên nếu đưa cả record qua bộ lọc số thẻ, định danh correlation có
    # thể bị che nhầm và không còn join được với conversation/trace.
    record = {
        "schema_version": KNOWLEDGE_GAP_SCHEMA_VERSION,
        "question": redact_sensitive_data(question),
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        "top_score": top_score,
        "threshold": threshold,
        "reason": reason,
        "occurred_at": occurred_at or datetime.now(UTC).isoformat(),
    }
    captured = _CAPTURED_GAPS.get()
    if captured is not None:
        captured.append(dict(record))

    destination = knowledge_gap_path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _WRITE_LOCK, destination.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
    except OSError as exc:
        warnings.warn(
            f"Không thể ghi knowledge gap tại {destination}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
    return record


__all__ = [
    "DEFAULT_KNOWLEDGE_GAP_PATH",
    "KNOWLEDGE_GAP_SCHEMA_VERSION",
    "KnowledgeGapReason",
    "capture_knowledge_gaps",
    "knowledge_gap_path",
    "log_knowledge_gap",
    "reply_indicates_missing_knowledge",
]
