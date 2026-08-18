"""Safe appointment handoff tool for healthcare tenants.

This local adapter does not claim a booking is confirmed and deliberately does
not accept patient identity, symptoms, medical records or payment details.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_core.tools.base import ToolDefinition


class AppointmentRequestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: str | None = Field(
        default=None,
        max_length=100,
        description="Chuyên khoa hoặc loại khám khách muốn đặt; không ghi triệu chứng hay bệnh án.",
    )
    preferred_time: str | None = Field(
        default=None,
        max_length=100,
        description="Khoảng thời gian khách mong muốn; đây chưa phải lịch được xác nhận.",
    )

    @field_validator("specialty", "preferred_time")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class AppointmentRequestResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    status: Literal["handoff_required"]
    specialty: str | None = None
    preferred_time: str | None = None
    requires_human: bool
    message: str


def create_appointment_handoff(
    specialty: str | None = None,
    preferred_time: str | None = None,
) -> dict:
    return {
        "ok": True,
        "status": "handoff_required",
        "specialty": specialty,
        "preferred_time": preferred_time,
        "requires_human": True,
        "message": (
            "Đã tạo yêu cầu đặt lịch sơ bộ. Lịch chưa được xác nhận; "
            "nhân viên phòng khám cần liên hệ để xác nhận thời gian và thông tin cần thiết."
        ),
    }


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def matches_appointment_request(message: str) -> bool:
    plain = re.sub(r"\s+", " ", _plain_text(message)).strip()
    return any(
        marker in plain
        for marker in (
            "dat lich",
            "dang ky kham",
            "hen kham",
            "hen bac si",
            "book lich",
            "book kham",
        )
    )


REQUEST_APPOINTMENT_TOOL = ToolDefinition(
    name="request_appointment",
    description=(
        "Tạo yêu cầu đặt lịch khám sơ bộ và chuyển nhân viên xác nhận. "
        "Không xác nhận lịch, không thu bệnh án, thông tin thanh toán hoặc mã OTP."
    ),
    args_model=AppointmentRequestArgs,
    handler=create_appointment_handoff,
    timeout_seconds=2.0,
    result_model=AppointmentRequestResult,
    matches_message=matches_appointment_request,
)
