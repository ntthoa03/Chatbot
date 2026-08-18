"""Generate the provisional H2-01 Zalo/Fanpage source log deterministically.

The messages come from normal cases in ``eval/cases.yaml``. They are explicitly
marked synthetic so they cannot be confused with customer data. Replace this
artifact when real Zalo/Fanpage exports arrive next week.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_core.evaluator import load_cases


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "eval" / "cases.yaml"
DEFAULT_OUTPUT = ROOT / "eval" / "synthetic_zalo_fanpage_h2_01.jsonl"


def generate(cases_path: Path, output_path: Path) -> int:
    normal_cases = [case for case in load_cases(cases_path) if case.type == "normal"]
    start = datetime(2026, 8, 10, 8, 7, tzinfo=timezone(timedelta(hours=7)))
    rows = []
    for index, case in enumerate(normal_cases, start=1):
        timestamp = start + timedelta(minutes=37 * (index - 1))
        channel = "zalo" if index % 3 else "fanpage"
        rows.append({
            "log_id": f"SYN-H2-01-{index:03d}",
            "timestamp": timestamp.isoformat(),
            "channel": channel,
            "customer_id": f"synthetic-{channel}-{index:03d}",
            "synthetic": True,
            "source_basis": "customer_style_generated_before_real_export",
            "case_id": case.id,
            "topic": case.topic,
            "message": case.question,
        })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sinh log Zalo/Fanpage tạm cho H2-01.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    count = generate(args.cases, args.output)
    print(f"Đã sinh {count} log tạm: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
