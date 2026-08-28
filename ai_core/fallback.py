"""Fallback hữu ích nhưng không bịa khi retrieval không có chunk đạt ngưỡng."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence

from ai_core.guardrail.pricing import currency_mentions, customer_budget_amounts


MAX_SUGGESTIONS = 3
MAX_TITLE_CHARS = 100


def _clean_title(value: object) -> str:
    if not isinstance(value, str):
        return ""
    title = unicodedata.normalize("NFKC", value)
    # Title là dữ liệu crawl không đáng tin cậy: bỏ control/markdown và giới hạn độ dài.
    title = re.sub(r"[\x00-\x1f\x7f`#*_<>\[\]{}]", " ", title)
    title = " ".join(title.split()).strip(" .,:;!?-|/")
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    return title


def _dedupe_key(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def build_suggestions(
    candidates: Sequence[dict[str, Any]],
    *,
    limit: int = MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Tạo tối đa ba câu hỏi chỉ từ title của candidate đã truy xuất."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 3:
        raise ValueError("limit gợi ý phải là 2 hoặc 3.")
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        metadata = candidate.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        title = _clean_title(metadata.get("title"))
        key = _dedupe_key(title)
        if not title or not key or key in seen:
            continue
        seen.add(key)
        suggestions.append(
            {
                "question": f"Anh/chị có muốn tìm hiểu về “{title}” không?",
                "chunk_id": candidate.get("chunk_id"),
                "title": title,
                "url": candidate.get("url") or metadata.get("url"),
                "score": candidate.get("score"),
                # Content chỉ dùng làm evidence cho output guardrail, không đưa vào reply.
                "content": str(candidate.get("content", "")),
            }
        )
        if len(suggestions) >= limit:
            break
    return suggestions


def build_helpful_fallback(
    *,
    no_data_message: str,
    handoff_message: str,
    suggestions: Sequence[dict[str, Any]],
) -> str:
    """Ghép thông báo chưa biết → gợi ý có evidence → đề nghị người thật."""

    intro = no_data_message.strip()
    handoff = handoff_message.strip()
    if not suggestions:
        return f"{intro}\n\n{handoff}".strip()
    bullets = "\n".join(f"- {item['question']}" for item in suggestions[:MAX_SUGGESTIONS])
    return (
        f"{intro}\n\n"
        "Trong lúc chờ thêm thông tin, em có thể hỗ trợ các nội dung gần nhất sau:\n"
        f"{bullets}\n\n"
        "Nếu anh/chị vẫn cần đúng nội dung ban đầu, em xin phép hỗ trợ chuyển người phụ trách. "
        f"{handoff}"
    )


def build_budget_catalogue_fallback(
    *,
    question: str,
    sources: Sequence[dict[str, Any]],
) -> str | None:
    """Lọc gói theo khoảng tiền chỉ từ giá có thật trong các chunk RAG.

    Một mức tiền được hiểu là ngân sách tối đa (0 → mức khách nêu); hai mức tiền
    được hiểu là khoảng min → max. Không có giá phù hợp thì trả ``None`` để luồng
    gọi tiếp tục dùng fallback an toàn hiện có.
    """

    budgets = sorted(customer_budget_amounts([question]))
    if not budgets:
        return None
    single_maximum = len(budgets) == 1
    minimum = 0 if single_maximum else budgets[0]
    maximum = budgets[-1]

    matches: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for source in sources:
        content = str(source.get("content", "")).strip()
        metadata = source.get("metadata")
        if not isinstance(metadata, dict):
            metadata = source.get("source") if isinstance(source.get("source"), dict) else {}
        title = _clean_title(metadata.get("title"))
        if not title or not content:
            continue
        for _, _, amount in currency_mentions(content):
            key = (_dedupe_key(title), amount)
            if minimum <= amount <= maximum and key not in seen:
                seen.add(key)
                matches.append((title, amount))

    if not matches:
        return None
    matches.sort(key=lambda item: (item[1], item[0].casefold()))

    def format_vnd(amount: int) -> str:
        return f"{amount:,}".replace(",", ".") + "đ"

    bullets = "\n".join(
        f"- {title}: {format_vnd(amount)}." for title, amount in matches
    )
    budget_description = (
        f"ngân sách tối đa {format_vnd(maximum)}"
        if single_maximum
        else f"khoảng ngân sách {format_vnd(minimum)}–{format_vnd(maximum)}"
    )
    return (
        f"Dạ, với {budget_description}, dữ liệu hiện có cho thấy các lựa chọn "
        "có giá phù hợp gồm:\n"
        f"{bullets}\n\n"
        "Anh/chị muốn em mô tả chi tiết lựa chọn nào ạ?"
    )


def build_repeated_question_handoff(handoff_message: str) -> str:
    """Trả lời tự nhiên khi khách lặp cùng câu lần ba nhưng vẫn giữ chặn spam."""

    handoff = handoff_message.strip()
    return (
        "Em đã ghi nhận anh/chị đang hỏi lại nội dung này. "
        "Hiện em vẫn chưa có thêm dữ liệu để trả lời chính xác ạ.\n\n"
        f"{handoff}"
    )
