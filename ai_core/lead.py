"""Deterministic lead capture and human-handoff rules for HOA-14."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from ai_core.models import LeadCaptured, Message


_PHONE_CANDIDATE = re.compile(
    r"(?<!\d)(?:\+?84|0)(?:[\s.()/\-–—]*\d){9,10}(?!\d)"
)
_PHONE_LABEL = re.compile(
    r"\b(?:sdt|sđt|so dien thoai|số điện thoại|dien thoai|điện thoại|phone)\b",
    re.IGNORECASE,
)
_NAME_PATTERNS = (
    re.compile(
        r"\b(?:t[eê]n(?:\s+c[uủ]a\s+(?:anh|ch[iị]|em|t[oô]i|m[iì]nh))?\s*(?:l[aà]|:)|"
        r"(?:anh|ch[iị]|em|t[oô]i|m[iì]nh)\s+t[eê]n(?:\s+l[aà])?|"
        r"(?:anh|ch[iị]|em|t[oô]i|m[iì]nh)\s+l[aà])\s*"
        r"([A-Za-zÀ-ỹĐđ]+(?:[ '\-]+[A-Za-zÀ-ỹĐđ]+){0,5})",
        re.IGNORECASE,
    ),
)
_CONFIRMATION_PREFIX = "Em xin xác nhận thông tin:"
_LEAD_REQUEST_MARKER = "cho em xin tên và số điện thoại"
_AFFIRMATIVE = re.compile(
    r"\b(?:dung|ok|okay|chinh xac|xac nhan|u|uh|vang|dong y|yes)\b"
)
_NEGATIVE = re.compile(r"\b(?:không đúng|khong dung|sai|không phải|khong phai)\b")


@dataclass(frozen=True)
class LeadDecision:
    captured: LeadCaptured | None = None
    reply_override: str | None = None


def _normalize(text: str) -> str:
    text = text.casefold().replace("đ", "d")
    text = "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", text).strip()


def extract_vietnamese_phone(text: str) -> str | None:
    """Return a canonical 10-digit Vietnamese mobile number, if present."""

    for match in _PHONE_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if digits.startswith("84") and len(digits) == 11:
            digits = "0" + digits[2:]
        if len(digits) == 10 and digits[:2] in {"03", "05", "07", "08", "09"}:
            return digits
    return None


def looks_like_phone_submission(text: str) -> bool:
    """Distinguish a submitted phone value from a request for the company hotline."""

    return bool(_PHONE_LABEL.search(text) and re.search(r"\d", text))


def extract_name(text: str) -> str | None:
    """Extract only explicitly introduced names; never guess from arbitrary prose."""

    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            name = re.split(
                r"\s*(?:,|;|\.|\b(?:sđt|sdt|số điện thoại|so dien thoai)\b)",
                match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" ,-.")
            if 1 <= len(name.split()) <= 6:
                return " ".join(part.capitalize() for part in name.split())
    if extract_vietnamese_phone(text):
        before_phone = _PHONE_CANDIDATE.split(text, maxsplit=1)[0]
        normalized_before = _normalize(before_phone)
        if re.search(r"\b(?:sdt|so dien thoai|phone|lien he)\b", normalized_before):
            return None
        before_phone = before_phone.strip(" ,;:-")
        words = re.findall(r"[A-Za-zÀ-ỹĐđ]+", before_phone)
        if 2 <= len(words) <= 6:
            return " ".join(part.capitalize() for part in words)
    return None


def _latest_pending_confirmation(history: Sequence[Message]) -> Message | None:
    for item in reversed(history):
        if item.role == "assistant":
            return item if item.content.startswith(_CONFIRMATION_PREFIX) else None
    return None


def decide_lead(message: str, history: Sequence[Message]) -> LeadDecision:
    """Confirm an extracted lead first, then emit it only on customer approval."""

    pending = _latest_pending_confirmation(history)
    normalized = _normalize(message)
    if pending is not None and _AFFIRMATIVE.search(normalized) and not _NEGATIVE.search(normalized):
        name = extract_name(pending.content)
        phone = extract_vietnamese_phone(pending.content)
        if name and phone:
            return LeadDecision(
                captured=LeadCaptured(name=name, phone=phone),
                reply_override=(
                    "Em đã ghi nhận thông tin của anh/chị. Chuyên viên sẽ liên hệ hỗ trợ sớm ạ."
                ),
            )

    name = extract_name(message)
    phone = extract_vietnamese_phone(message)
    if looks_like_phone_submission(message) and phone is None:
        return LeadDecision(
            reply_override=(
                "Số điện thoại anh/chị vừa nhập chưa hợp lệ. Vui lòng gửi SĐT Việt Nam "
                "gồm 10 số và bắt đầu bằng 03, 05, 07, 08 hoặc 09 ạ."
            )
        )
    if name or phone:
        for item in reversed(history):
            if item.role != "user":
                continue
            name = name or extract_name(item.content)
            phone = phone or extract_vietnamese_phone(item.content)
            if name and phone:
                break
    if pending is not None and _NEGATIVE.search(normalized) and not (name and phone):
        return LeadDecision(
            reply_override=(
                "Dạ, anh/chị vui lòng gửi lại tên và số điện thoại đúng để em xác nhận ạ."
            )
        )

    if name and phone:
        return LeadDecision(
            reply_override=(
                f"{_CONFIRMATION_PREFIX} anh/chị tên {name}, số điện thoại {phone}. "
                "Thông tin này đã đúng chưa ạ?"
            )
        )
    return LeadDecision()


def count_user_turns(history: Sequence[Message], current_message: str) -> int:
    return sum(item.role == "user" for item in history) + bool(current_message.strip())


def count_lead_requests(history: Sequence[Message]) -> int:
    marker = _normalize(_LEAD_REQUEST_MARKER)
    return sum(item.role == "assistant" and marker in _normalize(item.content) for item in history)


def should_request_lead(
    history: Sequence[Message],
    current_message: str,
    *,
    ask_after_turns: int,
    max_requests: int,
) -> bool:
    """Ask at the configured turn, at most ``max_requests`` times per conversation."""

    if count_user_turns(history, current_message) < ask_after_turns:
        return False
    if count_lead_requests(history) >= max_requests:
        return False
    combined = "\n".join([*(item.content for item in history), current_message])
    return not (extract_name(combined) and extract_vietnamese_phone(combined))


def append_lead_request(reply: str) -> str:
    request = "Để chuyên viên tư vấn kỹ hơn, anh/chị cho em xin tên và số điện thoại nhé?"
    return f"{reply.rstrip()}\n\n{request}" if reply.strip() else request


def is_missing_data_reply(text: str, configured_message: str) -> bool:
    return _normalize(configured_message) in _normalize(text)


def previous_consecutive_misses(history: Sequence[Message], configured_message: str) -> int:
    """Count the immediately preceding miss response without storing server-side state."""

    for item in reversed(history):
        if item.role == "assistant":
            return 1 if is_missing_data_reply(item.content, configured_message) else 0
    return 0
