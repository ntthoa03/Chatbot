"""
Hàm hỗ trợ chấm điểm câu trả lời cho khung eval.
STUB — logic thật thuộc phạm vi task khác.
"""

from __future__ import annotations


def score_reply(reply: str, must_contain: list[str], must_not_contain: list[str]) -> bool:
    ok_contain = all(kw.lower() in reply.lower() for kw in must_contain)
    ok_not_contain = all(kw.lower() not in reply.lower() for kw in must_not_contain)
    return ok_contain and ok_not_contain
