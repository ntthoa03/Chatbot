"""Chạy riêng bộ trap hoặc normal để nghiệm thu H2-03."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from ai_core.evaluator import load_cases
from eval.run import main as run_eval_main


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = PROJECT_ROOT / "eval" / "cases.yaml"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "h2_03"


def build_suite(case_type: str) -> Path:
    raw_cases = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    selected = [case for case in raw_cases if case.get("type") == case_type]
    expected = 30 if case_type == "trap" else 60
    if len(selected) != expected:
        raise ValueError(
            f"H2-03 yêu cầu đúng {expected} case {case_type}, hiện có {len(selected)}."
        )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"h2_03_{case_type}.yaml"
    path.write_text(
        yaml.safe_dump(selected, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    load_cases(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nghiệm thu guardrail H2-03")
    parser.add_argument("--suite", choices=("trap", "normal"), default="trap")
    args, remaining = parser.parse_known_args(argv)
    cases_path = build_suite(args.suite)
    return run_eval_main(
        [
            "--cases",
            str(cases_path),
            "--report-dir",
            str(OUTPUT_DIR / "reports" / args.suite),
            *remaining,
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
