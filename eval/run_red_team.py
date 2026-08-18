"""Run only the 30 H2-02 trap cases from the canonical eval suite."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from ai_core.evaluator import load_cases
from eval.run import main as run_eval_main


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = PROJECT_ROOT / "eval" / "cases.yaml"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "h2_02"
TRAP_INPUT_PATH = OUTPUT_DIR / "h2_02_traps.yaml"


def build_trap_input() -> Path:
    raw_cases = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    traps = [case for case in raw_cases if case.get("type") == "trap"]
    if len(traps) != 30:
        raise ValueError(f"H2-02 yêu cầu đúng 30 trap case, hiện có {len(traps)}.")
    guarantee_cases = [
        case for case in traps
        if case.get("id") in {"T001", "T008_ASCII"}
        or case.get("topic") == "result_guarantee"
    ]
    if len(guarantee_cases) != 10:
        raise ValueError(
            "H2-02 yêu cầu đúng 10 cách ép cam kết, "
            f"hiện có {len(guarantee_cases)}."
        )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TRAP_INPUT_PATH.write_text(
        yaml.safe_dump(traps, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    # Validate the generated input using the same strict schema as production eval.
    load_cases(TRAP_INPUT_PATH)
    return TRAP_INPUT_PATH


def main(argv: list[str] | None = None) -> int:
    trap_path = build_trap_input()
    return run_eval_main([
        "--cases", str(trap_path),
        "--report-dir", str(OUTPUT_DIR / "reports"),
        *(argv if argv is not None else sys.argv[1:]),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
