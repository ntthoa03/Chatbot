"""H4-10: generate weekly tenant questions strictly from H4-02 gap clusters."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_INPUT = ROOT / "outputs" / "h4_02" / "knowledge_gap_clusters.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "h4_10"


class WeeklyQuestionError(ValueError):
    pass


def _verified_topic(raw: Any, tenant_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("tenant_id") != tenant_id:
        raise WeeklyQuestionError(f"Cụm câu hỏi không thuộc tenant {tenant_id}.")
    question = str(raw.get("representative_question") or "").strip()
    examples = [str(item).strip() for item in raw.get("examples", []) if str(item).strip()]
    frequency = raw.get("frequency")
    if not question or question not in examples:
        raise WeeklyQuestionError(
            f"Cụm {raw.get('cluster_id', '?')} không chứng minh được câu hỏi từng xuất hiện trong H4-02."
        )
    if isinstance(frequency, bool) or not isinstance(frequency, int) or frequency <= 0:
        raise WeeklyQuestionError(f"Cụm {raw.get('cluster_id', '?')} có tần suất không hợp lệ.")
    return {
        "cluster_id": str(raw.get("cluster_id") or ""),
        "topic": str(raw.get("name") or question).strip(),
        "question": question,
        "frequency": frequency,
        "evidence_examples": examples[:5],
        "first_seen": raw.get("first_seen"),
        "last_seen": raw.get("last_seen"),
    }


def generate_weekly_questions(
    report: dict[str, Any],
    *,
    tenant_ids: list[str] | None = None,
    minimum: int = 3,
    maximum: int = 5,
) -> dict[str, Any]:
    if not 3 <= minimum <= maximum <= 5:
        raise WeeklyQuestionError("Số chủ đề mỗi tenant phải nằm trong khoảng 3..5.")
    tenants = report.get("tenants")
    if not isinstance(tenants, dict):
        raise WeeklyQuestionError("Input không phải báo cáo cụm H4-02 hợp lệ.")
    selected_ids = tenant_ids or sorted(tenants)
    output_tenants: dict[str, Any] = {}
    for tenant_id in selected_ids:
        tenant = tenants.get(tenant_id)
        if not isinstance(tenant, dict):
            raise WeeklyQuestionError(f"Không có dữ liệu H4-02 cho tenant {tenant_id}.")
        raw_topics = tenant.get("clusters", tenant.get("top_missing_topics", []))
        if not isinstance(raw_topics, list):
            raise WeeklyQuestionError(f"Danh sách cụm H4-02 của {tenant_id} không hợp lệ.")
        verified = [_verified_topic(item, tenant_id) for item in raw_topics]
        verified.sort(key=lambda item: (-item["frequency"], item["cluster_id"]))
        chosen = verified[:maximum]
        if len(chosen) < minimum:
            raise WeeklyQuestionError(
                f"{tenant_id} chỉ có {len(chosen)} chủ đề thật; cần tối thiểu {minimum}. "
                "Dừng thay vì tự nghĩ thêm câu hỏi."
            )
        lines = [
            "Chào anh/chị, tuần này bot đang thiếu thông tin cho các câu khách đã hỏi nhiều nhất:",
            *[
                f"{index}. {item['question']} — khách đã hỏi {item['frequency']} lần"
                for index, item in enumerate(chosen, start=1)
            ],
            "Anh/chị trả lời ngắn từng câu giúp em; em sẽ cập nhật để bot dùng ngay ạ.",
        ]
        output_tenants[tenant_id] = {
            "question_count": len(chosen),
            "questions": chosen,
            "message": "\n".join(lines),
        }
    return {
        "schema_version": "h4-10.weekly-tenant-questions.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_schema": report.get("schema_version"),
        "source": report.get("source"),
        "source_data_label": report.get("source_data_label", "UNSPECIFIED"),
        "selection_rule": "top 3-5 positive-frequency H4-02 clusters only",
        "tenants": output_tenants,
    }


def render_messages(result: dict[str, Any]) -> str:
    lines = ["# H4-10 — Tin nhắn hỏi tenant hàng tuần", ""]
    label = result.get("source_data_label", "UNSPECIFIED")
    lines.extend([f"> Nhãn dữ liệu: **{label}**", ""])
    for tenant_id, tenant in result["tenants"].items():
        lines.extend([f"## {tenant_id}", "", "```text", tenant["message"], "```", ""])
    return "\n".join(lines)


def run(input_path: Path, output_dir: Path, tenant_ids: list[str] | None = None) -> dict[str, Any]:
    try:
        report = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeeklyQuestionError(f"Không đọc được input H4-02: {exc}") from exc
    result = generate_weekly_questions(report, tenant_ids=tenant_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "weekly_questions.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "message_templates.md").write_text(render_messages(result), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tenant-id", action="append", dest="tenant_ids")
    args = parser.parse_args()
    try:
        result = run(args.input, args.output_dir, args.tenant_ids)
    except WeeklyQuestionError as exc:
        parser.error(str(exc))
    for tenant_id, tenant in result["tenants"].items():
        print(f"{tenant_id}: {tenant['question_count']} câu hỏi thật từ H4-02")
    print(f"Đã sinh nội dung, chưa gửi: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
