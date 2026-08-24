"""Nhận diện tiền và ngân sách dùng chung cho mọi tenant.

Module này chỉ phân tích cách viết số tiền và ngữ cảnh chi tiêu. Nó không biết
tên tenant, tên gói, giá bán hay dịch vụ nào được phép báo giá. Quyền báo giá
vẫn do config tenant quyết định; giá cụ thể vẫn phải được kiểm chứng bằng RAG
hoặc tool trong tầng kiểm duyệt output.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence


# Giá VND xuất hiện trong reply của bot. Đây là tập hẹp dùng để grounding giá.
AMOUNT_PATTERN = re.compile(
    r"(?P<number>\d+(?:[., ]\d+)*)\s*(?P<unit>trieu|tr|nghin|k|vnd|dong|d)\b"
)
NON_VND_PATTERN = re.compile(
    r"(?:\$\s*\d|\b\d+(?:[.,]\d+)?\s*(?:usd|eur|gbp|jpy|cny)\b)"
)

# Ngân sách khách có thể được nhập không dấu, viết tắt hoặc không kèm đơn vị.
BUDGET_AMOUNT_PATTERN = re.compile(
    r"(?P<number>\d+(?:[., ]\d+)*)\s*"
    r"(?P<unit>trieu|tr|m|mil|million|cu|chai|nghin|ngan|k|thousand|vnd|dong|d)\b"
    r"|(?P<bare>\d{6,12}|\d{1,3}(?:[., ]\d{3}){1,3})"
)
# Khoảng ngân sách khách thường gõ rút gọn đơn vị ở cuối: "15-30tr", "5 đến 10 triệu".
# Phải lấy cả hai đầu mút; nếu chỉ lấy 30tr thì lúc bot nhắc lại khoảng tiền sẽ bị hiểu
# nhầm thành một giá dịch vụ do bot tự đặt.
BUDGET_RANGE_PATTERN = re.compile(
    r"(?P<first>\d+(?:[.,]\d+)?)\s*"
    r"(?P<first_unit>trieu|tr|m|mil|million|cu|chai|nghin|ngan|k|thousand|vnd|dong|d)?\s*"
    r"(?:-|–|—|den|toi|to)\s*"
    r"(?P<second>\d+(?:[.,]\d+)?)\s*"
    r"(?P<second_unit>trieu|tr|m|mil|million|cu|chai|nghin|ngan|k|thousand|vnd|dong|d)\b"
)
BUDGET_MARKER_PATTERN = re.compile(
    r"\b(?:ngan sach|ns|tai chinh|so tien|tam gia|muc gia|muc chi|khoan chi|"
    r"du kien chi|kha nang chi|budget|bud|bdg|price range|spending limit|"
    r"spend|afford|max budget)\b"
)
COMPARISON_BEFORE_PATTERN = re.compile(
    r"(?:[<>]=?|\b(?:duoi|tren|toi da|toi thieu|it hon|nhieu hon|khong qua|"
    r"khoang|tam|tu|den|under|below|less than|over|above|more than|up to|"
    r"at most|at least|max(?:imum)?|min(?:imum)?|around|about|between|from|to))\s*$"
)
COMPARISON_AFTER_PATTERN = re.compile(
    r"^\s*(?:tro xuong|tro len|do lai|or less|or more|and below|and under|maximum|minimum)\b"
)

# Danh từ chung cho hạng mục được định giá; không chứa tên ngành hay tenant.
PRICED_ITEM_PATTERN = re.compile(
    r"\b(?:goi|dich vu|san pham|lieu trinh|package|plan|service|product|"
    r"treatment|procedure|tour|course|subscription)\b"
)


def normalize_money_text(text: str) -> str:
    """Chuẩn hoá chữ hoa, dấu tiếng Việt và khoảng trắng trước khi so pattern."""

    text = text.casefold().replace("đ", "d")
    text = "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", text).strip()


def _parse_vnd_amount(number: str, unit: str) -> int:
    if unit in {"trieu", "tr"}:
        compact = number.replace(" ", "")
        if re.fullmatch(r"\d+[.,]\d{1,2}", compact):
            return round(float(compact.replace(",", ".")) * 1_000_000)
        return int(re.sub(r"\D", "", compact)) * 1_000_000
    digits = int(re.sub(r"\D", "", number))
    return digits * 1_000 if unit in {"nghin", "k"} else digits


def currency_mentions(text: str) -> list[tuple[int, int, int]]:
    """Trả span và giá trị chuẩn hoá của mọi số tiền VND trong văn bản."""

    normalized = normalize_money_text(text)
    return [
        (match.start(), match.end(), _parse_vnd_amount(match.group("number"), match.group("unit")))
        for match in AMOUNT_PATTERN.finditer(normalized)
    ]


def currency_amounts(text: str) -> set[int]:
    return {amount for _, _, amount in currency_mentions(text)}


def has_non_vnd_amount(text: str) -> bool:
    return bool(NON_VND_PATTERN.search(normalize_money_text(text)))


def _parse_budget_amount(match: re.Match[str]) -> int:
    bare = match.group("bare")
    if bare is not None:
        return int(re.sub(r"\D", "", bare))
    number = match.group("number")
    unit = match.group("unit")
    if unit in {"trieu", "tr", "m", "mil", "million", "cu", "chai"}:
        compact = number.replace(" ", "")
        if re.fullmatch(r"\d+[.,]\d{1,2}", compact):
            return round(float(compact.replace(",", ".")) * 1_000_000)
        return int(re.sub(r"\D", "", compact)) * 1_000_000
    digits = int(re.sub(r"\D", "", number))
    return digits * 1_000 if unit in {"nghin", "ngan", "k", "thousand"} else digits


def _parse_budget_number(number: str, unit: str) -> int:
    """Chuẩn hoá một đầu mút của khoảng ngân sách về VND."""

    if unit in {"trieu", "tr", "m", "mil", "million", "cu", "chai"}:
        compact = number.replace(" ", "")
        if re.fullmatch(r"\d+[.,]\d{1,2}", compact):
            return round(float(compact.replace(",", ".")) * 1_000_000)
        return int(re.sub(r"\D", "", compact)) * 1_000_000
    digits = int(re.sub(r"\D", "", number))
    return digits * 1_000 if unit in {"nghin", "ngan", "k", "thousand"} else digits


def _looks_like_service_price(text: str, amount_start: int, amount_end: int) -> bool:
    """Không biến giá một hạng mục do khách tự gõ thành evidence đáng tin cậy."""

    before = text[max(0, amount_start - 90):amount_start]
    after = text[amount_end:min(len(text), amount_end + 80)]
    priced_match = re.search(
        r"\b(?:goi|dich vu|package|plan)\s+"
        r"(?!nao\b|phu hop\b|trong\b|duoi\b|tren\b|tam\b)"
        r"(?P<label>[a-z0-9_-]+(?:\s+[a-z0-9_-]+){0,5})\s+"
        r"(?:co\s+)?(?:gia|price|costs?|priced(?:\s+at)?)"
        r"(?:\s+(?:duoi|tren|under|below|over|above|around|about|[<>]=?))?\s*$",
        before,
    )
    # "các gói website với mức giá dưới 15tr" là một bộ lọc ngân sách,
    # không phải phát biểu gán 15tr cho một gói cụ thể. Ngược lại,
    # "gói Basic giá 2tr" vẫn phải được xem là giá dịch vụ và đối chiếu RAG.
    generic_label_words = {"cac", "nhung", "voi", "muc", "tam", "khoang", "range"}
    priced_before = bool(
        priced_match
        and not (set(priced_match.group("label").split()) & generic_label_words)
    )
    assigned_after = bool(
        re.search(
            r"^\s*(?:cho|cua|for)\s+(?:goi|dich vu|package|plan)\s+"
            r"(?!nao\b|phu hop\b|trong\b)[a-z0-9_-]+",
            after,
        )
    )
    return priced_before or assigned_after


def is_budget_context(text: str, amount_start: int, amount_end: int) -> bool:
    """Cho biết số tiền tại span đang là giới hạn chi tiêu hay giá hạng mục."""

    normalized = normalize_money_text(text)
    if _looks_like_service_price(normalized, amount_start, amount_end):
        return False

    before = normalized[max(0, amount_start - 90):amount_start]
    after = normalized[amount_end:min(len(normalized), amount_end + 35)]
    markers = list(BUDGET_MARKER_PATTERN.finditer(before))
    if markers:
        gap = before[markers[-1].end():]
        # Cho phép cả hai đầu mút trong "ngân sách 15–30tr". Trường hợp gán
        # giá cho một gói cụ thể đã bị _looks_like_service_price() loại phía trên.
        prior_amounts = list(BUDGET_AMOUNT_PATTERN.finditer(gap))
        if len(gap) <= 65 and len(prior_amounts) <= 1:
            return True
    marker_after = BUDGET_MARKER_PATTERN.search(after)
    if marker_after and marker_after.start() <= 20:
        return True
    return bool(
        COMPARISON_BEFORE_PATTERN.search(before[-35:])
        or COMPARISON_AFTER_PATTERN.search(after)
    )


def customer_budget_amounts(items: Sequence[str]) -> set[int]:
    """Lấy các mức ngân sách khách đã tự cung cấp qua nhiều kiểu chat."""

    amounts: set[int] = set()
    for item in items:
        normalized = normalize_money_text(item)
        for match in BUDGET_RANGE_PATTERN.finditer(normalized):
            before = normalized[max(0, match.start() - 70):match.start()]
            generic_service_range = bool(
                re.search(r"\b(?:dich vu|cac goi|goi nao|lua chon|tu van)\b", before)
            )
            if not (
                is_budget_context(normalized, match.start(), match.end())
                or generic_service_range
            ):
                continue
            second_unit = match.group("second_unit")
            first_unit = match.group("first_unit") or second_unit
            amounts.add(_parse_budget_number(match.group("first"), first_unit))
            amounts.add(_parse_budget_number(match.group("second"), second_unit))
        for match in BUDGET_AMOUNT_PATTERN.finditer(normalized):
            if is_budget_context(normalized, match.start(), match.end()):
                amounts.add(_parse_budget_amount(match))
    return amounts


def contains_priced_item(text: str) -> bool:
    """Nhận diện danh từ chung của một hạng mục có thể mang giá."""

    return bool(PRICED_ITEM_PATTERN.search(normalize_money_text(text)))
