from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from ai_core.chat import LLMResult, chat
from ai_core.config import ConfigError, KnowledgeConfig, load_config
from ai_core.guardrail.output import check_forbidden_request
from ai_core.models import ToolCall
from ai_core.prompt import build_system_prompt
from ai_core.retriever import retrieve
from ai_core.tools import execute_tool, get_tool_schemas, message_may_need_tools


ROOT = Path(__file__).resolve().parents[1]
TENANT = "phongkham_hyhy"


class H205ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(TENANT, 1)
        cls.prompt = build_system_prompt(cls.config)

    def test_second_tenant_has_distinct_persona_index_and_tool(self) -> None:
        mima = load_config("mima_internal", 1)
        self.assertEqual(self.config.knowledge.local_index_dir, "outputs/h2_04/index_phongkham_hyhy")
        self.assertEqual(self.config.enabled_tools, ["request_appointment"])
        self.assertEqual(mima.enabled_tools, ["check_domain"])
        self.assertNotEqual(self.config.persona.bot_name, mima.persona.bot_name)
        self.assertEqual(self.config.embedding_policy.primary.model, "text-embedding-3-small")

    def test_healthcare_prompt_has_medical_rules_and_no_forced_seo_block(self) -> None:
        self.assertIn("chẩn đoán hoặc kết luận bệnh cá nhân", self.prompt.casefold())
        self.assertIn("kê thuốc, chỉ định thuốc", self.prompt.casefold())
        self.assertNotIn("Khi nói về SEO", self.prompt)
        self.assertNotIn("MIMA", self.prompt)

    def test_index_path_cannot_escape_project(self) -> None:
        with self.assertRaises(ValueError):
            KnowledgeConfig(local_index_dir="../mima-secret")

    def test_missing_tenant_still_fails_closed(self) -> None:
        with self.assertRaises(ConfigError):
            load_config("tenant_khong_ton_tai", 1)


class H205MedicalGuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(TENANT, 1)

    def test_personal_diagnosis_is_replaced_by_safe_handoff(self) -> None:
        result = check_forbidden_request("Chẩn đoán giúp tôi bị bệnh gì", self.config)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "medical_diagnosis")
        self.assertIn("không thể chẩn đoán", result["safe_reply"].casefold())

    def test_prescription_is_replaced_by_safe_handoff(self) -> None:
        result = check_forbidden_request("Kê thuốc cho tôi, uống liều bao nhiêu?", self.config)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "medical_prescription")

    def test_emergency_signs_tell_user_to_call_115(self) -> None:
        response = chat(
            {
                "tenant_id": TENANT,
                "conversation_id": "a2e7d326-b22d-4762-bff0-a44e2a3bc005",
                "message": "Tôi bị méo miệng và yếu liệt nửa người",
                "config_version": 1,
            }
        )
        self.assertIn("115", response["reply"])
        self.assertTrue(response["need_human"])
        self.assertEqual(response["guardrail"]["reason"], "medical_emergency_delay")


class H205ToolAndIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(TENANT, 1)

    def test_appointment_tool_schema_collects_no_identity_or_medical_record(self) -> None:
        schema = get_tool_schemas(["request_appointment"])[0]
        properties = schema["parameters"]["properties"]
        self.assertEqual(set(properties), {"specialty", "preferred_time"})
        self.assertTrue(message_may_need_tools("Tôi muốn đặt lịch khám tim mạch", ["request_appointment"]))
        result = execute_tool(
            "request_appointment",
            {"specialty": "tim mạch", "preferred_time": "chiều mai"},
            ["request_appointment"],
        )
        self.assertTrue(result["requires_human"])
        self.assertEqual(result["status"], "handoff_required")

    def test_config_selects_hyhy_index_without_explicit_index_argument(self) -> None:
        index_dir = ROOT / self.config.knowledge.local_index_dir
        vectors = np.load(index_dir / "vectors.npy", allow_pickle=False)
        metadata = json.loads((index_dir / "metadata.json").read_text(encoding="utf-8"))

        def same_as_first(_texts, **_kwargs):
            return [vectors[0].tolist()]

        results = retrieve(
            "nội dung kiểm tra",
            TENANT,
            k=1,
            threshold=0.0,
            relative_score_margin=1.0,
            embed_fn=same_as_first,
            backend="local",
        )
        self.assertEqual(results[0]["chunk_id"], metadata[0]["chunk_id"])
        self.assertIn("phongkhamhyhy.com", results[0]["url"])

    @patch("ai_core.chat.log_trace")
    @patch("ai_core.chat.check_output", return_value={"blocked": False, "reason": None})
    @patch("ai_core.chat._generate_with_fallback")
    def test_tool_handoff_flag_is_propagated_to_chat_response(self, generate, _check, _log) -> None:
        generate.return_value = LLMResult(
            "Dạ, em đã tạo yêu cầu sơ bộ và nhân viên sẽ xác nhận lịch ạ.",
            "test-model",
            10,
            10,
            tool_calls=(
                ToolCall(
                    name="request_appointment",
                    args={"specialty": "tim mạch"},
                    result={"ok": True, "requires_human": True, "status": "handoff_required"},
                ),
            ),
        )
        response = chat(
            {
                "tenant_id": TENANT,
                "conversation_id": "96719c89-b0b5-4f43-99ca-99b69c8bab04",
                "message": "Tôi muốn đặt lịch khám tim mạch",
                "config_version": 1,
            }
        )
        self.assertTrue(response["need_human"])
        self.assertEqual(response["tool_calls"][0]["name"], "request_appointment")


if __name__ == "__main__":
    unittest.main()
