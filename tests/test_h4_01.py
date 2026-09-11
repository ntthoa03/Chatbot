"""H4-01 — knowledge gap logging, SQLite persistence và acceptance 50 câu."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai_core.chat import LLMResult, chat
from ai_core.config import load_config
from ai_core.gap_logger import (
    capture_knowledge_gaps,
    log_knowledge_gap,
    reply_indicates_missing_knowledge,
)
from ai_core.interfaces import AIServices
from api.main import PublicKeyResolver, create_app
from fastapi.testclient import TestClient
from storage import SQLiteStore, StorageValidationError


TENANT_ID = "mima_internal"


def _candidate(score: float = 0.41) -> dict:
    return {
        "chunk_id": "candidate-under-threshold",
        "content": "MIMA cung cấp dịch vụ thiết kế website.",
        "url": "https://mimadigi.com/thiet-ke-website",
        "score": score,
        "metadata": {"title": "Thiết kế website"},
    }


def _chat_patches(retrieve_side_effect):
    return (
        patch("ai_core.chat.cache_is_enabled", return_value=False),
        patch("ai_core.chat.message_may_need_tools", return_value=False),
        patch("ai_core.chat.retrieve", side_effect=retrieve_side_effect),
        patch(
            "ai_core.chat.check_input",
            return_value={"blocked": False, "reason": None, "need_human": False},
        ),
        patch(
            "ai_core.chat.check_forbidden_request",
            return_value={
                "blocked": False,
                "reason": None,
                "variant": None,
                "safe_reply": None,
            },
        ),
        patch("ai_core.chat.check_output", return_value={"blocked": False, "reason": None}),
        patch("ai_core.chat.log_trace"),
    )


class GapLoggerUnitTests(unittest.TestCase):
    def test_detects_missing_information_paraphrases(self) -> None:
        self.assertTrue(
            reply_indicates_missing_knowledge(
                "Hiện bên em không có thông tin về văn phòng tại Singapore ạ."
            )
        )
        self.assertTrue(reply_indicates_missing_knowledge("Em chưa thể xác nhận nội dung này."))
        self.assertFalse(
            reply_indicates_missing_knowledge("MIMA có văn phòng tại Thành phố Hồ Chí Minh.")
        )

    def test_jsonl_and_capture_contain_required_fields_and_mask_pii(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "gaps.jsonl"
            with (
                patch.dict(os.environ, {"AI_CORE_KNOWLEDGE_GAP_PATH": str(destination)}),
                capture_knowledge_gaps() as captured,
            ):
                record = log_knowledge_gap(
                    question="Gọi tôi qua 0912345678 về bảng giá",
                    tenant_id=TENANT_ID,
                    conversation_id="conversation-01",
                    trace_id="trace-01",
                    top_score=0.4,
                    threshold=0.65,
                    reason="below_threshold",
                )

            self.assertEqual(1, len(captured))
            self.assertEqual(record, captured[0])
            self.assertIn("[REDACTED]", record["question"])
            self.assertTrue(record["occurred_at"].endswith("+00:00"))
            disk_record = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(record, disk_record)


class GapStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteStore(":memory:")
        self.storage.upsert_tenant(TENANT_ID, "MIMA")
        self.storage.upsert_tenant("tenant_b", "Tenant B")
        self.storage.create_conversation(TENANT_ID, "conversation-01")

    def tearDown(self) -> None:
        self.storage.close()

    def test_round_trip_and_tenant_isolation(self) -> None:
        gap_id = self.storage.save_knowledge_gap(
            TENANT_ID,
            "conversation-01",
            question="Có mở cửa cuối tuần không?",
            top_score=0.42,
            threshold=0.65,
            reason="below_threshold",
            trace_id="trace-01",
            occurred_at="2026-09-04T08:00:00+00:00",
        )
        self.assertGreater(gap_id, 0)
        rows = self.storage.list_knowledge_gaps(TENANT_ID)
        self.assertEqual(1, len(rows))
        self.assertEqual(0.42, rows[0]["top_score"])
        self.assertEqual("below_threshold", rows[0]["reason"])
        self.assertEqual([], self.storage.list_knowledge_gaps("tenant_b"))

    def test_cannot_write_gap_to_wrong_tenant_conversation(self) -> None:
        with self.assertRaises(StorageValidationError):
            self.storage.save_knowledge_gap(
                "tenant_b",
                "conversation-01",
                question="Câu hỏi",
                top_score=None,
                threshold=0.65,
                reason="no_match",
                trace_id="trace-02",
                occurred_at="2026-09-04T08:00:00+00:00",
            )


class FiftyCaseAcceptanceTests(unittest.TestCase):
    def test_all_fifty_below_threshold_fallbacks_are_logged_completely(self) -> None:
        def fake_retrieve(_query: str, _tenant_id: str, *_args, **kwargs):
            return [_candidate()] if kwargs.get("threshold") == 0.0 else []

        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "gaps.jsonl"
            patches = _chat_patches(fake_retrieve)
            with (
                patch.dict(os.environ, {"AI_CORE_KNOWLEDGE_GAP_PATH": str(destination)}),
                patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6],
                capture_knowledge_gaps() as captured,
            ):
                for index in range(50):
                    response = chat(
                        {
                            "tenant_id": TENANT_ID,
                            "conversation_id": str(uuid4()),
                            "message": f"Câu eval thiếu dữ liệu số {index + 1}",
                            "history": [],
                            "config_version": 1,
                        }
                    )
                    self.assertEqual([], response["sources"])
                    self.assertIn("chưa có đủ dữ liệu", response["reply"].casefold())

            self.assertEqual(50, len(captured))
            self.assertEqual(50, len(destination.read_text(encoding="utf-8").splitlines()))
            for index, gap in enumerate(captured, start=1):
                self.assertEqual(TENANT_ID, gap["tenant_id"])
                self.assertEqual(f"Câu eval thiếu dữ liệu số {index}", gap["question"])
                self.assertEqual(0.41, gap["top_score"])
                self.assertEqual(0.65, gap["threshold"])
                self.assertEqual("below_threshold", gap["reason"])
                self.assertTrue(gap["conversation_id"])
                self.assertTrue(gap["occurred_at"])

    def test_model_fallback_with_accepted_chunk_is_also_a_gap(self) -> None:
        config = load_config(TENANT_ID, 1)

        def fake_retrieve(_query: str, _tenant_id: str, *_args, **_kwargs):
            return [_candidate(0.8)]

        patches = _chat_patches(fake_retrieve)
        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "gaps.jsonl"
            with (
                patch.dict(os.environ, {"AI_CORE_KNOWLEDGE_GAP_PATH": str(destination)}),
                patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6],
                patch(
                    "ai_core.chat._generate_with_fallback",
                    return_value=LLMResult(config.refusal_message, "test-model", 10, 5),
                ),
                capture_knowledge_gaps() as captured,
            ):
                chat(
                    {
                        "tenant_id": TENANT_ID,
                        "conversation_id": str(uuid4()),
                        "message": "Thông tin vận hành chưa có",
                        "history": [],
                        "config_version": 1,
                    }
                )

        self.assertEqual(1, len(captured))
        self.assertEqual("fallback_response", captured[0]["reason"])
        self.assertEqual(0.8, captured[0]["top_score"])

    def test_model_missing_info_paraphrase_with_accepted_chunk_is_a_gap(self) -> None:
        def fake_retrieve(_query: str, _tenant_id: str, *_args, **_kwargs):
            return [_candidate(0.8)]

        patches = _chat_patches(fake_retrieve)
        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "gaps.jsonl"
            with (
                patch.dict(os.environ, {"AI_CORE_KNOWLEDGE_GAP_PATH": str(destination)}),
                patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6],
                patch(
                    "ai_core.chat._generate_with_fallback",
                    return_value=LLMResult(
                        "MIMA không có thông tin về văn phòng tại Singapore ạ.",
                        "test-model",
                        10,
                        5,
                    ),
                ),
                capture_knowledge_gaps() as captured,
            ):
                chat(
                    {
                        "tenant_id": TENANT_ID,
                        "conversation_id": str(uuid4()),
                        "message": "MIMA có văn phòng tại Singapore không?",
                        "history": [],
                        "config_version": 1,
                    }
                )

        self.assertEqual(1, len(captured))
        self.assertEqual("fallback_response", captured[0]["reason"])
        self.assertEqual(0.8, captured[0]["top_score"])

    def test_low_score_is_logged_even_when_model_returns_an_answer(self) -> None:
        """Bẫy H4-01: điểm thấp phải được ghi, không phụ thuộc câu fallback."""

        def fake_retrieve(_query: str, _tenant_id: str, *_args, **_kwargs):
            # Mô phỏng adapter trả candidate mà không tự lọc theo threshold.
            return [_candidate(0.41)]

        patches = _chat_patches(fake_retrieve)
        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "gaps.jsonl"
            with (
                patch.dict(os.environ, {"AI_CORE_KNOWLEDGE_GAP_PATH": str(destination)}),
                patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6],
                patch(
                    "ai_core.chat._generate_with_fallback",
                    return_value=LLMResult(
                        "MIMA có cung cấp dịch vụ thiết kế website.",
                        "test-model",
                        10,
                        5,
                    ),
                ),
                capture_knowledge_gaps() as captured,
            ):
                response = chat(
                    {
                        "tenant_id": TENANT_ID,
                        "conversation_id": str(uuid4()),
                        "message": "MIMA có thiết kế website không?",
                        "history": [],
                        "config_version": 1,
                    }
                )

        self.assertIn("có cung cấp", response["reply"].casefold())
        self.assertEqual(1, len(captured))
        self.assertEqual("below_threshold", captured[0]["reason"])
        self.assertEqual(0.41, captured[0]["top_score"])


class ApiPersistenceTests(unittest.TestCase):
    def test_api_captures_core_event_and_persists_it_after_conversation(self) -> None:
        conversation_id = "11111111-1111-4111-8111-111111114001"
        trace_id = "22222222-2222-4222-8222-222222224001"

        class StubRetriever:
            def retrieve(self, query: str, tenant_id: str, k: int = 5) -> list[dict]:
                return []

        class GapEmittingChat:
            def chat(self, payload: dict) -> dict:
                log_knowledge_gap(
                    question=payload["message"],
                    tenant_id=payload["tenant_id"],
                    conversation_id=payload["conversation_id"],
                    trace_id=trace_id,
                    top_score=0.33,
                    threshold=0.65,
                    reason="below_threshold",
                )
                return {
                    "reply": "Hiện em chưa có đủ dữ liệu ạ.",
                    "sources": [],
                    "tool_calls": [],
                    "need_human": False,
                    "lead_captured": None,
                    "guardrail": {"blocked": False, "reason": None},
                    "usage": {
                        "model": "stub-model",
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "cached_tokens_in": 0,
                        "cache_write_tokens_in": 0,
                        "cost_usd": 0.0,
                        "latency_ms": 1,
                    },
                    "trace_id": trace_id,
                }

        with TemporaryDirectory() as temporary_directory:
            storage = SQLiteStore(Path(temporary_directory) / "chat.sqlite3")
            try:
                api = create_app(
                    services=AIServices(StubRetriever(), GapEmittingChat(), "in_memory"),
                    storage=storage,
                    public_key_resolver=PublicKeyResolver({"test-key": TENANT_ID}),
                )
                gap_path = Path(temporary_directory) / "gaps.jsonl"
                with (
                    patch.dict(os.environ, {"AI_CORE_KNOWLEDGE_GAP_PATH": str(gap_path)}),
                    TestClient(api) as client,
                ):
                    response = client.post(
                        "/chat",
                        headers={"X-Public-Key": "test-key"},
                        json={
                            "tenant_id": TENANT_ID,
                            "conversation_id": conversation_id,
                            "message": "Bên mình có mở cửa Chủ nhật không?",
                            "history": [],
                            "config_version": 1,
                        },
                    )
                self.assertEqual(200, response.status_code, response.text)
                gaps = storage.list_knowledge_gaps(TENANT_ID, conversation_id)
                self.assertEqual(1, len(gaps))
                self.assertEqual(trace_id, gaps[0]["trace_id"])
                self.assertEqual(0.33, gaps[0]["top_score"])
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
