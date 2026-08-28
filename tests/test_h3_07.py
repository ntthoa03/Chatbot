"""Acceptance tests cho deliverable H3-07."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs" / "h3_07"


class H307AcceptanceTests(unittest.TestCase):
    def test_deck_has_five_slides_and_speaker_notes(self) -> None:
        deck = OUTPUT_DIR / "H3-07-demo-5-slides.pptx"
        self.assertTrue(deck.is_file())
        with zipfile.ZipFile(deck) as archive:
            names = archive.namelist()
        slides = [name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        notes = [name for name in names if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml")]
        self.assertEqual(len(slides), 5)
        self.assertEqual(len(notes), 5)

    def test_script_contains_all_required_demo_segments(self) -> None:
        content = (OUTPUT_DIR / "H3-07-demo-script.md").read_text(encoding="utf-8").lower()
        for phrase in (
            "tenant mima",
            "phòng khám",
            "tên miền",
            "guardrail",
            "79,66%",
            "$0,0004216",
            "2,06 giây",
            "5 tenant",
        ):
            self.assertIn(phrase, content)

    def test_backup_video_and_two_rehearsals_exist(self) -> None:
        video = OUTPUT_DIR / "H3-07-backup-demo.mp4"
        self.assertTrue(video.is_file())
        self.assertGreater(video.stat().st_size, 10_000)

        report = json.loads((OUTPUT_DIR / "rehearsal-report.json").read_text(encoding="utf-8"))
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["runs"]), 2)
        self.assertTrue(all(run["passed"] for run in report["runs"]))

        live_report = json.loads(
            (OUTPUT_DIR / "live-rehearsal-report.json").read_text(encoding="utf-8")
        )
        self.assertTrue(live_report["passed"])
        self.assertEqual(len(live_report["runs"]), 2)
        self.assertTrue(all(run["passed"] for run in live_report["runs"]))

    def test_demo_public_keys_cover_both_demo_tenants(self) -> None:
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn('"demo-mima-key":"mima_internal"', env_example)
        self.assertIn('"demo-clinic-key":"phongkham_hyhy"', env_example)


if __name__ == "__main__":
    unittest.main()
