"""Tạo dữ liệu synthetic để chứng minh round-trip SQLite H3-04.

Script không gọi LLM và không giả vờ đây là dữ liệu khách thật. H3-05 sẽ thay
fixture bằng request/response thật nhưng giữ nguyên các method Storage.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage import SQLiteStore


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="H3-04: demo round-trip SQLite synthetic")
    parser.add_argument("--database", default="outputs/h3_04/demo.sqlite3")
    parser.add_argument("--report", default="outputs/h3_04/acceptance.json")
    args = parser.parse_args()

    tenant_id = "h3_04_demo"
    conversation_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    database_path = Path(args.database)

    # TODO(Hieu/Postgres): chỉ thay SQLiteStore bằng PostgresStorage được inject.
    with SQLiteStore(database_path) as storage:
        storage.upsert_tenant(tenant_id, "Tenant demo H3-04", config_version=1)
        storage.create_conversation(tenant_id, conversation_id)
        storage.save_message(
            tenant_id,
            conversation_id,
            "user",
            "Tôi cần tư vấn dịch vụ phù hợp.",
        )
        storage.save_message(
            tenant_id,
            conversation_id,
            "assistant",
            "Dạ, anh/chị cho em biết thêm nhu cầu và ngân sách dự kiến nhé.",
            trace_id=trace_id,
        )
        storage.save_lead(
            tenant_id,
            conversation_id,
            name="Khách Demo",
            phone="0900000000",
        )
        storage.save_usage_event(
            tenant_id,
            conversation_id,
            trace_id=trace_id,
            usage={
                "model": "synthetic-model",
                "tokens_in": 120,
                "tokens_out": 35,
                "cached_tokens_in": 0,
                "cache_write_tokens_in": 0,
                "cost_usd": 0.0001,
                "latency_ms": 450,
            },
        )
        conversation = storage.get_conversation(tenant_id, conversation_id)
        leads = storage.list_leads(tenant_id, conversation_id)
        usage_events = storage.list_usage_events(tenant_id, conversation_id)
        wrong_tenant_result = storage.get_conversation("h3_04_other", conversation_id)

    report = {
        "schema_version": "h3-04.sqlite-acceptance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "synthetic": True,
        "database": str(database_path).replace("\\", "/"),
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "checks": {
            "conversation_round_trip": conversation is not None,
            "message_count": len((conversation or {}).get("messages", [])),
            "lead_count": len(leads),
            "usage_event_count": len(usage_events),
            "wrong_tenant_returns_none": wrong_tenant_result is None,
        },
    }
    report["passed"] = report["checks"] == {
        "conversation_round_trip": True,
        "message_count": 2,
        "lead_count": 1,
        "usage_event_count": 1,
        "wrong_tenant_returns_none": True,
    }
    write_json_atomic(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
