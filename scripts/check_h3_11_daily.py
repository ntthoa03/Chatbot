"""Kiểm tra sản phẩm H3-11 và xuất bảng phân loại lỗi dạng CSV/JSON."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import yaml


ALLOWED_GROUPS = {
    "thieu_du_lieu": "Thiếu dữ liệu",
    "hieu_sai_y": "Hiểu sai ý",
    "tra_loi_kho_cung": "Trả lời khô cứng",
    "guardrail_chan_nham": "Guardrail chặn nhầm",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kiểm tra tối thiểu 2 case log mới mỗi ngày cho H3-11.")
    parser.add_argument("--date", required=True, help="Ngày review theo định dạng YYYY-MM-DD.")
    parser.add_argument("--input", default="eval/h3_11_log_cases.yaml")
    parser.add_argument("--out-dir", default="outputs/h3_11")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.input)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])

    # Chỉ tính case được rút trong đúng ngày review, không dùng case cũ để đủ số lượng.
    if payload.get("review_date") != args.date:
        raise SystemExit(f"Ngày trong file là {payload.get('review_date')}, không khớp {args.date}.")
    if len(cases) < 2:
        raise SystemExit(f"H3-11 chưa đạt: ngày {args.date} chỉ có {len(cases)} case, cần ít nhất 2.")

    required = (
        "case_id", "tenant_id", "error_group", "error_label", "source_log",
        "source_logged_at", "trace_id", "question", "observed_reply",
        "expected_behavior", "root_cause_hint", "status",
    )
    ids: set[str] = set()
    counts: Counter[str] = Counter()
    for item in cases:
        missing = [key for key in required if not str(item.get(key, "")).strip()]
        if missing:
            raise SystemExit(f"{item.get('case_id', '<không ID>')} thiếu trường: {', '.join(missing)}")
        if item["case_id"] in ids:
            raise SystemExit(f"Trùng case_id: {item['case_id']}")
        if item["error_group"] not in ALLOWED_GROUPS:
            raise SystemExit(f"Nhóm lỗi không hợp lệ: {item['error_group']}")
        if not Path(item["source_log"]).exists():
            raise SystemExit(f"Không tìm thấy log nguồn: {item['source_log']}")
        ids.add(item["case_id"])
        counts[item["error_group"]] += 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / "h3_11_error_cases.csv"
    with detail_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(required))
        writer.writeheader()
        for item in cases:
            writer.writerow({key: item[key] for key in required})

    summary = {
        "task": "H3-11",
        "review_date": args.date,
        "total_new_cases": len(cases),
        "minimum_required": 2,
        "definition_of_done_met": len(cases) >= 2,
        "verification": payload.get("verification", {}),
        "counts": [
            {"error_group": key, "error_label": label, "count": counts[key]}
            for key, label in ALLOWED_GROUPS.items()
        ],
    }
    (out_dir / "h3_11_error_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "h3_11_error_cases.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"H3-11 ĐẠT: {len(cases)} case mới ngày {args.date} (yêu cầu >= 2).")
    for row in summary["counts"]:
        print(f"- {row['error_label']}: {row['count']}")
    print(f"Chi tiết: {detail_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
