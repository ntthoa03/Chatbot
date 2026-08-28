from pathlib import Path
import unittest

import yaml

from ai_core.evaluator import load_cases


class H311DailyLogReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = yaml.safe_load(
            Path("eval/h3_11_log_cases.yaml").read_text(encoding="utf-8")
        )

    def test_each_review_day_has_at_least_two_new_cases(self) -> None:
        self.assertGreaterEqual(len(self.registry["cases"]), 2)
        self.assertEqual(self.registry["review_date"], "2026-08-27")

    def test_cases_have_trace_evidence_and_valid_error_group(self) -> None:
        allowed = set(self.registry["allowed_error_groups"])
        for case in self.registry["cases"]:
            self.assertIn(case["error_group"], allowed)
            self.assertTrue(case["trace_id"])
            self.assertTrue(Path(case["source_log"]).exists())
            self.assertTrue(case["question"].strip())
            self.assertTrue(case["observed_reply"].strip())
            self.assertTrue(case["expected_behavior"].strip())

    def test_all_review_cases_are_added_to_runnable_eval(self) -> None:
        eval_cases = load_cases("eval/cases_h3_11_daily.yaml")
        registry_ids = {item["case_id"] for item in self.registry["cases"]}
        self.assertEqual({item.id for item in eval_cases}, registry_ids)


if __name__ == "__main__":
    unittest.main()
