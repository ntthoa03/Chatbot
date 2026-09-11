from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from ingestion.chat_answer_save import (
    ChatAnswerSaveError,
    generalize_chat_pair,
    save_generalized_chat_answer,
    validate_generalized_pair,
)


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


class H414ChatKnowledgeSaveTests(unittest.TestCase):
    def test_draft_removes_customer_identity_contact_and_chat_cta(self) -> None:
        question, answer = generalize_chat_pair(
            "Tôi là Nguyễn Văn An, số 0912 345 678 muốn hỏi phí duy trì?",
            "Chào anh An. Phí duy trì là 2 triệu mỗi năm. Anh có muốn em gọi lại không?",
        )
        combined = question + answer
        self.assertNotIn("Nguyễn Văn An", combined)
        self.assertNotIn("0912", combined)
        self.assertNotIn("Chào anh An", combined)
        self.assertNotIn("gọi lại", combined)
        self.assertIn("2 triệu", answer)

    def test_validation_requires_confirmation_and_blocks_pii(self) -> None:
        with self.assertRaisesRegex(ChatAnswerSaveError, "xác nhận"):
            validate_generalized_pair("Phí duy trì?", "2 triệu", confirmed=False)
        with self.assertRaisesRegex(ChatAnswerSaveError, "số điện thoại"):
            validate_generalized_pair(
                "Phí duy trì?", "Gọi khách theo số 0912345678", confirmed=True
            )

    def test_confirmed_general_answer_uses_h4_11_ingestion(self) -> None:
        ingest = Mock(return_value={"elapsed_seconds": 0.2, "verified_source": "tenant_provided"})
        receipt = save_generalized_chat_answer(
            "mima_internal", "Phí duy trì website?", "Phí là 2 triệu mỗi năm.",
            confirmed=True, ingest_fn=ingest,
        )
        ingest.assert_called_once_with(
            "mima_internal", "Phí duy trì website?", "Phí là 2 triệu mỗi năm."
        )
        self.assertEqual(receipt["verified_source"], "tenant_provided")

    def test_ui_requires_edit_confirmation_before_save(self) -> None:
        response = {
            "reply": "Chào anh An. Phí duy trì là 2 triệu mỗi năm. Anh có muốn em gọi 0912345678 không?",
            "sources": [], "tool_calls": [], "need_human": False,
            "guardrail": {"blocked": False},
            "usage": {"model": "mock", "tokens_in": 1, "tokens_out": 1,
                      "cost_usd": 0.0, "latency_ms": 1},
            "trace_id": "trace-h4-14",
        }

        def fake_chat(*_args, **_kwargs):
            return iter([
                {"type": "delta", "delta": response["reply"]},
                {"type": "done", "response": response},
            ])

        receipt = {
            "elapsed_seconds": 0.2, "verified_source": "tenant_provided",
            "verified_top_chunk_id": "tenant-answer",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "AI_CORE_UI_ACCESS_CODE": " ",
            "AI_CORE_FEEDBACK_PATH": str(Path(directory) / "feedback.jsonl"),
            "AI_CORE_SALE_TURNS_PATH": str(Path(directory) / "turns.jsonl"),
        }), patch("ai_core.chat.chat", side_effect=fake_chat), patch(
            "ingestion.chat_answer_save.ingest_tenant_answer", return_value=receipt
        ) as ingest:
            app = AppTest.from_file(str(APP)).run(timeout=15)
            app.text_input[0].set_value("Nhân viên test").run(timeout=15)
            app.selectbox[0].select("primary").run(timeout=15)
            app.chat_input[0].set_value(
                "Tôi là Nguyễn Văn An, số 0912 345 678 muốn hỏi phí duy trì?"
            ).run(timeout=15)
            next(
                button for button in app.button
                if button.label == "Lưu câu này để bot tự trả lời lần sau"
            ).click().run(timeout=15)
            self.assertEqual(len(app.text_area), 2)
            combined = " ".join(item.value for item in app.text_area)
            self.assertNotIn("Nguyễn Văn An", combined)
            self.assertNotIn("0912", combined)
            next(
                button for button in app.button
                if button.label == "Xác nhận và lưu vào kho tri thức"
            ).click().run(timeout=15)
            self.assertTrue(any("xác nhận" in item.value for item in app.warning))
            self.assertEqual(ingest.call_count, 0)
            app.checkbox[0].check().run(timeout=15)
            next(
                button for button in app.button
                if button.label == "Xác nhận và lưu vào kho tri thức"
            ).click().run(timeout=15)

        self.assertEqual(list(app.exception), [])
        self.assertEqual(ingest.call_count, 1)
        self.assertTrue(any("Đã lưu để bot dùng lần sau" in item.label for item in app.button))


if __name__ == "__main__":
    unittest.main()
