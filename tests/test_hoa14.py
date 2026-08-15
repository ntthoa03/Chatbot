from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_core.chat import LLMResult, chat
from ai_core.config import load_config
from ai_core.lead import (
    append_lead_request,
    extract_vietnamese_phone,
    should_request_lead,
)
from ai_core.models import Message


PAYLOAD = {
    "tenant_id": "mima_internal",
    "conversation_id": "b3e1e2b0-1234-4a11-8b11-000000000014",
    "message": "Tư vấn giúp tôi gói website phù hợp.",
    "config_version": 1,
}

SOURCE = [{
    "chunk_id": "service-1",
    "content": "MIMA tư vấn giải pháp website theo nhu cầu của khách hàng.",
    "url": "https://example.test/service",
    "score": 0.91,
    "metadata": {"title": "Dịch vụ website"},
}]


class Hoa14Scenarios(unittest.TestCase):
    @patch("ai_core.chat.log_trace")
    @patch("ai_core.chat.retrieve", return_value=SOURCE)
    @patch("ai_core.chat._generate_with_fallback")
    def test_1_asks_on_configured_third_turn_not_on_first(self, generate, *_mocks) -> None:
        generate.return_value = LLMResult("Dạ, em đang tư vấn cho anh/chị ạ.", "test", 10, 8)
        first = chat(PAYLOAD)
        self.assertNotIn("cho em xin tên", first["reply"].casefold())

        history = [
            {"role": "user", "content": PAYLOAD["message"]},
            {"role": "assistant", "content": first["reply"]},
            {"role": "user", "content": "Tôi cần website bán hàng."},
            {"role": "assistant", "content": "Dạ, em đã hiểu nhu cầu ạ."},
        ]
        third = chat({**PAYLOAD, "message": "Có thể tư vấn thêm không?", "history": history})
        self.assertIn("cho em xin tên và số điện thoại", third["reply"].casefold())

        messages = [Message(**item) for item in history]
        self.assertFalse(
            should_request_lead(messages, "Lượt ba", ask_after_turns=4, max_requests=2)
        )

    def test_2_never_requests_lead_more_than_twice(self) -> None:
        request = append_lead_request("Dạ.")
        history = [
            Message(role="user", content="Lượt 1"),
            Message(role="assistant", content=request),
            Message(role="user", content="Lượt 2"),
            Message(role="assistant", content=request),
            Message(role="user", content="Lượt 3"),
            Message(role="assistant", content="Dạ."),
        ]
        self.assertFalse(
            should_request_lead(history, "Lượt 4", ask_after_turns=3, max_requests=2)
        )

    @patch("ai_core.chat.log_trace")
    @patch("ai_core.chat.retrieve", return_value=SOURCE)
    @patch("ai_core.chat._generate_with_fallback")
    def test_3_confirms_before_returning_lead_captured(self, generate, retrieve, _log) -> None:
        generate.return_value = LLMResult("Dạ, em ghi nhận ạ.", "test", 10, 8)
        supplied = chat({**PAYLOAD, "message": "Tôi tên là Nguyễn Văn Nam, SĐT 0912.345.678"})
        self.assertIsNone(supplied["lead_captured"])
        self.assertIn("xác nhận", supplied["reply"].casefold())
        self.assertIn("0912345678", supplied["reply"])

        confirmed = chat({
            **PAYLOAD,
            "message": "Đúng rồi",
            "history": [
                {"role": "user", "content": "Tôi tên là Nguyễn Văn Nam, SĐT 0912.345.678"},
                {"role": "assistant", "content": supplied["reply"]},
            ],
        })
        self.assertEqual(
            confirmed["lead_captured"],
            {"name": "Nguyễn Văn Nam", "phone": "0912345678"},
        )
        self.assertFalse(confirmed["need_human"])
        self.assertEqual(retrieve.call_count, 0)

    def test_4_rejects_invalid_vietnamese_mobile_numbers(self) -> None:
        self.assertIsNone(extract_vietnamese_phone("SĐT của tôi là 0212345678"))
        self.assertIsNone(extract_vietnamese_phone("SĐT của tôi là 091234567"))
        formats = (
            "0912345678",
            "0912 345 678",
            "0912.345.678",
            "0912-345-678",
            "0912/345/678",
            "(0912) 345 678",
            "+84 912-345-678",
            "84.912.345.678",
        )
        for value in formats:
            with self.subTest(value=value):
                self.assertEqual(extract_vietnamese_phone(value), "0912345678")

    @patch("ai_core.chat.log_trace")
    @patch("ai_core.chat.retrieve")
    def test_invalid_submitted_phone_is_not_mistaken_for_hotline_request(
        self, retrieve, _log
    ) -> None:
        response = chat({
            **PAYLOAD,
            "message": "Toi ten la nguyen van an, sdt 033283444",
        })
        retrieve.assert_not_called()
        self.assertIn("chưa hợp lệ", response["reply"])
        self.assertNotIn("0909 035 333", response["reply"])
        self.assertIsNone(response["lead_captured"])

    @patch("ai_core.chat.log_trace")
    @patch("ai_core.chat.retrieve", return_value=[])
    def test_5_handoff_rules_include_request_contract_upset_and_two_misses(
        self, _retrieve, _log
    ) -> None:
        first = chat({**PAYLOAD, "message": "Một câu ngoài kho dữ liệu"})
        self.assertFalse(first["need_human"])
        self.assertEqual(first["reply"], load_config("mima_internal", 1).lead.no_data_retry_message)

        second = chat({
            **PAYLOAD,
            "message": "Tôi hỏi lại câu ngoài kho dữ liệu",
            "history": [
                {"role": "user", "content": "Một câu ngoài kho dữ liệu"},
                {"role": "assistant", "content": first["reply"]},
            ],
        })
        self.assertTrue(second["need_human"])

        for message in (
            "Cho tôi gặp nhân viên tư vấn",
            "Tôi muốn hỏi về hợp đồng",
            "Tôi muốn khiếu nại dịch vụ",
            "Tôi rất bực mình vì dịch vụ quá tệ",
        ):
            with self.subTest(message=message):
                self.assertTrue(chat({**PAYLOAD, "message": message})["need_human"])


if __name__ == "__main__":
    unittest.main()
