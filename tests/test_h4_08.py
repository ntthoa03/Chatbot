import json
import tempfile
import unittest
from pathlib import Path

from ai_core.chat import LLMResult
from ingestion.profile_generator import (
    PROFILE_FIELDS,
    ProfileError,
    _catalog_sources,
    generate_profile,
    load_crawl_chunks,
    select_sources,
    verify_profile_grounding,
)


def _chunk(index: int, *, tenant_id: str = "mima_internal", kind: str = "service", url: str | None = None) -> dict:
    return {
        "tenant_id": tenant_id, "chunk_id": f"chunk-{index}",
        "content": f"MIMA cung cấp dịch vụ thiết kế website cho doanh nghiệp tại TP.HCM. Nguồn số {index}.",
        "metadata": {
            "url": url or f"https://example.test/page-{index}",
            "title": f"Trang dịch vụ {index}", "type": kind, "updated_at": "2026-08-01",
        },
    }


def _result(payload: dict) -> LLMResult:
    return LLMResult(json.dumps(payload, ensure_ascii=False), "gemini-3.5-flash-lite", 100, 50)


class H408ProfileGeneratorTests(unittest.TestCase):
    def test_second_judge_removes_unsupported_field_and_uncited_price_number(self) -> None:
        fields = {
            field: {"value": None, "confidence": 0.0, "citations": [], "note": "thiếu"}
            for field in PROFILE_FIELDS
        }
        citation = [{"source_id": "S0001", "chunk_id": "c1", "url": "https://example.test",
                     "title": "Nguồn", "evidence_quote": "Gói cơ bản giá 1.000.000 đồng."}]
        fields["industry"] = {"value": "Y tế", "confidence": 0.9, "citations": citation, "note": "nháp"}
        fields["public_pricing"] = {
            "value": [{"item": "Gói cơ bản", "price": "9.000.000", "note": None}],
            "confidence": 0.9, "citations": citation, "note": "nháp",
        }
        decisions = {"items": [
            {"field": "industry", "supported": False, "reason": "quote không nói ngành"},
            {"field": "public_pricing", "supported": True, "reason": "có giá"},
        ]}
        verified, _ = verify_profile_grounding(
            fields, object(), generate_fn=lambda *_: _result(decisions),
        )
        self.assertIsNone(verified["industry"]["value"])
        self.assertIsNone(verified["public_pricing"]["value"])
        self.assertIn("Số liệu", verified["public_pricing"]["note"])

    def test_filled_fields_require_verified_source_quote(self) -> None:
        evidence = "MIMA cung cấp dịch vụ thiết kế website cho doanh nghiệp tại TP.HCM."
        raw = {
            "industry": {"value": "Thiết kế website", "confidence": 0.9,
                         "citations": [{"source_id": "S0001", "evidence_quote": evidence}], "note": "Có bằng chứng."},
            "main_services": {"value": ["Thiết kế website"], "confidence": 0.9,
                              "citations": [{"source_id": "S0001", "evidence_quote": evidence}], "note": "Có bằng chứng."},
            "operating_regions": {"value": ["TP.HCM"], "confidence": 0.8,
                                  "citations": [{"source_id": "S0001", "evidence_quote": evidence}], "note": "Có bằng chứng."},
            "target_customers": {"value": ["Doanh nghiệp"], "confidence": 0.8,
                                 "citations": [{"source_id": "S0001", "evidence_quote": evidence}], "note": "Có bằng chứng."},
            "brand_tone": {"value": None, "confidence": 0, "citations": [], "note": "Không đủ bằng chứng."},
            "public_pricing": {"value": None, "confidence": 0, "citations": [], "note": "Không có giá công khai."},
        }
        profile = generate_profile(
            "mima_internal", [_chunk(1)], generate_fn=lambda *_: _result(raw)
        )
        self.assertEqual(profile.status, "draft_unconfirmed")
        self.assertEqual(profile.industry.citations[0].chunk_id, "chunk-1")
        self.assertIsNone(profile.brand_tone.value)
        self.assertEqual(profile.brand_tone.confidence, 0)

    def test_unverifiable_citation_fails_field_closed_instead_of_guessing(self) -> None:
        raw = {
            field: {"value": "Giá trị đoán" if field in {"industry", "brand_tone"} else ["Giá trị đoán"],
                    "confidence": 0.99,
                    "citations": [{"source_id": "S9999", "evidence_quote": "Không tồn tại"}],
                    "note": "model nói có"}
            for field in PROFILE_FIELDS
        }
        raw["public_pricing"]["value"] = [{"item": "Gói", "price": "1.000.000", "note": None}]
        profile = generate_profile(
            "mima_internal", [_chunk(1)], generate_fn=lambda *_: _result(raw)
        )
        for field in PROFILE_FIELDS:
            value = getattr(profile, field)
            self.assertIsNone(value.value)
            self.assertEqual(value.confidence, 0)
            self.assertEqual(value.citations, [])

    def test_compact_model_schema_expands_to_full_profile_schema(self) -> None:
        quote = "MIMA cung cấp dịch vụ thiết kế website cho doanh nghiệp tại TP.HCM."
        raw = {
            "i": {"v": "Thiết kế website", "c": 0.9, "x": [{"i": "S0001", "q": quote}], "n": "nháp"},
            "s": {"v": ["Thiết kế website"], "c": 0.9, "x": [{"i": "S0001", "q": quote}], "n": "nháp"},
            "r": {"v": ["TP.HCM"], "c": 0.8, "x": [{"i": "S0001", "q": quote}], "n": "nháp"},
            "c": {"v": ["Doanh nghiệp"], "c": 0.8, "x": [{"i": "S0001", "q": quote}], "n": "nháp"},
            "t": {"v": None, "c": 0, "x": [], "n": "thiếu"},
            "p": {"v": None, "c": 0, "x": [], "n": "không có giá"},
        }
        profile = generate_profile(
            "mima_internal", [_chunk(1)], generate_fn=lambda *_: _result(raw)
        )
        self.assertEqual(profile.industry.value, "Thiết kế website")
        self.assertEqual(profile.main_services.value, ["Thiết kế website"])
        self.assertIsNone(profile.public_pricing.value)

    def test_source_selection_is_url_diverse_and_bounded(self) -> None:
        chunks = [
            _chunk(1, url="https://a.test/service"),
            _chunk(2, url="https://a.test/service"),
            _chunk(3, url="https://b.test/pricing", kind="pricing"),
            _chunk(4, url="https://c.test/about", kind="blog"),
        ]
        selected = select_sources(chunks, max_sources=3, max_chars=5000)
        self.assertEqual(len({str(item["metadata"]["url"]) for item in selected}), 3)
        self.assertEqual(selected[0]["metadata"]["type"], "pricing")

    def test_loader_rejects_cross_tenant_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chunks.json"
            path.write_text(json.dumps([_chunk(1, tenant_id="other")]), encoding="utf-8")
            with self.assertRaisesRegex(ProfileError, "tenant khác"):
                load_crawl_chunks(path, "mima_internal")

    def test_existing_catalog_resolves_exactly_five_tenants(self) -> None:
        sources = _catalog_sources(Path("outputs/h3_01/index_catalog.json"))
        self.assertEqual(len(sources), 5)
        self.assertEqual(len({tenant_id for tenant_id, _, _ in sources}), 5)
        self.assertTrue(all(path.exists() for _, path, _ in sources))


if __name__ == "__main__":
    unittest.main()
