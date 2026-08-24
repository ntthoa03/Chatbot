"""Streamlit UI nội bộ cho HOA-09.

Chạy từ thư mục gốc bằng:
    streamlit run app.py
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Iterable
from uuid import uuid4

import streamlit as st

from ai_core.chat import chat
from ai_core.config import load_config
from ai_core.feedback import (
    load_feedback,
    log_sale_turn,
    sale_turns_path,
    sale_usage_stats,
    save_bad_feedback,
)
from ai_core.trace import find_trace
from handoff import (
    confirmed_handoff_contact,
    create_handoff,
)


TENANT_ID = os.getenv("AI_CORE_UI_TENANT_ID", "mima_internal")
CONFIG_VERSION = int(os.getenv("AI_CORE_UI_CONFIG_VERSION", "1"))
ACCESS_CODE = os.getenv("AI_CORE_UI_ACCESS_CODE", "").strip()


def _require_access_code() -> bool:
    """Protect tunneled test UI when an access code is configured."""

    if not ACCESS_CODE or st.session_state.get("ui_access_granted", False):
        return True
    try:
        display_name = load_config(TENANT_ID, CONFIG_VERSION).bot_name
    except Exception:
        display_name = "Chatbot"
    st.title(f"🔒 {display_name} — Sale Test")
    st.caption("Nhập mã truy cập do người phụ trách H2-12 cung cấp.")
    entered = st.text_input("Mã truy cập", type="password", key="ui_access_code")
    if st.button("Vào giao diện", type="primary"):
        if hmac.compare_digest(entered, ACCESS_CODE):
            st.session_state.ui_access_granted = True
            st.rerun()
        else:
            st.error("Mã truy cập không đúng.")
    return False


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
    st.session_state.feedback_submitted = set()
    st.session_state.feedback_open = set()
    st.session_state.conversation_id = str(uuid4())


def _init_session() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_response" not in st.session_state:
        st.session_state.last_response = None
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = str(uuid4())
    if "feedback_submitted" not in st.session_state:
        st.session_state.feedback_submitted = set()
    if "feedback_open" not in st.session_state:
        st.session_state.feedback_open = set()


def _render_bad_feedback_button(item: dict[str, Any], index: int) -> None:
    response = item.get("response")
    if not isinstance(response, dict):
        return
    trace_id = str(response.get("trace_id", "")).strip()
    feedback_key = trace_id or f"message-{index}"
    submitted = feedback_key in st.session_state.feedback_submitted
    opened = feedback_key in st.session_state.feedback_open
    if submitted:
        st.button(
            "✅ Đã gửi feedback",
            key=f"bad-feedback-done-{feedback_key}",
            disabled=True,
        )
        return
    if not opened:
        if st.button(
            "👎 Câu trả lời này tệ",
            key=f"bad-feedback-{feedback_key}",
            help="Mở ô nhập câu trả lời mong muốn để người phụ trách có đủ thông tin sửa bot.",
        ):
            st.session_state.feedback_open.add(feedback_key)
            st.rerun()
        return

    suggested_reply = st.text_area(
        "Sale mong bot trả lời thế nào?",
        key=f"bad-feedback-suggestion-{feedback_key}",
        placeholder=(
            "Ví dụ: Bot nên trả lời gói 12 triệu đã có SSL; hoặc ghi rõ phần nào sai. "
            "Nếu chưa biết đáp án, nhập: cần kiểm tra lại thông tin."
        ),
        help="Ghi câu trả lời đề xuất hoặc hướng sửa cụ thể; không nhập dữ liệu nhạy cảm.",
    )
    send_col, cancel_col = st.columns(2)
    send_clicked = send_col.button(
        "Gửi feedback",
        key=f"bad-feedback-send-{feedback_key}",
        type="primary",
        use_container_width=True,
    )
    if cancel_col.button(
        "Huỷ",
        key=f"bad-feedback-cancel-{feedback_key}",
        use_container_width=True,
    ):
        st.session_state.feedback_open.discard(feedback_key)
        st.rerun()
    if send_clicked:
        if not suggested_reply.strip():
            st.warning("Vui lòng nhập câu trả lời mong muốn hoặc mô tả phần cần sửa trước khi gửi.")
            return
        try:
            record, created = save_bad_feedback(
                question=str(item.get("question", "")),
                reply=str(item.get("content", "")),
                response=response,
                conversation_id=st.session_state.conversation_id,
                tenant_id=TENANT_ID,
                config_version=CONFIG_VERSION,
                tester_name=str(st.session_state.get("tester_name", "")),
                suggested_reply=suggested_reply,
            )
        except OSError:
            st.error("Chưa gửi được đánh giá. Vui lòng báo người phụ trách kiểm tra máy chủ.")
            return
        st.session_state.feedback_submitted.add(feedback_key)
        st.session_state.feedback_open.discard(feedback_key)
        if created:
            st.success(f"Đã gửi cho người phụ trách · mã {record['feedback_id']}")
        else:
            st.info(f"Đánh giá này đã được ghi nhận · mã {record['feedback_id']}")


def _render_sidebar(config: Any) -> tuple[str | None, str]:
    response = st.session_state.last_response
    with st.sidebar:
        st.header("Cấu hình thử nghiệm")
        tester_name = st.text_input(
            "Tên người test",
            key="tester_name",
            placeholder="Ví dụ: Sale Lan",
            help="Dùng tên/biệt danh để tổng hợp số người đã tham gia H2-12.",
        ).strip()
        if tester_name:
            stats = sale_usage_stats(load_feedback(sale_turns_path()))
            progress = stats["by_tester"].get(
                tester_name, {"conversations": 0, "turns": 0}
            )
            st.caption(
                f"Tiến độ H2-12: {progress['conversations']}/10 hội thoại · "
                f"{progress['turns']} lượt hỏi"
            )
        model_role = st.selectbox(
            "Model",
            options=("auto", "primary", "fallback"),
            index=0,
            placeholder="Chọn routing tự động hoặc model cố định",
            format_func=lambda role: (
                "Tự động — câu dễ model rẻ, câu khó model mạnh"
                if role == "auto"
                else f"Primary — {config.model_primary}"
                if role == "primary"
                else f"Fallback — {config.model_fallback}"
            ),
        )

        st.header("Thông tin lượt trả lời")
        if st.button("🗑️ Xoá hội thoại", use_container_width=True):
            reset_conversation()
            st.rerun()

        if not response:
            st.info("Hãy gửi một câu hỏi để xem nguồn và số liệu.")
            return model_role, tester_name

        usage = response.get("usage", {})
        left, right = st.columns(2)
        left.metric("Token vào", f"{int(usage.get('tokens_in', 0)):,}")
        right.metric("Token ra", f"{int(usage.get('tokens_out', 0)):,}")
        left.metric("Chi phí model ước tính", f"${float(usage.get('cost_usd', 0)):.8f}")
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
        return model_role, tester_name


def run_app() -> None:
    st.set_page_config(
        page_title="AI Chatbot — Internal Test",
        page_icon="💬",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if not _require_access_code():
        return
    _init_session()
    config = load_config(TENANT_ID, CONFIG_VERSION)

    st.title(f"💬 {config.bot_name} — Internal Test")
    st.caption(
        "Giao diện thử nghiệm nội bộ. Câu trả lời có thể cần chuyên viên xác nhận trước khi sử dụng."
    )
    model_role, tester_name = _render_sidebar(config)

    for index, item in enumerate(st.session_state.messages):
        with st.chat_message(item["role"]):
            st.markdown(item["content"])
            if item["role"] == "assistant":
                _render_bad_feedback_button(item, index)

    question = st.chat_input(
        f"Nhập câu hỏi cho {config.bot_name}…",
        disabled=model_role is None or not tester_name,
    )
    if not tester_name:
        st.info("Nhập tên người test trong thanh bên để bắt đầu và ghi nhận đúng người tham gia.")
    elif model_role is None:
        st.info("Chọn Primary hoặc Fallback trong thanh bên để bắt đầu thử nghiệm.")
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
            for event in chat(payload, stream=True, model_role=model_role):
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

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": answer,
        "question": question,
    }
    if response is not None:
        handoff_contact = confirmed_handoff_contact(response)
        if handoff_contact is not None:
            customer_name, customer_phone = handoff_contact
            try:
                handoff_record, _created = create_handoff(
                    tenant_id=TENANT_ID,
                    config_version=CONFIG_VERSION,
                    conversation_id=st.session_state.conversation_id,
                    trace_id=str(response.get("trace_id", "")),
                    tester_name=tester_name,
                    reason="hoa14_need_human_with_confirmed_lead",
                    question=question,
                    reply=answer,
                    messages=[
                        *previous_messages,
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ],
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                )
                # Đây là cờ của lớp UI; hợp đồng và mã nguồn ai_core không bị thay đổi.
                response = dict(response)
                response["need_human"] = True
                assistant_message["response"] = response
                assistant_message["handoff"] = handoff_record
                # Câu chat giữ phần dẫn từ core; mã ticket đã có thẻ trạng thái riêng.
            except OSError:
                # Không tuyên bố đã chuyển nếu hàng đợi không ghi được ticket.
                answer = (
                    "Dạ, hệ thống chưa tạo được yêu cầu chuyển chuyên viên. Anh/chị vui lòng "
                    "liên hệ hotline/Zalo hiển thị trong phần tư vấn hoặc thử lại sau ạ."
                )
                assistant_message["content"] = answer
                response = dict(response)
                response["reply"] = answer
        st.session_state.last_response = response
        assistant_message["response"] = response
        try:
            log_sale_turn(
                tester_name=tester_name,
                question=question,
                reply=answer,
                response=response,
                conversation_id=st.session_state.conversation_id,
                tenant_id=TENANT_ID,
                config_version=CONFIG_VERSION,
            )
        except OSError:
            # Không làm mất câu trả lời nếu hộp log tạm thời không ghi được.
            pass
    st.session_state.messages.append(assistant_message)
    st.rerun()


if __name__ == "__main__":
    run_app()
