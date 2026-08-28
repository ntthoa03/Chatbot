"""Kiểm tra sản phẩm H3-09: 75 case, bảng so sánh và workbook bàn giao."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from zipfile import ZipFile

from ai_core.evaluator import load_cases


ROOT = Path(__file__).resolve().parent.parent
TENANTS = (
    "mima_internal",
    "phongkham_hyhy",
    "bat_dong_san_phuoc_thinh",
    "giao_duc_haiyan",
    "thuc_pham_thien_minh",
)


class H309DeliverableTests(unittest.TestCase):
    def test_five_tenants_have_15_unique_cases_each(self) -> None:
        all_ids: list[str] = []
        all_questions: list[str] = []
        for tenant_id in TENANTS:
            cases = load_cases(ROOT / "eval" / f"cases_{tenant_id}.yaml")
            self.assertEqual(15, len(cases), tenant_id)
            all_ids.extend(case.id for case in cases)
            all_questions.extend(case.question.casefold().strip() for case in cases)
        self.assertEqual(75, len(set(all_ids)))
        self.assertEqual(75, len(set(all_questions)))

    def test_comparison_identifies_weakest_tenant_transparently(self) -> None:
        payload = json.loads(
            (ROOT / "outputs" / "h3_09" / "comparison.json").read_text(encoding="utf-8")
        )
        self.assertEqual(75, payload["case_count"])
        self.assertEqual(5, payload["tenant_count"])
        self.assertEqual("bat_dong_san_phuoc_thinh", payload["weakest_tenant"])
        rows = payload["comparison"]
        self.assertEqual(set(TENANTS), {row["tenant_id"] for row in rows})
        self.assertTrue(all("effective_pass_rate" in row for row in rows))
        self.assertEqual(0.8, rows[0]["effective_pass_rate"])

    def test_corpus_evidence_covers_all_cases(self) -> None:
        payload = json.loads(
            (ROOT / "outputs" / "h3_09" / "corpus_coverage.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(75, sum(row["total"] for row in payload["summary"]))
        self.assertEqual(75, sum(row["covered"] for row in payload["summary"]))

    def test_scoreboard_contains_four_expected_sheets(self) -> None:
        workbook = ROOT / "outputs" / "h3_09" / "H3-09-bang-diem-5-tenant.xlsx"
        self.assertTrue(workbook.is_file())
        with ZipFile(workbook) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        for sheet_name in ("Tổng quan", "Theo chủ đề", "Câu chưa đạt", "Nguồn &amp; phương pháp"):
            self.assertIn(sheet_name, workbook_xml)


if __name__ == "__main__":
    unittest.main()
