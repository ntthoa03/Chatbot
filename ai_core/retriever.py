"""
Truy vấn kho tri thức (RAG). STUB — logic thật thuộc phạm vi task khác.
"""

from __future__ import annotations


def retrieve(query: str, tenant_id: str, k: int = 5) -> list[dict]:
    """
    Trả về list các dict: {"chunk_id": str, "content": str, "url": str|None, "score": float}

    Stub: chưa có index thật -> luôn trả rỗng, tương đương "không tìm thấy dữ liệu",
    để chat() vẫn chạy được đúng theo hợp đồng (sources: []).
    """
    return []
