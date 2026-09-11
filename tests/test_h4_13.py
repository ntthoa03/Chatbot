from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ingestion.profile_completion import build_completion, load_gap_report
from ingestion.profile_review import save_review_decision


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "h4_10_clusters_5_tenants.json"
PROFILE = ROOT / "outputs" / "h4_08" / "tenants" / "mima_internal" / "business_profile.draft.json"
APP = ROOT / "app.py"


class H413ProfileCompletionTests(unittest.TestCase):
    def test_percentage_uses_question_frequency_not_schema_field_count(self) -> None:
        report = load_gap_report(FIXTURE)
        records = [{
            "tenant_id": "mima_internal", "field_name": "public_pricing", "action": "confirmed"
        }]
        result = build_completion(report, "mima_internal", records)
        self.assertEqual(result["total_frequency"], 16)
        self.assertEqual(result["resolved_frequency"], 8)
        self.assertEqual(result["completion_percent"], 50.0)
        self.assertEqual(result["next_actions"][0]["frequency"], 5)
        self.assertEqual(result["next_actions"][0]["benefit_rate"], 0.3125)
        self.assertIn("5/16", result["next_actions"][0]["benefit_text"])

    def test_no_real_gap_data_returns_no_percentage_instead_of_inventing(self) -> None:
        report = {
            "tenants": {"mima_internal": {"clusters": [], "total_gaps": 0}}
        }
        result = build_completion(report, "mima_internal", [])
        self.assertEqual(result["status"], "no_gap_data")
        self.assertIsNone(result["completion_percent"])

    def test_ui_shows_percentage_next_action_time_and_benefit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            review_root = Path(directory)
            save_review_decision(
                tenant_id="mima_internal", field_name="public_pricing", action="confirmed",
                proposed_value=[], final_value=[], citations=[], elapsed_seconds=10,
                output_root=review_root,
            )
            with patch.dict(os.environ, {
                "AI_CORE_UI_ACCESS_CODE": " ",
                "AI_CORE_UI_TENANT_ID": "mima_internal",
                "AI_CORE_PROFILE_DRAFT_PATH": str(PROFILE),
                "AI_CORE_PROFILE_REVIEW_ROOT": str(review_root),
                "AI_CORE_GAP_CLUSTER_PATH": str(FIXTURE),
            }):
                app = AppTest.from_file(str(APP)).run(timeout=15)
                next(radio for radio in app.radio if radio.label == "Khu vực").set_value(
                    "Duyệt hồ sơ"
                ).run(timeout=15)

        rendered = "\n".join(
            [item.value for item in app.markdown]
            + [item.value for item in app.caption]
        )
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.get("progress")[0].value, 50)
        self.assertIn("8/16", rendered)
        self.assertIn("Việc tiếp theo", rendered)
        self.assertIn("5/16", rendered)
        self.assertIn("phút", rendered)


if __name__ == "__main__":
    unittest.main()
