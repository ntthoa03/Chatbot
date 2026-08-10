"""
Sinh system prompt từ config tenant. STUB — logic thật thuộc phạm vi task khác.
"""

from __future__ import annotations

from ai_core.config import AgentConfig


def build_system_prompt(config: AgentConfig) -> str:
    return (
        f"Bạn là {config.bot_name}, xưng '{config.self_address}', "
        f"gọi khách là '{config.user_address}'. Giọng điệu: {config.tone}. "
        f"Trả lời dài {config.reply_length}. "
        "[STUB: khối persona/phạm vi/quy tắc cấm/CTA đầy đủ thuộc task khác]"
    )
