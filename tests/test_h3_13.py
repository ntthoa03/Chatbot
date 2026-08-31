"""Nghiệm thu H3-13: tri thức ngành ẩn danh và A/B khả thi."""

from __future__ import annotations

import json
import re
import unittest
from copy import deepcopy
from pathlib import Path

import yaml

from industry_knowledge import (
    IndustryKnowledgeError,
    IndustryKnowledgeStore,
    validate_industry_document,
)


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = ROOT / "industry_knowledge"
REPORT_PATH = ROOT / "outputs" / "h3_13" / "feasibility_report.json"
EXPECTED_INDUSTRIES = {
    "digital_agency",
    "medical_clinic",
    "real_estate",
    "education",
    "food",
}


class H313IndustryKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = IndustryKnowledgeStore(KNOWLEDGE_DIR)

    def test_exactly_five_anonymized_industry_documents_load(self) -> None:
        self.assertEqual(EXPECTED_INDUSTRIES, set(self.store.list_industries()))
        for industry_id in EXPECTED_INDUSTRIES:
            with self.subTest(industry_id=industry_id):
                document = self.store.load(industry_id)
                self.assertTrue(document["experimental"])
                self.assertTrue(document["evidence"]["anonymized"])
                self.assertGreaterEqual(len(document["patterns"]), 3)

    def test_yaml_has_no_tenant_identity_contact_or_specific_price(self) -> None:
        forbidden_keys = {
            "tenant_id", "company", "company_name", "brand", "phone", "email",
            "url", "address", "price", "pricing",
        }
        forbidden_names = re.compile(
            r"\b(?:mima|mimadigi|hyhy|phước\s+thịnh|haiyan|thiên\s+minh)\b",
            re.IGNORECASE,
        )
        contact_or_price = re.compile(
            r"https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}|"
            r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)|"
            r"\b\d+(?:[.,]\d+)?\s*(?:tr|triệu|vnd|vnđ|đồng|usd)\b",
            re.IGNORECASE,
        )

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key, forbidden_keys)
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)
            elif isinstance(value, str):
                yield value

        for path in KNOWLEDGE_DIR.glob("*.yaml"):
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            for text in walk(document):
                with self.subTest(path=path.name, text=text):
                    self.assertIsNone(forbidden_names.search(text))
                    self.assertIsNone(contact_or_price.search(text))

    def test_retrieval_is_explicit_by_industry_and_never_returns_tenant_id(self) -> None:
        rows = self.store.retrieve("Tôi khó thở, có chẩn đoán giúp không?", "medical_clinic")
        self.assertEqual("symptom_safety", rows[0]["pattern_id"])
        self.assertEqual("industry_knowledge", rows[0]["source_type"])
        self.assertNotIn("tenant_id", rows[0])
        with self.assertRaises(IndustryKnowledgeError):
            self.store.retrieve("xin tư vấn", "unknown_industry")
        with self.assertRaises(IndustryKnowledgeError):
            self.store.retrieve("xin tư vấn", "../medical_clinic")

    def test_validator_rejects_identity_phone_and_price_if_someone_adds_them(self) -> None:
        clean = self.store.load("digital_agency")
        unsafe_values = (
            "Thông tin riêng của MIMA",
            "Gọi số 0912345678",
            "Gói này có giá 12 triệu",
        )
        for value in unsafe_values:
            with self.subTest(value=value):
                unsafe = deepcopy(clean)
                unsafe["patterns"][0]["answer_guidance"] = value
                with self.assertRaises(IndustryKnowledgeError):
                    validate_industry_document(unsafe)

    def test_layer_is_separate_and_not_auto_enabled_in_chat_runtime(self) -> None:
        self.assertNotEqual(KNOWLEDGE_DIR.parent / "tenants", KNOWLEDGE_DIR)
        chat_source = (ROOT / "ai_core" / "chat.py").read_text(encoding="utf-8")
        self.assertNotIn("industry_knowledge", chat_source)

    def test_ab_report_proves_improvement_without_hiding_limitations(self) -> None:
        self.assertTrue(REPORT_PATH.is_file(), "Cần chạy script H3-13 để sinh báo cáo.")
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(5, report["industry_count"])
        self.assertEqual(15, report["case_count"])
        self.assertGreater(
            report["with_industry_layer"]["pass_rate"],
            report["baseline"]["pass_rate"],
        )
        self.assertTrue(report["definition_of_done_met"])
        self.assertTrue(report["privacy_validation"]["passed"])
        self.assertEqual(0.0, report["cost_usd"])
        self.assertGreaterEqual(len(report["limitations"]), 4)
        self.assertIn("synthetic", " ".join(report["limitations"]).casefold())


if __name__ == "__main__":
    unittest.main()
