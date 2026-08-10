"""
Trace một lượt trả lời của agent. STUB — ghi log thật thuộc phạm vi task khác.
"""

from __future__ import annotations

import uuid


def new_trace_id() -> str:
    return str(uuid.uuid4())


def log_trace(trace: dict) -> None:
    """Stub: chưa ghi ra đâu cả."""
    pass
