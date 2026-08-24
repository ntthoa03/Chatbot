"""Run reproducible semantic smoke queries against one local tenant index."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_core.retriever import retrieve


DEFAULT_QUERIES = [
    "Gói khám đánh giá nguy cơ đột quỵ gồm những gì?",
    "Phòng khám có khám tim mạch không?",
    "Địa chỉ và giờ làm việc của phòng khám",
    "Bác sĩ Hồ Hữu Thật chuyên khoa gì?",
    "Xét nghiệm HbA1c là gì?",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test semantic retrieval của index theo tenant")
    parser.add_argument("--index-dir", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--wrong-tenant", default="__wrong_tenant_probe__")
    parser.add_argument("--output", required=True)
    parser.add_argument("--threshold", type=float, default=0.30)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--query", action="append", dest="queries")
    args = parser.parse_args()

    queries = args.queries or DEFAULT_QUERIES
    rows = []
    for query in queries:
        results = retrieve(
            query,
            args.tenant_id,
            k=args.top_k,
            threshold=args.threshold,
            relative_score_margin=1.0,
            index_dir=args.index_dir,
            backend="local",
        )
        rows.append(
            {
                "query": query,
                "result_count": len(results),
                "results": [
                    {
                        "score": item["score"],
                        "title": item.get("metadata", {}).get("title"),
                        "url": item.get("metadata", {}).get("url"),
                        "content_preview": item.get("content", "")[:240],
                    }
                    for item in results
                ],
            }
        )

    isolation_probe = retrieve(
        queries[0],
        args.wrong_tenant,
        k=args.top_k,
        threshold=0.0,
        relative_score_margin=1.0,
        index_dir=args.index_dir,
        backend="local",
    )
    report = {
        "schema_version": "h2-04.index-smoke.v1",
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "index_dir": args.index_dir,
        "tenant_id": args.tenant_id,
        "threshold": args.threshold,
        "top_k": args.top_k,
        "queries": rows,
        "isolation_probe": {
            "wrong_tenant": args.wrong_tenant,
            "result_count": len(isolation_probe),
            "passed": len(isolation_probe) == 0,
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    for row in rows:
        top = row["results"][0] if row["results"] else None
        print(f"{row['query']} -> {row['result_count']} kết quả; top={top['title'] if top else 'NONE'}")
    print(f"Tenant isolation: {'PASS' if report['isolation_probe']['passed'] else 'FAIL'}")


if __name__ == "__main__":
    main()
