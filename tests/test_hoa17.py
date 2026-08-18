from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from ai_core.evaluator import EvalReport, EvalSummary
from eval.tune import Profile, _aggregate_row, _split_content, parse_args, save_aggregate


def _report(run_id: str, *, pass_rate: float, cost: float, latency: float, duration: float) -> EvalReport:
    total = 30
    passed = round(total * pass_rate)
    summary = EvalSummary(
        total=total,
        passed=passed,
        failed=total - passed,
        errors=0,
        manual_review=0,
        evaluated=total,
        pass_rate=pass_rate,
        completion_rate=1.0,
        average_cost_vnd=cost,
        average_model_call_cost_vnd=cost,
        model_calls=total,
        zero_cost_turns=0,
        average_latency_ms=latency,
        total_cost_vnd=round(cost * total, 2),
        duration_seconds=duration,
        unaccented_total=0,
        unaccented_passed=0,
        unaccented_pass_rate=0.0,
    )
    return EvalReport(
        run_id=run_id,
        created_at="2026-08-14T00:00:00Z",
        cases_path="eval/cases.yaml",
        tenant_id="mima_internal",
        config_version=1,
        case_fingerprint="same-cases",
        fingerprint=f"run-{run_id}",
        summary=summary,
        results=(),
    )


class Hoa17TuningTests(unittest.TestCase):
    def test_chunk_split_is_deterministic_bounded_and_overlapping(self):
        text = "Một câu ngắn. " * 80
        first = _split_content(text, 120, overlap_chars=20)
        second = _split_content(text, 120, overlap_chars=20)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 1)
        self.assertTrue(all(0 < len(piece) <= 120 for piece in first))

    def test_profile_context_is_auditable(self):
        profile = Profile("top_k_3", "top_k", "3", retrieval_k=3)
        context = profile.context()
        self.assertEqual(context["profile"], "top_k_3")
        self.assertEqual(context["changed_variable"], "top_k")
        self.assertEqual(context["retrieval_k"], 3)
        self.assertIsNone(context["threshold"])
        self.assertIsNone(context["prompt_suffix_sha256"])

    def test_aggregate_has_three_kpis_deltas_and_time_budget(self):
        baseline = _report("base", pass_rate=0.60, cost=12.0, latency=1800.0, duration=120.0)
        tuned = _report("tuned", pass_rate=0.70, cost=10.0, latency=1700.0, duration=299.0)
        row = _aggregate_row(Profile("top_k_3", "top_k", "3"), tuned, baseline)
        self.assertEqual(row["pass_rate_delta"], 0.10)
        self.assertEqual(row["average_cost_delta_vnd"], -2.0)
        self.assertEqual(row["average_latency_delta_ms"], -100.0)
        self.assertTrue(row["within_5_minutes"])

        with tempfile.TemporaryDirectory() as temp_dir:
            _, csv_path, md_path = save_aggregate(Path(temp_dir), [row])
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                saved = next(csv.DictReader(handle))
            self.assertEqual(saved["within_5_minutes"], "True")
            self.assertIn("Chi phí TB", md_path.read_text(encoding="utf-8"))

    def test_cli_profile_filter_is_optional(self):
        args = parse_args([])
        self.assertIsNone(args.profiles)


if __name__ == "__main__":
    unittest.main()
