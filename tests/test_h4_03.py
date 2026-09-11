from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core.evaluator import EvalCase, score_case, run_eval, save_report
from ai_core.trace import find_trace, log_trace
from eval.report_h4_03 import build_rows, save_dashboard


class H403DataSufficiencyTests(unittest.TestCase):
    @staticmethod
    def _response(*, need_human: bool = False, blocked: bool = False) -> dict:
        return {
            "reply": "Câu trả lời", "trace_id": "trace", "need_human": need_human,
            "guardrail": {"blocked": blocked},
            "usage": {"model": "stub", "cost_usd": 0, "latency_ms": 1},
        }

    @staticmethod
    def _high_score_trace(*, stage: str = "ok") -> dict:
        return {
            "stage": stage,
            "retrieval": {
                "attempted": True, "top_score": 0.91, "threshold": 0.65,
                "chunks": [{"chunk_id": "c1", "score": 0.91, "content": "Dữ kiện A"}],
                "error": None,
            },
        }

    def test_effective_metric_requires_direct_evidence(self) -> None:
        case = EvalCase(id="a", question="Hỏi A", type="normal", must_contain=("Câu",))
        result = score_case(
            case, self._response(), diagnostics=self._high_score_trace(),
            answerability_fn=lambda _q, _chunks: {
                "answerable": False, "confidence": 0.95,
                "supporting_chunk_ids": [], "reason": "Chỉ gần chủ đề",
            },
        )
        self.assertTrue(result.retrieval_hit)
        self.assertFalse(result.context_answerable)
        self.assertFalse(result.data_sufficient)
        self.assertEqual(result.data_status, "not_answerable")

    def test_fallback_and_guardrail_never_count_as_sufficient(self) -> None:
        judge = lambda _q, _chunks: {
            "answerable": True, "confidence": 1.0,
            "supporting_chunk_ids": ["c1"], "reason": "Có dữ kiện",
        }
        fallback = score_case(
            EvalCase(id="b", question="Hỏi", type="normal", must_contain=("Câu",)),
            self._response(need_human=True), diagnostics=self._high_score_trace(),
            answerability_fn=judge,
        )
        blocked = score_case(
            EvalCase(id="c", question="Hỏi", type="normal", must_contain=("Câu",)),
            self._response(blocked=True), diagnostics=self._high_score_trace(),
            answerability_fn=judge,
        )
        self.assertEqual(fallback.data_status, "fallback_response")
        self.assertEqual(blocked.data_status, "guardrail_blocked")
        self.assertTrue(fallback.context_answerable)
        self.assertTrue(blocked.context_answerable)
        self.assertFalse(fallback.data_sufficient)
        self.assertFalse(blocked.data_sufficient)
    def _cases(self, root: Path) -> Path:
        path = root / "cases.yaml"
        path.write_text(
            "\n".join(
                f'- id: h4-{index}\n  question: "Câu {index}"\n  type: normal\n'
                '  must_contain: ["ok"]'
                for index in range(1, 5)
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_eval_reports_formula_and_separates_missing_data_from_technical_error(self) -> None:
        diagnostics = {
            "trace-1": {
                "stage": "ok",
                "retrieval": {
                    "attempted": True, "top_score": 0.91, "threshold": 0.65,
                    "chunks": [{"score": 0.91}], "error": None,
                },
            },
            "trace-2": {
                "stage": "fallback",
                "retrieval": {
                    "attempted": True, "top_score": 0.42, "threshold": 0.65,
                    "chunks": [], "error": None,
                },
            },
            "trace-3": {
                "stage": "fallback",
                "retrieval": {
                    "attempted": True, "top_score": None, "threshold": 0.65,
                    "chunks": [], "error": None,
                },
            },
            "trace-4": {
                "stage": "retrieval_error",
                "retrieval": {
                    "attempted": True, "top_score": None, "threshold": 0.65,
                    "chunks": [], "error": "provider down",
                },
            },
        }
        counter = iter(range(1, 5))

        def chat_fn(_payload):
            index = next(counter)
            return {
                "reply": "ok", "trace_id": f"trace-{index}", "need_human": False,
                "guardrail": {"blocked": False},
                "usage": {"model": "stub", "cost_usd": 0, "latency_ms": 1},
            }

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = run_eval(
                self._cases(root), chat_fn, workers=1,
                diagnostic_resolver=diagnostics.get,
            )
            self.assertEqual(report.summary.data_sufficient_count, 1)
            self.assertEqual(report.summary.data_insufficient_count, 2)
            self.assertEqual(report.summary.retrieval_error_count, 1)
            self.assertEqual(report.summary.data_sufficiency_rate, 0.25)
            self.assertEqual(
                [item.data_status for item in report.results],
                ["sufficient", "insufficient", "insufficient", "retrieval_error"],
            )
            paths = save_report(report, root / "reports")
            scorecard = paths[3].read_text(encoding="utf-8-sig")
            self.assertIn("TỶ LỆ CÓ ĐỦ DỮ LIỆU", scorecard)
            with paths[1].open(encoding="utf-8-sig", newline="") as handle:
                detail = list(csv.DictReader(handle))
            self.assertEqual(detail[0]["retrieval_top_score"], "0.91")
            self.assertEqual(detail[1]["data_status"], "insufficient")

    def test_multi_tenant_dashboard_contains_table_and_dual_axis_svg(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reports = []
            for tenant, passed, covered in (("tenant_a", 8, 9), ("tenant_b", 6, 4)):
                path = root / f"{tenant}.json"
                path.write_text(json.dumps({
                    "tenant_id": tenant,
                    "summary": {
                        "total": 10, "passed": passed, "pass_rate": passed / 10,
                        "data_sufficient_count": covered,
                        "data_sufficiency_rate": covered / 10,
                        "data_insufficient_count": 10 - covered,
                        "retrieval_error_count": 0,
                        "retrieval_not_applicable_count": 0,
                        "data_unknown_count": 0,
                    },
                }), encoding="utf-8")
                reports.append(path)
            rows = build_rows(reports)
            outputs = save_dashboard(rows, root / "out")
            self.assertTrue(all(path.exists() for path in outputs))
            markdown = outputs[2].read_text(encoding="utf-8")
            self.assertIn("tenant_a", markdown)
            self.assertIn("accuracy-vs-data-sufficiency.svg", markdown)
            self.assertIn("Trục trái: tỷ lệ đúng", markdown)
            self.assertIn("Trục phải: tỷ lệ có đủ dữ liệu", markdown)
            svg = outputs[3].read_text(encoding="utf-8")
            self.assertIn("Tỷ lệ có đủ dữ liệu", svg)
            self.assertIn("polyline", svg)

    def test_trace_identifier_remains_findable_while_question_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trace_path = Path(temp) / "traces.jsonl"
            with patch.dict("os.environ", {"AI_CORE_TRACE_PATH": str(trace_path)}):
                log_trace({
                    "trace_id": "trace-123", "tenant_id": "tenant-a",
                    "question": "Gọi tôi theo số 0901234567",
                })
                record = find_trace("trace-123")
            self.assertIsNotNone(record)
            self.assertEqual(record["trace_id"], "trace-123")
            self.assertNotIn("0901234567", record["question"])


if __name__ == "__main__":
    unittest.main()
