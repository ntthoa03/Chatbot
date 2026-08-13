"""Streamlit UI nội bộ cho HOA-09.

Chạy từ thư mục gốc bằng:
    streamlit run app.py
"""

from __future__ import annotations

import os
from typing import Any, Iterable
from uuid import uuid4

import streamlit as st

from ai_core.chat import chat
from ai_core.config import load_config
from ai_core.trace import find_trace


TENANT_ID = os.getenv("AI_CORE_UI_TENANT_ID", "mima_internal")
CONFIG_VERSION = int(os.getenv("AI_CORE_UI_CONFIG_VERSION", "1"))


def build_payload(
    message: str,
    conversation_id: str,
    previous_messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the public chat contract without leaking UI-only state."""

    history = [
        {"role": item["role"], "content": item["content"]}
        for item in previous_messages
        if item.get("role") in {"user", "assistant"} and str(item.get("content", "")).strip()
    ]
    return {
        "tenant_id": TENANT_ID,
        "conversation_id": conversation_id,
        "message": message,
        "history": history,
        "config_version": CONFIG_VERSION,
    }


def consume_chat_events(events: Iterable[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Collect safe stream events and return the final contract response."""

    text = ""
    response: dict[str, Any] | None = None
    for event in events:
        if event.get("type") == "delta":
            text += str(event.get("delta", ""))
        elif event.get("type") == "done" and isinstance(event.get("response"), dict):
            response = event["response"]
    if response is None:
        raise RuntimeError("Luồng chat kết thúc nhưng không có response hoàn chỉnh.")
    return text or str(response.get("reply", "")), response


def get_source_details(response: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Enrich public sources with redacted chunk content from the HOA-16 trace."""

    if not response:
        return []
    trace_id = str(response.get("trace_id", ""))
    trace = find_trace(trace_id) if trace_id else None
    chunks = (
        trace.get("retrieval", {}).get("chunks", [])
        if isinstance(trace, dict)
        else []
    )
    if isinstance(chunks, list) and chunks:
        return [item for item in chunks if isinstance(item, dict)]
    return [
        {
            "chunk_id": source.get("chunk_id"),
            "score": source.get("score"),
            "content": None,
            "source": {"url": source.get("url")},
        }
        for source in response.get("sources", [])
        if isinstance(source, dict)
    ]


def reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.last_response = None
    st.session_state.conversation_id = str(uuid4())


def _init_session() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_response" not in st.session_state:
        st.session_state.last_response = None
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = str(uuid4())


def _render_sidebar() -> None:
    response = st.session_state.last_response
    with st.sidebar:
        st.header("Thông tin lượt trả lời")
        if st.button("🗑️ Xoá hội thoại", use_container_width=True):
            reset_conversation()
            st.rerun()

        if not response:
            st.info("Hãy gửi một câu hỏi để xem nguồn và số liệu.")
            return

        usage = response.get("usage", {})
        left, right = st.columns(2)
        left.metric("Token vào", f"{int(usage.get('tokens_in', 0)):,}")
        right.metric("Token ra", f"{int(usage.get('tokens_out', 0)):,}")
        left.metric("Chi phí", f"{float(usage.get('cost_vnd', 0)):.2f} ₫")
        right.metric("Độ trễ", f"{int(usage.get('latency_ms', 0)):,} ms")

        st.caption(f"Model: {usage.get('model', 'N/A')}")
        st.caption(f"Trace ID: {response.get('trace_id', 'N/A')}")
        if response.get("need_human"):
            st.warning("Lượt này được gắn cờ cần người thật hỗ trợ.")

        st.subheader("Nguồn RAG")
        sources = get_source_details(response)
        if not sources:
            st.caption("Không sử dụng chunk RAG trong lượt này.")
        for index, item in enumerate(sources, start=1):
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            title = source.get("title") or item.get("chunk_id") or f"Chunk {index}"
            score = float(item.get("score") or 0)
            with st.expander(f"{index}. {title} · {score:.3f}"):
                st.caption(f"Chunk ID: {item.get('chunk_id', 'N/A')}")
                if source.get("url"):
                    st.markdown(f"[Mở nguồn]({source['url']})")
                if item.get("content"):
                    st.write(item["content"])

        tool_calls = response.get("tool_calls", [])
        if tool_calls:
            st.subheader("Tool đã gọi")
            for call in tool_calls:
                with st.expander(str(call.get("name", "tool"))):
                    st.json({"args": call.get("args", {}), "result": call.get("result", {})})


def run_app() -> None:
    st.set_page_config(
        page_title="AI Chatbot — Internal Test",
        page_icon="💬",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_session()
    config = load_config(TENANT_ID, CONFIG_VERSION)

    st.title(f"💬 {config.bot_name} — Internal Test")
    st.caption(
        "Giao diện thử nghiệm nội bộ. Câu trả lời có thể cần chuyên viên xác nhận trước khi sử dụng."
    )
    _render_sidebar()

    for item in st.session_state.messages:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])

    question = st.chat_input("Nhập câu hỏi về website, SEO, tên miền…")
    if not question:
        return

    previous_messages = list(st.session_state.messages)
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        answer = ""
        response: dict[str, Any] | None = None
        try:
            payload = build_payload(
                question,
                st.session_state.conversation_id,
                previous_messages,
            )
            for event in chat(payload, stream=True):
                if event.get("type") == "delta":
                    answer += str(event.get("delta", ""))
                    placeholder.markdown(answer + "▌")
                elif event.get("type") == "done":
                    response = event.get("response")
            if not isinstance(response, dict):
                raise RuntimeError("Không nhận được response hoàn chỉnh từ chatbot.")
            answer = answer or str(response.get("reply", ""))
            placeholder.markdown(answer)
        except Exception:
            answer = "Xin lỗi, hệ thống đang gặp sự cố. Vui lòng thử lại hoặc báo cho người phụ trách."
            placeholder.error(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
    if response is not None:
        st.session_state.last_response = response
    st.rerun()


if __name__ == "__main__":
    run_app()
