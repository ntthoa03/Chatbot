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
        r"(?:anh|ch[iị]|em|t[oô]i|m[iì]nh)\s+l[aà]|"
        r"t[eê]n\s+(?!mi[eề]n\b|g[iì]\b|n[aà]o\b))\s*"
        r"([A-Za-zÀ-ỹĐđ]+(?:[ '\-]+[A-Za-zÀ-ỹĐđ]+){0,5})",
        re.IGNORECASE,
    ),
)
# Các mẫu này là câu hỏi truy xuất thông tin đã nhớ, không phải lời giới thiệu tên.
# Giữ ở tầng lead dùng chung để mọi tenant đều có cùng hành vi an toàn.
_IDENTITY_LOOKUP_PATTERNS = (
    re.compile(
        r"\b(?:toi|minh|anh|chi|em)\s+la\s+ai"
        r"(?:\s+(?:k|ko|khong|vay|nhi|nhe))?\s*[?!.]*$"
    ),
    re.compile(
        r"\bten\s+(?:cua\s+)?(?:toi|minh|anh|chi|em)\s+(?:la\s+)?gi"
        r"(?:\s+(?:vay|nhi|nhe))?\s*[?!.]*$"
    ),
    re.compile(r"\bwho\s+am\s+i\s*[?!.]*$"),
    re.compile(r"\bwhat(?:'s|\s+is)\s+my\s+name\s*[?!.]*$"),
    re.compile(r"\bdo\s+you\s+(?:still\s+)?(?:know|remember)\s+who\s+i\s+am\s*[?!.]*$"),
)
_CONFIRMATION_PREFIX = "Em xin xác nhận thông tin:"
_LEAD_REQUEST_MARKER = "cho em xin tên và số điện thoại"
_HANDOFF_CONTACT_MARKER = "để em chuyển yêu cầu đến chuyên viên"
_HANDOFF_COMPLETE_MARKER = "em đã ghi nhận yêu cầu chuyển chuyên viên"
_AFFIRMATIVE = re.compile(
    r"\b(?:dung|ok|okay|chinh xac|xac nhan|u|uh|vang|dong y|yes)\b"
)
_NEGATIVE = re.compile(
    r"\b(?:khong dung|sai|khong phai|k|ko|khong|no|nope)\b"
)


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


def is_identity_lookup(text: str) -> bool:
    """Return True when the customer asks the bot to recall their identity."""

    normalized = _normalize(text)
    return any(pattern.search(normalized) for pattern in _IDENTITY_LOOKUP_PATTERNS)


def extract_name(text: str) -> str | None:
    """Extract only explicitly introduced names; never guess from arbitrary prose."""

    # Không cho regex "tôi là ..." biến đại từ nghi vấn thành tên như "Ai K".
    if is_identity_lookup(text):
        return None

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


def confirmed_lead_from_history(history: Sequence[Message]) -> LeadCaptured | None:
    """Recover the latest lead that the customer explicitly approved."""

    latest: LeadCaptured | None = None
    for index, item in enumerate(history[:-1]):
        if item.role != "assistant" or not item.content.startswith(_CONFIRMATION_PREFIX):
            continue
        approval = history[index + 1]
        normalized_approval = _normalize(approval.content)
        if (
            approval.role != "user"
            or not _AFFIRMATIVE.search(normalized_approval)
            or _NEGATIVE.search(normalized_approval)
        ):
            continue
        name = extract_name(item.content)
        phone = extract_vietnamese_phone(item.content)
        if name and phone:
            # Nếu khách xác nhận lại thông tin mới, bản xác nhận gần nhất sẽ thắng.
            latest = LeadCaptured(name=name, phone=phone)
    return latest


def has_pending_handoff(history: Sequence[Message]) -> bool:
    """Khôi phục trạng thái đang chờ lead từ lịch sử vì core không giữ session server."""

    contact_marker = _normalize(_HANDOFF_CONTACT_MARKER)
    complete_marker = _normalize(_HANDOFF_COMPLETE_MARKER)
    for item in reversed(history):
        if item.role != "assistant":
            continue
        normalized = _normalize(item.content)
        if complete_marker in normalized:
            return False
        if contact_marker in normalized:
            return True
    return False


def append_handoff_contact_request(reply: str) -> str:
    """Yêu cầu lead ngay khi cần chuyển người thật, không chờ mốc 2-3 lượt."""

    request = (
        "Để em chuyển yêu cầu đến chuyên viên, anh/chị vui lòng cho em xin tên và "
        "số điện thoại liên hệ ạ. Em sẽ đọc lại để anh/chị xác nhận trước khi ghi nhận."
    )
    if _normalize(_HANDOFF_CONTACT_MARKER) in _normalize(reply):
        return reply
    return f"{reply.rstrip()}\n\n{request}" if reply.strip() else request


def handoff_acknowledgement(name: str | None) -> str:
    """Xác nhận nghiệp vụ sau khi đã có lead hợp lệ và được khách xác nhận."""

    salutation = f" của anh/chị {name}" if name else " của anh/chị"
    return (
        f"Em đã ghi nhận yêu cầu chuyển chuyên viên{salutation}. "
        "Chuyên viên sẽ tiếp nhận thông tin và hỗ trợ anh/chị sớm ạ."
    )


def append_handoff_acknowledgement(reply: str, name: str | None) -> str:
    """Giữ câu an toàn/tư vấn hiện tại rồi mới thêm trạng thái chuyển người."""

    if _normalize(_HANDOFF_COMPLETE_MARKER) in _normalize(reply):
        return reply
    acknowledgement = handoff_acknowledgement(name)
    return f"{reply.rstrip()}\n\n{acknowledgement}" if reply.strip() else acknowledgement


def decide_lead(message: str, history: Sequence[Message]) -> LeadDecision:
    """Confirm an extracted lead first, then emit it only on customer approval."""

    pending = _latest_pending_confirmation(history)
    normalized = _normalize(message)

    if is_identity_lookup(message):
        confirmed = confirmed_lead_from_history(history)
        if confirmed is not None and confirmed.name:
            # Chỉ nhắc lại tên được hỏi, không phát lại số điện thoại không cần thiết.
            return LeadDecision(
                reply_override=(
                    f"Dạ, anh/chị đã giới thiệu tên là {confirmed.name} ạ. "
                    "Em vẫn đang ghi nhớ thông tin này trong cuộc trò chuyện hiện tại."
                )
            )
        return LeadDecision(
            reply_override=(
                "Dạ, trong cuộc trò chuyện hiện tại em chưa thấy tên nào đã được "
                "anh/chị xác nhận. Anh/chị có thể giới thiệu lại tên giúp em nhé."
            )
        )

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
    if phone:
        return LeadDecision(
            reply_override=(
                f"Em đã nhận số điện thoại {phone}. "
                "Anh/chị cho em xin thêm tên để em đọc lại và xác nhận thông tin ạ."
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
