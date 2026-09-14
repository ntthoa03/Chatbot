"""H2-08 — kiểm thử semantic cache, TTL và cách ly tenant hoàn toàn offline."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from ai_core.cache import (
    CacheError,
    SemanticResponseCache,
    is_price_question,
    request_is_cacheable,
    response_is_cacheable,
)
from ai_core.chat import LLMResult, chat


MIMA = "mima_internal"
HYHY = "phongkham_hyhy"


def safe_response(reply: str = "Dạ, gói website có giá 2.000.000đ ạ.") -> dict:
    return {
        "reply": reply,
        "sources": [{"chunk_id": "price-1", "url": "https://mimadigi.com/gia", "score": 0.99}],
        "tool_calls": [],
        "need_human": False,
        "lead_captured": None,
        "guardrail": {"blocked": False, "reason": None},
        "usage": {"model": "test-model", "tokens_in": 10, "tokens_out": 5, "cost_usd": 1.0},
        "trace_id": str(uuid4()),
    }


class SemanticCacheUnitTests(unittest.TestCase):
    def test_similarity_hit_and_below_threshold_miss(self) -> None:
        cache = SemanticResponseCache(similarity_threshold=0.92)
        cache.put(
            tenant_id=MIMA,
            config_version=1,
            question="Thiết kế website bao nhiêu tiền?",
            vector=[1.0, 0.0],
            response=safe_response(),
        )
        hit = cache.lookup(
            tenant_id=MIMA,
            config_version=1,
            question="Làm web giá sao em?",
            vector=[0.93, 0.36756],
        )
        miss = cache.lookup(
            tenant_id=MIMA,
            config_version=1,
            question="SEO tổng thể gồm gì?",
            vector=[0.8, 0.6],
        )
        self.assertTrue(hit.hit)
        self.assertGreaterEqual(hit.similarity or 0.0, 0.92)
        self.assertFalse(miss.hit)

    def test_cache_never_crosses_tenant_or_config_version(self) -> None:
        cache = SemanticResponseCache()
        cache.put(
            tenant_id=MIMA,
            config_version=1,
            question="Giá website?",
            vector=[1.0, 0.0],
            response=safe_response(),
        )
        # Cùng vector tuyệt đối vẫn miss nếu tenant hoặc config version khác.
        self.assertFalse(
            cache.lookup(
                tenant_id=HYHY,
                config_version=1,
                question="Giá khám?",
                vector=[1.0, 0.0],
            ).hit
        )
        self.assertFalse(
            cache.lookup(
                tenant_id=MIMA,
                config_version=2,
                question="Giá website?",
                vector=[1.0, 0.0],
            ).hit
        )

    def test_missing_empty_and_malformed_tenant_are_errors(self) -> None:
        cache = SemanticResponseCache()
        for tenant_id in (None, "", "   ", "../mima_internal", "MIMA"):
            with self.subTest(tenant_id=tenant_id):
                with self.assertRaises(CacheError):
                    cache.lookup(
                        tenant_id=tenant_id,
                        config_version=1,
                        question="Giá web?",
                        vector=[1.0],
                    )

    def test_threshold_lower_than_092_is_rejected(self) -> None:
        with self.assertRaises(CacheError):
            SemanticResponseCache(similarity_threshold=0.919)

    def test_price_ttl_is_shorter_than_normal_ttl(self) -> None:
        now = [100.0]
        cache = SemanticResponseCache(
            default_ttl_seconds=60,
            price_ttl_seconds=5,
            clock=lambda: now[0],
        )
        cache.put(
            tenant_id=MIMA,
            config_version=1,
            question="Làm website bao nhiêu tiền?",
            vector=[1.0, 0.0],
            response=safe_response(),
        )
        cache.put(
            tenant_id=MIMA,
            config_version=1,
            question="Quy trình thiết kế website gồm gì?",
            vector=[0.0, 1.0],
            response=safe_response("Dạ, quy trình gồm tư vấn, thiết kế và bàn giao ạ."),
        )
        now[0] += 6
        self.assertFalse(
            cache.lookup(
                tenant_id=MIMA,
                config_version=1,
                question="Giá website?",
                vector=[1.0, 0.0],
            ).hit
        )
        self.assertTrue(
            cache.lookup(
                tenant_id=MIMA,
                config_version=1,
                question="Quy trình làm web?",
                vector=[0.0, 1.0],
            ).hit
        )

    def test_sensitive_or_contextual_request_is_not_cacheable(self) -> None:
        self.assertFalse(request_is_cacheable("Mã OTP của anh là 123456", []))
        self.assertFalse(request_is_cacheable("Gói đó bao nhiêu?", [{"role": "user"}]))
        self.assertTrue(request_is_cacheable("Làm website bao nhiêu tiền?", []))

    def test_dynamic_or_unsafe_response_is_not_cacheable(self) -> None:
        response = safe_response()
        self.assertTrue(response_is_cacheable(response))
        for field, value in (
            ("need_human", True),
            ("tool_calls", [{"name": "check_domain"}]),
            ("lead_captured", {"phone": "0900000000"}),
            ("sources", []),
        ):
            changed = {**response, field: value}
            self.assertFalse(response_is_cacheable(changed), field)

    def test_missing_knowledge_response_is_never_cached(self) -> None:
        response = safe_response("Hiện em chưa có đủ dữ liệu để trả lời chính xác.")
        self.assertFalse(response_is_cacheable(response))

    def test_price_detection_handles_accented_and_unaccented_text(self) -> None:
        self.assertTrue(is_price_question("Thiết kế website bao nhiêu tiền?"))
        self.assertTrue(is_price_question("bao gia lam web giup a"))
        self.assertFalse(is_price_question("Quy trình bàn giao website thế nào?"))


class ChatCacheIntegrationTests(unittest.TestCase):
    def test_second_identical_request_hits_cache_without_rag_or_model(self) -> None:
        cache = SemanticResponseCache(similarity_threshold=0.92)
        source = {
            "chunk_id": "price-1",
            "content": "Gói Website Basic có giá 2.000.000đ.",
            "url": "https://mimadigi.com/gia",
            "score": 0.99,
            "metadata": {"title": "Bảng giá", "type": "pricing"},
        }
        retrieve_mock = Mock(return_value=[source])
        generate_mock = Mock(
            return_value=LLMResult(
                "Dạ, gói Website Basic có giá 2.000.000đ ạ.",
                "gemini-3.5-flash-lite",
                100,
                20,
            )
        )
        payload = {
            "tenant_id": MIMA,
            "conversation_id": str(uuid4()),
            "message": "Thiết kế website bao nhiêu tiền?",
            "history": [],
            "config_version": 1,
        }
        with (
            patch.dict(os.environ, {"AI_CORE_SEMANTIC_CACHE_ENABLED": "1"}),
            patch("ai_core.chat.get_semantic_cache", return_value=cache),
            patch("ai_core.chat.embed_cache_question", return_value=[1.0, 0.0]),
            patch("ai_core.chat.retrieve", retrieve_mock),
            patch("ai_core.chat._generate_with_fallback", generate_mock),
            patch("ai_core.chat.check_input", return_value={"blocked": False, "reason": None, "need_human": False}),
            patch("ai_core.chat.check_forbidden_request", return_value={"blocked": False, "reason": None, "variant": None, "safe_reply": None}),
            patch("ai_core.chat.check_output", return_value={"blocked": False, "reason": None}),
            patch("ai_core.chat.log_trace"),
        ):
            first = chat(payload)
            second = chat({**payload, "conversation_id": str(uuid4())})

        self.assertEqual(first["reply"], second["reply"])
        self.assertNotEqual(first["trace_id"], second["trace_id"])
        self.assertGreater(first["usage"]["tokens_in"], 0)
        self.assertEqual(second["usage"]["tokens_in"], 0)
        self.assertEqual(second["usage"]["cost_usd"], 0.0)
        retrieve_mock.assert_called_once()
        generate_mock.assert_called_once()
        self.assertEqual(cache.stats()["hits"], 1)


if __name__ == "__main__":
    unittest.main()
