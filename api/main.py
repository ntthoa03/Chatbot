"""Endpoint HTTP /chat tối thiểu cho H3-05.

Flow duy nhất: nhận request -> phân giải tenant -> gọi ChatPort -> lưu Storage
-> trả JSON hoặc SSE. Không đặt RAG, guardrail hay nghiệp vụ lead trong file này.

TODO(Hieu/Production): backend thật thay PublicKeyResolver và thêm PostgresStore
qua storage factory; giữ nguyên contract response và không sửa logic AI core.
"""

from __future__ import annotations

import atexit
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from dotenv import load_dotenv

from ai_core.interfaces import AIServices, ChatPort, build_services
from ai_core.models import ChatRequest, ChatResponse
from storage import Storage, StorageError, StorageValidationError, build_storage


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)
PUBLIC_KEYS_ENV = "AI_API_PUBLIC_KEYS_JSON"
CORS_ORIGINS_ENV = "AI_API_CORS_ORIGINS"


class PublicKeyConfigurationError(ValueError):
    """Cấu hình public key không hợp lệ hoặc ánh xạ trùng."""


@dataclass(frozen=True)
class PublicKeyResolver:
    """Phân giải public key thành tenant; không coi tenant trong body là tin cậy."""

    # TODO(Hieu/Auth): đây chỉ là ánh xạ key tĩnh cho demo, chưa có xác thực người
    # dùng, hết hạn/thu hồi key hay audit. Production thay resolver được inject vào
    # create_app(); vẫn phải trả tenant_id đáng tin cậy và đối chiếu với payload.

    key_to_tenant: Mapping[str, str]

    def resolve(self, public_key: str | None) -> str:
        if not isinstance(public_key, str) or not public_key.strip():
            raise HTTPException(status_code=401, detail="X-Public-Key is required")
        tenant_id = self.key_to_tenant.get(public_key.strip())
        if tenant_id is None:
            raise HTTPException(status_code=401, detail="Invalid public key")
        return tenant_id

    @classmethod
    def from_env(cls) -> "PublicKeyResolver":
        raw = os.getenv(PUBLIC_KEYS_ENV, "{}").strip() or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PublicKeyConfigurationError(
                f"{PUBLIC_KEYS_ENV} must be a JSON object"
            ) from exc
        if not isinstance(payload, dict):
            raise PublicKeyConfigurationError(f"{PUBLIC_KEYS_ENV} must be a JSON object")
        mapping: dict[str, str] = {}
        for key, tenant_id in payload.items():
            if not isinstance(key, str) or not key.strip():
                raise PublicKeyConfigurationError("public key cannot be empty")
            if not isinstance(tenant_id, str) or not tenant_id.strip():
                raise PublicKeyConfigurationError("tenant_id mapped from public key cannot be empty")
            mapping[key.strip()] = tenant_id.strip()
        return cls(mapping)


def _cors_origins_from_env() -> list[str]:
    # TODO(Hieu/Security): dấu * chỉ phục vụ browser/widget demo. Production phải
    # khai báo đúng domain frontend để tránh website lạ gọi API bằng credential.
    raw = os.getenv(CORS_ORIGINS_ENV, "*")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["*"]


def _response_events(response: dict[str, Any], chunk_chars: int = 80) -> Iterator[str]:
    """Phát SSE từ response đã kiểm duyệt; không stream token thô trước guardrail."""

    # TODO(Hieu/Streaming): đây là giả streaming sau khi đã có toàn bộ reply.
    # Nếu đổi sang token streaming thật, bắt buộc thiết kế kiểm duyệt theo buffer;
    # không phát token chưa qua output guardrail chỉ để giảm time-to-first-token.

    reply = response["reply"]
    trace_id = response["trace_id"]
    for start in range(0, len(reply), chunk_chars):
        event = {"type": "delta", "delta": reply[start : start + chunk_chars], "trace_id": trace_id}
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    done = {"type": "done", "response": response, "trace_id": trace_id}
    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"


def _persist_turn(
    storage: Storage,
    request: ChatRequest,
    response: ChatResponse,
) -> None:
    """Chỉ ánh xạ dữ liệu contract sang Storage, không quyết định nghiệp vụ."""

    # TODO(Hieu/Postgres): PostgresStore nên gói toàn bộ lượt ghi vào một transaction
    # hoặc cung cấp outbox/idempotency theo trace_id. Bản demo gọi từng method nên
    # tiến trình dừng giữa chừng có thể lưu user message nhưng chưa lưu assistant/usage.

    tenant_id = request.tenant_id
    conversation_id = str(request.conversation_id)
    trace_id = str(response.trace_id)
    storage.upsert_tenant(tenant_id, tenant_id, request.config_version)
    storage.create_conversation(tenant_id, conversation_id)
    storage.save_message(tenant_id, conversation_id, "user", request.message)
    storage.save_message(
        tenant_id,
        conversation_id,
        "assistant",
        response.reply,
        trace_id=trace_id,
    )
    lead = response.lead_captured
    if lead is not None and (lead.name or lead.phone):
        storage.save_lead(
            tenant_id,
            conversation_id,
            name=lead.name,
            phone=lead.phone,
        )
    storage.save_usage_event(
        tenant_id,
        conversation_id,
        trace_id=trace_id,
        usage=response.usage.model_dump(mode="json"),
    )


def create_app(
    *,
    services: AIServices | None = None,
    storage: Storage | None = None,
    public_key_resolver: PublicKeyResolver | None = None,
) -> FastAPI:
    """Tạo app có dependency injection để SQLite/Postgres dùng cùng flow."""

    resolver = public_key_resolver or PublicKeyResolver.from_env()
    # Backend in_memory lấy đúng tập tenant đã đăng ký qua public key để chạy demo
    # không API/data thật; backend real bỏ qua fixture này và gọi implementation thật.
    active_services = services or build_services(tenant_ids=tuple(resolver.key_to_tenant.values()))
    owns_storage = storage is None
    # Composition root duy nhất: API chỉ phụ thuộc Storage. Hiếu thêm
    # PostgresStore ở storage/factory.py, không sửa flow endpoint bên dưới.
    active_storage = storage or build_storage()
    if owns_storage:
        # Đóng connection SQLite khi tiến trình demo kết thúc; storage được inject do caller quản lý.
        atexit.register(active_storage.close)
    api = FastAPI(title="AI Core Chat API", version="h3-05.v1")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins_from_env(),
        allow_credentials=False,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Public-Key"],
    )

    @api.post(
        "/chat",
        response_model=ChatResponse,
        responses={
            200: {
                "description": "ChatResponse JSON, hoặc chuỗi SSE khi stream=true",
                "content": {"text/event-stream": {}},
            }
        },
    )
    async def post_chat(
        payload: ChatRequest,
        stream: bool = Query(default=False),
        x_public_key: str | None = Header(default=None, alias="X-Public-Key"),
    ) -> JSONResponse | StreamingResponse:
        """Endpoint mỏng đúng hợp đồng tuần 1, hỗ trợ JSON và SSE."""

        resolved_tenant = resolver.resolve(x_public_key)
        if resolved_tenant != payload.tenant_id:
            raise HTTPException(status_code=403, detail="Public key does not match tenant_id")

        try:
            # TODO(Hieu/Runtime): chat hiện là lời gọi đồng bộ bên trong endpoint async.
            # Production chuyển sang worker/thread hoặc adapter async để một lượt LLM
            # chậm không khóa event loop; không đưa logic RAG vào file API này.
            raw_response = active_services.chat.chat(payload.model_dump(mode="json"))
            response = ChatResponse.model_validate(raw_response)
            _persist_turn(active_storage, payload, response)
        except (StorageError, StorageValidationError) as exc:
            raise HTTPException(status_code=500, detail="Cannot persist chat turn") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail="AI service failed") from exc

        response_payload = response.model_dump(mode="json")
        if stream:
            return StreamingResponse(
                _response_events(response_payload),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return JSONResponse(response_payload)

    # Để test/handoff có thể đọc dependency mà không gắn state nghiệp vụ vào ai_core.
    api.state.ai_services = active_services
    api.state.storage = active_storage
    api.state.public_key_resolver = resolver
    return api


app = create_app()


__all__ = ["PublicKeyResolver", "create_app", "app"]
