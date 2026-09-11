"""Streamlit UI nội bộ cho HOA-09.

Chạy từ thư mục gốc bằng:
    streamlit run app.py
"""

from __future__ import annotations

import hmac
import json
import os
import time
from pathlib import Path
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
from ai_core.retriever import use_index_dir
from handoff import (
    confirmed_handoff_contact,
    create_handoff,
)
from ingestion.profile_generator import PROFILE_FIELDS
from ingestion.chat_answer_save import (
    ChatAnswerSaveError,
    generalize_chat_pair,
    save_generalized_chat_answer,
)
from ingestion.profile_completion import (
    ProfileCompletionError,
    build_completion,
    load_gap_report,
    load_review_records,
)
from ingestion.profile_review import (
    FIELD_LABELS,
    ProfileReviewError,
    estimate_review_seconds,
    load_profile,
    parse_edited_value,
    save_review_decision,
    suggested_text,
)
from ingestion.tenant_answer_ingestion import TenantAnswerError
from ingestion.document_loader import DocumentLoadError, load_documents
from ingestion.document_test_index import DocumentTestIndexError, build_document_test_index


TENANT_ID = os.getenv("AI_CORE_UI_TENANT_ID", "mima_internal")
CONFIG_VERSION = int(os.getenv("AI_CORE_UI_CONFIG_VERSION", "1"))
ACCESS_CODE = os.getenv("AI_CORE_UI_ACCESS_CODE", "").strip()


def _profile_draft_path() -> Path:
    return Path(
        os.getenv(
            "AI_CORE_PROFILE_DRAFT_PATH",
            f"outputs/h4_08/tenants/{TENANT_ID}/business_profile.draft.json",
        )
    )


def _gap_cluster_path() -> Path:
    return Path(os.getenv("AI_CORE_GAP_CLUSTER_PATH", "outputs/h4_02/knowledge_gap_clusters.json"))


def _profile_review_path(tenant_id: str) -> Path:
    root = Path(os.getenv("AI_CORE_PROFILE_REVIEW_ROOT", "outputs/h4_09/reviews"))
    return root / f"{tenant_id}.jsonl"


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
    st.session_state.knowledge_save_open = set()
    st.session_state.knowledge_save_submitted = set()
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
    if "knowledge_save_open" not in st.session_state:
        st.session_state.knowledge_save_open = set()
    if "knowledge_save_submitted" not in st.session_state:
        st.session_state.knowledge_save_submitted = set()
    if "document_upload_id" not in st.session_state:
        st.session_state.document_upload_id = uuid4().hex
    if "document_upload_preview" not in st.session_state:
        st.session_state.document_upload_preview = None
    if "ui_test_index_dir" not in st.session_state:
        st.session_state.ui_test_index_dir = None


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


def _render_knowledge_save_button(item: dict[str, Any], index: int) -> None:
    """H4-14 requires an editable, generalized draft before indexing."""

    question = str(item.get("question", "")).strip()
    answer = str(item.get("content", "")).strip()
    if not question or not answer:
        return
    response = item.get("response") if isinstance(item.get("response"), dict) else {}
    save_key = str(response.get("trace_id") or f"message-{index}")
    if save_key in st.session_state.knowledge_save_submitted:
        st.button(
            "✅ Đã lưu để bot dùng lần sau",
            key=f"knowledge-save-done-{save_key}",
            disabled=True,
        )
        return
    if save_key not in st.session_state.knowledge_save_open:
        if st.button(
            "Lưu câu này để bot tự trả lời lần sau",
            key=f"knowledge-save-open-{save_key}",
            help="Mở bản nháp để bỏ tên khách và ngữ cảnh riêng trước khi nạp vào kho tri thức.",
        ):
            st.session_state.knowledge_save_open.add(save_key)
            st.rerun()
        return

    draft_question, draft_answer = generalize_chat_pair(question, answer)
    st.info("Hãy sửa thành kiến thức dùng được cho mọi khách. Không lưu tên, số điện thoại hoặc tình huống riêng.")
    general_question = st.text_area(
        "Câu hỏi tổng quát",
        value=draft_question,
        key=f"knowledge-question-{save_key}",
        height=90,
    )
    general_answer = st.text_area(
        "Câu trả lời tổng quát",
        value=draft_answer,
        key=f"knowledge-answer-{save_key}",
        height=140,
    )
    confirmed = st.checkbox(
        "Tôi đã kiểm tra và loại bỏ tên khách, thông tin liên hệ và ngữ cảnh riêng.",
        key=f"knowledge-confirm-{save_key}",
    )
    save_col, cancel_col = st.columns(2)
    if save_col.button(
        "Xác nhận và lưu vào kho tri thức",
        key=f"knowledge-save-confirm-{save_key}",
        type="primary",
        use_container_width=True,
    ):
        try:
            receipt = save_generalized_chat_answer(
                TENANT_ID,
                general_question,
                general_answer,
                confirmed=confirmed,
            )
        except (ChatAnswerSaveError, TenantAnswerError, OSError, ValueError) as exc:
            st.warning(str(exc))
        else:
            st.session_state.knowledge_save_submitted.add(save_key)
            st.session_state.knowledge_save_open.discard(save_key)
            st.success(
                f"Đã nạp và hỏi lại thành công trong {receipt['elapsed_seconds']:.2f} giây."
            )
            st.rerun()
    if cancel_col.button(
        "Huỷ lưu kiến thức",
        key=f"knowledge-save-cancel-{save_key}",
        use_container_width=True,
    ):
        st.session_state.knowledge_save_open.discard(save_key)
        st.rerun()


def _render_sidebar(config: Any) -> tuple[str | None, str]:
    response = st.session_state.last_response
    with st.sidebar:
        active_test_index = st.session_state.get("ui_test_index_dir")
        if active_test_index:
            st.success("Đang dùng kho tài liệu TEST")
            st.caption(str(active_test_index))
            if st.button("Tắt kho test, quay lại kho chính", use_container_width=True):
                st.session_state.ui_test_index_dir = None
                reset_conversation()
                st.rerun()
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


def _init_profile_review(profile: Any) -> None:
    """Create isolated H4-09 session state without touching chat state."""

    if st.session_state.get("profile_review_tenant") == profile.tenant_id:
        return
    st.session_state.profile_review_tenant = profile.tenant_id
    st.session_state.profile_review_index = 0
    st.session_state.profile_review_decisions = {}
    st.session_state.profile_review_started = time.perf_counter()
    st.session_state.profile_review_editing = None


def _record_profile_decision(
    profile: Any,
    field_name: str,
    action: str,
    final_value: Any,
) -> None:
    field = getattr(profile, field_name)
    elapsed = time.perf_counter() - float(st.session_state.profile_review_started)
    save_review_decision(
        tenant_id=profile.tenant_id,
        field_name=field_name,
        action=action,
        proposed_value=field.model_dump(mode="json")["value"],
        final_value=final_value,
        citations=[item.model_dump(mode="json") for item in field.citations],
        elapsed_seconds=elapsed,
    )
    st.session_state.profile_review_decisions[field_name] = action
    st.session_state.profile_review_index += 1
    st.session_state.profile_review_editing = None
    st.rerun()


def _render_profile_completion() -> None:
    """Render H4-13 from weighted H4-02 demand, never from schema field count."""

    try:
        report = load_gap_report(_gap_cluster_path())
        records = load_review_records(_profile_review_path(TENANT_ID), TENANT_ID)
        completion = build_completion(report, TENANT_ID, records)
    except (ProfileCompletionError, OSError, ValueError) as exc:
        st.warning(f"Chưa tính được mức hoàn thiện: {exc}")
        return
    if completion["status"] == "no_gap_data":
        st.info(completion["message"])
        return
    percent = float(completion["completion_percent"])
    st.markdown("### Mức hoàn thiện theo nhu cầu khách hỏi")
    st.progress(percent / 100, text=f"{percent:.1f}% chủ đề hỏi thực tế đã được xác nhận")
    st.caption(
        f"Tính theo tần suất H4-02: {completion['resolved_frequency']}/"
        f"{completion['total_frequency']} lượt hỏi thiếu đã có thông tin được tenant xác nhận."
    )
    if completion["next_actions"]:
        next_action = completion["next_actions"][0]
        st.markdown(f"**Việc tiếp theo:** {next_action['action']}")
        st.write(next_action["benefit_text"])
        st.caption(f"Thời gian ước tính: khoảng {next_action['estimated_minutes']} phút.")


def _render_profile_review_page() -> None:
    st.title("Xác nhận hồ sơ doanh nghiệp")
    st.caption(
        "Bản nháp H4-08 — xem từng mục, đối chiếu nguồn rồi chọn Đúng, Sửa, "
        "Bỏ qua hoặc Hỏi lại sau."
    )
    try:
        profile = load_profile(_profile_draft_path(), TENANT_ID)
    except (ProfileReviewError, OSError, ValueError) as exc:
        st.error(str(exc))
        return

    _init_profile_review(profile)
    _render_profile_completion()
    estimated_total = estimate_review_seconds(profile)
    st.caption(f"Ước tính hoàn tất toàn bộ: {estimated_total // 60} phút {estimated_total % 60} giây.")
    index = int(st.session_state.profile_review_index)
    decisions = st.session_state.profile_review_decisions
    if index >= len(PROFILE_FIELDS):
        elapsed = time.perf_counter() - float(st.session_state.profile_review_started)
        confirmed = sum(action in {"confirmed", "edited"} for action in decisions.values())
        deferred = sum(action in {"skipped", "deferred"} for action in decisions.values())
        st.success("Đã duyệt xong 6/6 mục hồ sơ.")
        st.metric("Thời gian phiên duyệt", f"{elapsed / 60:.1f} phút")
        st.write(f"Đã xác nhận/sửa: {confirmed} mục · Bỏ qua/hỏi lại sau: {deferred} mục")
        if st.button("Duyệt lại từ đầu", key="profile-review-restart"):
            st.session_state.profile_review_tenant = None
            st.rerun()
        return

    field_name = PROFILE_FIELDS[index]
    field = getattr(profile, field_name)
    proposal = suggested_text(field_name, field.value)
    remaining_seconds = sum(
        35 if getattr(profile, name).value is not None else 50
        for name in PROFILE_FIELDS[index:]
    )

    st.progress((index + 1) / len(PROFILE_FIELDS), text=f"Mục {index + 1}/{len(PROFILE_FIELDS)}")
    st.subheader(FIELD_LABELS[field_name])
    st.caption(f"Ước tính còn lại: khoảng {remaining_seconds // 60 + 1} phút")
    st.markdown("**Đáp án đề xuất**")
    st.code(proposal, language=None)
    st.caption(f"Độ tin cậy từ H4-08: {field.confidence:.0%} · {field.note}")

    st.markdown("**Nguồn trích dẫn**")
    if field.citations:
        for citation_index, citation in enumerate(field.citations, start=1):
            st.markdown(f"{citation_index}. [{citation.title}]({citation.url})")
            st.markdown(f"> {citation.evidence_quote}")
            st.caption(f"Chunk: {citation.chunk_id}")
    else:
        st.info(
            "Nguồn crawl H4-08 chưa có bằng chứng đủ tin cậy cho mục này. "
            "Đây là lý do hệ thống đề xuất tenant bổ sung hoặc hỏi lại sau."
        )

    if st.session_state.profile_review_editing == field_name:
        edited_text = st.text_area(
            "Nội dung chỉnh sửa",
            value=proposal,
            key=f"profile-review-text-{field_name}",
            height=160,
        )
        save_col, cancel_col = st.columns(2)
        if save_col.button(
            "Lưu bản sửa",
            key=f"profile-review-save-{field_name}",
            type="primary",
            use_container_width=True,
        ):
            try:
                edited_value = parse_edited_value(field_name, edited_text)
                _record_profile_decision(profile, field_name, "edited", edited_value)
            except ProfileReviewError as exc:
                st.warning(str(exc))
        if cancel_col.button(
            "Huỷ sửa",
            key=f"profile-review-cancel-{field_name}",
            use_container_width=True,
        ):
            st.session_state.profile_review_editing = None
            st.rerun()
        return

    correct_col, edit_col, skip_col, defer_col = st.columns(4)
    if correct_col.button(
        "✅ Đúng",
        key=f"profile-review-confirm-{field_name}",
        type="primary",
        use_container_width=True,
    ):
        _record_profile_decision(profile, field_name, "confirmed", field.value)
    if edit_col.button(
        "✏️ Sửa",
        key=f"profile-review-edit-{field_name}",
        use_container_width=True,
    ):
        st.session_state.profile_review_editing = field_name
        st.rerun()
    if skip_col.button(
        "⏭️ Bỏ qua",
        key=f"profile-review-skip-{field_name}",
        use_container_width=True,
    ):
        _record_profile_decision(profile, field_name, "skipped", None)
    if defer_col.button(
        "🕒 Hỏi lại sau",
        key=f"profile-review-defer-{field_name}",
        use_container_width=True,
    ):
        _record_profile_decision(profile, field_name, "deferred", None)


def _render_document_test_page(config: Any) -> None:
    """H4-04/H4-05 upload preview and isolated test-index activation."""

    st.title("Nạp tài liệu vào kho test")
    st.caption(
        "Tài liệu chỉ được thêm vào index riêng của phiên test này. "
        "Kho chính index/ không bị sửa."
    )
    uploaded = st.file_uploader(
        "Chọn PDF, Word, Excel, CSV hoặc ảnh",
        type=["pdf", "docx", "xlsx", "csv", "png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"],
        accept_multiple_files=True,
        help="PDF scan và ảnh sẽ gọi Gemini Vision, sau đó OpenAI Vision nếu cần.",
    )
    if st.button(
        "1. Trích xuất và xem trước",
        type="primary",
        disabled=not uploaded,
        use_container_width=True,
    ):
        upload_root = (
            Path("outputs/h4_04/ui_uploads") / str(st.session_state.document_upload_id)
        )
        upload_root.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        seen: set[str] = set()
        try:
            for position, item in enumerate(uploaded, start=1):
                safe_name = Path(str(item.name)).name
                if not safe_name or safe_name in {".", ".."}:
                    raise DocumentLoadError("Tên file upload không hợp lệ.")
                if safe_name.casefold() in seen:
                    safe_name = f"{position}-{safe_name}"
                seen.add(safe_name.casefold())
                path = upload_root / safe_name
                path.write_bytes(item.getvalue())
                paths.append(path)
            with st.spinner("Đang đọc tài liệu và OCR nếu cần…"):
                results = load_documents(paths, TENANT_ID, ocr_reviewed=False)
        except (DocumentLoadError, OSError, ValueError) as exc:
            st.session_state.document_upload_preview = None
            st.error(str(exc))
        else:
            # A confirmation belongs only to the exact extraction just shown;
            # never carry an OCR approval over to a later upload.
            st.session_state["document-ocr-confirmed"] = False
            st.session_state.document_upload_preview = {
                "documents": [result.manifest() for result in results],
                "chunks": [chunk for result in results for chunk in result.chunks],
                "used_ocr": any(result.used_ocr for result in results),
            }
            st.success(
                f"Đã trích {sum(len(result.chunks) for result in results)} chunks "
                f"từ {len(results)} tài liệu."
            )

    preview = st.session_state.get("document_upload_preview")
    if not isinstance(preview, dict):
        st.info("Chọn tài liệu rồi bấm bước 1 để xem nội dung trước khi nạp.")
        return

    st.subheader("Nội dung chuẩn bị nạp")
    for document in preview.get("documents", []):
        st.write(
            f"**{Path(str(document['path'])).name}** · {document['file_type']} · "
            f"{document['chunk_count']} chunks · OCR={document['used_ocr']}"
        )
    for index, chunk in enumerate(preview.get("chunks", []), start=1):
        metadata = chunk.get("metadata", {})
        with st.expander(f"Chunk {index}: {metadata.get('title', 'Không có tiêu đề')}"):
            st.code(str(chunk.get("content", "")), language=None)
            st.json({
                "tenant_id": chunk.get("tenant_id"),
                "chunk_id": chunk.get("chunk_id"),
                "metadata": metadata,
            })

    ocr_confirmed = True
    if preview.get("used_ocr"):
        st.warning("Có nội dung OCR. Hãy đối chiếu ảnh/PDF gốc trước khi tiếp tục.")
        ocr_confirmed = st.checkbox(
            "Tôi đã kiểm tra nội dung OCR và xác nhận có thể dùng để test.",
            key="document-ocr-confirmed",
        )
    if st.button(
        "2. Xác nhận và nạp vào kho TEST",
        disabled=not ocr_confirmed,
        use_container_width=True,
    ):
        base_index = Path(config.knowledge.local_index_dir)
        output_dir = (
            Path("outputs/h4_04/ui_test_indexes")
            / str(st.session_state.document_upload_id)
            / uuid4().hex
        )
        try:
            with st.spinner("Đang tạo embedding và index test…"):
                receipt = build_document_test_index(
                    TENANT_ID,
                    preview["chunks"],
                    base_index_dir=base_index,
                    output_dir=output_dir,
                )
                (output_dir / "document-chunks.json").write_text(
                    json.dumps(preview["chunks"], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except (DocumentTestIndexError, OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.ui_test_index_dir = receipt["index_dir"]
            reset_conversation()
            st.success(
                f"Đã bật kho test: {receipt['base_chunks']} chunks cũ + "
                f"{receipt['document_chunks']} chunks tài liệu = {receipt['total_chunks']} chunks."
            )
            st.caption(
                f"Embedding mới: {receipt['embedded_new']} · lấy từ cache: {receipt['cache_hits']}"
            )
            st.info("Chuyển sang khu vực Chatbot để hỏi thử. Thanh bên sẽ báo đang dùng kho TEST.")


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

    area = st.sidebar.radio(
        "Khu vực",
        options=("Chatbot", "Nạp tài liệu test", "Duyệt hồ sơ"),
        key="main-area",
    )
    if area == "Duyệt hồ sơ":
        _render_profile_review_page()
        return
    if area == "Nạp tài liệu test":
        _render_document_test_page(config)
        return

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
                _render_knowledge_save_button(item, index)

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
            with use_index_dir(st.session_state.get("ui_test_index_dir")):
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
