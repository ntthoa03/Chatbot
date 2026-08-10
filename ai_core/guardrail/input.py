"""
Lọc trước khi gửi cho LLM. STUB — logic thật thuộc phạm vi task khác.
"""

from __future__ import annotations


def check_input(message: str) -> dict:
    """Trả về {"blocked": bool, "reason": str|None}"""
    return {"blocked": False, "reason": None}
