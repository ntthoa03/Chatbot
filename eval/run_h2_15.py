"""Chạy nghiệm thu H2-15 trên 10 câu ngoài phạm vi và xuất bảng input/reply."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import uuid4

from ai_core.chat import chat_for_eval
from ai_core.config import load_config
from ai_core.trace import find_trace


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs" / "h2_15"
TENANT_ID = "mima_internal"
QUESTIONS = (
    "bên mình có nhận sửa máy lạnh tại nhà không em",
    "đặt vé máy bay đi Đà Nẵng giúp anh được không",
    "em biết quán ăn gia đình nào ngon ở quận 3 không",
    "bên em có dạy tiếng Anh giao tiếp buổi tối không",
    "chị muốn mua bảo hiểm ô tô thì đăng ký ở đâu",
    "công ty có nhận chuyển nhà trọn gói không vậy",
    "xe máy anh bị hết bình bên em sửa được không",
    "bên mình có chụp ảnh cưới ngoại cảnh không em",
    "chị cần làm báo cáo thuế quý thì bên em nhận không",
    "có tour Phú Quốc ba ngày hai đêm không bạn",
)


def main() -> int:
    # H2-15 đo fallback độc lập; tắt cache để mọi câu đều đi qua retrieval thật.
    os.environ["AI_CORE_SEMANTIC_CACHE_ENABLED"] = "0"
    config = load_config(TENANT_ID, 1)
    rows: list[dict] = []
    for index, question in enumerate(QUESTIONS, start=1):
        response = chat_for_eval(
            {
                "tenant_id": TENANT_ID,
                "conversation_id": str(uuid4()),
                "message": question,
                "history": [],
                "config_version": 1,
            }
        )
        trace = find_trace(str(response["trace_id"])) or {}
        retrieval = trace.get("retrieval") if isinstance(trace.get("retrieval"), dict) else {}
        candidates = retrieval.get("fallback_candidates")
        if not isinstance(candidates, list):
            candidates = []
        bullets = [line for line in response["reply"].splitlines() if line.startswith("- ")]
        model = trace.get("model") if isinstance(trace.get("model"), dict) else {}
        passed = bool(
            config.lead.no_data_retry_message in response["reply"]
            and config.refusal_message in response["reply"]
            and 2 <= len(bullets) <= 3
            and len(candidates) == len(bullets)
            and not response["sources"]
            and not response["guardrail"]["blocked"]
        )
        rows.append(
            {
                "id": f"H215-{index:02d}",
                "question": question,
                "reply": response["reply"],
                "suggestion_count": len(bullets),
                "suggested_questions": " | ".join(line[2:] for line in bullets),
                "candidate_chunk_ids": " | ".join(
                    str(item.get("chunk_id", "")) for item in candidates
                ),
                "sources_returned": len(response["sources"]),
                "model_called": bool(model.get("called", False)),
                "cost_usd": response["usage"]["cost_usd"],
                "latency_ms": response["usage"]["latency_ms"],
                "trace_id": response["trace_id"],
                "passed": passed,
            }
        )

    passed_count = sum(row["passed"] for row in rows)
    summary = {
        "schema_version": "h2-15.run.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "tenant_id": TENANT_ID,
        "total": len(rows),
        "passed": passed_count,
        "failed": len(rows) - passed_count,
        "pass_rate": round(passed_count / len(rows), 4),
        "average_latency_ms": round(sum(row["latency_ms"] for row in rows) / len(rows), 2),
        "total_model_cost_usd": round(sum(row["cost_usd"] for row in rows), 4),
        "results": rows,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "H2-15-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (OUTPUT_DIR / "H2-15-results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False, indent=2))
    return 0 if passed_count == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
