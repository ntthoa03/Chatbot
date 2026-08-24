"""Hộp bàn giao người thật ở tầng ứng dụng, độc lập với ``ai_core``."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_HANDOFF_PATH = PROJECT_ROOT / "outputs" / "handoff_requests.jsonl"
_WRITE_LOCK = threading.Lock()


def handoff_path() -> Path:
    configured = os.getenv("AI_UI_HANDOFF_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_HANDOFF_PATH


def canonical_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) != 10 or digits[:2] not in {"03", "05", "07", "08", "09"}:
        raise ValueError("Số điện thoại Việt Nam phải có 10 số và bắt đầu bằng 03, 05, 07, 08 hoặc 09.")
    return digits


def confirmed_handoff_contact(response: dict[str, Any]) -> tuple[str | None, str] | None:
    """UI chỉ tạo ticket khi core vừa yêu cầu chuyển và trả lead đã xác nhận."""

    lead = response.get("lead_captured")
    if not response.get("need_human") or not isinstance(lead, dict) or not lead.get("phone"):
        return None
    return (str(lead.get("name") or "") or None, canonical_phone(str(lead["phone"])))


def _redact_secrets(text: str) -> str:
    """Giữ thông tin liên hệ nhưng loại bí mật không cần cho Sale xử lý."""

    safe = re.sub(
        r"(?i)\b(otp|cvv|mat khau|mật khẩu|password)\s*[:=]?\s*\S+",
        r"\1 [REDACTED]",
        text,
    )
    return re.sub(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", "[REDACTED_CARD]", safe)


def _append_event(event: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with destination.open("a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()


def load_handoffs(path: Path | None = None) -> list[dict[str, Any]]:
    destination = path or handoff_path()
    if not destination.exists():
        return []
    records: dict[str, dict[str, Any]] = {}
    with destination.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            handoff_id = str(event.get("handoff_id", ""))
            if not handoff_id:
                continue
            if event.get("event") == "created":
                records[handoff_id] = dict(event)
            elif handoff_id in records:
                records[handoff_id].update(
                    {
                        key: value
                        for key, value in event.items()
                        if key not in {"event", "logged_at"}
                    }
                )
                records[handoff_id]["updated_at"] = event.get("logged_at")
    return sorted(records.values(), key=lambda item: str(item.get("created_at", "")))


def _notify_webhook(record: dict[str, Any]) -> tuple[str, str | None]:
    url = os.getenv("AI_UI_HANDOFF_WEBHOOK_URL", "").strip()
    if not url:
        return "local_queue", None
    payload = json.dumps(record, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=5) as response:
            if 200 <= int(response.status) < 300:
                return "webhook_sent", None
            return "webhook_failed", f"HTTP {response.status}"
    except (OSError, URLError, ValueError) as exc:
        return "webhook_failed", type(exc).__name__


def create_handoff(
    *,
    tenant_id: str,
    config_version: int,
    conversation_id: str,
    trace_id: str,
    tester_name: str,
    reason: str,
    question: str,
    reply: str,
    messages: Sequence[dict[str, Any]],
    customer_name: str | None = None,
    customer_phone: str | None = None,
    path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    """Tạo ticket một lần theo trace và luôn lưu trước khi thử gửi webhook."""

    destination = path or handoff_path()
    existing = load_handoffs(destination)
    duplicate = next(
        (item for item in existing if trace_id and item.get("trace_id") == trace_id),
        None,
    )
    if duplicate is not None:
        return duplicate, False

    phone = canonical_phone(customer_phone) if customer_phone else None
    now = datetime.now(UTC).isoformat()
    handoff_id = f"HO-{uuid4().hex[:10].upper()}"
    record = {
        "schema_version": "ui.handoff.v1",
        "event": "created",
        "handoff_id": handoff_id,
        "created_at": now,
        "updated_at": now,
        "status": "waiting" if phone else "awaiting_contact",
        "delivery_status": "pending",
        "tenant_id": tenant_id,
        "config_version": config_version,
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        "tester_name": tester_name.strip(),
        "reason": reason,
        "customer_name": (customer_name or "").strip() or None,
        "customer_phone": phone,
        "question": _redact_secrets(question),
        "reply": _redact_secrets(reply),
        "messages": [
            {
                "role": str(item.get("role", "")),
                "content": _redact_secrets(str(item.get("content", ""))),
            }
            for item in messages[-30:]
            if item.get("role") in {"user", "assistant"}
        ],
    }
    _append_event(record, destination)
    delivery_status, delivery_error = _notify_webhook(record)
    delivery_event = {
        "schema_version": "ui.handoff.v1",
        "event": "delivery",
        "handoff_id": handoff_id,
        "logged_at": datetime.now(UTC).isoformat(),
        "delivery_status": delivery_status,
        "delivery_error": delivery_error,
    }
    _append_event(delivery_event, destination)
    return next(item for item in load_handoffs(destination) if item["handoff_id"] == handoff_id), True


def attach_contact(
    handoff_id: str,
    *,
    customer_name: str,
    customer_phone: str,
    path: Path | None = None,
) -> dict[str, Any]:
    destination = path or handoff_path()
    records = load_handoffs(destination)
    current = next((item for item in records if item.get("handoff_id") == handoff_id), None)
    if current is None:
        raise ValueError("Không tìm thấy yêu cầu chuyển chuyên viên.")
    name = customer_name.strip()
    if not name:
        raise ValueError("Vui lòng nhập tên để chuyên viên xưng hô khi liên hệ.")
    phone = canonical_phone(customer_phone)
    event = {
        "schema_version": "ui.handoff.v1",
        "event": "contact_added",
        "handoff_id": handoff_id,
        "logged_at": datetime.now(UTC).isoformat(),
        "customer_name": name,
        "customer_phone": phone,
        "status": "waiting",
    }
    _append_event(event, destination)
    updated = next(item for item in load_handoffs(destination) if item["handoff_id"] == handoff_id)
    delivery_status, delivery_error = _notify_webhook(updated)
    _append_event(
        {
            "schema_version": "ui.handoff.v1",
            "event": "delivery",
            "handoff_id": handoff_id,
            "logged_at": datetime.now(UTC).isoformat(),
            "delivery_status": delivery_status,
            "delivery_error": delivery_error,
        },
        destination,
    )
    return next(item for item in load_handoffs(destination) if item["handoff_id"] == handoff_id)


def handoff_csv(records: Sequence[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    fields = (
        "handoff_id", "created_at", "status", "delivery_status", "tenant_id",
        "conversation_id", "trace_id", "reason", "customer_name", "customer_phone",
        "question", "reply",
    )
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    return "\ufeff" + output.getvalue()


def _main() -> None:
    parser = argparse.ArgumentParser(description="Đọc hàng đợi chuyển chuyên viên của UI.")
    parser.add_argument("--tail", type=int, default=20)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    records = load_handoffs()
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(handoff_csv(records), encoding="utf-8")
        print(f"Đã xuất {len(records)} yêu cầu: {args.export}")
        return
    waiting = sum(item.get("status") in {"waiting", "awaiting_contact"} for item in records)
    print(f"Tổng yêu cầu: {len(records)} | Đang chờ: {waiting} | Nguồn: {handoff_path()}")
    for item in records[-max(args.tail, 0):]:
        print(
            f"{item.get('created_at')} | {item.get('handoff_id')} | {item.get('status')} | "
            f"{item.get('customer_name') or '-'} | {item.get('customer_phone') or '-'} | "
            f"{item.get('question')}"
        )


if __name__ == "__main__":
    _main()
