"""Chạy thí nghiệm H2-09: strong-all so với routing tự động trên cùng 60 case."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

from ai_core.chat import chat_for_eval
from ai_core.cache import reset_semantic_cache
from ai_core.evaluator import EvalReport, load_cases, report_as_dict, run_eval, save_report
from ai_core.router import decide_model_route
from ai_core.trace import find_trace
from eval.run_h2_07 import build_h2_01_cases


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "outputs" / "h2_09"
CASES_PATH = OUTPUT_ROOT / "inputs" / "h2_01_60_cases.yaml"


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def prepare_cases() -> None:
    """Dùng đúng 60 case H2-01; không tự tạo suite dễ hơn cho routing."""

    build_h2_01_cases(CASES_PATH)


def _run_profile(
    *,
    profile: str,
    model_role: str,
    workers: int,
    requests_per_minute: float | None,
) -> EvalReport:
    # H2-09 chỉ đo tác động của model routing. Xóa cache giữa hai profile để
    # câu trả lời của baseline không thể bị tái sử dụng cho lượt auto-routing.
    reset_semantic_cache()

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        return chat_for_eval(payload, model_role=model_role)  # type: ignore[arg-type]

    report = run_eval(
        CASES_PATH,
        execute,
        tenant_id="mima_internal",
        config_version=1,
        workers=workers,
        requests_per_minute=requests_per_minute,
        diagnostic_resolver=find_trace,
        experiment_context={
            "task": "H2-09",
            "profile": profile,
            "model_role": model_role,
            "cache_enabled": False,
            "judge_enabled": False,
            "dataset": "H2-01-60",
        },
    )
    save_report(report, OUTPUT_ROOT / "reports" / profile)
    return report


def _result_map(report: EvalReport) -> dict[str, Any]:
    return {item.id: item for item in report.results}


def write_comparison(baseline: EvalReport, routed: EvalReport) -> tuple[Path, Path, Path]:
    baseline_by_id = _result_map(baseline)
    routed_by_id = _result_map(routed)
    cases = load_cases(CASES_PATH)
    baseline_cost = baseline.summary.average_cost_usd
    routed_cost = routed.summary.average_cost_usd
    cost_reduction = (
        (baseline_cost - routed_cost) / baseline_cost if baseline_cost > 0 else 0.0
    )
    quality_delta = routed.summary.pass_rate - baseline.summary.pass_rate
    acceptance = cost_reduction >= 0.30 and quality_delta >= -0.02

    route_counts = {"simple": 0, "complex": 0}
    detail_rows: list[dict[str, Any]] = []
    for case in cases:
        route = decide_model_route(case.question)
        route_counts[route.complexity] += 1
        before = baseline_by_id[case.id]
        after = routed_by_id[case.id]
        detail_rows.append({
            "id": case.id,
            "topic": case.topic,
            "question": case.question,
            "complexity": route.complexity,
            "selected_role": route.model_role,
            "route_reason": route.reason,
            "baseline_status": before.status,
            "baseline_model": before.model,
            "baseline_cost_usd": before.cost_usd,
            "baseline_latency_ms": before.latency_ms,
            "routed_status": after.status,
            "routed_model": after.model,
            "routed_cost_usd": after.cost_usd,
            "routed_latency_ms": after.latency_ms,
            "cost_delta_usd": after.cost_usd - before.cost_usd,
        })

    summary = {
        "schema_version": "h2-09.comparison.v1",
        "task": "H2-09",
        "dataset": "H2-01-60",
        "baseline": {
            "profile": "strong_all",
            "pass_rate": baseline.summary.pass_rate,
            "average_cost_usd": baseline_cost,
            "average_latency_ms": baseline.summary.average_latency_ms,
            "total_cost_usd": baseline.summary.total_cost_usd,
            "run_id": baseline.run_id,
        },
        "routed": {
            "profile": "auto_routing",
            "pass_rate": routed.summary.pass_rate,
            "average_cost_usd": routed_cost,
            "average_latency_ms": routed.summary.average_latency_ms,
            "total_cost_usd": routed.summary.total_cost_usd,
            "run_id": routed.run_id,
        },
        "delta": {
            "cost_reduction_rate": round(cost_reduction, 6),
            "pass_rate_delta": round(quality_delta, 6),
            "latency_delta_ms": round(
                routed.summary.average_latency_ms - baseline.summary.average_latency_ms, 2
            ),
        },
        "routing": route_counts,
        "acceptance": {
            "cost_reduction_at_least_30_percent": cost_reduction >= 0.30,
            "pass_rate_drop_no_more_than_2_percent": quality_delta >= -0.02,
            "h2_09_passed": acceptance,
        },
    }

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_ROOT / "h2_09_comparison.json"
    csv_path = OUTPUT_ROOT / "h2_09_case_details.csv"
    md_path = OUTPUT_ROOT / "H2-09-bao-cao.md"
    json_path.write_text(
        json.dumps(
            {
                **summary,
                "baseline_report": report_as_dict(baseline),
                "routed_report": report_as_dict(routed),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(detail_rows[0]))
        writer.writeheader()
        writer.writerows(detail_rows)

    status = "ĐẠT" if acceptance else "CHƯA ĐẠT"
    md_path.write_text(
        "\n".join([
            "# H2-09 — Model routing",
            "",
            f"**Kết luận nghiệm thu: {status}.**",
            "",
            "| Chỉ số | Strong-all | Auto routing | Chênh lệch |",
            "|---|---:|---:|---:|",
            f"| Tỷ lệ đúng | {baseline.summary.pass_rate:.2%} | {routed.summary.pass_rate:.2%} | {quality_delta:+.2%} |",
            f"| Chi phí TB/lượt | ${baseline_cost:.8f} | ${routed_cost:.8f} | {-cost_reduction:+.2%} |",
            f"| Độ trễ TB | {baseline.summary.average_latency_ms:.2f} ms | {routed.summary.average_latency_ms:.2f} ms | {summary['delta']['latency_delta_ms']:+.2f} ms |",
            f"| Tổng chi phí | ${baseline.summary.total_cost_usd:.8f} | ${routed.summary.total_cost_usd:.8f} | ${routed.summary.total_cost_usd - baseline.summary.total_cost_usd:+.8f} |",
            "",
            "## Phân bổ routing",
            "",
            f"- Câu tra cứu đơn giản → model rẻ: {route_counts['simple']}/60.",
            f"- Câu suy luận/tư vấn → model mạnh: {route_counts['complex']}/60.",
            "- Output model rẻ bị `ungrounded_claim` được nâng cấp sang model mạnh và kiểm duyệt lại.",
            "- Quy tắc cấm cứng và giá không có nguồn không được nâng cấp để lách chặn.",
            "",
            "## Tiêu chí",
            "",
            f"- Giảm chi phí ≥30%: {'đạt' if cost_reduction >= 0.30 else 'chưa đạt'} ({cost_reduction:.2%}).",
            f"- Điểm eval không giảm quá 2%: {'đạt' if quality_delta >= -0.02 else 'chưa đạt'} ({quality_delta:+.2%}).",
            "",
            "Chi tiết từng case nằm trong `h2_09_case_details.csv`; dữ liệu thô và report hai lần chạy nằm trong `h2_09_comparison.json`.",
            "",
            "## Ghi chú phương pháp",
            "",
            "- Cache được tắt bằng `AI_CORE_SEMANTIC_CACHE_ENABLED=false` và reset giữa hai profile.",
            "- Baseline ép toàn bộ câu qua model mạnh; auto-routing dùng cùng dataset, config RAG và evaluator.",
            "- Nếu chưa đạt 30%, báo cáo giữ nguyên `CHƯA ĐẠT`; không nới guardrail để ép chỉ số.",
        ]),
        encoding="utf-8",
    )
    return json_path, csv_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chạy eval strong-all và auto-routing cho H2-09.")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--requests-per-minute", type=float, default=0.0)
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    _configure_utf8_console()
    args = parse_args()
    if args.workers <= 0 or args.requests_per_minute < 0:
        raise ValueError("workers phải > 0 và requests-per-minute phải >= 0.")
    prepare_cases()
    if args.prepare_only:
        print(f"Đã chuẩn bị {CASES_PATH}")
        return 0

    # H2-09 đo routing model, không đo cache H2-08; tắt cache cho cả hai profile.
    os.environ["AI_CORE_SEMANTIC_CACHE_ENABLED"] = "false"
    rpm = args.requests_per_minute or None
    print("[1/2] Baseline: model mạnh cho tất cả case", flush=True)
    baseline = _run_profile(
        profile="strong_all",
        model_role="fallback",
        workers=args.workers,
        requests_per_minute=rpm,
    )
    print("[2/2] Sau H2-09: routing tự động", flush=True)
    routed = _run_profile(
        profile="auto_routing",
        model_role="auto",
        workers=args.workers,
        requests_per_minute=rpm,
    )
    paths = write_comparison(baseline, routed)
    print("\n".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
