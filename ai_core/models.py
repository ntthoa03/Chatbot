"""
Model dữ liệu cho request/response của chat(), khớp 1-1 với hợp đồng API đã
chốt ở HOA-01 (file contract.md — không nằm trong package này, xem repo gốc).


"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ChunkMetadata(BaseModel):
    """Metadata contract from Task.xlsx / sheet ``Tham chieu``."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    title: str = Field(min_length=1)
    type: Literal["service", "pricing", "policy", "faq", "blog"]
    updated_at: date


class KnowledgeChunk(BaseModel):
    """Exact business chunk exchanged between Hieu and Hoa."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    metadata: ChunkMetadata


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # Chặn request thiếu/sai tenant ngay tại biên API, trước khi chạy RAG hoặc model.
    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    conversation_id: UUID
    # HOA-11 returns an auditable guardrail response for oversized input instead
    # of letting Pydantic raise before the guardrail can run.
    message: str = Field(min_length=1)
    history: list[Message] = Field(default_factory=list)
    config_version: int = 1


class Source(BaseModel):
    chunk_id: str
    url: Optional[str] = None
    score: float


class ToolCall(BaseModel):
    name: str
    args: dict
    result: dict


class LeadCaptured(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None


class GuardrailResult(BaseModel):
    blocked: bool = False
    reason: Optional[str] = None


class Usage(BaseModel):
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens_in: int = 0
    cache_write_tokens_in: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class ChatResponse(BaseModel):
    reply: str
    sources: list[Source] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    need_human: bool = False
    lead_captured: Optional[LeadCaptured] = None
    guardrail: GuardrailResult
    usage: Usage
    trace_id: UUID
