"""Generate isolated synthetic gaps and verify H4-02 top-10 ranking for five tenants."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_core.gap_clustering import normalize_question  # noqa: E402
from scripts.cluster_knowledge_gaps import run  # noqa: E402
from storage import SQLiteStore  # noqa: E402


TENANT_TOPICS: dict[str, list[str]] = {
    "mima_internal": [
        "phí duy trì website hàng năm", "thời gian hoàn thành website", "hỗ trợ sau bàn giao",
        "chi phí viết nội dung", "quy trình chỉnh sửa giao diện", "tích hợp thanh toán trực tuyến",
        "tối ưu SEO ban đầu", "chính sách sao lưu dữ liệu", "đăng ký tên miền",
        "bảo hành lỗi kỹ thuật", "kết nối phần mềm bán hàng", "đào tạo quản trị website",
    ],
    "phongkham_hyhy": [
        "lịch khám Chủ nhật", "áp dụng bảo hiểm y tế", "chi phí khám ban đầu",
        "đặt lịch ngoài giờ", "chỗ đậu ô tô", "thời gian nhận kết quả xét nghiệm",
        "khám cho trẻ em", "bác sĩ chuyên khoa", "đổi lịch hẹn",
        "chuẩn bị trước khi khám", "thanh toán bằng thẻ", "tư vấn trực tuyến",
    ],
    "bat_dong_san_phuoc_thinh": [
        "giấy tờ pháp lý căn nhà", "phí môi giới", "lịch xem nhà ngoài giờ",
        "hỗ trợ vay ngân hàng", "quy hoạch khu vực", "thời gian sang tên",
        "phí công chứng", "đặt cọc giữ chỗ", "kiểm tra sổ đỏ",
        "thương lượng giá bán", "bàn giao nội thất", "phí quản lý khu dân cư",
    ],
    "giao_duc_haiyan": [
        "lịch học buổi tối", "học thử trước đăng ký", "đóng học phí nhiều đợt",
        "giáo trình được sử dụng", "lớp học cuối tuần", "sĩ số mỗi lớp",
        "chính sách học bù", "thi xếp lớp đầu vào", "chứng chỉ cuối khóa",
        "học trực tuyến", "bảo lưu khóa học", "giảng viên bản ngữ",
    ],
    "thuc_pham_thien_minh": [
        "điều kiện giao hàng miễn phí", "hạn sử dụng sau mở gói", "bảng giá bán sỉ",
        "chứng nhận an toàn thực phẩm", "nhiệt độ bảo quản", "đổi trả hàng lỗi",
        "đơn hàng tối thiểu", "thời gian giao tỉnh", "nguồn gốc nguyên liệu",
        "chiết khấu đại lý", "quy cách đóng gói", "thanh toán công nợ",
    ],
}

TENANT_CODES = {
    "mima_internal": "MI",
    "phongkham_hyhy": "PK",
    "bat_dong_san_phuoc_thinh": "BD",
    "giao_duc_haiyan": "GD",
    "thuc_pham_thien_minh": "TP",
}


def _variants(tenant_id: str, topic_index: int, topic: str) -> list[str]:
    return [
        f"Khách muốn biết {topic} như thế nào?",
        f"Cho tôi hỏi thông tin về {topic}.",
    ]


QUESTION_TOPIC_INDEX = {
    normalize_question(question): topic_index
    for tenant_id, topics in TENANT_TOPICS.items()
    for topic_index, topic in enumerate(topics, start=1)
    for question in _variants(tenant_id, topic_index, topic)
}


def _synthetic_embed(texts: list[str], **_kwargs: Any) -> list[list[float]]:
    """Deterministic semantic stub using an internal question-to-topic mapping."""

    vectors: list[list[float]] = []
    for text in texts:
        topic_index = QUESTION_TOPIC_INDEX.get(normalize_question(text))
        if topic_index is None:
            raise ValueError(f"Synthetic question is not present in the internal topic map: {text}")
        vector = [0.0] * 12
        vector[topic_index - 1] = 1.0
        vectors.append(vector)
    return vectors


def _seed(database: Path) -> dict[str, list[dict[str, Any]]]:
    if database.exists():
        database.unlink()
    review_groups: dict[str, list[dict[str, Any]]] = {}
    occurred_at = datetime(2026, 9, 8, tzinfo=UTC)
    with SQLiteStore(database) as storage:
        for tenant_id, topics in TENANT_TOPICS.items():
            storage.upsert_tenant(tenant_id, tenant_id)
            review_groups[tenant_id] = []
            sequence = 0
            for topic_index, topic in enumerate(topics, start=1):
                variants = _variants(tenant_id, topic_index, topic)
                frequency = 21 - topic_index  # 20, 19, ..., 9: unique ranking.
                for occurrence in range(frequency):
                    sequence += 1
                    conversation_id = f"h402-{TENANT_CODES[tenant_id].lower()}-{sequence:04d}"
                    storage.create_conversation(tenant_id, conversation_id)
                    storage.save_knowledge_gap(
                        tenant_id,
                        conversation_id,
                        question=variants[occurrence % len(variants)],
                        top_score=0.4,
                        threshold=0.65,
                        reason="below_threshold",
                        trace_id=f"trace-{conversation_id}",
                        occurred_at=(occurred_at + timedelta(seconds=sequence)).isoformat(),
                    )
                if topic_index <= 10:
                    review_groups[tenant_id].append(
                        {
                            "name": topic[0].upper() + topic[1:],
                            "questions": variants,
                            "review_status": "approved",
                            "review_notes": "Đã đối chiếu với ground truth SYNTHETIC_TEST.",
                        }
                    )
    return review_groups


def _verify(report: dict[str, Any], *, reviewed: bool) -> dict[str, Any]:
    expected_frequencies = list(range(20, 10, -1))
    tenants: dict[str, Any] = {}
    all_passed = True
    for tenant_id in TENANT_TOPICS:
        tenant = report["tenants"][tenant_id]
        top = tenant["top_missing_topics"]
        frequencies = [item["frequency"] for item in top]
        tenant_questions = {
            normalize_question(question)
            for topic_index, topic in enumerate(TENANT_TOPICS[tenant_id], start=1)
            for question in _variants(tenant_id, topic_index, topic)
        }
        checks = {
            "has_12_clusters": tenant["cluster_count"] == 12,
            "has_top_10": len(top) == 10,
            "ranked_by_frequency": frequencies == expected_frequencies,
            "no_cross_tenant_data": all(
                normalize_question(item["representative_question"]) in tenant_questions
                for item in top
            ),
            "no_test_markers_in_output": all(
                "h402" not in normalize_question(example)
                for item in top
                for example in item["examples"]
            ),
            "review_state_correct": all(
                item["review_status"] == ("approved" if reviewed else "needs_manual_review")
                for item in top
            ),
        }
        passed = all(checks.values())
        all_passed = all_passed and passed
        tenants[tenant_id] = {
            "passed": passed,
            "checks": checks,
            "top_10_frequencies": frequencies,
            "top_10_topics": [item["name"] for item in top],
        }
    return {"passed": all_passed, "tenant_count": len(tenants), "tenants": tenants}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "h4_02_acceptance",
    )
    args = parser.parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    database = output_dir / "synthetic_knowledge_gaps.sqlite3"
    review_groups = _seed(database)

    auto_report = run(
        database=database,
        tenant_ids=list(TENANT_TOPICS),
        output_dir=output_dir / "auto_before_review",
        threshold=0.85,
        use_llm=False,
        embed_fn=_synthetic_embed,
        source_data_label="SYNTHETIC_TEST — không phải log khách thật",
    )
    auto_result = _verify(auto_report, reviewed=False)

    review_path = output_dir / "manual_review_overrides.json"
    review_path.write_text(
        json.dumps(
            {
                "schema_version": "h4-02.manual-review.v1",
                "source_data_label": "SYNTHETIC_TEST",
                "tenants": {
                    tenant_id: {"groups": groups}
                    for tenant_id, groups in review_groups.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    reviewed_report = run(
        database=database,
        tenant_ids=list(TENANT_TOPICS),
        output_dir=output_dir / "reviewed",
        threshold=0.85,
        use_llm=False,
        embed_fn=_synthetic_embed,
        manual_review_groups=review_groups,
        review_source=str(review_path.resolve()),
        source_data_label="SYNTHETIC_TEST — không phải log khách thật",
    )
    reviewed_result = _verify(reviewed_report, reviewed=True)
    summary = {
        "schema_version": "h4-02.acceptance.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_data_label": "SYNTHETIC_TEST — không phải log khách thật",
        "auto_clustering": auto_result,
        "after_manual_review": reviewed_result,
        "passed": auto_result["passed"] and reviewed_result["passed"],
    }
    (output_dir / "acceptance_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# H4-02 — Nghiệm thu top 10 theo tenant",
        "",
        "> Dữ liệu **SYNTHETIC_TEST**, không phải log khách thật.",
        "",
        f"- Kết quả: **{'PASS' if summary['passed'] else 'FAIL'}**",
        f"- Tenant: **{len(TENANT_TOPICS)}**",
        "- Mỗi tenant: **12 cụm**, báo cáo lấy **top 10** theo tần suất `20 → 11`.",
        "- Kiểm tra hai pha: gom cụm tự động, sau đó áp dụng review thủ công.",
        "",
        "| Tenant | Auto | Sau review | Top 10 | Tần suất |",
        "|---|---|---|---:|---|",
    ]
    for tenant_id in TENANT_TOPICS:
        auto = auto_result["tenants"][tenant_id]
        reviewed = reviewed_result["tenants"][tenant_id]
        lines.append(
            f"| {tenant_id} | {'PASS' if auto['passed'] else 'FAIL'} | "
            f"{'PASS' if reviewed['passed'] else 'FAIL'} | 10 | "
            f"{', '.join(map(str, reviewed['top_10_frequencies']))} |"
        )
    (output_dir / "acceptance_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"H4-02 ACCEPTANCE: {'PASS' if summary['passed'] else 'FAIL'}")
    for tenant_id, result in reviewed_result["tenants"].items():
        print(
            f"{tenant_id}: {'PASS' if result['passed'] else 'FAIL'} | "
            f"top10={result['top_10_frequencies']}"
        )
    print(f"Báo cáo: {(output_dir / 'acceptance_report.md').resolve()}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
