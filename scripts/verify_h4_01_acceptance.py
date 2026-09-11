"""Nghiệm thu H4-01 bằng luồng chat thật và báo cáo đối chiếu từng câu.

Script gọi endpoint ``POST /chat`` với AI services thật, đọc chẩn đoán retrieval
từ trace độc lập, rồi đối chiếu với bản ghi ``knowledge_gaps`` qua interface
``Storage``. Không dùng mock, không phụ thuộc SQLite và không trộn gap lịch sử.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import yaml
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_core.interfaces import build_services
from ai_core.gap_logger import reply_indicates_missing_knowledge
from ai_core.trace import find_trace
from api.main import PublicKeyResolver, create_app
from storage import Storage, build_storage
from storage.factory import STORAGE_BACKEND_ENV


DEFAULT_CASES = PROJECT_ROOT / "eval" / "cases.yaml"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "h4_01"


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _load_cases(path: Path, limit: int) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("File eval phải là một YAML list.")
    cases = [item for item in raw if isinstance(item, dict) and item.get("question")]
    if len(cases) < limit:
        raise ValueError(f"Chỉ có {len(cases)} case, không đủ {limit} case yêu cầu.")
    return cases[:limit]


def _run_one(
    case: dict[str, Any],
    *,
    client: TestClient,
    storage: Storage,
    tenant_id: str,
    config_version: int,
    run_id: str,
) -> dict[str, Any]:
    case_id = str(case.get("id") or "unknown")
    question = str(case["question"]).strip()
    conversation_id = str(uuid5(NAMESPACE_URL, f"h4-01:{run_id}:{case_id}"))
    try:
        http_response = client.post(
            "/chat",
            headers={"X-Public-Key": "h4-01-acceptance-key"},
            json={
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "message": question,
                "history": [],
                "config_version": config_version,
            },
        )
        if http_response.status_code != 200:
            raise RuntimeError(
                f"POST /chat returned HTTP {http_response.status_code}: "
                f"{http_response.text[:300]}"
            )
        response = http_response.json()
        trace_id = str(response.get("trace_id") or "")
        trace = find_trace(trace_id) or {}
        retrieval = trace.get("retrieval") if isinstance(trace.get("retrieval"), dict) else {}
        attempted = bool(retrieval.get("attempted"))
        top_score = _float(retrieval.get("top_score"))
        threshold = _float(retrieval.get("threshold"))
        retrieval_error = bool(retrieval.get("error"))
        bot_stuck = bool(
            trace.get("helpful_fallback_used")
            or reply_indicates_missing_knowledge(str(response.get("reply") or ""))
        )
        below_threshold = bool(
            attempted
            and top_score is not None
            and threshold is not None
            and top_score < threshold
        )
        expected_gap = bool(
            attempted
            and (retrieval_error or top_score is None or below_threshold or bot_stuck)
        )
        # Bằng chứng nghiệm thu lấy từ bảng SQLite bắt buộc, không lấy từ
        # ContextVar/capture trong bộ nhớ.
        matching = storage.list_knowledge_gaps(tenant_id, conversation_id)
        gap_logged = len(matching) == 1
        exact_match = gap_logged == expected_gap and len(matching) <= 1
        return {
            "id": case_id,
            "question": question,
            "bot_reply": str(response.get("reply") or ""),
            "bot_stuck": bot_stuck,
            "retrieval_attempted": attempted,
            "retrieval_top_score": top_score,
            "retrieval_threshold": threshold,
            "retrieval_error": retrieval_error,
            "expected_gap": expected_gap,
            "gap_logged": gap_logged,
            "gap_count": len(matching),
            "gap_reason": matching[0].get("reason") if matching else None,
            "matched": exact_match,
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "error": None,
        }
    except Exception as exc:  # Báo từng case; không làm mất kết quả 49 case còn lại.
        return {
            "id": case_id,
            "question": question,
            "bot_reply": "",
            "bot_stuck": False,
            "retrieval_attempted": False,
            "retrieval_top_score": None,
            "retrieval_threshold": None,
            "retrieval_error": False,
            "expected_gap": False,
            "gap_logged": False,
            "gap_count": 0,
            "gap_reason": None,
            "matched": False,
            "trace_id": "",
            "conversation_id": conversation_id,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _save_reports(
    rows: list[dict[str, Any]],
    output_dir: Path,
    run_id: str,
    *,
    min_gap_cases: int,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{run_id}.json"
    csv_path = output_dir / f"{run_id}.csv"
    md_path = output_dir / f"{run_id}.md"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    gaps_expected = sum(bool(row["expected_gap"]) for row in rows)
    gaps_logged = sum(bool(row["gap_logged"]) for row in rows)
    missing = sum(bool(row["expected_gap"]) and not bool(row["gap_logged"]) for row in rows)
    false_positive = sum(not bool(row["expected_gap"]) and bool(row["gap_logged"]) for row in rows)
    errors = sum(bool(row["error"]) for row in rows)
    retrieval_errors = sum(bool(row["retrieval_error"]) for row in rows)
    gap_coverage_passed = gaps_expected >= min_gap_cases
    logging_passed = (
        missing == 0
        and false_positive == 0
        and errors == 0
        and gap_coverage_passed
        and all(row["matched"] for row in rows)
    )
    live_api_passed = errors == 0 and retrieval_errors == 0
    lines = [
        "# Nghiệm thu H4-01",
        "",
        f"- Tổng câu: {len(rows)}",
        f"- Bot bí: {sum(bool(row['bot_stuck']) for row in rows)}",
        f"- Gap cần ghi: {gaps_expected}",
        f"- Gap thực tế: {gaps_logged}",
        f"- Thiếu: {missing}",
        f"- Ghi nhầm: {false_positive}",
        f"- Lỗi chạy: {errors}",
        f"- Lỗi retrieval/provider: {retrieval_errors}",
        f"- Coverage gap: {gaps_expected}/{min_gap_cases} "
        f"({'PASS' if gap_coverage_passed else 'FAIL'})",
        f"- KẾT QUẢ LOGGER H4-01: {'PASS' if logging_passed else 'FAIL'}",
        f"- KẾT QUẢ API THẬT: {'PASS' if live_api_passed else 'FAIL'}",
        "",
        "| ID | Câu hỏi | Câu trả lời bot | Điểm/ngưỡng | Bot bí | Gap | Lý do | Đối chiếu |",
        "|---|---|---|---:|:---:|:---:|---|:---:|",
    ]
    for row in rows:
        reply = str(row["bot_reply"]).replace("|", "\\|").replace("\n", " ")
        question = str(row["question"]).replace("|", "\\|")
        score = f"{row['retrieval_top_score']}/{row['retrieval_threshold']}"
        lines.append(
            f"| {row['id']} | {question} | {reply} | {score} | "
            f"{'Có' if row['bot_stuck'] else 'Không'} | "
            f"{'Có' if row['gap_logged'] else 'Không'} | {row['gap_reason'] or ''} | "
            f"{'Đạt' if row['matched'] else 'Lỗi'} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, csv_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chạy nghiệm thu H4-01 qua API thật.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--tenant-id", default="mima_internal")
    parser.add_argument("--config-version", type=int, default=1)
    parser.add_argument(
        "--min-gap-cases",
        type=int,
        default=1,
        help="Số case gap tối thiểu để lượt chạy có giá trị nghiệm thu; mặc định 1.",
    )
    parser.add_argument(
        "--storage-backend",
        choices=("sqlite", "postgres"),
        default=None,
        help=f"Mặc định đọc {STORAGE_BACKEND_ENV}; nếu chưa đặt thì dùng sqlite.",
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=None,
        help="Tùy chọn đường dẫn SQLite; không áp dụng khi backend là postgres.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if args.limit <= 0 or args.min_gap_cases < 0 or args.min_gap_cases > args.limit:
        print("--limit phải > 0 và --min-gap-cases phải nằm trong 0..limit.", file=sys.stderr)
        return 2
    cases = _load_cases(args.cases, args.limit)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    isolated_gap_path = args.output_dir / f"{run_id}.knowledge_gaps.jsonl"
    os.environ["AI_CORE_KNOWLEDGE_GAP_PATH"] = str(isolated_gap_path)
    selected_backend = (
        args.storage_backend or os.getenv(STORAGE_BACKEND_ENV, "sqlite")
    ).strip().casefold()
    if selected_backend != "sqlite" and args.sqlite_path is not None:
        print("--sqlite-path chỉ dùng với --storage-backend sqlite.", file=sys.stderr)
        return 2
    sqlite_path = (
        args.sqlite_path or args.output_dir / f"{run_id}.sqlite3"
        if selected_backend == "sqlite"
        else None
    )
    storage = build_storage(backend=selected_backend, sqlite_path=sqlite_path)
    storage_target = str(sqlite_path) if sqlite_path is not None else selected_backend
    services = build_services(backend="real")
    api = create_app(
        services=services,
        storage=storage,
        public_key_resolver=PublicKeyResolver(
            {"h4-01-acceptance-key": args.tenant_id}
        ),
    )
    rows: list[dict[str, Any]] = []
    try:
        with TestClient(api) as client:
            for index, case in enumerate(cases, start=1):
                row = _run_one(
                    case,
                    client=client,
                    storage=storage,
                    tenant_id=args.tenant_id,
                    config_version=args.config_version,
                    run_id=run_id,
                )
                rows.append(row)
                reply = row["bot_reply"].replace("\n", " ")[:100]
                print(
                    f"[{index:02d}/{len(cases)}] {row['id']} | "
                    f"bot_bí={row['bot_stuck']} | gap_db={row['gap_logged']} "
                    f"({row['gap_reason'] or '-'}) | {reply}",
                    flush=True,
                )
    finally:
        storage.close()
    json_path, csv_path, md_path = _save_reports(
        rows,
        args.output_dir,
        run_id,
        min_gap_cases=args.min_gap_cases,
    )
    logging_failures = [row for row in rows if not row["matched"] or row["error"]]
    provider_failures = [row for row in rows if row["retrieval_error"] or row["error"]]
    gap_coverage_failed = sum(bool(row["expected_gap"]) for row in rows) < args.min_gap_cases
    print(f"\nBáo cáo JSON: {json_path}")
    print(f"Báo cáo CSV:  {csv_path}")
    print(f"Báo cáo đọc: {md_path}")
    print(f"Storage chứa knowledge_gaps: {storage_target}")
    print(
        f"LOGGER H4-01: "
        f"{'PASS' if not logging_failures and not gap_coverage_failed else 'FAIL'} "
        f"({len(rows) - len(logging_failures)}/{len(rows)})"
    )
    if gap_coverage_failed:
        print(
            "COVERAGE GAP: FAIL "
            f"({sum(bool(row['expected_gap']) for row in rows)}/{args.min_gap_cases})"
        )
    print(
        f"API THẬT: {'PASS' if not provider_failures else 'FAIL'} "
        f"({len(rows) - len(provider_failures)}/{len(rows)})"
    )
    return 0 if not logging_failures and not provider_failures and not gap_coverage_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
