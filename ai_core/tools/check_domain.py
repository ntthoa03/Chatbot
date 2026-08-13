"""Tool tra cứu tên miền bản mock của HOA-10."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_core.tools.base import ToolDefinition


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
DOMAIN_IN_TEXT_PATTERN = re.compile(
    r"(?<![a-z0-9-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![a-z0-9-])",
    re.IGNORECASE,
)


class CheckDomainArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(
        min_length=4,
        max_length=253,
        description="Tên miền đầy đủ cần kiểm tra, ví dụ example.vn",
    )

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str) -> str:
        normalized = value.strip().casefold().rstrip(".")
        if not DOMAIN_PATTERN.fullmatch(normalized):
            raise ValueError("tên miền không đúng định dạng")
        return normalized


class CheckDomainResult(BaseModel):
    """Chỉ các trường dữ liệu này được phép đi từ adapter ngoài vào model."""

    model_config = ConfigDict(extra="ignore")

    ok: bool
    domain: str
    available: bool
    source: Literal["mock", "h07"]
    authoritative: bool

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return CheckDomainArgs(domain=value).domain


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold())
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn").replace("đ", "d")


def matches_domain_question(message: str) -> bool:
    """Router rẻ để quyết định có nên cho model nhìn thấy tool khi RAG rỗng."""

    plain = _plain_text(message)
    has_subject = bool(DOMAIN_IN_TEXT_PATTERN.search(message.casefold())) or any(
        marker in plain
        for marker in ("ten mien", "domain", "dia chi web")
    )
    has_check_intent = any(
        marker in plain
        for marker in (
            "kiem tra",
            "check",
            "con trong",
            "con khong",
            "dang ky duoc",
            "mua duoc",
            "available",
        )
    )
    return has_subject and has_check_intent


def mock_check_domain(domain: str) -> dict:
    """Kết quả mock ổn định để demo; thay handler này bằng adapter H-07 sau này."""

    # Một số tên mẫu luôn bận; các tên khác dùng hash để kết quả ổn định giữa
    # các lần chạy mà không gọi mạng hoặc giả vờ đây là dữ liệu WHOIS thật.
    reserved = {"google.com", "facebook.com", "mimadigi.com", "openai.com"}
    available = domain not in reserved and hashlib.sha256(domain.encode()).digest()[0] % 3 != 0
    return {
        "ok": True,
        "domain": domain,
        "available": available,
        "source": "mock",
        "authoritative": False,
    }


# Khi H-07 sẵn sàng, chỉ thay ``handler=mock_check_domain`` bằng adapter thật.
CHECK_DOMAIN_TOOL = ToolDefinition(
    name="check_domain",
    description="Kiểm tra một tên miền có đang khả dụng để đăng ký hay không.",
    args_model=CheckDomainArgs,
    handler=mock_check_domain,
    timeout_seconds=2.0,
    result_model=CheckDomainResult,
    matches_message=matches_domain_question,
)
