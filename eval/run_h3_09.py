"""Chạy H3-09 song song cho 5 tenant và tổng hợp điểm theo tenant/chủ đề."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs" / "h3_09"
TENANTS = {
    "mima_internal": ("digital_agency", ROOT / "eval" / "cases_mima_internal.yaml"),
    "phongkham_hyhy": ("medical_clinic", ROOT / "eval" / "cases_phongkham_hyhy.yaml"),
    "bat_dong_san_phuoc_thinh": (
        "real_estate",
        ROOT / "eval" / "cases_bat_dong_san_phuoc_thinh.yaml",
    ),
    "giao_duc_haiyan": ("education", ROOT / "eval" / "cases_giao_duc_haiyan.yaml"),
    "thuc_pham_thien_minh": ("food", ROOT / "eval" / "cases_thuc_pham_thien_minh.yaml"),
}


def _run_tenant(tenant_id: str, cases_path: Path, rpm: float) -> Path:
    report_dir = OUTPUT_DIR / "reports" / tenant_id
    report_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "eval.run",
        "--cases",
        str(cases_path),
        "--tenant-id",
        tenant_id,
        "--report-dir",
        str(report_dir),
        "--workers",
        "3",
        "--requests-per-minute",
        str(rpm),
        "--time-budget-seconds",
        "900",
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    (report_dir / "run.log").write_text(
        completed.stdout + "\n--- STDERR ---\n" + completed.stderr,
        encoding="utf-8",
    )
    reports = sorted(report_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise RuntimeError(
            f"Eval {tenant_id} không sinh báo cáo JSON (exit={completed.returncode}); "
            f"xem {report_dir / 'run.log'}"
        )
    # eval.run trả 1 khi có FAIL và 2 khi có ERROR; đây là kết quả chất lượng cần
    # tổng hợp, không phải lỗi điều phối. Chỉ thiếu report mới là run thất bại.
    return reports[-1]


def _summarise(report_path: Path, industry: str) -> tuple[dict[str, Any], list[dict], list[dict]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]
    topic_rows: list[dict[str, Any]] = []
    for topic, metrics in summary.get("topic_metrics", {}).items():
        topic_rows.append(
            {
                "tenant_id": report["tenant_id"],
                "topic": topic,
                "passed": metrics["passed"],
                "evaluated": metrics["evaluated"],
                "pass_rate": metrics["pass_rate"],
                "errors": metrics["errors"],
                "average_latency_ms": metrics["average_latency_ms"],
            }
        )

    weakest_topic = min(
        topic_rows,
        key=lambda row: (row["pass_rate"], -row["evaluated"], row["topic"]),
        default={"topic": "n/a", "pass_rate": 0.0},
    )
    failed = [item for item in report["results"] if item["status"] != "PASS"]
    quality_failures = [item for item in report["results"] if item["status"] == "FAIL"]
    infrastructure_errors = [item for item in report["results"] if item["status"] == "ERROR"]
    stage_counts = Counter((item.get("diagnostic_stage") or "scoring") for item in failed)
    dominant_stage = stage_counts.most_common(1)[0][0] if stage_counts else "none"
    if summary["errors"]:
        reason = f"Có {summary['errors']} lỗi hạ tầng; stage chính: {dominant_stage}."
    elif failed:
        reason = (
            f"Yếu nhất ở nhóm {weakest_topic['topic']} "
            f"({weakest_topic['pass_rate']:.1%}); lỗi chính: {dominant_stage}."
        )
    else:
        reason = "Không có case sai trong bộ 15 câu hiện tại."

    # Tách lỗi chất lượng khỏi lỗi hạ tầng để bảng điểm không quy lỗi provider thành
    # lỗi kiến thức của bot, đồng thời vẫn tính tỷ lệ đạt trên toàn bộ 15 case.
    quality_stages = Counter(
        (item.get("diagnostic_stage") or "scoring") for item in quality_failures
    )
    error_stages = Counter(
        (item.get("diagnostic_stage") or "provider_error") for item in infrastructure_errors
    )
    quality_stage = quality_stages.most_common(1)[0][0] if quality_stages else "none"
    error_stage = error_stages.most_common(1)[0][0] if error_stages else "none"
    if not quality_failures and all(row["pass_rate"] >= 1.0 for row in topic_rows):
        weakest_topic = {"topic": "Không có", "pass_rate": 1.0}
    reason_parts: list[str] = []
    if quality_failures:
        reason_parts.append(
            f"Có {len(quality_failures)} câu sai; nhóm yếu nhất {weakest_topic['topic']} "
            f"({weakest_topic['pass_rate']:.1%}), nguyên nhân chính: {quality_stage}"
        )
    else:
        reason_parts.append("Không có câu sai trong các case đã chấm được")
    if infrastructure_errors:
        reason_parts.append(
            f"có {len(infrastructure_errors)} lỗi hạ tầng riêng ({error_stage}), "
            "không tính thành lỗi kiến thức"
        )
    reason = "; ".join(reason_parts) + "."

    comparison = {
        "tenant_id": report["tenant_id"],
        "industry": industry,
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "errors": summary["errors"],
        "pass_rate": summary["pass_rate"],
        "effective_pass_rate": summary["passed"] / summary["total"] if summary["total"] else 0.0,
        "completion_rate": summary["completion_rate"],
        "average_cost_usd": summary["average_cost_usd"],
        "average_latency_ms": summary["average_latency_ms"],
        "weakest_topic": weakest_topic["topic"],
        "weakest_topic_pass_rate": weakest_topic["pass_rate"],
        "diagnosis": reason,
        "report_path": str(report_path.relative_to(ROOT)).replace("\\", "/"),
    }
    details = [
        {
            "tenant_id": report["tenant_id"],
            "id": item["id"],
            "topic": item["topic"],
            "status": item["status"],
            "score": item["score"],
            "question": item["question"],
            "reply": item["reply"],
            "failed_checks": item.get("failed_checks", ""),
            "diagnostic_stage": item.get("diagnostic_stage") or "",
            "cost_usd": item["cost_usd"],
            "latency_ms": item["latency_ms"],
        }
        for item in report["results"]
    ]
    return comparison, details, topic_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_report(comparison: list[dict[str, Any]], weakest: dict[str, Any]) -> None:
    lines = [
        "# H3-09 — So sánh chất lượng 5 tenant",
        "",
        "> Điểm được chấm trên 15 câu riêng/tenant, cùng evaluator và cùng tiêu chí keyword; "
        "không lấy điểm MIMA đại diện cho ngành khác.",
        "",
        "| Tenant | Ngành | Đạt | Tỷ lệ đúng | Lỗi | Chi phí TB | Độ trễ TB | Nhóm yếu nhất |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in comparison:
        lines.append(
            f"| `{row['tenant_id']}` | {row['industry']} | {row['passed']}/{row['total']} | "
            f"{row['effective_pass_rate']:.1%} toàn bộ / {row['pass_rate']:.1%} đã chấm | "
            f"{row['errors']} | ${row['average_cost_usd']:.8f} | "
            f"{row['average_latency_ms']:.0f} ms | {row['weakest_topic']} "
            f"({row['weakest_topic_pass_rate']:.1%}) |"
        )
    lines.extend(
        [
            "",
            "## Tenant yếu nhất",
            "",
            f"- **{weakest['tenant_id']} — {weakest['pass_rate']:.1%}.**",
            f"- {weakest['diagnosis']}",
            "",
            "## Cách đọc kết quả",
            "",
            "- `ERROR` là lỗi hạ tầng/provider, không được nhập chung thành lỗi kiến thức.",
            "- `FAIL` là câu trả lời không đạt keyword/ràng buộc tenant của case.",
            "- Xem `details.csv` để đọc nguyên câu hỏi, reply và tiêu chí sai; xem `topics.csv` để so theo chủ đề.",
            "",
            "## Bẫy đã tránh",
            "",
            "- Mỗi tenant dùng câu hỏi lấy từ index của chính website đó.",
            "- Mỗi case đều cấm xuất hiện tên tenant khác để kiểm tra rò chéo ở mức câu trả lời.",
            "- Kết luận tenant yếu nhất dựa trên điểm riêng và chủ đề yếu, không suy rộng từ MIMA.",
        ]
    )
    (OUTPUT_DIR / "H3-09-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Chạy eval H3-09 cho 5 tenant")
    parser.add_argument("--requests-per-minute", type=float, default=30.0)
    parser.add_argument(
        "--reuse-reports",
        action="store_true",
        help="Không gọi API; tổng hợp các report mới nhất đã chạy của từng tenant.",
    )
    args = parser.parse_args()
    if args.requests_per_minute <= 0:
        parser.error("--requests-per-minute phải > 0")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_paths: dict[str, Path] = {}
    failures: list[str] = []
    if args.reuse_reports:
        for tenant_id in TENANTS:
            report_dir = OUTPUT_DIR / "reports" / tenant_id
            reports = sorted(report_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
            if reports:
                report_paths[tenant_id] = reports[-1]
                print(f"[REUSE] {tenant_id}: {reports[-1]}")
            else:
                failures.append(f"{tenant_id}: chưa có report JSON để tổng hợp")
    else:
        # Chạy tenant song song; mỗi tenant vẫn tự giới hạn RPM và worker bên trong.
        with ThreadPoolExecutor(max_workers=len(TENANTS)) as executor:
            futures = {
                executor.submit(_run_tenant, tenant_id, cases, args.requests_per_minute): tenant_id
                for tenant_id, (_, cases) in TENANTS.items()
            }
            for future in as_completed(futures):
                tenant_id = futures[future]
                try:
                    report_paths[tenant_id] = future.result()
                    print(f"[DONE] {tenant_id}: {report_paths[tenant_id]}")
                except Exception as exc:
                    failures.append(f"{tenant_id}: {exc}")
                    print(f"[ERROR] {tenant_id}: {exc}", file=sys.stderr)

    if failures:
        (OUTPUT_DIR / "run-errors.json").write_text(
            json.dumps({"errors": failures}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 1

    comparison: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    topics: list[dict[str, Any]] = []
    for tenant_id, (industry, _) in TENANTS.items():
        row, tenant_details, tenant_topics = _summarise(report_paths[tenant_id], industry)
        comparison.append(row)
        details.extend(tenant_details)
        topics.extend(tenant_topics)
    # Xếp hạng theo toàn bộ 15 case để tenant có lỗi hạ tầng không được nhìn tốt giả tạo.
    comparison.sort(
        key=lambda row: (row["effective_pass_rate"], row["pass_rate"], row["tenant_id"])
    )
    weakest = comparison[0]

    payload = {
        "schema_version": "h3-09.comparison.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(details),
        "tenant_count": len(comparison),
        "weakest_tenant": weakest["tenant_id"],
        "comparison": comparison,
    }
    (OUTPUT_DIR / "comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_csv(OUTPUT_DIR / "comparison.csv", comparison)
    _write_csv(OUTPUT_DIR / "details.csv", details)
    _write_csv(OUTPUT_DIR / "topics.csv", topics)
    _write_report(comparison, weakest)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
