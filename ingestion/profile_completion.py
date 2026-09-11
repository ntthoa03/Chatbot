"""H4-13 completion score weighted by real H4-02 customer-question frequency."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from ingestion.profile_review import FIELD_LABELS


class ProfileCompletionError(ValueError):
    pass


FIELD_KEYWORDS = {
    "public_pricing": (
        "gia", "chi phi", "hoc phi", "phi duy tri", "bao nhieu", "bang gia", "ban si",
    ),
    "operating_regions": (
        "khu vuc", "o dau", "dia chi", "tinh thanh", "phuc vu tai", "giao den",
    ),
    "target_customers": (
        "phu hop voi ai", "do tuoi", "doi tuong", "khach hang nao",
    ),
    "main_services": (
        "dich vu nao", "cung cap gi", "khoa hoc nao", "chuyen khoa nao",
    ),
    "industry": ("linh vuc", "nganh nghe"),
    "brand_tone": ("giong dieu", "phong cach thuong hieu"),
}


def _normalized(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text).casefold().replace("đ", "d"))
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def topic_profile_field(topic: dict[str, Any]) -> str | None:
    haystack = _normalized(
        " ".join([
            str(topic.get("name", "")),
            str(topic.get("representative_question", topic.get("question", ""))),
            *[str(item) for item in topic.get("examples", topic.get("evidence_examples", []))],
        ])
    )
    for field_name, keywords in FIELD_KEYWORDS.items():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", haystack) for keyword in keywords):
            return field_name
    return None


def load_review_records(path: Path, tenant_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProfileCompletionError(f"Review JSONL lỗi ở dòng {number}.") from exc
        if item.get("tenant_id") != tenant_id:
            raise ProfileCompletionError("Review chứa dữ liệu tenant khác.")
        records.append(item)
    return records


def load_gap_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileCompletionError(f"Không tìm thấy báo cáo H4-02: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileCompletionError("Báo cáo H4-02 không phải JSON hợp lệ.") from exc
    if not isinstance(report.get("tenants"), dict):
        raise ProfileCompletionError("Báo cáo H4-02 thiếu tenants.")
    return report


def build_completion(
    report: dict[str, Any],
    tenant_id: str,
    review_records: list[dict[str, Any]],
) -> dict[str, Any]:
    tenant = report.get("tenants", {}).get(tenant_id)
    if not isinstance(tenant, dict):
        raise ProfileCompletionError(f"H4-02 không có tenant {tenant_id}.")
    raw_topics = tenant.get("clusters", tenant.get("top_missing_topics", []))
    if not isinstance(raw_topics, list):
        raise ProfileCompletionError("Danh sách cluster H4-02 không hợp lệ.")
    topics = []
    for raw in raw_topics:
        if not isinstance(raw, dict) or raw.get("tenant_id") != tenant_id:
            raise ProfileCompletionError("Cluster H4-02 sai hoặc lẫn tenant.")
        frequency = raw.get("frequency")
        question = str(raw.get("representative_question") or raw.get("question") or "").strip()
        if isinstance(frequency, bool) or not isinstance(frequency, int) or frequency <= 0 or not question:
            continue
        examples = [str(item).strip() for item in raw.get("examples", []) if str(item).strip()]
        if question not in examples:
            raise ProfileCompletionError(
                f"Cluster {raw.get('cluster_id', '?')} không chứng minh được câu hỏi từng xuất hiện."
            )
        topics.append({**raw, "frequency": frequency, "question": question})
    if not topics:
        return {
            "status": "no_gap_data",
            "tenant_id": tenant_id,
            "completion_percent": None,
            "message": "Chưa có chủ đề khách hỏi trong H4-02 nên chưa thể tính % mà không bịa.",
            "next_actions": [],
        }

    latest: dict[str, str] = {}
    for record in review_records:
        field_name = str(record.get("field_name", ""))
        action = str(record.get("action", ""))
        if field_name in FIELD_LABELS:
            latest[field_name] = action
    total_frequency = sum(item["frequency"] for item in topics)
    resolved_frequency = 0
    unresolved = []
    topic_rows = []
    for topic in topics:
        field_name = topic_profile_field(topic)
        resolved = bool(field_name and latest.get(field_name) in {"confirmed", "edited"})
        if resolved:
            resolved_frequency += topic["frequency"]
        else:
            unresolved.append((topic, field_name))
        topic_rows.append({
            "cluster_id": topic.get("cluster_id"), "question": topic["question"],
            "frequency": topic["frequency"], "profile_field": field_name, "resolved": resolved,
        })
    unresolved.sort(key=lambda pair: (-pair[0]["frequency"], str(pair[0].get("cluster_id", ""))))
    next_actions = []
    for topic, field_name in unresolved[:3]:
        benefit = topic["frequency"] / total_frequency
        next_actions.append({
            "cluster_id": topic.get("cluster_id"),
            "question": topic["question"],
            "frequency": topic["frequency"],
            "profile_field": field_name,
            "action": (
                f"Xác nhận mục {FIELD_LABELS[field_name]}"
                if field_name else f"Trả lời câu: {topic['question']}"
            ),
            "estimated_minutes": 1 if field_name else 2,
            "benefit_rate": round(benefit, 4),
            "benefit_text": (
                f"Giúp bot có dữ liệu cho khoảng {benefit:.0%} lượt hỏi thiếu "
                f"({topic['frequency']}/{total_frequency} lượt ghi nhận trong H4-02)."
            ),
        })
    return {
        "status": "ready",
        "tenant_id": tenant_id,
        "completion_percent": round(resolved_frequency / total_frequency * 100, 1),
        "resolved_frequency": resolved_frequency,
        "total_frequency": total_frequency,
        "calculation": "resolved H4-02 question frequency / total H4-02 question frequency",
        "next_actions": next_actions,
        "topics": topic_rows,
    }


__all__ = [
    "ProfileCompletionError", "build_completion", "load_gap_report",
    "load_review_records", "topic_profile_field",
]
