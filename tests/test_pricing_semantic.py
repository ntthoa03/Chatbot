from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_core.chat import LLMResult, chat
from ai_core.config import load_config
from ai_core.guardrail.output import check_output
from ai_core.guardrail.pricing_semantic import (
    extract_customer_budgets_semantic,
    may_contain_customer_budget,
)


class PricingSemanticUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config("mima_internal", 1)

    def test_candidate_filter_covers_natural_budget_language(self) -> None:
        for message in (
            "app nay e chi co hai chuc do lai",
            "tầm mười lăm củ làm được gì",
            "budget max thirty million",
            "mình đầu tư cỡ 15-30tr",
        ):
            with self.subTest(message=message):
                self.assertTrue(may_contain_customer_budget([message]))

    def test_model_extracts_slang_budget_into_vnd(self) -> None:
        result = extract_customer_budgets_semantic(
            ["app nay e chi co hai chuc do lai"],
            self.config,
            semantic_checker=lambda _items: {
                "is_customer_budget": True,
                "amounts_vnd": [20_000_000],
                "confidence": 0.97,
            },
        )
        self.assertEqual(result["amounts_vnd"], [20_000_000])
        self.assertEqual(result["semantic_check"]["status"], "checked")

    def test_low_confidence_or_invalid_model_cannot_open_guardrail(self) -> None:
        low = extract_customer_budgets_semantic(
            ["hình như tầm hai chục"],
            self.config,
            semantic_checker=lambda _items: {
                "is_customer_budget": True,
                "amounts_vnd": [20_000_000],
                "confidence": 0.4,
            },
        )
        invalid = extract_customer_budgets_semantic(
            ["hình như tầm hai chục"],
            self.config,
            semantic_checker=lambda _items: {"amounts_vnd": [20_000_000]},
        )
        self.assertEqual(low["amounts_vnd"], [])
        self.assertEqual(invalid["amounts_vnd"], [])
        self.assertEqual(invalid["semantic_check"]["status"], "error")

    def test_trusted_budget_only_allows_echo_or_refusal_not_service_price(self) -> None:
        safe = (
            "Gói thiết kế app cần báo giá riêng; em chưa thể xác nhận mức "
            "20 triệu nếu chưa rõ tính năng."
        )
        unsafe = "Gói thiết kế app bên em có giá 20 triệu."
        kwargs = {
            "evidence": [],
            "conversation_evidence": ["app nay e chi co hai chuc do lai"],
            "trusted_customer_budgets": [20_000_000],
        }
        self.assertEqual(
            check_output(safe, self.config, **kwargs),
            {"blocked": False, "reason": None},
        )
        self.assertEqual(
            check_output(unsafe, self.config, **kwargs),
            {"blocked": True, "reason": "unauthorized_price"},
        )


class PricingSemanticChatTests(unittest.TestCase):
    @patch("ai_core.chat.cache_is_enabled", return_value=False)
    @patch(
        "ai_core.chat.retrieve",
        return_value=[
            {
                "chunk_id": "app-scope",
                "content": "Thiết kế app cần khảo sát tính năng và phạm vi triển khai.",
                "url": "https://example.com/app",
                "score": 0.9,
                "metadata": {"title": "Thiết kế app", "url": "https://example.com/app"},
            }
        ],
    )
    @patch("ai_core.chat.check_input", return_value={"blocked": False, "reason": None})
    @patch(
        "ai_core.chat._generate_with_fallback",
        return_value=LLMResult(
            "Gói thiết kế app cần báo giá riêng; em chưa thể xác nhận mức 20 triệu nếu chưa rõ tính năng.",
            "gemini-3.5-flash-lite",
            100,
            20,
        ),
    )
    @patch("ai_core.chat.pricing_semantic_is_enabled", return_value=True)
    @patch("ai_core.chat.may_contain_customer_budget", return_value=True)
    @patch(
        "ai_core.chat.extract_customer_budgets_semantic",
        return_value={
            "amounts_vnd": [20_000_000],
            "semantic_check": {
                "status": "checked",
                "is_customer_budget": True,
                "candidate_amounts_vnd": [20_000_000],
                "confidence": 0.97,
                "min_confidence": 0.85,
                "model": "gemini-3.5-flash-lite",
                "tokens_in": 7,
                "tokens_out": 3,
                "error": None,
            },
        },
    )
    @patch("ai_core.chat.output_semantic_is_enabled", return_value=False)
    @patch("ai_core.chat.log_trace")
    def test_chat_rechecks_blocked_budget_reply_with_structured_budget(
        self,
        log_trace,
        *_mocks,
    ) -> None:
        response = chat(
            {
                "tenant_id": "mima_internal",
                "conversation_id": "b3e1e2b0-1234-4a11-8b11-000000000031",
                "message": "app nay e chi co hai chuc do lai",
                "config_version": 1,
            }
        )
        self.assertFalse(response["guardrail"]["blocked"])
        self.assertIn("20 triệu", response["reply"])
        self.assertEqual(response["usage"]["tokens_in"], 107)
        self.assertEqual(response["usage"]["tokens_out"], 23)
        trace = log_trace.call_args.args[0]
        self.assertEqual(
            trace["guardrails"]["pricing_semantic"][0]["candidate_amounts_vnd"],
            [20_000_000],
        )


if __name__ == "__main__":
    unittest.main()
