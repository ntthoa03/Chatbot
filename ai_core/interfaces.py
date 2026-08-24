"""Contract H2-11 ổn định để backend tích hợp với AI core.

File này chỉ định nghĩa ranh giới tích hợp và các adapter. Logic RAG/chat thật vẫn
nằm trong ``ai_core.retriever`` và ``ai_core.chat``. Khi bàn giao, không đổi chữ ký
hai hàm public nếu chưa tạo phiên bản interface mới.
"""

from __future__ import annotations

import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from ai_core.models import ChatRequest, ChatResponse, GuardrailResult, KnowledgeChunk, Usage


# Phiên bản này được contract test khóa lại để tránh thay đổi interface âm thầm.
INTERFACE_VERSION = "h2-11.v2"
# Chỉ cần đổi biến môi trường này để chuyển đồng thời retriever và chat backend.
INTERFACE_BACKEND_ENV = "AI_CORE_INTERFACE_BACKEND"
SUPPORTED_INTERFACE_BACKENDS = frozenset({"real", "in_memory"})
_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_WORD_PATTERN = re.compile(r"[a-z0-9]+")


class InterfaceConfigurationError(ValueError):
    """Lỗi cấu hình implementation, ví dụ chọn backend không được hỗ trợ."""


class InterfaceValidationError(ValueError):
    """Lỗi request/response không đúng contract H2-11 đã đóng băng."""


@runtime_checkable
class RetrieverPort(Protocol):
    """Contract tìm kiếm tri thức mà mọi retriever phải triển khai."""

    def retrieve(self, query: str, tenant_id: str, k: int = 5) -> list[dict[str, Any]]:
        """Trả tối đa ``k`` chunk và chỉ được thuộc đúng ``tenant_id``."""


@runtime_checkable
class ChatPort(Protocol):
    """Contract chat không streaming mà mọi chat implementation phải giữ."""

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Nhận payload H2-11 và trả response đúng schema ``ChatResponse``."""


@dataclass(frozen=True)
class AIServices:
    """Gom retriever và chat cùng backend để không cấu hình lệch hai bên."""

    retriever: RetrieverPort
    chat: ChatPort
    backend: str
    interface_version: str = INTERFACE_VERSION


# Hai type alias này cho phép tiêm hàm stub trong test mà vẫn giữ đúng chữ ký thật.
RetrieveCallable = Callable[[str, str, int], list[dict[str, Any]]]
ChatCallable = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class RealRetriever:
    """Adapter gọi hàm retrieve production nhưng chỉ lộ contract H2-11 ra ngoài."""

    implementation: RetrieveCallable | None = None

    def retrieve(self, query: str, tenant_id: str, k: int = 5) -> list[dict[str, Any]]:
        """Chuyển nguyên ba tham số contract sang retriever production."""

        # Import trễ để khi backend chỉ import contract sẽ không tải model/index tốn tài nguyên.
        implementation = self.implementation
        if implementation is None:
            from ai_core.retriever import retrieve as implementation

        # Không truyền các tham số mở rộng của core để interface public luôn ổn định.
        return implementation(query, tenant_id, k)


@dataclass(frozen=True)
class RealChat:
    """Adapter gọi hàm chat production nhưng chỉ lộ contract H2-11 ra ngoài."""

    implementation: ChatCallable | None = None

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Gọi chat production và bảo đảm response thường là một dictionary."""

        # H2-11 đóng băng response không streaming; streaming là extension nội bộ của core.
        implementation = self.implementation
        if implementation is None:
            from ai_core.chat import chat as implementation

        result = implementation(payload)
        # Chặn sớm trường hợp ai đó trả iterator/kiểu khác làm backend Hiếu bị vỡ contract.
        if not isinstance(result, dict):
            raise InterfaceValidationError("chat(payload) must return a dict in H2-11")
        return result


def _normalise_text(value: str) -> str:
    """Chuẩn hóa chữ thường, bỏ dấu và ký tự thừa cho fake deterministic."""

    # NFD tách dấu tiếng Việt khỏi ký tự gốc để cùng câu có dấu/không dấu khớp nhau.
    decomposed = unicodedata.normalize("NFD", value.casefold())
    without_marks = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(_WORD_PATTERN.findall(without_marks))


def _validate_tenant_id(tenant_id: Any, allowed_tenants: frozenset[str]) -> str:
    """Kiểm tra tenant bắt buộc, đúng định dạng và nằm trong danh sách cho phép."""

    # Fail-closed: thiếu tenant phải lỗi, tuyệt đối không chuyển thành truy vấn dữ liệu chung.
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise InterfaceValidationError("tenant_id is required and cannot be empty")
    clean_tenant_id = tenant_id.strip()
    # Chặn khoảng trắng, path traversal và các định dạng tenant không hợp lệ.
    if not _TENANT_ID_PATTERN.fullmatch(clean_tenant_id):
        raise InterfaceValidationError("tenant_id has an invalid format")
    # Tenant đúng cú pháp nhưng chưa đăng ký cũng phải lỗi trước khi retrieval/chat chạy.
    if clean_tenant_id not in allowed_tenants:
        raise InterfaceValidationError(f"unknown tenant_id: {clean_tenant_id}")
    return clean_tenant_id


@dataclass
class InMemoryRetriever:
    """Retriever giả lập ổn định, cách ly tenant, không gọi embedding/vector DB."""

    chunks: Sequence[dict[str, Any] | KnowledgeChunk] = field(default_factory=tuple)
    tenant_ids: Sequence[str] = field(default_factory=tuple)
    _chunks: tuple[dict[str, Any], ...] = field(init=False, repr=False)
    _tenant_ids: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate fixture một lần và chốt danh sách tenant dùng khi test."""

        parsed_chunks: list[dict[str, Any]] = []
        derived_tenants: set[str] = set()
        for raw_chunk in self.chunks:
            # Dùng cùng KnowledgeChunk với production để fake không chấp nhận dữ liệu sai schema.
            chunk = raw_chunk if isinstance(raw_chunk, KnowledgeChunk) else KnowledgeChunk.model_validate(raw_chunk)
            parsed = chunk.model_dump(mode="json")
            parsed_chunks.append(parsed)
            derived_tenants.add(chunk.tenant_id)

        # Cho phép khai báo tenant rỗng dữ liệu để test trường hợp truy vấn không có kết quả.
        allowed_tenants = {str(value).strip() for value in self.tenant_ids if str(value).strip()}
        allowed_tenants.update(derived_tenants)
        for tenant_id in allowed_tenants:
            if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
                raise InterfaceConfigurationError(f"invalid configured tenant_id: {tenant_id}")

        self._chunks = tuple(parsed_chunks)
        self._tenant_ids = frozenset(allowed_tenants)

    def retrieve(self, query: str, tenant_id: str, k: int = 5) -> list[dict[str, Any]]:
        """Tìm theo độ trùng từ, sau khi đã giới hạn dữ liệu về đúng tenant."""

        clean_tenant_id = _validate_tenant_id(tenant_id, self._tenant_ids)
        if not isinstance(query, str):
            raise InterfaceValidationError("query must be a string")
        if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
            raise InterfaceValidationError("k must be a positive integer")

        # Cách tính điểm đơn giản có chủ đích để test lặp lại luôn cho cùng kết quả.
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

        # chunk_id là khóa phụ để thứ tự ổn định khi nhiều chunk có cùng điểm.
        ranked.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        return [item[1] for item in ranked[:k]]


@dataclass
class InMemoryChat:
    """Chat giả lập không gọi LLM nhưng trả đúng toàn bộ schema public."""

    tenant_ids: Sequence[str]
    replies: Mapping[tuple[str, str], str] = field(default_factory=dict)
    default_reply: str = "Dạ, đây là phản hồi giả lập từ AI core ạ."
    _tenant_ids: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Chốt tenant hợp lệ; fake chat không tự đoán tenant mặc định."""

        clean_tenants = frozenset(str(value).strip() for value in self.tenant_ids if str(value).strip())
        if not clean_tenants:
            raise InterfaceConfigurationError("in_memory chat requires at least one tenant_id")
        for tenant_id in clean_tenants:
            if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
                raise InterfaceConfigurationError(f"invalid configured tenant_id: {tenant_id}")
        self._tenant_ids = clean_tenants

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate request, chọn reply fixture và dựng response như production."""

        try:
            # Dùng model request thật để test bắt được payload thiếu/sai ngay tại boundary.
            request = ChatRequest.model_validate(payload)
        except Exception as exc:
            raise InterfaceValidationError(f"invalid chat payload: {exc}") from exc

        tenant_id = _validate_tenant_id(request.tenant_id, self._tenant_ids)
        # Khóa reply luôn chứa tenant để fixture của tenant A không dùng nhầm cho tenant B.
        lookup_key = (tenant_id, _normalise_text(request.message))
        reply = self.replies.get(lookup_key, self.default_reply)
        # Dù là fake, vẫn trả đủ sources/tool/guardrail/usage/trace cho backend tích hợp.
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
    """Tạo đồng thời retriever và chat từ một giá trị cấu hình.

    Production dùng ``AI_CORE_INTERFACE_BACKEND=real``. Test/local có thể đặt
    ``in_memory`` rồi truyền fixture đã gắn tenant, không cần sửa logic AI.
    """

    # Tham số truyền trực tiếp được ưu tiên để unit test không phải sửa biến môi trường thật.
    selected = (backend or os.getenv(INTERFACE_BACKEND_ENV, "real")).strip().casefold()
    if selected not in SUPPORTED_INTERFACE_BACKENDS:
        options = ", ".join(sorted(SUPPORTED_INTERFACE_BACKENDS))
        raise InterfaceConfigurationError(f"unsupported interface backend {selected!r}; use one of: {options}")

    if selected == "real":
        # Cặp adapter real cùng được tạo ở đây để tránh retriever fake nhưng chat thật.
        return AIServices(
            retriever=RealRetriever(implementation=retrieve_implementation),
            chat=RealChat(implementation=chat_implementation),
            backend=selected,
        )

    # Retriever chuẩn hóa fixture trước; chat tái sử dụng đúng tập tenant đã xác nhận đó.
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
