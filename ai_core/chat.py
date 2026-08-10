r"""
Cửa vào duy nhất của ai_core (HOA-02). Input dùng để thiết kế schema: hợp
đồng API đã chốt ở HOA-01 (file contract.md).

    from ai_core.chat import chat
    response_dict = chat(payload_dict)

Chạy thử với payload mẫu:

    python -m ai_core.chat

Ràng buộc: module này (và mọi module ai_core.* mà nó import) KHÔNG được import
psycopg/sqlalchemy/django/redis/flask/fastapi hay bất cứ thứ gì thuộc DB/ORM/
HTTP framework. Kiểm tra nhanh:

    grep -RniE "^\s*(import|from)\s+(psycopg|sqlalchemy|django|redis|flask|fastapi)" ai_core/
"""

from __future__ import annotations

import json

from ai_core.config import load_config
from ai_core.guardrail.input import check_input
from ai_core.guardrail.output import check_output
from ai_core.models import (
    ChatRequest,
    ChatResponse,
    GuardrailResult,
    Source,
    Usage,
)
from ai_core.prompt import build_system_prompt
from ai_core.retriever import retrieve
from ai_core.router import decide_need_human
from ai_core.trace import log_trace, new_trace_id


def chat(payload: dict) -> dict:
    """
    Một lượt hội thoại, đúng theo request/response schema đã chốt ở HOA-01.

    LƯU Ý (HOA-02): đây mới là KHUNG. load_config/retrieve/build_system_prompt/
    check_input/check_output/decide_need_human/log_trace hiện là STUB, sẽ được
    thay bằng logic thật ở các task sau — không đổi chữ ký hàm khi thay.
    """
    request = ChatRequest(**payload)
    trace_id = new_trace_id()
    config = load_config(request.tenant_id, request.config_version)

    input_check = check_input(request.message)
    if input_check["blocked"]:
        response = ChatResponse(
            reply=config.refusal_message,
            need_human=True,
            guardrail=GuardrailResult(**input_check),
            usage=Usage(model=config.model_primary),
            trace_id=trace_id,
        )
        log_trace({"trace_id": trace_id, "stage": "blocked_input", "message": request.message})
        return response.model_dump(mode="json")

    raw_sources = retrieve(request.message, request.tenant_id)
    system_prompt = build_system_prompt(config)  # noqa: F841 (chưa dùng tới khi chưa nối LLM thật)

    # TODO: thay đoạn này bằng lời gọi LLM thật, ghép system_prompt + ngữ cảnh
    # RAG (raw_sources) + request.history + request.message — thuộc task khác.
    reply_text = (
        f"[stub] Đã nhận: '{request.message}'. "
        "Chưa nối LLM thật — đây là khung để test đường ống."
    )

    output_check = check_output(reply_text)
    if output_check["blocked"]:
        reply_text = config.refusal_message

    response = ChatResponse(
        reply=reply_text,
        sources=[Source(**s) for s in raw_sources],
        tool_calls=[],
        need_human=decide_need_human(request.message),
        lead_captured=None,
        guardrail=GuardrailResult(**output_check),
        usage=Usage(model=config.model_primary),
        trace_id=trace_id,
    )

    log_trace(
        {
            "trace_id": trace_id,
            "tenant_id": request.tenant_id,
            "message": request.message,
            "sources": raw_sources,
            "guardrail": output_check,
        }
    )
    return response.model_dump(mode="json")


_SAMPLE_PAYLOAD = {
    "tenant_id": "mima_internal",
    "conversation_id": "b3e1e2b0-1234-4a11-8b11-000000000001",
    "message": "Làm web bao nhiêu tiền em?",
    "history": [
        {"role": "user", "content": "Chào em"},
        {"role": "assistant", "content": "Dạ chào anh/chị, em là MIMA ạ..."},
    ],
    "config_version": 1,
}


if __name__ == "__main__":
    result = chat(_SAMPLE_PAYLOAD)
    print(json.dumps(result, indent=2, ensure_ascii=False))
