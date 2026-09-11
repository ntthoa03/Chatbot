from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ingestion.profile_generator import PROFILE_FIELDS
from ingestion.profile_review import (
    FIELD_LABELS,
    ProfileReviewError,
    estimate_review_seconds,
    load_profile,
    parse_edited_value,
    save_review_decision,
    suggested_text,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "outputs" / "h4_08" / "tenants" / "mima_internal" / "business_profile.draft.json"
APP_PATH = ROOT / "app.py"


class H409ProfileReviewTests(unittest.TestCase):
    def test_loader_rejects_multi_tenant_array_and_wrong_tenant_file(self) -> None:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aggregate = root / "aggregate.json"
            aggregate.write_text(json.dumps([profile]), encoding="utf-8")
            with self.assertRaisesRegex(ProfileReviewError, "đúng một tenant"):
                load_profile(aggregate, "mima_internal")
            wrong = root / "wrong.json"
            wrong.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(ProfileReviewError, "Từ chối hồ sơ tenant"):
                load_profile(wrong, "phongkham_hyhy")

    def test_every_screen_has_a_non_empty_proposal(self) -> None:
        profile = load_profile(PROFILE_PATH, "mima_internal")
        proposals = {
            name: suggested_text(name, getattr(profile, name).value)
            for name in PROFILE_FIELDS
        }
        self.assertEqual(set(proposals), set(PROFILE_FIELDS))
        self.assertTrue(all(text.strip() for text in proposals.values()))
        for name in PROFILE_FIELDS:
            value = getattr(profile, name).value
            if value is None or value == []:
                self.assertIn("Hỏi lại sau", proposals[name])

    def test_edit_parser_keeps_field_shape(self) -> None:
        self.assertEqual(parse_edited_value("industry", "Bán lẻ"), "Bán lẻ")
        self.assertEqual(
            parse_edited_value("main_services", "• Tư vấn\n- Thiết kế\nTư vấn"),
            ["Tư vấn", "Thiết kế"],
        )
        self.assertEqual(
            parse_edited_value("public_pricing", "Gói cơ bản | 2.000.000đ | Chưa VAT"),
            [{"item": "Gói cơ bản", "price": "2.000.000đ", "note": "Chưa VAT"}],
        )

    def test_decisions_are_persisted_per_tenant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tenant_id in ("mima_internal", "tenant_khac"):
                save_review_decision(
                    tenant_id=tenant_id,
                    field_name="industry",
                    action="confirmed",
                    proposed_value="Dịch vụ",
                    final_value="Dịch vụ",
                    citations=[],
                    elapsed_seconds=12.5,
                    output_root=root,
                )
            mima = [json.loads(line) for line in (root / "mima_internal.jsonl").read_text(encoding="utf-8").splitlines()]
            other = [json.loads(line) for line in (root / "tenant_khac.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["tenant_id"] for item in mima], ["mima_internal"])
        self.assertEqual([item["tenant_id"] for item in other], ["tenant_khac"])

    def test_non_developer_estimate_is_under_ten_minutes(self) -> None:
        profile = load_profile(PROFILE_PATH, "mima_internal")
        self.assertEqual(len(PROFILE_FIELDS), 6)
        estimate = estimate_review_seconds(profile)
        self.assertGreaterEqual(estimate, 6 * 35)
        self.assertLessEqual(estimate, 6 * 50)
        self.assertLess(estimate, 10 * 60)

    def test_ui_reviews_one_field_per_screen_and_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "AI_CORE_UI_ACCESS_CODE": "",
                "AI_CORE_UI_TENANT_ID": "mima_internal",
                "AI_CORE_PROFILE_DRAFT_PATH": str(PROFILE_PATH),
                "AI_CORE_PROFILE_REVIEW_ROOT": directory,
            },
        ):
            started = time.perf_counter()
            app = AppTest.from_file(str(APP_PATH)).run(timeout=15)
            area = next(radio for radio in app.radio if radio.label == "Khu vực")
            area.set_value("Duyệt hồ sơ").run(timeout=15)

            for expected_field in PROFILE_FIELDS:
                self.assertEqual(list(app.exception), [])
                visible_labels = [item.value for item in app.subheader]
                self.assertEqual(visible_labels, [FIELD_LABELS[expected_field]])
                labels = {button.label for button in app.button}
                self.assertTrue({"✅ Đúng", "✏️ Sửa", "⏭️ Bỏ qua", "🕒 Hỏi lại sau"}.issubset(labels))
                self.assertTrue(any("Nguồn trích dẫn" in item.value for item in app.markdown))
                correct = next(button for button in app.button if button.label == "✅ Đúng")
                correct.click().run(timeout=15)

            elapsed = time.perf_counter() - started
            records_path = Path(directory) / "mima_internal.jsonl"
            records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(list(app.exception), [])
        self.assertTrue(any("6/6" in item.value for item in app.success))
        self.assertEqual([item["field_name"] for item in records], list(PROFILE_FIELDS))
        self.assertTrue(all(item["action"] == "confirmed" for item in records))
        self.assertLess(elapsed, 60)

    def test_ui_edit_is_prefilled_and_defer_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "AI_CORE_UI_ACCESS_CODE": " ",
                "AI_CORE_UI_TENANT_ID": "mima_internal",
                "AI_CORE_PROFILE_DRAFT_PATH": str(PROFILE_PATH),
                "AI_CORE_PROFILE_REVIEW_ROOT": directory,
            },
        ):
            app = AppTest.from_file(str(APP_PATH)).run(timeout=15)
            next(radio for radio in app.radio if radio.label == "Khu vực").set_value(
                "Duyệt hồ sơ"
            ).run(timeout=15)
            next(button for button in app.button if button.label == "✏️ Sửa").click().run(timeout=15)
            self.assertTrue(app.text_area[0].value.strip())
            app.text_area[0].set_value("Công nghệ và marketing").run(timeout=15)
            next(button for button in app.button if button.label == "Lưu bản sửa").click().run(timeout=15)
            next(button for button in app.button if button.label == "🕒 Hỏi lại sau").click().run(timeout=15)
            records = [
                json.loads(line)
                for line in (Path(directory) / "mima_internal.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(list(app.exception), [])
        self.assertEqual(records[0]["action"], "edited")
        self.assertEqual(records[0]["final_value"], "Công nghệ và marketing")
        self.assertEqual(records[1]["action"], "deferred")


if __name__ == "__main__":
    unittest.main()
