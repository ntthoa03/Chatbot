"""Acceptance test H3-05: contract HTTP, tenant resolution, streaming và storage."""

from __future__ import annotations

import json
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

from fastapi.testclient import TestClient

from ai_core.interfaces import AIServices
from api.main import PublicKeyResolver, create_app
from storage import SQLiteStore


TENANT_ID = "mima_internal"
CONVERSATION_ID = "11111111-1111-4111-8111-111111111111"
PUBLIC_KEY = "demo-public-key"


class StubRetriever:
    def retrieve(self, query: str, tenant_id: str, k: int = 5) -> list[dict]:
        return []


class StubChat:
    """Fake AI theo đúng interface; API không được phụ thuộc implementation thật."""

    def chat(self, payload: dict) -> dict:
        self.last_payload = payload
        return {
            "reply": "Dạ, đây là câu trả lời kiểm thử.",
            "sources": [
                {
                    "chunk_id": "chunk-01",
                    "url": "https://example.com/service",
                    "score": 0.91,
                }
            ],
            "tool_calls": [],
            "need_human": True,
            "lead_captured": {"name": "An", "phone": "0912345678"},
            "guardrail": {"blocked": False, "reason": None},
            "usage": {
                "model": "stub-model",
                "tokens_in": 100,
                "tokens_out": 20,
                "cached_tokens_in": 5,
                "cache_write_tokens_in": 0,
                "cost_usd": 0.001,
                "latency_ms": 250,
            },
            "trace_id": "22222222-2222-4222-8222-222222222222",
        }


def request_payload() -> dict:
    return {
        "tenant_id": TENANT_ID,
        "conversation_id": CONVERSATION_ID,
        "message": "Tư vấn website cho tôi",
        "history": [],
        "config_version": 1,
    }


class H305AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = TemporaryDirectory()
        self.storage = SQLiteStore(f"{self.temp_directory.name}/chat.sqlite3")
        self.stub_chat = StubChat()
        services = AIServices(
            retriever=StubRetriever(),
            chat=self.stub_chat,
            backend="in_memory",
        )
        api = create_app(
            services=services,
            storage=self.storage,
            public_key_resolver=PublicKeyResolver({PUBLIC_KEY: TENANT_ID}),
        )
        self.client = TestClient(api)

    def tearDown(self) -> None:
        self.client.close()
        self.storage.close()
        self.temp_directory.cleanup()

    def test_json_chat_matches_contract_and_persists_turn(self) -> None:
        response = self.client.post(
            "/chat",
            headers={"X-Public-Key": PUBLIC_KEY},
            json=request_payload(),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(
            {
                "reply",
                "sources",
                "tool_calls",
                "need_human",
                "lead_captured",
                "guardrail",
                "usage",
                "trace_id",
            },
            set(body),
        )
        UUID(body["trace_id"])
        self.assertEqual(TENANT_ID, self.stub_chat.last_payload["tenant_id"])

        conversation = self.storage.get_conversation(TENANT_ID, CONVERSATION_ID)
        self.assertEqual(["user", "assistant"], [item["role"] for item in conversation["messages"]])
        self.assertEqual(1, len(self.storage.list_leads(TENANT_ID, CONVERSATION_ID)))
        self.assertEqual(1, len(self.storage.list_usage_events(TENANT_ID, CONVERSATION_ID)))

    def test_public_key_is_required_and_must_match_body_tenant(self) -> None:
        missing = self.client.post("/chat", json=request_payload())
        self.assertEqual(401, missing.status_code)

        wrong = self.client.post(
            "/chat",
            headers={"X-Public-Key": "wrong-key"},
            json=request_payload(),
        )
        self.assertEqual(401, wrong.status_code)

        mismatch_api = create_app(
            services=AIServices(StubRetriever(), self.stub_chat, "in_memory"),
            storage=self.storage,
            public_key_resolver=PublicKeyResolver({PUBLIC_KEY: "phongkham_hyhy"}),
        )
        with TestClient(mismatch_api) as mismatch_client:
            mismatch = mismatch_client.post(
                "/chat",
                headers={"X-Public-Key": PUBLIC_KEY},
                json=request_payload(),
            )
        self.assertEqual(403, mismatch.status_code)
        self.assertIsNone(self.storage.get_conversation(TENANT_ID, CONVERSATION_ID))

    def test_streaming_returns_delta_then_full_done_response(self) -> None:
        response = self.client.post(
            "/chat?stream=true",
            headers={"X-Public-Key": PUBLIC_KEY},
            json=request_payload(),
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual("delta", events[0]["type"])
        self.assertEqual("done", events[-1]["type"])
        self.assertEqual("chunk-01", events[-1]["response"]["sources"][0]["chunk_id"])

    def test_browser_cors_preflight_is_supported(self) -> None:
        response = self.client.options(
            "/chat",
            headers={
                "Origin": "https://demo.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-public-key",
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("*", response.headers["access-control-allow-origin"])

    def test_openapi_documents_frozen_request_and_response_models(self) -> None:
        schema = self.client.get("/openapi.json").json()
        operation = schema["paths"]["/chat"]["post"]
        request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        response_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        self.assertTrue(request_ref.endswith("/ChatRequest"))
        self.assertTrue(response_ref.endswith("/ChatResponse"))

    def test_invalid_contract_payload_returns_422_without_calling_ai(self) -> None:
        payload = request_payload()
        payload.pop("message")
        response = self.client.post(
            "/chat",
            headers={"X-Public-Key": PUBLIC_KEY},
            json=payload,
        )
        self.assertEqual(422, response.status_code)
        self.assertIsNone(self.storage.get_conversation(TENANT_ID, CONVERSATION_ID))


if __name__ == "__main__":
    unittest.main()
