"""Contract tests H2-11 chạy cùng tiêu chí trên real adapter và in-memory fake."""

from __future__ import annotations

import inspect
import os
import unittest
from datetime import date
from unittest.mock import patch
from uuid import uuid4

from ai_core.interfaces import (
    ChatPort,
    INTERFACE_VERSION,
    InMemoryChat,
    InMemoryRetriever,
    InterfaceValidationError,
    RealChat,
    RealRetriever,
    RetrieverPort,
    build_services,
)
from ai_core.models import ChatResponse


TENANT_A = "tenant_a"
TENANT_B = "tenant_b"


def _chunk(tenant_id: str, chunk_id: str, content: str) -> dict:
    return {
        "tenant_id": tenant_id,
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {
            "url": f"https://example.com/{tenant_id}/{chunk_id}",
            "title": chunk_id,
            "type": "service",
            "updated_at": date(2026, 8, 20).isoformat(),
        },
    }


CHUNKS = [
    _chunk(TENANT_A, "a-web", "thiết kế website doanh nghiệp"),
    _chunk(TENANT_A, "a-seo", "dịch vụ seo website"),
    _chunk(TENANT_B, "b-web", "thiết kế website phòng khám"),
]


def _chat_payload(tenant_id: str = TENANT_A) -> dict:
    return {
        "tenant_id": tenant_id,
        "conversation_id": str(uuid4()),
        "message": "xin chào",
        "history": [],
        "config_version": 1,
    }


def _chat_response(reply: str = "Dạ, xin chào anh/chị ạ.") -> dict:
    return {
        "reply": reply,
        "sources": [],
        "tool_calls": [],
        "need_human": False,
        "lead_captured": None,
        "guardrail": {"blocked": False, "reason": None},
        "usage": {
            "model": "contract-stub",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "latency_ms": 0,
        },
        "trace_id": str(uuid4()),
    }


class H211FrozenSignatureTests(unittest.TestCase):
    def test_interface_version_is_explicit(self) -> None:
        self.assertEqual(INTERFACE_VERSION, "h2-11.v2")

    def test_retriever_protocol_signature_is_frozen(self) -> None:
        signature = inspect.signature(RetrieverPort.retrieve)
        self.assertEqual(list(signature.parameters), ["self", "query", "tenant_id", "k"])
        self.assertEqual(signature.parameters["k"].default, 5)

    def test_chat_protocol_signature_is_frozen(self) -> None:
        signature = inspect.signature(ChatPort.chat)
        self.assertEqual(list(signature.parameters), ["self", "payload"])


class H211SharedImplementationContractTests(unittest.TestCase):
    def _implementations(self):
        # Stub production chỉ thay I/O ngoài; adapter real vẫn phải giữ đúng contract H2-11.
        def real_retrieve(query: str, tenant_id: str, k: int) -> list[dict]:
            if tenant_id not in {TENANT_A, TENANT_B}:
                raise InterfaceValidationError(f"unknown tenant_id: {tenant_id}")
            terms = {term.casefold() for term in query.split()}
            rows = []
            for raw in CHUNKS:
                if raw["tenant_id"] != tenant_id:
                    continue
                if not terms.intersection(raw["content"].casefold().split()):
                    continue
                row = dict(raw)
                row["score"] = 1.0
                rows.append(row)
            return rows[:k]

        def real_chat(payload: dict) -> dict:
            if payload.get("tenant_id") not in {TENANT_A, TENANT_B}:
                raise InterfaceValidationError("unknown tenant_id")
            return _chat_response()

        yield "real", RealRetriever(real_retrieve), RealChat(real_chat)
        yield (
            "in_memory",
            InMemoryRetriever(chunks=CHUNKS, tenant_ids=[TENANT_A, TENANT_B]),
            InMemoryChat(
                tenant_ids=[TENANT_A, TENANT_B],
                replies={(TENANT_A, "xin chao"): "Dạ, xin chào anh/chị ạ."},
            ),
        )

    def test_both_implementations_match_protocols(self) -> None:
        for name, retriever, chat in self._implementations():
            with self.subTest(backend=name):
                self.assertIsInstance(retriever, RetrieverPort)
                self.assertIsInstance(chat, ChatPort)

    def test_both_retrievers_return_only_requested_tenant(self) -> None:
        for name, retriever, _ in self._implementations():
            with self.subTest(backend=name):
                rows = retriever.retrieve("thiết kế website", TENANT_A, 5)
                self.assertTrue(rows)
                self.assertLessEqual(len(rows), 5)
                self.assertTrue(all(row["tenant_id"] == TENANT_A for row in rows))
                self.assertTrue(
                    all({"tenant_id", "chunk_id", "content", "metadata", "score"} <= row.keys() for row in rows)
                )

    def test_both_chats_return_chat_response_contract(self) -> None:
        required = {
            "reply",
            "sources",
            "tool_calls",
            "need_human",
            "lead_captured",
            "guardrail",
            "usage",
            "trace_id",
        }
        for name, _, chat in self._implementations():
            with self.subTest(backend=name):
                result = chat.chat(_chat_payload())
                self.assertEqual(set(result), required)
                ChatResponse.model_validate(result)


class H211InMemorySafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.retriever = InMemoryRetriever(chunks=CHUNKS, tenant_ids=[TENANT_A, TENANT_B])
        self.chat = InMemoryChat(tenant_ids=[TENANT_A, TENANT_B])

    def test_fake_filters_before_ranking(self) -> None:
        rows = self.retriever.retrieve("thiết kế website phòng khám", TENANT_A, 10)
        self.assertTrue(all(row["tenant_id"] == TENANT_A for row in rows))
        self.assertNotIn("b-web", {row["chunk_id"] for row in rows})

    def test_fake_rejects_missing_empty_malformed_and_unknown_tenant(self) -> None:
        for tenant_id in (None, "", "../tenant_b", "unknown"):
            with self.subTest(tenant_id=tenant_id):
                with self.assertRaises(InterfaceValidationError):
                    self.retriever.retrieve("website", tenant_id, 5)  # type: ignore[arg-type]

    def test_fake_chat_rejects_unknown_tenant(self) -> None:
        with self.assertRaises(InterfaceValidationError):
            self.chat.chat(_chat_payload("unknown"))


class H211FactoryTests(unittest.TestCase):
    def test_same_factory_call_switches_both_implementations_with_one_config(self) -> None:
        # Cùng một đoạn wiring: chỉ giá trị AI_CORE_INTERFACE_BACKEND thay đổi.
        factory_kwargs = {
            "tenant_ids": [TENANT_A],
            "chunks": [CHUNKS[0]],
            "replies": {(TENANT_A, "xin chao"): "Dạ, xin chào anh/chị ạ."},
        }

        with patch.dict(os.environ, {"AI_CORE_INTERFACE_BACKEND": "real"}):
            real_services = build_services(**factory_kwargs)
        with patch.dict(os.environ, {"AI_CORE_INTERFACE_BACKEND": "in_memory"}):
            fake_services = build_services(**factory_kwargs)

        self.assertIsInstance(real_services.retriever, RealRetriever)
        self.assertIsInstance(real_services.chat, RealChat)
        self.assertIsInstance(fake_services.retriever, InMemoryRetriever)
        self.assertIsInstance(fake_services.chat, InMemoryChat)

    def test_one_environment_value_switches_both_services(self) -> None:
        with patch.dict(os.environ, {"AI_CORE_INTERFACE_BACKEND": "in_memory"}):
            services = build_services(tenant_ids=[TENANT_A], chunks=[CHUNKS[0]])
        self.assertEqual(services.backend, "in_memory")
        self.assertEqual(services.interface_version, INTERFACE_VERSION)
        self.assertIsInstance(services.retriever, InMemoryRetriever)
        self.assertIsInstance(services.chat, InMemoryChat)

    def test_real_is_safe_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            services = build_services()
        self.assertEqual(services.backend, "real")
        self.assertEqual(services.interface_version, INTERFACE_VERSION)
        self.assertIsInstance(services.retriever, RealRetriever)
        self.assertIsInstance(services.chat, RealChat)


if __name__ == "__main__":
    unittest.main()
