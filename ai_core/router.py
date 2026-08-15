"""Điều phối các yêu cầu phải chuyển cho người thật (HOA-14)."""

from __future__ import annotations

import re
import unicodedata


def decide_need_human(message: str, consecutive_misses: int = 0) -> bool:
    """Route explicit requests, contracts/complaints, or two consecutive misses."""

    normalized = message.casefold().replace("đ", "d")
    normalized = "".join(
        char for char in unicodedata.normalize("NFD", normalized)
        if unicodedata.category(char) != "Mn"
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    asks_for_human = re.search(
        r"\b(?:gap|noi chuyen voi|chuyen cho|ket noi voi)\s+"
        r"(?:nguoi that|nhan vien|tu van vien|chuyen vien|quan ly)\b",
        normalized,
    )
    sensitive_business = re.search(r"\b(?:hop dong|khieu nai|khieu kien)\b", normalized)
    return bool(asks_for_human or sensitive_business or consecutive_misses >= 2)
