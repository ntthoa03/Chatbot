"""Điều phối model theo độ khó (H2-09) và yêu cầu chuyển người thật (HOA-14)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ModelRoute:
    """Kết quả routing H2-09; role ánh xạ sang model trong config tenant."""

    model_role: Literal["primary", "fallback"]
    complexity: Literal["simple", "complex"]
    reason: str


_COMPLEX_PATTERNS = (
    r"\b(?:so sanh|khac nhau|hon kem|uu nhuoc|phan tich|danh gia)\b",
    r"\b(?:nen chon|chon goi nao|goi nao phu hop|tu van theo|de xuat|khuyen nghi)\b",
    r"\b(?:compare|comparison|recommend|recommendation|trade[ -]?off|pros and cons|analy[sz]e)\b",
    r"\b(?:chien luoc|lo trinh|phuong an|giai phap)\b",
)
_SIMPLE_PATTERNS = (
    r"\b(?:gia|bang gia|bao gia|bao nhieu|chi phi|price|cost)\b",
    r"\b(?:bao lau|thoi gian|may ngay|may thang|duration|timeline)\b",
    r"\b(?:lien he|hotline|zalo|dia chi|email|contact|phone)\b",
    r"\b(?:co|kem|bao gom|ho tro)\b.{0,45}\b(?:ssl|song ngu|gio hang|source code|hosting|ten mien)\b",
    r"\b(?:la gi|co lam|co nhan|nhung gi|hang muc nao|quy trinh|may buoc|o dau|van phong)\b",
    r"\b(?:check|kiem tra|tra giup|con mua|dang ky|uptime|cong nghe|kenh nao)\b",
    r"\b(?:backlink|entity|schema|traffic|bao mat|cap nhat)\b",
)
_CONSTRAINT_PATTERNS = (
    r"\b(?:ngan sach|tai chinh|budget|duoi|tren|toi da|khong qua)\b",
    r"\b(?:can|muon|yeu cau|must|need|want)\b",
    r"\b(?:gio hang|song ngu|ssl|seo|mobile|tablet|source code)\b",
)


def _normalize(message: str) -> str:
    normalized = message.casefold().replace("đ", "d")
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFD", normalized)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def decide_model_route(message: str) -> ModelRoute:
    """Dùng heuristic $0 để câu tra cứu dùng model rẻ, câu suy luận dùng model mạnh.

    Mặc định fail-safe là model mạnh. Chỉ những intent tra cứu đơn giản được
    hạ xuống primary; nhờ vậy tenant mới không cần danh sách tên gói hardcode.
    """

    normalized = _normalize(message)
    if any(re.search(pattern, normalized) for pattern in _COMPLEX_PATTERNS):
        return ModelRoute("fallback", "complex", "reasoning_or_comparison")

    constraint_groups = sum(
        bool(re.search(pattern, normalized)) for pattern in _CONSTRAINT_PATTERNS
    )
    if constraint_groups >= 2:
        return ModelRoute("fallback", "complex", "multiple_customer_constraints")

    if any(re.search(pattern, normalized) for pattern in _SIMPLE_PATTERNS):
        return ModelRoute("primary", "simple", "direct_lookup")

    return ModelRoute("fallback", "complex", "safe_default")


def is_pricing_catalogue_query(message: str) -> bool:
    """Nhận diện câu hỏi giá tổng quát cần tập hợp nhiều chunk pricing."""

    normalized = _normalize(message)
    has_price_intent = bool(
        re.search(r"\b(?:gia|bang gia|bao gia|chi phi|price|cost)\b", normalized)
    )
    # Khách thật thường bỏ hẳn chữ "giá/ngân sách": "tư vấn dịch vụ 15-30tr".
    # Đây vẫn là yêu cầu lọc catalogue theo khoảng tiền, không phải lookup một gói.
    has_service_budget_band = bool(
        re.search(
            r"\b(?:dich vu|cac goi|goi nao|lua chon|tu van)\b.{0,60}"
            # _normalize() đổi dấu gạch của "15-30tr" thành khoảng trắng.
            r"\b\d+(?:[.,]\d+)?\s+(?:(?:den|toi|to)\s+)?"
            r"\d+(?:[.,]\d+)?\s*(?:trieu|tr|m|cu|chai|k|vnd|d)\b",
            normalized,
        )
    )
    # "gói Basic giá bao nhiêu" là lookup một gói; "các gói/gói nào" vẫn là catalogue.
    names_one_package = bool(
        re.search(r"\bgoi\s+(?!(?:nao|gi|dich vu|web|website)\b)[a-z0-9]+", normalized)
    )
    return (has_price_intent or has_service_budget_band) and not names_one_package


def decide_need_human(message: str, consecutive_misses: int = 0) -> bool:
    """Route explicit requests, contracts/complaints, or two consecutive misses."""

    normalized = _normalize(message)
    asks_for_human = re.search(
        r"\b(?:gap|noi chuyen voi|chuyen cho|ket noi voi)\s+"
        r"(?:nguoi that|nhan vien|tu van vien|chuyen vien|quan ly)\b",
        normalized,
    )
    # Khách thường xác nhận rất ngắn sau khi bot đề nghị chuyển Sale.
    short_handoff = re.fullmatch(
        r"(?:(?:ok|dong y)\s+)?(?:ket noi|chuyen)(?:\s+(?:di|nhe|ngay|luon))?",
        normalized,
    )
    sensitive_business = re.search(r"\b(?:hop dong|khieu nai|khieu kien)\b", normalized)
    return bool(asks_for_human or short_handoff or sensitive_business or consecutive_misses >= 2)
