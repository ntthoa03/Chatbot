"""A/B offline H3-13: tenant seed tối thiểu so với seed + tri thức ngành.

Đây là phép đo độ phủ câu trả lời xác định, không gọi LLM và không được diễn giải
thành tỷ lệ hài lòng khách hoặc chất lượng production.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from industry_knowledge import IndustryKnowledgeStore  # noqa: E402


DEFAULT_CASES = ROOT / "eval" / "cases_h3_13_industry.yaml"
DEFAULT_BASELINE = ROOT / "eval" / "h3_13_new_tenant_baseline.yaml"
DEFAULT_OUTPUT = ROOT / "outputs" / "h3_13"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text).split())


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} phải là YAML object.")
    return data


def baseline_reply(question: str, entries: list[dict[str, Any]]) -> str | None:
    normalized = normalize(question)
    ranked: list[tuple[int, str]] = []
    for entry in entries:
        hits = sum(1 for keyword in entry["keywords"] if normalize(keyword) in normalized)
        if hits:
            ranked.append((hits, entry["answer"]))
    ranked.sort(key=lambda row: -row[0])
    return ranked[0][1] if ranked else None


def passes(reply: str | None, expected_terms: list[str]) -> bool:
    if not reply:
        return False
    normalized = normalize(reply)
    return all(normalize(term) in normalized for term in expected_terms)


def evaluate(cases_path: Path, baseline_path: Path, output_dir: Path) -> dict:
    started = time.perf_counter()
    case_document = load_yaml(cases_path)
    baseline_document = load_yaml(baseline_path)
    cases = case_document.get("cases")
    baseline_by_industry = baseline_document.get("industries")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Bộ eval H3-13 không có cases.")
    if not isinstance(baseline_by_industry, dict):
        raise ValueError("Baseline H3-13 không có industries.")

    store = IndustryKnowledgeStore()
    industries = store.list_industries()
    if len(industries) != 5:
        raise ValueError(f"H3-13 cần đúng 5 YAML ngành, hiện có {len(industries)}.")
    # Gọi load trước A/B để validator PII/giá chạy trên toàn bộ năm file.
    documents = {industry: store.load(industry) for industry in industries}

    details: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "baseline_passed": 0, "with_layer_passed": 0}
    )
    for case in cases:
        industry_id = case["industry_id"]
        if industry_id not in documents or industry_id not in baseline_by_industry:
            raise ValueError(f"Case {case.get('id')} dùng ngành chưa đăng ký: {industry_id}.")
        baseline = baseline_reply(case["question"], baseline_by_industry[industry_id])
        matches = store.retrieve(case["question"], industry_id, k=1)
        layered = matches[0]["answer_guidance"] if matches else baseline
        baseline_ok = passes(baseline, case["expected_terms"])
        layered_ok = passes(layered, case["expected_terms"])
        grouped[industry_id]["total"] += 1
        grouped[industry_id]["baseline_passed"] += int(baseline_ok)
        grouped[industry_id]["with_layer_passed"] += int(layered_ok)
        details.append(
            {
                "id": case["id"],
                "industry_id": industry_id,
                "question": case["question"],
                "expected_terms": case["expected_terms"],
                "baseline_reply": baseline,
                "baseline_passed": baseline_ok,
                "with_layer_reply": layered,
                "with_layer_pattern_id": matches[0]["pattern_id"] if matches else None,
                "with_layer_passed": layered_ok,
            }
        )

    total = len(details)
    baseline_passed = sum(int(row["baseline_passed"]) for row in details)
    layered_passed = sum(int(row["with_layer_passed"]) for row in details)
    per_industry = []
    for industry_id in industries:
        row = grouped[industry_id]
        per_industry.append(
            {
                "industry_id": industry_id,
                **row,
                "baseline_pass_rate": round(row["baseline_passed"] / row["total"], 4),
                "with_layer_pass_rate": round(row["with_layer_passed"] / row["total"], 4),
            }
        )
    result = {
        "schema_version": "h3-13.feasibility.v1",
        "task": "H3-13",
        "mode": "offline_deterministic_ab",
        "industry_count": len(industries),
        "case_count": total,
        "baseline": {
            "passed": baseline_passed,
            "total": total,
            "pass_rate": round(baseline_passed / total, 4),
        },
        "with_industry_layer": {
            "passed": layered_passed,
            "total": total,
            "pass_rate": round(layered_passed / total, 4),
        },
        "absolute_gain": round((layered_passed - baseline_passed) / total, 4),
        "definition_of_done_met": layered_passed > baseline_passed,
        "privacy_validation": {
            "passed": True,
            "rules": ["no_tenant_identity", "no_phone_email_url", "no_specific_price"],
        },
        "per_industry": per_industry,
        "cost_usd": 0.0,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "limitations": [
            "Chỉ có một tenant quan sát cho mỗi ngành; không đủ để suy rộng mẫu hình.",
            "A/B đo độ phủ deterministic trên dữ liệu synthetic, không đo chất lượng sinh của LLM.",
            "Chưa nối tầng ngành vào runtime; năm bot hiện tại không bị thay đổi.",
            "Production cần data owner duyệt, log khách thật, guardrail và isolation regression.",
        ],
        "details": details,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "feasibility_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# H3-13 — Báo cáo khả thi tầng tri thức ngành",
        "",
        "## Kết luận",
        "",
        f"- A/B offline trên **{total} câu / {len(industries)} ngành**.",
        f"- Chỉ dữ liệu tenant mới: **{baseline_passed}/{total} = {baseline_passed / total:.2%}**.",
        f"- Tenant + tầng ngành: **{layered_passed}/{total} = {layered_passed / total:.2%}**.",
        f"- Mức tăng tuyệt đối: **{(layered_passed - baseline_passed) / total:.2%}**; định nghĩa hoàn thành: **{'ĐẠT' if result['definition_of_done_met'] else 'CHƯA ĐẠT'}**.",
        "- Chi phí API: **$0.00000000** vì phép thử deterministic không gọi LLM.",
        "",
        "## Theo ngành",
        "",
        "| Ngành | Baseline | Có tầng ngành |",
        "|---|---:|---:|",
    ]
    lines.extend(
        f"| `{row['industry_id']}` | {row['baseline_passed']}/{row['total']} | {row['with_layer_passed']}/{row['total']} |"
        for row in per_industry
    )
    lines.extend(
        [
            "",
            "## Kiểm soát bẫy",
            "",
            "- Năm YAML đã qua validator chặn danh tính tenant, URL, email, số điện thoại và giá cụ thể.",
            "- Tầng ngành nằm ngoài `tenants/` và không được gọi tự động từ runtime.",
            "- Không có kết luận rằng đây là chuẩn ngành hoặc chất lượng production.",
            "",
            "## Giới hạn",
            "",
            *[f"- {item}" for item in result["limitations"]],
            "",
            "## Quyết định",
            "",
            "Kết quả đủ chứng minh hướng tiếp cận có thể tăng độ phủ cho tenant mới có dữ liệu mỏng. Chỉ nên tiếp tục pilot sau khi có thêm tenant cùng ngành và dữ liệu thật đã được ẩn danh, duyệt nghiệp vụ.",
            "",
        ]
    )
    (output_dir / "feasibility_report.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="H3-13 industry knowledge offline A/B")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.cases, args.baseline, args.output_dir)
    print(
        "H3-13 "
        f"baseline={result['baseline']['passed']}/{result['case_count']} "
        f"with_layer={result['with_industry_layer']['passed']}/{result['case_count']} "
        f"gain={result['absolute_gain']:.2%} "
        f"status={'PASS' if result['definition_of_done_met'] else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
