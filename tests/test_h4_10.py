from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_weekly_tenant_questions import (
    WeeklyQuestionError,
    generate_weekly_questions,
    run,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "h4_10_clusters_5_tenants.json"


class H410WeeklyQuestionTests(unittest.TestCase):
    def test_five_tenants_receive_only_recorded_questions_with_frequency(self) -> None:
        source = json.loads(FIXTURE.read_text(encoding="utf-8"))
        result = generate_weekly_questions(source)
        self.assertEqual(len(result["tenants"]), 5)
        for tenant_id, tenant in result["tenants"].items():
            self.assertGreaterEqual(tenant["question_count"], 3)
            self.assertLessEqual(tenant["question_count"], 5)
            source_examples = {
                example
                for cluster in source["tenants"][tenant_id]["clusters"]
                for example in cluster["examples"]
            }
            self.assertTrue(all(item["question"] in source_examples for item in tenant["questions"]))
            self.assertTrue(all(item["frequency"] > 0 for item in tenant["questions"]))
            self.assertIn("khách đã hỏi", tenant["message"])

    def test_refuses_to_invent_when_tenant_has_fewer_than_three_topics(self) -> None:
        source = json.loads(FIXTURE.read_text(encoding="utf-8"))
        source["tenants"]["mima_internal"]["clusters"] = source["tenants"]["mima_internal"]["clusters"][:2]
        with self.assertRaisesRegex(WeeklyQuestionError, "Dừng thay vì tự nghĩ"):
            generate_weekly_questions(source, tenant_ids=["mima_internal"])

    def test_question_must_be_present_in_h4_02_examples(self) -> None:
        source = json.loads(FIXTURE.read_text(encoding="utf-8"))
        source["tenants"]["mima_internal"]["clusters"][0]["representative_question"] = "Câu tưởng tượng"
        with self.assertRaisesRegex(WeeklyQuestionError, "từng xuất hiện"):
            generate_weekly_questions(source, tenant_ids=["mima_internal"])

    def test_script_writes_json_and_message_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run(FIXTURE, output)
            self.assertEqual(len(result["tenants"]), 5)
            self.assertTrue((output / "weekly_questions.json").is_file())
            self.assertTrue((output / "message_templates.md").is_file())


if __name__ == "__main__":
    unittest.main()
