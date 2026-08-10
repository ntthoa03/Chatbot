"""
Kiểm duyệt câu trả lời trước khi gửi cho khách. STUB — logic thật (hạng mục
rủi ro cao nhất dự án) thuộc phạm vi một task riêng, không được bỏ qua sau này.
"""

from __future__ import annotations


def check_output(reply: str) -> dict:
    """Trả về {"blocked": bool, "reason": str|None}"""
    return {"blocked": False, "reason": None}
