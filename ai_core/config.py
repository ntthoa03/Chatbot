"""
Nạp config của tenant (persona, guardrails, pricing, enabled_tools, model_policy).

STUB — đây là HOA-02 (dựng khung), việc đọc config thật từ file (YAML/DB...)
thuộc phạm vi một task riêng. Hàm dưới đây chỉ trả về giá trị giả tối thiểu
để chat() chạy được độc lập, KHÔNG hardcode nội dung nghiệp vụ thật.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    tenant_id: str
    bot_name: str = "assistant"
    self_address: str = "tôi"
    user_address: str = "bạn"
    tone: str = "trung tính"
    reply_length: str = "3-6 câu"
    forbidden_topics: list[str] = field(default_factory=list)
    refusal_message: str = "Xin phép chưa hỗ trợ được nội dung này."
    enabled_tools: list[str] = field(default_factory=list)
    model_primary: str = "stub-model"


def load_config(tenant_id: str, config_version: int = 1) -> AgentConfig:
    """Stub tạm thời — trả về config giả để test khung chạy."""
    return AgentConfig(tenant_id=tenant_id)
