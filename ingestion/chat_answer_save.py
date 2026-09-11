"""H4-14 helpers for generalizing a chat answer before authoritative ingestion."""

from __future__ import annotations

import re
from typing import Any, Callable

from ingestion.chat_log_importer import redact_pii
from ingestion.tenant_answer_ingestion import ingest_tenant_answer


class ChatAnswerSaveError(ValueError):
    pass


_CONTEXT_SENTENCE = re.compile(
    r"(?:^\s*(?:xin\s+)?chào\b|anh/chị có muốn|anh có muốn|chị có muốn|"
    r"cho em xin (?:tên|số)|em sẽ (?:gọi|liên hệ)|mã hội thoại|đơn hàng của (?:anh|chị))",
    flags=re.IGNORECASE,
)
_PERSONAL_NAME = re.compile(
    r"\b(?:anh|chị|cô|chú|khách)\s+[A-ZÀ-ỸĐ][a-zà-ỹđ]+(?:\s+[A-ZÀ-ỸĐ][a-zà-ỹđ]+){0,3}\b"
)


def _without_context_sentences(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept = [item.strip() for item in sentences if item.strip() and not _CONTEXT_SENTENCE.search(item)]
    return " ".join(kept).strip()


def generalize_chat_pair(question: str, answer: str) -> tuple[str, str]:
    safe_question, _ = redact_pii(question)
    safe_answer, _ = redact_pii(answer)
    safe_question = re.sub(r"\[REDACTED_(?:NAME|PHONE|EMAIL)\]", "khách hàng", safe_question)
    safe_answer = re.sub(r"\[REDACTED_(?:NAME|PHONE|EMAIL)\]", "", safe_answer)
    safe_question = re.sub(
        r"^\s*(?:tôi|em|mình)\s+là\s+khách hàng\s*[,;:-]*\s*(?:muốn\s+hỏi\s*)?",
        "",
        safe_question,
        flags=re.IGNORECASE,
    )
    safe_question = " ".join(safe_question.split()).strip(" ,;:-")
    safe_answer = _without_context_sentences(safe_answer)
    safe_answer = " ".join(safe_answer.split()).strip()
    return safe_question or "Câu hỏi tổng quát cần bổ sung", safe_answer or "Cần nhập câu trả lời tổng quát"


def validate_generalized_pair(question: str, answer: str, *, confirmed: bool) -> tuple[str, str]:
    clean_question = " ".join(question.split()).strip()
    clean_answer = " ".join(answer.split()).strip()
    if not confirmed:
        raise ChatAnswerSaveError("Cần xác nhận đã loại bỏ ngữ cảnh riêng trước khi lưu.")
    if not clean_question or not clean_answer:
        raise ChatAnswerSaveError("Câu hỏi và câu trả lời tổng quát không được để trống.")
    redacted_question, q_stats = redact_pii(clean_question)
    redacted_answer, a_stats = redact_pii(clean_answer)
    if q_stats.phones + q_stats.emails + q_stats.names + a_stats.phones + a_stats.emails + a_stats.names:
        raise ChatAnswerSaveError("Còn số điện thoại, email hoặc tên khách trong nội dung.")
    if redacted_question != clean_question or redacted_answer != clean_answer:
        raise ChatAnswerSaveError("Nội dung còn dữ liệu cá nhân.")
    if "[REDACTED_" in clean_question or "[REDACTED_" in clean_answer:
        raise ChatAnswerSaveError("Hãy viết lại phần đã che thành nội dung tổng quát.")
    if _PERSONAL_NAME.search(clean_question) or _PERSONAL_NAME.search(clean_answer):
        raise ChatAnswerSaveError("Nội dung có vẻ còn tên/ngữ cảnh của một khách cụ thể.")
    return clean_question, clean_answer


def save_generalized_chat_answer(
    tenant_id: str,
    question: str,
    answer: str,
    *,
    confirmed: bool,
    ingest_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_question, clean_answer = validate_generalized_pair(
        question, answer, confirmed=confirmed
    )
    selected_ingest = ingest_fn or ingest_tenant_answer
    return selected_ingest(tenant_id, clean_question, clean_answer)


__all__ = [
    "ChatAnswerSaveError", "generalize_chat_pair", "save_generalized_chat_answer",
    "validate_generalized_pair",
]
