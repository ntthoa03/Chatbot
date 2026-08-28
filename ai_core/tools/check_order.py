"""Tool tra đơn an toàn dành cho template bán lẻ H3-02.

H3-02 chưa có API đơn hàng thật theo tenant. Tool chỉ chuẩn hóa mã đơn và tạo yêu
cầu tra cứu cho người thật; tuyệt đối không tự sinh trạng thái giao hàng.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_core.tools.base import ToolDefinition


class CheckOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_code: str = Field(
        min_length=3,
        max_length=50,
        description="Mã đơn hàng do khách cung cấp; không nhận OTP hoặc thông tin thẻ.",
    )

    @field_validator("order_code")
    @classmethod
    def validate_order_code(cls, value: str) -> str:
        normalized = " ".join(value.split()).upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,49}", normalized):
            raise ValueError("mã đơn chỉ gồm chữ, số, '_' hoặc '-'")
        return normalized


class CheckOrderResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    status: Literal["handoff_required"]
    order_code: str
    requires_human: bool
    message: str


def create_order_lookup_handoff(order_code: str) -> dict:
    """Không có backend thật thì fail-safe sang người, không bịa trạng thái đơn."""

    return {
        "ok": True,
        "status": "handoff_required",
        "order_code": order_code,
        "requires_human": True,
        "message": (
            "Đã ghi nhận yêu cầu tra cứu mã đơn. Nguồn trạng thái đơn hàng thật chưa "
            "được kết nối; nhân viên cửa hàng cần kiểm tra và phản hồi cho khách."
        ),
    }


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def matches_order_lookup(message: str) -> bool:
    plain = re.sub(r"\s+", " ", _plain_text(message)).strip()
    return any(
        marker in plain
        for marker in (
            "tra don",
            "kiem tra don",
            "check don",
            "don hang cua toi",
            "don toi dau",
            "ma don",
        )
    )


CHECK_ORDER_TOOL = ToolDefinition(
    name="check_order",
    description=(
        "Tiếp nhận mã đơn để tra cứu an toàn. Khi chưa có API trạng thái thật, "
        "tool bắt buộc chuyển nhân viên và không tự đoán trạng thái đơn."
    ),
    args_model=CheckOrderArgs,
    handler=create_order_lookup_handoff,
    timeout_seconds=2.0,
    result_model=CheckOrderResult,
    matches_message=matches_order_lookup,
)
