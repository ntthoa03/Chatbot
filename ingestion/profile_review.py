"""Persistence and presentation helpers for the H4-09 profile review UI."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ai_core.config import validate_tenant_id
from ingestion.profile_generator import BusinessProfileDraft, PROFILE_FIELDS


FIELD_LABELS = {
    "industry": "Ngành nghề",
    "main_services": "Dịch vụ chính",
    "operating_regions": "Khu vực hoạt động",
    "target_customers": "Tệp khách mục tiêu",
    "brand_tone": "Giọng điệu thương hiệu",
    "public_pricing": "Bảng giá công khai",
}
ReviewAction = Literal["confirmed", "edited", "skipped", "deferred"]


class ProfileReviewError(ValueError):
    pass


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def load_profile(path: Path, tenant_id: str) -> BusinessProfileDraft:
    validate_tenant_id(tenant_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileReviewError(f"Không tìm thấy hồ sơ H4-08: {path}") from exc
    if not isinstance(raw, dict):
        raise ProfileReviewError("File hồ sơ H4-08 phải là JSON object của đúng một tenant.")
    if raw.get("tenant_id") != tenant_id:
        raise ProfileReviewError(
            f"Từ chối hồ sơ tenant {raw.get('tenant_id')!r} trong phiên tenant {tenant_id!r}."
        )
    return BusinessProfileDraft.model_validate(raw)


def suggested_text(field_name: str, value: Any) -> str:
    """Always return a non-empty tenant-facing proposal."""

    if value is None or value == []:
        return "Chưa có thông tin công khai — đề xuất tenant bổ sung hoặc chọn Hỏi lại sau."
    if isinstance(value, str):
        return value.strip() or "Chưa có thông tin công khai — đề xuất tenant bổ sung hoặc chọn Hỏi lại sau."
    if field_name == "public_pricing" and isinstance(value, list):
        rows = []
        for item in value:
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
            if isinstance(item, dict):
                row = f"{item.get('item', '')} | {item.get('price', '')}"
                if item.get("note"):
                    row += f" | {item['note']}"
                rows.append(row.strip(" |"))
        return "\n".join(rows) or "Chưa có bảng giá công khai — đề xuất tenant bổ sung hoặc chọn Hỏi lại sau."
    if isinstance(value, list):
        return "\n".join(f"• {item}" for item in value) or "Chưa có thông tin công khai."
    return str(value).strip() or "Chưa có thông tin công khai."


def parse_edited_value(field_name: str, text: str) -> Any:
    clean = text.strip()
    if not clean:
        raise ProfileReviewError("Nội dung sửa không được để trống.")
    if field_name in {"industry", "brand_tone"}:
        return clean
    lines = [re.sub(r"^[•\-*]\s*", "", line).strip() for line in clean.splitlines() if line.strip()]
    if field_name != "public_pricing":
        return list(dict.fromkeys(lines))
    prices: list[dict[str, str | None]] = []
    for line in lines:
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ProfileReviewError("Mỗi dòng giá cần dạng: Hạng mục | Giá | Ghi chú (không bắt buộc).")
        prices.append({"item": parts[0], "price": parts[1], "note": parts[2] if len(parts) > 2 else None})
    return prices


def estimate_review_seconds(profile: BusinessProfileDraft) -> int:
    """Conservative non-developer estimate: 35s filled, 50s missing field."""

    return sum(
        35 if getattr(profile, field).value is not None else 50 for field in PROFILE_FIELDS
    )


def save_review_decision(
    *, tenant_id: str, field_name: str, action: ReviewAction,
    proposed_value: Any, final_value: Any, citations: list[dict[str, Any]],
    elapsed_seconds: float, output_root: Path | None = None,
) -> dict[str, Any]:
    validate_tenant_id(tenant_id)
    if field_name not in PROFILE_FIELDS:
        raise ProfileReviewError(f"Trường hồ sơ không hợp lệ: {field_name}")
    if action not in {"confirmed", "edited", "skipped", "deferred"}:
        raise ProfileReviewError(f"Quyết định không hợp lệ: {action}")
    if action == "edited" and (final_value is None or final_value == "" or final_value == []):
        raise ProfileReviewError("Nội dung đã sửa không được rỗng.")
    root = output_root or Path(os.getenv("AI_CORE_PROFILE_REVIEW_ROOT", "outputs/h4_09/reviews"))
    destination = root / f"{tenant_id}.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "review_id": str(uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id, "field_name": field_name, "action": action,
        "proposed_value": _jsonable(proposed_value), "final_value": _jsonable(final_value),
        "citations": _jsonable(citations), "elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
    }
    with destination.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record
