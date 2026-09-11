from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eval.report_h4_12 import TENANT_ID, build_report, write_report


ROOT = Path(__file__).resolve().parents[1]


class H412ImpactReportTests(unittest.TestCase):
    def test_report_uses_one_tenant_and_ranks_four_sources(self) -> None:
        report = build_report()
        self.assertEqual(report["tenant_id"], "mima_internal")
        self.assertIn("same tenant", report["comparison_scope"])
        self.assertEqual(len(report["sources"]), 4)
        self.assertEqual(
            {item["source"] for item in report["sources"]},
            {"crawl", "tài liệu", "log chat", "tenant tự trả lời"},
        )
        self.assertEqual(report["ranking"][0], "tenant tự trả lời")
        for item in report["sources"]:
            self.assertGreater(item["case_count"], 0)
            self.assertIsInstance(item["accuracy_gain"], float)
            self.assertIsInstance(item["sufficiency_gain"], float)

    def test_writes_required_markdown_and_machine_readable_metrics(self) -> None:
        report = build_report()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "tac-dong-nguon-data.md"
            metrics = root / "impact.json"
            write_report(report, markdown, metrics)
            text = markdown.read_text(encoding="utf-8")
            saved = json.loads(metrics.read_text(encoding="utf-8"))
        self.assertIn("Tác động của từng nguồn data", text)
        self.assertIn("TEST/SYNTHETIC", text)
        self.assertIn("Giới hạn bắt buộc", text)
        self.assertEqual(saved["tenant_id"], TENANT_ID)

    def test_required_report_exists(self) -> None:
        path = ROOT / "docs" / "tac-dong-nguon-data.md"
        self.assertTrue(path.is_file())
        self.assertIn("mima_internal", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
