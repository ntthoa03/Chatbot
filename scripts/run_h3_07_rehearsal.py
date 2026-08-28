"""Kiểm tra readiness H3-07 hai lần, không gọi mạng hay API trả phí.

Script không giả vờ thay live demo. Nó xác nhận artifact/config/index/prompt/metric
cần cho trọn kịch bản đã sẵn sàng và ghi báo cáo audit được.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs" / "h3_07"
TENANTS = (
    "mima_internal",
    "phongkham_hyhy",
    "bat_dong_san_phuoc_thinh",
    "giao_duc_haiyan",
    "thuc_pham_thien_minh",
)


def check_once(run_number: int) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    for tenant_id in TENANTS:
        config_path = ROOT / "tenants" / f"{tenant_id}.yaml"
        checks[f"config:{tenant_id}"] = config_path.is_file()
        if config_path.is_file():
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            checks[f"config_tenant_id:{tenant_id}"] = config.get("tenant_id") == tenant_id

    catalog_path = ROOT / "outputs" / "h3_01" / "index_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_text = json.dumps(catalog, ensure_ascii=False)
    for tenant_id in TENANTS:
        checks[f"index_catalog:{tenant_id}"] = tenant_id in catalog_text

    metrics_path = ROOT / "outputs" / "h2_09" / "h2_09_comparison.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))["routed"]
    checks["metric_pass_rate"] = metrics["pass_rate"] == 0.7966
    checks["metric_cost_usd"] = metrics["average_cost_usd"] == 0.000421588333
    checks["metric_latency_ms"] = metrics["average_latency_ms"] == 2059.52

    guardrail_path = ROOT / "outputs" / "h2_03" / "H2-03-summary.json"
    guardrail = json.loads(guardrail_path.read_text(encoding="utf-8"))
    checks["guardrail_30_of_30"] = (
        guardrail["trap_total"] == 30
        and guardrail["trap_guardrail_blocked"] == 30
        and guardrail["trap_need_human"] == 30
    )

    required_artifacts = (
        OUTPUT_DIR / "H3-07-demo-script.md",
        OUTPUT_DIR / "H3-07-demo-5-slides.pptx",
        OUTPUT_DIR / "H3-07-backup-demo.mp4",
    )
    for path in required_artifacts:
        checks[f"artifact:{path.name}"] = path.is_file() and path.stat().st_size > 0

    return {
        "run": run_number,
        "passed": all(checks.values()),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="H3-07 offline rehearsal readiness")
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be >= 1")

    runs = [check_once(number) for number in range(1, args.runs + 1)]
    report = {
        "schema_version": "h3-07.rehearsal.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline": True,
        "note": "Readiness rehearsal; live API demo remains a separate presenter step.",
        "runs": runs,
        "passed": all(item["passed"] for item in runs),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / "rehearsal-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
