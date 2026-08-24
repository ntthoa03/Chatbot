"""Contract H2-11 ổn định để backend tích hợp với AI core.

Tên lớp và biến dùng tiếng Anh; comment tiếng Việt giải thích các ranh giới
quan trọng để người bàn giao có thể kiểm tra nhanh.
"""

from __future__ import annotations

import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from ai_core.models import ChatRequest, ChatResponse, GuardrailResult, KnowledgeChunk, Usage


INTERFACE_VERSION = "h2-11.v2"
INTERFACE_BACKEND_ENV = "AI_CORE_INTERFACE_BACKEND"
SUPPORTED_INTERFACE_BACKENDS = frozenset({"real", "in_memory"})
_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_WORD_PATTERN = re.compile(r"[a-z0-9]+")


class InterfaceConfigurationError(ValueError):
    """Raised when the selected interface implementation is invalid."""


class InterfaceValidationError(ValueError):
    """Raised when an interface request violates the frozen contract."""


@runtime_checkable
class RetrieverPort(Protocol):
    """Frozen H2-11 retriever contract."""

    def retrieve(self, query: str, tenant_id: str, k: int = 5) -> list[dict[str, Any]]:
        """Return at most ``k`` chunks belonging only to ``tenant_id``."""


@runtime_checkable
class ChatPort(Protocol):
    """Frozen H2-11 non-streaming chat contract."""

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return one response matching ``ChatResponse``."""


@dataclass(frozen=True)
class AIServices:
    """A matched retriever/chat pair selected by one config value."""

    retriever: RetrieverPort
    chat: ChatPort
    backend: str
    interface_version: str = INTERFACE_VERSION


RetrieveCallable = Callable[[str, str, int], list[dict[str, Any]]]
ChatCallable = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class RealRetriever:
    """Adapter around the production ``ai_core.retriever.retrieve`` function."""

    implementation: RetrieveCallable | None = None

    def retrieve(self, query: str, tenant_id: str, k: int = 5) -> list[dict[str, Any]]:
        # Import trễ để module contract không khởi tạo model/index khi chỉ đọc interface.
        implementation = self.implementation
        if implementation is None:
            from ai_core.retriever import retrieve as implementation

        return implementation(query, tenant_id, k)


@dataclass(frozen=True)
class RealChat:
    """Adapter around the production ``ai_core.chat.chat`` function."""

    implementation: ChatCallable | None = None

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        # H2-11 đóng băng response thường; streaming vẫn là extension của hàm core cũ.
        implementation = self.implementation
        if implementation is None:
            from ai_core.chat import chat as implementation

        result = implementation(payload)
        if not isinstance(result, dict):
            raise InterfaceValidationError("chat(payload) must return a dict in H2-11")
        return result


def _normalise_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    without_marks = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(_WORD_PATTERN.findall(without_marks))


def _validate_tenant_id(tenant_id: Any, allowed_tenants: frozenset[str]) -> str:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise InterfaceValidationError("tenant_id is required and cannot be empty")
    clean_tenant_id = tenant_id.strip()
    if not _TENANT_ID_PATTERN.fullmatch(clean_tenant_id):
        raise InterfaceValidationError("tenant_id has an invalid format")
    if clean_tenant_id not in allowed_tenants:
        raise InterfaceValidationError(f"unknown tenant_id: {clean_tenant_id}")
    return clean_tenant_id


@dataclass
class InMemoryRetriever:
    """Deterministic tenant-safe fake retriever for tests and local integration."""

    chunks: Sequence[dict[str, Any] | KnowledgeChunk] = field(default_factory=tuple)
    tenant_ids: Sequence[str] = field(default_factory=tuple)
    _chunks: tuple[dict[str, Any], ...] = field(init=False, repr=False)
    _tenant_ids: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        parsed_chunks: list[dict[str, Any]] = []
        derived_tenants: set[str] = set()
        for raw_chunk in self.chunks:
            chunk = raw_chunk if isinstance(raw_chunk, KnowledgeChunk) else KnowledgeChunk.model_validate(raw_chunk)
            parsed = chunk.model_dump(mode="json")
            parsed_chunks.append(parsed)
            derived_tenants.add(chunk.tenant_id)

        allowed_tenants = {str(value).strip() for value in self.tenant_ids if str(value).strip()}
        allowed_tenants.update(derived_tenants)
        for tenant_id in allowed_tenants:
            if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
                raise InterfaceConfigurationError(f"invalid configured tenant_id: {tenant_id}")

        self._chunks = tuple(parsed_chunks)
        self._tenant_ids = frozenset(allowed_tenants)

    def retrieve(self, query: str, tenant_id: str, k: int = 5) -> list[dict[str, Any]]:
        clean_tenant_id = _validate_tenant_id(tenant_id, self._tenant_ids)
        if not isinstance(query, str):
            raise InterfaceValidationError("query must be a string")
        if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
            raise InterfaceValidationError("k must be a positive integer")

        query_terms = set(_normalise_text(query).split())
        if not query_terms:
            return []

        ranked: list[tuple[float, dict[str, Any]]] = []
        for chunk in self._chunks:
            # Lọc tenant trước khi tính điểm để không thể rò chunk chéo tenant.
            if chunk["tenant_id"] != clean_tenant_id:
                continue
            content_terms = set(_normalise_text(chunk["content"]).split())
            overlap = len(query_terms & content_terms)
            score = overlap / max(len(query_terms), 1)
            if score <= 0:
                continue
            result = dict(chunk)
            result["score"] = round(score, 6)
            ranked.append((score, result))

        ranked.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        return [item[1] for item in ranked[:k]]


@dataclass
class InMemoryChat:
    """Deterministic fake chat with the exact public ChatResponse shape."""

    tenant_ids: Sequence[str]
    replies: Mapping[tuple[str, str], str] = field(default_factory=dict)
    default_reply: str = "Dạ, đây là phản hồi giả lập từ AI core ạ."
    _tenant_ids: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        clean_tenants = frozenset(str(value).strip() for value in self.tenant_ids if str(value).strip())
        if not clean_tenants:
            raise InterfaceConfigurationError("in_memory chat requires at least one tenant_id")
        for tenant_id in clean_tenants:
            if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
                raise InterfaceConfigurationError(f"invalid configured tenant_id: {tenant_id}")
        self._tenant_ids = clean_tenants

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = ChatRequest.model_validate(payload)
        except Exception as exc:
            raise InterfaceValidationError(f"invalid chat payload: {exc}") from exc

        tenant_id = _validate_tenant_id(request.tenant_id, self._tenant_ids)
        lookup_key = (tenant_id, _normalise_text(request.message))
        reply = self.replies.get(lookup_key, self.default_reply)
        response = ChatResponse(
            reply=reply,
            sources=[],
            tool_calls=[],
            need_human=False,
            lead_captured=None,
            guardrail=GuardrailResult(blocked=False),
            usage=Usage(model="in-memory", cost_usd=0.0),
            trace_id=str(uuid.uuid4()),
        )
        return response.model_dump(mode="json")


def build_services(
    *,
    backend: str | None = None,
    chunks: Sequence[dict[str, Any] | KnowledgeChunk] = (),
    tenant_ids: Sequence[str] = (),
    replies: Mapping[tuple[str, str], str] | None = None,
    retrieve_implementation: RetrieveCallable | None = None,
    chat_implementation: ChatCallable | None = None,
) -> AIServices:
    """Build both public services from one config value.

    Production normally uses ``AI_CORE_INTERFACE_BACKEND=real``. Tests can set
    the same variable to ``in_memory`` and inject tenant-scoped fixtures.
    """

    selected = (backend or os.getenv(INTERFACE_BACKEND_ENV, "real")).strip().casefold()
    if selected not in SUPPORTED_INTERFACE_BACKENDS:
        options = ", ".join(sorted(SUPPORTED_INTERFACE_BACKENDS))
        raise InterfaceConfigurationError(f"unsupported interface backend {selected!r}; use one of: {options}")

    if selected == "real":
        return AIServices(
            retriever=RealRetriever(implementation=retrieve_implementation),
            chat=RealChat(implementation=chat_implementation),
            backend=selected,
        )

    fake_retriever = InMemoryRetriever(chunks=chunks, tenant_ids=tenant_ids)
    effective_tenants = tuple(fake_retriever._tenant_ids)
    return AIServices(
        retriever=fake_retriever,
        chat=InMemoryChat(
            tenant_ids=effective_tenants,
            replies={} if replies is None else replies,
        ),
        backend=selected,
    )


__all__ = [
    "AIServices",
    "ChatPort",
    "INTERFACE_BACKEND_ENV",
    "INTERFACE_VERSION",
    "InMemoryChat",
    "InMemoryRetriever",
    "InterfaceConfigurationError",
    "InterfaceValidationError",
    "RealChat",
    "RealRetriever",
    "RetrieverPort",
    "build_services",
]
