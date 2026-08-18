from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path

from ai_core.evaluator import load_cases
from eval.run_red_team import build_trap_input


ROOT = Path(__file__).resolve().parents[1]


class H202RedTeamSuiteTests(unittest.TestCase):
    def test_suite_has_exactly_thirty_traps_and_ten_guarantee_attacks(self) -> None:
        traps = [
            case for case in load_cases(ROOT / "eval" / "cases.yaml")
            if case.type == "trap"
        ]
        guarantee_ids = {
            "T001", "T008_ASCII",
            "T015_H2", "T016_H2", "T017_H2", "T018_H2",
            "T019_H2", "T020_H2_ASCII", "T021_H2", "T022_H2",
        }

        self.assertEqual(len(traps), 30)
        self.assertEqual({case.id for case in traps} & guarantee_ids, guarantee_ids)
        self.assertEqual(len(guarantee_ids), 10)

    def test_new_attacks_cover_all_four_h2_02_directions(self) -> None:
        new_cases = [
            case for case in load_cases(ROOT / "eval" / "cases.yaml")
            if case.id.startswith("T0") and case.id.endswith(("_H2", "_H2_ASCII"))
        ]
        topics = Counter(case.topic for case in new_cases)

        self.assertEqual(len(new_cases), 16)
        self.assertEqual(topics["result_guarantee"], 8)
        self.assertEqual(topics["unauthorized_discount"], 3)
        self.assertEqual(topics["prompt_injection"], 3)
        self.assertEqual(topics["internal_information"], 1)
        self.assertEqual(topics["banking_information"], 1)
        self.assertTrue(all(case.manual_review_required for case in new_cases))

    def test_red_team_runner_builds_strict_trap_only_input(self) -> None:
        generated = build_trap_input()
        cases = load_cases(generated)

        self.assertEqual(len(cases), 30)
        self.assertTrue(all(case.type == "trap" for case in cases))


if __name__ == "__main__":
    unittest.main()
