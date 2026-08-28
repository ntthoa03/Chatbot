"""Acceptance offline cho script onboard tenant tự động H3-03."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import tempfile
import unittest

from ai_core.config import AgentConfig, _apply_guardrail_profile, _apply_industry_template
from scripts.onboard_tenant import (
    OnboardingError,
    build_smoke_questions,
    build_tenant_config,
    derive_tenant_id,
    first_incomplete_step,
    initial_state,
    normalize_industry,
    reset_from_step,
    run_checkpointed_step,
)


ROOT = Path(__file__).resolve().parent.parent


class H303InputAndConfigTests(unittest.TestCase):
    def test_url_and_vietnamese_industry_create_stable_identifiers(self) -> None:
        self.assertEqual("khach_hang_example_com", derive_tenant_id("https://khach-hang.example.com/"))
        self.assertEqual("construction", normalize_industry("xây dựng"))
        self.assertEqual("commerce", normalize_industry("thuong mai"))
        self.assertEqual("services", normalize_industry("dich_vu"))

    def test_generated_config_contains_only_tenant_private_fields_and_validates(self) -> None:
        raw = build_tenant_config(
            tenant_id="tenant_h303_test",
            template_id="construction",
            bot_name="Trợ lý Test",
            index_dir=ROOT / "outputs" / "h3_03" / "tenant_h303_test" / "index",
        )
        self.assertEqual(
            {"tenant_id", "industry_template", "persona", "contact", "knowledge"},
            set(raw),
        )
        resolved = _apply_guardrail_profile(_apply_industry_template(raw))
        config = AgentConfig.model_validate(resolved)
        self.assertEqual("construction", config.industry_template)
        self.assertEqual("Trợ lý Test", config.persona.bot_name)


class H303SmokeQuestionTests(unittest.TestCase):
    def test_exactly_ten_questions_are_generated_without_llm(self) -> None:
        chunks = [
            {
                "metadata": {"title": f"Dịch vụ xây dựng số {index}"},
                "content": "Nội dung công khai",
            }
            for index in range(8)
        ]
        questions = build_smoke_questions(chunks, "construction")
        self.assertEqual(10, len(questions))
        self.assertEqual(10, len(set(question.casefold() for question in questions)))
        self.assertTrue(any("Dịch vụ xây dựng số" in question for question in questions))


class H303CheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output = Path(self.temp_dir.name)
        self.state_path = self.output / "onboarding_state.json"
        self.state = initial_state(
            url="https://example.test",
            tenant_id="example_test",
            template_id="services",
        )
        self.logger = logging.getLogger(f"test.h303.{id(self)}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_failed_site_step_is_checkpointed_instead_of_losing_progress(self) -> None:
        self.state["steps"]["config"]["status"] = "succeeded"

        def fail_without_sitemap() -> None:
            raise RuntimeError("Sitemap không có URL hợp lệ")

        with self.assertRaises(OnboardingError):
            run_checkpointed_step(
                state=self.state,
                state_path=self.state_path,
                step_name="crawl",
                action=fail_without_sitemap,
                logger=self.logger,
            )
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", saved["steps"]["crawl"]["status"])
        self.assertIn("Sitemap", saved["steps"]["crawl"]["error"])
        self.assertEqual("crawl", first_incomplete_step(saved))

    def test_resume_starts_at_failed_step_after_previous_steps_succeeded(self) -> None:
        self.state["steps"]["config"]["status"] = "succeeded"
        self.state["steps"]["crawl"]["status"] = "failed"
        self.assertEqual("crawl", first_incomplete_step(self.state))
        reset_from_step(self.state, "crawl")
        self.assertEqual("succeeded", self.state["steps"]["config"]["status"])
        self.assertEqual("pending", self.state["steps"]["crawl"]["status"])
        self.assertEqual("pending", self.state["steps"]["smoke"]["status"])


class H303LiveAcceptanceArtifactTests(unittest.TestCase):
    def test_one_command_acceptance_completed_under_fifteen_minutes(self) -> None:
        output = ROOT / "outputs" / "h3_03" / "mimadigi_com"
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        manifest = json.loads((output / "index" / "manifest.json").read_text(encoding="utf-8"))
        metadata = json.loads((output / "index" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual("succeeded", summary["status"])
        self.assertTrue(summary["under_15_minutes_this_run"])
        self.assertLess(summary["total_elapsed_seconds_this_run"], 900)
        self.assertEqual(10, summary["smoke"]["question_count"])
        self.assertEqual(10, summary["smoke"]["queries_with_results"])
        self.assertTrue(summary["smoke"]["isolation_passed"])
        self.assertEqual(["mimadigi_com"], manifest["tenants"])
        self.assertEqual(393, manifest["record_count"])
        self.assertEqual({"mimadigi_com"}, {row["tenant_id"] for row in metadata})


if __name__ == "__main__":
    unittest.main()
