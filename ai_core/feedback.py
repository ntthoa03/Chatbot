"""Durable feedback inbox for H2-12 internal Sale testing."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ai_core.trace import PROJECT_ROOT, redact_sensitive_data


FEEDBACK_SCHEMA_VERSION = "h2-12.v3"
DEFAULT_FEEDBACK_PATH = PROJECT_ROOT / "outputs" / "sale_feedback.jsonl"
DEFAULT_SALE_TURNS_PATH = PROJECT_ROOT / "outputs" / "sale_ui_turns.jsonl"
_WRITE_LOCK = threading.Lock()
_TURN_WRITE_LOCK = threading.Lock()


def feedback_path() -> Path:
    configured = os.getenv("AI_CORE_FEEDBACK_PATH", "").strip()
    if not configured:
        return DEFAULT_FEEDBACK_PATH
    selected = Path(configured)
    return selected if selected.is_absolute() else PROJECT_ROOT / selected


def sale_turns_path() -> Path:
    configured = os.getenv("AI_CORE_SALE_TURNS_PATH", "").strip()
    if not configured:
        return DEFAULT_SALE_TURNS_PATH
    selected = Path(configured)
    return selected if selected.is_absolute() else PROJECT_ROOT / selected


def load_feedback(path: Path | None = None) -> list[dict[str, Any]]:
    destination = path or feedback_path()
    try:
        lines = destination.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def save_bad_feedback(
    *,
    question: str,
    reply: str,
    response: dict[str, Any],
    conversation_id: str,
    tenant_id: str,
    config_version: int,
    tester_name: str = "",
    suggested_reply: str = "",
    path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    """Append one redacted negative rating and deduplicate it by trace ID."""

    destination = path or feedback_path()
    trace_id = str(response.get("trace_id", "")).strip()
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    record = redact_sensitive_data({
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "feedback_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "rating": "bad",
        "tenant_id": tenant_id,
        "config_version": config_version,
        "conversation_id": conversation_id,
        "tester_name": tester_name.strip(),
        "trace_id": trace_id,
        "question": question,
        "reply": reply,
        "suggested_reply": suggested_reply.strip(),
        "model": str(usage.get("model", "")),
        "tokens_in": int(usage.get("tokens_in", 0) or 0),
        "tokens_out": int(usage.get("tokens_out", 0) or 0),
        "cost_usd": float(usage.get("cost_usd", 0) or 0),
        "latency_ms": int(usage.get("latency_ms", 0) or 0),
        "need_human": bool(response.get("need_human", False)),
        "guardrail": response.get("guardrail", {}),
    })
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        existing = load_feedback(destination)
        if trace_id:
            duplicate = next(
                (
                    item for item in existing
                    if item.get("trace_id") == trace_id and item.get("rating") == "bad"
                ),
                None,
            )
            if duplicate is not None:
                return duplicate, False
        with destination.open("a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
    return record, True


def log_sale_turn(
    *,
    tester_name: str,
    question: str,
    reply: str,
    response: dict[str, Any],
    conversation_id: str,
    tenant_id: str,
    config_version: int,
    path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    """Log every completed UI turn so H2-12 adoption can be measured."""

    destination = path or sale_turns_path()
    trace_id = str(response.get("trace_id", "")).strip()
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    record = redact_sensitive_data({
        "schema_version": "h2-12.turn.v2",
        "logged_at": datetime.now(UTC).isoformat(),
        "tester_name": tester_name.strip(),
        "tenant_id": tenant_id,
        "config_version": config_version,
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        "question": question,
        "reply": reply,
        "model": str(usage.get("model", "")),
        "cost_usd": float(usage.get("cost_usd", 0) or 0),
        "latency_ms": int(usage.get("latency_ms", 0) or 0),
    })
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _TURN_WRITE_LOCK:
        existing = load_feedback(destination)
        if trace_id and any(item.get("trace_id") == trace_id for item in existing):
            duplicate = next(item for item in existing if item.get("trace_id") == trace_id)
            return duplicate, False
        with destination.open("a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
    return record, True


def feedback_csv(records: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "feedback_id", "created_at", "rating", "tenant_id", "conversation_id",
        "tester_name", "trace_id", "question", "reply", "suggested_reply", "model",
        "cost_usd", "latency_ms", "need_human",
    ])
    for item in records:
        writer.writerow([
            item.get("feedback_id", ""),
            item.get("created_at", ""),
            item.get("rating", ""),
            item.get("tenant_id", ""),
            item.get("conversation_id", ""),
            item.get("tester_name", ""),
            item.get("trace_id", ""),
            item.get("question", ""),
            item.get("reply", ""),
            item.get("suggested_reply", ""),
            item.get("model", ""),
            item.get("cost_usd", ""),
            item.get("latency_ms", 0),
            item.get("need_human", False),
        ])
    return "\ufeff" + output.getvalue()


def sale_usage_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize H2-12 participation without counting blank identifiers."""

    testers = sorted({
        str(item.get("tester_name", "")).strip()
        for item in records
        if str(item.get("tester_name", "")).strip()
    })
    conversations = {
        str(item.get("conversation_id", "")).strip()
        for item in records
        if str(item.get("conversation_id", "")).strip()
    }
    by_tester: dict[str, dict[str, int]] = {}
    for tester in testers:
        tester_records = [
            item for item in records
            if str(item.get("tester_name", "")).strip() == tester
        ]
        tester_conversations = {
            str(item.get("conversation_id", "")).strip()
            for item in tester_records
            if str(item.get("conversation_id", "")).strip()
        }
        by_tester[tester] = {
            "conversations": len(tester_conversations),
            "turns": len(tester_records),
        }
    return {
        "turns": len(records),
        "conversations": len(conversations),
        "testers": testers,
        "tester_count": len(testers),
        "by_tester": by_tester,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Đọc hộp thư feedback H2-12.")
    parser.add_argument("--tail", type=int, default=10)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--stats", action="store_true", help="Thống kê lượt dùng UI của Sale.")
    args = parser.parse_args()
    if args.stats:
        stats = sale_usage_stats(load_feedback(sale_turns_path()))
        print(
            f"Sale: {stats['tester_count']} | Hội thoại: {stats['conversations']} | "
            f"Lượt hỏi: {stats['turns']}"
        )
        print("Người test: " + (", ".join(stats["testers"]) or "chưa có"))
        for tester, progress in stats["by_tester"].items():
            print(
                f"- {tester}: {progress['conversations']}/10 hội thoại | "
                f"{progress['turns']} lượt hỏi"
            )
        print(f"Nguồn: {sale_turns_path()}")
        return 0
    records = load_feedback()
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(feedback_csv(records), encoding="utf-8")
        print(f"Đã xuất {len(records)} feedback: {args.export}")
        return 0
    print(f"Tổng feedback: {len(records)} | Nguồn: {feedback_path()}")
    for item in records[-max(0, args.tail):]:
        print(
            f"{item.get('created_at')} | {item.get('feedback_id')} | "
            f"trace={item.get('trace_id')} | {item.get('question')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
