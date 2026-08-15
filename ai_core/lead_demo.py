"""Nhập từng câu và xem trực tiếp kết quả HOA-14, hoàn toàn offline."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from ai_core.chat import LLMResult, chat


_SOURCE = [{
    "chunk_id": "hoa14-demo",
    "content": "Dữ liệu tư vấn mô phỏng dùng riêng để kiểm tra HOA-14.",
    "url": "https://example.test/hoa14-demo",
    "score": 0.99,
    "metadata": {"title": "Dữ liệu thử HOA-14"},
}]


def _retrieve(query: str, _tenant_id: str) -> list[dict]:
    return [] if query.strip().casefold().splitlines()[-1].startswith("/bi") else _SOURCE


def _generate(_config, _system_prompt, messages) -> LLMResult:
    question = messages[-1]["content"].strip()
    return LLMResult(
        f"Dạ, em đã nhận câu hỏi: “{question}”. Em có thể tư vấn thêm cho anh/chị ạ.",
        "hoa14-offline-demo",
        0,
        0,
    )


def main() -> None:
    history: list[dict[str, str]] = []
    conversation_id = str(uuid4())
    print(
        "=== THỬ HOA-14 TRỰC TIẾP (OFFLINE) ===\n"
        "Nhập từng câu để xem reply, need_human và lead_captured.\n"
        "/bi <câu hỏi>: mô phỏng bot bí | /reset: xóa lịch sử | /quit: thoát\n"
    )

    with (
        patch("ai_core.chat.retrieve", side_effect=_retrieve),
        patch("ai_core.chat._generate_with_fallback", side_effect=_generate),
        patch("ai_core.chat._generate_stream_with_fallback", side_effect=_generate),
        patch("ai_core.chat.log_trace"),
        patch("ai_core.chat.check_output", return_value={"blocked": False, "reason": None}),
        patch(
            "ai_core.guardrail.input._configured_small_model_check",
            return_value={"injection": False, "harmful": False, "upset": False},
        ),
    ):
        while True:
            try:
                message = input("\nBạn: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nĐã thoát.")
                return
            if not message:
                continue
            if message.casefold() == "/quit":
                print("Đã thoát.")
                return
            if message.casefold() == "/reset":
                history.clear()
                conversation_id = str(uuid4())
                print("--- Đã xóa lịch sử ---")
                continue

            response = chat({
                "tenant_id": "mima_internal",
                "conversation_id": conversation_id,
                "message": message,
                "history": history,
                "config_version": 1,
            })
            print(f"Bot: {response['reply']}")
            print(f"need_human    = {response['need_human']}")
            print(f"lead_captured = {response['lead_captured']}")
            history.extend([
                {"role": "user", "content": message},
                {"role": "assistant", "content": response["reply"]},
            ])


if __name__ == "__main__":
    main()
