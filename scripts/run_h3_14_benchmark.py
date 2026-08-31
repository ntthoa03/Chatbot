"""Chạy mẫu cố định H3-14 và lưu bằng chứng crawl 10 website.

Danh sách được lấy mẫu một lần bằng seed cố định trước khi crawl, vì vậy website
thất bại vẫn nằm trong báo cáo và không thể bị thay để làm đẹp tỷ lệ.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crawl_chunks import CrawlError, crawl, write_json


CANDIDATE_POOL = (
    "https://mimadigi.com",
    "https://phongkhamhyhy.com",
    "https://www.nhabanphuocthinh.com",
    "https://hannguhaiyan.edu.vn",
    "https://thucphamchaythienminh.com",
    "https://www.python.org",
    "https://www.djangoproject.com",
    "https://flask.palletsprojects.com",
    "https://www.sqlite.org",
    "https://www.iana.org",
    "https://fastapi.tiangolo.com",
    "https://www.w3.org",
)
SAMPLE_SEED = 314


def slug(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", url.lower().split("://", 1)[-1]).strip("_")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="H3-14: benchmark crawler trên mẫu 10 website")
    parser.add_argument("--output-dir", default="outputs/h3_14")
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--max-headless-pages", type=int, default=2)
    parser.add_argument("--no-headless-fallback", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_size = min(max(1, args.sample_size), len(CANDIDATE_POOL))
    selected = random.Random(SAMPLE_SEED).sample(list(CANDIDATE_POOL), sample_size)
    rows: list[dict] = []

    for position, url in enumerate(selected, start=1):
        site_slug = slug(url)
        started = time.perf_counter()
        try:
            chunks, manifest = crawl(
                base_url=url,
                tenant_id=f"h3_14_{site_slug}"[:64],
                max_pages=max(1, args.max_pages),
                timeout=max(1.0, args.timeout),
                delay_seconds=0.05,
                target_chars=900,
                overlap_chars=120,
                min_chars=80,
                min_chunks=1,
                headless_fallback=not args.no_headless_fallback,
                max_headless_pages=max(0, args.max_headless_pages),
            )
            write_json(output_dir / f"{position:02d}_{site_slug}_chunks.json", chunks)
            write_json(output_dir / f"{position:02d}_{site_slug}_manifest.json", manifest)
            success = bool(chunks and manifest["fetched_page_count"] > 0)
            row = {
                "position": position,
                "url": url,
                "success": success,
                "strategy": manifest["discovery_strategy"],
                "pages": manifest["fetched_page_count"],
                "chunks": len(chunks),
                "headless_attempts": manifest["headless_attempt_count"],
                "headless_successes": manifest["headless_success_count"],
                "error": "" if success else "Không tạo được chunk công khai",
            }
        except (CrawlError, ValueError) as exc:
            row = {
                "position": position,
                "url": url,
                "success": False,
                "strategy": "failed_before_manifest",
                "pages": 0,
                "chunks": 0,
                "headless_attempts": 0,
                "headless_successes": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        rows.append(row)
        print(f"[{position}/{sample_size}] {'PASS' if row['success'] else 'FAIL'} {url} - {row['strategy']}")

    passed = sum(bool(row["success"]) for row in rows)
    report = {
        "schema_version": "h3-14.benchmark.v1",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "sample_seed": SAMPLE_SEED,
        "candidate_pool": list(CANDIDATE_POOL),
        "selected_before_run": selected,
        "success_count": passed,
        "sample_count": sample_size,
        "success_rate": round(passed / sample_size, 4),
        "definition_of_done": "Crawl thành công ít nhất 8/10 website",
        "definition_met": sample_size == 10 and passed >= 8,
        "success_rule": "fetched_page_count > 0 và chunk_count > 0",
        "rows": rows,
    }
    write_json(output_dir / "benchmark_results.json", report)
    print(f"H3-14 result: {passed}/{sample_size} ({report['success_rate']:.0%})")
    return 0 if report["definition_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
