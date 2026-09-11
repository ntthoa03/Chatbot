"""Aggregate H4-03 accuracy and retrieval coverage across tenant eval reports."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_core.config import load_config
from ai_core.evaluator import _extract_data_diagnostics
from ai_core.trace import trace_path
from eval.run_h3_09 import _write_dual_axis_chart


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "outputs" / "h4_03"


def build_rows(report_paths: list[Path]) -> list[dict[str, Any]]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    legacy_trace_ids = {
        str(item.get("trace_id"))
        for report in reports
        if "data_sufficiency_rate" not in report.get("summary", {})
        for item in report.get("results", [])
        if item.get("trace_id")
    }
    traces: dict[str, dict[str, Any]] = {}
    if legacy_trace_ids:
        try:
            lines = trace_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                trace = json.loads(line)
            except json.JSONDecodeError:
                continue
            trace_id = str(trace.get("trace_id", ""))
            if trace_id in legacy_trace_ids:
                traces[trace_id] = trace
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, report in zip(report_paths, reports, strict=True):
        tenant_id = str(report.get("tenant_id", "")).strip()
        summary = report.get("summary")
        if not tenant_id or not isinstance(summary, dict):
            raise ValueError(f"Báo cáo không hợp lệ: {path}")
        if tenant_id in seen:
            raise ValueError(f"Tenant bị trùng: {tenant_id}")
        if "data_sufficiency_rate" in summary:
            case_data_statuses = [
                str(item.get("data_status", "unknown")) for item in report.get("results", [])
            ]
            data_metrics = {
                "data_sufficient_count": int(summary.get("data_sufficient_count", 0)),
                "data_sufficiency_rate": float(summary["data_sufficiency_rate"]),
                "data_insufficient_count": int(summary.get("data_insufficient_count", 0)),
                "retrieval_error_count": int(summary.get("retrieval_error_count", 0)),
                "not_applicable_count": int(summary.get("retrieval_not_applicable_count", 0)),
                "unknown_count": int(summary.get("data_unknown_count", 0)),
                "metric_source": "eval_report",
            }
        else:
            threshold = float(
                load_config(tenant_id, int(report.get("config_version", 1))).retrieval_policy.min_score
            )
            case_data_statuses: list[str] = []
            for item in report.get("results", []):
                trace = traces.get(str(item.get("trace_id", "")))
                if trace and isinstance(trace.get("retrieval"), dict):
                    trace = dict(trace)
                    retrieval = dict(trace["retrieval"])
                    retrieval.setdefault("threshold", threshold)
                    trace["retrieval"] = retrieval
                case_data_statuses.append(_extract_data_diagnostics(trace)["data_status"])
            total_cases = int(summary.get("total", len(case_data_statuses)))
            sufficient = case_data_statuses.count("sufficient")
            data_metrics = {
                "data_sufficient_count": sufficient,
                "data_sufficiency_rate": round(sufficient / total_cases, 4) if total_cases else 0.0,
                "data_insufficient_count": case_data_statuses.count("insufficient"),
                "retrieval_error_count": case_data_statuses.count("retrieval_error"),
                "not_applicable_count": case_data_statuses.count("not_applicable"),
                "unknown_count": case_data_statuses.count("unknown")
                + max(0, total_cases - len(case_data_statuses)),
                "metric_source": "legacy_trace_backfill",
            }
        wrong_pairs = [
            (str(item.get("status", "")), data_status)
            for item, data_status in zip(
                report.get("results", []), case_data_statuses, strict=False
            )
            if item.get("status") != "PASS"
        ]
        cause_metrics = {
            "wrong_missing_data_count": sum(
                status == "FAIL" and data_status == "insufficient"
                for status, data_status in wrong_pairs
            ),
            "wrong_rag_prompt_count": sum(
                status == "FAIL" and data_status == "sufficient"
                for status, data_status in wrong_pairs
            ),
            "wrong_retrieval_error_count": sum(
                data_status == "retrieval_error" for _, data_status in wrong_pairs
            ),
        }
        cause_metrics["wrong_other_count"] = len(wrong_pairs) - sum(cause_metrics.values())
        total = int(summary.get("total", 0))
        passed = int(summary.get("passed", 0))
        row = {
            "tenant_id": tenant_id,
            "total": total,
            "passed": passed,
            "effective_pass_rate": round(passed / total, 4) if total else 0.0,
            "pass_rate": float(summary.get("pass_rate", 0)),
            **data_metrics,
            **cause_metrics,
            "report_path": str(path),
        }
        rows.append(row)
        seen.add(tenant_id)
    return sorted(rows, key=lambda item: item["tenant_id"])


def save_dashboard(rows: list[dict[str, Any]], output_dir: Path) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    csv_path = output_dir / "report.csv"
    md_path = output_dir / "report.md"
    chart_path = output_dir / "accuracy-vs-data-sufficiency.svg"
    payload = {
        "schema_version": "h4-03.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "formula": "retrievals_at_or_above_threshold / total_questions",
        "tenants": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if rows:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = [
        "# H4-03 — Độ đúng và độ phủ dữ liệu",
        "",
        "Công thức độ phủ: số câu có retrieval score ≥ threshold / tổng số câu hỏi.",
        "",
        "| Tenant | Tỷ lệ đúng | Đủ dữ liệu | Sai do thiếu data | Sai dù đủ data | Lỗi retrieval | Khác |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['tenant_id']}` | {row['effective_pass_rate']:.1%} | "
            f"{row['data_sufficiency_rate']:.1%} | {row['wrong_missing_data_count']} | "
            f"{row['wrong_rag_prompt_count']} | {row['wrong_retrieval_error_count']} | "
            f"{row['wrong_other_count']} |"
        )
    lines.extend([
        "",
        "- `Sai do thiếu data`: ưu tiên bổ sung/crawl lại tri thức tenant.",
        "- `Sai dù đủ data`: retrieval đã vượt ngưỡng; ưu tiên kiểm tra ranking, context, prompt hoặc guardrail.",
        "- `Lỗi retrieval`: lỗi kỹ thuật được tách khỏi thiếu tri thức.",
        "",
        "## Biểu đồ hai trục theo tenant",
        "",
        "- Trục trái: tỷ lệ đúng.",
        "- Trục phải: tỷ lệ có đủ dữ liệu.",
        "",
        "![Tỷ lệ đúng và tỷ lệ có đủ dữ liệu theo tenant](accuracy-vs-data-sufficiency.svg)",
    ])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_dual_axis_chart(chart_path, rows)
    return json_path, csv_path, md_path, chart_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Tổng hợp report H4-03 theo tenant")
    parser.add_argument("reports", nargs="+", type=Path, help="Các file eval report JSON")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    paths = save_dashboard(build_rows(args.reports), args.output_dir)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
