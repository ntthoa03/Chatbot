"""
Model dữ liệu cho request/response của chat(), khớp 1-1 với hợp đồng API đã
chốt ở HOA-01 (file contract.md — không nằm trong package này, xem repo gốc).


"""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    tenant_id: str
    conversation_id: UUID
    message: str = Field(min_length=1, max_length=1000)
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
    cost_vnd: float = 0.0
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
